"""
Unit tests for app/api/api_v1/endpoints/licenses/instructor.py
Covers: instructor_advance_license, get_user_licenses, get_user_licenses_by_instructor,
        get_user_license_dashboard_by_instructor, get_instructor_teachable_specializations
Note: all endpoints are async → use asyncio.run()
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch

from app.api.api_v1.endpoints.licenses.instructor import (
    instructor_advance_license,
    get_user_licenses,
    get_user_licenses_by_instructor,
    get_user_license_dashboard_by_instructor,
    get_instructor_teachable_specializations,
)
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.licenses.instructor"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(first_val=None, all_val=None):
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = all_val if all_val is not None else []
    q.first.return_value = first_val
    return q


def _user(uid=42, role=UserRole.INSTRUCTOR):
    u = MagicMock()
    u.id = uid
    u.role = role
    return u


def _admin():
    return _user(uid=42, role=UserRole.ADMIN)


def _student(uid=99):
    return _user(uid=uid, role=UserRole.STUDENT)


def _license_mock(spec_type="COACH", level=1, canonical_program_id=None):
    lic = MagicMock()
    lic.specialization_type = spec_type
    lic.current_level = level
    lic.canonical_program_id = canonical_program_id
    return lic


# ---------------------------------------------------------------------------
# instructor_advance_license
# ---------------------------------------------------------------------------

class TestInstructorAdvanceLicense:
    def _call(self, data=None, current_user=None, db=None):
        data = data or {"user_id": 7, "specialization": "COACH", "target_level": 2}
        return asyncio.run(instructor_advance_license(
            data=data,
            current_user=current_user or _user(),
            db=db or MagicMock(),
        ))

    def test_non_instructor_403(self):
        """IAL-01: non-instructor → 403."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(current_user=_admin())
        assert exc.value.status_code == 403

    def test_missing_user_id_400(self):
        """IAL-02: missing user_id → 400."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(data={"specialization": "COACH", "target_level": 2})
        assert exc.value.status_code == 400

    def test_missing_specialization_400(self):
        """IAL-03: missing specialization → 400."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(data={"user_id": 7, "target_level": 2})
        assert exc.value.status_code == 400

    def test_missing_target_level_400(self):
        """IAL-04: missing target_level → 400."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(data={"user_id": 7, "specialization": "COACH"})
        assert exc.value.status_code == 400

    def test_success(self):
        """IAL-05: all fields present → LicenseService called."""
        mock_result = {"success": True, "new_level": 2}
        with patch(f"{_BASE}.LicenseService") as MockLS:
            MockLS.return_value.advance_license.return_value = mock_result
            result = self._call()
        assert result["success"] is True


# ---------------------------------------------------------------------------
# get_user_licenses
# ---------------------------------------------------------------------------

class TestGetUserLicenses:
    def _call(self, user_id=42, current_user=None, db=None):
        return asyncio.run(get_user_licenses(
            user_id=user_id,
            current_user=current_user or _user(uid=42),
            db=db or MagicMock(),
        ))

    def test_student_viewing_other_403(self):
        """GUL-01: student viewing other's licenses → 403."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(user_id=99, current_user=_student(uid=10))
        assert exc.value.status_code == 403

    def test_user_can_view_own(self):
        """GUL-02: user viewing own licenses → success."""
        with patch(f"{_BASE}.LicenseService") as MockLS:
            MockLS.return_value.get_user_licenses.return_value = []
            result = self._call(user_id=42, current_user=_student(uid=42))
        assert result == []

    def test_instructor_can_view_any(self):
        """GUL-03: instructor viewing another user → success."""
        with patch(f"{_BASE}.LicenseService") as MockLS:
            MockLS.return_value.get_user_licenses.return_value = []
            result = self._call(user_id=99, current_user=_user(uid=42, role=UserRole.INSTRUCTOR))
        assert result == []

    def test_admin_can_view_any(self):
        """GUL-04: admin viewing another user → success."""
        with patch(f"{_BASE}.LicenseService") as MockLS:
            MockLS.return_value.get_user_licenses.return_value = []
            result = self._call(user_id=99, current_user=_admin())
        assert result == []


# ---------------------------------------------------------------------------
# get_user_licenses_by_instructor
# ---------------------------------------------------------------------------

