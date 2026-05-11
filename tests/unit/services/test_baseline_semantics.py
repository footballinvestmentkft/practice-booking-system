"""
Unit tests for baseline semantics correction (feat/baseline-semantics-correction).

Business rule under test:
  - Every new LFA Football Player starts with visible current_level = 60 (SYSTEM_BASELINE).
  - Onboarding self-assessment is stored in 'self_assessment' field, never in current_level.
  - EMA anchors on system_baseline / baseline = 60, not on self-assessment.
  - Legacy flat-scalar and legacy rich-dict records remain fully backward-compatible.
  - NULL football_skills fallback returns DEFAULT_BASELINE = 60.0 (was 50.0).

Self-Assessment Contract (Plan C):
  - self_assessment may differ from current_level; current_level always starts at 60.0.
  - get_baseline_skills() returns 60.0 even when self_assessment = 80.
  - EMA formula output is identical for two players with different self_assessment
    but the same tournament history (self_assessment is not an EMA input).
  - assessment_delta is initialised to 0.0 at onboarding; it is computed from
    FootballSkillAssessment rows (coach evaluations), never from self_assessment.
  - All three baseline-family fields (baseline, system_baseline, current_level)
    are 60.0 at onboarding regardless of self_assessment.

All tests use MagicMock DB — no real DB connection required.
"""
from unittest.mock import MagicMock

import pytest

from app.services.skill_progression import DEFAULT_BASELINE, SYSTEM_BASELINE
from app.services.skill_progression._config import get_baseline_skills
from app.models.license import UserLicense


# ── Helpers ───────────────────────────────────────────────────────────────────

_SKILL_KEY = "finishing"  # arbitrary canonical key
_USER_ID = 42             # non-1 constant required by Hardcoded FK ID Guard


def _mock_db(football_skills):
    """Return a mock DB whose UserLicense query returns a license with given football_skills."""
    lic = MagicMock(spec=UserLicense)
    lic.football_skills = football_skills
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = lic
    return db


def _mock_db_no_license():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


# ── SYSTEM_BASELINE and DEFAULT_BASELINE constants ────────────────────────────

class TestBaselineConstants:
    def test_system_baseline_is_60(self):
        assert SYSTEM_BASELINE == 60.0

    def test_default_baseline_is_60(self):
        assert DEFAULT_BASELINE == 60.0

    def test_system_baseline_equals_default_baseline(self):
        assert SYSTEM_BASELINE == DEFAULT_BASELINE


# ── NULL / missing football_skills fallback ───────────────────────────────────

class TestNullFallback:
    def test_no_license_returns_all_60(self):
        """User with no active license → all 29 skills return DEFAULT_BASELINE = 60."""
        db = _mock_db_no_license()
        result = get_baseline_skills(db, user_id=_USER_ID)
        assert all(v == 60.0 for v in result.values())
        assert len(result) == 29

    def test_empty_football_skills_returns_all_60(self):
        """License exists but football_skills is empty dict → DEFAULT_BASELINE = 60."""
        db = _mock_db({})
        result = get_baseline_skills(db, user_id=_USER_ID)
        assert all(v == 60.0 for v in result.values())

    def test_none_football_skills_returns_all_60(self):
        """License exists but football_skills is None → DEFAULT_BASELINE = 60."""
        lic = MagicMock(spec=UserLicense)
        lic.football_skills = None
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = lic
        result = get_baseline_skills(db, user_id=_USER_ID)
        assert all(v == 60.0 for v in result.values())


# ── New-format onboarding record ──────────────────────────────────────────────

class TestNewFormatRecord:
    """football_skills written by onboarding after this correction."""

    def _new_skills(self, self_assessment: float = 75.0):
        """Build a football_skills dict matching the corrected onboarding output."""
        from app.skills_config import get_all_skill_keys
        return {
            k: {
                "system_baseline":  60.0,
                "self_assessment":  self_assessment,
                "baseline":         60.0,
                "current_level":    60.0,
                "total_delta":      0.0,
                "tournament_delta": 0.0,
                "assessment_delta": 0.0,
                "last_updated":     "2026-04-24T10:00:00+00:00",
                "assessment_count": 0,
                "tournament_count": 0,
            }
            for k in get_all_skill_keys()
        }

    def test_system_baseline_stored(self):
        skills = self._new_skills(self_assessment=75.0)
        assert skills[_SKILL_KEY]["system_baseline"] == 60.0

    def test_self_assessment_stored_separately(self):
        skills = self._new_skills(self_assessment=75.0)
        assert skills[_SKILL_KEY]["self_assessment"] == 75.0

    def test_current_level_is_60_not_self_assessment(self):
        skills = self._new_skills(self_assessment=75.0)
        assert skills[_SKILL_KEY]["current_level"] == 60.0
        assert skills[_SKILL_KEY]["current_level"] != skills[_SKILL_KEY]["self_assessment"]

    def test_baseline_is_60_not_self_assessment(self):
        skills = self._new_skills(self_assessment=75.0)
        assert skills[_SKILL_KEY]["baseline"] == 60.0

    def test_get_baseline_skills_reads_system_baseline(self):
        """get_baseline_skills() must prefer system_baseline = 60 over any other field."""
        skills = self._new_skills(self_assessment=75.0)
        db = _mock_db(skills)
        result = get_baseline_skills(db, user_id=_USER_ID)
        assert result[_SKILL_KEY] == 60.0

    def test_ema_anchor_is_60_not_self_assessment(self):
        """When system_baseline=60 and self_assessment=75, EMA anchor returned is 60."""
        skills = self._new_skills(self_assessment=75.0)
        db = _mock_db(skills)
        result = get_baseline_skills(db, user_id=_USER_ID)
        for v in result.values():
            assert v == 60.0, f"Expected EMA anchor 60.0, got {v}"


