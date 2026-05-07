"""
Tournament Lifecycle API
Handles tournament creation, status transitions, and status history
"""
from datetime import datetime
from typing import Optional, List
import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, Field

from app.database import get_db
from app.api.api_v1.endpoints.auth import get_current_user
from app.dependencies import get_current_admin_user_hybrid
from app.models.user import User, UserRole
from app.models.semester import Semester
from app.models.specialization import SpecializationType
from app.services.tournament.status_validator import (
    validate_status_transition,
    get_next_allowed_statuses
)

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================

class TournamentCreateRequest(BaseModel):
    """Request to create a new tournament in DRAFT status"""
    name: str = Field(..., description="Tournament name")
    specialization_type: SpecializationType = Field(..., description="Specialization type")
    age_group: Optional[str] = Field(None, description="Age group (e.g., PRE, YOUTH)")
    start_date: str = Field(..., description="Start date (YYYY-MM-DD)")
    end_date: str = Field(..., description="End date (YYYY-MM-DD)")
    location_id: Optional[int] = Field(None, description="Location ID")
    campus_id: Optional[int] = Field(None, description="Campus ID")
    description: Optional[str] = Field(None, description="Tournament description")


class TournamentCreateResponse(BaseModel):
    """Response from tournament creation"""
    tournament_id: int
    name: str
    status: str
    specialization_type: str
    start_date: str
    end_date: str
    created_at: str


class StatusTransitionRequest(BaseModel):
    """Request to change tournament status"""
    new_status: str = Field(..., description="New status to transition to")
    reason: Optional[str] = Field(None, description="Reason for status change")
    metadata: Optional[dict] = Field(None, description="Additional metadata")


class StatusTransitionResponse(BaseModel):
    """Response from status transition"""
    tournament_id: int
    old_status: Optional[str]
    new_status: str
    changed_by: int
    changed_at: str
    reason: Optional[str]
    allowed_next_statuses: List[str]


class StatusHistoryEntry(BaseModel):
    """Single status history entry"""
    id: int
    old_status: Optional[str]
    new_status: str
    changed_by: int
    changed_by_name: str
    changed_at: str
    reason: Optional[str]
    metadata: Optional[dict]


class StatusHistoryResponse(BaseModel):
    """Response with full status history"""
    tournament_id: int
    tournament_name: str
    current_status: Optional[str]
    history: List[StatusHistoryEntry]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def record_status_change(
    db: Session,
    tournament_id: int,
    old_status: Optional[str],
    new_status: str,
    changed_by: int,
    reason: Optional[str] = None,
    metadata: Optional[dict] = None
) -> None:
    """
    Record a status change in tournament_status_history table

    Args:
        db: Database session
        tournament_id: Tournament ID
        old_status: Previous status (None for creation)
        new_status: New status
        changed_by: User ID who made the change
        reason: Optional reason for change
        metadata: Optional metadata dict
    """
    # Convert metadata dict to JSON string if provided
    metadata_json = json.dumps(metadata) if metadata is not None else None

    db.execute(
        text("""
        INSERT INTO tournament_status_history
        (tournament_id, old_status, new_status, changed_by, reason, extra_metadata)
        VALUES (:tournament_id, :old_status, :new_status, :changed_by, :reason, :extra_metadata)
        """),
        {
            "tournament_id": tournament_id,
            "old_status": old_status,
            "new_status": new_status,
            "changed_by": changed_by,
            "reason": reason,
            "extra_metadata": metadata_json
        }
    )
    # Note: No commit here - let the calling code handle transaction management


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("/", response_model=TournamentCreateResponse, status_code=status.HTTP_201_CREATED)
def create_tournament(
    request: TournamentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_hybrid)
):
    """
    Create a new tournament in DRAFT status (Admin only)

    Business rules:
    - Only admins can create tournaments
    - Tournaments are created in DRAFT status
    - Status history is automatically recorded
    """

    # Authorization: Admin only
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can create tournaments"
        )

    # Validate dates
    try:
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(request.end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Use YYYY-MM-DD"
        )

    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after start date"
        )

    # Generate unique tournament code
    tournament_code = f"TOURN-{start_date.strftime('%Y%m%d')}-{datetime.now().strftime('%H%M%S')}"

    # Create tournament (Semester) record with DRAFT status
    # Only include optional FK fields if they have values
    tournament_data = {
        "code": tournament_code,
        "name": request.name,
        "specialization_type": request.specialization_type.value,  # Convert enum to string
        "age_group": request.age_group,
        "start_date": start_date,
        "end_date": end_date,
        "focus_description": request.description,
        "tournament_status": "DRAFT",
        "is_active": True
    }

    # Only add FK fields if they have values (avoid NULL FK violations)
    if request.location_id is not None:
        tournament_data["location_id"] = request.location_id
    if request.campus_id is not None:
        tournament_data["campus_id"] = request.campus_id

    tournament = Semester(**tournament_data)

    db.add(tournament)
    db.flush()  # Get tournament ID before commit

    # Record status history (NULL → DRAFT)
    record_status_change(
        db=db,
        tournament_id=tournament.id,
        old_status=None,
        new_status="DRAFT",
        changed_by=current_user.id,
        reason="Tournament created",
        metadata={"created_by": current_user.email}
    )

    db.commit()
    db.refresh(tournament)

    return TournamentCreateResponse(
        tournament_id=tournament.id,
        name=tournament.name,
        status=tournament.tournament_status,
        specialization_type=tournament.specialization_type,  # Already a string from DB
        start_date=tournament.start_date.isoformat(),
        end_date=tournament.end_date.isoformat(),
        created_at=datetime.now().isoformat()
    )


