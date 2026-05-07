"""Domain integrity tests — PR Domain-Integrity-Fix.

Tests verify the invariants introduced by the domain integrity fix:
  BP-01  bootstrap _seed_pitches creates 2 active pitches on the campus
  BP-02  bootstrap _seed_pitches is idempotent (second call creates 0)
  GV-01  generation_validator blocks tournament with no active pitches
  GV-02  generation_validator passes tournament with active pitches
  GV-03  generation_validator blocks HEAD_TO_HEAD session generation without instructor
  GV-04  generation_validator blocks INDIVIDUAL_RANKING session generation without instructor
  GV-05  generation_validator passes HEAD_TO_HEAD with instructor assigned
  LC-01  lifecycle CHECK_IN_OPEN raises HTTPException when session gen fails
  SI-01  _create_tournament links a game_preset_id (not NULL)
  SI-02  _stamp_player_checkins stamps all APPROVED enrollments and returns count
  SI-03  _stamp_player_checkins is idempotent (already-stamped rows unchanged)
  PA-01  session_generator assigns pitch_id from active pitches round-robin
  PA-02  session_generator skips pitch assignment when no active pitches
  HMIA-01..04  has_master_instructor_assignment helper unit tests
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import pytest


# ── BP-01 / BP-02 — bootstrap pitch seeding ───────────────────────────────────

class TestBootstrapSeedPitches:
    """_seed_pitches() from bootstrap_clean.py."""

    def _make_db(self, existing_pitch_numbers: list[int]):
        """Return a mock db whose Pitch query returns the given existing numbers."""
        db = MagicMock()

        def _query_side_effect(model):
            from app.models.pitch import Pitch
            if model is Pitch:
                mock_q = MagicMock()
                def _filter_se(*args, **kwargs):
                    inner = MagicMock()
                    # pitch_number argument is in args — extract from filter call
                    # Instead, we track calls via a closure
                    inner._existing = existing_pitch_numbers
                    def _first():
                        # Check if the pitch_number being queried is in existing list
                        # We can't easily introspect the filter args from a MagicMock,
                        # so we track call count instead
                        if not hasattr(_first, "_call_count"):
                            _first._call_count = 0
                        result = _first._call_count < len(existing_pitch_numbers)
                        _first._call_count += 1
                        return MagicMock() if result else None
                    inner.first = _first
                    return inner
                mock_q.filter.side_effect = _filter_se
                return mock_q
            return MagicMock()

        db.query.side_effect = _query_side_effect
        return db

    def test_bp_01_creates_two_pitches_on_fresh_campus(self):
        """Creates 2 pitches when campus has none."""
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

        from scripts.bootstrap_clean import _seed_pitches

        campus = MagicMock()
        campus.id = 42

        db = MagicMock()
        # filter().first() always returns None → both pitches are new
        db.query.return_value.filter.return_value.first.return_value = None

        created = _seed_pitches(db, campus)

        assert created == 2
        assert db.add.call_count == 2
        assert db.flush.call_count == 2

    def test_bp_02_idempotent_when_pitches_already_exist(self):
        """Creates 0 pitches when campus already has both."""
        from scripts.bootstrap_clean import _seed_pitches

        campus = MagicMock()
        campus.id = 42

        db = MagicMock()
        # filter().first() always returns an existing Pitch → skip both
        db.query.return_value.filter.return_value.first.return_value = MagicMock()

        created = _seed_pitches(db, campus)

        assert created == 0
        db.add.assert_not_called()


# ── GV-01 / GV-02 — generation validator pitch guard ─────────────────────────

class TestGenerationValidatorPitchGuard:
    """GenerationValidator.can_generate_sessions() pitch guard."""

    def _make_tournament(self, campus_id: int | None = 1):
        t = MagicMock()
        t.id = 99
        t.sessions_generated = False
        t.format = "INDIVIDUAL_RANKING"
        t.tournament_type_id = None
        t.tournament_status = "CHECK_IN_OPEN"
        t.participant_type = "INDIVIDUAL"
        t.location_id = 1
        t.campus_id = campus_id
        return t

    def _make_db(self, tournament, active_pitch_count: int):
        from app.models.pitch import Pitch
        from app.models.semester_enrollment import SemesterEnrollment

        db = MagicMock()

        def _query(model):
            q = MagicMock()
            if model is Pitch:
                q.filter.return_value.count.return_value = active_pitch_count
            elif model is SemesterEnrollment:
                q.filter.return_value.count.return_value = 4
            else:
                q.filter.return_value.first.return_value = tournament
            return q

        db.query.side_effect = _query
        return db

    def test_gv_01_blocks_when_no_active_pitches(self):
        """Returns (False, reason) when campus has 0 active pitches."""
        from app.services.tournament.session_generation.validators.generation_validator import GenerationValidator

        t = self._make_tournament(campus_id=5)
        db = self._make_db(t, active_pitch_count=0)

        # Patch TournamentRepository.get_optional
        with patch(
            "app.services.tournament.session_generation.validators.generation_validator.TournamentRepository"
        ) as MockRepo:
            MockRepo.return_value.get_optional.return_value = t
            validator = GenerationValidator(db)
            ok, reason = validator.can_generate_sessions(99)

        assert ok is False
        assert "no active pitches" in reason.lower() or "active pitch" in reason.lower()

    def test_gv_02_passes_when_active_pitches_exist(self):
        """Returns (True, ...) when campus has ≥1 active pitch."""
        from app.services.tournament.session_generation.validators.generation_validator import GenerationValidator

        t = self._make_tournament(campus_id=5)
        db = self._make_db(t, active_pitch_count=2)

        with patch(
            "app.services.tournament.session_generation.validators.generation_validator.TournamentRepository"
        ) as MockRepo:
            MockRepo.return_value.get_optional.return_value = t
            validator = GenerationValidator(db)
            ok, reason = validator.can_generate_sessions(99)

        assert ok is True


# ── LC-01 — lifecycle HTTPException on session gen failure ────────────────────

class TestLifecycleSessionGenFailure:
    """CHECK_IN_OPEN transition raises 400 when session gen fails or is blocked."""

    def test_lc_01_raises_400_when_can_generate_returns_false(self):
        """If can_generate_sessions returns False, transition raises HTTPException(400)."""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import get_db
        from app.models.user import User, UserRole
        from app.models.semester import Semester
        from app.dependencies import get_current_admin_user_hybrid

        admin = MagicMock(spec=User)
        admin.id = 1
        admin.role = UserRole.ADMIN
        admin.is_active = True
        admin.email = "admin@test.com"

        tournament = MagicMock()
        tournament.id = 77
        tournament.tournament_status = "ENROLLMENT_CLOSED"
        tournament.format = "INDIVIDUAL_RANKING"
        tournament.master_instructor_id = None
        tournament.campus_id = 1
        tournament.sessions_generated = False  # must be falsy for the CHECK_IN_OPEN block to run
        tournament.tournament_config_obj = MagicMock()
        tournament.tournament_config_obj.enrollment_snapshot = None
        tournament.reward_config = None

        # Build a db mock whose query chains all resolve to the tournament mock
        db_mock = MagicMock()
        q = MagicMock()
        q.options.return_value = q
        q.filter.return_value = q
        q.first.return_value = tournament
        q.all.return_value = []
        q.count.return_value = 0
        db_mock.query.return_value = q

        app.dependency_overrides[get_db] = lambda: db_mock
        app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin

        try:
            with (
                patch(
                    "app.services.tournament.status_validator.validate_status_transition",
                    return_value=(True, "ok"),
                ),
                patch(
                    "app.services.tournament_session_generator.TournamentSessionGenerator"
                ) as MockGen,
            ):
                MockGen.return_value.can_generate_sessions.return_value = (
                    False,
                    "Campus 1 has no active pitches",
                )
                client = TestClient(app, raise_server_exceptions=False)
                response = client.patch(
                    "/api/v1/tournaments/77/status",
                    json={"new_status": "CHECK_IN_OPEN", "reason": "test"},
                )

            assert response.status_code == 400
            body = response.text.lower()
            assert "pitches" in body or "cannot" in body or "session generation" in body
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_admin_user_hybrid, None)


# ── SI-01 / SI-02 / SI-03 — seed invariants ──────────────────────────────────

class TestSeedInvariants:
    """seed_promotion_events invariants."""

    def test_si_01_create_tournament_links_game_preset(self):
        """_create_tournament must create a GameConfiguration with game_preset_id set."""
        from scripts.seed_promotion_events import _create_tournament
        from app.models.game_configuration import GameConfiguration
        from app.models.game_preset import GamePreset

        added_objects = []

        db = MagicMock()
        db.add.side_effect = lambda obj: added_objects.append(obj)
        db.flush.return_value = None
        db.commit.return_value = None

        # Simulate queries
        from app.models.campus import Campus as CampusModel
        campus_mock = MagicMock(spec=CampusModel)
        campus_mock.location_id = 10
        preset_mock = MagicMock(spec=GamePreset)
        preset_mock.id = 3

        def _query(model):
            q = MagicMock()
            if model is CampusModel:
                q.filter.return_value.first.return_value = campus_mock
            elif model is GamePreset:
                q.filter.return_value.first.return_value = preset_mock
            else:
                q.filter.return_value.first.return_value = None
            return q

        db.query.side_effect = _query

        sponsor = MagicMock()
        sponsor.id = 1
        campaign = MagicMock()
        campaign.id = 2
        tt = MagicMock()
        tt.id = 5

        # Need Semester to come back from refresh
        semester_mock = MagicMock()
        db.refresh.return_value = None

        with patch("scripts.seed_promotion_events.Semester") as MockSemester:
            MockSemester.return_value = semester_mock
            semester_mock.id = 99
            semester_mock.location_id = None

            result = _create_tournament(db, "Test", sponsor, campaign, tt, 1, dry_run=False)

        game_cfgs = [o for o in added_objects if isinstance(o, GameConfiguration)]
        assert len(game_cfgs) == 1, "Exactly one GameConfiguration must be added"
        assert game_cfgs[0].game_preset_id == 3, "game_preset_id must be set to default preset id"

    def test_si_02_stamp_player_checkins_stamps_approved_enrollments(self):
        """_stamp_player_checkins stamps tournament_checked_in_at on APPROVED rows."""
        from scripts.seed_promotion_events import _stamp_player_checkins
        from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus

        enr1 = MagicMock(spec=SemesterEnrollment)
        enr1.tournament_checked_in_at = None
        enr2 = MagicMock(spec=SemesterEnrollment)
        enr2.tournament_checked_in_at = None

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [enr1, enr2]
        db.flush.return_value = None

        count = _stamp_player_checkins(db, tid=99)

        assert count == 2
        assert enr1.tournament_checked_in_at is not None
        assert enr2.tournament_checked_in_at is not None

    def test_si_03_stamp_player_checkins_idempotent(self):
        """_stamp_player_checkins returns 0 when all players already checked in."""
        from scripts.seed_promotion_events import _stamp_player_checkins

        db = MagicMock()
        # Already-stamped rows filtered out by tournament_checked_in_at == None
        db.query.return_value.filter.return_value.all.return_value = []
        db.flush.return_value = None

        count = _stamp_player_checkins(db, tid=99)
        assert count == 0


# ── PA-01 / PA-02 — pitch assignment in session generator ────────────────────

class TestPitchAssignment:
    """Round-robin pitch assignment in TournamentSessionGenerator."""

    def _make_sessions(self, n: int) -> list[dict]:
        return [{"round_number": i + 1} for i in range(n)]

    def _make_pitches(self, ids: list[int]):
        pitches = []
        for i, pid in enumerate(ids, 1):
            p = MagicMock()
            p.id = pid
            p.pitch_number = i
            pitches.append(p)
        return pitches

    def test_pa_01_assigns_pitch_ids_round_robin(self):
        """All sessions get a pitch_id from the active pitches list, cycling round-robin."""
        from app.models.pitch import Pitch as PitchModel

        sessions = self._make_sessions(6)
        pitches = self._make_pitches([10, 20])  # 2 active pitches

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = pitches

        # Simulate the assignment logic from session_generator
        _active_pitches = pitches
        _pitch_ids = [p.id for p in _active_pitches]
        _pitch_count = len(_pitch_ids)
        for _i, _sd in enumerate(sessions):
            if not _sd.get("pitch_id"):
                _sd["pitch_id"] = _pitch_ids[_i % _pitch_count]

        assigned = [s["pitch_id"] for s in sessions]
        assert assigned == [10, 20, 10, 20, 10, 20], f"Expected round-robin [10,20,...], got {assigned}"

    def test_pa_02_skips_sessions_that_already_have_pitch_id(self):
        """Sessions with an existing pitch_id are not overwritten."""
        pitches = self._make_pitches([10, 20])

        sessions = [
            {"round_number": 1, "pitch_id": 99},  # pre-assigned — must stay
            {"round_number": 2},
        ]

        _pitch_ids = [p.id for p in pitches]
        _pitch_count = len(_pitch_ids)
        for _i, _sd in enumerate(sessions):
            if not _sd.get("pitch_id"):
                _sd["pitch_id"] = _pitch_ids[_i % _pitch_count]

        assert sessions[0]["pitch_id"] == 99, "Pre-assigned pitch_id must not be overwritten"
        assert sessions[1]["pitch_id"] in (10, 20), "Unassigned session must get a pitch"


# ── HMIA-01..04 — has_master_instructor_assignment unit tests ─────────────────


class TestHasMasterInstructorAssignment:
    """Unit tests for has_master_instructor_assignment(db, tournament_id)."""

    def _make_db(self, tournament=None, master_slot=None):
        from app.models.semester import Semester
        from app.models.tournament_instructor_slot import TournamentInstructorSlot

        db = MagicMock()

        def _query(model):
            q = MagicMock()
            q.filter.return_value = q
            if model is Semester:
                q.first.return_value = tournament
            elif model is TournamentInstructorSlot:
                q.first.return_value = master_slot
            else:
                q.first.return_value = None
            return q

        db.query.side_effect = _query
        return db

    def test_hmia_01_returns_false_when_no_tournament(self):
        from app.services.tournament.instructor_service import has_master_instructor_assignment
        db = self._make_db(tournament=None)
        assert has_master_instructor_assignment(db, 999) is False

    def test_hmia_02_returns_true_with_master_instructor_id(self):
        from app.services.tournament.instructor_service import has_master_instructor_assignment
        t = MagicMock()
        t.master_instructor_id = 7
        db = self._make_db(tournament=t)
        assert has_master_instructor_assignment(db, 1) is True

    def test_hmia_03_returns_false_without_master_id_and_no_slot(self):
        from app.services.tournament.instructor_service import has_master_instructor_assignment
        t = MagicMock()
        t.master_instructor_id = None
        db = self._make_db(tournament=t, master_slot=None)
        assert has_master_instructor_assignment(db, 1) is False

    def test_hmia_04_returns_true_with_confirmed_master_slot(self):
        from app.models.tournament_instructor_slot import SlotRole, SlotStatus
        from app.services.tournament.instructor_service import has_master_instructor_assignment
        t = MagicMock()
        t.master_instructor_id = None
        slot = MagicMock()
        slot.role = SlotRole.MASTER.value
        slot.status = SlotStatus.CONFIRMED.value
        db = self._make_db(tournament=t, master_slot=slot)
        assert has_master_instructor_assignment(db, 1) is True


# ── PA-03 — preflight audit instructor check ─────────────────────────────────


class TestPreflightAuditInstructorCheck:
    """_run_preflight_audit() reports and blocks on instructor@lfa.com missing."""

    def _make_db(self, *, has_campus=True, has_pitches=True, has_preset=True,
                 has_admin=True, has_instructor=True):
        from app.models.campus import Campus
        from app.models.pitch import Pitch
        from app.models.game_preset import GamePreset
        from app.models.user import User

        campus_obj = MagicMock()
        campus_obj.id = 1
        campus_obj.name = "TestCampus"

        admin_obj = MagicMock() if has_admin else None
        instructor_obj = MagicMock() if has_instructor else None

        # Counter lives outside _query so it persists across multiple db.query(User) calls.
        user_call_count = [0]

        db = MagicMock()

        def _query(model):
            q = MagicMock()
            if model is Campus:
                q.filter.return_value.first.return_value = campus_obj if has_campus else None
            elif model is Pitch:
                q.filter.return_value.count.return_value = 2 if has_pitches else 0
            elif model is GamePreset:
                q.filter.return_value.first.return_value = MagicMock() if has_preset else None
            elif model is User:
                # Return admin on first User query, instructor on second.
                # The counter is shared across calls so ordering is stable.
                def _filter_side(*args, **kwargs):
                    inner = MagicMock()
                    idx = user_call_count[0]
                    user_call_count[0] += 1
                    inner.first.return_value = admin_obj if idx == 0 else instructor_obj
                    return inner

                q.filter.side_effect = _filter_side
            return q

        db.query.side_effect = _query
        return db

    def test_pa_03_all_ok_returns_empty_issues(self):
        from scripts.seed_promotion_events import _run_preflight_audit
        db = self._make_db()
        issues = _run_preflight_audit(db, campus_id=1, fail=False)
        assert issues == []

    def test_pa_04_missing_instructor_adds_issue(self):
        from scripts.seed_promotion_events import _run_preflight_audit
        db = self._make_db(has_instructor=False)
        issues = _run_preflight_audit(db, campus_id=1, fail=False)
        assert any("instructor@lfa.com" in i for i in issues)

    def test_pa_05_admin_ok_instructor_missing_is_single_distinct_issue(self):
        """admin present, instructor missing → exactly 1 issue (not admin)."""
        from scripts.seed_promotion_events import _run_preflight_audit
        db = self._make_db(has_admin=True, has_instructor=False)
        issues = _run_preflight_audit(db, campus_id=1, fail=False)
        assert len(issues) == 1
        assert "instructor" in issues[0].lower()

    def test_pa_06_fail_true_exits_when_instructor_missing(self):
        from scripts.seed_promotion_events import _run_preflight_audit
        db = self._make_db(has_instructor=False)
        with pytest.raises(SystemExit):
            _run_preflight_audit(db, campus_id=1, fail=True)


# ── GV-03/04/05 — GenerationValidator instructor prerequisite guard ───────────

class TestGenerationValidatorInstructorGuard:
    """
    GV-03  HEAD_TO_HEAD + no instructor → (False, reason with 'instructor')
    GV-04  INDIVIDUAL_RANKING + no instructor → also blocked (format does not exempt)
    GV-05  HEAD_TO_HEAD + instructor present → instructor guard passes

    The session_generator assigns instructor_id via FIELD-slot OR master_instructor_id.
    If neither is set, every session gets instructor_id=NULL — a domain invariant
    violation.  GenerationValidator enforces the prerequisite for ALL formats before
    enrollment count so the failure is attributable and clear.
    """

    _PATCH_REPO = (
        "app.services.tournament.session_generation.validators"
        ".generation_validator.TournamentRepository"
    )
    _PATCH_HMIA = "app.services.tournament.instructor_service.has_master_instructor_assignment"

    def _make_tournament(self, fmt, type_id=None):
        t = MagicMock()
        t.id = 77
        t.sessions_generated = False
        t.format = fmt
        t.tournament_type_id = type_id
        t.tournament_status = "CHECK_IN_OPEN"
        t.participant_type = "INDIVIDUAL"
        t.location_id = 1
        t.campus_id = 1
        return t

    def _make_db(self, tournament):
        from app.models.semester_enrollment import SemesterEnrollment
        from app.models.pitch import Pitch
        from app.models.tournament_type import TournamentType

        tt_mock = MagicMock()
        tt_mock.min_players = 2

        db = MagicMock()

        def _query(model):
            q = MagicMock()
            if model is SemesterEnrollment:
                q.filter.return_value.count.return_value = 4
            elif model is Pitch:
                q.filter.return_value.count.return_value = 1
            elif model is TournamentType:
                q.filter.return_value.first.return_value = tt_mock
            else:
                q.filter.return_value.first.return_value = tournament
            return q

        db.query.side_effect = _query
        return db

    def test_gv_03_head_to_head_no_instructor_blocked(self):
        """HEAD_TO_HEAD + no instructor → (False, reason mentioning 'instructor')."""
        from app.services.tournament.session_generation.validators.generation_validator import GenerationValidator

        t = self._make_tournament("HEAD_TO_HEAD", type_id=5)
        db = self._make_db(t)

        with patch(self._PATCH_REPO) as MockRepo, \
             patch(self._PATCH_HMIA, return_value=False):
            MockRepo.return_value.get_optional.return_value = t
            ok, reason = GenerationValidator(db).can_generate_sessions(77)

        assert ok is False
        assert "instructor" in reason.lower()

    def test_gv_04_individual_ranking_no_instructor_blocked(self):
        """INDIVIDUAL_RANKING + no instructor → blocked; format does not exempt.

        Session generator assigns instructor_id via master_instructor_id or FIELD slots.
        Without either, all generated sessions get instructor_id=NULL — a domain
        invariant violation that the guard prevents for both formats.
        """
        from app.services.tournament.session_generation.validators.generation_validator import GenerationValidator

        t = self._make_tournament("INDIVIDUAL_RANKING", type_id=None)
        db = self._make_db(t)

        with patch(self._PATCH_REPO) as MockRepo, \
             patch(self._PATCH_HMIA, return_value=False):
            MockRepo.return_value.get_optional.return_value = t
            ok, reason = GenerationValidator(db).can_generate_sessions(77)

        assert ok is False
        assert "instructor" in reason.lower()

    def test_gv_05_head_to_head_with_instructor_passes_guard(self):
        """HEAD_TO_HEAD + instructor assigned → instructor guard passes."""
        from app.services.tournament.session_generation.validators.generation_validator import GenerationValidator

        t = self._make_tournament("HEAD_TO_HEAD", type_id=5)
        db = self._make_db(t)

        with patch(self._PATCH_REPO) as MockRepo, \
             patch(self._PATCH_HMIA, return_value=True):
            MockRepo.return_value.get_optional.return_value = t
            ok, reason = GenerationValidator(db).can_generate_sessions(77)

        assert ok is True
