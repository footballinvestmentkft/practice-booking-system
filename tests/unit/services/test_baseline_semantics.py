"""
Unit tests for baseline semantics correction (feat/baseline-semantics-correction).

Business rule under test:
  - Every new LFA Football Player starts with visible current_level = 60 (SYSTEM_BASELINE).
  - Onboarding self-assessment is stored in 'self_assessment' field, never in current_level.
  - EMA anchors on system_baseline / baseline = 60, not on self-assessment.
  - Legacy flat-scalar and legacy rich-dict records remain fully backward-compatible.
  - NULL football_skills fallback returns DEFAULT_BASELINE = 60.0 (was 50.0).

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
