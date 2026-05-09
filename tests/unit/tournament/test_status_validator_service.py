"""
Tests for tournament/status_validator.py — missing branch coverage.

Missing targets:
  Lines 104-112: ENROLLMENT_CLOSED transition with tournament_type_id set
    → loads TournamentType from DB via _sa_instance_state.session
  Lines 132-140: IN_PROGRESS transition with tournament_type_id set
    → same pattern
"""
import pytest
from unittest.mock import MagicMock

from app.services.tournament.status_validator import validate_status_transition


# ──────────────────── helpers ────────────────────


def _active_enrollment():
    e = MagicMock()
    e.is_active = True
    return e


def _tournament(
    status="ENROLLMENT_OPEN",
    type_id=None,
    master_id=1,
    max_players=10,
    enrollments=None,
    sessions=None,
    campus_id=1,
    name="Test Tournament",
    start_date="2026-01-01",
    end_date="2026-12-31",
):
    t = MagicMock()
    t.tournament_status = status
    t.tournament_type_id = type_id
    t.master_instructor_id = master_id
    t.max_players = max_players
    t.campus_id = campus_id
    t.enrollments = enrollments or []
    t.sessions = sessions or [MagicMock()]
    t.name = name
    t.start_date = start_date
    t.end_date = end_date
    # Ensure parallel_fields is an integer so Guard 2 comparisons don't raise TypeError
    t.tournament_config_obj.parallel_fields = 1
    return t


def _sa_state(db):
    """Mock _sa_instance_state with a live DB session."""
    state = MagicMock()
    state.session = db
    return state


def _type_db(min_players, found=True):
    """DB mock: TournamentType with min_players + a CHECKED_IN FIELD slot + active pitch."""
    from app.models.tournament_type import TournamentType
    from app.models.tournament_instructor_slot import TournamentInstructorSlot
    from app.models.pitch import Pitch as _Pitch

    db = MagicMock()
    tt = MagicMock()
    tt.min_players = min_players

    field_slot = MagicMock()
    field_slot.status = "CHECKED_IN"
    field_slot.pitch_id = 1

    active_pitch = MagicMock()
    active_pitch.id = 1

    def _q(model):
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = 0
        if model is TournamentType:
            q.first.return_value = tt if found else None
        elif model is TournamentInstructorSlot:
            q.first.return_value = field_slot   # MASTER slot: non-None → CHECKED_IN
            q.all.return_value = [field_slot]   # FIELD slots: 1 CHECKED_IN slot
        elif model is _Pitch:
            q.first.return_value = active_pitch
        else:
            q.first.return_value = None
            q.all.return_value = []
        return q

    db.query.side_effect = _q
    return db


# ──────────────────── ENROLLMENT_CLOSED with tournament_type_id ────────────────────


class TestEnrollmentClosedWithTournamentType:
    """Lines 104-112: ENROLLMENT_OPEN→ENROLLMENT_CLOSED when type_id is set."""

    def test_loads_type_min_players_enough_players(self):
        """Type min_players=2; 2 active enrollments → valid."""
        db = _type_db(min_players=2)
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(
            status="ENROLLMENT_OPEN", type_id=42, enrollments=enrollments
        )
        t.__dict__["_sa_instance_state"] = _sa_state(db)

        is_valid, err = validate_status_transition(
            "ENROLLMENT_OPEN", "ENROLLMENT_CLOSED", t
        )

        assert is_valid is True
        assert err is None

    def test_loads_type_min_players_not_enough(self):
        """Type min_players=5; only 2 enrolled → invalid, message mentions 5."""
        db = _type_db(min_players=5)
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(
            status="ENROLLMENT_OPEN", type_id=42, enrollments=enrollments
        )
        t.__dict__["_sa_instance_state"] = _sa_state(db)

        is_valid, err = validate_status_transition(
            "ENROLLMENT_OPEN", "ENROLLMENT_CLOSED", t
        )

        assert is_valid is False
        assert "5" in err

    def test_tournament_type_not_found_fallback_2(self):
        """Type query returns None → fallback min_players=2 → 2 enrollments ok."""
        db = _type_db(min_players=2, found=False)
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(
            status="ENROLLMENT_OPEN", type_id=99, enrollments=enrollments
        )
        t.__dict__["_sa_instance_state"] = _sa_state(db)

        is_valid, err = validate_status_transition(
            "ENROLLMENT_OPEN", "ENROLLMENT_CLOSED", t
        )

        # fallback min=2, 2 players → passes
        assert is_valid is True

    def test_no_sa_session_uses_fallback_min_2(self):
        """_sa_instance_state.session is None → fallback min=2."""
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(
            status="ENROLLMENT_OPEN", type_id=42, enrollments=enrollments
        )
        state = MagicMock()
        state.session = None
        t.__dict__["_sa_instance_state"] = state

        is_valid, err = validate_status_transition(
            "ENROLLMENT_OPEN", "ENROLLMENT_CLOSED", t
        )

        assert is_valid is True

    def test_rollback_in_progress_to_enrollment_closed_skips_guard(self):
        """IN_PROGRESS→ENROLLMENT_CLOSED is admin rollback — player count not checked."""
        t = _tournament(status="IN_PROGRESS", type_id=42, enrollments=[])
        # No _sa_instance_state needed since guard is skipped for rollback path

        is_valid, err = validate_status_transition(
            "IN_PROGRESS", "ENROLLMENT_CLOSED", t
        )

        assert is_valid is True


