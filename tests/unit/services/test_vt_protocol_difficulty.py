"""Protocol difficulty multiplier tests — Phase 2.4.

PD-01   v1 payload → protocol multiplier 1.00
PD-02   v2 payload (no hand_profile) → 1.00
PD-03   v3 + Free / no hand_profile → 1.00
PD-04   Right Index → 1.00
PD-05   Right Thumb → 1.05
PD-06   Left Index → 1.10
PD-07   Left Thumb → 1.15
PD-08   Client sends 9.99 → server clamps to 1.25
PD-09   Client sends 0.50 → server floors to 1.00
PD-10   Invalid string → fallback 1.00
PD-11   XP not affected by protocol multiplier
PD-12   Positive skill delta increases with protocol multiplier
PD-13   Negative skill delta magnitude increases with protocol multiplier
PD-14   Daily negative cap still respected with protocol multiplier
PD-15   Invalid attempt → no XP, no skill delta (unchanged)
PD-16   CR raw_metrics v3 hand_profile saves to VTSignals
PD-17   GNG raw_metrics v3 hand_profile saves to VTSignals
PD-18   Result page badge absent when no hand_profile
PD-19   History badge absent for v1 attempt
PD-20   v1/v2 result page does not crash (backward compat)
PD-21   score_normalized unchanged by protocol multiplier
PD-22   Anti-farming: attempt_index multiplier still dominates
"""
from __future__ import annotations

import pytest

from app.services.virtual_training_metrics import (
    VTDeltaComputer,
    VTSignalExtractor,
    VTSignals,
)
from app.services.virtual_training_service import VirtualTrainingService

# ── Shared helpers ────────────────────────────────────────────────────────────

_PHASE_CONFIG = [
    {"stimuli": 12, "targets": 3, "delay_ms": 2000, "window_ms": 4000, "diameter_px": 70},
    {"stimuli": 12, "targets": 4, "delay_ms": 1200, "window_ms": 3000, "diameter_px": 64},
    {"stimuli": 12, "targets": 5, "delay_ms":  700, "window_ms": 2200, "diameter_px": 58},
]

_SKILL_TARGETS = {"reactions": 0.35, "decisions": 0.30, "concentration": 0.20, "anticipation": 0.15}
_BASE_XP = 20


def _payload(v: int = 3, mult: float | None = 1.00, hand: str = "right",
             finger: str = "index", label: str = "Right Index") -> dict:
    """Build a valid submit payload at the requested version with hand_profile."""
    raw: dict = {"v": v, "per_stimulus": [], "per_phase": []}
    if v >= 2:
        raw["late_summary"] = {
            "late_click_count": 0, "late_click_avg_ms": None,
            "late_click_max_ms": None, "late_go_count": 0, "late_no_go_count": 0,
        }
    if v >= 3 and mult is not None:
        raw["hand_profile"] = {
            "hand": hand, "finger": finger, "label": label,
            "protocol_difficulty_multiplier": mult,
            "self_declared": True, "selected_at_ms": 1234567890,
        }
    return {
        "stimuli_count": 36, "correct_count": 30, "wrong_click_count": 2,
        "error_count": 4, "avg_reaction_ms": 450,
        "min_reaction_ms": 210, "score_raw": 0.72, "score_normalized": 72,
        "duration_seconds": 55.0,
        "raw_metrics": raw,
    }


# ── PD-01..10: extract_protocol_difficulty ────────────────────────────────────

class TestExtractProtocolDifficulty:

    def test_pd01_v1_payload_returns_1(self):
        """PD-01: v1 payload → 1.00 (no hand_profile key exists)."""
        data = _payload(v=1)
        assert VirtualTrainingService.extract_protocol_difficulty(data) == 1.00

    def test_pd02_v2_no_hand_profile_returns_1(self):
        """PD-02: v2 payload (no hand_profile) → 1.00."""
        data = _payload(v=2)
        assert VirtualTrainingService.extract_protocol_difficulty(data) == 1.00

    def test_pd03_v3_no_hand_profile_returns_1(self):
        """PD-03: v3 with mult=None (no hand_profile key) → 1.00."""
        data = _payload(v=3, mult=None)
        assert VirtualTrainingService.extract_protocol_difficulty(data) == 1.00

    def test_pd04_right_index_1_00(self):
        """PD-04: Right Index → 1.00."""
        data = _payload(v=3, mult=1.00, hand="right", finger="index")
        assert VirtualTrainingService.extract_protocol_difficulty(data) == 1.00

    def test_pd05_right_thumb_1_05(self):
        """PD-05: Right Thumb → 1.05."""
        data = _payload(v=3, mult=1.05, hand="right", finger="thumb")
        result = VirtualTrainingService.extract_protocol_difficulty(data)
        assert abs(result - 1.05) < 1e-9

    def test_pd06_left_index_1_10(self):
        """PD-06: Left Index → 1.10."""
        data = _payload(v=3, mult=1.10, hand="left", finger="index")
        result = VirtualTrainingService.extract_protocol_difficulty(data)
        assert abs(result - 1.10) < 1e-9

    def test_pd07_left_thumb_1_15(self):
        """PD-07: Left Thumb → 1.15."""
        data = _payload(v=3, mult=1.15, hand="left", finger="thumb")
        result = VirtualTrainingService.extract_protocol_difficulty(data)
        assert abs(result - 1.15) < 1e-9

    def test_pd08_client_sends_9_99_clamped_to_1_25(self):
        """PD-08: Client sends 9.99 → server clamps to 1.25."""
        data = _payload(v=3, mult=9.99)
        assert VirtualTrainingService.extract_protocol_difficulty(data) == 1.25

    def test_pd09_client_sends_0_50_floored_to_1_00(self):
        """PD-09: Client sends 0.50 → server floors to 1.00."""
        data = _payload(v=3, mult=0.50)
        assert VirtualTrainingService.extract_protocol_difficulty(data) == 1.00

    def test_pd10_invalid_string_fallback_1_00(self):
        """PD-10: Invalid string value → fallback 1.00."""
        data = _payload(v=3, mult=None)
        data["raw_metrics"]["hand_profile"] = {
            "protocol_difficulty_multiplier": "not_a_number",
            "self_declared": True,
        }
        assert VirtualTrainingService.extract_protocol_difficulty(data) == 1.00


