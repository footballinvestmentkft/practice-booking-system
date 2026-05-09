"""
Tests for session_generation/utils.py and generation_validator.py

Missing coverage (utils.py):
  Lines 77-78: get_tournament_venue — campus.name + location.city → "Name (City)"
  Lines 81-82: get_tournament_venue — campus.name only (no location)
  Lines 85-86: get_tournament_venue — no campus, has location → location.city
  Line  128:   get_campus_schedule — campus_id=None → global defaults (db.query not called)

Missing coverage (generation_validator.py):
  Line  36:    sessions_generated → False + reason
  Line  42:    HEAD_TO_HEAD without tournament_type_id → False
  Lines 46-48: INDIVIDUAL_RANKING with tournament_type_id → False
  Line  63:    INDIVIDUAL_RANKING min_players=2 fallback
  Lines 67-70: HEAD_TO_HEAD loads TournamentType.min_players from DB
  Line  73:    not enough players → False + reason
"""
import pytest
from unittest.mock import MagicMock

from app.services.tournament.session_generation.utils import (
    get_tournament_venue,
    get_campus_schedule,
    pick_campus,
)
from app.services.tournament.session_generation.validators.generation_validator import (
    GenerationValidator,
)


# ══════════════════════════════════════════════════════════════════
# get_tournament_venue
# ══════════════════════════════════════════════════════════════════


def _t(campus=None, location=None):
    t = MagicMock()
    t.campus = campus
    t.location = location
    return t


class TestGetTournamentVenue:

    def test_campus_venue_wins(self):
        """Lines 72-74: campus.venue set → return it directly."""
        campus = MagicMock()
        campus.venue = "Main Field"
        assert get_tournament_venue(_t(campus=campus)) == "Main Field"

    def test_campus_name_with_location_city(self):
        """Lines 77-78: campus has no venue but has name + location.city."""
        campus = MagicMock()
        campus.venue = None
        campus.name = "Buda Campus"
        campus.location = MagicMock()
        campus.location.city = "Budapest"

        assert get_tournament_venue(_t(campus=campus)) == "Buda Campus (Budapest)"

    def test_campus_name_no_location(self):
        """Lines 81-82: campus.name but campus.location is None → just 'Name'."""
        campus = MagicMock()
        campus.venue = None
        campus.name = "Pest Field"
        campus.location = None

        assert get_tournament_venue(_t(campus=campus)) == "Pest Field"

    def test_location_city_fallback_no_campus(self):
        """Lines 85-86: campus is None, location present → location.city."""
        location = MagicMock()
        location.city = "Debrecen"

        assert get_tournament_venue(_t(campus=None, location=location)) == "Debrecen"

    def test_tbd_no_campus_no_location(self):
        """Line 89: campus=None, location=None → 'TBD'."""
        assert get_tournament_venue(_t()) == "TBD"


# ══════════════════════════════════════════════════════════════════
# get_campus_schedule
# ══════════════════════════════════════════════════════════════════


class TestGetCampusSchedule:

    def test_no_campus_id_returns_globals_without_query(self):
        """Line 128: campus_id=None → returns global defaults, db.query not called."""
        db = MagicMock()

        result = get_campus_schedule(
            db,
            tournament_id=1,
            campus_id=None,
            global_match_duration=90,
            global_break_duration=15,
            global_parallel_fields=2,
        )

        assert result["match_duration_minutes"] == 90
        assert result["break_duration_minutes"] == 15
        assert result["parallel_fields"] == 2
        assert result["venue_label"] is None
        db.query.assert_not_called()

    def test_campus_id_no_config_returns_globals(self):
        """campus_id provided but no CampusScheduleConfig found → global defaults."""
        db = MagicMock()
        cfg_q = MagicMock()
        cfg_q.filter.return_value = cfg_q
        cfg_q.first.return_value = None
        db.query.return_value = cfg_q

        result = get_campus_schedule(
            db,
            tournament_id=1,
            campus_id=5,
            global_match_duration=75,
            global_break_duration=10,
            global_parallel_fields=1,
        )

        assert result["match_duration_minutes"] == 75
        assert result["venue_label"] is None

    def test_campus_id_with_config_uses_resolved_values(self):
        """campus_id + CampusScheduleConfig found → resolved values returned."""
        db = MagicMock()
        cfg = MagicMock()
        cfg.resolved_match_duration.return_value = 60
        cfg.resolved_break_duration.return_value = 10
        cfg.resolved_parallel_fields.return_value = 3
        cfg.venue_label = "Field A"

        cfg_q = MagicMock()
        cfg_q.filter.return_value = cfg_q
        cfg_q.first.return_value = cfg
        db.query.return_value = cfg_q

        result = get_campus_schedule(db, tournament_id=1, campus_id=5)

        assert result["match_duration_minutes"] == 60
        assert result["parallel_fields"] == 3
        assert result["venue_label"] == "Field A"


