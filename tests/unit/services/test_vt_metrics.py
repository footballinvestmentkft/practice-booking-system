"""Unit tests for Virtual Training Metrics — Phase 2.2 + Phase 2.3 late-click.

VM-01..08   VTSignalExtractor.extract()
VM-09..13   VTSkillScorer.score_reactions()
VM-14..18   VTSkillScorer.score_decisions()
VM-19..23   VTSkillScorer.score_concentration()
VM-24..27   VTSkillScorer.score_anticipation()
VM-28..33   VTDeltaComputer.compute()
VM-34..36   End-to-end: score-blindness eliminated (same XP, different performance → different delta)
LC-01..16   Late-click detection: raw_metrics v2 extraction + scorer v2 behaviour
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.services.virtual_training_metrics import (
    VTSignalExtractor,
    VTSignals,
    VTSkillScorer,
    VTDeltaComputer,
    compute_vt_skill_deltas,
)


# ── Shared fixtures ────────────────────────────────────────────────────────────

_PHASE21_CONFIG = [
    {"stimuli": 12, "targets": 3, "delay_ms": 2000, "window_ms": 4000, "diameter_px": 70},
    {"stimuli": 12, "targets": 4, "delay_ms": 1200, "window_ms": 3000, "diameter_px": 64},
    {"stimuli": 12, "targets": 5, "delay_ms":  700, "window_ms": 2200, "diameter_px": 58},
]

_SKILL_TARGETS = {
    "reactions": 0.35, "decisions": 0.30, "concentration": 0.20, "anticipation": 0.15,
}

# Phase avg window = (4000 + 3000 + 2200) / 3 = 3066.67 ms
_PHASE_AVG_WINDOW = (4000 + 3000 + 2200) / 3


def _mock_game(base_xp: int = 20, skill_targets: dict | None = None) -> MagicMock:
    g = MagicMock()
    g.base_xp       = base_xp
    g.skill_targets = skill_targets or _SKILL_TARGETS
    g.config        = {"phases": _PHASE21_CONFIG}
    return g


def _signals(
    *,
    hit_rate:        float = 1.0,
    wrong_rate:      float = 0.0,
    miss_rate:       float = 0.0,
    speed_score:     float = 0.9,
    completion_rate: float = 1.0,
    avg_reaction_ms: float | None = None,
    per_phase:       list | None = None,
) -> VTSignals:
    return VTSignals(
        hit_rate=hit_rate,
        wrong_rate=wrong_rate,
        miss_rate=miss_rate,
        speed_score=speed_score,
        completion_rate=completion_rate,
        avg_reaction_ms=avg_reaction_ms,
        per_phase=per_phase,
    )


# ── TestSignalExtraction (VM-01..08) ──────────────────────────────────────────

class TestSignalExtraction:

    def test_vm_01_perfect_run(self):
        """VM-01: 36/36 correct, 0 wrong, 0 miss, avg_rt=280 → correct signals."""
        sig = VTSignalExtractor.extract(
            {"stimuli_count": 36, "correct_count": 36, "wrong_click_count": 0,
             "error_count": 0, "avg_reaction_ms": 280.0},
            _PHASE21_CONFIG,
        )
        assert sig.hit_rate        == pytest.approx(1.0)
        assert sig.wrong_rate      == pytest.approx(0.0)
        assert sig.miss_rate       == pytest.approx(0.0)
        assert sig.speed_score     == pytest.approx(1.0 - 280.0 / _PHASE_AVG_WINDOW, abs=0.001)
        assert sig.completion_rate == pytest.approx(1.0)
        assert sig.avg_reaction_ms == pytest.approx(280.0)

    def test_vm_02_all_misses(self):
        """VM-02: 0 correct, 0 wrong, 36 misses → miss_rate=1, hit_rate=0."""
        sig = VTSignalExtractor.extract(
            {"stimuli_count": 36, "correct_count": 0, "wrong_click_count": 0, "error_count": 36},
            _PHASE21_CONFIG,
        )
        assert sig.hit_rate  == pytest.approx(0.0)
        assert sig.miss_rate == pytest.approx(1.0)

    def test_vm_03_all_wrong_clicks(self):
        """VM-03: 0 correct, 36 wrong, 0 miss → wrong_rate=1, hit_rate=0."""
        sig = VTSignalExtractor.extract(
            {"stimuli_count": 36, "correct_count": 0, "wrong_click_count": 36, "error_count": 0},
            _PHASE21_CONFIG,
        )
        assert sig.wrong_rate == pytest.approx(1.0)
        assert sig.hit_rate   == pytest.approx(0.0)

    def test_vm_04_mixed_outcomes(self):
        """VM-04: 28 correct, 4 wrong, 4 miss → rates sum to 1."""
        sig = VTSignalExtractor.extract(
            {"stimuli_count": 36, "correct_count": 28, "wrong_click_count": 4, "error_count": 4},
            _PHASE21_CONFIG,
        )
        assert sig.hit_rate   == pytest.approx(28 / 36, abs=0.001)
        assert sig.wrong_rate == pytest.approx(4  / 36, abs=0.001)
        assert sig.miss_rate  == pytest.approx(4  / 36, abs=0.001)
        # rates sum to 1.0 (all stimuli accounted for)
        assert sig.hit_rate + sig.wrong_rate + sig.miss_rate == pytest.approx(1.0, abs=0.001)

    def test_vm_05_missing_avg_reaction_ms_gives_neutral_speed(self):
        """VM-05: No avg_reaction_ms → speed_score = 0.5 (neutral), avg_reaction_ms = None."""
        sig = VTSignalExtractor.extract(
            {"stimuli_count": 36, "correct_count": 30, "wrong_click_count": 2, "error_count": 4},
            _PHASE21_CONFIG,
        )
        assert sig.speed_score     == pytest.approx(0.5)
        assert sig.avg_reaction_ms is None

    def test_vm_06_zero_stimuli_count_does_not_crash(self):
        """VM-06: stimuli_count=0 (or absent) → inferred from phase_config sum, no ZeroDivisionError."""
        sig = VTSignalExtractor.extract(
            {"correct_count": 5, "wrong_click_count": 0, "error_count": 0},
            _PHASE21_CONFIG,
        )
        # should not raise; completion_rate and rates are valid
        assert 0.0 <= sig.hit_rate  <= 1.0
        assert 0.0 <= sig.miss_rate <= 1.0

    def test_vm_07_raw_metrics_v1_per_phase_populated(self):
        """VM-07: valid raw_metrics with v=1 → per_phase extracted into VTSignals."""
        per_phase_data = [
            {"phase": 0, "stimuli": 12, "correct": 11, "wrong": 1, "miss": 0, "avg_rt_ms": 400},
            {"phase": 1, "stimuli": 12, "correct": 10, "wrong": 1, "miss": 1, "avg_rt_ms": 360},
            {"phase": 2, "stimuli": 12, "correct":  8, "wrong": 1, "miss": 3, "avg_rt_ms": 320},
        ]
        sig = VTSignalExtractor.extract(
            {"stimuli_count": 36, "correct_count": 29, "avg_reaction_ms": 360.0,
             "raw_metrics": {"v": 1, "per_stimulus": [], "per_color": {}, "per_phase": per_phase_data}},
            _PHASE21_CONFIG,
        )
        assert sig.per_phase is not None
        assert len(sig.per_phase) == 3
        assert sig.per_phase[2]["correct"] == 8

    def test_vm_08_raw_metrics_v0_gives_none(self):
        """VM-08: raw_metrics with v=0 (below minimum) → per_phase remains None."""
        sig = VTSignalExtractor.extract(
            {"stimuli_count": 36, "correct_count": 30,
             "raw_metrics": {"v": 0, "per_phase": [{"phase": 0}]}},
            _PHASE21_CONFIG,
        )
        assert sig.per_phase is None


# ── TestReactionsScore (VM-09..13) ────────────────────────────────────────────

class TestReactionsScore:

    def test_vm_09_fast_reactions_high_score(self):
        """VM-09: speed_score=0.94, hit_rate=1.0 → reactions > 0.95."""
        sig = _signals(speed_score=0.94, hit_rate=1.0)
        score = VTSkillScorer.score_reactions(sig)
        expected = 0.65 * 0.94 + 0.35 * 1.0
        assert score == pytest.approx(expected, abs=0.001)
        assert score > 0.95

    def test_vm_10_slow_reactions_lower_score(self):
        """VM-10: speed_score=0.74 (≈800 ms), hit_rate=0.78 → moderate score."""
        sig = _signals(speed_score=0.74, hit_rate=0.78)
        score = VTSkillScorer.score_reactions(sig)
        expected = 0.65 * 0.74 + 0.35 * 0.78
        assert score == pytest.approx(expected, abs=0.001)
        assert 0.6 < score < 0.85

    def test_vm_11_very_slow_reactions_above_window(self):
        """VM-11: speed_score=0.0 (rt ≥ window), hit_rate=0.5 → score = 0.35×hit_rate only."""
        sig = _signals(speed_score=0.0, hit_rate=0.5)
        score = VTSkillScorer.score_reactions(sig)
        assert score == pytest.approx(0.35 * 0.5, abs=0.001)

    def test_vm_12_neutral_speed_when_rt_not_recorded(self):
        """VM-12: speed_score=0.5 (neutral), hit_rate=0.8 → blended score."""
        sig = _signals(speed_score=0.5, hit_rate=0.8)
        score = VTSkillScorer.score_reactions(sig)
        expected = 0.65 * 0.5 + 0.35 * 0.8
        assert score == pytest.approx(expected, abs=0.001)

    def test_vm_13_zero_hit_rate_score_from_speed_only(self):
        """VM-13: hit_rate=0.0, speed_score=0.8 → score = 0.65×speed only."""
        sig = _signals(speed_score=0.8, hit_rate=0.0)
        score = VTSkillScorer.score_reactions(sig)
        assert score == pytest.approx(0.65 * 0.8, abs=0.001)


# ── TestDecisionsScore (VM-14..18) ────────────────────────────────────────────

class TestDecisionsScore:

    def test_vm_14_zero_wrong_score_equals_hit_rate(self):
        """VM-14: wrong_rate=0 → decisions = hit_rate."""
        sig = _signals(hit_rate=0.85, wrong_rate=0.0)
        score = VTSkillScorer.score_decisions(sig)
        assert score == pytest.approx(0.85, abs=0.001)

    def test_vm_15_high_wrong_rate_low_score(self):
        """VM-15: wrong_rate=0.4, hit_rate=0.5 → score penalised to negative (lower clamp removed)."""
        sig = _signals(hit_rate=0.5, wrong_rate=0.4)
        score = VTSkillScorer.score_decisions(sig)
        # 0.5 - 1.5*0.4 = -0.1; only upper clamp (min(1.0)) applies now
        expected = min(1.0, 0.5 - 1.5 * 0.4)
        assert score == pytest.approx(expected, abs=0.001)
        assert score < 0

    def test_vm_16_moderate_wrong_reasonable_score(self):
        """VM-16: wrong_rate=0.1, hit_rate=0.85 → reasonable decision score."""
        sig = _signals(hit_rate=0.85, wrong_rate=0.1)
        score = VTSkillScorer.score_decisions(sig)
        expected = 0.85 - 1.5 * 0.1
        assert score == pytest.approx(expected, abs=0.001)
        assert 0.5 < score < 0.85

    def test_vm_17_all_wrong_clicks_score_very_negative(self):
        """VM-17: wrong_rate=1.0, hit_rate=0.0 → -1.5 (lower clamp removed)."""
        sig = _signals(hit_rate=0.0, wrong_rate=1.0)
        score = VTSkillScorer.score_decisions(sig)
        # min(1.0, 0 - 1.5*1.0) = -1.5
        assert score == pytest.approx(-1.5)
        assert score < 0

    def test_vm_18_wrong_pushes_below_zero_negative(self):
        """VM-18: formula result < 0 → negative score (lower clamp removed)."""
        # hit_rate=0.1, wrong_rate=0.5 → 0.1 - 0.75 = -0.65
        sig = _signals(hit_rate=0.1, wrong_rate=0.5)
        score = VTSkillScorer.score_decisions(sig)
        assert score == pytest.approx(-0.65)
        assert score < 0


# ── TestConcentrationScore (VM-19..23) ────────────────────────────────────────

class TestConcentrationScore:

    def test_vm_19_zero_misses_perfect_score(self):
        """VM-19: miss_rate=0 → concentration = 1.0."""
        sig = _signals(miss_rate=0.0)
        assert VTSkillScorer.score_concentration(sig) == pytest.approx(1.0)

    def test_vm_20_all_misses_score_negative(self):
        """VM-20: miss_rate=1.0 → 1−2×1 = -1.0 (lower clamp removed)."""
        sig = _signals(miss_rate=1.0)
        assert VTSkillScorer.score_concentration(sig) == pytest.approx(-1.0)
        assert VTSkillScorer.score_concentration(sig) < 0

    def test_vm_21_quarter_miss_rate(self):
        """VM-21: miss_rate=0.25 → concentration = 1 - 2×0.25 = 0.5."""
        sig = _signals(miss_rate=0.25)
        assert VTSkillScorer.score_concentration(sig) == pytest.approx(0.5)

    def test_vm_22_half_miss_rate_boundary(self):
        """VM-22: miss_rate=0.5 → 1 - 2×0.5 = 0.0 (exactly at boundary)."""
        sig = _signals(miss_rate=0.5)
        assert VTSkillScorer.score_concentration(sig) == pytest.approx(0.0)

    def test_vm_23_single_miss_near_perfect(self):
        """VM-23: 1 miss out of 36 → miss_rate≈0.028 → concentration ≈ 0.944."""
        sig = _signals(miss_rate=1 / 36)
        score = VTSkillScorer.score_concentration(sig)
        assert score == pytest.approx(1.0 - 2.0 / 36, abs=0.001)
        assert score > 0.93


# ── TestAnticipationScore (VM-24..27) ─────────────────────────────────────────

class TestAnticipationScore:

    def test_vm_24_perfect_completion_and_hit_rate(self):
        """VM-24: completion=1.0, hit_rate=1.0, no per_phase → anticipation = 1.0."""
        sig = _signals(completion_rate=1.0, hit_rate=1.0, per_phase=None)
        assert VTSkillScorer.score_anticipation(sig) == pytest.approx(1.0)

    def test_vm_25_partial_completion_reduces_score(self):
        """VM-25: 28/36 completion × 0.9 hit_rate → anticipation ≈ 0.70."""
        sig = _signals(completion_rate=28 / 36, hit_rate=0.9, per_phase=None)
        expected = (28 / 36) * 0.9
        assert VTSkillScorer.score_anticipation(sig) == pytest.approx(expected, abs=0.001)

    def test_vm_26_zero_hit_rate_gives_zero(self):
        """VM-26: hit_rate=0.0, completion=1.0 → anticipation = 0."""
        sig = _signals(completion_rate=1.0, hit_rate=0.0, per_phase=None)
        assert VTSkillScorer.score_anticipation(sig) == pytest.approx(0.0)

    def test_vm_27_per_phase_high_phase3_boosts_anticipation(self):
        """VM-27: per_phase present with phase-3 accuracy=0.92 → uses 0.6×p3_acc path."""
        per_phase = [
            {"phase": 0, "stimuli": 12, "correct": 10, "wrong": 1, "miss": 1},
            {"phase": 1, "stimuli": 12, "correct": 10, "wrong": 1, "miss": 1},
            {"phase": 2, "stimuli": 12, "correct": 11, "wrong": 0, "miss": 1},  # 11/12 = 0.917
        ]
        sig = _signals(completion_rate=1.0, hit_rate=31 / 36, per_phase=per_phase)
        score = VTSkillScorer.score_anticipation(sig)
        p3_acc = 11 / 12
        expected = 0.4 * 1.0 * (31 / 36) + 0.6 * p3_acc
        assert score == pytest.approx(expected, abs=0.001)


# ── TestDeltaComputation (VM-28..33) ──────────────────────────────────────────

class TestDeltaComputation:

    def test_vm_28_perfect_scores_max_delta_calibration(self):
        """VM-28: All scores=1.0, base_xp=20, multiplier=1.0 → total delta = 2.0."""
        scores = {s: 1.0 for s in _SKILL_TARGETS}
        deltas = VTDeltaComputer.compute(scores, _SKILL_TARGETS, base_xp=20, multiplier=1.0)
        assert sum(deltas.values()) == pytest.approx(2.0, abs=0.01)
        assert set(deltas.keys()) == set(_SKILL_TARGETS.keys())

    def test_vm_29_zero_score_gives_negative_delta(self):
        """VM-29: All scores=0.0 (below NEUTRAL=0.45) → all deltas negative."""
        scores = {s: 0.0 for s in _SKILL_TARGETS}
        deltas = VTDeltaComputer.compute(scores, _SKILL_TARGETS, base_xp=20, multiplier=1.0)
        assert len(deltas) > 0
        for skill, delta in deltas.items():
            assert delta < 0, f"Expected negative delta for {skill}, got {delta}"

    def test_vm_30_zero_multiplier_returns_empty(self):
        """VM-30: multiplier=0.0 (4th+ attempt) → {} regardless of scores."""
        scores = {s: 1.0 for s in _SKILL_TARGETS}
        deltas = VTDeltaComputer.compute(scores, _SKILL_TARGETS, base_xp=20, multiplier=0.0)
        assert deltas == {}

    def test_vm_31_all_four_skills_present_in_result(self):
        """VM-31: scores > 0 for all 4 skills → all 4 keys in output."""
        scores = {"reactions": 0.8, "decisions": 0.7, "concentration": 0.9, "anticipation": 0.6}
        deltas = VTDeltaComputer.compute(scores, _SKILL_TARGETS, base_xp=20, multiplier=1.0)
        assert "reactions"     in deltas
        assert "decisions"     in deltas
        assert "concentration" in deltas
        assert "anticipation"  in deltas

    def test_vm_32_partial_skill_targets_two_skills_only(self):
        """VM-32: skill_targets with only 2 skills → only those 2 in output."""
        partial = {"reactions": 0.6, "decisions": 0.4}
        scores  = {"reactions": 0.8, "decisions": 0.5}
        deltas  = VTDeltaComputer.compute(scores, partial, base_xp=20, multiplier=1.0)
        assert set(deltas.keys()) <= {"reactions", "decisions"}
        assert "concentration" not in deltas

    def test_vm_33_attempt_index_2_multiplier_applied(self):
        """VM-33: multiplier=0.75 (attempt 2) → deltas at 75% of index-1 values."""
        scores = {s: 1.0 for s in _SKILL_TARGETS}
        d1 = VTDeltaComputer.compute(scores, _SKILL_TARGETS, base_xp=20, multiplier=1.0)
        d2 = VTDeltaComputer.compute(scores, _SKILL_TARGETS, base_xp=20, multiplier=0.75)
        for skill in d1:
            assert d2[skill] == pytest.approx(d1[skill] * 0.75, abs=0.001)


# ── TestScoreBlindnessEliminated (VM-34..36) ──────────────────────────────────

class TestScoreBlindnessEliminated:
    """Verify that same XP but different gameplay → different skill_deltas."""

    def _run(self, data: dict) -> dict:
        return compute_vt_skill_deltas(data=data, game=_mock_game(), multiplier=1.0)

    def test_vm_34_better_performance_higher_reactions_delta(self):
        """VM-34: Fast accurate run → higher reactions delta than slow inaccurate run."""
        delta_good = self._run({
            "stimuli_count": 36, "correct_count": 34,
            "wrong_click_count": 1, "error_count": 1, "avg_reaction_ms": 250.0,
        })
        delta_poor = self._run({
            "stimuli_count": 36, "correct_count": 20,
            "wrong_click_count": 8, "error_count": 8, "avg_reaction_ms": 1800.0,
        })
        assert delta_good.get("reactions", 0) > delta_poor.get("reactions", 0)
        # Sanity: both produce non-zero reactions delta
        assert delta_good.get("reactions", 0) > 0
        assert delta_poor.get("reactions", 0) >= 0

    def test_vm_35_wrong_clicks_reduce_decisions_delta(self):
        """VM-35: Many wrong clicks → lower decisions delta than clean run."""
        delta_clean = self._run({
            "stimuli_count": 36, "correct_count": 35,
            "wrong_click_count": 0, "error_count": 1, "avg_reaction_ms": 350.0,
        })
        delta_sloppy = self._run({
            "stimuli_count": 36, "correct_count": 25,
            "wrong_click_count": 8, "error_count": 3, "avg_reaction_ms": 350.0,
        })
        assert delta_clean.get("decisions", 0) > delta_sloppy.get("decisions", 0)

    def test_vm_36_many_misses_reduce_concentration_delta(self):
        """VM-36: High miss count → lower concentration delta than near-zero-miss run."""
        delta_focused = self._run({
            "stimuli_count": 36, "correct_count": 35,
            "wrong_click_count": 0, "error_count": 1, "avg_reaction_ms": 400.0,
        })
        delta_distracted = self._run({
            "stimuli_count": 36, "correct_count": 25,
            "wrong_click_count": 0, "error_count": 11, "avg_reaction_ms": 400.0,
        })
        assert delta_focused.get("concentration", 0) > delta_distracted.get("concentration", 0)


# ── TestLateClickDetection (LC-01..16) ───────────────────────────────────────
# raw_metrics v2 signal extraction and scorer behaviour.

class TestLateClickDetection:

    _BASE = {
        "stimuli_count": 36, "correct_count": 28,
        "wrong_click_count": 2, "error_count": 6, "avg_reaction_ms": 380.0,
    }

    @staticmethod
    def _v2_raw(late_click_count=0, late_go_count=0, late_no_go_count=0, per_phase=None):
        return {
            "v": 2,
            "per_stimulus": [],
            "per_color": {},
            "per_phase": per_phase or [],
            "late_summary": {
                "late_click_count":  late_click_count,
                "late_click_avg_ms": 200 if late_click_count else None,
                "late_click_max_ms": 450 if late_click_count else None,
                "late_go_count":     late_go_count,
                "late_no_go_count":  late_no_go_count,
            },
        }

    # ── Signal extraction ─────────────────────────────────────────────────────

    def test_lc_01_v2_late_click_count_sets_late_click_rate(self):
        """LC-01: v2 payload with 4 late clicks → late_click_rate = 4/36."""
        data = {**self._BASE, "raw_metrics": self._v2_raw(late_click_count=4)}
        sig = VTSignalExtractor.extract(data, _PHASE21_CONFIG)
        assert sig.late_click_rate == pytest.approx(4 / 36, abs=0.001)

    def test_lc_02_v2_late_go_and_nogo_rates_extracted_independently(self):
        """LC-02: 3 late GO + 2 late NO-GO → rates extracted separately."""
        data = {**self._BASE, "raw_metrics": self._v2_raw(
            late_click_count=5, late_go_count=3, late_no_go_count=2)}
        sig = VTSignalExtractor.extract(data, _PHASE21_CONFIG)
        assert sig.late_go_rate   == pytest.approx(3 / 36, abs=0.001)
        assert sig.late_nogo_rate == pytest.approx(2 / 36, abs=0.001)

    def test_lc_03_v1_payload_late_rates_default_to_zero(self):
        """LC-03: v1 raw_metrics (no late_summary) → all late rates = 0.0."""
        data = {**self._BASE, "raw_metrics": {"v": 1, "per_stimulus": [], "per_phase": []}}
        sig = VTSignalExtractor.extract(data, _PHASE21_CONFIG)
        assert sig.late_click_rate == 0.0
        assert sig.late_go_rate    == 0.0
        assert sig.late_nogo_rate  == 0.0

    def test_lc_04_v2_missing_late_summary_late_rates_default_to_zero(self):
        """LC-04: v2 raw_metrics but late_summary absent → late rates = 0.0."""
        data = {**self._BASE, "raw_metrics": {"v": 2, "per_stimulus": [], "per_phase": []}}
        sig = VTSignalExtractor.extract(data, _PHASE21_CONFIG)
        assert sig.late_click_rate == 0.0
        assert sig.late_nogo_rate  == 0.0

    def test_lc_05_late_click_rate_clamped_to_one(self):
        """LC-05: late_click_count > stimuli → late_click_rate clamped to 1.0."""
        data = {**self._BASE, "raw_metrics": self._v2_raw(late_click_count=50)}
        sig = VTSignalExtractor.extract(data, _PHASE21_CONFIG)
        assert sig.late_click_rate == pytest.approx(1.0)

    def test_lc_06_v2_per_phase_also_extracted(self):
        """LC-06: v=2 still extracts per_phase (≥1 condition handles both versions)."""
        pp = [{"phase": 0, "correct": 10}]
        data = {**self._BASE, "raw_metrics": self._v2_raw(late_click_count=2, per_phase=pp)}
        sig = VTSignalExtractor.extract(data, _PHASE21_CONFIG)
        assert sig.per_phase is not None
        assert sig.per_phase[0]["correct"] == 10

    # ── score_concentration v2 behaviour ─────────────────────────────────────

    def test_lc_07_concentration_v1_path_unchanged_when_no_late_clicks(self):
        """LC-07: v1-style signals (late_* = 0) → formula identical to old implementation."""
        sig = VTSignals(hit_rate=0.8, wrong_rate=0.05, miss_rate=0.15,
                        speed_score=0.8, completion_rate=1.0)
        score = VTSkillScorer.score_concentration(sig)
        expected = min(1.0, 1.0 - 2.0 * 0.15)
        assert score == pytest.approx(expected, abs=0.001)

    def test_lc_08_late_clicks_reduce_concentration_less_than_full_misses(self):
        """LC-08: Same total lapse rate split between miss+late → higher score than all-miss."""
        sig_all_miss = VTSignals(hit_rate=0.8, wrong_rate=0.0, miss_rate=0.2,
                                 speed_score=0.8, completion_rate=1.0)
        sig_mixed = VTSignals(hit_rate=0.8, wrong_rate=0.0, miss_rate=0.1,
                              speed_score=0.8, completion_rate=1.0,
                              late_click_rate=0.1, late_go_rate=0.1, late_nogo_rate=0.0)
        assert VTSkillScorer.score_concentration(sig_mixed) > VTSkillScorer.score_concentration(sig_all_miss)

    def test_lc_09_late_nogo_clicks_excluded_from_concentration_penalty(self):
        """LC-09: Late NO-GO clicks attributed to composure, not concentration."""
        sig = VTSignals(hit_rate=0.8, wrong_rate=0.0, miss_rate=0.1,
                        speed_score=0.8, completion_rate=1.0,
                        late_click_rate=0.1, late_go_rate=0.0, late_nogo_rate=0.1)
        score = VTSkillScorer.score_concentration(sig)
        # late_for_conc = max(0, 0.1 − 0.1) = 0.0 → pure_miss = 0.1, no late penalty
        expected = min(1.0, 1.0 - 2.0 * 0.1)
        assert score == pytest.approx(expected, abs=0.001)

    def test_lc_10_concentration_pure_miss_does_not_go_negative(self):
        """LC-10: late_click_rate > miss_rate → pure_miss clamped to 0, no double-count."""
        sig = VTSignals(hit_rate=0.9, wrong_rate=0.0, miss_rate=0.05,
                        speed_score=0.9, completion_rate=1.0,
                        late_click_rate=0.1, late_go_rate=0.1, late_nogo_rate=0.0)
        score = VTSkillScorer.score_concentration(sig)
        # pure_miss = max(0, 0.05 − 0.1) = 0.0 → late_for_conc = 0.1 only
        expected = min(1.0, 1.0 - 0.8 * 0.1)
        assert score == pytest.approx(expected, abs=0.001)

    # ── score_composure v2 behaviour ──────────────────────────────────────────

    def test_lc_11_composure_v1_path_unchanged_when_no_late_nogo(self):
        """LC-11: v1-style signals (late_nogo_rate=0) → composure = 1 − 1.5 × wrong_rate."""
        sig = VTSignals(hit_rate=0.8, wrong_rate=0.1, miss_rate=0.1,
                        speed_score=0.8, completion_rate=1.0)
        score = VTSkillScorer.score_composure(sig)
        assert score == pytest.approx(1.0 - 1.5 * 0.1, abs=0.001)

    def test_lc_12_late_nogo_clicks_reduce_composure(self):
        """LC-12: late_nogo_rate > 0 → composure lower than identical run without late NO-GO."""
        sig_clean = VTSignals(hit_rate=0.8, wrong_rate=0.05, miss_rate=0.15,
                              speed_score=0.8, completion_rate=1.0)
        sig_late  = VTSignals(hit_rate=0.8, wrong_rate=0.05, miss_rate=0.15,
                              speed_score=0.8, completion_rate=1.0,
                              late_click_rate=0.1, late_go_rate=0.0, late_nogo_rate=0.1)
        assert VTSkillScorer.score_composure(sig_late) < VTSkillScorer.score_composure(sig_clean)

    def test_lc_13_late_go_clicks_do_not_affect_composure(self):
        """LC-13: late GO clicks (late_go_rate only) → composure unchanged."""
        sig_no_late = VTSignals(hit_rate=0.8, wrong_rate=0.05, miss_rate=0.1,
                                speed_score=0.8, completion_rate=1.0)
        sig_late_go = VTSignals(hit_rate=0.8, wrong_rate=0.05, miss_rate=0.1,
                                speed_score=0.8, completion_rate=1.0,
                                late_click_rate=0.1, late_go_rate=0.1, late_nogo_rate=0.0)
        assert VTSkillScorer.score_composure(sig_no_late) == pytest.approx(
            VTSkillScorer.score_composure(sig_late_go), abs=0.001)

    # ── End-to-end ────────────────────────────────────────────────────────────

    def test_lc_14_e2e_v2_late_clicks_lower_concentration_delta(self):
        """LC-14: v2 clean run (no misses/late) beats run with late GO clicks on concentration."""
        _perfect_base = {
            "stimuli_count": 36, "correct_count": 36,
            "wrong_click_count": 0, "error_count": 0, "avg_reaction_ms": 280.0,
        }

        def _run(raw_metrics):
            return compute_vt_skill_deltas(
                data={**_perfect_base, "raw_metrics": raw_metrics},
                game=_mock_game(skill_targets={"concentration": 1.0}),
                multiplier=1.0,
            )
        clean = _run(self._v2_raw(late_click_count=0))
        # Inject 6 late GO clicks; error_count stays 0 (no pure misses)
        late  = _run(self._v2_raw(late_click_count=6, late_go_count=6))
        assert clean.get("concentration", 0) >= late.get("concentration", 0)

    def test_lc_15_e2e_v2_late_nogo_lower_composure_delta(self):
        """LC-15: v2 run with late NO-GO clicks → composure delta ≤ clean run."""
        def _run(raw_metrics):
            return compute_vt_skill_deltas(
                data={
                    "stimuli_count": 30, "correct_count": 20,
                    "wrong_click_count": 2, "error_count": 5, "avg_reaction_ms": 500.0,
                    "raw_metrics": raw_metrics,
                },
                game=_mock_game(skill_targets={"composure": 1.0}),
                multiplier=1.0,
            )
        clean = _run(self._v2_raw(late_click_count=0))
        late  = _run(self._v2_raw(late_click_count=4, late_no_go_count=4))
        assert clean.get("composure", 0) >= late.get("composure", 0)

    def test_lc_16_v1_backward_compat_concentration_formula_identical(self):
        """LC-16: v1 payload → late_* = 0.0, concentration formula reduces to original."""
        data = {**self._BASE, "raw_metrics": {"v": 1, "per_stimulus": [], "per_phase": []}}
        sig = VTSignalExtractor.extract(data, _PHASE21_CONFIG)
        assert sig.late_click_rate == 0.0
        assert sig.late_go_rate    == 0.0
        assert sig.late_nogo_rate  == 0.0
        score    = VTSkillScorer.score_concentration(sig)
        expected = min(1.0, 1.0 - 2.0 * sig.miss_rate)
        assert score == pytest.approx(expected, abs=0.001)
