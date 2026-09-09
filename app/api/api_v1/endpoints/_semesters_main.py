from typing import Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_

from ....database import get_db
from ....dependencies import get_current_user, get_current_admin_user
from ....models.user import User, UserRole
from ....models.semester import Semester, SemesterStatus
from ....models.group import Group
from ....models.session import Session as SessionTypel
from ....models.booking import Booking
from ....schemas.semester import (
    Semester as SemesterSchema, SemesterCreate, SemesterUpdate,
    SemesterWithStats, SemesterList
)
from ....services.location_validation_service import LocationValidationService
from ....models.specialization import SpecializationType

router = APIRouter()


@router.post("/", response_model=SemesterSchema)
def create_semester(
    semester_data: SemesterCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Create new semester (Admin only)

    Validates:
    - Semester code uniqueness
    - Location type vs semester type compatibility (PARTNER vs CENTER)
    """
    # Check if semester code already exists
    existing_semester = db.query(Semester).filter(Semester.code == semester_data.code).first()
    if existing_semester:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Semester with this code already exists"
        )

    # K1: ACADEMY types require an explicit location_id so the CENTER/PARTNER
    # rule can be evaluated.  Without a location we cannot enforce the constraint.
    _ACADEMY_TYPES = {
        SpecializationType.LFA_PLAYER_PRE_ACADEMY,
        SpecializationType.LFA_PLAYER_YOUTH_ACADEMY,
        SpecializationType.LFA_PLAYER_AMATEUR_ACADEMY,
        SpecializationType.LFA_PLAYER_PRO_ACADEMY,
    }
    if semester_data.specialization_type and not semester_data.location_id:
        try:
            _spec = SpecializationType(semester_data.specialization_type)
        except ValueError:
            _spec = None
        if _spec in _ACADEMY_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "Hiányzó helyszín",
                    "message": (
                        "Academy Season típushoz kötelező a location_id megadása "
                        "a CENTER / PARTNER helyszín-típus ellenőrzéséhez."
                    ),
                    "specialization_type": semester_data.specialization_type,
                }
            )

    # Validate location type vs semester type when location_id is provided
    location_id = semester_data.location_id
    if location_id and semester_data.specialization_type:
        try:
            spec_enum = SpecializationType(semester_data.specialization_type)
        except ValueError:
            spec_enum = None
        if spec_enum:
            validation = LocationValidationService.can_create_semester_at_location(
                location_id=location_id,
                specialization_type=spec_enum,
                db=db
            )
            if not validation["allowed"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": "Helyszín típus korlátozás",
                        "message": validation["reason"],
                        "location_type": validation["location_type"],
                        "semester_type": str(semester_data.specialization_type)
                    }
                )

    # Separate fields for Semester table vs TournamentConfiguration table (P2 refactoring)
    semester_dict = semester_data.model_dump()

    # Extract tournament configuration fields (stored in separate TournamentConfiguration table)
    tournament_config_fields = {
        'assignment_type': semester_dict.pop('assignment_type', None),
        'max_players': semester_dict.pop('max_players', None),
        'tournament_type_id': semester_dict.pop('tournament_type_id', None),
        'scoring_type': semester_dict.pop('scoring_type', 'PLACEMENT'),  # Default
        'measurement_unit': semester_dict.pop('measurement_unit', None),
        'ranking_direction': semester_dict.pop('ranking_direction', None),
    }

    # Remove deprecated location fields
    semester_dict.pop('location_city', None)
    semester_dict.pop('location_venue', None)
    semester_dict.pop('location_address', None)
    semester_dict.pop('format', None)  # Derived from tournament_type, not stored

    # Create Semester
    semester = Semester(**semester_dict)
    db.add(semester)
    db.flush()  # Get semester.id before creating related objects

    # Create TournamentConfiguration if tournament-related fields are provided
    if any(tournament_config_fields.values()):
        from app.models.tournament_configuration import TournamentConfiguration

        tournament_config = TournamentConfiguration(
            semester_id=semester.id,
            **{k: v for k, v in tournament_config_fields.items() if v is not None}
        )
        db.add(tournament_config)

    db.commit()
    db.refresh(semester)

    return semester


@router.get("/", response_model=SemesterList)
def list_semesters(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    List semesters based on user role:
    - ADMIN: See ALL semesters (any status)
    - STUDENT: See only READY_FOR_ENROLLMENT and ONGOING semesters
    - INSTRUCTOR: See assigned semesters + SEEKING_INSTRUCTOR
    """
    current_date = datetime.now().date()

    # Admin sees everything (including expired semesters)
    if current_user.role == UserRole.ADMIN:
        semesters = db.query(Semester).options(
            joinedload(Semester.location),  # Eager load location for type info
            joinedload(Semester.master_instructor)  # Eager load instructor details
        ).order_by(Semester.start_date.desc()).all()

    # Student sees only READY or ONGOING semesters
    elif current_user.role == UserRole.STUDENT:
        semesters = db.query(Semester).options(
            joinedload(Semester.location),  # Eager load location for type info
            joinedload(Semester.master_instructor)  # Eager load instructor details
        ).filter(
            and_(
                Semester.status.in_([SemesterStatus.READY_FOR_ENROLLMENT, SemesterStatus.ONGOING]),
                Semester.end_date >= current_date
            )
        ).order_by(Semester.start_date.desc()).all()

    # Instructor sees semesters they're assigned to + SEEKING_INSTRUCTOR
    else:  # instructor
        semesters = db.query(Semester).options(
            joinedload(Semester.location),  # Eager load location for type info
            joinedload(Semester.master_instructor)  # Eager load instructor details
        ).filter(
            and_(
                Semester.end_date >= current_date,
                # Either assigned to this instructor OR seeking instructor
                (
                    (Semester.master_instructor_id == current_user.id) |
                    (Semester.status == SemesterStatus.SEEKING_INSTRUCTOR)
                )
            )
        ).order_by(Semester.start_date.desc()).all()
    
    semester_stats = []
    for semester in semesters:
        # Calculate statistics
        total_groups = db.query(func.count(Group.id)).filter(Group.semester_id == semester.id).scalar() or 0
        total_sessions = db.query(func.count(SessionTypel.id)).filter(SessionTypel.semester_id == semester.id).scalar() or 0
        total_bookings = db.query(func.count(Booking.id)).join(SessionTypel).filter(SessionTypel.semester_id == semester.id).scalar() or 0
        
        # Count active users (users with bookings in this semester)
        active_users = db.query(func.count(func.distinct(Booking.user_id))).join(SessionTypel).filter(SessionTypel.semester_id == semester.id).scalar() or 0
        
        semester_stats.append(SemesterWithStats(
            **semester.__dict__,
            total_groups=total_groups,
            total_sessions=total_sessions,
            total_bookings=total_bookings,
            active_users=active_users,
            location_type=semester.location.location_type.value if semester.location else None,
            master_instructor_name=semester.master_instructor.name if semester.master_instructor else None,
            master_instructor_email=semester.master_instructor.email if semester.master_instructor else None
            # Note: sessions_generated and sessions_generated_at are already in semester.__dict__
        ))
    
    return SemesterList(
        semesters=semester_stats,
        total=len(semester_stats)
    )


@router.get("/active", response_model=SemesterSchema)
def get_active_semester(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Get currently active semester (READY_FOR_ENROLLMENT or ONGOING)"""
    current_date = datetime.now().date()

    active_semester = db.query(Semester).filter(
        and_(
            Semester.start_date <= current_date,
            Semester.end_date >= current_date,
            Semester.status.in_([SemesterStatus.READY_FOR_ENROLLMENT, SemesterStatus.ONGOING])
        )
    ).first()
    
    if not active_semester:
        raise HTTPException(
            status_code=404,
            detail="No active semester found"
        )
    
    return active_semester


@router.get("/{semester_id}", response_model=SemesterWithStats)
def get_semester(
    semester_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get semester by ID with statistics
    """
    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Semester not found"
        )
    
    # Calculate statistics
    total_groups = db.query(func.count(Group.id)).filter(Group.semester_id == semester.id).scalar() or 0
    total_sessions = db.query(func.count(SessionTypel.id)).filter(SessionTypel.semester_id == semester.id).scalar() or 0
    total_bookings = db.query(func.count(Booking.id)).join(SessionTypel).filter(SessionTypel.semester_id == semester.id).scalar() or 0
    active_users = db.query(func.count(func.distinct(Booking.user_id))).join(SessionTypel).filter(SessionTypel.semester_id == semester.id).scalar() or 0
    
    return SemesterWithStats(
        **semester.__dict__,
        total_groups=total_groups,
        total_sessions=total_sessions,
        total_bookings=total_bookings,
        active_users=active_users
    )


@router.patch("/{semester_id}", response_model=SemesterSchema)
def update_semester(
    semester_id: int,
    semester_update: SemesterUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Update semester (Admin only)
    """
    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Semester not found"
        )
    
    # Check code uniqueness if code is being updated
    if semester_update.code and semester_update.code != semester.code:
        existing_semester = db.query(Semester).filter(Semester.code == semester_update.code).first()
        if existing_semester:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Semester with this code already exists"
            )
    
    # Update fields
    update_data = semester_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(semester, field, value)
    
    db.commit()
    db.refresh(semester)
    
    return semester


@router.delete("/{semester_id}")
def delete_semester(
    semester_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Delete semester/tournament (Admin only)

    **CRITICAL**: Admin can ALWAYS delete semesters/tournaments.
    This endpoint handles cascading deletes for ALL foreign key dependencies:
    - Notifications (related_semester_id)
    - Tournament status history
    - Instructor assignment requests
    - Semester enrollments (CASCADE in DB)
    - Sessions
    - Tournament rankings (CASCADE in DB)
    - Groups
    - Projects
    - Tracks
    - Credit transactions (SET NULL in DB)
    - Teams (CASCADE in DB)

    **Authorization:** Admin only

    **Use Case:** Clean deletion of semesters/tournaments regardless of dependencies
    """
    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Semester not found"
        )

    # ============================================================================
    # STEP 1: REFUND CREDITS TO ALL ENROLLED USERS (100% refund)
    # ============================================================================
    from ....models.semester_enrollment import SemesterEnrollment
    from ....models.credit_transaction import CreditTransaction

    enrollments = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == semester_id,
        SemesterEnrollment.is_active == True
    ).all()

    enrollment_cost = semester.enrollment_cost if semester.enrollment_cost is not None else 500
    refunded_users_count = 0

    for enrollment in enrollments:
        user_obj = db.query(User).filter(User.id == enrollment.user_id).first()
        if user_obj:
            # Refund 100% of enrollment cost
            user_obj.credit_balance = user_obj.credit_balance + enrollment_cost
            db.add(user_obj)

            # Create refund transaction record
            refund_transaction = CreditTransaction(
                user_id=user_obj.id,
                context_user_license_id=enrollment.user_license_id,
                transaction_type="TOURNAMENT_DELETED_REFUND",
                amount=enrollment_cost,  # Positive amount (refund)
                balance_after=user_obj.credit_balance,
                description=f"Tournament deleted by admin - Full refund: {semester.name} ({semester.code})",
                semester_id=None,  # Will be deleted
                enrollment_id=None,  # Will be deleted
                idempotency_key=f"tournament-delete-refund-{semester.id}-{enrollment.id}",
            )
            db.add(refund_transaction)
            refunded_users_count += 1

    # Flush refunds before deleting data
    if refunded_users_count > 0:
        db.flush()

    # ============================================================================
    # STEP 2: CASCADE DELETE ALL DEPENDENCIES (in correct order)
    # ============================================================================

    # Import all models that reference semesters
    from ....models.notification import Notification
    from ....models.tournament_status_history import TournamentStatusHistory
    from ....models.instructor_assignment import InstructorAssignmentRequest
    from ....models.session import Session as SessionModel
    from ....models.project import Project
    from ....models.track import Track

    deleted_counts = {}

    # 1. Delete notifications (related_semester_id)
    count = db.query(Notification).filter(
        Notification.related_semester_id == semester_id
    ).delete(synchronize_session=False)
    deleted_counts['notifications'] = count

    # 2. Delete tournament status history
    count = db.query(TournamentStatusHistory).filter(
        TournamentStatusHistory.tournament_id == semester_id
    ).delete(synchronize_session=False)
    deleted_counts['tournament_status_history'] = count

    # 3. Delete instructor assignment requests
    count = db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.semester_id == semester_id
    ).delete(synchronize_session=False)
    deleted_counts['instructor_assignment_requests'] = count

    # 4. Delete sessions (includes bookings via CASCADE in DB)
    count = db.query(SessionModel).filter(
        SessionModel.semester_id == semester_id
    ).delete(synchronize_session=False)
    deleted_counts['sessions'] = count

    # 5. Delete projects
    count = db.query(Project).filter(
        Project.semester_id == semester_id
    ).delete(synchronize_session=False)
    deleted_counts['projects'] = count

    # 6. Delete groups
    count = db.query(Group).filter(
        Group.semester_id == semester_id
    ).delete(synchronize_session=False)
    deleted_counts['groups'] = count

    # Note: semester_enrollments, tournament_rankings, teams have CASCADE in DB schema
    # Note: credit_transactions have SET NULL in DB schema

    # 8. Finally delete the semester itself
    db.delete(semester)
    db.commit()

    return {
        "message": f"Semester '{semester.name}' deleted successfully with all dependencies",
        "semester_id": semester_id,
        "deleted_dependencies": deleted_counts,
        "refunded_users": refunded_users_count,
        "refund_amount_per_user": enrollment_cost
    }
