"""Admin coupon and invitation code management routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
import logging

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User
from ....models.coupon import Coupon, CouponType
from ....models.invitation_code import InvitationCode

from . import templates, _admin_guard

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/coupons", response_class=HTMLResponse)
async def admin_coupons_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Coupon Management page"""
    _admin_guard(user)

    # Get all coupons (ordered by creation date)
    coupons = db.query(Coupon).order_by(Coupon.created_at.desc()).all()

    # Add validity status
    for coupon in coupons:
        coupon.is_currently_valid = coupon.is_valid()

    logger.info("admin_coupons_loaded", extra={"count": len(coupons)})

    return templates.TemplateResponse(
        "admin/coupons.html",
        {
            "request": request,
            "user": user,
            "coupons": coupons,
            "today": datetime.now(timezone.utc)
        }
    )


@router.post("/admin/coupons")
async def admin_create_coupon(
    code: str = Form(...),
    coupon_type: str = Form(...),
    value: float = Form(...),
    description: str = Form(...),
    max_uses: str = Form(""),
    expires_days: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    from ....models.coupon import Coupon as _Coupon, CouponType as CT
    try:
        ct = CT(coupon_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid coupon type: {coupon_type}")

    # Convert value based on type
    if ct == CT.PURCHASE_DISCOUNT_PERCENT:
        if not (0 < value <= 100):
            raise HTTPException(status_code=400, detail="Discount percent must be between 1 and 100")
        discount_value = value / 100.0  # store as 0-1 fraction
    else:
        if value <= 0:
            raise HTTPException(status_code=400, detail="Credit value must be positive")
        discount_value = float(value)

    if not code.strip():
        raise HTTPException(status_code=400, detail="Coupon code cannot be empty")

    max_uses_int = int(max_uses) if max_uses.strip() else None
    expires_at = None
    if expires_days.strip():
        expires_at = datetime.now(timezone.utc) + timedelta(days=int(expires_days))

    coupon = _Coupon(
        code=code.strip().upper(),
        type=ct,
        discount_value=discount_value,
        description=description.strip(),
        is_active=True,
        max_uses=max_uses_int,
        expires_at=expires_at,
    )
    coupon.set_flags_based_on_type()
    db.add(coupon)
    db.commit()
    return RedirectResponse(url="/admin/coupons", status_code=303)


@router.post("/admin/coupons/{coupon_id}/toggle")
async def admin_toggle_coupon(
    coupon_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    coupon.is_active = not coupon.is_active
    db.commit()
    return RedirectResponse(url="/admin/coupons", status_code=303)


@router.post("/admin/coupons/{coupon_id}/delete")
async def admin_delete_coupon(
    coupon_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    db.delete(coupon)
    db.commit()
    return RedirectResponse(url="/admin/coupons", status_code=303)


@router.get("/admin/invitation-codes", response_class=HTMLResponse)
async def admin_invitation_codes_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Partner Invitation Codes Management page"""
    _admin_guard(user)

    # Get all invitation codes with creator/redeemer in one query
    codes = db.query(InvitationCode).order_by(InvitationCode.created_at.desc()).all()

    # Bulk-load user names in two queries (avoid N+1)
    used_ids = {c.used_by_user_id for c in codes if c.used_by_user_id}
    admin_ids = {c.created_by_admin_id for c in codes if c.created_by_admin_id}
    all_ids = used_ids | admin_ids
    if all_ids:
        users_map = {u.id: u.name for u in db.query(User.id, User.name).filter(User.id.in_(all_ids)).all()}
    else:
        users_map = {}

    for code in codes:
        code.used_by_name = users_map.get(code.used_by_user_id) if code.used_by_user_id else None
        code.created_by_name = users_map.get(code.created_by_admin_id) if code.created_by_admin_id else None

    logger.info("admin_invitation_codes_loaded", extra={"count": len(codes)})

    return templates.TemplateResponse(
        "admin/invitation_codes.html",
        {
            "request": request,
            "user": user,
            "codes": codes,
            "now": datetime.now(timezone.utc)
        }
    )