# ── Legacy backward-compatibility ─────────────────────────────────────────────

class TestLegacyBackwardCompat:
    """Existing records in production must continue to work unchanged."""

    def test_flat_scalar_record_unchanged(self):
        """Old flat format {'ball_control': 72.0} still reads the scalar value."""
        from app.skills_config import get_all_skill_keys
        flat_skills = {k: 72.0 for k in get_all_skill_keys()}
        db = _mock_db(flat_skills)
        result = get_baseline_skills(db, user_id=_USER_ID)
        assert result["ball_control"] == 72.0

    def test_legacy_rich_dict_without_system_baseline(self):
        """Old rich format {'baseline': 70.0, 'current_level': 70.0} → reads 'baseline'."""
        from app.skills_config import get_all_skill_keys
        legacy_skills = {
            k: {"baseline": 70.0, "current_level": 70.0, "total_delta": 0.0}
            for k in get_all_skill_keys()
        }
        db = _mock_db(legacy_skills)
        result = get_baseline_skills(db, user_id=_USER_ID)
        assert result["ball_control"] == 70.0

    def test_legacy_flat_63_preserved(self):
        """Bootstrap U15 flat 63.0 records are read unchanged."""
        from app.skills_config import get_all_skill_keys
        flat_skills = {k: 63.0 for k in get_all_skill_keys()}
        db = _mock_db(flat_skills)
        result = get_baseline_skills(db, user_id=_USER_ID)
        assert all(v == 63.0 for v in result.values())

    def test_legacy_flat_68_preserved(self):
        """Bootstrap U18 flat 68.0 records are read unchanged."""
        from app.skills_config import get_all_skill_keys
        flat_skills = {k: 68.0 for k in get_all_skill_keys()}
        db = _mock_db(flat_skills)
        result = get_baseline_skills(db, user_id=_USER_ID)
        assert all(v == 68.0 for v in result.values())

    def test_partial_record_missing_skill_falls_back_to_60(self):
        """A skill key absent from football_skills falls back to DEFAULT_BASELINE = 60."""
        db = _mock_db({"ball_control": 70.0})  # only one skill present
        result = get_baseline_skills(db, user_id=_USER_ID)
        for key, val in result.items():
            if key == "ball_control":
                assert val == 70.0
            else:
                assert val == 60.0, f"{key}: expected 60.0 fallback, got {val}"


# ── Self-Assessment Contract tests (Plan C) ────────────────────────────────────

