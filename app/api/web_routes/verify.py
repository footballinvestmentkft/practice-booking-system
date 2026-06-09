"""
Public Academy ID verify page — GET /verify/{public_token}

No authentication required.  Shows only the minimal safe subset of user data:
  - profile photo (processed → original → placeholder)
  - display name
  - lfa_academy_id
  - card status badge (verified / not ready / expired / inactive / etc.)
  - Member since (year)
  - specialization display label (if present)

Deliberately omitted: email, phone, DOB, address, user_id, credit_balance,
  public_token (not rendered in template), private license details.

Rate limited: 20 requests / 60 s per source IP.

Card status values:
  verified            — all conditions met (photo + active licence + onboarding + not expired)
  no_licence          — no LFA_FOOTBALL_PLAYER licence found
  inactive            — licence.is_active == False
  expired             — licence.expires_at is past
  onboarding_required — licence active but onboarding_completed == False
  photo_required      — licence + onboarding OK but no profile photo
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from fastapi import Depends

from ...database import get_db
from ...models.license import UserLicense
from ...models.user import User
from ...services.academy_id_service import (
    check_verify_rate_limit,
    specialization_display_label,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _compute_card_status(user: User, db: Session) -> tuple[str, str | None]:
    """
    Compute the card status and optional expiry display string.

    Returns (card_status, expiry_display) where expiry_display is a
    human-readable date string or None.

    Priority order (first matching wins):
      inactive > expired > no_licence > onboarding_required > photo_required > verified
    """
    licence = (
        db.query(UserLicense)
        .filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        )
        .order_by(UserLicense.id.desc())
        .first()
    )

    if licence is None:
        return "no_licence", None

    if not licence.is_active:
        return "inactive", None

    now = datetime.now(timezone.utc)
    expiry_display: str | None = None
    if licence.expires_at is not None:
        # Ensure timezone-aware comparison
        exp = licence.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        expiry_display = exp.strftime("%-d %b %Y")
        if exp <= now:
            return "expired", expiry_display

    if not licence.onboarding_completed:
        return "onboarding_required", expiry_display

    has_photo = bool(user.profile_photo_processed_url or user.profile_photo_url)
    if not has_photo:
        return "photo_required", expiry_display

    return "verified", expiry_display


@router.get("/verify/{public_token}", response_class=HTMLResponse)
def verify_academy_id(
    public_token: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """
    Public Academy ID verification page.

    Accessed by scanning the QR code on an Academy ID card.
    Computes card_status from licence validity, onboarding, and profile photo.
    Only shows "Verified LFA Member" when all conditions are met.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not check_verify_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again in a minute.",
        )

    user = db.query(User).filter(User.public_token == public_token).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # Build the display name (prefer first+last, fall back to name)
    first = getattr(user, "first_name", None)
    last  = getattr(user, "last_name",  None)
    if first or last:
        display_name = " ".join(p for p in [first, last] if p)
    else:
        display_name = getattr(user, "name", "") or ""

    # Photo: processed PNG takes priority over original upload
    photo_url = user.profile_photo_processed_url or user.profile_photo_url

    # Specialization — display label only, hide raw enum / None
    spec_raw   = None
    spec_value = getattr(user, "specialization", None)
    if spec_value is not None:
        spec_raw = spec_value.value if hasattr(spec_value, "value") else str(spec_value)
    spec_label = specialization_display_label(spec_raw)

    member_since = None
    if user.created_at:
        member_since = user.created_at.year

    card_status, expiry_display = _compute_card_status(user, db)

    return templates.TemplateResponse(
        request,
        "verify_academy_id.html",
        {
            "display_name":   display_name,
            "lfa_academy_id": user.lfa_academy_id,
            "photo_url":      photo_url,
            "member_since":   member_since,
            "spec_label":     spec_label,
            "card_status":    card_status,
            "expiry_display": expiry_display,
            "verified":       card_status == "verified",  # kept for template convenience
        },
    )
