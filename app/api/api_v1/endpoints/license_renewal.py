"""
💰 License Renewal API Endpoints
=================================
Admin endpoints for managing license renewals.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from app.database import get_db
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.services.license_renewal_service import (
    LicenseRenewalService,
    InsufficientCreditsError,
    LicenseNotFoundError
)
from app.dependencies import get_current_user

router = APIRouter()


# ============================================================================
# Pydantic Schemas
# ============================================================================

class LicenseRenewalRequest(BaseModel):
    """Request to renew a license"""
    license_id: int = Field(..., description="ID of license to renew")
    renewal_months: int = Field(..., description="12 or 24 months", ge=12, le=24)
    payment_verified: bool = Field(True, description="Whether admin verified payment")


class LicenseRenewalResponse(BaseModel):
    """Response after successful renewal"""
    success: bool
    license_id: int
    specialization_type: str
    current_level: int
    new_expiration: datetime
    credits_charged: int
    remaining_credits: int
    renewal_months: int
    message: str


class LicenseStatusResponse(BaseModel):
    """License status details"""
    license_id: int
    user_id: int
    specialization_type: str
    current_level: int
    is_active: bool
    expires_at: Optional[datetime]
    last_renewed_at: Optional[datetime]
    days_until_expiration: Optional[int]
    is_expired: bool
    needs_renewal: bool
    status: str  # "active", "expiring_soon", "expired", "perpetual"
    renewal_cost: int


class ExpiringLicensesSummary(BaseModel):
    """Summary of expiring licenses"""
    total_expiring: int
    licenses: List[LicenseStatusResponse]


# ============================================================================
# License Renewal Endpoints
# ============================================================================

@router.post("/renew", response_model=LicenseRenewalResponse, status_code=status.HTTP_200_OK)
def renew_license(
    request: LicenseRenewalRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=16,
        max_length=255,
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Renew a license for 12 or 24 months (Admin only).

    Deducts renewal_cost credits from user's balance and extends license expiration.
    """
    # Authorization - only admins
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can renew licenses"
        )

    try:
        # Validate renewal period
        if request.renewal_months not in [12, 24]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Renewal period must be 12 or 24 months"
            )

        # Renew license
        result = LicenseRenewalService.renew_license(
            license_id=request.license_id,
            renewal_months=request.renewal_months,
            admin_id=current_user.id,
            db=db,
            idempotency_key=idempotency_key,
            payment_verified=request.payment_verified
        )

        return LicenseRenewalResponse(**result)

    except LicenseNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except InsufficientCreditsError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(e)
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/status/{license_id}", response_model=LicenseStatusResponse)
def get_license_status(
    license_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get detailed status of a license including expiration info.

    Accessible by admins or the license owner.
    """
    # Get license
    license = db.query(UserLicense).filter(UserLicense.id == license_id).first()
    if not license:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"License {license_id} not found"
        )

    # Authorization - admin or license owner
    if current_user.role != UserRole.ADMIN and current_user.id != license.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own licenses"
        )

    # Get status
    status_info = LicenseRenewalService.get_license_status(license)

    return LicenseStatusResponse(
        license_id=license.id,
        user_id=license.user_id,
        specialization_type=license.specialization_type,
        current_level=license.current_level,
        is_active=license.is_active,
        expires_at=license.expires_at,
        last_renewed_at=license.last_renewed_at,
        days_until_expiration=status_info['days_until_expiration'],
        is_expired=status_info['is_expired'],
        needs_renewal=status_info['needs_renewal'],
        status=status_info['status'],
        renewal_cost=license.renewal_cost
    )


@router.get("/expiring", response_model=ExpiringLicensesSummary)
def get_expiring_licenses(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all licenses expiring within specified days (Admin only).

    Useful for proactive renewal reminders.
    """
    # Authorization - only admins
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can view expiring licenses"
        )

    # Get expiring licenses
    expiring_licenses = LicenseRenewalService.get_expiring_licenses(
        days_threshold=days,
        db=db
    )

    # Format response
    licenses_data = []
    for license in expiring_licenses:
        status_info = LicenseRenewalService.get_license_status(license)
        licenses_data.append(
            LicenseStatusResponse(
                license_id=license.id,
                user_id=license.user_id,
                specialization_type=license.specialization_type,
                current_level=license.current_level,
                is_active=license.is_active,
                expires_at=license.expires_at,
                last_renewed_at=license.last_renewed_at,
                days_until_expiration=status_info['days_until_expiration'],
                is_expired=status_info['is_expired'],
                needs_renewal=status_info['needs_renewal'],
                status=status_info['status'],
                renewal_cost=license.renewal_cost
            )
        )

    return ExpiringLicensesSummary(
        total_expiring=len(licenses_data),
        licenses=licenses_data
    )


@router.post("/check-expirations", status_code=status.HTTP_200_OK)
def bulk_check_expirations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Bulk check all licenses and deactivate expired ones (Admin only).

    This should be run as a scheduled task (cronjob).
    Returns summary of how many licenses were checked/expired.
    """
    # Authorization - only admins
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can bulk check expirations"
        )

    # Run bulk check
    result = LicenseRenewalService.bulk_check_expirations(db)

    return {
        "success": True,
        "total_checked": result['total_checked'],
        "expired_count": result['expired_count'],
        "still_active": result['still_active'],
        "message": f"Checked {result['total_checked']} licenses, deactivated {result['expired_count']} expired"
    }
