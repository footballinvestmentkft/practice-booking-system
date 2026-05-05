"""
Tournament Status Validator Service
Handles tournament status transitions with business rule validation
"""
from typing import Optional, Tuple
from app.models.semester import Semester


def _count_active_participants(tournament) -> int:
    """Count active enrollments for the tournament, respecting participant type.

    TEAM tournaments store enrollments in TournamentTeamEnrollment (tournament_team_enrollments).
    INDIVIDUAL tournaments use SemesterEnrollment (semester_enrollments relationship).
    """
    participant_type = getattr(tournament, 'participant_type', None)
    if participant_type == 'TEAM':
        from app.models.team import TournamentTeamEnrollment
        from sqlalchemy.orm import Session as _Session
        db: _Session = tournament.__dict__.get('_sa_instance_state').session
        if db:
            return db.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == tournament.id,
                TournamentTeamEnrollment.is_active == True,
            ).count()
        return 0
    else:
        enrollments = getattr(tournament, 'enrollments', [])
        return len([e for e in enrollments if e.is_active])


# Valid status transition graph
# NOTE: READY_FOR_ENROLLMENT removed - it was redundant (no player visibility, no functionality)
# Two supported workflows:
#   Fast-path (no instructor): DRAFT → ENROLLMENT_OPEN → ENROLLMENT_CLOSED → IN_PROGRESS
#   Full instructor workflow:  DRAFT → SEEKING_INSTRUCTOR → … → INSTRUCTOR_CONFIRMED → ENROLLMENT_OPEN → …
# NOTE: IN_PROGRESS guard still requires master_instructor_id — so a fast-path tournament
#       can open enrollment without an instructor but cannot be started without one.
VALID_TRANSITIONS = {
    "DRAFT": ["SEEKING_INSTRUCTOR", "ENROLLMENT_OPEN", "ENROLLMENT_CLOSED", "CANCELLED"],
    "SEEKING_INSTRUCTOR": ["PENDING_INSTRUCTOR_ACCEPTANCE", "CANCELLED"],
    "PENDING_INSTRUCTOR_ACCEPTANCE": ["INSTRUCTOR_CONFIRMED", "SEEKING_INSTRUCTOR", "CANCELLED"],
    "INSTRUCTOR_CONFIRMED": ["ENROLLMENT_OPEN", "CANCELLED"],  # Direct to ENROLLMENT_OPEN
    "ENROLLMENT_OPEN": ["ENROLLMENT_CLOSED", "CANCELLED"],
    "ENROLLMENT_CLOSED": ["CHECK_IN_OPEN", "CANCELLED"],  # Direct IN_PROGRESS removed; check-in phase is mandatory
    "CHECK_IN_OPEN": ["IN_PROGRESS", "ENROLLMENT_CLOSED", "CANCELLED"],  # IN_PROGRESS starts the tournament; ENROLLMENT_CLOSED reverts
    "IN_PROGRESS": ["COMPLETED", "CANCELLED", "ENROLLMENT_CLOSED"],  # ENROLLMENT_CLOSED: admin rollback for stuck (0 sessions)
    "COMPLETED": ["REWARDS_DISTRIBUTED", "ARCHIVED"],
    "REWARDS_DISTRIBUTED": ["ARCHIVED"],
    "CANCELLED": ["ARCHIVED"],
    "ARCHIVED": []  # Terminal state
}


class StatusValidationError(Exception):
    """Raised when a status transition is invalid"""


