"""Admin Adaptive Learning — knowledge base browser, JSON import center, and content editor."""
import json
import logging
import math

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.al_import_log import ALImportLog, ImportStatus
from ....models.quiz import (
    ContentStatus,
    OptionType,
    Quiz,
    QuizAnswerOption,
    QuizDifficulty,
    QuizQuestion,
)
from ....models.user import User
from ....services.al_import_service import (
    SPECIALIZATION_CATEGORY_ALLOWLIST,
    _MAX_FILES_PER_IMPORT,
    validate_files,
    apply_import,
)
from ....services import al_editor_service as editor
from ....services.al_editor_service import (
    EditorError,
    InvalidTransitionError,
    OptionEditPayload,
    ProtectedFieldError,
    QuestionEditPayload,
    ValidationError,
)
from . import _admin_guard, templates

logger = logging.getLogger(__name__)

router = APIRouter()

_KNOWN_SPECS = list(SPECIALIZATION_CATEGORY_ALLOWLIST.keys())
_DEFAULT_SPEC = "LFA_FOOTBALL_PLAYER"
_QUIZZES_PER_PAGE = 30
_HISTORY_PER_PAGE = 20


# ── AL-01  Dashboard ──────────────────────────────────────────────────────────

@router.get("/admin/adaptive-learning/dashboard", response_class=HTMLResponse)
async def al_dashboard(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    total_quizzes    = db.query(Quiz).count()
    active_quizzes   = db.query(Quiz).filter(
        Quiz.content_status == ContentStatus.PUBLISHED.value
    ).count()
    total_questions  = db.query(QuizQuestion).count()
    total_options    = db.query(QuizAnswerOption).count()
    variant_options  = db.query(QuizAnswerOption).filter(
        QuizAnswerOption.option_type == OptionType.CORRECT_VARIANT
    ).count()
    distractor_options = db.query(QuizAnswerOption).filter(
        QuizAnswerOption.option_type == OptionType.DISTRACTOR
    ).count()
    recent_imports = (
        db.query(ALImportLog)
        .order_by(ALImportLog.completed_at.desc())
        .limit(5)
        .all()
    )

    return templates.TemplateResponse("admin/al_dashboard.html", {
        "request":             request,
        "total_quizzes":       total_quizzes,
        "active_quizzes":      active_quizzes,
        "total_questions":     total_questions,
        "total_options":       total_options,
        "variant_options":     variant_options,
        "distractor_options":  distractor_options,
        "recent_imports":      recent_imports,
        "ImportStatus":        ImportStatus,
    })


# ── AL-02  Quiz list ──────────────────────────────────────────────────────────

@router.get("/admin/adaptive-learning/quizzes", response_class=HTMLResponse)
async def al_quiz_list(
    request: Request,
    page: int = 1,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    total  = db.query(Quiz).count()
    offset = (page - 1) * _QUIZZES_PER_PAGE
    quizzes = (
        db.query(Quiz)
        .order_by(Quiz.id.asc())
        .offset(offset)
        .limit(_QUIZZES_PER_PAGE)
        .all()
    )
    total_pages = math.ceil(total / _QUIZZES_PER_PAGE) if total else 1

    return templates.TemplateResponse("admin/al_quiz_list.html", {
        "request":     request,
        "quizzes":     quizzes,
        "page":        page,
        "total_pages": total_pages,
        "total":       total,
    })


# ── AL-03  Quiz detail (with question list) ───────────────────────────────────

@router.get("/admin/adaptive-learning/quizzes/{quiz_id}", response_class=HTMLResponse)
async def al_quiz_detail(
    quiz_id: int,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        return RedirectResponse(
            "/admin/adaptive-learning/quizzes?error=Quiz+not+found",
            status_code=303,
        )

    questions = (
        db.query(QuizQuestion)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.order_index.asc())
        .all()
    )

    return templates.TemplateResponse("admin/al_quiz_detail.html", {
        "request":   request,
        "quiz":      quiz,
        "questions": questions,
    })


# ── AL-04  Question detail (read-only) ────────────────────────────────────────

@router.get(
    "/admin/adaptive-learning/quizzes/{quiz_id}/questions/{question_id}",
    response_class=HTMLResponse,
)
async def al_question_detail(
    quiz_id:     int,
    question_id: int,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    question = db.query(QuizQuestion).filter(
        QuizQuestion.id == question_id,
        QuizQuestion.quiz_id == quiz_id,
    ).first()

    if not quiz or not question:
        return RedirectResponse(
            f"/admin/adaptive-learning/quizzes/{quiz_id}?error=Question+not+found",
            status_code=303,
        )

    options = (
        db.query(QuizAnswerOption)
        .filter(QuizAnswerOption.question_id == question_id)
        .order_by(QuizAnswerOption.order_index.asc())
        .all()
    )

    return templates.TemplateResponse("admin/al_question_detail.html", {
        "request":  request,
        "quiz":     quiz,
        "question": question,
        "options":  options,
        "OptionType": OptionType,
    })


# ── AL-05  Import form ────────────────────────────────────────────────────────

@router.get("/admin/adaptive-learning/import", response_class=HTMLResponse)
async def al_import_form(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)

    return templates.TemplateResponse("admin/al_import.html", {
        "request":           request,
        "known_specs":       _KNOWN_SPECS,
        "default_spec":      _DEFAULT_SPEC,
        "max_files":         _MAX_FILES_PER_IMPORT,
        "report":            None,
        "apply_payload_json": "",
        "error":             request.query_params.get("error", ""),
        "success":           request.query_params.get("success", ""),
    })


# ── AL-06  Import validate (dry-run, re-renders form with report) ─────────────

@router.post("/admin/adaptive-learning/import/validate", response_class=HTMLResponse)
async def al_import_validate(
    request: Request,
    spec:    str            = Form(_DEFAULT_SPEC),
    files:   list[UploadFile] = File(...),
    db:      Session        = Depends(get_db),
    user:    User           = Depends(get_current_user_web),
):
    _admin_guard(user)

    if spec not in _KNOWN_SPECS:
        return RedirectResponse(
            f"/admin/adaptive-learning/import?error=Unknown+spec+{spec}",
            status_code=303,
        )

    if len(files) > _MAX_FILES_PER_IMPORT:
        return RedirectResponse(
            f"/admin/adaptive-learning/import?error=Too+many+files+%28max+{_MAX_FILES_PER_IMPORT}%29",
            status_code=303,
        )

    raw_files: list[tuple[str, bytes]] = []
    for upload in files:
        content = await upload.read()
        raw_files.append((upload.filename or "unnamed.json", content))

    try:
        report = validate_files(raw_files, spec, db)
    except ValueError as exc:
        return RedirectResponse(
            f"/admin/adaptive-learning/import?error={str(exc)[:200]}",
            status_code=303,
        )

    return templates.TemplateResponse("admin/al_import.html", {
        "request":            request,
        "known_specs":        _KNOWN_SPECS,
        "default_spec":       spec,
        "max_files":          _MAX_FILES_PER_IMPORT,
        "report":             report,
        "apply_payload_json": report.apply_payload_json,
        "error":              "",
        "success":            "",
    })


# ── AL-07  Import apply ───────────────────────────────────────────────────────

@router.post("/admin/adaptive-learning/import/apply", response_class=HTMLResponse)
async def al_import_apply(
    request: Request,
    spec:                str  = Form(_DEFAULT_SPEC),
    apply_payload_json:  str  = Form(""),
    db:   Session             = Depends(get_db),
    user: User                = Depends(get_current_user_web),
):
    _admin_guard(user)

    if not apply_payload_json:
        return RedirectResponse(
            "/admin/adaptive-learning/import?error=No+validated+payload.+Upload+files+first.",
            status_code=303,
        )
    if spec not in _KNOWN_SPECS:
        return RedirectResponse(
            f"/admin/adaptive-learning/import?error=Unknown+spec+{spec}",
            status_code=303,
        )

    try:
        summary = apply_import(
            apply_payload_json=apply_payload_json,
            spec=spec,
            db=db,
            operator_user_id=user.id,
        )
    except ValueError as exc:
        return RedirectResponse(
            f"/admin/adaptive-learning/import?error={str(exc)[:200]}",
            status_code=303,
        )
    except Exception as exc:
        logger.exception("AL import apply failed")
        return RedirectResponse(
            f"/admin/adaptive-learning/import?error=Import+failed%3A+{str(exc)[:150]}",
            status_code=303,
        )

    return templates.TemplateResponse("admin/al_import_success.html", {
        "request": request,
        "summary": summary,
        "spec":    spec,
        "log_id":  summary.log_id,
    })


# ── AL-08  Import history ─────────────────────────────────────────────────────

@router.get("/admin/adaptive-learning/import/history", response_class=HTMLResponse)
async def al_import_history(
    request: Request,
    page: int = 1,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    total  = db.query(ALImportLog).count()
    offset = (page - 1) * _HISTORY_PER_PAGE
    logs   = (
        db.query(ALImportLog)
        .order_by(ALImportLog.completed_at.desc())
        .offset(offset)
        .limit(_HISTORY_PER_PAGE)
        .all()
    )
    total_pages = math.ceil(total / _HISTORY_PER_PAGE) if total else 1

    return templates.TemplateResponse("admin/al_import_history.html", {
        "request":     request,
        "logs":        logs,
        "page":        page,
        "total_pages": total_pages,
        "total":       total,
        "ImportStatus": ImportStatus,
    })


# ── AE-01  Quiz meta editor (GET) ─────────────────────────────────────────────

@router.get(
    "/admin/adaptive-learning/quizzes/{quiz_id}/edit",
    response_class=HTMLResponse,
)
async def al_quiz_edit_get(
    quiz_id: int,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        return RedirectResponse(
            "/admin/adaptive-learning/quizzes?error=Quiz+not+found",
            status_code=303,
        )

    questions = (
        db.query(QuizQuestion)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.order_index.asc())
        .all()
    )

    return templates.TemplateResponse("admin/al_quiz_edit.html", {
        "request":       request,
        "quiz":          quiz,
        "questions":     questions,
        "difficulties":  [d.value for d in QuizDifficulty],
        "ContentStatus": ContentStatus,
        "error":         request.query_params.get("error", ""),
        "success":       request.query_params.get("success", ""),
    })


# ── AE-02  Quiz meta editor (POST) ────────────────────────────────────────────

@router.post(
    "/admin/adaptive-learning/quizzes/{quiz_id}/edit",
    response_class=HTMLResponse,
)
async def al_quiz_edit_post(
    quiz_id: int,
    request: Request,
    title:              str   = Form(...),
    description:        str   = Form(""),
    difficulty:         str   = Form("MEDIUM"),
    time_limit_minutes: int   = Form(15),
    xp_reward:          int   = Form(50),
    passing_score:      float = Form(70.0),
    language:           str   = Form("en"),
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        return RedirectResponse(
            "/admin/adaptive-learning/quizzes?error=Quiz+not+found",
            status_code=303,
        )

    try:
        if not title.strip():
            raise ValidationError("title must be non-empty")
        if difficulty not in [d.value for d in QuizDifficulty]:
            raise ValidationError(f"Unknown difficulty: {difficulty}")
        if not (0.0 <= passing_score <= 100.0):
            raise ValidationError("passing_score must be 0–100")
        if time_limit_minutes <= 0:
            raise ValidationError("time_limit_minutes must be > 0")
        if xp_reward < 0:
            raise ValidationError("xp_reward must be ≥ 0")

        quiz.title              = title.strip()
        quiz.description        = description.strip() or None
        quiz.difficulty         = difficulty
        quiz.time_limit_minutes = time_limit_minutes
        quiz.xp_reward          = xp_reward
        quiz.passing_score      = passing_score
        quiz.language           = language.strip() or "en"
        db.commit()
    except (ValidationError, EditorError) as exc:
        return RedirectResponse(
            f"/admin/adaptive-learning/quizzes/{quiz_id}/edit?error={str(exc)[:200]}",
            status_code=303,
        )

    return RedirectResponse(
        f"/admin/adaptive-learning/quizzes/{quiz_id}/edit?success=Quiz+metadata+updated",
        status_code=303,
    )


# ── AE-03  Publish quiz ───────────────────────────────────────────────────────

@router.post(
    "/admin/adaptive-learning/quizzes/{quiz_id}/publish",
    response_class=HTMLResponse,
)
async def al_quiz_publish(
    quiz_id: int,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)
    try:
        editor.publish_quiz(db, quiz_id)
    except (EditorError, InvalidTransitionError) as exc:
        return RedirectResponse(
            f"/admin/adaptive-learning/quizzes/{quiz_id}/edit?error={str(exc)[:200]}",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin/adaptive-learning/quizzes/{quiz_id}/edit?success=Quiz+published",
        status_code=303,
    )


# ── AE-04  Draft quiz ─────────────────────────────────────────────────────────

@router.post(
    "/admin/adaptive-learning/quizzes/{quiz_id}/draft",
    response_class=HTMLResponse,
)
async def al_quiz_draft(
    quiz_id: int,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)
    try:
        editor.draft_quiz(db, quiz_id)
    except (EditorError, InvalidTransitionError) as exc:
        return RedirectResponse(
            f"/admin/adaptive-learning/quizzes/{quiz_id}/edit?error={str(exc)[:200]}",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin/adaptive-learning/quizzes/{quiz_id}/edit?success=Quiz+moved+to+draft",
        status_code=303,
    )


# ── AE-05  Archive quiz ───────────────────────────────────────────────────────

@router.post(
    "/admin/adaptive-learning/quizzes/{quiz_id}/archive",
    response_class=HTMLResponse,
)
async def al_quiz_archive(
    quiz_id: int,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)
    try:
        editor.archive_quiz(db, quiz_id)
    except (EditorError, InvalidTransitionError) as exc:
        return RedirectResponse(
            f"/admin/adaptive-learning/quizzes/{quiz_id}/edit?error={str(exc)[:200]}",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin/adaptive-learning/quizzes/{quiz_id}/edit?success=Quiz+archived",
        status_code=303,
    )


# ── AE-06  Question editor (GET) ──────────────────────────────────────────────

@router.get(
    "/admin/adaptive-learning/quizzes/{quiz_id}/questions/{question_id}/edit",
    response_class=HTMLResponse,
)
async def al_question_edit_get(
    quiz_id:     int,
    question_id: int,
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        return RedirectResponse(
            "/admin/adaptive-learning/quizzes?error=Quiz+not+found",
            status_code=303,
        )

    ctx = editor.get_question_with_options(db, question_id)
    if not ctx:
        return RedirectResponse(
            f"/admin/adaptive-learning/quizzes/{quiz_id}/edit?error=Question+not+found",
            status_code=303,
        )
    if ctx["question"].quiz_id != quiz_id:
        return RedirectResponse(
            f"/admin/adaptive-learning/quizzes/{quiz_id}/edit?error=Question+not+in+this+quiz",
            status_code=303,
        )

    return templates.TemplateResponse("admin/al_question_edit.html", {
        "request":       request,
        "quiz":          quiz,
        "ContentStatus": ContentStatus,
        "error":         request.query_params.get("error", ""),
        "success":       request.query_params.get("success", ""),
        **ctx,
    })


# ── AE-07  Question editor (POST) ─────────────────────────────────────────────

@router.post(
    "/admin/adaptive-learning/quizzes/{quiz_id}/questions/{question_id}/edit",
    response_class=HTMLResponse,
)
async def al_question_edit_post(
    quiz_id:              int,
    question_id:          int,
    request:              Request,
    question_text:        str   = Form(...),
    explanation:          str   = Form(""),
    estimated_difficulty: float = Form(0.5),
    cognitive_load:       float = Form(0.5),
    average_time_seconds: float = Form(30.0),
    concept_tags_raw:     str   = Form(""),
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    tags: list[str] = [
        t.strip() for t in concept_tags_raw.split(",") if t.strip()
    ]
    payload = QuestionEditPayload(
        question_text         = question_text,
        explanation           = explanation,
        estimated_difficulty  = estimated_difficulty,
        cognitive_load        = cognitive_load,
        average_time_seconds  = average_time_seconds,
        concept_tags          = tags,
    )
    try:
        editor.update_question(db, question_id, payload, quiz_id=quiz_id)
    except (EditorError, ValidationError, ProtectedFieldError) as exc:
        return RedirectResponse(
            f"/admin/adaptive-learning/quizzes/{quiz_id}/questions/{question_id}/edit"
            f"?error={str(exc)[:200]}",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin/adaptive-learning/quizzes/{quiz_id}/questions/{question_id}/edit"
        f"?success=Question+updated",
        status_code=303,
    )


# ── AE-08  Option text editor (POST) ─────────────────────────────────────────

@router.post(
    "/admin/adaptive-learning/quizzes/{quiz_id}/questions/{question_id}/options/{option_id}/edit",
    response_class=HTMLResponse,
)
async def al_option_edit_post(
    quiz_id:     int,
    question_id: int,
    option_id:   int,
    request:     Request,
    option_text: str = Form(...),
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    _admin_guard(user)

    payload = OptionEditPayload(option_text=option_text)
    try:
        editor.update_option(db, option_id, payload, question_id=question_id)
    except (EditorError, ValidationError, ProtectedFieldError) as exc:
        return RedirectResponse(
            f"/admin/adaptive-learning/quizzes/{quiz_id}/questions/{question_id}/edit"
            f"?error={str(exc)[:200]}",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin/adaptive-learning/quizzes/{quiz_id}/questions/{question_id}/edit"
        f"?success=Option+updated",
        status_code=303,
    )
