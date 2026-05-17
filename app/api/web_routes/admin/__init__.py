"""Admin panel — domain-organized route modules."""
from fastapi import APIRouter, HTTPException
from fastapi.templating import Jinja2Templates
from pathlib import Path

from ....models.user import User, UserRole
from ....utils.country_codes import register_filters as _register_country_filters

# Shared template instance (used by all sub-modules)
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
_register_country_filters(templates.env)


def _admin_guard(user: User):
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")


from . import (  # noqa: E402
    users,
    credits,
    semesters,
    locations,
    bookings,
    finance,
    coupons,
    clubs,
    game_presets,
    sport_directors,
    analytics,
    enrollments,
    sponsors,
    card_themes,
)

router = APIRouter()
for _mod in [
    users,
    credits,
    semesters,
    locations,
    bookings,
    finance,
    coupons,
    clubs,
    game_presets,
    sport_directors,
    analytics,
    enrollments,
    sponsors,
    card_themes,
]:
    router.include_router(_mod.router)
