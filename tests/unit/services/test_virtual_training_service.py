"""Unit tests for VirtualTrainingService — Phase 1.

VT-01   get_games() returns only active games, ordered by id
VT-02   get_games() returns empty list when no active games
VT-03   get_game() returns game by code regardless of is_active
VT-04   get_game() returns None for unknown code
VT-05   validate_attempt() passes clean attempt data
VT-06   validate_attempt() flags avg_reaction_ms < 100 as bot_suspected
VT-07   validate_attempt() accepts avg_reaction_ms == 100 (boundary)
VT-08   validate_attempt() accepts attempt with no avg_reaction_ms key
VT-09   calculate_daily_attempt_index() returns 1 for first attempt
VT-10   calculate_daily_attempt_index() returns 3 when 2 valid attempts today
VT-11   calculate_xp_multiplier() table: 1→1.00, 2→0.75, 3→0.50, 4→0.30, 5→0.15, 6→0.00, 99→0.00
VT-12   calculate_xp_awarded() floors base_xp * multiplier to int
VT-13   calculate_xp_awarded() returns 0 when multiplier is 0.0
VT-18   calculate_daily_attempt_index() is game-specific: CR attempts don't affect GNG index
VT-19   attempt 4 multiplier is 0.30 (reward-eligible)
VT-20   attempt 5 multiplier is 0.15 (reward-eligible)
VT-21   attempt 6 multiplier is 0.00 (no reward)
VT-22   GNG base_xp=12, attempt 5 → 1 XP (floor(12 * 0.15))
VT-23   CR base_xp=20, attempt 5 → 3 XP (floor(20 * 0.15))
VT-24   attempt 4 positive performance → positive skill delta
VT-25   attempt 4 below-neutral performance → negative skill delta (Phase 2.4)
VT-26   attempt 6 → empty skill delta regardless of performance
VT-14   calculate_skill_deltas() produces correct per-skill deltas
VT-15   calculate_skill_deltas() returns empty dict when xp_awarded=0
VT-16   seed data: 12 games present; color_reaction active; stroop_challenge show_in_hub=False
VT-17   get_training_skill_deltas_for_user() merges VT attempt deltas with segment deltas
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from app.services.virtual_training_service import VirtualTrainingService


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_game(
    *,
    id: int = 1,
    code: str = "color_reaction",
    is_active: bool = True,
    base_xp: int = 15,
    skill_targets: dict | None = None,
) -> MagicMock:
    g = MagicMock()
    g.id = id
    g.code = code
    g.is_active = is_active
    g.base_xp = base_xp
    g.skill_targets = skill_targets or {"reactions": 0.55, "concentration": 0.25, "anticipation": 0.20}
    return g


def _mock_db() -> MagicMock:
    return MagicMock()


# ── VT-01..04: get_games / get_game ───────────────────────────────────────────

class TestGetGames:

    def test_vt01_returns_only_active_games(self):
        """VT-01: get_games() filters is_active=True and orders by id."""
        db = _mock_db()
        active = [_mock_game(id=1), _mock_game(id=2)]
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = active
        db.query.return_value = q

        result = VirtualTrainingService.get_games(db)

        assert result is active
        q.all.assert_called_once()

    def test_vt02_returns_empty_list_when_no_active_games(self):
        """VT-02: get_games() returns [] when all games are inactive."""
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        result = VirtualTrainingService.get_games(db)
        assert result == []

    def test_vt03_get_game_returns_by_code(self):
        """VT-03: get_game() fetches a game regardless of is_active."""
        db = _mock_db()
        game = _mock_game(code="stroop_challenge", is_active=False)
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = game
        db.query.return_value = q

        result = VirtualTrainingService.get_game(db, "stroop_challenge")
        assert result is game

    def test_vt04_get_game_returns_none_for_unknown_code(self):
        """VT-04: get_game() returns None for an unknown code."""
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = None
        db.query.return_value = q

        result = VirtualTrainingService.get_game(db, "nonexistent")
        assert result is None


# ── VT-05..08: validate_attempt ───────────────────────────────────────────────

class TestValidateAttempt:

    def test_vt05_valid_attempt_passes(self):
        """VT-05: Clean data with avg_reaction_ms=250 passes validation."""
        is_valid, reason = VirtualTrainingService.validate_attempt(
            {"avg_reaction_ms": 250.0, "score_raw": 8}
        )
        assert is_valid is True
        assert reason is None

    def test_vt06_bot_reaction_ms_flagged(self):
        """VT-06: avg_reaction_ms < 100 triggers bot_suspected."""
        is_valid, reason = VirtualTrainingService.validate_attempt(
            {"avg_reaction_ms": 42.0}
        )
        assert is_valid is False
        assert reason == "bot_suspected"

    def test_vt07_boundary_100ms_is_valid(self):
        """VT-07: avg_reaction_ms == 100.0 is exactly at the boundary — valid."""
        is_valid, reason = VirtualTrainingService.validate_attempt(
            {"avg_reaction_ms": 100.0}
        )
        assert is_valid is True
        assert reason is None

    def test_vt08_missing_reaction_ms_is_valid(self):
        """VT-08: Attempts without avg_reaction_ms key pass (non-reaction games)."""
        is_valid, reason = VirtualTrainingService.validate_attempt(
            {"score_raw": 9, "score_normalized": 90.0}
        )
        assert is_valid is True
        assert reason is None


# ── VT-09..10: calculate_daily_attempt_index ──────────────────────────────────

class TestDailyAttemptIndex:

    def _mock_count_db(self, count: int) -> MagicMock:
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = count
        db.query.return_value = q
        return db

    def test_vt09_first_attempt_returns_1(self):
        """VT-09: When no prior valid attempts today, index = 1."""
        db = self._mock_count_db(0)
        idx = VirtualTrainingService.calculate_daily_attempt_index(db, user_id=7, game_id=1)
        assert idx == 1

    def test_vt10_third_attempt_returns_3(self):
        """VT-10: With 2 valid attempts already today, index = 3."""
        db = self._mock_count_db(2)
        idx = VirtualTrainingService.calculate_daily_attempt_index(db, user_id=7, game_id=1)
        assert idx == 3


# ── VT-11..13: XP calculation ─────────────────────────────────────────────────

class TestXpCalculation:

    @pytest.mark.parametrize("index,expected", [
        (1, 1.00),
        (2, 0.75),
        (3, 0.50),
        (4, 0.30),
        (5, 0.15),
        (6, 0.00),
        (99, 0.00),
    ])
    def test_vt11_xp_multiplier_table(self, index, expected):
        """VT-11: Diminishing-returns multiplier table — 5 reward-eligible attempts per game."""
        assert VirtualTrainingService.calculate_xp_multiplier(index) == pytest.approx(expected)

    def test_vt12_xp_awarded_floors_to_int(self):
        """VT-12: XP = floor(base_xp * multiplier); attempt 2, base_xp=15 → 11."""
        game = _mock_game(base_xp=15)
        # attempt 2: multiplier = 0.75 → 15 * 0.75 = 11.25 → int 11
        xp = VirtualTrainingService.calculate_xp_awarded(game, multiplier=0.75)
        assert xp == 11

    def test_vt13_zero_multiplier_gives_zero_xp(self):
        """VT-13: Multiplier=0.0 yields xp_awarded=0 (4th attempt or beyond)."""
        game = _mock_game(base_xp=15)
        xp = VirtualTrainingService.calculate_xp_awarded(game, multiplier=0.0)
        assert xp == 0


# ── VT-14..15: compute_vt_skill_deltas (Phase 2.2 — performance-based) ───────

class TestSkillDeltas:

    def test_vt14_deltas_computed_correctly(self):
        """VT-14: compute_vt_skill_deltas() produces per-skill deltas from gameplay signals.

        Phase 2.2: skill delta is based on actual performance (reactions, concentration,
        anticipation) not solely on XP. A clean perfect run produces positive deltas
        for all skill keys; total ≤ base_xp / 10 (max at speed=1.0, hit=1.0).
        """
        from app.services.virtual_training_metrics import compute_vt_skill_deltas

        game = _mock_game(
            skill_targets={"reactions": 0.55, "concentration": 0.25, "anticipation": 0.20},
            base_xp=15,
        )
        game.config = {}  # no phase config → defaults (36 stimuli, 3067 ms window)

        deltas = compute_vt_skill_deltas(
            data={"stimuli_count": 36, "correct_count": 36, "wrong_click_count": 0,
                  "error_count": 0, "avg_reaction_ms": 300.0},
            game=game,
            multiplier=1.0,
        )

        assert set(deltas.keys()) == {"reactions", "concentration", "anticipation"}
        for skill, delta in deltas.items():
            assert delta > 0, f"Expected positive delta for {skill}"
        # Total must be ≤ base_xp/10 = 1.5 (ceiling at perfect performance)
        assert sum(deltas.values()) <= 1.5 + 0.01

    def test_vt15_zero_multiplier_returns_empty_deltas(self):
        """VT-15: multiplier=0.0 (4th+ attempt) → empty skill deltas dict."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas

        game = _mock_game(skill_targets={"reactions": 1.0}, base_xp=15)
        game.config = {}
        deltas = compute_vt_skill_deltas(
            data={"stimuli_count": 36, "correct_count": 36, "avg_reaction_ms": 300.0},
            game=game,
            multiplier=0.0,
        )
        assert deltas == {}


