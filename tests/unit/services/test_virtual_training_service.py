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
VT-11   calculate_xp_multiplier() table: index 1→1.0, 2→0.6, 3→0.3, 4→0.0, 99→0.0
VT-12   calculate_xp_awarded() floors base_xp * multiplier to int
VT-13   calculate_xp_awarded() returns 0 when multiplier is 0.0
VT-14   calculate_skill_deltas() produces correct per-skill deltas
VT-15   calculate_skill_deltas() returns empty dict when xp_awarded=0
VT-16   seed data: 3 games present, all is_active=False (regression guard)
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
        (1, 1.0),
        (2, 0.6),
        (3, 0.3),
        (4, 0.0),
        (99, 0.0),
    ])
    def test_vt11_xp_multiplier_table(self, index, expected):
        """VT-11: Diminishing-returns multiplier table is correct."""
        assert VirtualTrainingService.calculate_xp_multiplier(index) == pytest.approx(expected)

    def test_vt12_xp_awarded_floors_to_int(self):
        """VT-12: XP = floor(base_xp * multiplier); index 2 with base_xp=15 → 9."""
        game = _mock_game(base_xp=15)
        # multiplier = 0.6 → 15 * 0.6 = 9.0 → int 9
        xp = VirtualTrainingService.calculate_xp_awarded(game, multiplier=0.6)
        assert xp == 9

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
        """VT-16: Seed data defines exactly 3 presets; color_reaction is_active=True (Phase 2), others False."""
        from scripts.seed_virtual_training_games import _GAMES

        assert len(_GAMES) == 3
        codes = {g["code"] for g in _GAMES}
        assert codes == {"color_reaction", "stroop_challenge", "go_no_go"}

        active_map = {g["code"]: g["is_active"] for g in _GAMES}
        assert active_map["color_reaction"] is True, "color_reaction must be active in Phase 2"
        assert active_map["stroop_challenge"] is False, "stroop_challenge not yet active"
        assert active_map["go_no_go"] is False, "go_no_go not yet active"


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
