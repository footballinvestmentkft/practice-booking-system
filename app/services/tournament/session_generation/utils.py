"""
Tournament Session Generation Utilities

Helper functions for session generation.
"""
import logging
from typing import List, Optional, TYPE_CHECKING
from app.models.semester import Semester

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DBSession


def pick_campus(session_index: int, campus_ids: Optional[List[int]]) -> Optional[int]:
    """
    Round-robin campus selection for multi-campus session distribution.

    Returns campus_ids[session_index % len(campus_ids)] when campus_ids is provided,
    otherwise None (session inherits tournament.campus_id — existing behaviour).

    Args:
        session_index: Zero-based index of the session being created (use len(sessions)).
        campus_ids: Explicit list of campus IDs from the request. None = single-campus mode.

    Returns:
        int | None: Campus ID to assign to this session, or None.
    """
    if not campus_ids:
        return None
    return campus_ids[session_index % len(campus_ids)]


def pick_pitch(
    session_index: int,
    campus_id: Optional[int],
    parallel_fields: int,
    db: "DBSession",
) -> Optional[int]:
    """
    Round-robin pitch selection within a campus based on parallel_fields.

    For each session on a campus, assigns a pitch in rotating order:
      parallel_fields=1 → always Field 1
      parallel_fields=2 → Field 1, Field 2, Field 1, Field 2, ...

    Pitches are auto-created if they don't exist yet (idempotent flush, no commit).
    Returns None when campus_id is None (single-campus mode without explicit campus).

    Args:
        session_index: Zero-based index of the session being created.
        campus_id:     Campus the session belongs to (None = no pitch assignment).
        parallel_fields: Number of concurrent fields available at this campus.
        db:            Database session (for get-or-create of Pitch records).

    Returns:
        int | None: Pitch ID to assign to this session, or None.
    """
    if not campus_id:
        return None
    effective_fields = max(parallel_fields, 1)
    pitch_number = (session_index % effective_fields) + 1  # 1-based
    return _get_or_create_pitch(db, campus_id, pitch_number).id


def _get_or_create_pitch(db: "DBSession", campus_id: int, pitch_number: int):
    """
    Get existing Pitch or create a new one (flush-only, SAVEPOINT-safe).

    Auto-generated name follows the pattern: "Field {pitch_number}"
    (admin can rename via the pitches API later).
    """
    from app.models.pitch import Pitch

    pitch = (
        db.query(Pitch)
        .filter(Pitch.campus_id == campus_id, Pitch.pitch_number == pitch_number)
        .first()
    )
    if not pitch:
        pitch = Pitch(
            campus_id=campus_id,
            pitch_number=pitch_number,
            name=f"Field {pitch_number}",
            capacity=2,
        )
        db.add(pitch)
        db.flush()  # Get the ID without committing (SAVEPOINT-compatible)
    return pitch


def get_tournament_venue(tournament: Semester) -> str:
    """
    Get tournament venue with proper fallback chain.

    Replaces deprecated tournament.location_venue attribute.

    Fallback chain:
    1. tournament.campus.venue (most specific - facility level)
    2. tournament.campus.name (campus name if no venue)
    3. tournament.location.city (city level fallback)
    4. 'TBD' (if no location data available)

    Args:
        tournament: Tournament (Semester) instance

    Returns:
        str: Venue string or 'TBD' if not available

    Examples:
        >>> # Campus with venue
        >>> get_tournament_venue(tournament)
        'Main Field'

        >>> # Campus without venue
        >>> get_tournament_venue(tournament)
        'Buda Campus (Budapest)'

        >>> # Location only
        >>> get_tournament_venue(tournament)
        'Budapest'

        >>> # No location data
        >>> get_tournament_venue(tournament)
        'TBD'

    Note:
        Requires eager loading of tournament.campus and tournament.location
        relationships to avoid N+1 queries.
    """
    # Priority 1: Campus venue (most specific)
    if tournament.campus:
        if tournament.campus.venue:
            return tournament.campus.venue

        # Fallback: Campus name with city
        if tournament.campus.name and tournament.campus.location:
            return f"{tournament.campus.name} ({tournament.campus.location.city})"

        # Campus name only
        if tournament.campus.name:
            return tournament.campus.name

    # Priority 2: Location city (city-level fallback)
    if tournament.location:
        return tournament.location.city

    # Priority 3: Default
    return 'TBD'


def get_campus_schedule(
    db: "DBSession",
    tournament_id: int,
    campus_id: Optional[int],
    global_match_duration: int = 90,
    global_break_duration: int = 15,
    global_parallel_fields: int = 1,
) -> dict:
    """
    Resolve effective schedule parameters for a (tournament, campus) pair.

    Precedence:
      1. campus_schedule_configs row for (tournament_id, campus_id)
      2. global TournamentConfiguration values (passed as parameters)

    Returns a dict with:
      - match_duration_minutes: int
      - break_duration_minutes: int
      - parallel_fields: int
      - venue_label: str | None
    """
    if campus_id is not None:
        from app.models.campus_schedule_config import CampusScheduleConfig
        cfg = db.query(CampusScheduleConfig).filter(
            CampusScheduleConfig.tournament_id == tournament_id,
            CampusScheduleConfig.campus_id == campus_id,
            CampusScheduleConfig.is_active == True,
        ).first()
        if cfg:
            return {
                "match_duration_minutes": cfg.resolved_match_duration(global_match_duration),
                "break_duration_minutes": cfg.resolved_break_duration(global_break_duration),
                "parallel_fields": cfg.resolved_parallel_fields(global_parallel_fields),
                "venue_label": cfg.venue_label,
            }

    return {
        "match_duration_minutes": global_match_duration,
        "break_duration_minutes": global_break_duration,
        "parallel_fields": global_parallel_fields,
        "venue_label": None,
    }


def dedup_participant_ids(
    raw_ids: List[int],
    tournament_id: int,
    logger: logging.Logger,
    context: str = "",
) -> List[int]:
    """
    Remove duplicate user/team IDs from a seeding pool, preserving insertion order.

    Duplicate IDs cause self-match sessions (participant_user_ids = [id, id]).
    This is the single dedup point used by all format generators (P0-A fix).

    Logs an error when duplicates are found so the enrollment data anomaly is
    visible in monitoring without crashing generation.
    """
    deduped = list(dict.fromkeys(raw_ids))
    if len(deduped) != len(raw_ids):
        dupes = [uid for uid in set(raw_ids) if raw_ids.count(uid) > 1]
        logger.error(
            "🚨 SEEDING DEDUP | tournament=%s | raw=%d → unique=%d | "
            "duplicate_ids=%s | context=%s",
            tournament_id, len(raw_ids), len(deduped), sorted(dupes), context,
        )
    return deduped