# ──────────────────── IN_PROGRESS with tournament_type_id ────────────────────


class TestInProgressWithTournamentType:
    """CHECK_IN_OPEN→IN_PROGRESS when type_id is set (formerly ENROLLMENT_CLOSED→IN_PROGRESS)."""

    def test_loads_type_min_players_enough(self):
        """Type min_players=3; 4 enrolled → valid."""
        db = _type_db(min_players=3)
        enrollments = [_active_enrollment() for _ in range(4)]
        t = _tournament(
            status="CHECK_IN_OPEN", type_id=10, enrollments=enrollments
        )
        t.__dict__["_sa_instance_state"] = _sa_state(db)

        is_valid, err = validate_status_transition(
            "CHECK_IN_OPEN", "IN_PROGRESS", t
        )

        assert is_valid is True

    def test_loads_type_min_players_not_enough(self):
        """Type min_players=5; only 2 enrolled → invalid, message mentions 5."""
        db = _type_db(min_players=5)
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(
            status="CHECK_IN_OPEN", type_id=10, enrollments=enrollments
        )
        t.__dict__["_sa_instance_state"] = _sa_state(db)

        is_valid, err = validate_status_transition(
            "CHECK_IN_OPEN", "IN_PROGRESS", t
        )

        assert is_valid is False
        assert "5" in err

    def test_type_not_found_fallback_min_2(self):
        """Type query returns None → fallback min=2 → 2 players passes."""
        db = _type_db(min_players=2, found=False)
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(
            status="CHECK_IN_OPEN", type_id=5, enrollments=enrollments
        )
        t.__dict__["_sa_instance_state"] = _sa_state(db)

        is_valid, err = validate_status_transition(
            "CHECK_IN_OPEN", "IN_PROGRESS", t
        )

        assert is_valid is True

    def test_no_sa_session_fallback_min_2(self):
        """session=None in _sa_instance_state → fallback min=2."""
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(
            status="CHECK_IN_OPEN", type_id=5, enrollments=enrollments
        )
        state = MagicMock()
        state.session = None
        t.__dict__["_sa_instance_state"] = state

        is_valid, err = validate_status_transition(
            "CHECK_IN_OPEN", "IN_PROGRESS", t
        )

        assert is_valid is True

    def test_no_instructor_blocks_in_progress(self):
        """No master_instructor_id → fails before type lookup."""
        t = _tournament(
            status="CHECK_IN_OPEN", type_id=10, master_id=None, enrollments=[]
        )

        is_valid, err = validate_status_transition(
            "CHECK_IN_OPEN", "IN_PROGRESS", t
        )

        assert is_valid is False
        assert "instructor" in err.lower()


# ──────────────────── TournamentInstructorSlot fallback ──────────────────────


class TestMasterSlotFallback:
    """IN_PROGRESS guard: TournamentInstructorSlot fallback when master_instructor_id is None."""

    def _slot_db(self, has_slot=True):
        """DB mock: MASTER slot (optional) + CHECKED_IN FIELD slot + active pitch."""
        from app.models.tournament_instructor_slot import TournamentInstructorSlot
        from app.models.pitch import Pitch as _Pitch

        db = MagicMock()
        master_slot = MagicMock() if has_slot else None

        field_slot = MagicMock()
        field_slot.status = "CHECKED_IN"
        field_slot.pitch_id = 1

        active_pitch = MagicMock()
        active_pitch.id = 1

        def _q(model):
            q = MagicMock()
            q.filter.return_value = q
            if model is TournamentInstructorSlot:
                q.first.return_value = master_slot
                q.all.return_value = [field_slot] if has_slot else []
            elif model is _Pitch:
                q.first.return_value = active_pitch if has_slot else None
            else:
                q.first.return_value = None
                q.all.return_value = []
            return q

        db.query.side_effect = _q
        return db

    def test_master_slot_satisfies_in_progress_guard(self):
        """ISF-01: no master_instructor_id but MASTER slot (non-ABSENT) → allowed."""
        db = self._slot_db(has_slot=True)
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(
            status="CHECK_IN_OPEN", master_id=None, type_id=None, enrollments=enrollments
        )
        t.__dict__["_sa_instance_state"] = _sa_state(db)

        is_valid, err = validate_status_transition("CHECK_IN_OPEN", "IN_PROGRESS", t)

        assert is_valid is True
        assert err is None

    def test_absent_master_slot_blocks_in_progress(self):
        """ISF-02: no master_instructor_id, no slot found (ABSENT filtered out) → blocked."""
        db = self._slot_db(has_slot=False)
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(
            status="CHECK_IN_OPEN", master_id=None, type_id=None, enrollments=enrollments
        )
        t.__dict__["_sa_instance_state"] = _sa_state(db)

        is_valid, err = validate_status_transition("CHECK_IN_OPEN", "IN_PROGRESS", t)

        assert is_valid is False
        assert "instructor" in err.lower()

    def test_legacy_master_instructor_id_still_works(self):
        """ISF-03: master_instructor_id set → no slot query needed, passes."""
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(
            status="CHECK_IN_OPEN", master_id=99, type_id=None, enrollments=enrollments
        )

        is_valid, err = validate_status_transition("CHECK_IN_OPEN", "IN_PROGRESS", t)

        assert is_valid is True
        assert err is None


