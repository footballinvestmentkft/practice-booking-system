"""Admin credit and license management routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import logging
import uuid as _uuid

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User, UserRole
from ....models.license import UserLicense, LicenseProgression
from ....models.specialization import SpecializationType
from ....models.credit_transaction import CreditTransaction, TransactionType

from . import _admin_guard

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/admin/users/{user_id}/grant-credit")
async def admin_grant_credit(
    user_id: int,
    request: Request,
    amount: int = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: grant credits to a user (creates CreditTransaction audit record)."""
    _admin_guard(user)
    if amount < 1 or amount > 50000:
        raise HTTPException(status_code=400, detail="Amount must be between 1 and 50000")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.credit_balance = (target.credit_balance or 0) + amount
    target.credit_purchased = (target.credit_purchased or 0) + amount
    ct = CreditTransaction(
        user_id=target.id,
        transaction_type=TransactionType.ADMIN_ADJUSTMENT.value,
        amount=amount,
        balance_after=target.credit_balance,
        description=reason[:500],
        idempotency_key=f"admin-grant-{target.id}-{_uuid.uuid4()}",
        performed_by_user_id=user.id,
    )
    db.add(ct)
    db.commit()
    logger.info("admin_credit_granted", extra={"admin": user.email, "target": target.email, "amount": amount})
    return RedirectResponse(url=f"/admin/users/{user_id}/edit#credits", status_code=303)