# ══════════════════════════════════════════════════════════════════
# GenerationValidator
# ══════════════════════════════════════════════════════════════════


def _make_tournament(
    t_id=1,
    sessions_generated=False,
    sessions_generated_at=None,
    fmt="INDIVIDUAL_RANKING",
    type_id=None,
    status="IN_PROGRESS",
):
    t = MagicMock()
    t.id = t_id
    t.sessions_generated = sessions_generated
    t.sessions_generated_at = sessions_generated_at
    t.format = fmt
    t.tournament_type_id = type_id
    t.tournament_status = status
    return t


def _make_validator(tournament=None):
    """Build GenerationValidator with mocked tournament_repo."""
    db = MagicMock()
    v = GenerationValidator(db)
    v.tournament_repo = MagicMock()
    v.tournament_repo.get_optional.return_value = tournament
    return v, db


class TestGenerationValidator:

    def test_tournament_not_found(self):
        v, _ = _make_validator(None)
        ok, reason = v.can_generate_sessions(99)
        assert ok is False
        assert "not found" in reason

    def test_already_generated(self):
        """Line 36: sessions_generated=True → False with 'already generated'."""
        t = _make_tournament(sessions_generated=True, sessions_generated_at="2026-01-01T10:00:00")
        v, _ = _make_validator(t)

        ok, reason = v.can_generate_sessions(1)

        assert ok is False
        assert "already generated" in reason

    def test_head_to_head_no_tournament_type(self):
        """Line 42: HEAD_TO_HEAD without tournament_type_id → False."""
        t = _make_tournament(fmt="HEAD_TO_HEAD", type_id=None, status="IN_PROGRESS")
        v, _ = _make_validator(t)

        ok, reason = v.can_generate_sessions(1)

        assert ok is False
        assert "tournament type" in reason.lower()

    def test_individual_ranking_has_tournament_type(self):
        """Lines 46-48: INDIVIDUAL_RANKING with type_id → False."""
        t = _make_tournament(fmt="INDIVIDUAL_RANKING", type_id=5, status="IN_PROGRESS")
        v, _ = _make_validator(t)

        ok, reason = v.can_generate_sessions(1)

        assert ok is False
        assert "cannot have a tournament type" in reason

    def test_invalid_format(self):
        """Line 48: unknown format → False with 'Invalid tournament format'."""
        t = _make_tournament(fmt="UNKNOWN_FORMAT", status="IN_PROGRESS")
        v, _ = _make_validator(t)

        ok, reason = v.can_generate_sessions(1)

        assert ok is False
        assert "Invalid tournament format" in reason

    def test_status_not_in_progress_or_completed(self):
        """Line 52: status not in {CHECK_IN_OPEN, IN_PROGRESS, COMPLETED} → False."""
        t = _make_tournament(fmt="INDIVIDUAL_RANKING", status="ENROLLMENT_OPEN")
        v, _ = _make_validator(t)

        ok, reason = v.can_generate_sessions(1)

        assert ok is False
        assert "CHECK_IN_OPEN" in reason or "IN_PROGRESS" in reason

    def test_individual_ranking_not_enough_players(self):
        """Line 63: IR format, only 1 enrolled (< 2 minimum) → False."""
        t = _make_tournament(fmt="INDIVIDUAL_RANKING", status="IN_PROGRESS")
        v, db = _make_validator(t)

        enroll_q = MagicMock()
        enroll_q.filter.return_value = enroll_q
        enroll_q.count.return_value = 1  # < 2
        db.query.return_value = enroll_q

        ok, reason = v.can_generate_sessions(1)

        assert ok is False
        assert "2" in reason  # Need at least 2

    def test_head_to_head_loads_type_min_players_passes(self):
        """Lines 67-70: HEAD_TO_HEAD → loads TournamentType.min_players → enough."""
        from app.models.tournament_type import TournamentType
        from app.models.semester_enrollment import SemesterEnrollment

        t = _make_tournament(fmt="HEAD_TO_HEAD", type_id=3, status="IN_PROGRESS")
        # No campus → skip pitch check; instructor check bypassed via flag
        t.campus_id = None
        v, db = _make_validator(t)

        tournament_type = MagicMock()
        tournament_type.min_players = 4

        enroll_q = MagicMock()
        enroll_q.filter.return_value = enroll_q
        enroll_q.count.return_value = 6  # enough

        type_q = MagicMock()
        type_q.filter.return_value = type_q
        type_q.first.return_value = tournament_type

        def _q(model):
            if model is TournamentType:
                return type_q
            return enroll_q

        db.query.side_effect = _q

        ok, reason = v.can_generate_sessions(1, skip_instructor_check=True)

        assert ok is True

    def test_head_to_head_not_enough_players(self):
        """Lines 67-73: HEAD_TO_HEAD type.min_players=6, only 4 → False."""
        from app.models.tournament_type import TournamentType

        t = _make_tournament(fmt="HEAD_TO_HEAD", type_id=3, status="IN_PROGRESS")
        v, db = _make_validator(t)

        tournament_type = MagicMock()
        tournament_type.min_players = 6

        enroll_q = MagicMock()
        enroll_q.filter.return_value = enroll_q
        enroll_q.count.return_value = 4

        type_q = MagicMock()
        type_q.filter.return_value = type_q
        type_q.first.return_value = tournament_type

        def _q(model):
            if model is TournamentType:
                return type_q
            return enroll_q

        db.query.side_effect = _q

        ok, reason = v.can_generate_sessions(1)

        assert ok is False
        assert "6" in reason

    def test_individual_ranking_enough_players(self):
        """Happy path: IR format, IN_PROGRESS, 3 players → True."""
        t = _make_tournament(fmt="INDIVIDUAL_RANKING", status="IN_PROGRESS")
        t.campus_id = None  # skip pitch + instructor checks (not under test here)
        v, db = _make_validator(t)

        enroll_q = MagicMock()
        enroll_q.filter.return_value = enroll_q
        enroll_q.count.return_value = 3
        db.query.return_value = enroll_q

        ok, reason = v.can_generate_sessions(1, skip_instructor_check=True)

        assert ok is True
        assert "Ready" in reason

    def test_head_to_head_type_not_found_fallback(self):
        """Lines 70: TournamentType not found → fallback min_players=4."""
        from app.models.tournament_type import TournamentType

        t = _make_tournament(fmt="HEAD_TO_HEAD", type_id=99, status="IN_PROGRESS")
        t.campus_id = None  # skip pitch + instructor checks (not under test here)
        v, db = _make_validator(t)

        enroll_q = MagicMock()
        enroll_q.filter.return_value = enroll_q
        enroll_q.count.return_value = 5  # > 4 (fallback min)

        type_q = MagicMock()
        type_q.filter.return_value = type_q
        type_q.first.return_value = None  # not found → fallback min=4

        def _q(model):
            if model is TournamentType:
                return type_q
            return enroll_q

        db.query.side_effect = _q

        ok, reason = v.can_generate_sessions(1, skip_instructor_check=True)

        assert ok is True
