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
    return t


def _sa_state(db):
    """Mock _sa_instance_state with a live DB session."""
    state = MagicMock()
    state.session = db
    return state


def _type_db(min_players, found=True):
    """DB mock that returns a TournamentType with given min_players."""
    db = MagicMock()
    tt = MagicMock()
    tt.min_players = min_players
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = tt if found else None
    db.query.return_value = q
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
        """DB mock that returns/doesn't return a TournamentInstructorSlot for MASTER query."""
        db = MagicMock()
        slot_mock = MagicMock() if has_slot else None
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = slot_mock
        db.query.return_value = q
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


# ──────────────────── PROMOTION_EVENT status machine ────────────────────────


def _audience_db(count: int) -> MagicMock:
    """DB mock whose query().filter(...).count() returns `count`."""
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.count.return_value = count
    db.query.return_value = q
    return db


def _promo_tournament(
    status: str = "DRAFT",
    organizer_sponsor_id: int = 10,
    organizer_campaign_id: int = 20,
    audience_count: int = 3,
    enrollments=None,
    master_id: int = 1,
):
    """Build a PROMOTION_EVENT mock tournament with a wired DB session."""
    from app.models.semester import SemesterCategory

    t = MagicMock()
    t.tournament_status = status
    t.semester_category = SemesterCategory.PROMOTION_EVENT
    t.organizer_sponsor_id = organizer_sponsor_id
    t.organizer_campaign_id = organizer_campaign_id
    t.master_instructor_id = master_id
    t.tournament_type_id = None
    t.campus_id = 1
    t.name = "Promo Test Event"
    t.start_date = "2026-09-01"
    t.end_date = "2026-09-02"
    t.enrollments = enrollments or []
    t.sessions = [MagicMock()]
    t.format = "INDIVIDUAL_RANKING"
    t.participant_type = "INDIVIDUAL"

    db = _audience_db(audience_count)
    state = MagicMock()
    state.session = db
    t.__dict__["_sa_instance_state"] = state
    return t


def _non_promo_tournament(status: str = "DRAFT"):
    """Build a non-PROMOTION_EVENT (MINI_SEASON) mock tournament."""
    from app.models.semester import SemesterCategory

    t = _tournament(status=status)
    t.semester_category = SemesterCategory.MINI_SEASON
    return t


class TestPromotionEventDraftToEnrollmentClosed:
    """PROMO-SM-01..04: DRAFT → ENROLLMENT_CLOSED guard for PROMOTION_EVENT."""

    def test_promo_sm_01_passes_with_campaign_and_active_entries(self):
        """PROMO-SM-01: PROMOTION_EVENT + campaign linked + ACTIVE entries → valid."""
        t = _promo_tournament(audience_count=3)
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_CLOSED", t)
        assert is_valid is True
        assert err is None

    def test_promo_sm_02_blocked_no_campaign_linkage(self):
        """PROMO-SM-02: organizer_campaign_id=None → rejected, mentions campaign."""
        t = _promo_tournament(organizer_campaign_id=None)
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_CLOSED", t)
        assert is_valid is False
        assert "campaign" in err.lower()

    def test_promo_sm_02b_blocked_no_sponsor_linkage(self):
        """PROMO-SM-02b: organizer_sponsor_id=None → rejected, mentions sponsor/campaign."""
        t = _promo_tournament(organizer_sponsor_id=None)
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_CLOSED", t)
        assert is_valid is False
        assert "sponsor" in err.lower() or "campaign" in err.lower()

    def test_promo_sm_03_blocked_all_deleted_entries(self):
        """PROMO-SM-03: campaign has 0 active+consented entries → rejected."""
        t = _promo_tournament(audience_count=0)
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_CLOSED", t)
        assert is_valid is False
        assert "active" in err.lower() or "entries" in err.lower()

    def test_promo_sm_04_blocked_for_non_promotion_event(self):
        """PROMO-SM-04: MINI_SEASON DRAFT → ENROLLMENT_CLOSED → rejected (must go via ENROLLMENT_OPEN)."""
        t = _non_promo_tournament(status="DRAFT")
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_CLOSED", t)
        assert is_valid is False
        assert "PROMOTION_EVENT" in err