def validate_status_transition(
    current_status: Optional[str],
    new_status: str,
    tournament: Semester
) -> Tuple[bool, Optional[str]]:
    """
    Validate if a status transition is allowed based on business rules

    Args:
        current_status: Current tournament status (None for new tournaments)
        new_status: Desired new status
        tournament: Tournament (Semester) object with related data

    Returns:
        Tuple of (is_valid, error_message)
    """

    # Special case: New tournament creation (NULL → DRAFT)
    if current_status is None:
        if new_status != "DRAFT":
            return False, "New tournaments must start in DRAFT status"
        return True, None

    # 1. Check if transition is allowed in the graph
    allowed_transitions = VALID_TRANSITIONS.get(current_status, [])
    if new_status not in allowed_transitions:
        return False, f"Invalid transition: {current_status} → {new_status} is not allowed"

    # 2. Business rule validations for specific status transitions

    # Guard: DRAFT → ENROLLMENT_CLOSED is a PROMOTION_EVENT-only fast path.
    # All other categories must pass through ENROLLMENT_OPEN first.
    if current_status == "DRAFT" and new_status == "ENROLLMENT_CLOSED":
        from app.models.semester import SemesterCategory
        if getattr(tournament, 'semester_category', None) != SemesterCategory.PROMOTION_EVENT:
            return False, (
                "Direct DRAFT → ENROLLMENT_CLOSED is only allowed for PROMOTION_EVENT tournaments. "
                "Non-promotion events must pass through ENROLLMENT_OPEN first."
            )

    if new_status == "SEEKING_INSTRUCTOR":
        # Must have sessions defined before seeking instructor
        if not tournament.sessions or len(tournament.sessions) == 0:
            return False, "Cannot seek instructor: No sessions defined for this tournament"

        # Must have basic tournament info (name, dates)
        if not tournament.name or not tournament.start_date or not tournament.end_date:
            return False, "Cannot seek instructor: Missing basic tournament information (name, dates)"

    if new_status == "PENDING_INSTRUCTOR_ACCEPTANCE":
        # Must have instructor assigned
        if not tournament.master_instructor_id:
            return False, "Cannot move to pending acceptance: No instructor assigned"

    if new_status == "ENROLLMENT_OPEN":
        # PROMOTION_EVENT uses the DRAFT → ENROLLMENT_CLOSED fast path.
        # Allowing ENROLLMENT_OPEN would create a state with no recovery UI (the bulk enroll
        # button and Lock Audience action were only wired for DRAFT/ENROLLMENT_CLOSED).
        from app.models.semester import SemesterCategory
        if getattr(tournament, 'semester_category', None) == SemesterCategory.PROMOTION_EVENT:
            return False, (
                "PROMOTION_EVENT tournaments cannot enter ENROLLMENT_OPEN. "
                "Use 'Lock Audience & Start Preparation' to transition directly to ENROLLMENT_CLOSED."
            )

        # instructor check only on the full workflow path (INSTRUCTOR_CONFIRMED → ENROLLMENT_OPEN)
        # Fast-path (DRAFT → ENROLLMENT_OPEN) is allowed without an instructor;
        # IN_PROGRESS guard will enforce instructor assignment before the tournament can start.
        if current_status == "INSTRUCTOR_CONFIRMED" and not tournament.master_instructor_id:
            return False, "Cannot open enrollment: No instructor assigned"
        # max_players is optional — unlimited if not set
        # Must have campus assigned — campus_id is required before sessions can be generated
        if not getattr(tournament, 'campus_id', None):
            return False, (
                "Cannot open enrollment: No campus assigned. "
                "Set campus_id via PATCH /{id} before opening enrollment."
            )

        # Tournament name must be non-empty
        name = getattr(tournament, 'name', None)
        if not name or not str(name).strip():
            return False, "Cannot open enrollment: Tournament name is required"

        # Dates must be valid: start not in the past, end >= start
        from datetime import date as _date, datetime as _datetime
        start = getattr(tournament, 'start_date', None)
        end = getattr(tournament, 'end_date', None)
        # Normalise to date — datetime is a subclass of date, so check datetime first
        start_d = start.date() if isinstance(start, _datetime) else (start if isinstance(start, _date) else None)
        end_d = end.date() if isinstance(end, _datetime) else (end if isinstance(end, _date) else None)
        if start_d and start_d < _date.today():
            return False, "Cannot open enrollment: Start date is in the past"
        if start_d and end_d and end_d < start_d:
            return False, "Cannot open enrollment: End date must be on or after start date"

        # HEAD_TO_HEAD tournaments need a tournament type (league/knockout/etc.)
        fmt = getattr(tournament, 'format', None)
        if fmt == "HEAD_TO_HEAD" and not getattr(tournament, 'tournament_type_id', None):
            return False, (
                "Cannot open enrollment: Tournament type (league/knockout/etc.) "
                "must be selected for HEAD_TO_HEAD format"
            )

    if new_status == "ENROLLMENT_CLOSED":
        # SM-BUG-01 fix: bifurcate guard on source state.
        # IN_PROGRESS → ENROLLMENT_CLOSED is an admin emergency rollback for a
        # stuck tournament.  The player-count guard was designed for the forward
        # path (ENROLLMENT_OPEN/DRAFT → ENROLLMENT_CLOSED) and must NOT block a rewind.
        if current_status != "IN_PROGRESS":
            from app.models.semester import SemesterCategory
            if getattr(tournament, 'semester_category', None) == SemesterCategory.PROMOTION_EVENT:
                # PROMOTION_EVENT forward path: validate campaign linkage + active audience.
                # Counts SponsorAudienceEntry rows that are ACTIVE with consent — the same
                # filter used by promote_entries() — so only entries that can become
                # enrolled participants count toward readiness.
                if not tournament.organizer_sponsor_id or not tournament.organizer_campaign_id:
                    return False, (
                        "Cannot lock audience: PROMOTION_EVENT must have both organizer sponsor "
                        "and campaign linked before locking."
                    )
                _db = tournament.__dict__.get('_sa_instance_state').session
                if _db:
                    from app.models.sponsor import SponsorAudienceEntry
                    audience_count = _db.query(SponsorAudienceEntry).filter(
                        SponsorAudienceEntry.sponsor_id == tournament.organizer_sponsor_id,
                        SponsorAudienceEntry.campaign_id == tournament.organizer_campaign_id,
                        SponsorAudienceEntry.status == "ACTIVE",
                        SponsorAudienceEntry.consent_given == True,
                    ).count()
                    if audience_count < 1:
                        return False, (
                            "Cannot lock audience: campaign has no active, consented audience entries. "
                            "Import and activate campaign entries before locking."
                        )
            else:
                # Standard forward path: SemesterEnrollment-based participant count.
                player_count = _count_active_participants(tournament)

                # Get minimum from tournament type (fallback to 2 if no type configured)
                min_players_required = 2
                if tournament.tournament_type_id:
                    from app.models.tournament_type import TournamentType
                    from sqlalchemy.orm import Session

                    db: Session = tournament.__dict__.get('_sa_instance_state').session
                    if db:
                        tournament_type = db.query(TournamentType).filter(TournamentType.id == tournament.tournament_type_id).first()
                        if tournament_type:
                            min_players_required = tournament_type.min_players

                if player_count < min_players_required:
                    return False, f"Cannot close enrollment: Minimum {min_players_required} participants required (current: {player_count})"
        # else: rollback path (IN_PROGRESS → ENROLLMENT_CLOSED) — allow unconditionally

    if new_status == "IN_PROGRESS":
        # Participant count contract: SemesterEnrollment is the SOLE source of truth
        # for ALL tournament types, including PROMOTION_EVENT.
        # Campaign audience (SponsorAudienceEntry) drives only the ENROLLMENT_CLOSED
        # "lock audience" guard.  Reaching IN_PROGRESS requires that bulk_enroll_from_campaign
        # (or manual enroll) has created actual SemesterEnrollment rows first.
        # Do NOT replace _count_active_participants with an audience query here.

        # Check instructor assignment: legacy field OR new TournamentInstructorSlot
        has_instructor = bool(tournament.master_instructor_id)
        if not has_instructor:
            # Fallback: check TournamentInstructorSlot for a non-absent MASTER slot.
            # Handles tournaments where the new planning system was used (slot exists
            # but legacy master_instructor_id was never set).
            from app.models.tournament_instructor_slot import TournamentInstructorSlot
            _state = tournament.__dict__.get('_sa_instance_state')
            _db = _state.session if _state else None
            if _db:
                master_slot = _db.query(TournamentInstructorSlot).filter(
                    TournamentInstructorSlot.semester_id == tournament.id,
                    TournamentInstructorSlot.role == 'MASTER',
                    TournamentInstructorSlot.status.notin_(['ABSENT']),
                ).first()
                has_instructor = bool(master_slot)
        if not has_instructor:
            return False, "Cannot start tournament: No instructor assigned"

        # Validate against tournament type's minimum player requirement
        player_count = _count_active_participants(tournament)

        # Get minimum from tournament type (fallback to 2 if no type configured)
        min_players_required = 2
        if tournament.tournament_type_id:
            # Load tournament type to get min_players
            from app.models.tournament_type import TournamentType
            from sqlalchemy.orm import Session

            # Get db session from tournament
            db: Session = tournament.__dict__.get('_sa_instance_state').session
            if db:
                tournament_type = db.query(TournamentType).filter(TournamentType.id == tournament.tournament_type_id).first()
                if tournament_type:
                    min_players_required = tournament_type.min_players

        if player_count < min_players_required:
            return False, f"Cannot start tournament: Minimum {min_players_required} participants required (current: {player_count})"

    if new_status == "COMPLETED":
        sessions = getattr(tournament, 'sessions', [])
        if not sessions:
            return False, "Cannot complete tournament: No sessions found"

        # Require at least 1 TournamentRanking row before marking COMPLETED
        from app.models.tournament_ranking import TournamentRanking
        _state = tournament.__dict__.get('_sa_instance_state')
        _db = _state.session if _state else None
        if _db:
            ranking_count = _db.query(TournamentRanking).filter(
                TournamentRanking.tournament_id == tournament.id
            ).count()
            if ranking_count == 0:
                return False, (
                    "Cannot complete tournament: No rankings calculated yet. "
                    "Call POST /{id}/calculate-rankings first."
                )

    if new_status == "REWARDS_DISTRIBUTED":
        # Rankings must be submitted before distributing rewards
        pass

        # Note: Attendance validation is NOT required here - rankings are sufficient
        # The reward distribution endpoint will handle any additional validations

        # Count submitted rankings
        # NOTE: Cannot use func.count() with filter directly - need proper query
        # For now, skip the complex SQL validation (endpoint will validate)

    # All validations passed
    return True, None


def get_next_allowed_statuses(current_status: Optional[str]) -> list[str]:
    """
    Get list of statuses that can be transitioned to from current status

    Args:
        current_status: Current tournament status (None for new tournaments)

    Returns:
        List of allowed next statuses
    """
    if current_status is None:
        return ["DRAFT"]

    return VALID_TRANSITIONS.get(current_status, [])


def is_terminal_status(status: str) -> bool:
    """
    Check if a status is terminal (no further transitions allowed)

    Args:
        status: Tournament status

    Returns:
        True if terminal status
    """
    return len(VALID_TRANSITIONS.get(status, [])) == 0
