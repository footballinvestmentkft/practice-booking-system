"""Adaptive Learning web routes — entry page, session page, session lifecycle (AL-3)."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.quiz import AdaptiveLearningSession, Quiz, QuizAnswerOption, QuizCategory, QuizQuestion, QuestionMetadata
from ...models.user import User
from ...models.xp_transaction import XPTransaction
from ...services.adaptive_learning import AdaptiveLearningService
from ...services.gamification.xp_service import award_xp
from .helpers import require_student_onboarding
from .student_features import _spec_ctx

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["adaptive-learning"])

MIN_QUESTIONS_PER_CATEGORY = 10


@router.get("/adaptive-learning", response_class=HTMLResponse)
async def adaptive_learning_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Adaptive Learning entry point — XP summary + disabled CTA until full engine lands."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    # ── Stats from adaptive_learning_sessions (safe — table exists) ──────────
    total_xp = (
        db.query(func.sum(AdaptiveLearningSession.xp_earned))
        .filter(AdaptiveLearningSession.user_id == user.id)
        .scalar()
    ) or 0

    session_count = (
        db.query(func.count(AdaptiveLearningSession.id))
        .filter(AdaptiveLearningSession.user_id == user.id)
        .scalar()
    ) or 0

    recent_sessions = (
        db.query(AdaptiveLearningSession)
        .filter(AdaptiveLearningSession.user_id == user.id)
        .order_by(AdaptiveLearningSession.started_at.desc())
        .limit(5)
        .all()
    )

    last_session = recent_sessions[0] if recent_sessions else None

    return templates.TemplateResponse(
        "adaptive_learning.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "total_xp": total_xp,
            "session_count": session_count,
            "recent_sessions": recent_sessions,
            "last_session": last_session,
        },
    )


# ── Session page (AL-2) ───────────────────────────────────────────────────────

@router.get("/adaptive-learning/session", response_class=HTMLResponse)
async def adaptive_learning_session_page(
    request: Request,
    language: str = Query("en", description="Session language ('en' or 'hu')"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Server-rendered session page. JS bootstraps the question loop via AL-1 routes."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    if language not in _VALID_LANGUAGES:
        language = "en"

    available_categories = (
        db.query(Quiz.category)
        .join(QuizQuestion, QuizQuestion.quiz_id == Quiz.id)
        .join(QuestionMetadata, QuestionMetadata.question_id == QuizQuestion.id)
        .filter(Quiz.is_active == True, Quiz.language == language)
        .group_by(Quiz.category)
        .having(func.count(QuizQuestion.id) >= MIN_QUESTIONS_PER_CATEGORY)
        .all()
    )
    category_values = [row[0] for row in available_categories]

    return templates.TemplateResponse(
        "adaptive_learning_session.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "available_categories": category_values,
            "session_language": language,
        },
    )


# ── Session lifecycle (AL-1) ──────────────────────────────────────────────────
# These 4 routes expose the AdaptiveLearningService via web-cookie auth so the
# AL-2 session page can call them with the same session credentials as all other
# web routes (no Bearer token required from the frontend).

def _session_guard(db: Session, session_id: int, user_id: int):
    """Return (session, error_response). error_response is None if session is valid and active."""
    session = (
        db.query(AdaptiveLearningSession)
        .filter(
            AdaptiveLearningSession.id == session_id,
            AdaptiveLearningSession.user_id == user_id,
        )
        .first()
    )
    if not session:
        return None, JSONResponse({"error": "session not found"}, status_code=404)
    if session.ended_at is not None:
        return session, JSONResponse(
            {"error": "session already completed", "session_complete": True}, status_code=410
        )
    return session, None


_VALID_TIME_LIMITS = {60, 180, 300}
_VALID_LANGUAGES = {"en", "hu"}


@router.post("/adaptive-learning/session/start")
async def al_session_start(
    request: Request,
    category: str = Query("LESSON", description="QuizCategory value for this session"),
    time_limit: int = Query(180, description="Session time limit in seconds (60, 180, or 300)"),
    language: str = Query("en", description="Session language ('en' or 'hu')"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Start a new adaptive learning session, or return the existing active one."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    try:
        quiz_category = QuizCategory[category.upper()]
    except KeyError:
        valid = [c.value for c in QuizCategory]
        return JSONResponse(
            {"error": f"invalid category {category!r}. Valid values: {valid}"},
            status_code=422,
        )

    if time_limit not in _VALID_TIME_LIMITS:
        return JSONResponse(
            {"error": f"time_limit must be one of {sorted(_VALID_TIME_LIMITS)}"},
            status_code=422,
        )

    if language not in _VALID_LANGUAGES:
        return JSONResponse(
            {"error": f"language {language!r} is not supported. Valid values: {sorted(_VALID_LANGUAGES)}"},
            status_code=422,
        )

    # Reject categories that have fewer than the minimum number of active questions
    # for the requested language. This prevents sessions that immediately complete 0/0.
    question_count = (
        db.query(func.count(QuizQuestion.id))
        .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
        .filter(
            Quiz.category == quiz_category,
            Quiz.language == language,
            Quiz.is_active == True,
        )
        .scalar()
    ) or 0
    if question_count < MIN_QUESTIONS_PER_CATEGORY:
        return JSONResponse(
            {
                "error": (
                    f"category {category!r} has insufficient questions for language {language!r} "
                    f"({question_count} available, {MIN_QUESTIONS_PER_CATEGORY} required)"
                )
            },
            status_code=422,
        )

    # Return existing active session rather than creating a duplicate.
    # If the existing session is already server-side expired, retire it first so
    # the user gets a fresh session rather than an immediately-terminal resume.
    existing = (
        db.query(AdaptiveLearningSession)
        .filter(
            AdaptiveLearningSession.user_id == user.id,
            AdaptiveLearningSession.ended_at.is_(None),
        )
        .order_by(AdaptiveLearningSession.id.desc())
        .first()
    )
    if existing:
        elapsed = 0.0
        if existing.session_start_time and existing.session_time_limit_seconds:
            elapsed = (datetime.now(timezone.utc) - existing.session_start_time).total_seconds()

        if existing.session_time_limit_seconds and elapsed >= existing.session_time_limit_seconds:
            # Stale expired session — retire it and fall through to create a new one
            existing.ended_at = datetime.now(timezone.utc)
            db.commit()
        else:
            # Valid unexpired session — update time limit to match user's current choice.
            # If elapsed time already exceeds the new (shorter) limit, reset the clock so
            # the first next-question call doesn't immediately expire the session.
            existing.session_time_limit_seconds = time_limit
            if elapsed >= time_limit:
                existing.session_start_time = datetime.now(timezone.utc)
            db.commit()
            current_score = (existing.questions_correct or 0) * 2 - (existing.questions_presented or 0)
            return JSONResponse({
                "session_id": existing.id,
                "question_count": 10,
                "started_at": existing.session_start_time.isoformat() if existing.session_start_time else None,
                "resumed": True,
                "time_limit_seconds": time_limit,
                "current_score": current_score,
                "questions_presented": existing.questions_presented or 0,
            })

    service = AdaptiveLearningService(db)
    session = service.start_adaptive_session(
        user.id, quiz_category, session_duration_seconds=time_limit, language=language
    )
    return JSONResponse({
        "session_id": session.id,
        "question_count": 10,
        "started_at": session.session_start_time.isoformat() if session.session_start_time else None,
        "resumed": False,
        "time_limit_seconds": time_limit,
        "current_score": 0,
    })


@router.get("/adaptive-learning/session/{session_id}/next-question")
async def al_session_next_question(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
    exclude_ids: str = Query(
        "",
        description=(
            # Compatibility layer: client passes comma-separated IDs of questions already shown
            # this session. The service deduplicates via user_question_performance (1-hour window),
            # but that window can be exhausted in short sessions with few questions.
            # exclude_ids gives the AL-2 UI explicit within-session dedup without a server-side
            # session question log table. Not used by the service directly — handled here at the
            # route level to avoid requiring a new DB table in AL-1.
            "Comma-separated question IDs already shown this session (client-tracked)"
        ),
    ),
):
    """Return the next adaptive question for this session."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    _, err = _session_guard(db, session_id, user.id)
    if err:
        return err

    # Parse client-tracked exclude list
    seen_ids: set[int] = set()
    if exclude_ids.strip():
        try:
            seen_ids = {int(x) for x in exclude_ids.split(",") if x.strip()}
        except ValueError:
            seen_ids = set()

    service = AdaptiveLearningService(db)
    result = service.get_next_question(user.id, session_id, exclude_ids=seen_ids or None)

    if result is None:
        # Bare None should not happen (service returns structured dict), but guard defensively.
        return JSONResponse({"session_complete": True, "reason": "pool_exhausted"})

    return JSONResponse(result)


