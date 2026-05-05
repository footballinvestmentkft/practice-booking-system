"""
Tournament Admin Update Endpoint
Extracted from lifecycle.py as part of file-size refactoring (lifecycle.py was 1133 lines).
Boundary: lifecycle.py lines 818–1133.

Single endpoint:
  PATCH /{tournament_id} — Admin updates 15+ tournament fields with auto-session triggers.

WARNING: tournament_status field in TournamentUpdateRequest bypasses the state machine
(admin override). Use PATCH /{id}/status for validated status transitions.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.database import get_db
from app.dependencies import get_current_admin_user_hybrid
from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterCategory
from app.models.specialization import SpecializationType
from app.api.api_v1.endpoints.tournaments.lifecycle import record_status_change

router = APIRouter()


# ============================================================================
# SCHEMA
# ============================================================================

class TournamentUpdateRequest(BaseModel):
    """Request to update tournament fields (Admin only)"""
    name: Optional[str] = Field(None, description="Tournament name")
    enrollment_cost: Optional[int] = Field(None, ge=0, description="Enrollment cost in credits")
    max_players: Optional[int] = Field(None, gt=0, description="Maximum players")
    age_group: Optional[str] = Field(None, description="Age group")
    description: Optional[str] = Field(None, description="Tournament description")
    start_date: Optional[str] = Field(None, description="Tournament start date (ISO format)")
    end_date: Optional[str] = Field(None, description="Tournament end date (ISO format)")
    specialization_type: Optional[str] = Field(None, description="Specialization type")
    assignment_type: Optional[str] = Field(None, description="Assignment type (OPEN_ASSIGNMENT or APPLICATION_BASED)")
    participant_type: Optional[str] = Field(None, description="Participant type (INDIVIDUAL, TEAM, MIXED)")
    tournament_type_id: Optional[int] = Field(None, description="Tournament type ID (⚠️ WARNING: Can only change if no sessions generated)")
    tournament_status: Optional[str] = Field(None, description="Tournament status (⚠️ ADMIN OVERRIDE: Bypasses state machine validation)")
    format: Optional[str] = Field(None, description="Tournament format (HEAD_TO_HEAD or INDIVIDUAL_RANKING)")
    scoring_type: Optional[str] = Field(None, description="Scoring type (TIME_BASED, DISTANCE_BASED, SCORE_BASED, PLACEMENT)")
    measurement_unit: Optional[str] = Field(None, description="Measurement unit (seconds, meters, points, etc.)")
    ranking_direction: Optional[str] = Field(None, description="Ranking direction (ASC = lowest wins, DESC = highest wins)")
    number_of_rounds: Optional[int] = Field(None, ge=1, le=10, description="Number of rounds for INDIVIDUAL_RANKING tournaments (⚠️ WARNING: Triggers session regeneration if changed)")
    campus_id: Optional[int] = Field(None, description="Campus ID where tournament will be held")
    location_id: Optional[int] = Field(None, description="Location ID where tournament will be held")
    game_preset_id: Optional[int] = Field(None, description="Game preset ID (updates GameConfiguration)")
    theme: Optional[str] = Field(None, description="Marketing theme / headline (e.g. 'Spring 2026 Edition')")
    winner_count: Optional[int] = Field(None, ge=1, description="Number of winners (INDIVIDUAL_RANKING)")
    team_enrollment_cost: Optional[int] = Field(None, ge=0, description="Credit cost per team enrollment (TEAM tournaments)")


# ============================================================================
# ENDPOINT
# ============================================================================

@router.patch("/{tournament_id}", status_code=status.HTTP_200_OK)
def update_tournament(
    tournament_id: int,
    request: TournamentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_hybrid)
):
    """
    Update tournament fields (Admin only)

    **Authorization:** Admin only

    **Allowed updates:**
    - name: Tournament name
    - enrollment_cost: Credits required to enroll
    - max_players: Maximum participant capacity (cannot be reduced below current enrollment count)
    - age_group: Age group classification
    - description: Tournament description
    - start_date: Tournament start date (ISO format)
    - end_date: Tournament end date (ISO format)
    - specialization_type: Specialization type
    - assignment_type: OPEN_ASSIGNMENT or APPLICATION_BASED
    - participant_type: INDIVIDUAL, TEAM, or MIXED
    - tournament_type_id: Tournament type (⚠️ Auto-deletes sessions if changed)
    - tournament_status: Tournament status (⚠️ ADMIN OVERRIDE: Bypasses state machine validation)

    **Important Notes:**
    - max_players cannot be reduced below current enrollment count
    - tournament_type_id: Automatically deletes existing sessions if changed
    - tournament_status: Admin can set ANY status (state machine validation bypassed)
    """
    # Admin-only check
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can update tournaments"
        )

    # Fetch tournament
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tournament {tournament_id} not found"
        )

    # Track what was updated
    updates = {}

    # Update name
    if request.name is not None:
        updates["name"] = {"old": tournament.name, "new": request.name}
        tournament.name = request.name

    # Update enrollment_cost
    if request.enrollment_cost is not None:
        updates["enrollment_cost"] = {"old": tournament.enrollment_cost, "new": request.enrollment_cost}
        tournament.enrollment_cost = request.enrollment_cost

    # Update max_players (lives in TournamentConfiguration)
    if request.max_players is not None:
        # Check if tournament has enrollments
        enrollments_count = db.query(Semester).filter(
            Semester.id == tournament_id
        ).join(Semester.enrollments).count() if hasattr(tournament, 'enrollments') else 0

        if enrollments_count > request.max_players:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reduce max_players to {request.max_players}: Tournament already has {enrollments_count} enrollments"
            )

        updates["max_players"] = {"old": tournament.max_players, "new": request.max_players}
        if tournament.tournament_config_obj:
            tournament.tournament_config_obj.max_players = request.max_players

    # Update age_group
    if request.age_group is not None:
        if tournament.semester_category == SemesterCategory.PROMOTION_EVENT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Age group cannot be changed for promotion events.",
            )
        updates["age_group"] = {"old": tournament.age_group, "new": request.age_group}
        tournament.age_group = request.age_group

    # Update description
    if request.description is not None:
        updates["focus_description"] = {"old": tournament.focus_description, "new": request.description}
        tournament.focus_description = request.description

    # ✅ NEW: Update start_date
    if request.start_date is not None:
        from datetime import datetime
        try:
            new_start_date = datetime.fromisoformat(request.start_date).date()
            updates["start_date"] = {"old": str(tournament.start_date), "new": str(new_start_date)}
            tournament.start_date = new_start_date
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid start_date format: {request.start_date}. Expected ISO format (YYYY-MM-DD)"
            )

    # ✅ NEW: Update end_date
    if request.end_date is not None:
        from datetime import datetime
        try:
            new_end_date = datetime.fromisoformat(request.end_date).date()
            updates["end_date"] = {"old": str(tournament.end_date), "new": str(new_end_date)}
            tournament.end_date = new_end_date
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid end_date format: {request.end_date}. Expected ISO format (YYYY-MM-DD)"
            )

    # ✅ NEW: Update specialization_type
    if request.specialization_type is not None:
        updates["specialization_type"] = {"old": tournament.specialization_type, "new": request.specialization_type}
        tournament.specialization_type = request.specialization_type

    # Update assignment_type (lives in TournamentConfiguration)
    if request.assignment_type is not None:
        valid_types = ["OPEN_ASSIGNMENT", "APPLICATION_BASED"]
        if request.assignment_type not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid assignment_type: {request.assignment_type}. Must be one of {valid_types}"
            )
        updates["assignment_type"] = {"old": tournament.assignment_type, "new": request.assignment_type}
        if tournament.tournament_config_obj:
            tournament.tournament_config_obj.assignment_type = request.assignment_type

    # Update participant_type (lives in TournamentConfiguration)
    if request.participant_type is not None:
        valid_types = ["INDIVIDUAL", "TEAM", "MIXED"]
        if request.participant_type not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid participant_type: {request.participant_type}. Must be one of {valid_types}"
            )
        updates["participant_type"] = {"old": tournament.participant_type, "new": request.participant_type}
        if tournament.tournament_config_obj:
            tournament.tournament_config_obj.participant_type = request.participant_type

    # ✅ NEW: Update campus_id (required for ENROLLMENT_OPEN status)
    if request.campus_id is not None:
        # Validate campus exists
        from app.models.campus import Campus
        campus = db.query(Campus).filter(Campus.id == request.campus_id).first()
        if not campus:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Campus {request.campus_id} not found"
            )
        updates["campus_id"] = {"old": tournament.campus_id, "new": request.campus_id}
        tournament.campus_id = request.campus_id
        # Auto-derive location from campus if tournament has no location set
        if campus.location_id and not tournament.location_id:
            tournament.location_id = campus.location_id
            updates["location_id_derived"] = {"source": "campus", "value": campus.location_id}

    # Update tournament_type_id (lives in TournamentConfiguration — ⚠️ auto-deletes sessions on change)
    # Use model_fields_set to detect explicit null (IR switch) vs omitted field
    if 'tournament_type_id' in request.model_fields_set:
        old_type_id = tournament.tournament_type_id
        if request.tournament_type_id is None:
            # Switching to INDIVIDUAL_RANKING: clear tournament_type_id
            if tournament.sessions_generated:
                from app.models.session import Session as SessionModel
                deleted_count = db.query(SessionModel).filter(SessionModel.semester_id == tournament.id).delete()
                if tournament.tournament_config_obj:
                    tournament.tournament_config_obj.sessions_generated = False
                    tournament.tournament_config_obj.sessions_generated_at = None
                updates["sessions_deleted"] = {"count": deleted_count, "reason": "format_switched_to_individual_ranking"}
            updates["tournament_type_id"] = {"old": old_type_id, "new": None, "format_switch": "INDIVIDUAL_RANKING"}
            if tournament.tournament_config_obj:
                tournament.tournament_config_obj.tournament_type_id = None
        else:
            from app.models.tournament_type import TournamentType
            tournament_type = db.query(TournamentType).filter(TournamentType.id == request.tournament_type_id).first()
            if not tournament_type:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Tournament type {request.tournament_type_id} not found"
                )
            # Auto-delete existing sessions if type changes
            if tournament.sessions_generated and old_type_id != request.tournament_type_id:
                from app.models.session import Session as SessionModel
                deleted_count = db.query(SessionModel).filter(SessionModel.semester_id == tournament.id).delete()
                if tournament.tournament_config_obj:
                    tournament.tournament_config_obj.sessions_generated = False
                    tournament.tournament_config_obj.sessions_generated_at = None
                updates["sessions_deleted"] = {"count": deleted_count, "reason": "tournament_type_changed"}
            updates["tournament_type_id"] = {"old": old_type_id, "new": request.tournament_type_id}
            if tournament.tournament_config_obj:
                tournament.tournament_config_obj.tournament_type_id = request.tournament_type_id

    # ⚠️ ADMIN OVERRIDE: Update tournament_status (bypasses state machine validation)
    if request.tournament_status is not None:
        # Validate status value exists in VALID_TRANSITIONS
        from app.services.tournament.status_validator import VALID_TRANSITIONS
        if request.tournament_status not in VALID_TRANSITIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid tournament_status: {request.tournament_status}. Must be one of {list(VALID_TRANSITIONS.keys())}"
            )

        # Store old status before changing
        old_tournament_status = tournament.tournament_status

        # ✅ ADMIN OVERRIDE: Allow ANY status transition (no validation)
        updates["tournament_status"] = {
            "old": old_tournament_status,
            "new": request.tournament_status,
            "admin_override": True
        }
        tournament.tournament_status = request.tournament_status
        db.flush()

        # ⚠️ TRIGGER AUTO-GENERATION if transitioning to IN_PROGRESS
        if request.tournament_status == "IN_PROGRESS" and not tournament.sessions_generated:
            from app.services.tournament_session_generator import TournamentSessionGenerator

            generator = TournamentSessionGenerator(db)
            can_generate, reason = generator.can_generate_sessions(tournament.id)

            if can_generate:
                session_duration = tournament.match_duration_minutes if tournament.match_duration_minutes else 90
                break_duration = tournament.break_duration_minutes if tournament.break_duration_minutes else 15
                parallel_fields = tournament.parallel_fields if tournament.parallel_fields else 1
                number_of_rounds = tournament.number_of_rounds if tournament.number_of_rounds else 1

                success, message, sessions_created = generator.generate_sessions(
                    tournament_id=tournament.id,
                    parallel_fields=parallel_fields,
                    session_duration_minutes=session_duration,
                    break_minutes=break_duration,
                    number_of_rounds=number_of_rounds
                )

                if success:
                    updates["sessions_auto_generated"] = {
                        "count": len(sessions_created),
                        "rounds": number_of_rounds
                    }
                else:
                    updates["session_generation_failed"] = message
            else:
                updates["session_generation_skipped"] = reason

    # format is derived from tournament_type.format — cannot be set directly; use tournament_type_id
    if request.format is not None:
        updates["format_note"] = "format is derived from tournament_type; use tournament_type_id to change it"

    # Update scoring_type (lives in TournamentConfiguration)
    if request.scoring_type is not None:
        updates["scoring_type"] = {"old": tournament.scoring_type, "new": request.scoring_type}
        if tournament.tournament_config_obj:
            tournament.tournament_config_obj.scoring_type = request.scoring_type

    # Update measurement_unit (lives in TournamentConfiguration)
    if request.measurement_unit is not None:
        updates["measurement_unit"] = {"old": tournament.measurement_unit, "new": request.measurement_unit}
        if tournament.tournament_config_obj:
            tournament.tournament_config_obj.measurement_unit = request.measurement_unit

    # Update ranking_direction (lives in TournamentConfiguration)
    if request.ranking_direction is not None:
        updates["ranking_direction"] = {"old": tournament.ranking_direction, "new": request.ranking_direction}
        if tournament.tournament_config_obj:
            tournament.tournament_config_obj.ranking_direction = request.ranking_direction

    # Update number_of_rounds (lives in TournamentConfiguration — ⚠️ auto-regenerates sessions on change)
    if request.number_of_rounds is not None:
        old_rounds = tournament.number_of_rounds

        if tournament.sessions_generated and old_rounds != request.number_of_rounds:
            from app.models.session import Session as SessionModel
            from app.models.attendance import Attendance

            session_ids = [s.id for s in db.query(SessionModel).filter(
                SessionModel.semester_id == tournament.id,
                SessionModel.auto_generated == True
            ).all()]

            if session_ids:
                db.query(Attendance).filter(Attendance.session_id.in_(session_ids)).delete(synchronize_session=False)
                deleted_count = db.query(SessionModel).filter(
                    SessionModel.semester_id == tournament.id,
                    SessionModel.auto_generated == True
                ).delete(synchronize_session=False)

                if tournament.tournament_config_obj:
                    tournament.tournament_config_obj.sessions_generated = False
                    tournament.tournament_config_obj.sessions_generated_at = None

                updates["sessions_deleted"] = {
                    "count": deleted_count,
                    "reason": f"number_of_rounds changed from {old_rounds} to {request.number_of_rounds}",
                    "note": "Sessions will auto-regenerate when tournament starts (status → IN_PROGRESS)"
                }

        updates["number_of_rounds"] = {"old": old_rounds, "new": request.number_of_rounds}
        if tournament.tournament_config_obj:
            tournament.tournament_config_obj.number_of_rounds = request.number_of_rounds

    # Update location_id (direct Semester column)
    if request.location_id is not None:
        from app.models.location import Location
        location = db.query(Location).filter(Location.id == request.location_id).first()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Location {request.location_id} not found"
            )
        updates["location_id"] = {"old": tournament.location_id, "new": request.location_id}
        tournament.location_id = request.location_id

    # Update game_preset_id (lives in GameConfiguration)
    if request.game_preset_id is not None:
        from app.models.game_preset import GamePreset
        from app.models.game_configuration import GameConfiguration
        preset = db.query(GamePreset).filter(
            GamePreset.id == request.game_preset_id,
            GamePreset.is_active == True,
        ).first()
        if not preset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Game preset {request.game_preset_id} not found or inactive",
            )
        game_cfg = db.query(GameConfiguration).filter(
            GameConfiguration.semester_id == tournament_id
        ).first()
        if game_cfg:
            updates["game_preset_id"] = {"old": game_cfg.game_preset_id, "new": request.game_preset_id}
            game_cfg.game_preset_id = request.game_preset_id

    # Update theme (direct Semester column)
    if request.theme is not None:
        updates["theme"] = {"old": tournament.theme, "new": request.theme}
        tournament.theme = request.theme

    # Update winner_count (direct Semester column — for INDIVIDUAL_RANKING)
    if request.winner_count is not None:
        updates["winner_count"] = {"old": tournament.winner_count, "new": request.winner_count}
        tournament.winner_count = request.winner_count

    # Update team_enrollment_cost (lives in TournamentConfiguration)
    if request.team_enrollment_cost is not None:
        updates["team_enrollment_cost"] = {
            "old": getattr(tournament.tournament_config_obj, 'team_enrollment_cost', None),
            "new": request.team_enrollment_cost,
        }
        if tournament.tournament_config_obj:
            tournament.tournament_config_obj.team_enrollment_cost = request.team_enrollment_cost

    # If no updates, return early
    if not updates:
        return {
            "tournament_id": tournament.id,
            "message": "No fields updated",
            "updates": {}
        }

    # Save changes
    db.commit()
    db.refresh(tournament)

    return {
        "tournament_id": tournament.id,
        "tournament_name": tournament.name,
        "message": "Tournament updated successfully",
        "updates": updates
    }
