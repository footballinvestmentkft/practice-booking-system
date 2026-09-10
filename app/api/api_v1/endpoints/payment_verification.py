from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from pydantic import BaseModel

from ....database import get_db
from ....dependencies import get_current_admin_user_web, get_current_admin_user
from ....models.user import User, UserRole
from ....models.specialization import SpecializationType
from ....models.license import UserLicense
from ....services.player_identity_service import (
    PlayerIdentityError,
    issue_football_player_entitlement,
)

router = APIRouter()


class PaymentVerificationRequest(BaseModel):
    """Request body for payment verification"""
    specializations: List[str]  # List of SpecializationType enum values


class SpecializationRequest(BaseModel):
    """Request body for adding/removing a single specialization"""
    specialization_type: str  # SpecializationType enum value


@router.get("/students")
async def get_students_payment_status(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_web)
) -> Any:
    """
    Get all students with their payment verification status (Admin only)
    """
    students = db.query(User).filter(User.role == UserRole.STUDENT).all()

    # Add payment verification status to response
    student_responses = []
    for student in students:
        student_data = {
            "id": student.id,
            "name": student.name,
            "email": student.email,
            "nickname": student.nickname,
            "role": student.role.value,
            "is_active": student.is_active,
            "payment_verified": student.payment_verified,
            "payment_verified_at": student.payment_verified_at,
            "payment_verified_by": student.payment_verified_by,
            "payment_status_display": student.payment_status_display,
            "can_enroll_in_semester": student.can_enroll_in_semester,
            "specialization": student.specialization.value if student.specialization else None,
            "onboarding_completed": student.onboarding_completed,
            "created_at": student.created_at,
            "credit_payment_reference": student.credit_payment_reference  # Payment reference code for verification
        }
        student_responses.append(student_data)

    return student_responses


@router.post("/students/{student_id}/verify")
async def verify_student_payment(
    request: Request,
    student_id: int,
    payment_request: PaymentVerificationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)  # Changed to support both web cookies and API Bearer tokens
) -> Any:
    """
    Verify payment for a specific student and set their specialization (Admin only)
    """
    # Get student
    student = db.query(User).filter(
        User.id == student_id,
        User.role == UserRole.STUDENT
    ).first()

    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found"
        )

    # Validate specializations
    if not payment_request.specializations or len(payment_request.specializations) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one specialization must be selected"
        )

    validated_specializations = []
    for spec_str in payment_request.specializations:
        try:
            spec = SpecializationType(spec_str)
            validated_specializations.append(spec)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid specialization: {spec_str}. Must be one of: {', '.join([s.value for s in SpecializationType])}"
            )

    # Set primary specialization (first one selected)
    student.specialization = validated_specializations[0]

    # Create or update UserLicense entries for all selected specializations
    created_licenses = []
    for spec in validated_specializations:
        # Check if license already exists
        existing_license = db.query(UserLicense).filter(
            UserLicense.user_id == student.id,
            UserLicense.specialization_type == spec.value
        ).first()

        if not existing_license and spec is SpecializationType.LFA_FOOTBALL_PLAYER:
            try:
                issue_football_player_entitlement(
                    db,
                    user=student,
                    payment_verified=True,
                )
            except PlayerIdentityError as exc:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=exc.code,
                ) from exc
            created_licenses.append(spec.value)
        elif not existing_license:
            # Create new license — student must still complete onboarding form
            new_license = UserLicense(
                user_id=student.id,
                specialization_type=spec.value,
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc),
            )
            db.add(new_license)
            created_licenses.append(spec.value)

    # Verify payment
    student.verify_payment(current_user)
    db.commit()
    db.refresh(student)

    return {
        "message": f"Payment verified for {student.name} - {len(validated_specializations)} specialization(s)",
        "student_id": student.id,
        "verified_by": current_user.name,
        "verified_at": student.payment_verified_at,
        "payment_verified": student.payment_verified,
        "primary_specialization": student.specialization.value if student.specialization else None,
        "all_specializations": [s.value for s in validated_specializations],
        "newly_created_licenses": created_licenses
    }


@router.post("/students/{student_id}/unverify")
async def unverify_student_payment(
    request: Request,
    student_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_web)
) -> Any:
    """
    Remove payment verification for a specific student (Admin only)
    """
    # Get student
    student = db.query(User).filter(
        User.id == student_id,
        User.role == UserRole.STUDENT
    ).first()
    
    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found"
        )
    
    # Unverify payment
    student.unverify_payment()
    db.commit()
    db.refresh(student)
    
    return {
        "message": f"Payment verification removed for {student.name}",
        "student_id": student.id,
        "unverified_by": current_user.name,
        "payment_verified": student.payment_verified
    }


