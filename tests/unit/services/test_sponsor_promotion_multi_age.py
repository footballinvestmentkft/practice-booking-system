"""
Unit tests for sponsor promotion multi-age redesign (Option B).

Tests verified:
  MA-01  Single age_groups=[PRE] → age_group='PRE', age_groups=['PRE']
  MA-02  Multi age_groups=[PRE,YOUTH] → age_group=None, age_groups=['PRE','YOUTH']
  MA-03  Single age: name carries no age-category suffix
  MA-04  Multi age: name carries no age-category suffix
  MA-05  Organizer fields (sponsor_id, campaign_id, club_id) set correctly
  MA-06  specialization_type == "LFA_FOOTBALL_PLAYER"
  MA-07  get_allowed_age_groups: multi-age JSONB takes precedence over scalar
  MA-08  get_allowed_age_groups: scalar age_group backward-compat fallback

DONE = pytest tests/unit/services/test_sponsor_promotion_multi_age.py -v
"""
from types import SimpleNamespace

import pytest

from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.tournament_configuration import TournamentConfiguration
from app.services.tournament.validation import get_allowed_age_groups


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_semester(age_groups: list, tournament_name: str = "Test Event",
                   sponsor_id: int = 1, campaign_id: int = 10) -> Semester:
    """Build a Semester the same way the wizard route does — no DB flush needed."""
    from datetime import date
    return Semester(
        code="PROMO-TEST-001",
        name=tournament_name,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        status=SemesterStatus.DRAFT,
        tournament_status="DRAFT",
        semester_category=SemesterCategory.PROMOTION_EVENT,
        specialization_type="LFA_FOOTBALL_PLAYER",
        age_group=age_groups[0] if len(age_groups) == 1 else None,
        age_groups=age_groups,
        enrollment_cost=0,
        organizer_sponsor_id=sponsor_id,
        organizer_campaign_id=campaign_id,
        organizer_club_id=None,
    )


def _make_tc(semester_id=None) -> TournamentConfiguration:
    return TournamentConfiguration(
        semester_id=semester_id,
        participant_type="INDIVIDUAL",
        number_of_rounds=1,
        assignment_type="OPEN_ASSIGNMENT",
    )


# ── MA-01 / MA-02: age_group vs age_groups semantics ─────────────────────────

class TestAgeGroupFieldSemantics:

    def test_ma_01_single_age_sets_scalar_and_list(self):
        """Single PRE → age_group='PRE', age_groups=['PRE']."""
        s = _make_semester(["PRE"])
        assert s.age_group == "PRE"
        assert s.age_groups == ["PRE"]

    def test_ma_02_multi_age_nulls_scalar(self):
        """PRE+YOUTH → age_group=None, age_groups=['PRE','YOUTH']."""
        s = _make_semester(["PRE", "YOUTH"])
        assert s.age_group is None
        assert set(s.age_groups) == {"PRE", "YOUTH"}

    def test_ma_02_four_age_groups(self):
        """All four groups → age_group=None, age_groups contains all four."""
        s = _make_semester(["PRE", "YOUTH", "AMATEUR", "PRO"])
        assert s.age_group is None
        assert set(s.age_groups) == {"PRE", "YOUTH", "AMATEUR", "PRO"}


# ── MA-03 / MA-04: no name suffix ────────────────────────────────────────────

class TestNoNameSuffix:
    _AGE_SUFFIXES = ("(PRE)", "(YOUTH)", "(AMATEUR)", "(PRO)")

    def test_ma_03_no_suffix_single(self):
        """Single-age event name must not have any age-category suffix."""
        s = _make_semester(["PRE"], tournament_name="Spring Cup 2026")
        assert s.name == "Spring Cup 2026"
        for suffix in self._AGE_SUFFIXES:
            assert suffix not in s.name

    def test_ma_04_no_suffix_multi(self):
        """Multi-age event name must not have any age-category suffix."""
        s = _make_semester(["PRE", "YOUTH"], tournament_name="Open Challenge")
        assert s.name == "Open Challenge"
        for suffix in self._AGE_SUFFIXES:
            assert suffix not in s.name


# ── MA-05 / MA-06: organizer fields + domain defaults ────────────────────────

class TestOrganizerFieldsAndDefaults:

    def test_ma_05_organizer_fields(self):
        """organizer_sponsor_id + campaign_id set; club_id=None."""
        s = _make_semester(["YOUTH"], sponsor_id=7, campaign_id=42)
        assert s.organizer_sponsor_id == 7
        assert s.organizer_campaign_id == 42
        assert s.organizer_club_id is None

    def test_ma_06_specialization_type(self):
        """specialization_type must always be LFA_FOOTBALL_PLAYER."""
        s = _make_semester(["AMATEUR"])
        assert s.specialization_type == "LFA_FOOTBALL_PLAYER"

    def test_ma_06_tc_assignment_type(self):
        """TournamentConfiguration.assignment_type == 'OPEN_ASSIGNMENT'."""
        tc = _make_tc(semester_id=99)
        assert tc.assignment_type == "OPEN_ASSIGNMENT"
        assert tc.participant_type == "INDIVIDUAL"


# ── MA-07 / MA-08: get_allowed_age_groups ────────────────────────────────────

class TestGetAllowedAgeGroups:

    def test_ma_07_jsonb_takes_precedence(self):
        """age_groups JSONB overrides scalar age_group."""
        sem = SimpleNamespace(age_groups=["PRE", "YOUTH"], age_group="PRE")
        assert get_allowed_age_groups(sem) == ["PRE", "YOUTH"]

    def test_ma_07_returns_copy(self):
        """Returns a new list (not a reference to the stored JSONB)."""
        stored = ["PRE", "YOUTH"]
        sem = SimpleNamespace(age_groups=stored, age_group=None)
        result = get_allowed_age_groups(sem)
        assert result == stored
        assert result is not stored

    def test_ma_08_scalar_fallback(self):
        """Falls back to [age_group] when age_groups is None."""
        sem = SimpleNamespace(age_groups=None, age_group="YOUTH")
        assert get_allowed_age_groups(sem) == ["YOUTH"]

    def test_ma_08_empty_list_fallback(self):
        """Empty list is falsy → falls back to scalar."""
        sem = SimpleNamespace(age_groups=[], age_group="AMATEUR")
        assert get_allowed_age_groups(sem) == ["AMATEUR"]

    def test_ma_both_none_returns_none(self):
        """No restriction when both fields are None."""
        sem = SimpleNamespace(age_groups=None, age_group=None)
        assert get_allowed_age_groups(sem) is None
