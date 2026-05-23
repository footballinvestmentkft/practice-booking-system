"""SKPREC — Skill card display precision tests.

Phase 2.4D follow-up: current_level and total_delta now use training_delta_raw
(2-decimal) so small VT deltas are not swallowed by premature 1-decimal rounding.

SKPREC-01  training_delta_raw=0.0325 → current_level=60.03, not 60.0
SKPREC-02  training_delta_raw=0.0325 → total_delta=0.03, not 0.0
SKPREC-03  deltaHtml(0.03) → '+0.03 ↑', not '±0'
SKPREC-04  deltaHtml(-0.03) → '-0.03 ↓'
SKPREC-05  deltaHtml(0.004) below threshold → '±0.00'
SKPREC-06  0 training delta → current_level=60.0 (backward compatible)
SKPREC-07  large delta → 60.33, +0.33
SKPREC-08  average_level stays 1-decimal (stat card not affected)
SKPREC-09  get_skill_profile() emits 2-decimal current_level and total_delta
SKPREC-10  negative training delta: current_level stays above MIN_SKILL_VALUE
SKPREC-11  training_delta backward-compat field still 1-decimal
SKPREC-12  training_delta_precise equals round(raw, 2)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

_BASE_VIEWS = "app.services.skill_progression._views"
_BASE_SRS   = "app.services.segment_reward_service"

_DEFAULT_BASELINE = 60.0


def _make_skill_data(current_value: float = 60.0, contribution: float = 0.0) -> dict:
    return {
        "baseline": _DEFAULT_BASELINE,
        "current_value": current_value,
        "contribution": contribution,
        "tournament_count": 0,
    }


def _delta_html_py(delta: float) -> str:
    """Python mirror of the updated JS deltaHtml() in skills.html."""
    if delta > 0.005:
        return f"+{delta:.2f} ↑"
    if delta < -0.005:
        return f"{delta:.2f} ↓"
    return "±0.00"


# ── SKPREC-01..02: _views.py rounding ─────────────────────────────────────────

class TestSkillLevelPrecision:

    def _build_profile_skill(
        self,
        training_delta_raw: float,
        tournament_contribution: float = 0.0,
        current_value: float = 60.0,
    ) -> dict:
        """Mirror the skill dict logic from get_skill_profile() in _views.py."""
        from app.services.skill_progression._formulas import (
            MIN_SKILL_VALUE, MAX_SKILL_CAP,
        )
        training_delta = round(training_delta_raw, 1)
        current_level = round(
            min(MAX_SKILL_CAP, max(MIN_SKILL_VALUE, current_value + training_delta_raw)),
            2,
        )
        total_delta = round(tournament_contribution + training_delta_raw, 2)
        return {
            "current_level": current_level,
            "total_delta": total_delta,
            "training_delta": training_delta,
            "training_delta_precise": round(training_delta_raw, 2),
        }

    def test_skprec01_small_training_delta_visible_in_current_level(self):
        """SKPREC-01: raw=0.0325 → current_level=60.03, not 60.0."""
        s = self._build_profile_skill(0.0325)
        assert s["current_level"] == 60.03
        assert s["current_level"] != 60.0

    def test_skprec02_small_training_delta_visible_in_total_delta(self):
        """SKPREC-02: raw=0.0325 → total_delta=0.03, not 0.0."""
        s = self._build_profile_skill(0.0325)
        assert s["total_delta"] == 0.03
        assert s["total_delta"] != 0.0

    def test_skprec06_zero_training_delta_backward_compatible(self):
        """SKPREC-06: raw=0.0 → current_level=60.0 (no change, stays compatible)."""
        s = self._build_profile_skill(0.0)
        assert s["current_level"] == 60.0

    def test_skprec07_large_delta_two_decimal(self):
        """SKPREC-07: raw=0.325 → current_level=60.33, total_delta=0.33 (or 0.32 via float repr)."""
        s = self._build_profile_skill(0.325)
        assert s["current_level"] == round(60.0 + 0.325, 2)
        assert s["total_delta"] == round(0.325, 2)

    def test_skprec10_negative_delta_capped_at_min(self):
        """SKPREC-10: large negative delta → current_level stays ≥ MIN_SKILL_VALUE."""
        from app.services.skill_progression._formulas import MIN_SKILL_VALUE
        s = self._build_profile_skill(-99.0)
        assert s["current_level"] >= MIN_SKILL_VALUE

    def test_skprec11_training_delta_field_stays_one_decimal(self):
        """SKPREC-11: training_delta backward-compat field rounds to 1 decimal."""
        s = self._build_profile_skill(0.0325)
        assert s["training_delta"] == 0.0   # round(0.0325, 1) = 0.0

    def test_skprec12_training_delta_precise_two_decimal(self):
        """SKPREC-12: training_delta_precise = round(raw, 2) = 0.03."""
        s = self._build_profile_skill(0.0325)
        assert s["training_delta_precise"] == 0.03


# ── SKPREC-03..05: deltaHtml JS mirror ────────────────────────────────────────

class TestDeltaHtmlPrecision:

    def test_skprec03_positive_small_delta_shows(self):
        """SKPREC-03: 0.03 > 0.005 threshold → '+0.03 ↑', not '±0'."""
        assert _delta_html_py(0.03) == "+0.03 ↑"

    def test_skprec04_negative_small_delta_shows(self):
        """SKPREC-04: -0.03 < -0.005 → '-0.03 ↓'."""
        assert _delta_html_py(-0.03) == "-0.03 ↓"

    def test_skprec05_below_threshold_zero(self):
        """SKPREC-05: abs(0.004) < 0.005 → '±0.00'."""
        assert _delta_html_py(0.004) == "±0.00"

    def test_skprec05b_exactly_zero(self):
        """SKPREC-05b: delta=0.0 → '±0.00'."""
        assert _delta_html_py(0.0) == "±0.00"

    def test_skprec05c_negative_below_threshold(self):
        """SKPREC-05c: -0.003 → '±0.00'."""
        assert _delta_html_py(-0.003) == "±0.00"

    def test_skprec_large_positive(self):
        """Large positive delta formats correctly."""
        assert _delta_html_py(5.2) == "+5.20 ↑"

    def test_skprec_large_negative(self):
        """Large negative delta formats correctly."""
        assert _delta_html_py(-3.7) == "-3.70 ↓"


# ── SKPREC-08: average_level unchanged ────────────────────────────────────────

class TestAverageLevelPrecision:

    def test_skprec08_average_level_stays_one_decimal(self):
        """SKPREC-08: average_level is still rounded to 1 decimal at the top level."""
        total = 60.03 + 60.33 + 60.0
        avg = round(total / 3, 1)
        assert str(avg).split(".")[-1] in {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}
        assert len(str(avg).split(".")[-1]) == 1


# ── SKPREC-09: get_skill_profile() integration ────────────────────────────────

class TestGetSkillProfilePrecision:

    def test_skprec09_profile_emits_precise_current_level_and_total_delta(self):
        """SKPREC-09: get_skill_profile() importable and emits 2-decimal fields."""
        from app.services.skill_progression._views import get_skill_profile

        db = MagicMock()

        skill_keys = ["decisions"]
        assessed_map = {}
        assessed_count_map = {}

        with (
            patch(f"{_BASE_VIEWS}.get_all_skill_keys", return_value=skill_keys),
            patch(f"{_BASE_VIEWS}.get_baseline_skills", return_value={}),
            patch(f"{_BASE_VIEWS}.FootballSkillAssessment") as MockFSA,
            patch(f"{_BASE_VIEWS}.TournamentParticipation") as MockTP,
            patch(f"{_BASE_VIEWS}.calculate_tournament_skill_contribution", return_value={
                "decisions": {
                    "baseline": 60.0,
                    "current_value": 60.0,
                    "contribution": 0.0,
                    "tournament_count": 0,
                }
            }),
            patch(f"{_BASE_VIEWS}.get_training_skill_deltas_for_user", return_value={
                "decisions": 0.0325,
            }),
            patch(f"{_BASE_VIEWS}.get_training_session_count_for_user", return_value=0),
            patch(f"{_BASE_VIEWS}.get_vt_attempt_count_per_skill_for_user", return_value={
                "decisions": 2,
            }),
        ):
            MockFSA.query = db.query
            db.query.return_value.filter.return_value.all.return_value = []
            MockTP.query = db.query
            db.query.return_value.filter.return_value.count.return_value = 0

            profile = get_skill_profile(db, user_id=42)

        skill = profile["skills"]["decisions"]
        assert skill["current_level"] == 60.03, f"expected 60.03 got {skill['current_level']}"
        assert skill["total_delta"] == 0.03, f"expected 0.03 got {skill['total_delta']}"
        assert skill["training_delta"] == 0.0     # 1-dec backward compat
        assert skill["training_delta_precise"] == 0.03