@router.get("/students/{student_id}/status")
async def get_student_payment_status(
    request: Request,
    student_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_web)
) -> Any:
    """
    Get payment verification status for a specific student (Admin only)
    """
    # Get student
    student = db.query(User).filter(
        User.id == student_id,
        User.role == UserRole.STUDENT
    ).first()
    
    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found"
        )
    
    verifier_name = None
    if student.payment_verified_by:
        verifier = db.query(User).filter(User.id == student.payment_verified_by).first()
        verifier_name = verifier.name if verifier else "Unknown"
    
    return {
        "student_id": student.id,
        "student_name": student.name,
        "student_email": student.email,
        "payment_verified": student.payment_verified,
        "payment_verified_at": student.payment_verified_at,
        "payment_verified_by": student.payment_verified_by,
        "payment_verifier_name": verifier_name,
        "payment_status_display": student.payment_status_display,
        "can_enroll_in_semester": student.can_enroll_in_semester
    }


@router.post("/students/{student_id}/add-specialization")
async def add_student_specialization(
    request: Request,
    student_id: int,
    spec_request: SpecializationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)  # Changed to support both web cookies and API Bearer tokens
) -> Any:
    """
    Add a single specialization to a student (Admin only)
    Creates a UserLicense entry for the specified specialization.
    Supports both web cookies and API Bearer tokens.
    """
    # Get student
    student = db.query(User).filter(
        User.id == student_id,
        User.role == UserRole.STUDENT
    ).first()

    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found"
        )

    # Validate specialization
    try:
        spec = SpecializationType(spec_request.specialization_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid specialization: {spec_request.specialization_type}. Must be one of: {', '.join([s.value for s in SpecializationType])}"
        )

    # Check if license already exists
    existing_license = db.query(UserLicense).filter(
        UserLicense.user_id == student.id,
        UserLicense.specialization_type == spec.value
    ).first()

    if existing_license:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Student already has {spec.value} specialization"
        )

    if spec is SpecializationType.LFA_FOOTBALL_PLAYER:
        try:
            issue_football_player_entitlement(
                db,
                user=student,
                payment_verified=True,
            )
        except PlayerIdentityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=exc.code,
            ) from exc
    else:
        # Non-Player programs retain their existing provisioning path.
        new_license = UserLicense(
            user_id=student.id,
            specialization_type=spec.value,
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
        )
        db.add(new_license)

    # If student has no primary specialization, set this as primary
    if not student.specialization:
        student.specialization = spec

    # Mark payment as verified if not already
    if not student.payment_verified:
        student.verify_payment(current_user)

    db.commit()
    db.refresh(student)

    return {
        "success": True,
        "message": f"Added {spec.value.replace('_', ' ')} specialization to {student.name}",
        "student_id": student.id,
        "specialization_added": spec.value,
        "added_by": current_user.name
    }


@router.post("/students/{student_id}/remove-specialization")
async def remove_student_specialization(
    request: Request,
    student_id: int,
    spec_request: SpecializationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_web)
) -> Any:
    """
    Remove a single specialization from a student (Admin only)
    Deletes the UserLicense entry for the specified specialization.
    If this is the student's only specialization, also unverifies payment.
    """
    # Get student
    student = db.query(User).filter(
        User.id == student_id,
        User.role == UserRole.STUDENT
    ).first()

    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found"
        )

    # Validate specialization
    try:
        spec = SpecializationType(spec_request.specialization_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid specialization: {spec_request.specialization_type}"
        )

    # Find the license to remove
    license_to_remove = db.query(UserLicense).filter(
        UserLicense.user_id == student.id,
        UserLicense.specialization_type == spec.value
    ).first()

    if not license_to_remove:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student does not have {spec.value} specialization"
        )

    # Delete the license
    db.delete(license_to_remove)

    # Check remaining licenses
    remaining_licenses = db.query(UserLicense).filter(
        UserLicense.user_id == student.id,
        UserLicense.specialization_type != spec.value
    ).all()

    # If no licenses remain, unverify payment and clear primary specialization
    if not remaining_licenses:
        student.unverify_payment()
        student.specialization = None
    # If the removed specialization was the primary, set a new primary
    elif student.specialization == spec:
        student.specialization = SpecializationType(remaining_licenses[0].specialization_type)

    db.commit()
    db.refresh(student)

    return {
        "success": True,
        "message": f"Removed {spec.value.replace('_', ' ')} specialization from {student.name}",
        "student_id": student.id,
        "specialization_removed": spec.value,
        "removed_by": current_user.name,
        "remaining_licenses": len(remaining_licenses),
        "payment_still_verified": student.payment_verified
    }