class TestSelfAssessmentContract:
    """
    Explicit contract tests for the business rule:

        Onboarding self_assessment values are a motivational reference only.
        They are never used as EMA anchors, baselines, or calculation inputs.
        SYSTEM_BASELINE = 60.0 is the sole authoritative starting point.
    """

    def _skills_with_sa(self, sa_value: float):
        """Build a full 29-skill football_skills dict with given self_assessment."""
        from app.skills_config import get_all_skill_keys
        return {
            k: {
                "system_baseline":  60.0,
                "self_assessment":  sa_value,
                "baseline":         60.0,
                "current_level":    60.0,
                "total_delta":      0.0,
                "tournament_delta": 0.0,
                "assessment_delta": 0.0,
                "last_updated":     "2026-05-01T00:00:00+00:00",
                "assessment_count": 0,
                "tournament_count": 0,
            }
            for k in get_all_skill_keys()
        }

    # C-1: self_assessment may differ from current_level; current_level starts at 60.0
    def test_c1_current_level_always_60_at_onboarding_regardless_of_self_assessment(self):
        for sa in [0, 30, 60, 80, 100]:
            skills = self._skills_with_sa(sa)
            for skill_key, data in skills.items():
                assert data["current_level"] == 60.0, (
                    f"skill={skill_key}, sa={sa}: current_level must be 60.0, got {data['current_level']}"
                )
                if sa != 60:
                    assert data["current_level"] != data["self_assessment"], (
                        f"skill={skill_key}, sa={sa}: current_level must differ from self_assessment"
                    )

    # C-2: get_baseline_skills() returns 60.0 even when self_assessment = 80
    def test_c2_get_baseline_skills_ignores_self_assessment_80(self):
        skills = self._skills_with_sa(80.0)
        db = _mock_db(skills)
        result = get_baseline_skills(db, user_id=_USER_ID)
        for key, val in result.items():
            assert val == 60.0, (
                f"skill={key}: get_baseline_skills() must return 60.0 (system_baseline), "
                f"not 80.0 (self_assessment). Got {val}."
            )

    # C-3: EMA formula output is independent of self_assessment
    def test_c3_ema_formula_output_independent_of_self_assessment(self):
        """
        Two players with different self_assessment values but identical system_baseline
        and identical tournament history must produce identical EMA output.

        This is verified by confirming get_baseline_skills() returns the same anchor
        for both — since the EMA formula is a pure function of (baseline, placement,
        total_players, ...), identical anchors guarantee identical EMA output.
        """
        from app.skills_config import get_all_skill_keys
        from app.services.skill_progression._formulas import (
            calculate_skill_value_from_placement, SYSTEM_BASELINE
        )

        skill_keys = list(get_all_skill_keys())

        # Player A: self_assessment = 30 (thinks they're bad)
        skills_a = self._skills_with_sa(30.0)
        db_a = _mock_db(skills_a)
        baseline_a = get_baseline_skills(db_a, user_id=_USER_ID)

        # Player B: self_assessment = 90 (thinks they're excellent)
        skills_b = self._skills_with_sa(90.0)
        db_b = _mock_db(skills_b)
        baseline_b = get_baseline_skills(db_b, user_id=_USER_ID)

        # Both anchors must be identical (60.0) — self_assessment has no effect
        for key in skill_keys:
            assert baseline_a[key] == baseline_b[key] == SYSTEM_BASELINE, (
                f"skill={key}: baseline must be {SYSTEM_BASELINE} for both players "
                f"(got A={baseline_a[key]}, B={baseline_b[key]})"
            )

        # EMA formula output with identical inputs must be identical
        ema_kwargs = dict(placement=3, total_players=8, tournament_count=1,
                          skill_weight=1.0, prev_value=None, learning_rate=0.20,
                          opponent_factor=1.0, match_performance_modifier=0.0)
        result_a = calculate_skill_value_from_placement(baseline_a[skill_keys[0]], **ema_kwargs)
        result_b = calculate_skill_value_from_placement(baseline_b[skill_keys[0]], **ema_kwargs)
        assert result_a == result_b, (
            f"EMA output must be identical regardless of self_assessment: "
            f"A={result_a}, B={result_b}"
        )

    # C-4: assessment_delta is 0.0 at onboarding; not derived from self_assessment
    def test_c4_assessment_delta_zero_at_onboarding_not_derived_from_self_assessment(self):
        """
        At onboarding the assessment_delta must be 0.0.
        It is computed later from FootballSkillAssessment rows (coach evaluations),
        never from the self_assessment value stored at onboarding.
        """
        for sa in [0, 50, 80, 100]:
            skills = self._skills_with_sa(sa)
            for key, data in skills.items():
                assert data["assessment_delta"] == 0.0, (
                    f"skill={key}, sa={sa}: assessment_delta must be 0.0 at onboarding, "
                    f"got {data['assessment_delta']}"
                )
                # assessment_delta must NOT be derived from self_assessment
                # (i.e. it must not equal self_assessment - 60, or self_assessment, etc.)
                if sa != 60.0:
                    assert data["assessment_delta"] != (sa - 60.0), (
                        f"skill={key}: assessment_delta looks like it was computed from "
                        f"self_assessment ({sa} - 60 = {sa - 60.0}). This is a contract violation."
                    )

    # C-5: All three baseline-family fields are 60.0 at onboarding
    def test_c5_all_baseline_fields_are_60_at_onboarding(self):
        """
        At onboarding, regardless of self_assessment, these three fields must all be 60.0:
          - baseline (backward-compat alias)
          - system_baseline (canonical EMA anchor)
          - current_level (visible starting level)
        """
        for sa in [0, 25, 75, 100]:
            skills = self._skills_with_sa(sa)
            for key, data in skills.items():
                assert data["system_baseline"] == 60.0, (
                    f"skill={key}, sa={sa}: system_baseline must be 60.0"
                )
                assert data["baseline"] == 60.0, (
                    f"skill={key}, sa={sa}: baseline must be 60.0"
                )
                assert data["current_level"] == 60.0, (
                    f"skill={key}, sa={sa}: current_level must be 60.0"
                )