# ──────────────────── DRAFT → ENROLLMENT_OPEN new guards ────────────────────


class TestDraftToEnrollmentOpenValidation:
    """New validation guards for DRAFT → ENROLLMENT_OPEN (name, dates, H2H type)."""

    def _valid_draft(self, *, campus_id=1, name="Spring Cup", type_id=1, fmt="HEAD_TO_HEAD",
                     start_offset=7, end_offset=14):
        """Build a mock DRAFT tournament satisfying all ENROLLMENT_OPEN guards."""
        from datetime import date, timedelta
        t = _tournament(
            status="DRAFT",
            campus_id=campus_id,
            name=name,
            start_date=date.today() + timedelta(days=start_offset),
            end_date=date.today() + timedelta(days=end_offset),
            type_id=type_id,
        )
        t.format = fmt
        return t

    # ── name ─────────────────────────────────────────────────────────────────

    def test_blank_name_rejected(self):
        """DOE-01: Whitespace-only name → blocked."""
        t = self._valid_draft(name="   ")
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is False
        assert "name" in err.lower()

    def test_empty_name_rejected(self):
        """DOE-02: Empty string name → blocked."""
        t = self._valid_draft(name="")
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is False
        assert "name" in err.lower()

    def test_valid_name_passes(self):
        """DOE-03: Non-empty name → name check clears."""
        t = self._valid_draft(name="Spring Cup 2026")
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is True
        assert err is None

    # ── start_date ───────────────────────────────────────────────────────────

    def test_past_start_date_rejected(self):
        """DOE-04: start_date yesterday → blocked."""
        t = self._valid_draft(start_offset=-1, end_offset=14)
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is False
        assert "start" in err.lower() or "past" in err.lower() or "date" in err.lower()

    def test_future_start_date_passes(self):
        """DOE-05: start_date tomorrow → passes."""
        t = self._valid_draft(start_offset=1, end_offset=14)
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is True

    # ── end_date ─────────────────────────────────────────────────────────────

    def test_end_before_start_rejected(self):
        """DOE-06: end_date < start_date → blocked."""
        t = self._valid_draft(start_offset=14, end_offset=7)
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is False
        assert "end" in err.lower() or "date" in err.lower()

    def test_same_day_start_end_passes(self):
        """DOE-07: end_date == start_date (single-day event) → passes."""
        t = self._valid_draft(start_offset=7, end_offset=7)
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is True

    # ── H2H tournament_type_id ───────────────────────────────────────────────

    def test_h2h_without_type_id_rejected(self):
        """DOE-08: HEAD_TO_HEAD with tournament_type_id=None → blocked."""
        t = self._valid_draft(type_id=None, fmt="HEAD_TO_HEAD")
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is False
        assert "type" in err.lower()

    def test_h2h_with_type_id_passes(self):
        """DOE-09: HEAD_TO_HEAD with tournament_type_id set → passes."""
        t = self._valid_draft(type_id=1, fmt="HEAD_TO_HEAD")
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is True

    def test_individual_ranking_no_type_id_passes(self):
        """DOE-10: INDIVIDUAL_RANKING without type_id → allowed."""
        t = self._valid_draft(type_id=None, fmt="INDIVIDUAL_RANKING")
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is True

    # ── original campus guard preserved ─────────────────────────────────────

    def test_campus_guard_still_blocks(self):
        """DOE-11: campus_id=None still rejected (original guard preserved)."""
        t = self._valid_draft(campus_id=None)
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is False
        assert "campus" in err.lower()
