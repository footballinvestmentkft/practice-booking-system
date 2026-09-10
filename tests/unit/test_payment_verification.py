"""
Unit tests for app/api/api_v1/endpoints/payment_verification.py

Coverage targets (async endpoints — called via asyncio.run):
  verify_student_payment()   — POST /students/{student_id}/verify
  unverify_student_payment() — POST /students/{student_id}/unverify
  add_student_specialization()    — POST /students/{student_id}/add-specialization
  remove_student_specialization() — POST /students/{student_id}/remove-specialization
  get_student_payment_status()    — GET  /students/{student_id}/status

Mock strategy
-------------
All routes use db.query(Model).filter(...).first() pattern.
Use a model-discriminating db: lambda *args: user_q if args[0] is User else license_q.
Student mock: MagicMock with .id, .role, .name, .specialization, .payment_verified etc.
License mock: MagicMock with .user_id, .specialization_type attributes.
verify_payment / unverify_payment: side_effect sets attributes on the student mock.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, call, patch
from fastapi import HTTPException

from app.api.api_v1.endpoints.payment_verification import (
    verify_student_payment,
    unverify_student_payment,
    add_student_specialization,
    remove_student_specialization,
    get_student_payment_status,
    get_students_payment_status,
    PaymentVerificationRequest,
    SpecializationRequest,
)
from app.models.user import User, UserRole
from app.models.specialization import SpecializationType


# ── Helpers ────────────────────────────────────────────────────────────────────

def _admin():
    u = MagicMock(spec=User)
    u.id   = 1
    u.name = "Admin User"
    u.role = UserRole.ADMIN
    return u


def _student(student_id: int = 42):
    s = MagicMock(spec=User)
    s.id               = student_id
    s.name             = "Test Student"
    s.email            = "student@test.com"
    s.role             = UserRole.STUDENT
    s.specialization   = None
    s.payment_verified = False
    s.payment_verified_at  = None
    s.payment_verified_by  = None
    s.payment_status_display  = "Unverified"
    s.can_enroll_in_semester  = False
    s.onboarding_completed    = True
    s.nickname                = "ts"
    s.is_active               = True
    s.credit_payment_reference = "REF123"
    s.created_at              = None
    return s


def _make_db(student=None, license_first=None, remaining_licenses=None):
    """
    Build a discriminating mock db:
      db.query(User).filter().first()     → student
      db.query(UserLicense).filter().first() → license_first (1st call)
      db.query(UserLicense).filter().all()   → remaining_licenses
    """
    db = MagicMock()

    user_q    = MagicMock()
    user_q.filter.return_value = user_q
    user_q.first.return_value  = student
    user_q.all.return_value    = [student] if student else []

    license_q = MagicMock()
    license_q.filter.return_value = license_q
    license_q.first.return_value  = license_first
    license_q.all.return_value    = remaining_licenses if remaining_licenses is not None else []

    from app.models.license import UserLicense
    db.query.side_effect = lambda model: user_q if model is User else license_q
    return db


def _run(coro):
    return asyncio.run(coro)


# ── verify_student_payment — 404 path ─────────────────────────────────────────

class TestVerifyStudentPayment404:

    def test_raises_404_when_student_not_found(self):
        db = _make_db(student=None)
        req = PaymentVerificationRequest(specializations=["LFA_FOOTBALL_PLAYER"])
        with pytest.raises(HTTPException) as exc:
            _run(verify_student_payment(
                request=MagicMock(), student_id=99999,
                payment_request=req, db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 404


# ── verify_student_payment — 400 paths ────────────────────────────────────────

class TestVerifyStudentPaymentValidation:

    def test_empty_specializations_raises_400(self):
        db = _make_db(student=_student())
        req = PaymentVerificationRequest(specializations=[])
        with pytest.raises(HTTPException) as exc:
            _run(verify_student_payment(
                request=MagicMock(), student_id=42,
                payment_request=req, db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 400

    def test_invalid_specialization_string_raises_400(self):
        db = _make_db(student=_student())
        req = PaymentVerificationRequest(specializations=["NOT_REAL"])
        with pytest.raises(HTTPException) as exc:
            _run(verify_student_payment(
                request=MagicMock(), student_id=42,
                payment_request=req, db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 400


# ── verify_student_payment — happy path ───────────────────────────────────────

class TestVerifyStudentPaymentHappyPath:

    def _run_verify(self, existing_license=None):
        s = _student()
        s.payment_verified    = True
        s.payment_verified_at = "2026-01-01T00:00:00Z"
        s.specialization      = SpecializationType.LFA_FOOTBALL_PLAYER

        def _verify_payment(admin):
            s.payment_verified = True
        s.verify_payment.side_effect = _verify_payment

        db = _make_db(student=s, license_first=existing_license)
        req = PaymentVerificationRequest(specializations=["LFA_FOOTBALL_PLAYER"])
        with patch(
            "app.api.api_v1.endpoints.payment_verification.issue_football_player_entitlement"
        ) as canonical_issue:
            result = _run(verify_student_payment(
                request=MagicMock(), student_id=42,
                payment_request=req, db=db, current_user=_admin(),
            ))
        db.canonical_issue = canonical_issue
        return result, db, s

    def test_returns_student_id(self):
        result, _, _ = self._run_verify()
        assert result["student_id"] == 42

    def test_returns_verified_flag(self):
        result, _, _ = self._run_verify()
        assert result["payment_verified"] is True

    def test_returns_primary_specialization(self):
        result, _, _ = self._run_verify()
        assert result["primary_specialization"] == "LFA_FOOTBALL_PLAYER"

    def test_canonical_issue_called_when_no_existing_license(self):
        _, db, _ = self._run_verify(existing_license=None)
        db.canonical_issue.assert_called_once()
        db.add.assert_not_called()

    def test_db_add_not_called_when_license_exists(self):
        existing = MagicMock()
        existing.specialization_type = "LFA_FOOTBALL_PLAYER"
        _, db, _ = self._run_verify(existing_license=existing)
        db.canonical_issue.assert_not_called()
        db.add.assert_not_called()

    def test_db_commit_called(self):
        _, db, _ = self._run_verify()
        db.commit.assert_called_once()

    def test_newly_created_licenses_list(self):
        result, _, _ = self._run_verify(existing_license=None)
        assert "LFA_FOOTBALL_PLAYER" in result["newly_created_licenses"]

    def test_newly_created_empty_when_license_exists(self):
        existing = MagicMock()
        existing.specialization_type = "LFA_FOOTBALL_PLAYER"
        result, _, _ = self._run_verify(existing_license=existing)
        assert result["newly_created_licenses"] == []

    def test_verify_payment_called_on_student(self):
        _, _, s = self._run_verify()
        s.verify_payment.assert_called_once()


# ── unverify_student_payment ───────────────────────────────────────────────────

class TestUnverifyStudentPayment:

    def test_raises_404_when_student_not_found(self):
        db = _make_db(student=None)
        with pytest.raises(HTTPException) as exc:
            _run(unverify_student_payment(
                request=MagicMock(), student_id=99999, db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 404

    def test_returns_payment_verified_false(self):
        s = _student()
        s.payment_verified = False
        s.unverify_payment.return_value = None
        db = _make_db(student=s)
        result = _run(unverify_student_payment(
            request=MagicMock(), student_id=42, db=db, current_user=_admin(),
        ))
        assert result["payment_verified"] is False

    def test_unverify_payment_called(self):
        s = _student()
        db = _make_db(student=s)
        _run(unverify_student_payment(
            request=MagicMock(), student_id=42, db=db, current_user=_admin(),
        ))
        s.unverify_payment.assert_called_once()


# ── add_student_specialization ─────────────────────────────────────────────────

class TestAddStudentSpecialization:

    def test_raises_404_when_student_not_found(self):
        db = _make_db(student=None)
        req = SpecializationRequest(specialization_type="LFA_COACH")
        with pytest.raises(HTTPException) as exc:
            _run(add_student_specialization(
                request=MagicMock(), student_id=99, spec_request=req,
                db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 404

    def test_raises_400_for_invalid_specialization(self):
        db = _make_db(student=_student())
        req = SpecializationRequest(specialization_type="INVALID")
        with pytest.raises(HTTPException) as exc:
            _run(add_student_specialization(
                request=MagicMock(), student_id=42, spec_request=req,
                db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 400

    def test_raises_400_when_license_already_exists(self):
        existing = MagicMock()
        existing.specialization_type = "LFA_COACH"
        db = _make_db(student=_student(), license_first=existing)
        req = SpecializationRequest(specialization_type="LFA_COACH")
        with pytest.raises(HTTPException) as exc:
            _run(add_student_specialization(
                request=MagicMock(), student_id=42, spec_request=req,
                db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 400

    def test_happy_path_returns_success(self):
        s = _student()
        s.specialization   = None
        s.payment_verified = False
        s.verify_payment.return_value = None
        db = _make_db(student=s, license_first=None)
        req = SpecializationRequest(specialization_type="LFA_COACH")
        result = _run(add_student_specialization(
            request=MagicMock(), student_id=42, spec_request=req,
            db=db, current_user=_admin(),
        ))
        assert result["success"] is True
        assert result["specialization_added"] == "LFA_COACH"

    def test_sets_primary_when_no_existing_specialization(self):
        s = _student()
        s.specialization   = None
        s.payment_verified = True
        db = _make_db(student=s, license_first=None)
        req = SpecializationRequest(specialization_type="LFA_COACH")
        _run(add_student_specialization(
            request=MagicMock(), student_id=42, spec_request=req,
            db=db, current_user=_admin(),
        ))
        assert s.specialization == SpecializationType.LFA_COACH


# ── get_student_payment_status ─────────────────────────────────────────────────

class TestGetStudentPaymentStatus:

    def test_raises_404_when_student_not_found(self):
        db = _make_db(student=None)
        with pytest.raises(HTTPException) as exc:
            _run(get_student_payment_status(
                request=MagicMock(), student_id=99, db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 404

    def test_returns_student_id(self):
        s = _student()
        s.payment_verified_by = None
        db = _make_db(student=s)
        result = _run(get_student_payment_status(
            request=MagicMock(), student_id=42, db=db, current_user=_admin(),
        ))
        assert result["student_id"] == 42

    def test_verifier_name_none_when_not_verified_by_anyone(self):
        s = _student()
        s.payment_verified_by = None
        db = _make_db(student=s)
        result = _run(get_student_payment_status(
            request=MagicMock(), student_id=42, db=db, current_user=_admin(),
        ))
        assert result["payment_verifier_name"] is None

    def test_verifier_name_resolved_when_payment_verified_by_set(self):
        s = _student()
        s.payment_verified_by = 1  # admin id

        admin_mock = MagicMock()
        admin_mock.name = "Admin User"

        # First query → student, second query → admin (verifier)
        db = MagicMock()
        call_count = [0]
        user_q = MagicMock()
        user_q.filter.return_value = user_q

        results = [s, admin_mock]
        def _first():
            result = results[call_count[0]]
            call_count[0] += 1
            return result
        user_q.first.side_effect = _first

        from app.models.license import UserLicense
        license_q = MagicMock()
        license_q.filter.return_value = license_q
        db.query.side_effect = lambda model: user_q

        result = _run(get_student_payment_status(
            request=MagicMock(), student_id=42, db=db, current_user=_admin(),
        ))
        assert result["payment_verifier_name"] == "Admin User"


# ── get_students_payment_status (GET /students) ───────────────────────────────

class TestGetStudentsPaymentStatus:

    def test_returns_list(self):
        s = _student()
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = [s]
        db.query.return_value = q
        result = _run(get_students_payment_status(
            request=MagicMock(), db=db, current_user=_admin(),
        ))
        assert isinstance(result, list)
        assert len(result) == 1

    def test_returns_empty_list_when_no_students(self):
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = []
        db.query.return_value = q
        result = _run(get_students_payment_status(
            request=MagicMock(), db=db, current_user=_admin(),
        ))
        assert result == []

    def test_each_entry_has_id_and_email(self):
        s = _student()
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = [s]
        db.query.return_value = q
        result = _run(get_students_payment_status(
            request=MagicMock(), db=db, current_user=_admin(),
        ))
        entry = result[0]
        assert "id" in entry
        assert "email" in entry
        assert "payment_verified" in entry


# ── remove_student_specialization ─────────────────────────────────────────────

class TestRemoveStudentSpecialization:

    def _make_remove_db(self, student=None, license_to_remove=None, remaining=None):
        """
        Build db for remove path:
          query(User).filter().first()       → student
          query(UserLicense).filter().first() → license_to_remove
          query(UserLicense).filter().all()   → remaining (for remaining_licenses check)
        """
        from app.models.license import UserLicense
        db = MagicMock()

        user_q = MagicMock()
        user_q.filter.return_value = user_q
        user_q.first.return_value  = student

        lic_q = MagicMock()
        lic_q.filter.return_value = lic_q
        lic_q.first.return_value  = license_to_remove
        lic_q.all.return_value    = remaining or []

        db.query.side_effect = lambda model: user_q if model is User else lic_q
        return db

    def test_raises_404_when_student_not_found(self):
        db = self._make_remove_db(student=None)
        req = SpecializationRequest(specialization_type="LFA_COACH")
        with pytest.raises(HTTPException) as exc:
            _run(remove_student_specialization(
                request=MagicMock(), student_id=99, spec_request=req,
                db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 404

    def test_raises_400_for_invalid_specialization(self):
        db = self._make_remove_db(student=_student())
        req = SpecializationRequest(specialization_type="INVALID_SPEC")
        with pytest.raises(HTTPException) as exc:
            _run(remove_student_specialization(
                request=MagicMock(), student_id=42, spec_request=req,
                db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 400

    def test_raises_404_when_license_not_found(self):
        db = self._make_remove_db(student=_student(), license_to_remove=None)
        req = SpecializationRequest(specialization_type="LFA_COACH")
        with pytest.raises(HTTPException) as exc:
            _run(remove_student_specialization(
                request=MagicMock(), student_id=42, spec_request=req,
                db=db, current_user=_admin(),
            ))
        assert exc.value.status_code == 404

    def test_happy_path_returns_success(self):
        s = _student()
        s.specialization   = SpecializationType.LFA_COACH
        s.payment_verified = True
        lic = MagicMock()
        lic.specialization_type = "LFA_COACH"
        # One remaining license after removal
        remaining = [MagicMock()]
        remaining[0].specialization_type = "LFA_FOOTBALL_PLAYER"
        db = self._make_remove_db(student=s, license_to_remove=lic, remaining=remaining)
        req = SpecializationRequest(specialization_type="LFA_COACH")
        result = _run(remove_student_specialization(
            request=MagicMock(), student_id=42, spec_request=req,
            db=db, current_user=_admin(),
        ))
        assert result["success"] is True
        assert result["specialization_removed"] == "LFA_COACH"

    def test_unverifies_payment_when_no_licenses_remain(self):
        s = _student()
        s.specialization   = SpecializationType.LFA_COACH
        s.payment_verified = True
        lic = MagicMock()
        lic.specialization_type = "LFA_COACH"
        db = self._make_remove_db(student=s, license_to_remove=lic, remaining=[])
        req = SpecializationRequest(specialization_type="LFA_COACH")
        _run(remove_student_specialization(
            request=MagicMock(), student_id=42, spec_request=req,
            db=db, current_user=_admin(),
        ))
        s.unverify_payment.assert_called_once()

    def test_db_delete_called_on_license(self):
        s = _student()
        s.specialization   = SpecializationType.LFA_COACH
        s.payment_verified = True
        lic = MagicMock()
        lic.specialization_type = "LFA_COACH"
        db = self._make_remove_db(student=s, license_to_remove=lic, remaining=[])
        req = SpecializationRequest(specialization_type="LFA_COACH")
        _run(remove_student_specialization(
            request=MagicMock(), student_id=42, spec_request=req,
            db=db, current_user=_admin(),
        ))
        db.delete.assert_called_once_with(lic)
        db.commit.assert_called_once()
