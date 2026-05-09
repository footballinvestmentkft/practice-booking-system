"""
Tournament Session Generation Validator

Validates whether a tournament is ready for session generation.
"""
from typing import Tuple
from sqlalchemy.orm import Session

from app.models.semester import Semester
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.repositories.tournament_repository import TournamentRepository


class GenerationValidator:
    """
    Validates tournament readiness for session generation
    """

    def __init__(self, db: Session):
        self.db = db
        self.tournament_repo = TournamentRepository(db)

    def can_generate_sessions(self, tournament_id: int) -> Tuple[bool, str]:
        """
        Check if tournament is ready for session generation

        Returns:
            (can_generate, reason)
        """
        tournament = self.tournament_repo.get_optional(tournament_id)
        if not tournament:
            return False, "Tournament not found"

        # Check if already generated
        if tournament.sessions_generated:
            return False, f"Sessions already generated at {tournament.sessions_generated_at}"

        # ✅ Check format-specific requirements
        if tournament.format == "HEAD_TO_HEAD":
            # HEAD_TO_HEAD requires tournament type
            if not tournament.tournament_type_id:
                return False, "HEAD_TO_HEAD tournaments require a tournament type (Swiss, League, Knockout, etc.)"
        elif tournament.format == "INDIVIDUAL_RANKING":
            # INDIVIDUAL_RANKING should NOT have tournament type
            if tournament.tournament_type_id is not None:
                return False, "INDIVIDUAL_RANKING tournaments cannot have a tournament type"
        else:
            return False, f"Invalid tournament format: {tournament.format}"

        # Check if enrollment is closed (tournament status must be CHECK_IN_OPEN or later)
        if tournament.tournament_status not in ["CHECK_IN_OPEN", "IN_PROGRESS", "COMPLETED"]:
            return False, f"Tournament not ready for session generation. Current status: {tournament.tournament_status}. Sessions can only be generated when status is CHECK_IN_OPEN or later."

        # Check if there are enough enrolled participants (TEAM vs INDIVIDUAL differ)
        if tournament.participant_type == "TEAM":
            from app.models.team import TournamentTeamEnrollment
            active_enrollment_count = self.db.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == tournament_id,
                TournamentTeamEnrollment.is_active == True,
            ).count()

            if tournament.format == "INDIVIDUAL_RANKING":
                min_participants = 2
            else:
                from app.models.tournament_type import TournamentType
                tournament_type = self.db.query(TournamentType).filter(
                    TournamentType.id == tournament.tournament_type_id
                ).first()
                min_participants = tournament_type.min_players if tournament_type else 2

            if active_enrollment_count < min_participants:
                return False, f"Not enough teams enrolled. Need at least {min_participants}, have {active_enrollment_count}"
        else:
            # INDIVIDUAL: count SemesterEnrollment (approved, active)
            active_enrollment_count = self.db.query(SemesterEnrollment).filter(
                SemesterEnrollment.semester_id == tournament_id,
                SemesterEnrollment.is_active == True,
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED
            ).count()

            if tournament.format == "INDIVIDUAL_RANKING":
                min_players = 2  # INDIVIDUAL_RANKING needs at least 2 players
            else:
                from app.models.tournament_type import TournamentType
                tournament_type = self.db.query(TournamentType).filter(
                    TournamentType.id == tournament.tournament_type_id
                ).first()
                min_players = tournament_type.min_players if tournament_type else 4

            if active_enrollment_count < min_players:
                return False, f"Not enough players enrolled. Need at least {min_players}, have {active_enrollment_count}"

        # Check location/campus is set — session_generator.get_campus_schedule() requires it
        if not tournament.location_id and not tournament.campus_id:
            return False, "Tournament must have a Location or Campus set before sessions can be generated."

        # Check enough active pitches exist on the campus (>= parallel_fields)
        cfg = tournament.tournament_config_obj
        parallel_fields = (cfg.parallel_fields if cfg and cfg.parallel_fields else 1)

        if tournament.campus_id:
            from app.models.pitch import Pitch
            active_pitch_count = self.db.query(Pitch).filter(
                Pitch.campus_id == tournament.campus_id,
                Pitch.is_active == True,  # noqa: E712
            ).count()
            if active_pitch_count < parallel_fields:
                return False, (
                    f"Campus {tournament.campus_id} has {active_pitch_count} active pitch(es) "
                    f"but parallel_fields={parallel_fields}. "
                    "Add more active pitches or lower parallel_fields before generating sessions."
                )

        # Check FIELD instructor slots: need >= parallel_fields CHECKED_IN slots,
        # each with a valid active pitch.
        from app.models.tournament_instructor_slot import TournamentInstructorSlot
        from app.models.pitch import Pitch as _Pitch

        field_slots = self.db.query(TournamentInstructorSlot).filter(
            TournamentInstructorSlot.semester_id == tournament_id,
            TournamentInstructorSlot.role == "FIELD",
        ).all()

        checked_in_field = [s for s in field_slots if s.status == "CHECKED_IN"]
        if len(checked_in_field) < parallel_fields:
            return False, (
                f"Tournament requires {parallel_fields} FIELD instructor slot(s) "
                f"with status CHECKED_IN, but only {len(checked_in_field)} found. "
                "Assign and check in a FIELD instructor for each pitch before generating sessions."
            )

        invalid_pitch_slots = []
        for slot in checked_in_field:
            if not slot.pitch_id:
                invalid_pitch_slots.append(f"slot {slot.id} has no pitch assigned")
                continue
            pitch = self.db.query(_Pitch).filter(
                _Pitch.id == slot.pitch_id,
                _Pitch.is_active == True,  # noqa: E712
            ).first()
            if not pitch:
                invalid_pitch_slots.append(f"slot {slot.id} references inactive/missing pitch {slot.pitch_id}")

        if invalid_pitch_slots:
            return False, (
                "FIELD instructor slot(s) have invalid pitch assignments: "
                + "; ".join(invalid_pitch_slots)
                + ". Assign an active pitch to every FIELD slot before generating sessions."
            )

        return True, "Ready for session generation"