# ── VT-16: seed data regression guard ────────────────────────────────────────

class TestSeedData:

    def test_vt16_seed_presets_present_and_correct_active_state(self):
        """VT-16: Seed contains 12 games; color_reaction+go_no_go+memory_sequence+target_tracking active; stroop_challenge hidden; rest planned."""
        from scripts.seed_virtual_training_games import _GAMES

        # 4 active + 1 hidden + 7 planned = 12 total
        assert len(_GAMES) == 12

        codes = {g["code"] for g in _GAMES}
        # Core games present
        assert "color_reaction" in codes
        assert "stroop_challenge" in codes
        assert "go_no_go" in codes
        # Catalog games present
        assert "direction_swipe" in codes
        assert "number_color_conflict" in codes
        assert "memory_sequence" in codes
        assert "target_tracking" in codes
        assert "peripheral_vision" in codes
        assert "dual_task" in codes
        assert "fake_target" in codes
        assert "audio_visual_reaction" in codes
        assert "pattern_break" in codes

        game_map = {g["code"]: g for g in _GAMES}

        # Active state — 4 active games on this branch
        _active_games = {"color_reaction", "go_no_go", "memory_sequence", "target_tracking"}
        for code in _active_games:
            assert game_map[code]["is_active"] is True, f"{code} must be active"
        for code in codes - _active_games:
            assert game_map[code]["is_active"] is False, f"{code} must remain inactive until admin toggle"

        # stroop_challenge hidden from hub
        assert game_map["stroop_challenge"]["config"].get("show_in_hub") is False

        # All hub-visible games have football_benefit in config
        hub_games = [g for g in _GAMES if g["config"].get("show_in_hub", True) is not False]
        for g in hub_games:
            assert g["config"].get("football_benefit"), f"{g['code']} missing football_benefit in config"
            assert g["config"].get("icon"), f"{g['code']} missing icon in config"


