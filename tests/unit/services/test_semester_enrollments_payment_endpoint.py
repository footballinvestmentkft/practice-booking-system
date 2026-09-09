"""
Unit tests for semester_enrollments/payment.py + category_override.py
Covers: verify/unverify payment, get_payment_info, verify_by_code,
        override_age_category
All endpoints are async → asyncio.run()
"""
import asyncio
import json
import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock, patch

from app.api.api_v1.endpoints.semester_enrollments.payment import (
    verify_enrollment_payment,
    unverify_enrollment_payment,
    get_payment_info,
    verify_payment_by_code,
)
from app.api.api_v1.endpoints.semester_enrollments.category_override import (
    override_age_category,
)
from app.api.api_v1.endpoints.semester_enrollments.schemas import CategoryOverride
from app.models.user import UserRole

_BASE_CAT = "app.api.api_v1.endpoints.semester_enrollments.category_override"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(uid=42, role=UserRole.ADMIN):
    u = MagicMock(); u.id = uid; u.name = "Admin"; u.role = role; return u


def _instructor():
    return _user(uid=42, role=UserRole.INSTRUCTOR)


def _q(first_val=None):
    q = MagicMock()
    q.options.return_value = q
    q.filter.return_value = q
    q.first.return_value = first_val
    return q


def _enrollment(uid=42, payment_verified=False):
    e = MagicMock()
    e.id = 1
    e.user_id = uid
    e.payment_verified = payment_verified
    e.payment_reference_code = "LFA-2025-ABCD"
    e.payment_verified_at = None
    e.user.name = "Student"
    e.user.email = "student@lfa.hu"
    e.semester.name = "2025 Q1"
    e.semester_id = 1
    e.specialization_type = "LFA_FOOTBALL_PLAYER"
    e.user_license = MagicMock()
    return e


# ---------------------------------------------------------------------------
# verify_enrollment_payment
# ---------------------------------------------------------------------------

class TestVerifyEnrollmentPayment:
    def _call(self, enrollment_id=1, db=None):
        return asyncio.run(verify_enrollment_payment(
            request=MagicMock(),
            enrollment_id=enrollment_id,
            db=db or MagicMock(),
            current_user=_user(),
        ))

    def test_vp01_not_found_404(self):
        """VP-01: enrollment not found → 404."""
        from fastapi import HTTPException
        db = MagicMock(); db.query.return_value = _q(first_val=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_vp02_success(self):
        """VP-02: enrollment found → verify_payment() called."""
        enr = _enrollment()
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db)
        assert result["success"] is True
        enr.verify_payment.assert_called_once_with(42)
        db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# unverify_enrollment_payment
# ---------------------------------------------------------------------------

class TestUnverifyEnrollmentPayment:
    def _call(self, enrollment_id=1, db=None):
        return asyncio.run(unverify_enrollment_payment(
            request=MagicMock(),
            enrollment_id=enrollment_id,
            db=db or MagicMock(),
            current_user=_user(),
        ))

    def test_uvp01_not_found_404(self):
        """UVP-01: not found → 404."""
        from fastapi import HTTPException
        db = MagicMock(); db.query.return_value = _q(first_val=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_uvp02_success(self):
        """UVP-02: found → unverify_payment() called."""
        enr = _enrollment()
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db)
        assert result["success"] is True
        enr.unverify_payment.assert_called_once()


# ---------------------------------------------------------------------------
# get_payment_info
# ---------------------------------------------------------------------------

class TestGetPaymentInfo:
    def _call(self, enrollment_id=1, db=None, current_user=None):
        return asyncio.run(get_payment_info(
            enrollment_id=enrollment_id,
            db=db or MagicMock(),
            current_user=current_user or _user(),
        ))

    def test_gpi01_not_found_404(self):
        """GPI-01: enrollment not found → 404."""
        from fastapi import HTTPException
        db = MagicMock(); db.query.return_value = _q(first_val=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_gpi02_student_viewing_other_403(self):
        """GPI-02: student viewing other student's enrollment → 403."""
        from fastapi import HTTPException
        enr = _enrollment(uid=99)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, current_user=_user(uid=42, role=UserRole.STUDENT))
        assert exc.value.status_code == 403

    def test_gpi03_admin_views_any(self):
        """GPI-03: admin viewing any enrollment → allowed, JSONResponse."""
        enr = _enrollment(uid=99)
        enr.payment_reference_code = "LFA-EXISTING"
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db, current_user=_user(uid=42, role=UserRole.ADMIN))
        data = json.loads(result.body)
        assert data["payment_reference_code"] == "LFA-EXISTING"

    def test_gpi04_own_enrollment_student_ok(self):
        """GPI-04: student viewing own enrollment → allowed."""
        enr = _enrollment(uid=42)
        enr.payment_reference_code = "LFA-OWN"
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db, current_user=_user(uid=42, role=UserRole.STUDENT))
        data = json.loads(result.body)
        assert data["payment_reference_code"] == "LFA-OWN"

    def test_gpi05_no_payment_code_generates_one(self):
        """GPI-05: no payment_reference_code → set_payment_code() called + commit."""
        enr = _enrollment(uid=42)
        enr.payment_reference_code = None
        # After set_payment_code() call, simulate code being set
        def _set_code():
            enr.payment_reference_code = "LFA-NEW-CODE"
        enr.set_payment_code.side_effect = _set_code
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db, current_user=_user(uid=42, role=UserRole.ADMIN))
        enr.set_payment_code.assert_called_once()
        data = json.loads(result.body)
        assert data["payment_reference_code"] == "LFA-NEW-CODE"

    def test_gpi06_known_spec_name_mapped(self):
        """GPI-06: known specialization_type → display name in response."""
        enr = _enrollment(uid=42)
        enr.specialization_type = "INTERNSHIP"
        enr.payment_reference_code = "LFA-X"
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db)
        data = json.loads(result.body)
        assert data["specialization"] == "Internship Program"


