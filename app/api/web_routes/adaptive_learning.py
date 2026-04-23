"""Adaptive Learning entry point — shows XP history from adaptive_learning_sessions."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.quiz import AdaptiveLearningSession
from ...models.user import User
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
