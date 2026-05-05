"""
Tournament Enrollment API Endpoint
Students can enroll in available tournaments
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta

from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterCategory
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.license import UserLicense
from app.models.session import Session as SessionModel
from app.models.booking import Booking, BookingStatus
from app.models.credit_transaction import CreditTransaction
from app.schemas.tournament import EnrollmentResponse, EnrollmentConflict
from app.services.age_category_service import (
    get_automatic_age_category,
    get_current_season_year,
    calculate_age_at_season_start
)
from app.services.enrollment_conflict_service import EnrollmentConflictService
from app.services.tournament.validation import validate_tournament_enrollment_age, check_duplicate_enrollment, get_allowed_age_groups
from app.services.audit_service import AuditService
from app.models.audit_log import AuditAction
import logging
import traceback

router = APIRouter()

# Module-level logging to confirm this file loads
_module_logger = logging.getLogger(__name__)
_module_logger.error(f"🔥 TOURNAMENTS/ENROLL.PY MODULE LOADED SUCCESSFULLY")


@router.post("/{tournament_id}/enroll", response_model=EnrollmentResponse)
def enroll_in_tournament(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Enroll current student in a tournament

    **Authorization:** Student role only

    **Validations:**
    1. Tournament exists and tournament_status is ENROLLMENT_OPEN or IN_PROGRESS
    2. Student has LFA_FOOTBALL_PLAYER license
    3. Age category enrollment rules (UPWARD ENROLLMENT - no instructor approval needed):
       - PRE (5-13): Can enroll in PRE, YOUTH, AMATEUR, PRO (all above)
       - YOUTH (14-18): Can enroll in YOUTH, AMATEUR, PRO (all above)
       - AMATEUR (18+): Can enroll in AMATEUR, PRO (all above)
       - PRO (18+): Can enroll in PRO only (already at top)
    4. Student not already enrolled
    5. Sufficient credit balance
    6. Conflict check (WARNING only, non-blocking)

    **Creates:**
    - SemesterEnrollment record (AUTO-APPROVED, is_active=True)
    - Deducts enrollment_cost from credit balance (INSTANT payment)
    - Assigns age_category based on age at season start (July 1)

    **Returns:**
    - Enrollment details
    - Conflict warnings (if any)
    - Credits remaining after enrollment
    """
    logger = logging.getLogger(__name__)
    logger.error(f"🚀 ENROLLMENT START - Tournament: {tournament_id}, User: {current_user.id}, Email: {current_user.email}")

    # 1. Fetch tournament
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tournament not found"
        )

    # 2. Block self-enrollment for PROMOTION_EVENT — participants come from sponsor campaign audience
    if tournament.semester_category == SemesterCategory.PROMOTION_EVENT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This event accepts participants by invitation only.",
        )

    # 3. Verify tournament status (check tournament_status field, NOT the old status field)
    # Only ENROLLMENT_OPEN and IN_PROGRESS allow enrollment
    # ENROLLMENT_CLOSED does NOT allow new enrollments (enrollment period ended)
    if tournament.tournament_status not in ["ENROLLMENT_OPEN", "IN_PROGRESS"]:
        # Audit enrollment failure for observability
        audit_service = AuditService(db)
        audit_service.log(
            action=AuditAction.USER_UPDATED,  # Reuse existing action (no ENROLLMENT_FAILED yet)
            user_id=current_user.id,
            details={
                "event": "enrollment_failed",
                "tournament_id": tournament_id,
                "tournament_name": tournament.name,
                "tournament_status": tournament.tournament_status,
                "failure_reason": "tournament_not_accepting_enrollments",
                "allowed_statuses": ["ENROLLMENT_OPEN", "IN_PROGRESS"],
                "student_balance": current_user.credit_balance,
                "enrollment_cost": tournament.enrollment_cost
            }
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tournament not accepting enrollments (tournament_status: {tournament.tournament_status}). Only ENROLLMENT_OPEN and IN_PROGRESS tournaments accept enrollments."
        )

    # 2.5. Verify enrollment deadline (1 hour before first tournament session)
    first_session = db.query(SessionModel).filter(
        SessionModel.semester_id == tournament_id
    ).order_by(SessionModel.date_start).first()

    if first_session and first_session.date_start:
        enrollment_deadline = first_session.date_start - timedelta(hours=1)
        if datetime.utcnow() >= enrollment_deadline:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Enrollment closed - tournament starting soon (deadline: {enrollment_deadline.strftime('%Y-%m-%d %H:%M')} UTC)"
            )

    # 3. Verify student role
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can enroll in tournaments"
        )

    # 4. Get student's LFA_FOOTBALL_PLAYER license
    license = db.query(UserLicense).filter(
        UserLicense.user_id == current_user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER"
    ).first()

    if not license:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LFA Football Player license not found. Please unlock this specialization first."
        )

    # 4.5 Onboarding guard: effective_onboarding = flag OR skills OR legacy enrollment
    has_enrollment_for_lic = db.query(SemesterEnrollment.id).filter(
        SemesterEnrollment.user_license_id == license.id
    ).first() is not None
    effective_onboarding = (
        license.onboarding_completed
        or license.football_skills is not None
        or has_enrollment_for_lic
    )
    if not effective_onboarding:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Complete your LFA Football Player onboarding before enrolling in tournaments."
        )

    # 5. Calculate age category at season start (July 1)
    if not current_user.date_of_birth:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Date of birth not set. Please set your date of birth in your profile."
        )

    season_year = get_current_season_year()
    age_at_season_start = calculate_age_at_season_start(current_user.date_of_birth, season_year)
    player_age_category = get_automatic_age_category(age_at_season_start)

    # For 18+ users, automatically infer category from tournament's allowed age groups
    if not player_age_category:
        allowed = get_allowed_age_groups(tournament)
        upper_allowed = [ag for ag in (allowed or []) if ag in ["AMATEUR", "PRO"]]
        if len(upper_allowed) == 1:
            player_age_category = upper_allowed[0]
            logger.info(f"✅ Auto-assigned age category {player_age_category} to user {current_user.id} based on tournament {tournament.code}")
        elif len(upper_allowed) > 1:
            # Multi-age event with multiple adult categories — admin must assign explicitly
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This event spans multiple adult age categories. "
                       "Please ask an administrator to assign your age category before enrolling."
            )
        else:
            # No upper group (PRE/YOUTH only) — 18+ cannot enroll
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You are over 18 and cannot enroll in this tournament. Please enroll in AMATEUR or PRO tournaments."
            )

    # 6. Verify age category enrollment rules using get_allowed_age_groups
    allowed = get_allowed_age_groups(tournament)
    if allowed is not None and player_age_category not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Your age category ({player_age_category}) is not eligible for this event. "
                   f"Eligible categories: {allowed}"
        )

    # 7. Acquire row-level lock on the tournament row FIRST to prevent race conditions
    # B-03: This lock serializes concurrent enrollment requests. Two threads both
    # reaching this point will execute serially — the second waits until the first
    # commits or rolls back. This MUST come before duplicate enrollment check to
    # prevent TOCTOU race conditions.
    # Lock is held until db.commit() at step 16.
    db.query(Semester).filter(
        Semester.id == tournament_id
    ).with_for_update().one()

    # 8. Check not already enrolled using shared validation
    # CRITICAL: This check MUST come AFTER lock acquisition to prevent race conditions
    # where two threads both pass the duplicate check before either commits.
    is_unique, duplicate_message = check_duplicate_enrollment(
        db,
        current_user.id,
        tournament_id
    )

    if not is_unique:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=duplicate_message
        )

    # 9. Check tournament capacity (max_players)
    current_enrollment_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active == True,
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED
    ).count()

    max_players = tournament.max_players or 999  # Default if not set

    if current_enrollment_count >= max_players:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tournament is full: {current_enrollment_count}/{max_players} players enrolled"
        )

    # 10. Check credit balance (use user-level credit_balance, not license-level)
    enrollment_cost = tournament.enrollment_cost if tournament.enrollment_cost is not None else 500
    if current_user.credit_balance < enrollment_cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits: Need {enrollment_cost}, you have {current_user.credit_balance}"
        )

    # 11. Check conflicts (WARNING only - non-blocking)
    conflict_result = EnrollmentConflictService.check_session_time_conflict(
        user_id=current_user.id,
        semester_id=tournament_id,
        db=db
    )

    conflicts_list = []
    warnings_list = []

    if conflict_result and conflict_result.get("has_conflict"):
        for conflict in conflict_result.get("conflicts", []):
            conflicts_list.append(EnrollmentConflict(
                type=conflict.get("type", "time_overlap"),
                severity=conflict.get("severity", "warning"),
                message=conflict.get("message", ""),
                conflicting_session_id=conflict.get("session_id"),
                conflicting_semester_name=conflict.get("semester_name")
            ))

    if conflict_result and conflict_result.get("warnings"):
        warnings_list = conflict_result.get("warnings", [])

    # 12. Create enrollment record (AUTO-APPROVED ✅)
    enrollment = SemesterEnrollment(
        user_id=current_user.id,
        semester_id=tournament_id,
        user_license_id=license.id,
        age_category=player_age_category,
        request_status=EnrollmentStatus.APPROVED,  # ✅ AUTO-APPROVE (no manual approval)
        approved_at=datetime.utcnow(),
        approved_by=current_user.id,  # Self-enrollment
        payment_verified=True,  # ✅ INSTANT CREDIT PAYMENT (no manual verification)
        is_active=True,
        enrolled_at=datetime.utcnow(),
        requested_at=datetime.utcnow()
    )

    db.add(enrollment)

    # 13. B-02: Atomic credit deduction — prevents RACE-03 concurrent double-spend.
    # Uses SQL UPDATE ... WHERE credit_balance >= cost so that if another request
    # has already drained the balance between our check (step 10) and this UPDATE,
    # rowcount will be 0 and we abort cleanly without persisting a negative balance.
    _deduct = db.execute(
        sql_update(User)
        .where(User.id == current_user.id, User.credit_balance >= enrollment_cost)
        .values(credit_balance=User.credit_balance - enrollment_cost)
        .execution_options(synchronize_session=False)
    )
    if _deduct.rowcount == 0:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Insufficient credits (concurrent update): need {enrollment_cost}, "
                f"but balance was reduced by another concurrent request."
            )
        )
    db.refresh(current_user)  # Sync ORM state with DB after atomic UPDATE

    # 14. Create credit transaction record for audit trail
    # Generate unique idempotency key for transaction deduplication
    import hashlib
    idempotency_data = f"{current_user.id}:{tournament_id}:{license.id}:{datetime.utcnow().isoformat()}"
    idempotency_key = hashlib.sha256(idempotency_data.encode()).hexdigest()[:64]

    credit_transaction = CreditTransaction(
        user_license_id=license.id,
        transaction_type="TOURNAMENT_ENROLLMENT",
        amount=-enrollment_cost,  # Negative amount for deduction
        balance_after=current_user.credit_balance,
        description=f"Tournament enrollment: {tournament.name} ({tournament.code})",
        semester_id=tournament_id,
        enrollment_id=None,  # Will be updated after enrollment is committed
        idempotency_key=idempotency_key  # ✅ FIX: Set required idempotency_key
    )
    db.add(credit_transaction)
    db.flush()  # Get enrollment.id before commit

    # Update transaction with enrollment_id
    credit_transaction.enrollment_id = enrollment.id
    db.add(credit_transaction)

    # 15. Auto-create bookings for ALL tournament sessions (tournament enrollment = auto-booking)
    # Get ALL the tournament's sessions
    tournament_sessions = db.query(SessionModel).filter(
        SessionModel.semester_id == tournament_id
    ).all()

    if tournament_sessions:
        for tournament_session in tournament_sessions:
            # Create booking automatically LINKED to enrollment
            booking = Booking(
                user_id=current_user.id,
                session_id=tournament_session.id,
                enrollment_id=enrollment.id,  # ✅ NEW: Link to enrollment
                status=BookingStatus.CONFIRMED,
                created_at=datetime.utcnow()
            )
            db.add(booking)

        db.flush()  # Get booking IDs before commit
        logger.info(f"✅ Auto-created {len(tournament_sessions)} bookings for enrollment {enrollment.id}")
    else:
        logger.warning(f"⚠️ No sessions found for tournament {tournament_id} - bookings not created")

    # 16. Commit transaction
    logger = logging.getLogger(__name__)

    try:
        logger.info(f"🔍 PRE-COMMIT DEBUG:")
        logger.info(f"   - Tournament ID: {tournament_id}, Name: {tournament.name}")
        logger.info(f"   - User ID: {current_user.id}, Email: {current_user.email}")
        logger.info(f"   - Credit balance BEFORE: {current_user.credit_balance + enrollment_cost}")
        logger.info(f"   - Credit balance AFTER: {current_user.credit_balance}")
        logger.info(f"   - Enrollment ID (pre-flush): {enrollment.id}")
        logger.info(f"   - Transaction amount: {credit_transaction.amount}")
        logger.info(f"   - Transaction balance_after: {credit_transaction.balance_after}")

        db.commit()
        db.refresh(enrollment)
        db.refresh(current_user)

        logger.info(f"✅ ENROLLMENT SUCCESS: Enrollment ID = {enrollment.id}")
    except IntegrityError as e:
        # B-01: Partial unique index uq_active_enrollment blocked a concurrent duplicate.
        # The second of two simultaneous POST /enroll requests from the same player
        # reaches commit() but the DB rejects the INSERT with a unique violation.
        db.rollback()
        orig = str(getattr(e, 'orig', e))
        if "uq_active_enrollment" in orig:
            logger.warning(f"⚠️ Duplicate enrollment blocked by DB constraint for user {current_user.id}, tournament {tournament_id}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Already enrolled in this tournament (concurrent duplicate request blocked)"
            )
        logger.error(f"❌ ENROLLMENT IntegrityError: {orig}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Database constraint violation: {orig}"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"❌ TOURNAMENT ENROLLMENT FAILED: {str(e)}")
        logger.error(f"❌ ERROR TYPE: {type(e).__name__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create enrollment: {str(e)}"
        )

    # 13. Serialize response data
    enrollment_dict = {
        "id": enrollment.id,
        "user_id": enrollment.user_id,
        "semester_id": enrollment.semester_id,
        "user_license_id": enrollment.user_license_id,
        "age_category": enrollment.age_category,
        "request_status": enrollment.request_status.value,
        "payment_verified": enrollment.payment_verified,
        "is_active": enrollment.is_active,
        "enrolled_at": enrollment.enrolled_at.isoformat() if enrollment.enrolled_at else None,
        "approved_at": enrollment.approved_at.isoformat() if enrollment.approved_at else None
    }

    tournament_dict = {
        "id": tournament.id,
        "code": tournament.code,
        "name": tournament.name,
        "start_date": tournament.start_date.isoformat(),
        "end_date": tournament.end_date.isoformat(),
        "age_group": tournament.age_group,
        "enrollment_cost": enrollment_cost
    }

    # 14. Return response with warnings
    return {
        "success": True,
        "enrollment": enrollment_dict,
        "tournament": tournament_dict,
        "conflicts": conflicts_list,
        "warnings": warnings_list,
        "credits_remaining": current_user.credit_balance
    }