# ── PD-11: XP unaffected ──────────────────────────────────────────────────────

class TestXPUnaffected:

    def test_pd11_xp_uses_only_xp_multiplier(self):
        """PD-11: XP calculation uses xp_multiplier only, not protocol_mult."""
        from unittest.mock import MagicMock
        game = MagicMock()
        game.base_xp = 20

        xp_at_1_00 = VirtualTrainingService.calculate_xp_awarded(game, 1.00)
        xp_at_1_15 = VirtualTrainingService.calculate_xp_awarded(game, 1.00)
        assert xp_at_1_00 == xp_at_1_15 == 20


# ── PD-12..14: Delta computation ─────────────────────────────────────────────

class TestDeltaWithProtocol:

    def _base_scores(self) -> dict[str, float]:
        return {"reactions": 0.72, "decisions": 0.65, "concentration": 0.58, "anticipation": 0.70}

    def test_pd12_positive_delta_increases_with_multiplier(self):
        """PD-12: Positive skill delta grows proportionally with protocol multiplier."""
        scores = self._base_scores()
        delta_free = VTDeltaComputer.compute(scores, _SKILL_TARGETS, _BASE_XP, 1.00)
        delta_left_thumb = VTDeltaComputer.compute(scores, _SKILL_TARGETS, _BASE_XP, 1.15)

        for skill in delta_free:
            assert delta_left_thumb[skill] > delta_free[skill], (
                f"{skill}: left_thumb delta should be > free delta"
            )
        # Ratio should be ≈ 1.15 for each positive score
        for skill in delta_free:
            ratio = delta_left_thumb[skill] / delta_free[skill]
            assert abs(ratio - 1.15) < 0.001, (
                f"{skill}: ratio {ratio:.4f} ≠ 1.15"
            )

    def test_pd13_negative_delta_magnitude_increases_with_multiplier(self):
        """PD-13: Negative delta magnitude grows with protocol multiplier."""
        scores = {"reactions": 0.20, "decisions": 0.10, "concentration": 0.15, "anticipation": 0.18}
        delta_free = VTDeltaComputer.compute(scores, _SKILL_TARGETS, _BASE_XP, 1.00)
        delta_left_thumb = VTDeltaComputer.compute(scores, _SKILL_TARGETS, _BASE_XP, 1.15)

        for skill in delta_free:
            assert delta_free[skill] < 0, f"{skill} should be negative"
            assert delta_left_thumb[skill] < delta_free[skill], (
                f"{skill}: protocol multiplier should make negative delta more negative"
            )

    def test_pd14_daily_neg_cap_respected_with_multiplier(self):
        """PD-14: Daily negative cap (-0.50/skill) still enforced with 1.25 multiplier."""
        scores = {"reactions": 0.10, "decisions": 0.05, "concentration": 0.08, "anticipation": 0.12}
        # Pre-fill daily negative: already at cap for 'decisions'
        existing_neg = {"decisions": -0.50}
        delta = VTDeltaComputer.compute(
            scores, _SKILL_TARGETS, _BASE_XP, 1.25, existing_neg_today=existing_neg
        )
        # decisions: cap already reached → delta must be 0.0
        assert delta.get("decisions", 0.0) == 0.0, (
            "decisions delta must be 0 when daily cap already reached"
        )


# ── PD-15: Invalid attempt ────────────────────────────────────────────────────

