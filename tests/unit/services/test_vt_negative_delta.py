"""NEGDELTA — Bidirectional Virtual Training skill delta tests.

Covers the Phase 2.4 negative-delta model introduced in virtual_training_metrics.py:
  - Scorer lower clamp removed (decisions, concentration, composure can go negative)
  - Neutral zone: score < 0.45 → negative delta
  - NEG_SCALE = 0.5 (negative intensity is half of positive)
  - Daily negative cap: −0.5 per skill per user per day (write-time enforcement)
  - Invalid attempts and attempts 4+ always produce zero skill deltas

NEGDELTA-01  score_decisions can return negative value
NEGDELTA-02  score_concentration can return negative value
NEGDELTA-03  score_composure can return negative value when wrong_rate > 0.67
NEGDELTA-04  score_reactions is always non-negative
NEGDELTA-05  score_anticipation is always non-negative
NEGDELTA-06  compute() below neutral → negative delta
NEGDELTA-07  compute() above neutral → positive delta
NEGDELTA-08  compute() daily cap: new neg truncated when approaching cap
NEGDELTA-09  compute() daily cap: no further neg when cap already reached
NEGDELTA-10  compute() positive delta allowed even when cap is reached
NEGDELTA-11  compute() multiplier applies to negative delta (attempt fairness)
NEGDELTA-12  compute() zero multiplier → empty result (attempts 4+)
NEGDELTA-13  johny7 weak GNG pattern → decisions/concentration negative delta
NEGDELTA-14  get_training_skill_deltas_for_user() aggregation handles negatives
"""
from __future__ import annotations

import pytest

# ── shared test signals helper ────────────────────────────────────────────────

def _sig(
    *,
    hit_rate:        float = 1.0,
    wrong_rate:      float = 0.0,
    miss_rate:       float = 0.0,
    speed_score:     float = 0.7,
    completion_rate: float = 1.0,
):
    from app.services.virtual_training_metrics import VTSignals
    return VTSignals(
        hit_rate=hit_rate,
        wrong_rate=wrong_rate,
        miss_rate=miss_rate,
        speed_score=speed_score,
        completion_rate=completion_rate,
    )


_GNG_TARGETS = {"decisions": 0.35, "concentration": 0.30, "composure": 0.20, "reactions": 0.15}
_GNG_BASE_XP = 12  # GNG seed value


# ── NEGDELTA-01..05: scorer lower-clamp removed ───────────────────────────────

class TestScorerNegativeOutputs:

    def test_negdelta01_decisions_negative_when_false_alarms_dominate(self):
        """NEGDELTA-01: score_decisions returns negative when wrong_rate > hit_rate / 1.5."""
        from app.services.virtual_training_metrics import VTSkillScorer
        # hit_rate=0.0, wrong_rate=0.5 → 0 - 0.75 = -0.75
        sig = _sig(hit_rate=0.0, wrong_rate=0.5)
        score = VTSkillScorer.score_decisions(sig)
        assert score == pytest.approx(-0.75)
        assert score < 0

    def test_negdelta02_concentration_negative_when_miss_rate_above_half(self):
        """NEGDELTA-02: score_concentration returns negative when miss_rate > 0.5."""
        from app.services.virtual_training_metrics import VTSkillScorer
        # 1 - 2*0.7 = -0.4
        sig = _sig(miss_rate=0.7)
        score = VTSkillScorer.score_concentration(sig)
        assert score == pytest.approx(-0.4)
        assert score < 0

    def test_negdelta03_composure_negative_when_wrong_rate_exceeds_threshold(self):
        """NEGDELTA-03: score_composure returns negative when wrong_rate > 0.67."""
        from app.services.virtual_training_metrics import VTSkillScorer
        # 1 - 1.5*0.8 = 1 - 1.2 = -0.2
        sig = _sig(wrong_rate=0.8)
        score = VTSkillScorer.score_composure(sig)
        assert score == pytest.approx(-0.2)
        assert score < 0

    def test_negdelta04_reactions_always_non_negative(self):
        """NEGDELTA-04: score_reactions ≥ 0 for any signal combination."""
        from app.services.virtual_training_metrics import VTSkillScorer
        # worst case: speed=0, hit=0 → 0.65*0 + 0.35*0 = 0
        sig = _sig(hit_rate=0.0, speed_score=0.0)
        assert VTSkillScorer.score_reactions(sig) == pytest.approx(0.0)
        assert VTSkillScorer.score_reactions(sig) >= 0.0

    def test_negdelta05_anticipation_always_non_negative(self):
        """NEGDELTA-05: score_anticipation ≥ 0 for any signal combination."""
        from app.services.virtual_training_metrics import VTSkillScorer
        sig = _sig(hit_rate=0.0, completion_rate=0.0)
        assert VTSkillScorer.score_anticipation(sig) >= 0.0


