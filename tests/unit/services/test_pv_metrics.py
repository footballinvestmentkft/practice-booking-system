"""Peripheral Vision metrics tests.

PVM-01  score_tactical_awareness: PV path activates when pv_far_hit_rate present
PVM-02  score_tactical_awareness PV: far=1.0, mid=0, near=0 → 0.40
PVM-03  score_tactical_awareness PV: all=1.0 → 1.0
PVM-04  score_tactical_awareness PV: all=0.0 → 0.0
PVM-05  score_tactical_awareness PV: missing mid/near defaults to 0
PVM-06  VTSignalExtractor.extract sets pv_*_hit_rate from raw_metrics.per_zone
PVM-07  VTSignalExtractor.extract: no per_zone → pv_* all None (fallback path safe)
PVM-08  VTSignalExtractor.extract: per_zone empty dict → pv_* all None
PVM-09  score_tactical_awareness: Memory Sequence path unaffected (no pv_ fields)
PVM-10  score_tactical_awareness: general fallback unaffected when no pv_ or per_phase
PVM-11  PV signals → skill_deltas positive when tactical_awareness score > 0.45
PVM-12  PV signals → skill_deltas negative when tactical_awareness score < 0.45
PVM-13  zone hit rate: empty zone (0 attempts) → None (not 0.0)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.services.virtual_training_metrics import (
    VTSignalExtractor,
    VTSkillScorer,
    VTSignals,
    VTDeltaComputer,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _signals(**kw) -> VTSignals:
    defaults = dict(
        hit_rate=0.8, wrong_rate=0.05, miss_rate=0.15,
        speed_score=0.6, completion_rate=1.0,
    )
    defaults.update(kw)
    return VTSignals(**defaults)


def _extract_pv(per_zone: dict | None, hits=30, total=42, avg_ms=680):
    data = {
        "stimuli_count":     total,
        "correct_count":     hits,
        "error_count":       total - hits - 4,
        "wrong_click_count": 4,
        "avg_reaction_ms":   avg_ms,
        "raw_metrics": {
            "v": 3,
            "per_zone": per_zone,
            "hand_profile": {"protocol_difficulty_multiplier": 1.0},
        },
    }
    phases = [
        {"zone": "near", "stimuli": 14, "window_ms": 1200},
        {"zone": "mid",  "stimuli": 14, "window_ms": 900},
        {"zone": "far",  "stimuli": 14, "window_ms": 700},
    ]
    return VTSignalExtractor.extract(data, phases)


# ── PVM-01..05: score_tactical_awareness PV path ──────────────────────────────

class TestTacticalAwarenessPVPath:

    def test_pvm01_pv_path_activates_when_far_present(self):
        """PVM-01: PV path activates when pv_far_hit_rate is not None."""
        sig = _signals(pv_near_hit_rate=0.85, pv_mid_hit_rate=0.70, pv_far_hit_rate=0.55)
        score = VTSkillScorer.score_tactical_awareness(sig)
        # PV formula: 0.40×0.55 + 0.35×0.70 + 0.25×0.85 = 0.22+0.245+0.2125 = 0.6775
        assert abs(score - 0.6775) < 0.001

    def test_pvm02_far_only_gives_040(self):
        """PVM-02: far=1.0, mid=0, near=0 → 0.40."""
        sig = _signals(pv_near_hit_rate=0.0, pv_mid_hit_rate=0.0, pv_far_hit_rate=1.0)
        assert abs(VTSkillScorer.score_tactical_awareness(sig) - 0.40) < 0.001

    def test_pvm03_all_perfect_gives_1_0(self):
        """PVM-03: all zones = 1.0 → score = 1.0."""
        sig = _signals(pv_near_hit_rate=1.0, pv_mid_hit_rate=1.0, pv_far_hit_rate=1.0)
        assert abs(VTSkillScorer.score_tactical_awareness(sig) - 1.0) < 0.001

    def test_pvm04_all_zero_gives_0_0(self):
        """PVM-04: all zones = 0.0 → score = 0.0."""
        sig = _signals(pv_near_hit_rate=0.0, pv_mid_hit_rate=0.0, pv_far_hit_rate=0.0)
        assert VTSkillScorer.score_tactical_awareness(sig) == 0.0

    def test_pvm05_missing_mid_near_default_to_zero(self):
        """PVM-05: mid=None, near=None → treated as 0 in PV formula."""
        sig = _signals(pv_near_hit_rate=None, pv_mid_hit_rate=None, pv_far_hit_rate=0.8)
        score = VTSkillScorer.score_tactical_awareness(sig)
        # 0.40×0.8 + 0.35×0 + 0.25×0 = 0.32
        assert abs(score - 0.32) < 0.001


# ── PVM-06..08: VTSignalExtractor PV zone extraction ─────────────────────────

class TestSignalExtractorPV:

    def test_pvm06_per_zone_populates_pv_fields(self):
        """PVM-06: per_zone present → pv_*_hit_rate extracted correctly."""
        per_zone = {
            "near": {"hits": 12, "misses": 1, "wrong_clicks": 1},
            "mid":  {"hits": 10, "misses": 3, "wrong_clicks": 1},
            "far":  {"hits": 8,  "misses": 4, "wrong_clicks": 2},
        }
        sig = _extract_pv(per_zone)
        assert sig.pv_near_hit_rate is not None
        assert sig.pv_mid_hit_rate  is not None
        assert sig.pv_far_hit_rate  is not None
        # near: 12/(12+1+1) = 12/14 ≈ 0.857
        assert abs(sig.pv_near_hit_rate - 12/14) < 0.001
        assert abs(sig.pv_mid_hit_rate  - 10/14) < 0.001
        assert abs(sig.pv_far_hit_rate  - 8/14)  < 0.001

    def test_pvm07_no_per_zone_gives_none(self):
        """PVM-07: no per_zone key → pv_* all None → fallback path safe."""
        sig = _extract_pv(None)
        assert sig.pv_near_hit_rate is None
        assert sig.pv_mid_hit_rate  is None
        assert sig.pv_far_hit_rate  is None

    def test_pvm08_empty_per_zone_gives_none(self):
        """PVM-08: per_zone={} → pv_* all None."""
        sig = _extract_pv({})
        assert sig.pv_far_hit_rate is None


# ── PVM-09..10: Other game paths unaffected ───────────────────────────────────

class TestOtherGamePathsUnaffected:

    def test_pvm09_memory_sequence_path_still_works(self):
        """PVM-09: Memory Sequence per_phase path unaffected by PV changes."""
        sig = _signals(
            completion_rate=0.9,
            per_phase=[
                {"correct_positions": 5, "total_positions": 5},
                {"correct_positions": 6, "total_positions": 6},
                {"correct_positions": 5, "total_positions": 7},
            ],
        )
        score = VTSkillScorer.score_tactical_awareness(sig)
        # Phase 3: 5/7 ≈ 0.714; score = 0.4×0.9×0.8 + 0.6×0.714 ≈ 0.288 + 0.429 = 0.717
        assert 0.60 < score < 0.85  # reasonable range, not the PV formula

    def test_pvm10_general_fallback_works_without_pv_or_phase(self):
        """PVM-10: General fallback (no pv_, no per_phase) uses hit+completion."""
        sig = _signals(hit_rate=0.8, completion_rate=1.0)
        score = VTSkillScorer.score_tactical_awareness(sig)
        # 0.65×0.8 + 0.35×1.0 = 0.52 + 0.35 = 0.87
        assert abs(score - 0.87) < 0.001


# ── PVM-11..12: Skill delta direction ─────────────────────────────────────────

class TestPVSkillDeltaDirection:

    def _run_delta(self, far_hit, mid_hit, near_hit):
        sig = _signals(
            pv_near_hit_rate=near_hit,
            pv_mid_hit_rate=mid_hit,
            pv_far_hit_rate=far_hit,
            speed_score=0.5,
        )
        skill_targets = {"tactical_awareness": 0.35, "reactions": 0.25,
                         "concentration": 0.25, "anticipation": 0.15}
        scores = VTSkillScorer.score_all(sig, skill_targets)
        deltas = VTDeltaComputer.compute(
            scores=scores,
            skill_targets=skill_targets,
            base_xp=12,
            multiplier=1.0,
            existing_neg_today={},
        )
        return deltas

    def test_pvm11_positive_delta_when_score_above_neutral(self):
        """PVM-11: High zone accuracy → tactical_awareness delta positive."""
        deltas = self._run_delta(far_hit=0.9, mid_hit=0.85, near_hit=0.95)
        assert deltas.get("tactical_awareness", 0) > 0

    def test_pvm12_negative_delta_when_score_below_neutral(self):
        """PVM-12: Low zone accuracy → tactical_awareness delta negative."""
        deltas = self._run_delta(far_hit=0.1, mid_hit=0.15, near_hit=0.2)
        assert deltas.get("tactical_awareness", 0) < 0


# ── PVM-13: zone hit rate edge case ──────────────────────────────────────────

class TestZoneHitRateEdgeCases:

    def test_pvm13_zero_total_zone_gives_none(self):
        """PVM-13: Zone with 0 total attempts → None (not 0.0)."""
        per_zone = {
            "near": {"hits": 0, "misses": 0, "wrong_clicks": 0},
            "mid":  {"hits": 5, "misses": 2, "wrong_clicks": 1},
            "far":  {"hits": 3, "misses": 4, "wrong_clicks": 2},
        }
        sig = _extract_pv(per_zone)
        assert sig.pv_near_hit_rate is None   # 0/0 → None, not 0.0
        assert sig.pv_mid_hit_rate  is not None
        assert sig.pv_far_hit_rate  is not None