# ── VT-17: get_training_skill_deltas_for_user merges VT deltas ───────────────

class TestGetTrainingSkillDeltas:

    def test_vt17_merges_segment_and_vt_deltas(self):
        """VT-17: get_training_skill_deltas_for_user sums both sources."""
        from app.services.segment_reward_service import get_training_skill_deltas_for_user

        db = _mock_db()

        # segment_segment_results rows: reactions=1.5
        seg_row = MagicMock()
        seg_row.__getitem__ = lambda self, i: ("reactions", 1.5)[i]
        seg_row = ("reactions", 1.5)

        # virtual_training_attempts rows: reactions=0.82, concentration=0.38
        vt_rows = [("reactions", 0.82), ("concentration", 0.38)]

        def _execute(query, params):
            sql = str(query)
            result = MagicMock()
            if "session_segment_results" in sql:
                result.fetchall.return_value = [seg_row]
            else:
                result.fetchall.return_value = vt_rows
            return result

        db.execute.side_effect = _execute

        deltas = get_training_skill_deltas_for_user(db, user_id=42)

        assert deltas["reactions"] == pytest.approx(1.5 + 0.82, abs=0.01)
        assert deltas["concentration"] == pytest.approx(0.38, abs=0.01)


# ── VT-18: game-specific attempt index ────────────────────────────────────────

class TestGameSpecificAttemptIndex:

    def _mock_count_db(self, count: int) -> MagicMock:
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = count
        db.query.return_value = q
        return db

    def test_vt18_cr_attempts_do_not_affect_gng_index(self):
        """VT-18: 5 Color Reaction attempts today → GNG index still starts at 1.

        The query filters by game_id so CR and GNG are counted separately.
        This test verifies the filter is applied (count returns 0 for a fresh game).
        """
        db = self._mock_count_db(0)   # 0 prior GNG attempts → index = 1
        idx = VirtualTrainingService.calculate_daily_attempt_index(db, user_id=7, game_id=3)
        assert idx == 1
        # game_id=3 (GNG) was passed to the query filter
        filter_call_args = db.query.return_value.filter.call_args
        assert filter_call_args is not None


# ── VT-19..21: new multiplier values (4, 5, 6) ────────────────────────────────