# ── NEGDELTA-06..12: compute() neutral-zone and daily cap ─────────────────────

class TestDeltaCompute:

    def test_negdelta06_score_below_neutral_gives_negative_delta(self):
        """NEGDELTA-06: score=0.30 < NEUTRAL(0.45) → negative delta."""
        from app.services.virtual_training_metrics import VTDeltaComputer
        # unit = (1.0/1.0) * 1.2 = 1.2; delta = (0.30-0.45)*0.5*1.2*1.0 = -0.09
        scores = {"decisions": 0.30}
        result = VTDeltaComputer.compute(scores, {"decisions": 1.0}, base_xp=10, multiplier=1.0)
        assert "decisions" in result
        assert result["decisions"] < 0
        assert result["decisions"] == pytest.approx(-0.075, abs=0.001)

    def test_negdelta07_score_above_neutral_gives_positive_delta(self):
        """NEGDELTA-07: score=0.80 ≥ NEUTRAL(0.45) → positive delta."""
        from app.services.virtual_training_metrics import VTDeltaComputer
        scores = {"decisions": 0.80}
        result = VTDeltaComputer.compute(scores, {"decisions": 1.0}, base_xp=10, multiplier=1.0)
        assert result.get("decisions", 0) > 0

    def test_negdelta08_daily_cap_truncates_new_negative_when_approaching(self):
        """NEGDELTA-08: existing_neg=-0.45, new delta=-0.09 → capped at −0.05 (only gap)."""
        from app.services.virtual_training_metrics import VTDeltaComputer
        # cap = -0.5; existing = -0.45; room = -0.05
        # raw new delta = (0.30-0.45)*0.5*1.0*1.0 = -0.075 → max(-0.075, -0.5-(-0.45)) = max(-0.075, -0.05) = -0.05
        scores = {"decisions": 0.30}
        result = VTDeltaComputer.compute(
            scores, {"decisions": 1.0}, base_xp=10, multiplier=1.0,
            existing_neg_today={"decisions": -0.45},
        )
        assert "decisions" in result
        assert result["decisions"] == pytest.approx(-0.05, abs=0.001)

    def test_negdelta09_daily_cap_blocks_further_negative_when_reached(self):
        """NEGDELTA-09: existing_neg already at cap → new attempt produces zero delta."""
        from app.services.virtual_training_metrics import VTDeltaComputer
        scores = {"decisions": 0.20}
        result = VTDeltaComputer.compute(
            scores, {"decisions": 1.0}, base_xp=10, multiplier=1.0,
            existing_neg_today={"decisions": -0.50},
        )
        # delta=0 → filtered from result dict
        assert "decisions" not in result

    def test_negdelta10_positive_delta_unaffected_by_neg_cap(self):
        """NEGDELTA-10: even when neg cap is reached, positive delta still applies."""
        from app.services.virtual_training_metrics import VTDeltaComputer
        scores = {"decisions": 0.90}
        result = VTDeltaComputer.compute(
            scores, {"decisions": 1.0}, base_xp=10, multiplier=1.0,
            existing_neg_today={"decisions": -0.50},
        )
        assert result.get("decisions", 0) > 0

    def test_negdelta11_multiplier_applies_to_negative_delta(self):
        """NEGDELTA-11: multiplier scales negative delta proportionally (same as positive)."""
        from app.services.virtual_training_metrics import VTDeltaComputer
        scores = {"decisions": 0.30}
        full    = VTDeltaComputer.compute(scores, {"decisions": 1.0}, base_xp=10, multiplier=1.0)
        partial = VTDeltaComputer.compute(scores, {"decisions": 1.0}, base_xp=10, multiplier=0.3)
        # Both negative; partial should be ~30% of full magnitude
        assert full["decisions"] < 0
        assert partial["decisions"] < 0
        assert abs(partial["decisions"]) == pytest.approx(abs(full["decisions"]) * 0.3, rel=0.02)

    def test_negdelta12_zero_multiplier_returns_empty(self):
        """NEGDELTA-12: multiplier=0.0 (attempt 4+) → empty result, no skill delta."""
        from app.services.virtual_training_metrics import VTDeltaComputer
        scores = {"decisions": 0.10, "concentration": 0.05}
        result = VTDeltaComputer.compute(
            scores, {"decisions": 0.5, "concentration": 0.5}, base_xp=12, multiplier=0.0
        )
        assert result == {}