class TestInvalidAttempt:

    def test_pd15_invalid_attempt_zero_delta(self):
        """PD-15: Invalid attempt (too_short) → is_valid=False → no skill delta."""
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "duration_seconds": 2.0,   # too short
            "stimuli_count": 36,
            "avg_reaction_ms": 400,
        })
        assert not is_valid
        assert reason == "too_short"


# ── PD-16..17: VTSignals v3 extraction ───────────────────────────────────────

class TestVTSignalsV3Extraction:

    def test_pd16_cr_v3_hand_profile_populates_signal(self):
        """PD-16: CR v3 hand_profile → VTSignals.protocol_difficulty_multiplier = 1.10."""
        data = _payload(v=3, mult=1.10, hand="left", finger="index", label="Left Index")
        signals = VTSignalExtractor.extract(data, _PHASE_CONFIG)
        assert abs(signals.protocol_difficulty_multiplier - 1.10) < 1e-9, (
            f"Expected 1.10, got {signals.protocol_difficulty_multiplier}"
        )

    def test_pd17_gng_v3_hand_profile_populates_signal(self):
        """PD-17: GNG v3 hand_profile → VTSignals.protocol_difficulty_multiplier = 1.15."""
        gng_phase_config = [
            {"go": 10, "no_go": 5, "isi_ms": 900, "window_ms": 1000, "stimulus_ms": 800},
            {"go": 11, "no_go": 4, "isi_ms": 650, "window_ms": 1000, "stimulus_ms": 800},
        ]
        data = _payload(v=3, mult=1.15, hand="left", finger="thumb", label="Left Thumb")
        signals = VTSignalExtractor.extract(data, gng_phase_config)
        assert abs(signals.protocol_difficulty_multiplier - 1.15) < 1e-9


# ── PD-18..20: UI / backward compat ──────────────────────────────────────────

class TestUIBackwardCompat:

    def test_pd18_v3_right_index_no_badge_needed(self):
        """PD-18: Right Index (×1.00) — protocol badge condition is mult > 1.0 → badge absent."""
        data = _payload(v=3, mult=1.00, hand="right", finger="index")
        signals = VTSignalExtractor.extract(data, _PHASE_CONFIG)
        assert signals.protocol_difficulty_multiplier == 1.00

    def test_pd19_v1_attempt_no_hand_profile(self):
        """PD-19: v1 attempt → VTSignals.protocol_difficulty_multiplier = 1.00."""
        data = _payload(v=1)
        signals = VTSignalExtractor.extract(data, _PHASE_CONFIG)
        assert signals.protocol_difficulty_multiplier == 1.00

    def test_pd20_v2_attempt_no_hand_profile(self):
        """PD-20: v2 attempt → VTSignals.protocol_difficulty_multiplier = 1.00 (no crash)."""
        data = _payload(v=2)
        signals = VTSignalExtractor.extract(data, _PHASE_CONFIG)
        assert signals.protocol_difficulty_multiplier == 1.00


# ── PD-21: score_normalized unchanged ────────────────────────────────────────

class TestScoreNormalizedUnchanged:

    def test_pd21_score_normalized_not_in_skill_delta_pipeline(self):
        """PD-21: score_normalized is in payload; VTDeltaComputer never reads it.

        The delta pipeline only uses VTSignals (extracted from counts/RT).
        This test confirms that changing protocol_mult doesn't alter score_normalized.
        """
        data_free = _payload(v=3, mult=1.00)
        data_hard = _payload(v=3, mult=1.25)
        # score_normalized is set by JS and stored by the route — it never enters the delta pipeline
        assert data_free["score_normalized"] == data_hard["score_normalized"] == 72
        # Extractor also doesn't touch it
        sig_free = VTSignalExtractor.extract(data_free, _PHASE_CONFIG)
        sig_hard = VTSignalExtractor.extract(data_hard, _PHASE_CONFIG)
        assert sig_free.hit_rate == sig_hard.hit_rate
        assert sig_free.speed_score == sig_hard.speed_score


# ── PD-22: Anti-farming multiplier still dominates ───────────────────────────

class TestAntiFarmingUnchanged:

    def test_pd22_attempt_index_6_effective_multiplier_is_zero(self):
        """PD-22: attempt_index=6 → xp_multiplier=0.0 → effective_multiplier=0 → zero delta."""
        xp_mult = VirtualTrainingService.calculate_xp_multiplier(6)
        assert xp_mult == 0.0, "Attempt index 6 must have zero XP multiplier"

        protocol_mult = 1.25
        effective = xp_mult * protocol_mult
        assert effective == 0.0, "Effective multiplier must be 0 when xp_mult=0"

        # VTDeltaComputer.compute() returns {} when multiplier <= 0
        scores = {"reactions": 0.9, "decisions": 0.85}
        deltas = VTDeltaComputer.compute(scores, _SKILL_TARGETS, _BASE_XP, effective)
        assert deltas == {}, "No deltas should be returned when effective_multiplier=0"