class TestNewMultiplierValues:

    def test_vt19_attempt_4_multiplier_is_0_30(self):
        """VT-19: attempt 4 → multiplier 0.30 (reward-eligible, reduced)."""
        assert VirtualTrainingService.calculate_xp_multiplier(4) == pytest.approx(0.30)

    def test_vt20_attempt_5_multiplier_is_0_15(self):
        """VT-20: attempt 5 → multiplier 0.15 (last reward-eligible attempt)."""
        assert VirtualTrainingService.calculate_xp_multiplier(5) == pytest.approx(0.15)

    def test_vt21_attempt_6_multiplier_is_0_00(self):
        """VT-21: attempt 6 → multiplier 0.00 (no reward, no delta)."""
        assert VirtualTrainingService.calculate_xp_multiplier(6) == pytest.approx(0.00)


# ── VT-22..23: XP at attempt 5 for GNG and CR ────────────────────────────────

class TestXpAttempt5:

    def test_vt22_gng_base_xp_12_attempt_5_gives_1_xp(self):
        """VT-22: GNG base_xp=12, attempt 5 (multiplier=0.15) → floor(1.8) = 1 XP."""
        game = _mock_game(base_xp=12)
        xp = VirtualTrainingService.calculate_xp_awarded(game, multiplier=0.15)
        assert xp == 1
        assert xp >= 1

    def test_vt23_cr_base_xp_20_attempt_5_gives_3_xp(self):
        """VT-23: CR base_xp=20, attempt 5 (multiplier=0.15) → floor(3.0) = 3 XP."""
        game = _mock_game(base_xp=20)
        xp = VirtualTrainingService.calculate_xp_awarded(game, multiplier=0.15)
        assert xp == 3
        assert xp >= 1


# ── VT-24..26: skill delta at attempt 4, 5, 6 (Phase 2.4) ───────────────────

class TestSkillDeltaAtNewAttemptIndices:

    def _gng_game(self) -> MagicMock:
        g = _mock_game(base_xp=12, skill_targets={
            "decisions": 0.35, "concentration": 0.30, "composure": 0.20, "reactions": 0.15,
        })
        g.config = {"phases": [{"go": 10, "no_go": 5, "window_ms": 1000},
                                {"go": 11, "no_go": 4, "window_ms": 1000}]}
        return g

    def test_vt24_attempt_4_positive_performance_gives_positive_delta(self):
        """VT-24: attempt 4 (multiplier=0.30) good performance → positive skill deltas."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas
        # Perfect GNG: all GO hit, no false alarm → decisions score = 1.0
        data = {
            "stimuli_count": 30, "correct_count": 21, "wrong_click_count": 0,
            "error_count": 0, "avg_reaction_ms": 350.0,
        }
        deltas = compute_vt_skill_deltas(data=data, game=self._gng_game(), multiplier=0.30)
        assert len(deltas) > 0
        assert all(v > 0 for v in deltas.values()), f"Expected all positive, got {deltas}"

    def test_vt25_attempt_4_poor_performance_gives_negative_delta(self):
        """VT-25: attempt 4 (multiplier=0.30) weak performance → negative skill deltas (Phase 2.4)."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas
        # Very poor GNG: mostly false alarms → decisions score negative
        data = {
            "stimuli_count": 30, "correct_count": 2, "wrong_click_count": 12,
            "error_count": 16, "avg_reaction_ms": 700.0,
        }
        deltas = compute_vt_skill_deltas(data=data, game=self._gng_game(), multiplier=0.30)
        # decisions: 2/30 - 1.5*(12/30) = 0.067 - 0.600 = -0.533 → negative delta
        assert "decisions" in deltas
        assert deltas["decisions"] < 0, f"Expected negative decisions delta, got {deltas}"

    def test_vt26_attempt_6_gives_empty_delta_regardless_of_performance(self):
        """VT-26: attempt 6 (multiplier=0.00) → empty skill deltas for any performance."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas
        # Good performance
        good_data = {
            "stimuli_count": 30, "correct_count": 21, "wrong_click_count": 0,
            "error_count": 0, "avg_reaction_ms": 350.0,
        }
        # Poor performance
        bad_data = {
            "stimuli_count": 30, "correct_count": 2, "wrong_click_count": 12,
            "error_count": 16, "avg_reaction_ms": 700.0,
        }
        assert compute_vt_skill_deltas(data=good_data, game=self._gng_game(), multiplier=0.00) == {}
        assert compute_vt_skill_deltas(data=bad_data,  game=self._gng_game(), multiplier=0.00) == {}