@router.post("/admin/users/{user_id}/deduct-credit")
async def admin_deduct_credit(
    user_id: int,
    request: Request,
    amount: int = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: deduct credits from a user (creates CreditTransaction audit record)."""
    _admin_guard(user)
    if amount < 1 or amount > 50000:
        raise HTTPException(status_code=400, detail="Amount must be between 1 and 50000")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if amount > (target.credit_balance or 0):
        raise HTTPException(status_code=400, detail="Cannot deduct more than current balance")
    target.credit_balance = (target.credit_balance or 0) - amount
    ct = CreditTransaction(
        user_id=target.id,
        transaction_type=TransactionType.ADMIN_ADJUSTMENT.value,
        amount=-amount,
        balance_after=target.credit_balance,
        description=reason[:500],
        idempotency_key=f"admin-deduct-{target.id}-{_uuid.uuid4()}",
        performed_by_user_id=user.id,
    )
    db.add(ct)
    db.commit()
    logger.info("admin_credit_deducted", extra={"admin": user.email, "target": target.email, "amount": amount})
    return RedirectResponse(url=f"/admin/users/{user_id}/edit#credits", status_code=303)


@router.post("/admin/users/{user_id}/grant-license")
async def admin_grant_license(
    user_id: int,
    request: Request,
    specialization_type: str = Form(...),
    reason: str = Form(...),
    expires_at: str = Form(default=""),  # optional "YYYY-MM-DD"; empty = perpetual
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: grant a new license to a user (creates LicenseProgression audit record)."""
    _admin_guard(user)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Validate specialization type
    try:
        spec = SpecializationType(specialization_type)
    except ValueError:
        return RedirectResponse(
            url=f"/admin/users/{user_id}/edit?error=invalid_spec&error_detail={specialization_type}#licenses",
            status_code=303,
        )

    # Check for existing active license — redirect with user-friendly error instead of
    # returning JSON 400 (which breaks the admin_base.html CSRF JS handler)
    existing = (
        db.query(UserLicense)
        .filter(UserLicense.user_id == user_id, UserLicense.specialization_type == spec.value, UserLicense.is_active == True)
        .first()
    )
    if existing:
        return RedirectResponse(
            url=f"/admin/users/{user_id}/edit?error=duplicate_license&error_detail={spec.value}#licenses",
            status_code=303,
        )

    # Parse optional expiry date (blank = perpetual license)
    expires_at_dt = None
    if expires_at and expires_at.strip():
        try:
            expires_at_dt = datetime.strptime(expires_at.strip(), "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid expires_at format (expected YYYY-MM-DD)")
        if expires_at_dt <= datetime.now(timezone.utc).replace(tzinfo=None):
            raise HTTPException(status_code=400, detail="expires_at must be in the future")

    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    new_license = UserLicense(
        user_id=user_id,
        specialization_type=spec.value,
        started_at=now_naive,
        issued_at=now_naive,
        is_active=True,
        expires_at=expires_at_dt,
    )
    db.add(new_license)
    db.flush()  # get new_license.id

    progression = LicenseProgression(
        user_license_id=new_license.id,
        from_level=0,
        to_level=0,
        advanced_by=user.id,
        advancement_reason=reason[:500],
        requirements_met="INITIAL_GRANT",
        advanced_at=now,
    )
    db.add(progression)
    db.commit()
    logger.info("admin_license_granted", extra={"admin": user.email, "target": target.email, "spec": spec.value})
    return RedirectResponse(url=f"/admin/users/{user_id}/edit#licenses", status_code=303)


@router.post("/admin/users/{user_id}/revoke-license/{license_id}")
async def admin_revoke_license(
    user_id: int,
    license_id: int,
    request: Request,
    reason: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: revoke a user's license (creates LicenseProgression audit record)."""
    _admin_guard(user)
    license = (
        db.query(UserLicense)
        .filter(UserLicense.id == license_id, UserLicense.user_id == user_id)
        .first()
    )
    if not license:
        raise HTTPException(status_code=404, detail="License not found")

    if not license.is_active:
        return RedirectResponse(
            url=f"/admin/users/{user_id}/edit?error=already_revoked&error_detail={license_id}#licenses",
            status_code=303,
        )

    now = datetime.now(timezone.utc)
    license.is_active = False
    progression = LicenseProgression(
        user_license_id=license.id,
        from_level=license.current_level or 0,
        to_level=-1,
        advanced_by=user.id,
        advancement_reason=reason[:500],
        requirements_met="REVOKED",
        advanced_at=now,
    )
    db.add(progression)
    db.commit()
    logger.info("admin_license_revoked", extra={"admin": user.email, "license_id": license_id})
    return RedirectResponse(url=f"/admin/users/{user_id}/edit#licenses", status_code=303)


@router.post("/admin/users/{user_id}/renew-license/{license_id}")
async def admin_renew_license(
    user_id: int,
    license_id: int,
    request: Request,
    new_expires_at: str = Form(...),  # "YYYY-MM-DD" required
    reason: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: renew a license — set new expires_at + record LicenseProgression('RENEWED')."""
    _admin_guard(user)
    license = (
        db.query(UserLicense)
        .filter(UserLicense.id == license_id, UserLicense.user_id == user_id)
        .first()
    )
    if not license:
        raise HTTPException(status_code=404, detail="License not found")

    if not license.is_active:
        return RedirectResponse(
            url=f"/admin/users/{user_id}/edit?error=cannot_renew_revoked&error_detail={license_id}#licenses",
            status_code=303,
        )

    try:
        new_expires_dt = datetime.strptime(new_expires_at.strip(), "%Y-%m-%d").replace(
            hour=23, minute=59, second=59
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format (expected YYYY-MM-DD)")

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if new_expires_dt <= now_naive:
        raise HTTPException(status_code=400, detail="New expiry date must be in the future")

    now = datetime.now(timezone.utc)
    license.expires_at = new_expires_dt
    license.last_renewed_at = now_naive

    progression = LicenseProgression(
        user_license_id=license.id,
        from_level=license.current_level or 0,
        to_level=license.current_level or 0,
        advanced_by=user.id,
        advancement_reason=(reason[:500] if reason else "License renewed by admin"),
        requirements_met="RENEWED",
        advanced_at=now,
    )
    db.add(progression)
    db.commit()
    logger.info(
        "admin_license_renewed",
        extra={"admin": user.email, "license_id": license_id, "new_expires": new_expires_at}
    )
    return RedirectResponse(url=f"/admin/users/{user_id}/edit#licenses", status_code=303)