@router.post("/adaptive-learning/session/{session_id}/answer")
async def al_session_answer(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Record an answer and return correctness + XP earned this turn."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    _, err = _session_guard(db, session_id, user.id)
    if err:
        return err

    body = await request.json()
    question_id: int = body.get("question_id")
    selected_option_id: int = body.get("selected_option_id")
    time_spent: float = float(body.get("time_spent_seconds", 30.0))
    timed_out: bool = bool(body.get("timed_out", False))

    if not question_id:
        return JSONResponse({"error": "question_id required"}, status_code=422)

    if timed_out:
        is_correct = False
        correct_option = (
            db.query(QuizAnswerOption)
            .filter(QuizAnswerOption.question_id == question_id, QuizAnswerOption.is_correct == True)
            .first()
        )
        explanation = (
            db.query(QuizQuestion.explanation)
            .filter(QuizQuestion.id == question_id)
            .scalar()
        ) or ""
    else:
        if not selected_option_id:
            return JSONResponse({"error": "selected_option_id required"}, status_code=422)

        # Verify answer correctness — route owns this check (no quiz_attempts coupling)
        option = (
            db.query(QuizAnswerOption)
            .filter(
                QuizAnswerOption.id == selected_option_id,
                QuizAnswerOption.question_id == question_id,
            )
            .first()
        )
        if not option:
            return JSONResponse({"error": "invalid option for this question"}, status_code=422)

        is_correct = bool(option.is_correct)
        correct_option = (
            db.query(QuizAnswerOption)
            .filter(QuizAnswerOption.question_id == question_id, QuizAnswerOption.is_correct == True)
            .first()
        )
        explanation = (
            db.query(QuizQuestion.explanation)
            .filter(QuizQuestion.id == question_id)
            .scalar()
        ) or ""

    service = AdaptiveLearningService(db)
    result = service.record_answer(
        user_id=user.id,
        session_id=session_id,
        question_id=question_id,
        is_correct=is_correct,
        time_spent_seconds=time_spent,
    )

    return JSONResponse({
        "correct": is_correct,
        "timed_out": timed_out,
        "correct_option_id": correct_option.id if correct_option else None,
        "explanation": explanation,
        "score_delta": result.get("score_delta", -1 if not is_correct else 1),
        "score": result.get("score", 0),
        "new_target_difficulty": result.get("new_target_difficulty"),
        "performance_trend": result.get("performance_trend"),
    })


@router.post("/adaptive-learning/session/{session_id}/complete")
async def al_session_complete(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Finalise the session and write XP to the ledger."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    # SELECT FOR UPDATE serialises concurrent complete calls on the same session row.
    # The first call proceeds; any concurrent duplicate blocks here, then sees
    # ended_at IS NOT NULL and returns 410 — award_xp is never reached twice.
    session = (
        db.query(AdaptiveLearningSession)
        .filter(
            AdaptiveLearningSession.id == session_id,
            AdaptiveLearningSession.user_id == user.id,
        )
        .with_for_update()
        .first()
    )
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404)
    if session.ended_at is not None:
        return JSONResponse(
            {"error": "session already completed", "session_complete": True},
            status_code=410,
        )

    service = AdaptiveLearningService(db)
    summary = service.end_session(session_id)  # sets ended_at, commits → releases lock

    presented = summary.get("questions_answered", 0)
    correct = summary.get("correct_answers", 0)
    accuracy_pct = round(correct / presented * 100, 1) if presented > 0 else 0.0
    xp = summary.get("xp_earned") or 0

    if xp > 0:
        idempotency_key = f"adaptive_session_{session_id}_xp"
        # Secondary guard: skip award_xp if a ledger row already exists for this key.
        # Primary guard is the SELECT FOR UPDATE above; this catches any edge case
        # where award_xp was called but end_session commit was delayed.
        already_awarded = (
            db.query(XPTransaction)
            .filter(XPTransaction.idempotency_key == idempotency_key)
            .first()
        )
        if not already_awarded:
            award_xp(
                db,
                user_id=user.id,
                xp_amount=xp,
                reason=f"Adaptive Learning session #{session_id}",
                idempotency_key=idempotency_key,
                transaction_type="ADAPTIVE_LEARNING_XP",
            )

    return JSONResponse({
        "session_id": session_id,
        "questions_presented": presented,
        "questions_correct": correct,
        "xp_earned": xp,
        "accuracy_pct": accuracy_pct,
    })
