"""Adaptive Learning web routes — entry page, session page, session lifecycle (AL-2)."""
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.quiz import AdaptiveLearningSession, QuizAnswerOption, QuizCategory, QuizQuestion
from ...models.user import User
from ...services.adaptive_learning import AdaptiveLearningService
from .helpers import require_student_onboarding
from .student_features import _spec_ctx

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["adaptive-learning"])


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
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Server-rendered session page. JS bootstraps the question loop via AL-1 routes."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    return templates.TemplateResponse(
        "adaptive_learning_session.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
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


@router.post("/adaptive-learning/session/start")
async def al_session_start(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Start a new adaptive learning session, or return the existing active one."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    # Return existing active session rather than creating a duplicate
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
        return JSONResponse({
            "session_id": existing.id,
            "question_count": 10,
            "started_at": existing.session_start_time.isoformat() if existing.session_start_time else None,
            "resumed": True,
        })

    service = AdaptiveLearningService(db)
    # GENERAL category — 2 questions; LESSON — 12 questions (most content available)
    session = service.start_adaptive_session(
        user.id, QuizCategory.LESSON, session_duration_seconds=600
    )
    return JSONResponse({
        "session_id": session.id,
        "question_count": 10,
        "started_at": session.session_start_time.isoformat() if session.session_start_time else None,
        "resumed": False,
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
    result = service.get_next_question(user.id, session_id)

    if result is None:
        return JSONResponse({"error": "session not found"}, status_code=404)

    if result.get("session_complete"):
        return JSONResponse(result)

    # Route-level within-session dedup: if service returned an already-seen question,
    # attempt one more call to get a different one (single retry — no recursion).
    if seen_ids and result.get("id") in seen_ids:
        retry = service.get_next_question(user.id, session_id)
        if retry and not retry.get("session_complete") and retry.get("id") not in seen_ids:
            result = retry

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

    if not question_id or not selected_option_id:
        return JSONResponse({"error": "question_id and selected_option_id required"}, status_code=422)

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
        "correct_option_id": correct_option.id if correct_option else None,
        "explanation": explanation,
        "xp_this_answer": result.get("xp_earned", 0),
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
    """Finalise the session and return the summary."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    session, err = _session_guard(db, session_id, user.id)
    if err:
        return err

    service = AdaptiveLearningService(db)
    summary = service.end_session(session_id)

    presented = summary.get("questions_answered", 0)
    correct = summary.get("correct_answers", 0)
    accuracy_pct = round(correct / presented * 100, 1) if presented > 0 else 0.0

    return JSONResponse({
        "session_id": session_id,
        "questions_presented": presented,
        "questions_correct": correct,
        "xp_earned": summary.get("xp_earned") or 0,
        "accuracy_pct": accuracy_pct,
    })