# ---------------------------------------------------------------------------
# verify_payment_by_code
# ---------------------------------------------------------------------------

class TestVerifyPaymentByCode:
    def _call(self, payment_code="LFA-2025-ABCD", db=None):
        return asyncio.run(verify_payment_by_code(
            payment_code=payment_code,
            db=db or MagicMock(),
            admin_user=_user(),
        ))

    def test_vbc01_not_found_404(self):
        """VBC-01: no enrollment with code → 404."""
        from fastapi import HTTPException
        db = MagicMock(); db.query.return_value = _q(first_val=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_vbc02_already_verified_400(self):
        """VBC-02: payment_verified=True → 400."""
        from fastapi import HTTPException
        enr = _enrollment(payment_verified=True)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_vbc03_success(self):
        """VBC-03: valid code, not verified → verify_payment() called."""
        enr = _enrollment(payment_verified=False)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db)
        assert result["success"] is True
        enr.verify_payment.assert_called_once_with(42)
        db.commit.assert_called_once()

    def test_vbc04_code_stripped_and_uppercased(self):
        """VBC-04: payment_code is stripped/uppercased before lookup."""
        enr = _enrollment(payment_verified=False)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        # Pass code with leading space and lowercase
        result = self._call(payment_code="  lfa-abcd  ", db=db)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# override_age_category
# ---------------------------------------------------------------------------

class TestOverrideAgeCategory:
    def _call(self, enrollment_id=1, age_category="YOUTH", db=None, current_user=None):
        return asyncio.run(override_age_category(
            request=MagicMock(),
            enrollment_id=enrollment_id,
            override=CategoryOverride(
                age_category=age_category,
                expected_version=1,
                base_participation_retained=True,
                reason="Unit-test movement",
                idempotency_key="unit-test-movement-key",
            ),
            db=db or MagicMock(),
            current_user=current_user or _instructor(),
        ))

    @staticmethod
    def _result(created=True):
        assignment = MagicMock(
            season_base_category="PRE", effective_category="YOUTH",
            base_participation_retained=True, version=2,
        )
        event = MagicMock(id=17)
        return MagicMock(assignment=assignment, event=event, created=created)

    def test_oc01_success_is_thin_canonical_adapter(self):
        db = MagicMock()
        with patch(f"{_BASE_CAT}.move_football_category", return_value=self._result()) as command:
            result = self._call(db=db)
        assert result["season_base_category"] == "PRE"
        assert result["effective_category"] == "YOUTH"
        assert result["movement_event_id"] == 17
        assert result["replayed"] is False
        command.assert_called_once()
        db.commit.assert_called_once()

    def test_oc02_replay_returns_original_result(self):
        with patch(f"{_BASE_CAT}.move_football_category", return_value=self._result(False)):
            assert self._call()["replayed"] is True

    @pytest.mark.parametrize(
        ("error_name", "status_code"),
        [
            ("FootballMovementAuthorizationError", 403),
            ("FootballMovementConcurrencyError", 409),
            ("FootballMovementReplayConflict", 409),
            ("FootballMovementError", 400),
        ],
    )
    def test_oc03_command_errors_are_safely_mapped(self, error_name, status_code):
        from app.api.api_v1.endpoints.semester_enrollments import category_override as module
        error = getattr(module, error_name)("internal detail")
        db = MagicMock()
        with patch(f"{_BASE_CAT}.move_football_category", side_effect=error):
            with pytest.raises(HTTPException) as exc:
                self._call(db=db)
        assert exc.value.status_code == status_code
        assert "internal detail" not in exc.value.detail
        db.rollback.assert_called_once()
