"""
Tournament Cancellation & Refund Module

This module handles tournament cancellation workflow with automatic credit refunds.

Business Rules:
1. Only ADMIN can cancel tournaments
2. Cannot cancel COMPLETED tournaments
3. APPROVED enrollments: Full refund
4. PENDING enrollments: Auto-reject (no payment yet)
5. Refunds go to user_license that paid
6. All enrollments marked inactive
7. Tournament status → CANCELLED
8. Full audit trail created

Created: 2026-01-23 (Feature development post-refactoring)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterStatus
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.license import UserLicense
from app.models.credit_transaction import CreditTransaction, TransactionType
from app.dependencies import get_current_user
from app.services.credit_service import CreditService

router = APIRouter()


# ============================================================================
# REQUEST/RESPONSE SCHEMAS
# ============================================================================

class CancellationRequest(BaseModel):
    """Request schema for tournament cancellation"""
    reason: str
    notify_participants: bool = True


class RefundDetails(BaseModel):
    """Details of a single refund"""
    enrollment_id: int
    user_id: int
    user_name: str
    user_email: str
    amount_refunded: int


class CancellationResponse(BaseModel):
    """Response schema for tournament cancellation"""
    message: str
    tournament_id: int
    tournament_name: str
    cancelled_at: str
    cancelled_by: Dict[str, Any]
    refunds_processed: Dict[str, Any]
    enrollments_rejected: Dict[str, Any]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def process_refund(
    db: Session,
    enrollment: SemesterEnrollment,
    tournament: Semester,
    admin_user: User,
    reason: str
) -> Optional[RefundDetails]:
    """
    Process credit refund for a single enrollment.

    Args:
        db: Database session
        enrollment: Enrollment to refund
        tournament: Tournament being cancelled
        admin_user: Admin performing cancellation
        reason: Cancellation reason

    Returns:
        RefundDetails if refund processed, None if no refund needed
    """
    # Only refund APPROVED enrollments (these have been paid)
    if enrollment.request_status != EnrollmentStatus.APPROVED:
        return None

    # Get the user_license that was used for enrollment
    user_license = db.query(UserLicense).filter(
        UserLicense.id == enrollment.user_license_id
    ).first()

    if not user_license:
        # Enrollment has no associated license - shouldn't happen but handle gracefully
        return None

    # Calculate refund amount
    refund_amount = tournament.enrollment_cost or 0

    if refund_amount == 0:
        # Free tournament, no refund needed
        return None

    user = enrollment.user
    credit_service = CreditService(db)
    idempotency_key = f"refund-tournament-{tournament.id}-enrollment-{enrollment.id}"

    transaction = credit_service.credit(
        user=user,
        transaction_type=TransactionType.REFUND.value,
        amount=refund_amount,
        description=f"Tournament cancellation refund: {tournament.name} (ID: {tournament.id}). Reason: {reason}",
        idempotency_key=idempotency_key,
    )
    transaction.context_user_license_id = user_license.id
    transaction.semester_id = tournament.id
    transaction.enrollment_id = enrollment.id

    return RefundDetails(
        enrollment_id=enrollment.id,
        user_id=user.id,
        user_name=user.name or "Unknown",
        user_email=user.email,
        amount_refunded=refund_amount
    )


def reject_pending_enrollment(
    db: Session,
    enrollment: SemesterEnrollment
) -> bool:
    """
    Auto-reject a pending enrollment (no payment, so no refund).

    Args:
        db: Database session
        enrollment: Enrollment to reject

    Returns:
        True if rejected, False if not pending
    """
    if enrollment.request_status != EnrollmentStatus.PENDING:
        return False

    enrollment.request_status = EnrollmentStatus.REJECTED
    return True


# ============================================================================
# CANCELLATION ENDPOINT
# ============================================================================

@router.post("/{tournament_id}/cancel", response_model=CancellationResponse)
def cancel_tournament(
    tournament_id: int,
    request_data: CancellationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Cancel a tournament and process refunds for enrolled participants.

    This endpoint:
    1. Validates admin authorization
    2. Checks tournament eligibility for cancellation
    3. Processes full refunds for APPROVED enrollments
    4. Auto-rejects PENDING enrollments
    5. Marks all enrollments as inactive
    6. Updates tournament status to CANCELLED
    7. Creates full audit trail

    **Authorization:** ADMIN only

    **Validations:**
    - Current user is ADMIN
    - Tournament exists
    - Tournament is not already COMPLETED
    - Cancellation reason provided

    **Actions Performed:**
    - Refund credits to user_licenses (APPROVED enrollments)
    - Create REFUND transactions
    - Auto-reject PENDING enrollments
    - Mark all enrollments as is_active=False
    - Update tournament status to CANCELLED
    - Optional: Send notifications

    **Returns:**
    - Cancellation summary
    - Refund details (count, total, per-user breakdown)
    - Rejected enrollment details

    **Example Response:**
    ```json
    {
        "message": "Tournament cancelled successfully",
        "tournament_id": 123,
        "tournament_name": "Youth Football Tournament 2026",
        "cancelled_at": "2026-01-23T22:30:00",
        "cancelled_by": {
            "user_id": 1,
            "name": "Admin User",
            "email": "admin@lfa.com"
        },
        "refunds_processed": {
            "count": 8,
            "total_credits_refunded": 4000,
            "enrollments_refunded": [
                {
                    "enrollment_id": 45,
                    "user_id": 10,
                    "user_name": "John Doe",
                    "user_email": "john@example.com",
                    "amount_refunded": 500
                }
            ]
        },
        "enrollments_rejected": {
            "count": 2,
            "enrollment_ids": [46, 47]
        }
    }
    ```

    **Raises:**
    - 403 FORBIDDEN: User is not an admin
    - 404 NOT FOUND: Tournament not found
    - 400 BAD REQUEST: Tournament already completed or invalid state
    """
    # ============================================================================
    # VALIDATION 1: Current user is ADMIN
    # ============================================================================
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "authorization_error",
                "message": "Only admins can cancel tournaments",
                "current_role": current_user.role.value,
                "required_role": "ADMIN"
            }
        )

    # ============================================================================
    # VALIDATION 2: Tournament exists
    # ============================================================================
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()

    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "tournament_not_found",
                "message": f"Tournament {tournament_id} not found",
                "tournament_id": tournament_id
            }
        )

    # ============================================================================
    # VALIDATION 3: Cannot cancel completed tournaments
    # ============================================================================
    if tournament.status == SemesterStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "cannot_cancel_completed",
                "message": "Cannot cancel a completed tournament",
                "tournament_id": tournament_id,
                "tournament_name": tournament.name,
                "current_status": tournament.status.value
            }
        )

    # ============================================================================
    # VALIDATION 4: Cannot cancel already cancelled tournaments
    # ============================================================================
    if tournament.status == SemesterStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "already_cancelled",
                "message": "Tournament is already cancelled",
                "tournament_id": tournament_id,
                "tournament_name": tournament.name
            }
        )

    # ============================================================================
    # VALIDATION 5: Cancellation reason required
    # ============================================================================
    if not request_data.reason or len(request_data.reason.strip()) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "reason_required",
                "message": "Cancellation reason is required"
            }
        )

    # ============================================================================
    # ACTION 1: Get all enrollments for this tournament
    # ============================================================================
    enrollments = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active == True
    ).all()

    # ============================================================================
    # ACTION 2: Process refunds for APPROVED enrollments
    # ============================================================================
    refunds_processed: List[RefundDetails] = []
    total_credits_refunded = 0

    for enrollment in enrollments:
        refund = process_refund(
            db=db,
            enrollment=enrollment,
            tournament=tournament,
            admin_user=current_user,
            reason=request_data.reason
        )

        if refund:
            refunds_processed.append(refund)
            total_credits_refunded += refund.amount_refunded

    # ============================================================================
    # ACTION 3: Auto-reject PENDING enrollments
    # ============================================================================
    rejected_enrollment_ids: List[int] = []

    for enrollment in enrollments:
        if reject_pending_enrollment(db, enrollment):
            rejected_enrollment_ids.append(enrollment.id)

    # ============================================================================
    # ACTION 4: Mark all enrollments as inactive
    # ============================================================================
    for enrollment in enrollments:
        enrollment.is_active = False

    # ============================================================================
    # ACTION 5: Update tournament status to CANCELLED
    # ============================================================================
    tournament.status = SemesterStatus.CANCELLED
    tournament.tournament_status = "CANCELLED"  # Also update tournament-specific status

    # ============================================================================
    # ACTION 6: Record cancellation metadata (via tournament update timestamp)
    # ============================================================================
    # The updated_at field will be automatically updated
    # Additional cancellation audit can be added via tournament_status_history if needed

    # ============================================================================
    # COMMIT TRANSACTION
    # ============================================================================
    db.commit()

    # ============================================================================
    # ACTION 7: Send notifications (if requested)
    # ============================================================================
    if request_data.notify_participants:
        # TODO: Implement notification service call
        # from app.services.notification_service import send_tournament_cancellation_notification
        # for enrollment in enrollments:
        #     send_tournament_cancellation_notification(
        #         db=db,
        #         user_id=enrollment.user_id,
        #         tournament=tournament,
        #         refund_amount=refund_amount if refunded else 0,
        #         reason=request_data.reason
        #     )
        pass

    # ============================================================================
    # RETURN SUCCESS RESPONSE
    # ============================================================================
    cancelled_at = datetime.utcnow()

    return {
        "message": "Tournament cancelled successfully",
        "tournament_id": tournament.id,
        "tournament_name": tournament.name,
        "cancelled_at": cancelled_at.isoformat(),
        "cancelled_by": {
            "user_id": current_user.id,
            "name": current_user.name,
            "email": current_user.email
        },
        "refunds_processed": {
            "count": len(refunds_processed),
            "total_credits_refunded": total_credits_refunded,
            "enrollments_refunded": [r.dict() for r in refunds_processed]
        },
        "enrollments_rejected": {
            "count": len(rejected_enrollment_ids),
            "enrollment_ids": rejected_enrollment_ids
        }
    }
