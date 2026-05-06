"""
Tournament Pre-Tournament Check-In Endpoint

Regression fix: the 15-minute pre-tournament check-in window existed for regular
sessions but was never wired to tournament bracket generation after the session
generator was decomposed (commit 812512c).

Flow:
  1. Player calls POST /api/v1/tournaments/{tournament_id}/checkin within
     the 15-minute window before the tournament starts.
  2. Their SemesterEnrollment.tournament_checked_in_at is stamped with now().
  3. When generate_sessions() runs, it uses ONLY confirmed (checked-in) players
     as the seeding pool, ignoring non-checked-in enrollees.
  4. Non-confirmed players are excluded from brackets (not assigned walkovers here;
     walkover handling is round-level only, when a player no-shows an assigned match).

─── Operational Rules ───────────────────────────────────────────────────────────

WHEN is bracket generation allowed?
  • ONLY after the check-in window CLOSES (i.e. at/after tournament.date_start).
  • NEVER before the check-in window opens (tournament.date_start - 15 min).
  • NEVER if seeded_count == 0 (neither check-ins nor enrolled players exist).
  • The generate_sessions() endpoint should only be called by an admin/staff user
    after verifying that total_checked_in >= tournament_type.min_players.

MINIMUM PLAYER THRESHOLD:
  • INDIVIDUAL_RANKING   : min 2 players (session_generator.py line ~223)
  • Knockout             : min 2 players (1 match minimum)
  • League               : min 2 players
  • Group+Knockout        : min 8 players (smallest valid group configuration)
  • Swiss                : follows TournamentType.validate_player_count() config

WHAT HAPPENS WITH 1 CHECKED-IN PLAYER?
  • seeded_count = 1 → generate_sessions() returns:
      False, "Not enough players. Need at least 2, have 1", []
  • No bracket is created. The admin must either:
      (a) Wait for more players to check in (if window still open), or
      (b) Use OPS auto-mode to bypass the window and auto-confirm all players.
  • Verified by: TestMinimumPlayerThreshold::test_1_checked_in_out_of_16_enrolled

FALLBACK MODE (OPS / legacy tournaments):
  • If checked_in_count == 0 (no one checked in), generate_sessions() falls back
    to all APPROVED enrollments as the seeding pool.
  • This preserves backward compatibility for tournaments that predate this feature.
  • OPS scenario runner auto-stamps tournament_checked_in_at at enrollment time,
    bypassing the real-time 15-min window for simulated/accelerated tournaments.

BYE LOGIC (knockout brackets with non-power-of-2 player counts):
  • bracket_size = next_power_of_2(seeded_count)
  • byes         = bracket_size - seeded_count   (auto-advance in round 1)
  • real_r1_matches = (seeded_count - byes) // 2
  • Example — 10 checked-in players:
      bracket_size=16, byes=6, real_r1_matches=2
      → 6 players auto-advance + 4 players form 2 real matches in round 1
      → 8 winners proceed to round 2
  • Invariant: byes < bracket_size / 2 (always, for seeded_count >= 2)
  • Verified by: TestByeLogic (test_checkin_seeding_integration.py)

─────────────────────────────────────────────────────────────────────────────────
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user
from app.models.semester import SemesterCategory
from app.models.user import User
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.repositories import TournamentRepository

router = APIRouter()

# Check-in window: player must check in within this many minutes before tournament start
_CHECKIN_WINDOW_MINUTES = 15


@router.post(
    "/{tournament_id}/checkin",
    status_code=status.HTTP_200_OK,
    summary="Pre-tournament check-in",
    description=(
        "Confirms a player's attendance in an upcoming tournament. "
        "Only accepted within the 15-minute window before tournament start. "
        "Bracket generation uses only checked-in players as the seeding pool."
    ),
)
def tournament_checkin(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Pre-tournament check-in (regression fix).

    Authorization: any enrolled player (student role).
    """
    # 1. Tournament must exist
    tournament = TournamentRepository(db).get_or_404(tournament_id)

    # 2. Must be a tournament, not a regular semester
    _TOURNAMENT_CATEGORIES = frozenset({
        SemesterCategory.TOURNAMENT,
        SemesterCategory.MINI_SEASON,
        SemesterCategory.PROMOTION_EVENT,
    })
    if tournament.semester_category not in _TOURNAMENT_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint is only available for tournament semesters",
        )

    # 3. Player must be enrolled and approved
    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == current_user.id,
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active == True,
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
    ).first()

    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not enrolled in this tournament",
        )

    # 4. Already checked in — idempotent
    if enrollment.tournament_checked_in_at is not None:
        return {
            "status": "already_checked_in",
            "checked_in_at": enrollment.tournament_checked_in_at.isoformat(),
            "tournament_id": tournament_id,
            "user_id": current_user.id,
        }

    # 5. Validate check-in window (15 minutes before tournament start)
    if tournament.date_start:
        now = datetime.now(timezone.utc)
        start = tournament.date_start
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

        window_open = start - timedelta(minutes=_CHECKIN_WINDOW_MINUTES)

        if now < window_open:
            minutes_remaining = int((window_open - now).total_seconds() / 60)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Check-in opens {_CHECKIN_WINDOW_MINUTES} minutes before tournament start. "
                    f"Please wait {minutes_remaining} more minutes."
                ),
            )

        if now > start:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tournament has already started. Check-in closed.",
            )

    # 6. Stamp check-in time
    now_utc = datetime.now(timezone.utc)
    enrollment.tournament_checked_in_at = now_utc

    db.commit()

    return {
        "status": "checked_in",
        "checked_in_at": now_utc.isoformat(),
        "tournament_id": tournament_id,
        "user_id": current_user.id,
    }