@router.delete("/{tournament_id}/unenroll")
def unenroll_from_tournament(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Unenroll (withdraw) current student from a tournament

    **Authorization:** Student role only

    **Business Rules:**
    1. Can only unenroll from ENROLLMENT_OPEN or IN_PROGRESS tournaments
    2. Cannot unenroll if tournament already COMPLETED or CANCELLED
    3. Credit refund: 50% penalty (user gets 50% back, 50% lost)
    4. Sets enrollment to is_active=False and request_status=WITHDRAWN
    5. Removes ALL bookings linked to this enrollment

    **Credit Refund Logic:**
    - Enrollment cost: 500 credits
    - Refund: 250 credits (50%)
    - Penalty: 250 credits (50% lost)

    **Returns:**
    - Success status
    - Refund amount
    - Final credit balance
    """
    logger = logging.getLogger(__name__)
    logger.info(f"🚫 UNENROLL START - Tournament: {tournament_id}, User: {current_user.id}")

    # 1. Verify student role
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can unenroll from tournaments"
        )

    # 2. Fetch tournament
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tournament not found"
        )

    # 3. Verify tournament status - can only unenroll from active tournaments
    if tournament.tournament_status not in ["ENROLLMENT_OPEN", "IN_PROGRESS"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot unenroll from tournament in {tournament.tournament_status} status. Only ENROLLMENT_OPEN and IN_PROGRESS tournaments allow unenrollment."
        )

    # 4. Find active enrollment — RACE-04 fix: SELECT FOR UPDATE
    # Acquiring a row-level lock here serializes concurrent unenroll requests.
    # If Thread B arrives while Thread A holds the lock, Thread B blocks until
    # Thread A commits (setting is_active=False). After Thread A's commit,
    # Thread B's query sees is_active=False → returns None → HTTP 404.
    # This prevents the double-refund window where both threads read is_active=True.
    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == current_user.id,
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active == True,
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED
    ).with_for_update().first()

    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active enrollment found for this tournament"
        )

    # 5. Calculate refund (50% penalty)
    enrollment_cost = tournament.enrollment_cost if tournament.enrollment_cost is not None else 500
    refund_amount = enrollment_cost // 2  # 50% refund
    penalty_amount = enrollment_cost - refund_amount  # 50% penalty

    # 6. Update enrollment status to WITHDRAWN
    enrollment.is_active = False
    enrollment.request_status = EnrollmentStatus.WITHDRAWN
    enrollment.updated_at = datetime.utcnow()
    db.add(enrollment)

    # 7. RACE-04 fix: Atomic refund credit — SQL UPDATE instead of Python-level addition.
    # Defense-in-depth: even if the FOR UPDATE lock above is somehow bypassed,
    # the atomic UPDATE ensures each refund is a separate, serialized DB write.
    db.execute(
        sql_update(User)
        .where(User.id == current_user.id)
        .values(credit_balance=User.credit_balance + refund_amount)
        .execution_options(synchronize_session=False)
    )
    db.refresh(current_user)  # Sync ORM state before reading balance_after

    # 8. Create credit transaction record for refund
    # Generate idempotency key for transaction deduplication
    idempotency_key = f"unenroll_{current_user.id}_{tournament_id}_{enrollment.id}_{datetime.utcnow().timestamp()}"

    refund_transaction = CreditTransaction(
        user_license_id=enrollment.user_license_id,
        transaction_type="TOURNAMENT_UNENROLL_REFUND",
        amount=refund_amount,  # Positive amount for refund
        balance_after=current_user.credit_balance,
        description=f"Tournament unenrollment refund (50%): {tournament.name} ({tournament.code})",
        semester_id=tournament_id,
        enrollment_id=enrollment.id,
        idempotency_key=idempotency_key
    )
    db.add(refund_transaction)

    # 9. Remove all bookings linked to this enrollment
    bookings = db.query(Booking).filter(
        Booking.enrollment_id == enrollment.id,
        Booking.user_id == current_user.id
    ).all()

    bookings_removed = len(bookings)
    for booking in bookings:
        db.delete(booking)
        logger.info(f"🗑️ Removed booking {booking.id} for session {booking.session_id}")

    # 10. Commit transaction
    try:
        db.commit()
        db.refresh(enrollment)
        db.refresh(current_user)

        logger.info(f"✅ UNENROLL SUCCESS:")
        logger.info(f"   - Enrollment ID: {enrollment.id} → WITHDRAWN")
        logger.info(f"   - Refund: {refund_amount} credits (50%)")
        logger.info(f"   - Penalty: {penalty_amount} credits (50%)")
        logger.info(f"   - Final balance: {current_user.credit_balance}")
        logger.info(f"   - Bookings removed: {bookings_removed}")

        # Audit refund event for observability
        audit_service = AuditService(db)
        audit_service.log(
            action=AuditAction.USER_UPDATED,  # Reuse existing action
            user_id=current_user.id,
            details={
                "event": "enrollment_refunded",
                "original_enrollment_id": enrollment.id,
                "tournament_id": tournament_id,
                "tournament_name": tournament.name,
                "refunded_amount": refund_amount,
                "penalty_amount": penalty_amount,
                "balance_before": current_user.credit_balance - refund_amount,
                "balance_after": current_user.credit_balance,
                "bookings_removed": bookings_removed,
                "refund_policy": "50_percent"
            }
        )

    except Exception as e:
        logger.error(f"❌ UNENROLL FAILED: {str(e)}")
        logger.error(traceback.format_exc())
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to unenroll from tournament: {str(e)}"
        )

    # 11. Return response
    return {
        "success": True,
        "message": "Successfully unenrolled from tournament",
        "enrollment_id": enrollment.id,
        "tournament_name": tournament.name,
        "refund_amount": refund_amount,
        "penalty_amount": penalty_amount,
        "credits_remaining": current_user.credit_balance,
        "bookings_removed": bookings_removed
    }