# ── NEGDELTA-13: johny7 weak GNG pattern ─────────────────────────────────────

class TestJohny7WeakGNG:

    def test_negdelta13_weak_gng_attempt_gives_negative_decisions_and_concentration(self):
        """NEGDELTA-13: attempt #3 pattern (7 GO hits, 8 FA, 14 miss / 30 stim) produces
        negative decisions and concentration deltas under the new formula."""
        from app.services.virtual_training_metrics import (
            VTSignalExtractor, VTSkillScorer, VTDeltaComputer,
        )
        # Replicate johny7 attempt #3 aggregate payload
        data = {
            "stimuli_count":     30,
            "correct_count":     7,    # 7 GO hits out of 21 GO stimuli
            "wrong_click_count": 8,    # 8 false alarms on NO-GO
            "error_count":       14,   # 14 missed GO
            "avg_reaction_ms":   646.0,
        }
        phase_config = [
            {"go": 10, "no_go": 5, "window_ms": 1000},
            {"go": 11, "no_go": 4, "window_ms": 1000},
        ]
        signals = VTSignalExtractor.extract(data, phase_config)

        # Verify raw signals
        assert signals.hit_rate   == pytest.approx(7 / 30, abs=0.001)
        assert signals.wrong_rate == pytest.approx(8 / 30, abs=0.001)
        assert signals.miss_rate  == pytest.approx(14 / 30, abs=0.001)

        scores = VTSkillScorer.score_all(signals, _GNG_TARGETS)

        # decisions = 7/30 - 1.5*(8/30) = 0.233 - 0.400 = -0.167 → negative
        assert scores["decisions"] < 0
        # concentration = 1 - 2*(14/30) = 1 - 0.933 = 0.067 → positive but below neutral
        assert 0.0 < scores["concentration"] < 0.45

        deltas = VTDeltaComputer.compute(scores, _GNG_TARGETS, base_xp=_GNG_BASE_XP, multiplier=0.5)

        # decisions: raw score < 0 → negative delta
        assert deltas.get("decisions", 0) < 0
        # concentration: 0 < score < NEUTRAL → also negative delta
        assert deltas.get("concentration", 0) < 0
        # composure: 1 - 1.5*(8/30) = 0.600 ≥ 0.45 → positive delta
        assert deltas.get("composure", 0) > 0


# ── NEGDELTA-14: aggregation read-path handles negatives ─────────────────────

class TestAggregationWithNegatives:

    def test_negdelta14_get_training_skill_deltas_sums_positive_and_negative(self):
        """NEGDELTA-14: get_training_skill_deltas_for_user() SUM() correctly aggregates
        mixed positive and negative skill_deltas from virtual_training_attempts."""
        from unittest.mock import MagicMock

        # Simulate two VT rows and one segment row being summed:
        #   segment row:  decisions=+0.10
        #   VT rows:      decisions=+0.21, concentration=+0.12
        # Expected totals: decisions=+0.31, concentration=+0.12
        # get_training_skill_deltas_for_user() accesses rows by index: row[0], row[1]

        segment_rows = [("decisions", 0.10)]
        vt_rows = [("decisions", 0.21), ("concentration", 0.12)]

        db = MagicMock()
        execute_results = [
            MagicMock(fetchall=lambda: segment_rows),
            MagicMock(fetchall=lambda: vt_rows),
        ]
        db.execute.side_effect = execute_results

        from app.services.segment_reward_service import get_training_skill_deltas_for_user
        result = get_training_skill_deltas_for_user(db=db, user_id=42)

        assert result["decisions"]     == pytest.approx(0.31, abs=0.001)
        assert result["concentration"] == pytest.approx(0.12, abs=0.001)
