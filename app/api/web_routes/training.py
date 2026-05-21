"""Training hub web route — entry point for On-site / Hybrid / Virtual training modes."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ...dependencies import get_current_user_web
from ...models.user import User
from .helpers import require_student_onboarding
from .student_features import _spec_ctx

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["training"])


@router.get("/training", response_class=HTMLResponse)
async def training_hub_page(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    """Training hub — top-level entry point for all training modes."""
    redirect = require_student_onboarding(user)
    if redirect:
        return redirect

    return templates.TemplateResponse(
        "training_hub.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user),
        },
    )