@router.patch("/{tournament_id}/status", response_model=StatusTransitionResponse)
def transition_tournament_status(
    tournament_id: int,
    request: StatusTransitionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_hybrid)
):
    """
    Transition tournament to a new status with validation

    Business rules:
    - Only admins and instructors can change tournament status
    - Transitions must follow the valid status graph
    - Prerequisites for each status must be met
    - All changes are audited in status history
    """

    # Authorization: Admin or Instructor only
    if current_user.role not in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and instructors can change tournament status"
        )

    # Fetch tournament with enrollments for validation
    from sqlalchemy.orm import joinedload
    tournament = db.query(Semester).options(
        joinedload(Semester.enrollments)
    ).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tournament {tournament_id} not found"
        )

    # Validate status transition
    is_valid, error_message = validate_status_transition(
        current_status=tournament.tournament_status,
        new_status=request.new_status,
        tournament=tournament
    )

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message
        )

    # Record old status for response and history
    old_status = tournament.tournament_status

    # ── Pre-check: instructor prerequisite (before status flush) ──────────────
    # GenerationValidator also checks this, but that check runs AFTER the flush.
    # In SAVEPOINT-isolated tests the flushed status change would remain visible
    # even after an HTTPException, making the status assertion fail.  Checking
    # here (before any mutation) keeps the status unchanged on failure.
    if request.new_status == "CHECK_IN_OPEN":
        from app.services.tournament.instructor_service import has_master_instructor_assignment
        if not has_master_instructor_assignment(db, tournament_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot generate sessions: No instructor assigned. "
                    "Assign a master instructor before generating sessions."
                ),
            )

    # Update tournament status
    tournament.tournament_status = request.new_status
    db.flush()

    # ============================================================================
    # AUTO-DELETE SESSIONS when transitioning from IN_PROGRESS to ENROLLMENT_CLOSED
    # ============================================================================
    if old_status == "IN_PROGRESS" and request.new_status == "ENROLLMENT_CLOSED":
        if tournament.sessions_generated:
            from app.models.session import Session as SessionModel
            from app.models.attendance import Attendance

            # Get session IDs to delete
            session_ids = [s.id for s in db.query(SessionModel).filter(
                SessionModel.semester_id == tournament_id,
                SessionModel.auto_generated == True
            ).all()]

            if session_ids:
                # Delete attendance records first
                db.query(Attendance).filter(Attendance.session_id.in_(session_ids)).delete(synchronize_session=False)

                # Delete sessions
                deleted_count = db.query(SessionModel).filter(
                    SessionModel.semester_id == tournament_id,
                    SessionModel.auto_generated == True
                ).delete(synchronize_session=False)

                # Reset flags (write to config object — Semester properties are read-only)
                if tournament.tournament_config_obj:
                    tournament.tournament_config_obj.sessions_generated = False
                    tournament.tournament_config_obj.sessions_generated_at = None
                    db.flush()

                print(f"🗑️ Auto-deleted {deleted_count} sessions when tournament reverted to ENROLLMENT_CLOSED")

    # ============================================================================
    # AUTO-DELETE SESSIONS when rolling back from CHECK_IN_OPEN to ENROLLMENT_CLOSED
    # ============================================================================
    if old_status == "CHECK_IN_OPEN" and request.new_status == "ENROLLMENT_CLOSED":
        if tournament.sessions_generated:
            from app.models.session import Session as SessionModel
            from app.models.attendance import Attendance

            session_ids = [s.id for s in db.query(SessionModel).filter(
                SessionModel.semester_id == tournament_id,
                SessionModel.auto_generated == True
            ).all()]

            if session_ids:
                db.query(Attendance).filter(Attendance.session_id.in_(session_ids)).delete(synchronize_session=False)

                deleted_count = db.query(SessionModel).filter(
                    SessionModel.semester_id == tournament_id,
                    SessionModel.auto_generated == True
                ).delete(synchronize_session=False)

                if tournament.tournament_config_obj:
                    tournament.tournament_config_obj.sessions_generated = False
                    tournament.tournament_config_obj.sessions_generated_at = None
                    db.flush()

                print(f"🗑️ Auto-deleted {deleted_count} sessions when tournament rolled back CHECK_IN_OPEN → ENROLLMENT_CLOSED")

    # ============================================================================
    # AUTO-GENERATE SESSIONS when transitioning to CHECK_IN_OPEN (initial draw)
    # ============================================================================
    if request.new_status == "CHECK_IN_OPEN":
        if not tournament.sessions_generated:
            # 📸 Save enrollment snapshot BEFORE session generation (initial draw)
            from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus

            enrolled_players = db.query(SemesterEnrollment).filter(
                SemesterEnrollment.semester_id == tournament_id,
                SemesterEnrollment.is_active == True,
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED
            ).all()

            enrollment_snapshot = {
                "timestamp": datetime.now().isoformat(),
                "total_enrolled": len(enrolled_players),
                "player_ids": [e.user_id for e in enrolled_players],
                "player_details": [
                    {
                        "user_id": e.user_id,
                        "enrollment_id": e.id,
                        "payment_verified": e.payment_verified,
                        "enrolled_at": e.created_at.isoformat() if e.created_at else None
                    }
                    for e in enrolled_players
                ],
                "schedule_config": {
                    "match_duration_minutes": tournament.match_duration_minutes,
                    "break_duration_minutes": tournament.break_duration_minutes,
                    "parallel_fields": tournament.parallel_fields,
                    "tournament_type_id": tournament.tournament_type_id
                }
            }

            if tournament.tournament_config_obj:
                tournament.tournament_config_obj.enrollment_snapshot = enrollment_snapshot
                db.flush()

            print(f"📸 ENROLLMENT SNAPSHOT saved at CHECK_IN_OPEN for tournament {tournament_id}: {len(enrolled_players)} players")

            # Generate initial sessions using all enrolled teams/players (no check-in filter yet)
            from app.services.tournament_session_generator import TournamentSessionGenerator

            generator = TournamentSessionGenerator(db)
            can_generate, reason = generator.can_generate_sessions(tournament_id)

            if can_generate:
                session_duration = tournament.match_duration_minutes if tournament.match_duration_minutes else 90
                break_duration = tournament.break_duration_minutes if tournament.break_duration_minutes else 15
                parallel_fields = tournament.parallel_fields if tournament.parallel_fields else 1
                number_of_rounds = tournament.number_of_rounds if tournament.number_of_rounds else 1
                _cfg_obj = tournament.tournament_config_obj
                number_of_legs = (_cfg_obj.number_of_legs if _cfg_obj and _cfg_obj.number_of_legs else 1)
                track_home_away = (_cfg_obj.track_home_away if _cfg_obj and _cfg_obj.track_home_away else False)

                success, message, sessions_created = generator.generate_sessions(
                    tournament_id=tournament_id,
                    parallel_fields=parallel_fields,
                    session_duration_minutes=session_duration,
                    break_minutes=break_duration,
                    number_of_rounds=number_of_rounds,
                    number_of_legs=number_of_legs,
                    track_home_away=track_home_away,
                )

                if success:
                    print(f"✅ Auto-generated {len(sessions_created)} sessions at CHECK_IN_OPEN for tournament {tournament_id}")
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Session generation failed at CHECK_IN_OPEN: {message}",
                    )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot generate sessions for tournament {tournament_id}: {reason}",
                )

    # ============================================================================
    # AUTO-REGENERATE SESSIONS when transitioning to IN_PROGRESS (check-in filter)
    # ============================================================================
    if request.new_status == "IN_PROGRESS":
        # 📸 SAVE REWARD POLICY SNAPSHOT FIRST (lock reward_config for this tournament)
        # This MUST happen BEFORE session generation and ALWAYS when entering IN_PROGRESS
        # This prevents admin from changing reward config after tournament starts
        if tournament.reward_config and not tournament.reward_policy_snapshot:
            if tournament.reward_config_obj:
                tournament.reward_config_obj.reward_policy_snapshot = tournament.reward_config
                db.flush()
            print(f"📸 REWARD POLICY SNAPSHOT saved for tournament {tournament_id}:")
            print(f"   Skills: {len(tournament.reward_config.get('skill_mappings', []))}")
            print(f"   Template: {tournament.reward_config.get('template_name', 'Custom')}")

        # Check if we need to regenerate sessions
        from app.models.session import Session as SessionModel

        current_session_count = db.query(SessionModel).filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.auto_generated == True
        ).count()

        # 🔄 Determine if regeneration is needed at IN_PROGRESS:
        # - INDIVIDUAL_RANKING: always expects exactly 1 session
        # - HEAD_TO_HEAD: sessions were already generated at CHECK_IN_OPEN;
        #   only regenerate if check-in exclusions are active (no-shows to filter out)
        if tournament.format == "INDIVIDUAL_RANKING":
            expected_session_count = 1
            needs_regeneration = (not tournament.sessions_generated) or (current_session_count != expected_session_count)
        else:
            # HEAD_TO_HEAD
            if not tournament.sessions_generated:
                needs_regeneration = True  # Fallback: generate if somehow missing
            elif tournament.participant_type == "TEAM":
                from app.models.team import TournamentTeamEnrollment
                has_checkins = db.query(TournamentTeamEnrollment).filter(
                    TournamentTeamEnrollment.semester_id == tournament_id,
                    TournamentTeamEnrollment.is_active == True,
                    TournamentTeamEnrollment.checked_in_at.isnot(None)
                ).count() > 0
                needs_regeneration = has_checkins
            else:
                from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
                has_checkins = db.query(SemesterEnrollment).filter(
                    SemesterEnrollment.semester_id == tournament_id,
                    SemesterEnrollment.is_active == True,
                    SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
                    SemesterEnrollment.tournament_checked_in_at.isnot(None)
                ).count() > 0
                needs_regeneration = has_checkins

        if needs_regeneration and current_session_count > 0:
            # Reset flags FIRST so can_generate_sessions() sees the correct state
            if tournament.tournament_config_obj:
                tournament.tournament_config_obj.sessions_generated = False
                tournament.tournament_config_obj.sessions_generated_at = None
                db.flush()

            # Delete existing sessions
            from app.models.attendance import Attendance

            session_ids = [s.id for s in db.query(SessionModel).filter(
                SessionModel.semester_id == tournament_id,
                SessionModel.auto_generated == True
            ).all()]

            if session_ids:
                db.query(Attendance).filter(Attendance.session_id.in_(session_ids)).delete(synchronize_session=False)
                deleted_count = db.query(SessionModel).filter(
                    SessionModel.semester_id == tournament_id,
                    SessionModel.auto_generated == True
                ).delete(synchronize_session=False)
                db.flush()
                print(f"🗑️ Deleted {deleted_count} sessions for check-in-filtered regeneration at IN_PROGRESS")

        if needs_regeneration:
            from app.services.tournament_session_generator import TournamentSessionGenerator

            generator = TournamentSessionGenerator(db)
            can_generate, reason = generator.can_generate_sessions(tournament_id)

            if can_generate:
                session_duration = tournament.match_duration_minutes if tournament.match_duration_minutes else 90
                break_duration = tournament.break_duration_minutes if tournament.break_duration_minutes else 15
                parallel_fields = tournament.parallel_fields if tournament.parallel_fields else 1
                number_of_rounds = tournament.number_of_rounds if tournament.number_of_rounds else 1
                _cfg_obj = tournament.tournament_config_obj
                number_of_legs = (_cfg_obj.number_of_legs if _cfg_obj and _cfg_obj.number_of_legs else 1)
                track_home_away = (_cfg_obj.track_home_away if _cfg_obj and _cfg_obj.track_home_away else False)

                success, message, sessions_created = generator.generate_sessions(
                    tournament_id=tournament_id,
                    parallel_fields=parallel_fields,
                    session_duration_minutes=session_duration,
                    break_minutes=break_duration,
                    number_of_rounds=number_of_rounds,
                    number_of_legs=number_of_legs,
                    track_home_away=track_home_away,
                )

                if success:
                    print(f"✅ Auto-regenerated {len(sessions_created)} sessions at IN_PROGRESS for tournament {tournament_id}")
                else:
                    print(f"⚠️ Failed to regenerate sessions at IN_PROGRESS: {message}")
            else:
                print(f"⚠️ Cannot regenerate sessions at IN_PROGRESS: {reason}")

    # Record status history
    record_status_change(
        db=db,
        tournament_id=tournament.id,
        old_status=old_status,
        new_status=request.new_status,
        changed_by=current_user.id,
        reason=request.reason,
        metadata=request.metadata
    )

    db.commit()
    db.refresh(tournament)

    # Get allowed next statuses for response
    allowed_next = get_next_allowed_statuses(tournament.tournament_status)

    return StatusTransitionResponse(
        tournament_id=tournament.id,
        old_status=old_status,
        new_status=tournament.tournament_status,
        changed_by=current_user.id,
        changed_at=datetime.now().isoformat(),
        reason=request.reason,
        allowed_next_statuses=allowed_next
    )