class TestNonPromotionEventRegressions:
    """PROMO-SM-05..07: existing non-PROMOTION_EVENT paths unchanged."""

    def _valid_non_promo_draft(self):
        from app.models.semester import SemesterCategory
        from datetime import date
        t = MagicMock()
        t.tournament_status = "DRAFT"
        t.semester_category = SemesterCategory.MINI_SEASON
        t.tournament_type_id = None
        t.master_instructor_id = 1
        t.campus_id = 1
        t.name = "Mini Season"
        t.start_date = date(2027, 1, 1)
        t.end_date = date(2027, 1, 2)
        t.format = "INDIVIDUAL_RANKING"
        t.enrollments = []
        t.sessions = [MagicMock()]
        return t

    def test_promo_sm_05_non_promo_draft_to_enrollment_open(self):
        """PROMO-SM-05: MINI_SEASON DRAFT → ENROLLMENT_OPEN still works (regression)."""
        t = self._valid_non_promo_draft()
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is True

    def test_promo_sm_06_non_promo_enrollment_open_to_closed_with_players(self):
        """PROMO-SM-06: MINI_SEASON ENROLLMENT_OPEN → ENROLLMENT_CLOSED with ≥2 players (regression)."""
        from app.models.semester import SemesterCategory
        enrollments = [_active_enrollment(), _active_enrollment(), _active_enrollment()]
        t = _tournament(status="ENROLLMENT_OPEN", enrollments=enrollments)
        t.semester_category = SemesterCategory.MINI_SEASON
        is_valid, err = validate_status_transition("ENROLLMENT_OPEN", "ENROLLMENT_CLOSED", t)
        assert is_valid is True

    def test_promo_sm_07_non_promo_enrollment_open_to_closed_no_players(self):
        """PROMO-SM-07: MINI_SEASON ENROLLMENT_OPEN → ENROLLMENT_CLOSED with 0 players → rejected (regression)."""
        from app.models.semester import SemesterCategory
        t = _tournament(status="ENROLLMENT_OPEN", enrollments=[])
        t.semester_category = SemesterCategory.MINI_SEASON
        is_valid, err = validate_status_transition("ENROLLMENT_OPEN", "ENROLLMENT_CLOSED", t)
        assert is_valid is False
        assert "participants" in err.lower() or "players" in err.lower()


# ──────────────────── PROMOTION_EVENT IN_PROGRESS invariant ──────────────────


class TestPromotionEventInProgressGuard:
    """PROMO-SM-08..10: IN_PROGRESS guard uses SemesterEnrollment for PROMOTION_EVENT.

    Core invariant: campaign audience (SponsorAudienceEntry) drives ONLY the
    ENROLLMENT_CLOSED "lock audience" guard.  IN_PROGRESS requires actual
    SemesterEnrollment rows — created by bulk_enroll_from_campaign() or manual
    enroll.  A tournament with audience entries but no enrollments must be blocked.
    """

    def test_promo_sm_08_blocked_audience_present_no_enrollment(self):
        """PROMO-SM-08: audience_count=3 but 0 SemesterEnrollments → IN_PROGRESS blocked."""
        # audience_count=3 wires the mock DB to return 3 for any .count() call;
        # enrollments=[] means no SemesterEnrollment rows → the player count is 0.
        t = _promo_tournament(audience_count=3, enrollments=[])
        is_valid, err = validate_status_transition("CHECK_IN_OPEN", "IN_PROGRESS", t)
        assert is_valid is False
        assert "participants" in err.lower() or "players" in err.lower()

    def test_promo_sm_09_allowed_after_bulk_enroll(self):
        """PROMO-SM-09: 2 active SemesterEnrollments → IN_PROGRESS allowed."""
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _promo_tournament(audience_count=2, enrollments=enrollments)
        is_valid, err = validate_status_transition("CHECK_IN_OPEN", "IN_PROGRESS", t)
        assert is_valid is True
        assert err is None

    def test_promo_sm_10_non_promo_in_progress_unchanged(self):
        """PROMO-SM-10: non-PROMOTION_EVENT CHECK_IN_OPEN → IN_PROGRESS still uses SemesterEnrollment (regression)."""
        from app.models.semester import SemesterCategory
        enrollments = [_active_enrollment(), _active_enrollment()]
        t = _tournament(status="CHECK_IN_OPEN", enrollments=enrollments, master_id=1)
        t.semester_category = SemesterCategory.MINI_SEASON
        is_valid, err = validate_status_transition("CHECK_IN_OPEN", "IN_PROGRESS", t)
        assert is_valid is True
        assert err is None


# ──────────────────── PROMOTION_EVENT ENROLLMENT_OPEN prevention ─────────────


class TestPromotionEventBlockedFromEnrollmentOpen:
    """PROMO-SM-11: DRAFT → ENROLLMENT_OPEN is blocked for PROMOTION_EVENT.

    Prevention: PROMOTION_EVENT uses the DRAFT → ENROLLMENT_CLOSED fast path.
    Allowing ENROLLMENT_OPEN would create a dead-end (no recovery UI).
    Non-PROMOTION_EVENT DRAFT → ENROLLMENT_OPEN must remain unaffected.
    """

    def test_promo_sm_11_draft_to_enrollment_open_blocked(self):
        """PROMO-SM-11: PROMOTION_EVENT DRAFT → ENROLLMENT_OPEN → rejected."""
        t = _promo_tournament(status="DRAFT")
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is False
        assert "ENROLLMENT_OPEN" in err or "Lock Audience" in err

    def test_promo_sm_11b_non_promo_draft_to_enrollment_open_unchanged(self):
        """PROMO-SM-11b: non-PROMOTION_EVENT DRAFT → ENROLLMENT_OPEN still allowed (regression)."""
        from app.models.semester import SemesterCategory
        from datetime import date
        t = MagicMock()
        t.tournament_status = "DRAFT"
        t.semester_category = SemesterCategory.MINI_SEASON
        t.tournament_type_id = None
        t.master_instructor_id = 1
        t.campus_id = 1
        t.name = "Mini Season"
        t.start_date = date(2027, 1, 1)
        t.end_date = date(2027, 1, 2)
        t.format = "INDIVIDUAL_RANKING"
        is_valid, err = validate_status_transition("DRAFT", "ENROLLMENT_OPEN", t)
        assert is_valid is True
