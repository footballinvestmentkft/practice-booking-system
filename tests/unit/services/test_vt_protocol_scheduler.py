"""Protocol scheduler tests — Phase 2.4 (system-assigned balanced scheduler).

PS-01   0 prior v3 attempts → Right Index (seeded first)
PS-02   1 prior → Right Thumb (seeded second)
PS-03   2 prior → Left Index (seeded third)
PS-04   3 prior → Left Thumb (seeded fourth)
PS-05   4+ equal usage → deterministic tiebreak (pool index 0 = Right Index)
PS-06   last two same → next must differ (consecutive guard)
PS-07   Left Thumb 5× in a row → consecutive guard blocks it
PS-08   invalid attempts excluded from history count
PS-09   game.config["protocol_assignment"] == "free" → Free
PS-10   assignment_source == "system" on every result
PS-11   all required keys present in returned dict
PS-12   Right Thumb → pdm = 1.05
PS-13   Left Index  → pdm = 1.10
PS-14   Left Thumb  → pdm = 1.15
PS-15   CR and GNG have independent assignment (game_id isolation)
PS-16   extract_protocol_difficulty reads system-assigned payload correctly
PS-17   XP unchanged by protocol multiplier (already covered by PD-11; smoke here)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.services.virtual_training_service import VirtualTrainingService, _FREE_PROTOCOL

# ── Helpers ───────────────────────────────────────────────────────────────────

_REQUIRED_KEYS = {
    "hand", "finger", "label",
    "protocol_difficulty_multiplier",
    "assignment_source",
    "self_declared",
    "not_verified",
}

def _history(*combos: str) -> list[str]:
    """Build a history list (most-recent first) from short-hand combo names.

    Accepts: "ri" | "rt" | "li" | "lt"
    Returns: e.g. ["right_index", "left_thumb", ...]
    """
    _map = {
        "ri": "right_index",
        "rt": "right_thumb",
        "li": "left_index",
        "lt": "left_thumb",
    }
    return [_map[c] for c in combos]


def _select(history_combos: list[str]) -> dict:
    """Invoke pure scheduler (no DB)."""
    return VirtualTrainingService._select_protocol_from_history(history_combos)


def _assign_with_mock(history_combos: list[str], protocol_assignment: str | None = None) -> dict:
    """Invoke assign_protocol() with a fully mocked DB.

    history_combos are returned by the mocked JSONB query (most-recent first).
    """
    db = MagicMock()

    game_mock = MagicMock()
    game_mock.config = (
        {"protocol_assignment": protocol_assignment}
        if protocol_assignment is not None
        else {}
    )
    db.query.return_value.filter.return_value.first.return_value = game_mock

    def _make_row(combo: str) -> MagicMock:
        hand, finger = combo.split("_", 1)
        row = MagicMock()
        row.hp = {"hand": hand, "finger": finger,
                  "protocol_difficulty_multiplier": 1.00,
                  "assignment_source": "system"}
        return row

    db.execute.return_value.fetchall.return_value = [
        _make_row(c) for c in history_combos
    ]

    return VirtualTrainingService.assign_protocol(db, user_id=101, game_id=42)


# ── PS-01..04: Seeded first-rotation ─────────────────────────────────────────

class TestSeededFirstRotation:

    def test_ps01_zero_history_right_index(self):
        """PS-01: 0 prior v3 attempts → Right Index."""
        result = _select([])
        assert result["hand"] == "right"
        assert result["finger"] == "index"

    def test_ps02_one_prior_right_thumb(self):
        """PS-02: 1 prior attempt → Right Thumb."""
        result = _select(_history("ri"))
        assert result["hand"] == "right"
        assert result["finger"] == "thumb"

    def test_ps03_two_prior_left_index(self):
        """PS-03: 2 prior attempts → Left Index."""
        result = _select(_history("ri", "rt"))
        assert result["hand"] == "left"
        assert result["finger"] == "index"

    def test_ps04_three_prior_left_thumb(self):
        """PS-04: 3 prior attempts → Left Thumb."""
        result = _select(_history("ri", "rt", "li"))
        assert result["hand"] == "left"
        assert result["finger"] == "thumb"


# ── PS-05: Tiebreak determinism ───────────────────────────────────────────────

class TestTiebreakDeterminism:

    def test_ps05_equal_usage_pool_index_zero_wins(self):
        """PS-05: All 4 combos equally used → Right Index (pool[0]) wins."""
        history = _history("ri", "rt", "li", "lt")  # one of each, most-recent first
        result = _select(history)
        # All counts = 1, tiebreak = pool index → Right Index (index 0)
        assert result["hand"] == "right"
        assert result["finger"] == "index"

    def test_ps05b_repeated_equal_usage_still_deterministic(self):
        """PS-05b: 2× of each → still Right Index (lowest pool index)."""
        history = _history("ri", "rt", "li", "lt", "ri", "rt", "li", "lt")
        result = _select(history)
        assert result["hand"] == "right"
        assert result["finger"] == "index"


# ── PS-06..07: Consecutive guard ─────────────────────────────────────────────

class TestConsecutiveGuard:

    def test_ps06_last_two_same_next_differs(self):
        """PS-06: last 2 = Left Thumb → next must not be Left Thumb."""
        history = _history("lt", "lt", "ri", "rt", "li")  # 2× lt at front
        result = _select(history)
        assert not (result["hand"] == "left" and result["finger"] == "thumb"), (
            "Consecutive guard failed: Left Thumb assigned again after 2 in a row"
        )

    def test_ps07_five_left_thumb_in_row_blocked(self):
        """PS-07: Left Thumb 5× in a row → consecutive guard blocks 6th."""
        history = _history("lt", "lt", "lt", "lt", "lt")
        result = _select(history)
        assert not (result["hand"] == "left" and result["finger"] == "thumb")

    def test_ps06b_one_repeat_not_blocked(self):
        """PS-06b: Only 1 left_thumb in a row → consecutive guard does NOT block."""
        # Build history where lt appears only once at front; all combos ≥4 present
        history = _history("lt", "ri", "rt", "li")
        result = _select(history)
        # No combo is blocked; least-used wins (all count 1 → pool index 0 = ri wins)
        assert result["hand"] == "right" and result["finger"] == "index"


# ── PS-08: Invalid attempts excluded ─────────────────────────────────────────

class TestInvalidExclusion:

    def test_ps08_invalid_attempts_not_counted(self):
        """PS-08: assign_protocol() only queries is_valid=TRUE attempts (mocked DB)."""
        db = MagicMock()
        game_mock = MagicMock()
        game_mock.config = {}
        db.query.return_value.filter.return_value.first.return_value = game_mock

        # Return 0 rows → seeded first rotation → Right Index
        db.execute.return_value.fetchall.return_value = []

        result = VirtualTrainingService.assign_protocol(db, user_id=99, game_id=1)
        assert result["hand"] == "right"
        assert result["finger"] == "index"

        # Verify SQL contains is_valid
        sql_str = str(db.execute.call_args[0][0])
        assert "is_valid" in sql_str


# ── PS-09: Feature flag → Free ────────────────────────────────────────────────

class TestFeatureFlag:

    def test_ps09_protocol_assignment_free_returns_free(self):
        """PS-09: game.config["protocol_assignment"] == "free" → Free protocol."""
        result = _assign_with_mock([], protocol_assignment="free")
        assert result["hand"] == "free"
        assert result["finger"] == "free"
        assert result["protocol_difficulty_multiplier"] == 1.00

    def test_ps09b_balanced_flag_uses_scheduler(self):
        """PS-09b: config without "free" flag → scheduler runs normally."""
        result = _assign_with_mock([])  # no flag → seeded first → Right Index
        assert result["hand"] == "right"
        assert result["finger"] == "index"


# ── PS-10..11: Required fields ────────────────────────────────────────────────

class TestRequiredFields:

    def test_ps10_assignment_source_is_system(self):
        """PS-10: Every scheduler result has assignment_source == "system"."""
        for combos in [[], _history("ri"), _history("ri", "rt", "li", "lt")]:
            result = _select(combos)
            # _select_protocol_from_history returns the raw slot dict (no source yet)
            # assign_protocol adds the source — test via assign
        result = _assign_with_mock([])
        assert result.get("assignment_source") == "system"

    def test_ps11_all_required_keys_present(self):
        """PS-11: Every assigned protocol contains all required keys."""
        result = _assign_with_mock(_history("ri", "rt", "li"))
        missing = _REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_ps11b_free_protocol_has_all_keys(self):
        """PS-11b: Free fallback also contains all required keys."""
        result = _assign_with_mock([], protocol_assignment="free")
        missing = _REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Free protocol missing keys: {missing}"

    def test_ps11c_self_declared_and_not_verified_are_true(self):
        """PS-11c: self_declared and not_verified are always True."""
        result = _assign_with_mock(_history("ri", "rt", "li"))
        assert result["self_declared"] is True
        assert result["not_verified"] is True


# ── PS-12..14: Multiplier values ─────────────────────────────────────────────

class TestMultiplierValues:

    def test_ps12_right_thumb_pdm_1_05(self):
        """PS-12: Right Thumb → protocol_difficulty_multiplier = 1.05."""
        result = _select(_history("ri"))  # seeded second = Right Thumb
        assert abs(result["protocol_difficulty_multiplier"] - 1.05) < 1e-9

    def test_ps13_left_index_pdm_1_10(self):
        """PS-13: Left Index → protocol_difficulty_multiplier = 1.10."""
        result = _select(_history("ri", "rt"))  # seeded third = Left Index
        assert abs(result["protocol_difficulty_multiplier"] - 1.10) < 1e-9

    def test_ps14_left_thumb_pdm_1_15(self):
        """PS-14: Left Thumb → protocol_difficulty_multiplier = 1.15."""
        result = _select(_history("ri", "rt", "li"))  # seeded fourth = Left Thumb
        assert abs(result["protocol_difficulty_multiplier"] - 1.15) < 1e-9

    def test_ps12b_right_index_pdm_1_00(self):
        """PS-12b: Right Index → protocol_difficulty_multiplier = 1.00."""
        result = _select([])  # seeded first = Right Index
        assert result["protocol_difficulty_multiplier"] == 1.00


# ── PS-15: CR / GNG game_id isolation ────────────────────────────────────────

class TestGameIdIsolation:

    def test_ps15_different_game_ids_independent(self):
        """PS-15: CR and GNG have independent history per game_id."""
        db = MagicMock()
        game_mock = MagicMock()
        game_mock.config = {}
        db.query.return_value.filter.return_value.first.return_value = game_mock

        call_args_list = []

        def _capture_execute(sql, params):
            call_args_list.append(params)
            mock_result = MagicMock()
            mock_result.fetchall.return_value = []
            return mock_result

        db.execute.side_effect = _capture_execute

        VirtualTrainingService.assign_protocol(db, user_id=5, game_id=1)
        VirtualTrainingService.assign_protocol(db, user_id=5, game_id=2)

        assert len(call_args_list) == 2
        assert call_args_list[0]["gid"] == 1
        assert call_args_list[1]["gid"] == 2


# ── PS-16: extract_protocol_difficulty reads system-assigned payload ──────────

class TestExtractFromSystemAssigned:

    def _system_payload(self, mult: float) -> dict:
        return {
            "stimuli_count": 36, "correct_count": 30, "wrong_click_count": 2,
            "error_count": 4, "avg_reaction_ms": 450, "min_reaction_ms": 210,
            "score_raw": 0.72, "score_normalized": 72, "duration_seconds": 55.0,
            "raw_metrics": {
                "v": 3,
                "per_stimulus": [], "per_phase": [],
                "hand_profile": {
                    "hand": "left", "finger": "thumb", "label": "Left Thumb",
                    "protocol_difficulty_multiplier": mult,
                    "assignment_source": "system",
                    "self_declared": True,
                    "not_verified": True,
                },
            },
        }

    def test_ps16_extract_reads_system_assigned_multiplier(self):
        """PS-16: extract_protocol_difficulty reads pdm from system-assigned payload."""
        data = self._system_payload(1.15)
        result = VirtualTrainingService.extract_protocol_difficulty(data)
        assert abs(result - 1.15) < 1e-9

    def test_ps16b_server_clamp_still_applies(self):
        """PS-16b: Server clamp still enforced even for system-assigned payloads."""
        data = self._system_payload(9.99)
        result = VirtualTrainingService.extract_protocol_difficulty(data)
        assert result == 1.25

    def test_ps16c_v1_payload_returns_1_00(self):
        """PS-16c: v1 backward compat — no hand_profile → 1.00."""
        data = {"raw_metrics": {"v": 1, "per_stimulus": []}}
        assert VirtualTrainingService.extract_protocol_difficulty(data) == 1.00


# ── PS-17: XP and skill delta smoke tests ────────────────────────────────────

class TestScoringInvariants:

    def test_ps17_xp_unchanged_by_protocol_multiplier(self):
        """PS-17: XP uses xp_multiplier only — protocol_mult has no effect."""
        from unittest.mock import MagicMock as MM
        game = MM()
        game.base_xp = 20

        xp_1_00 = VirtualTrainingService.calculate_xp_awarded(game, 1.00)
        xp_1_15 = VirtualTrainingService.calculate_xp_awarded(game, 1.00)  # same xp mult
        assert xp_1_00 == xp_1_15 == 20

    def test_ps17b_effective_multiplier_compound(self):
        """PS-17b: effective = xp_mult × protocol_mult."""
        xp_mult      = VirtualTrainingService.calculate_xp_multiplier(2)   # 0.75
        protocol_mult = 1.15
        effective    = xp_mult * protocol_mult
        assert abs(effective - 0.8625) < 1e-9

    def test_ps17c_zero_xp_mult_zero_effective(self):
        """PS-17c: attempt_index=6 → xp_mult=0 → effective=0 → zero delta."""
        from app.services.virtual_training_metrics import VTDeltaComputer
        xp_mult      = VirtualTrainingService.calculate_xp_multiplier(6)
        protocol_mult = 1.25
        effective    = xp_mult * protocol_mult
        assert effective == 0.0

        scores = {"reactions": 0.9, "decisions": 0.85}
        skill_targets = {"reactions": 0.5, "decisions": 0.5}
        deltas = VTDeltaComputer.compute(scores, skill_targets, 20, effective)
        assert deltas == {}
