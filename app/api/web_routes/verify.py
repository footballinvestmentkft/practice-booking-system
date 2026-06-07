"""
Public Academy ID verify page — GET /verify/{public_token}

No authentication required.  Shows only the minimal safe subset of user data:
  - profile photo (processed → original → placeholder)
  - display name
  - lfa_academy_id
  - "Verified LFA Member" badge
  - Member since (year)
  - specialization display label (if present)

Deliberately omitted: email, phone, DOB, address, user_id, credit_balance,
  public_token (not rendered in template), private license details.

Rate limited: 20 requests / 60 s per source IP.
"""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from fastapi import Depends

from ...database import get_db
from ...models.user import User
from ...services.academy_id_service import (
    check_verify_rate_limit,
    specialization_display_label,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/verify/{public_token}", response_class=HTMLResponse)
def verify_academy_id(
    public_token: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """
    Public Academy ID verification page.

    Accessed by scanning the QR code on an Academy ID card.
    Returns a minimal branded page confirming LFA membership.
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

    return templates.TemplateResponse(
        request,
        "verify_academy_id.html",
        {
            "display_name":   display_name,
            "lfa_academy_id": user.lfa_academy_id,
            "photo_url":      photo_url,
            "member_since":   member_since,
            "spec_label":     spec_label,
            "verified":       True,
        },
    )