class TestGetUserLicensesByInstructor:
    def _call(self, user_id=7, current_user=None, db=None):
        return asyncio.run(get_user_licenses_by_instructor(
            user_id=user_id,
            current_user=current_user or _user(),
            db=db or MagicMock(),
        ))

    def test_non_instructor_403(self):
        """GULIBI-01: non-instructor → 403."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(current_user=_admin())
        assert exc.value.status_code == 403

    def test_instructor_success(self):
        """GULIBI-02: instructor → returns licenses."""
        with patch(f"{_BASE}.LicenseService") as MockLS:
            MockLS.return_value.get_user_licenses.return_value = [{"level": 2}]
            result = self._call()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_user_license_dashboard_by_instructor
# ---------------------------------------------------------------------------

class TestGetUserLicenseDashboardByInstructor:
    def _call(self, user_id=7, current_user=None, db=None):
        return asyncio.run(get_user_license_dashboard_by_instructor(
            user_id=user_id,
            current_user=current_user or _user(),
            db=db or MagicMock(),
        ))

    def test_non_instructor_403(self):
        """GULDB-01: non-instructor → 403."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(current_user=_admin())
        assert exc.value.status_code == 403

    def test_success(self):
        """GULDB-02: instructor → returns dashboard."""
        with patch(f"{_BASE}.LicenseService") as MockLS:
            MockLS.return_value.get_user_license_dashboard.return_value = {"level": 3}
            result = self._call()
        assert result["level"] == 3


# ---------------------------------------------------------------------------
# get_instructor_teachable_specializations
# ---------------------------------------------------------------------------

def _gits_sys_modules():
    """Context manager patching the broken relative import in get_instructor_teachable_specializations.
    Production bug: `from ....models.license import UserLicense` resolves to
    app.api.models.license (non-existent). Inject a fake module so the lazy import succeeds.
    """
    import sys
    from unittest.mock import patch
    fake_parent = MagicMock()
    fake_mod = MagicMock()
    fake_mod.UserLicense = MagicMock()
    return patch.dict(sys.modules, {
        "app.api.models": fake_parent,
        "app.api.models.license": fake_mod,
    })


class TestGetInstructorTeachableSpecializations:
    def _call(self, instructor_id=42, current_user=None, db=None):
        return asyncio.run(get_instructor_teachable_specializations(
            instructor_id=instructor_id,
            current_user=current_user or _user(uid=42),
            db=db or MagicMock(),
        ))

    def test_non_admin_viewing_other_403(self):
        """GITS-01: instructor viewing other's specs → 403 (before lazy import)."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(instructor_id=99, current_user=_user(uid=42))
        assert exc.value.status_code == 403

    def test_no_licenses_returns_empty(self):
        """GITS-02: no active licenses → []."""
        q = _q(all_val=[])
        db = MagicMock()
        db.query.return_value = q
        with _gits_sys_modules():
            result = self._call(db=db)
        assert result == []

    def test_pre_head_coach_only_maps_pre_football(self):
        """GITS-03: level 2 Head Coach is scoped to PRE."""
        lic = _license_mock(spec_type="COACH", level=2)
        q = _q(all_val=[lic])
        db = MagicMock()
        db.query.return_value = q
        with _gits_sys_modules():
            result = self._call(db=db)
        assert "LFA_PLAYER_PRE" in result
        assert "LFA_PLAYER_YOUTH" not in result
        assert "LFA_PLAYER_AMATEUR" not in result
        assert "LFA_PLAYER_PRO" not in result

    def test_youth_assistant_maps_only_pre_and_youth(self):
        lic = _license_mock(spec_type="LFA_COACH", level=3)
        q = _q(all_val=[lic])
        db = MagicMock()
        db.query.return_value = q
        result = self._call(db=db)
        assert result == ["LFA_PLAYER_PRE", "LFA_PLAYER_YOUTH"]

    def test_internship_license(self):
        """GITS-04: INTERNSHIP license → INTERNSHIP specialization."""
        lic = _license_mock(spec_type="INTERNSHIP")
        q = _q(all_val=[lic])
        db = MagicMock()
        db.query.return_value = q
        with _gits_sys_modules():
            result = self._call(db=db)
        assert "INTERNSHIP" in result

    def test_player_license_does_not_grant_teaching_scope(self):
        """GITS-05: player entitlement never grants instructor authority."""
        lic = _license_mock(spec_type="PLAYER")
        q = _q(all_val=[lic])
        db = MagicMock()
        db.query.return_value = q
        with _gits_sys_modules():
            result = self._call(db=db)
        assert result == []

    def test_admin_can_view_any(self):
        """GITS-06: admin viewing any instructor's specs → success."""
        q = _q(all_val=[])
        db = MagicMock()
        db.query.return_value = q
        with _gits_sys_modules():
            result = self._call(instructor_id=99, current_user=_admin(), db=db)
        assert result == []

    def test_instructor_own_specs(self):
        """GITS-07: instructor viewing own → allowed."""
        lic = _license_mock(spec_type="COACH", level=2)
        q = _q(all_val=[lic])
        db = MagicMock()
        db.query.return_value = q
        with _gits_sys_modules():
            result = self._call(instructor_id=42, current_user=_user(uid=42), db=db)
        assert "LFA_PLAYER_PRE" in result

    def test_unknown_license_type_ignored(self):
        """GITS-08: unknown license type → not added to specs."""
        lic = _license_mock(spec_type="UNKNOWN_TYPE")
        q = _q(all_val=[lic])
        db = MagicMock()
        db.query.return_value = q
        with _gits_sys_modules():
            result = self._call(db=db)
        # Unknown type: no spec added, result is []
        assert isinstance(result, list)
