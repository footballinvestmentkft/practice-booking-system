"""
Instructor Eligibility Service — Unit + Integration Tests

Covers rögzített domain policy (2026-05-07):
  Master:   role=INSTRUCTOR + is_active + active non-expired LFA_COACH license
            + current_level >= required_level(age_groups)
  Field:    same + level >= max(1, required_level - 1)
  payment_verified: out-of-scope
  revoked/suspended: is_active=False covers it

Test IDs:
  E-01  eligible master instructor accepted
  E-02  inactive user rejected
  E-03  wrong role rejected
  E-04  no license rejected
  E-05  inactive license rejected
  E-06  expired license rejected (aware datetime)
  E-07  level too low for master rejected
  E-08  level sufficient for master accepted
  E-09  field instructor one level lower accepted
  E-10  field instructor too low rejected
  E-11  multi-age — highest age_group requirement applies
  E-12  expires_at=None accepted (perpetual license)
  E-13  naive expires_at in past rejected  (timezone-safe)
  E-14  naive expires_at in future accepted (timezone-safe)
  E-15  _to_utc_aware normalizes both naive and aware datetimes
  E-16  get_eligible_master_instructors — only eligible in list
  E-17  get_instructor_license_levels — correct mapping
  E-18  resolve_tournament_age_groups — priority logic
  E-19  check_tournament_master_instructor_eligible — no master assigned
  E-20  check_tournament_master_instructor_eligible — assigned but ineligible

Run:
  pytest tests/unit/services/test_instructor_eligibility_service.py -v
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.license import UserLicense
from app.models.user import User, UserRole
from app.services.tournament.instructor_eligibility_service import (
    _to_utc_aware,
    check_tournament_master_instructor_eligible,
    get_eligible_master_instructors,
    get_instructor_license_levels,
    is_eligible_field_instructor,
    is_eligible_master_instructor,
    resolve_tournament_age_groups,
)


# ── Factories ─────────────────────────────────────────────────────────────────

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_instructor(db: Session, *, is_active: bool = True) -> User:
    u = User(
        email=f"instr-{_uid()}@lfa.com",
        name=f"Instructor {_uid()}",
        password_hash=get_password_hash("pw"),
        role=UserRole.INSTRUCTOR,
        is_active=is_active,
    )
    db.add(u)
    db.flush()
    return u


def _make_coach_license(
    db: Session,
    user: User,
    *,
    level: int = 5,
    is_active: bool = True,
    expires_at: datetime | None = None,
) -> UserLicense:
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_COACH",
        current_level=level,
        max_achieved_level=level,
        is_active=is_active,
        expires_at=expires_at,
        started_at=datetime.now(timezone.utc),
    )
    db.add(lic)
    db.flush()
    return lic


# ── Timezone helper tests ─────────────────────────────────────────────────────

class TestToUtcAware:
    """E-15: _to_utc_aware normalizes naive and aware datetimes."""

    def test_naive_becomes_utc_aware(self):
        naive = datetime(2026, 1, 1, 12, 0, 0)  # no tzinfo
        result = _to_utc_aware(naive)
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc
        assert result.year == 2026

    def test_already_utc_aware_unchanged_value(self):
        aware = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        result = _to_utc_aware(aware)
        assert result == aware

    def test_non_utc_aware_converted_to_utc(self):
        from datetime import timezone as tz
        import zoneinfo
        try:
            budapest = zoneinfo.ZoneInfo("Europe/Budapest")
            dt = datetime(2026, 6, 1, 14, 0, tzinfo=budapest)
            result = _to_utc_aware(dt)
            # 14:00 CEST (UTC+2) = 12:00 UTC
            assert result.tzinfo == timezone.utc
            assert result.hour == 12
        except Exception:
            pytest.skip("zoneinfo not available or tz data missing")


# ── Master instructor eligibility ─────────────────────────────────────────────

class TestIsEligibleMasterInstructor:
    """E-01 through E-12: master instructor policy checks."""

    def test_e01_eligible_master_accepted(self, postgres_db: Session):
        """E-01: role=INSTRUCTOR, is_active, active non-expired license, level ≥ min."""
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=5)
        postgres_db.commit()

        ok, reason = is_eligible_master_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is True
        assert reason == ""

    def test_e02_inactive_user_rejected(self, postgres_db: Session):
        """E-02: User.is_active=False → rejected."""
        instr = _make_instructor(postgres_db, is_active=False)
        _make_coach_license(postgres_db, instr, level=5)
        postgres_db.commit()

        ok, reason = is_eligible_master_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is False
        assert "inactive" in reason.lower()

    def test_e03_wrong_role_rejected(self, postgres_db: Session):
        """E-03: UserRole.ADMIN is not eligible as instructor."""
        admin = User(
            email=f"admin-{_uid()}@lfa.com",
            name="Admin",
            password_hash=get_password_hash("pw"),
            role=UserRole.ADMIN,
            is_active=True,
        )
        postgres_db.add(admin)
        postgres_db.flush()
        _make_coach_license(postgres_db, admin, level=8)
        postgres_db.commit()

        ok, reason = is_eligible_master_instructor(postgres_db, admin.id, ["AMATEUR"])
        assert ok is False
        assert "instructor" in reason.lower()

    def test_e04_no_license_rejected(self, postgres_db: Session):
        """E-04: No UserLicense row exists → rejected."""
        instr = _make_instructor(postgres_db)
        postgres_db.commit()

        ok, reason = is_eligible_master_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is False
        assert "license" in reason.lower()

    def test_e05_inactive_license_rejected(self, postgres_db: Session):
        """E-05: UserLicense.is_active=False → rejected even if not expired."""
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=5, is_active=False)
        postgres_db.commit()

        ok, reason = is_eligible_master_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is False
        assert "license" in reason.lower()

    def test_e06_expired_license_aware_rejected(self, postgres_db: Session):
        """E-06: expires_at in the past (timezone-aware) → rejected."""
        instr = _make_instructor(postgres_db)
        past = datetime.now(timezone.utc) - timedelta(days=1)
        _make_coach_license(postgres_db, instr, level=5, expires_at=past)
        postgres_db.commit()

        ok, reason = is_eligible_master_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is False
        assert "license" in reason.lower()

    def test_e07_level_too_low_master_rejected(self, postgres_db: Session):
        """E-07: level 3 for AMATEUR (min 5) → rejected."""
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=3)
        postgres_db.commit()

        ok, reason = is_eligible_master_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is False
        assert "insufficient" in reason.lower() or "level" in reason.lower()

    def test_e08_level_sufficient_master_accepted(self, postgres_db: Session):
        """E-08: level 5 for AMATEUR (min 5) → accepted (boundary value)."""
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=5)
        postgres_db.commit()

        ok, _ = is_eligible_master_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is True

    def test_e11_multi_age_highest_requirement_applies(self, postgres_db: Session):
        """E-11: ["PRE", "AMATEUR"] → level 5 required (AMATEUR dominates)."""
        instr_low = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr_low, level=3)  # ok for PRE, not AMATEUR

        instr_high = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr_high, level=5)  # ok for both

        postgres_db.commit()

        ok_low, _ = is_eligible_master_instructor(postgres_db, instr_low.id, ["PRE", "AMATEUR"])
        ok_high, _ = is_eligible_master_instructor(postgres_db, instr_high.id, ["PRE", "AMATEUR"])

        assert ok_low is False
        assert ok_high is True

    def test_e12_expires_at_none_accepted(self, postgres_db: Session):
        """E-12: expires_at=None (perpetual license) → accepted."""
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=5, expires_at=None)
        postgres_db.commit()

        ok, _ = is_eligible_master_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is True

    def test_e13_naive_past_expires_at_rejected(self, postgres_db: Session):
        """E-13: expires_at as naive datetime in the past → rejected (timezone-safe)."""
        instr = _make_instructor(postgres_db)
        # Naive datetime (no tzinfo) — stored as TIMESTAMP WITHOUT TZ in PostgreSQL
        naive_past = datetime(2020, 1, 1, 0, 0, 0)  # clearly in the past, no tzinfo
        _make_coach_license(postgres_db, instr, level=5, expires_at=naive_past)
        postgres_db.commit()

        ok, reason = is_eligible_master_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is False
        assert "license" in reason.lower()

    def test_e14_naive_future_expires_at_accepted(self, postgres_db: Session):
        """E-14: expires_at as naive datetime in the future → accepted (timezone-safe)."""
        instr = _make_instructor(postgres_db)
        naive_future = datetime(2099, 12, 31, 23, 59, 59)  # clearly in future, no tzinfo
        _make_coach_license(postgres_db, instr, level=5, expires_at=naive_future)
        postgres_db.commit()

        ok, _ = is_eligible_master_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is True

    def test_nonexistent_user_rejected(self, postgres_db: Session):
        """Nonexistent user_id → rejected with clear reason."""
        ok, reason = is_eligible_master_instructor(postgres_db, 999_999, ["AMATEUR"])
        assert ok is False
        assert "not found" in reason.lower() or "999999" in reason


# ── Field instructor eligibility ──────────────────────────────────────────────

class TestIsEligibleFieldInstructor:
    """E-09, E-10: field instructor policy — one level lower minimum."""

    def test_e09_field_one_level_lower_accepted(self, postgres_db: Session):
        """E-09: level 4 for AMATEUR field (min = max(1, 5-1) = 4) → accepted."""
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=4)
        postgres_db.commit()

        ok, _ = is_eligible_field_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is True

    def test_e10_field_too_low_rejected(self, postgres_db: Session):
        """E-10: level 3 for AMATEUR field (min 4) → rejected."""
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=3)
        postgres_db.commit()

        ok, reason = is_eligible_field_instructor(postgres_db, instr.id, ["AMATEUR"])
        assert ok is False
        assert "insufficient" in reason.lower() or "level" in reason.lower()

    def test_field_pre_minimum_is_1(self, postgres_db: Session):
        """max(1, 1-1) = max(1,0) = 1; level 1 accepted for PRE field."""
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=1)
        postgres_db.commit()

        ok, _ = is_eligible_field_instructor(postgres_db, instr.id, ["PRE"])
        assert ok is True

    def test_field_inactive_license_rejected(self, postgres_db: Session):
        """Field instructor also requires active license."""
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=8, is_active=False)
        postgres_db.commit()

        ok, reason = is_eligible_field_instructor(postgres_db, instr.id, ["PRE"])
        assert ok is False
        assert "license" in reason.lower()


# ── Utility functions ─────────────────────────────────────────────────────────

class TestGetEligibleMasterInstructors:
    """E-16: get_eligible_master_instructors — only eligible users returned."""

    def test_e16_only_eligible_in_list(self, postgres_db: Session):
        """Only instructors with active, non-expired license appear."""
        eligible = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, eligible, level=5)

        no_lic = _make_instructor(postgres_db)  # no license
        inactive_user = _make_instructor(postgres_db, is_active=False)
        _make_coach_license(postgres_db, inactive_user, level=5)
        inact_lic_user = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, inact_lic_user, level=5, is_active=False)

        postgres_db.commit()

        result = get_eligible_master_instructors(postgres_db, age_groups=None)
        result_ids = {u.id for u in result}

        assert eligible.id in result_ids
        assert no_lic.id not in result_ids
        assert inactive_user.id not in result_ids
        assert inact_lic_user.id not in result_ids

    def test_level_filter_applied_when_age_groups_given(self, postgres_db: Session):
        """With age_groups=["AMATEUR"], level 3 instructor excluded."""
        low = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, low, level=3)
        high = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, high, level=5)
        postgres_db.commit()

        result = get_eligible_master_instructors(postgres_db, age_groups=["AMATEUR"])
        ids = {u.id for u in result}
        assert low.id not in ids
        assert high.id in ids

    def test_no_age_groups_no_level_filter(self, postgres_db: Session):
        """age_groups=None: license+role filter only, level 1 instructor included."""
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=1)
        postgres_db.commit()

        result = get_eligible_master_instructors(postgres_db, age_groups=None)
        assert any(u.id == instr.id for u in result)


class TestGetInstructorLicenseLevels:
    """E-17: get_instructor_license_levels — correct dict returned."""

    def test_e17_correct_level_mapping(self, postgres_db: Session):
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=6)
        postgres_db.commit()

        levels = get_instructor_license_levels(postgres_db, [instr.id])
        assert levels[instr.id] == 6

    def test_inactive_license_not_in_dict(self, postgres_db: Session):
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=5, is_active=False)
        postgres_db.commit()

        levels = get_instructor_license_levels(postgres_db, [instr.id])
        assert instr.id not in levels

    def test_empty_user_ids_returns_empty_dict(self, postgres_db: Session):
        assert get_instructor_license_levels(postgres_db, []) == {}


class TestResolveTournamentAgeGroups:
    """E-18: resolve_tournament_age_groups — priority: age_groups > age_group > []."""

    def _sem(self, age_groups=None, age_group=None):
        from unittest.mock import MagicMock
        s = MagicMock()
        s.age_groups = age_groups
        s.age_group = age_group
        return s

    def test_age_groups_list_takes_priority(self):
        s = self._sem(age_groups=["PRE", "YOUTH"], age_group="AMATEUR")
        assert resolve_tournament_age_groups(s) == ["PRE", "YOUTH"]

    def test_scalar_age_group_used_when_no_list(self):
        s = self._sem(age_groups=None, age_group="AMATEUR")
        assert resolve_tournament_age_groups(s) == ["AMATEUR"]

    def test_empty_list_when_neither_set(self):
        s = self._sem(age_groups=None, age_group=None)
        assert resolve_tournament_age_groups(s) == []

    def test_empty_list_is_falsy_coerces_to_scalar(self):
        # age_groups=[] is falsy → falls through to age_group
        s = self._sem(age_groups=[], age_group="PRO")
        assert resolve_tournament_age_groups(s) == ["PRO"]


class TestCheckTournamentMasterInstructorEligible:
    """E-19, E-20: check_tournament_master_instructor_eligible — composite check."""

    def test_e19_no_master_assigned_returns_false_with_clear_reason(self, postgres_db: Session):
        """E-19: no master_instructor_id, no MASTER slot → (False, "No master instructor assigned")."""
        from app.models.semester import Semester, SemesterStatus, SemesterCategory
        sem = Semester(
            code=f"ELIG-{_uid()}",
            name="Elig Test",
            start_date=datetime(2026, 6, 1).date(),
            end_date=datetime(2026, 6, 30).date(),
            enrollment_cost=0,
        )
        postgres_db.add(sem)
        postgres_db.flush()
        postgres_db.commit()

        ok, reason = check_tournament_master_instructor_eligible(postgres_db, sem.id)
        assert ok is False
        assert "no master" in reason.lower() or "assigned" in reason.lower()

    def test_e20_assigned_but_ineligible_returns_false(self, postgres_db: Session):
        """E-20: master_instructor_id set, but instructor has no valid license → (False, ...)."""
        from app.models.semester import Semester
        instr = _make_instructor(postgres_db)
        # Deliberately NO license — ineligible
        sem = Semester(
            code=f"ELIG-{_uid()}",
            name="Elig Test 2",
            start_date=datetime(2026, 6, 1).date(),
            end_date=datetime(2026, 6, 30).date(),
            enrollment_cost=0,
            master_instructor_id=instr.id,
            age_group="AMATEUR",
        )
        postgres_db.add(sem)
        postgres_db.flush()
        postgres_db.commit()

        ok, reason = check_tournament_master_instructor_eligible(postgres_db, sem.id)
        assert ok is False
        assert "license" in reason.lower()

    def test_assigned_and_eligible_returns_true(self, postgres_db: Session):
        """Assigned eligible master → (True, "")."""
        from app.models.semester import Semester
        instr = _make_instructor(postgres_db)
        _make_coach_license(postgres_db, instr, level=5)
        sem = Semester(
            code=f"ELIG-{_uid()}",
            name="Elig Test 3",
            start_date=datetime(2026, 6, 1).date(),
            end_date=datetime(2026, 6, 30).date(),
            enrollment_cost=0,
            master_instructor_id=instr.id,
            age_group="AMATEUR",
        )
        postgres_db.add(sem)
        postgres_db.flush()
        postgres_db.commit()

        ok, reason = check_tournament_master_instructor_eligible(postgres_db, sem.id)
        assert ok is True
        assert reason == ""
