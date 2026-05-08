"""
Tournament Readiness Validator

Pre-transition content checks for CHECK_IN_OPEN, IN_PROGRESS, COMPLETED,
and REWARDS_DISTRIBUTED.  These complement the status-graph and
enrollment-count checks in status_validator.py with content-level policy
enforcement:
    - status_validator.py: valid transitions + instructor presence + enrollment count
    - readiness_validator.py (this file): schedule config + reward config + session
      completion + ranking coverage + participation records

Called from lifecycle.py before the status flush so any 400 leaves
tournament_status unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models.semester import Semester


@dataclass
class ReadinessResult:
    ok: bool
    blocking_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requirement_codes: list[str] = field(default_factory=list)


def check_pre_check_in_open(db: "Session", tournament: "Semester") -> ReadinessResult:
    """
    Validate content readiness before transitioning to CHECK_IN_OPEN.

    Policy:
    - match_duration_minutes, break_duration_minutes, parallel_fields must all
      be explicitly configured in TournamentConfiguration.  NULL values are NOT
      silently defaulted to 90 / 15 / 1 — the admin must make an explicit choice.
    - Valid ranges: match_duration > 0, break_duration >= 0, parallel_fields >= 1.
    - PROMOTION_EVENT: organizer_sponsor_id must be set and the sponsor must be active.
      organizer_campaign_id is optional at this stage (follow-up: campaign status /
      audience import / budget mapping require a separate policy decision).
    """
    errors: list[str] = []
    codes: list[str] = []

    cfg = tournament.tournament_config_obj
    missing: list[str] = []

    if cfg is None or cfg.match_duration_minutes is None:
        missing.append("match_duration_minutes")
    if cfg is None or cfg.break_duration_minutes is None:
        missing.append("break_duration_minutes")
    if cfg is None or cfg.parallel_fields is None:
        missing.append("parallel_fields")

    if missing:
        errors.append(
            f"Schedule Configuration must be set before CHECK_IN_OPEN. "
            f"Missing: {', '.join(missing)}. "
            f"Configure via the Schedule Configuration section."
        )
        codes.append("SCHEDULE_CONFIG_MISSING")
    else:
        range_errs: list[str] = []
        if cfg.match_duration_minutes <= 0:
            range_errs.append("match_duration_minutes must be > 0")
        if cfg.break_duration_minutes < 0:
            range_errs.append("break_duration_minutes cannot be negative")
        if cfg.parallel_fields < 1:
            range_errs.append("parallel_fields must be >= 1")
        if range_errs:
            errors.extend(range_errs)
            codes.append("SCHEDULE_CONFIG_INVALID")

    # PROMOTION_EVENT organizer guard — the event must have at least one organizer
    # (organizer_club_id OR organizer_sponsor_id).  The two fields are mutually
    # exclusive (Semester._guard_single_organizer_fk ORM validator).
    # Club-organized events (organizer_club_id set, organizer_sponsor_id NULL) are
    # valid without further sponsor checks.  Sponsor-organized events must reference
    # an active Sponsor.  organizer_campaign_id is optional at this stage.
    from app.models.semester import SemesterCategory
    if tournament.semester_category == SemesterCategory.PROMOTION_EVENT:
        if not tournament.organizer_sponsor_id and not tournament.organizer_club_id:
            errors.append(
                "PROMOTION_EVENT tournaments require an organizer (club or sponsor) before CHECK_IN_OPEN. "
                "Set organizer_sponsor_id or organizer_club_id via the Tournament Settings."
            )
            codes.append("PROMOTION_SPONSOR_MISSING")
        elif tournament.organizer_sponsor_id:
            from app.models.sponsor import Sponsor
            sponsor = (
                db.query(Sponsor)
                .filter(Sponsor.id == tournament.organizer_sponsor_id)
                .first()
            )
            if sponsor is None or not sponsor.is_active:
                errors.append(
                    "The organizer sponsor is inactive or not found. "
                    "Activate the sponsor record before transitioning to CHECK_IN_OPEN."
                )
                codes.append("PROMOTION_SPONSOR_INACTIVE")
        # else: organizer_club_id is set — club-organized PROMOTION_EVENT, allowed.

    return ReadinessResult(
        ok=not errors,
        blocking_errors=errors,
        warnings=[],
        requirement_codes=codes,
    )


def check_pre_in_progress(db: "Session", tournament: "Semester") -> ReadinessResult:
    """
    Validate content readiness before transitioning to IN_PROGRESS.

    Policy:
    - Reward configuration must be present (non-empty reward_config JSONB).
      A tournament entering IN_PROGRESS without reward config will never
      distribute XP or badges to participants.

    TODO (GAP-4 tech debt): Re-validate master instructor LFA_COACH eligibility.
    status_validator.py:168-186 checks instructor *presence* only; a license may
    expire between CHECK_IN_OPEN and IN_PROGRESS.  When addressed, call
    check_tournament_master_instructor_eligible(db, tournament.id) here and surface
    as a INSTRUCTOR_ELIGIBILITY_STALE blocking error with code "INSTRUCTOR_LICENSE_EXPIRED".
    """
    errors: list[str] = []
    codes: list[str] = []

    # tournament.reward_config property returns {} when reward_config_obj is None
    # or when reward_config_obj.reward_config is None/empty — both are falsy.
    if not tournament.reward_config:
        errors.append(
            "Reward Configuration is required before starting the tournament (IN_PROGRESS). "
            "Set a reward policy via the Reward Configuration section."
        )
        codes.append("REWARD_CONFIG_MISSING")

    return ReadinessResult(
        ok=not errors,
        blocking_errors=errors,
        warnings=[],
        requirement_codes=codes,
    )


def check_pre_completed(db: "Session", tournament: "Semester") -> ReadinessResult:
    """
    Validate content readiness before transitioning to COMPLETED.

    Policy:
    - All auto-generated MATCH sessions must have session_status == 'completed'.
      Manual or TRAINING sessions are excluded — only sessions where
      auto_generated=True AND event_category=MATCH are checked.
    - TournamentRanking count must equal enrolled participant count (INDIVIDUAL:
      SemesterEnrollment approved+active; TEAM: TournamentTeamEnrollment active).
      Rankings are calculated via POST /{id}/calculate-rankings.
    """
    errors: list[str] = []
    codes: list[str] = []

    from app.models.session import Session as SessionModel, EventCategory
    from app.models.tournament_ranking import TournamentRanking

    # Check 1: all auto-generated MATCH sessions must be session_status='completed'.
    incomplete_count = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == tournament.id,
            SessionModel.auto_generated == True,  # noqa: E712
            SessionModel.event_category == EventCategory.MATCH,
            SessionModel.session_status != "completed",
        )
        .count()
    )

    if incomplete_count > 0:
        errors.append(
            f"{incomplete_count} match session(s) are not yet completed "
            f"(session_status != 'completed'). "
            "Mark all tournament matches as completed before closing the tournament."
        )
        codes.append("SESSIONS_INCOMPLETE")

    # Check 2: ranking count must equal enrolled participant count.
    cfg = tournament.tournament_config_obj
    participant_type = cfg.participant_type if cfg else "INDIVIDUAL"

    if participant_type == "TEAM":
        from app.models.team import TournamentTeamEnrollment
        enrolled_count = (
            db.query(TournamentTeamEnrollment)
            .filter(
                TournamentTeamEnrollment.semester_id == tournament.id,
                TournamentTeamEnrollment.is_active == True,  # noqa: E712
            )
            .count()
        )
        ranking_participant_type = "TEAM"
    else:
        from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
        enrolled_count = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == tournament.id,
                SemesterEnrollment.is_active == True,  # noqa: E712
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            )
            .count()
        )
        ranking_participant_type = "INDIVIDUAL"

    ranking_count = (
        db.query(TournamentRanking)
        .filter(
            TournamentRanking.tournament_id == tournament.id,
            TournamentRanking.participant_type == ranking_participant_type,
        )
        .count()
    )

    if enrolled_count > 0 and ranking_count != enrolled_count:
        errors.append(
            f"Rankings are incomplete: {ranking_count} of {enrolled_count} "
            f"{ranking_participant_type.lower()} participant(s) ranked. "
            "Call POST /{id}/calculate-rankings before completing the tournament."
        )
        codes.append("RANKINGS_INCOMPLETE")

    return ReadinessResult(
        ok=not errors,
        blocking_errors=errors,
        warnings=[],
        requirement_codes=codes,
    )


def check_pre_rewards_distributed(db: "Session", tournament: "Semester") -> ReadinessResult:
    """
    Validate content readiness before transitioning to REWARDS_DISTRIBUTED.

    Policy:
    - reward_policy_snapshot must be non-null.  The snapshot is locked at
      IN_PROGRESS entry (lifecycle.py).  If it is missing the reward policy
      cannot be audited after distribution.
    - TournamentRanking must cover all enrolled participants (ranking_count ==
      enrolled_count).  Stale or partial rankings block distribution.
    - TournamentParticipation records must cover all enrolled participants:
        INDIVIDUAL: participation_count == enrolled_user_count
        TEAM: every active enrolled team_id must appear in at least one
              TournamentParticipation.team_id row.  This is a direct FK join —
              TournamentParticipation.team_id references the same teams.id as
              TournamentTeamEnrollment.team_id.
    """
    errors: list[str] = []
    codes: list[str] = []

    from app.models.tournament_ranking import TournamentRanking
    from app.models.tournament_achievement import TournamentParticipation

    # Check 1: reward_policy_snapshot must be locked.
    if not tournament.reward_policy_snapshot:
        errors.append(
            "Reward policy snapshot is missing. The snapshot is locked at IN_PROGRESS "
            "entry — ensure reward_config was set before the tournament was started."
        )
        codes.append("SNAPSHOT_MISSING")

    # Resolve participant type and enrolled count (shared by checks 2 + 3).
    cfg = tournament.tournament_config_obj
    participant_type = cfg.participant_type if cfg else "INDIVIDUAL"

    if participant_type == "TEAM":
        from app.models.team import TournamentTeamEnrollment
        enrolled_team_ids: set[int] = {
            row[0]
            for row in db.query(TournamentTeamEnrollment.team_id)
            .filter(
                TournamentTeamEnrollment.semester_id == tournament.id,
                TournamentTeamEnrollment.is_active == True,  # noqa: E712
            )
            .all()
        }
        enrolled_count = len(enrolled_team_ids)
        ranking_participant_type = "TEAM"
    else:
        from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
        enrolled_count = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == tournament.id,
                SemesterEnrollment.is_active == True,  # noqa: E712
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            )
            .count()
        )
        enrolled_team_ids = set()
        ranking_participant_type = "INDIVIDUAL"

    # Check 2: rankings must be complete.
    ranking_count = (
        db.query(TournamentRanking)
        .filter(
            TournamentRanking.tournament_id == tournament.id,
            TournamentRanking.participant_type == ranking_participant_type,
        )
        .count()
    )

    if enrolled_count > 0 and ranking_count != enrolled_count:
        errors.append(
            f"Rankings are incomplete: {ranking_count} of {enrolled_count} "
            f"{ranking_participant_type.lower()} participant(s) ranked. "
            "Recalculate rankings before distributing rewards."
        )
        codes.append("RANKINGS_INCOMPLETE")

    # Check 3: TournamentParticipation coverage.
    if participant_type == "TEAM" and enrolled_team_ids:
        covered_team_ids: set[int] = {
            row[0]
            for row in db.query(TournamentParticipation.team_id)
            .filter(
                TournamentParticipation.semester_id == tournament.id,
                TournamentParticipation.team_id.isnot(None),
            )
            .all()
        }
        uncovered = enrolled_team_ids - covered_team_ids
        if uncovered:
            any_covered = bool(covered_team_ids & enrolled_team_ids)
            errors.append(
                f"{len(uncovered)} enrolled team(s) have no reward distribution records "
                f"(TournamentParticipation). "
                "Call the distribute-rewards endpoint before marking REWARDS_DISTRIBUTED."
            )
            codes.append(
                "PARTICIPATION_INCOMPLETE" if any_covered else "PARTICIPATION_RECORDS_MISSING"
            )
    else:
        # INDIVIDUAL: one TournamentParticipation row per enrolled user expected.
        participation_count = (
            db.query(TournamentParticipation)
            .filter(TournamentParticipation.semester_id == tournament.id)
            .count()
        )

        if enrolled_count > 0 and participation_count == 0:
            errors.append(
                "No reward distribution records found (TournamentParticipation). "
                "Call the distribute-rewards endpoint before marking REWARDS_DISTRIBUTED."
            )
            codes.append("PARTICIPATION_RECORDS_MISSING")
        elif enrolled_count > 0 and participation_count < enrolled_count:
            errors.append(
                f"Reward distribution is incomplete: {participation_count} of "
                f"{enrolled_count} participant(s) have distribution records. "
                "Call the distribute-rewards endpoint."
            )
            codes.append("PARTICIPATION_INCOMPLETE")

    return ReadinessResult(
        ok=not errors,
        blocking_errors=errors,
        warnings=[],
        requirement_codes=codes,
    )