@router.get("/{tournament_id}/status-history", response_model=StatusHistoryResponse)
def get_tournament_status_history(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get full status history for a tournament (audit trail)

    Returns all status transitions with user info, timestamps, and reasons
    """

    # Fetch tournament
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tournament {tournament_id} not found"
        )

    # Fetch status history with user info
    history_rows = db.execute(
        text("""
        SELECT
            tsh.id,
            tsh.old_status,
            tsh.new_status,
            tsh.changed_by,
            u.name as changed_by_name,
            tsh.created_at,
            tsh.reason,
            tsh.extra_metadata
        FROM tournament_status_history tsh
        JOIN users u ON tsh.changed_by = u.id
        WHERE tsh.tournament_id = :tournament_id
        ORDER BY tsh.created_at DESC
        """),
        {"tournament_id": tournament_id}
    ).fetchall()

    history = [
        StatusHistoryEntry(
            id=row.id,
            old_status=row.old_status,
            new_status=row.new_status,
            changed_by=row.changed_by,
            changed_by_name=row.changed_by_name,
            changed_at=row.created_at.isoformat(),
            reason=row.reason,
            metadata=row.extra_metadata
        )
        for row in history_rows
    ]

    return StatusHistoryResponse(
        tournament_id=tournament.id,
        tournament_name=tournament.name,
        current_status=tournament.tournament_status,
        history=history
    )
