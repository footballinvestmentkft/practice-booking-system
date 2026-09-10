"""
Unit tests for semester_enrollments/workflow.py + crud.py
Covers: approve/reject enrollment (workflow), create/delete/toggle enrollment (crud)
All endpoints are async → asyncio.run()
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch

from app.api.api_v1.endpoints.semester_enrollments.workflow import (
    approve_enrollment_request,
    reject_enrollment_request,
)
from app.api.api_v1.endpoints.semester_enrollments.crud import (
    create_enrollment,
    delete_enrollment,
    toggle_enrollment_active,
)
from app.api.api_v1.endpoints.semester_enrollments.schemas import (
    EnrollmentCreate,
    EnrollmentRejection,
)
from app.models.user import User, UserRole
from app.models.semester import Semester
from app.models.license import UserLicense
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus

_BASE_WORKFLOW = "app.api.api_v1.endpoints.semester_enrollments.workflow"
_BASE_CRUD = "app.api.api_v1.endpoints.semester_enrollments.crud"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(uid=42, role=UserRole.ADMIN):
    u = MagicMock(); u.id = uid; u.name = "Admin"; u.role = role; return u


def _instructor(uid=42, master_id_to_match=None):
    u = _user(uid=uid, role=UserRole.INSTRUCTOR)
    return u


def _q(first_val=None, all_val=None):
    """Fluent query mock — handles .options().filter().first() chains."""
    q = MagicMock()
    q.options.return_value = q
    q.filter.return_value = q
    q.all.return_value = all_val or []
    q.first.return_value = first_val
    return q


def _enrollment(status=EnrollmentStatus.PENDING, user_id=42, master_instructor_id=99):
    e = MagicMock()
    e.id = 1
    e.user_id = user_id
    e.request_status = status
    e.semester.master_instructor_id = master_instructor_id
    e.user.name = "Student Alice"
    e.rejection_reason = "Test reason"
    e.is_active = True
    return e


def _model_db(student=None, semester=None, lic=None, existing=None):
    """DB mock that routes db.query(Model) to different mocks by type."""
    def qside(*args):
        model = args[0]
        if model is User:
            return _q(first_val=student)
        elif model is Semester:
            return _q(first_val=semester)
        elif model is UserLicense:
            return _q(first_val=lic)
        elif model is SemesterEnrollment:
            return _q(first_val=existing)
        return _q()
    db = MagicMock()
    db.query.side_effect = qside
    return db


# ---------------------------------------------------------------------------
# approve_enrollment_request
# ---------------------------------------------------------------------------

class TestApproveEnrollmentRequest:
    def _call(self, enrollment_id=1, db=None, current_user=None):
        return asyncio.run(approve_enrollment_request(
            enrollment_id=enrollment_id,
            db=db or MagicMock(),
            current_user=current_user or _user(role=UserRole.ADMIN),
        ))

    def test_ae01_not_found_404(self):
        """AE-01: enrollment not found → 404."""
        from fastapi import HTTPException
        db = MagicMock(); db.query.return_value = _q(first_val=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_ae02_not_pending_400(self):
        """AE-02: enrollment not PENDING → 400."""
        from fastapi import HTTPException
        enr = _enrollment(status=EnrollmentStatus.APPROVED)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_ae03_student_role_403(self):
        """AE-03: student role → 403."""
        from fastapi import HTTPException
        enr = _enrollment(status=EnrollmentStatus.PENDING, user_id=99, master_instructor_id=77)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, current_user=_user(uid=42, role=UserRole.STUDENT))
        assert exc.value.status_code == 403

    def test_ae04_instructor_not_master_403(self):
        """AE-04: instructor but not master_instructor of semester → 403."""
        from fastapi import HTTPException
        enr = _enrollment(status=EnrollmentStatus.PENDING, user_id=99, master_instructor_id=77)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        # instructor id=42 but master_instructor_id=77
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, current_user=_user(uid=42, role=UserRole.INSTRUCTOR))
        assert exc.value.status_code == 403

    def test_ae05_admin_approves(self):
        """AE-05: admin → .approve() called, committed."""
        enr = _enrollment(status=EnrollmentStatus.PENDING, user_id=99, master_instructor_id=77)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db, current_user=_user(uid=42, role=UserRole.ADMIN))
        assert result["success"] is True
        assert result["approved_by"] == "Admin"
        enr.approve.assert_called_once_with(42)
        db.commit.assert_called_once()

    def test_ae06_master_instructor_approves(self):
        """AE-06: master_instructor of semester → .approve() called, success."""
        enr = _enrollment(status=EnrollmentStatus.PENDING, user_id=99, master_instructor_id=42)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db, current_user=_user(uid=42, role=UserRole.INSTRUCTOR))
        assert result["success"] is True
        assert result["approved_by"] == "Master Instructor"
        enr.approve.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# reject_enrollment_request
# ---------------------------------------------------------------------------

class TestRejectEnrollmentRequest:
    def _call(self, enrollment_id=1, db=None, current_user=None, reason="Bad fit"):
        return asyncio.run(reject_enrollment_request(
            enrollment_id=enrollment_id,
            rejection=EnrollmentRejection(reason=reason),
            db=db or MagicMock(),
            current_user=current_user or _user(role=UserRole.ADMIN),
        ))

    def test_re01_not_found_404(self):
        """RE-01: enrollment not found → 404."""
        from fastapi import HTTPException
        db = MagicMock(); db.query.return_value = _q(first_val=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_re02_not_pending_400(self):
        """RE-02: enrollment already REJECTED → 400."""
        from fastapi import HTTPException
        enr = _enrollment(status=EnrollmentStatus.REJECTED)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_re03_no_permission_403(self):
        """RE-03: student role → 403."""
        from fastapi import HTTPException
        enr = _enrollment(status=EnrollmentStatus.PENDING, user_id=99, master_instructor_id=77)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, current_user=_user(uid=42, role=UserRole.STUDENT))
        assert exc.value.status_code == 403

    def test_re04_admin_rejects(self):
        """RE-04: admin → .reject() called, committed."""
        enr = _enrollment(status=EnrollmentStatus.PENDING, user_id=99, master_instructor_id=77)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db, current_user=_user(uid=42, role=UserRole.ADMIN))
        assert result["success"] is True
        enr.reject.assert_called_once()
        db.commit.assert_called_once()

    def test_re05_master_instructor_rejects(self):
        """RE-05: master_instructor → .reject() called."""
        enr = _enrollment(status=EnrollmentStatus.PENDING, user_id=99, master_instructor_id=42)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db, current_user=_user(uid=42, role=UserRole.INSTRUCTOR))
        assert result["success"] is True
        enr.reject.assert_called_once()

    def test_re06_no_reason_defaults(self):
        """RE-06: reason=None → 'No reason provided' passed to reject()."""
        enr = _enrollment(status=EnrollmentStatus.PENDING, user_id=99, master_instructor_id=42)
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db, current_user=_user(uid=42, role=UserRole.ADMIN), reason=None)
        assert result["success"] is True
        enr.reject.assert_called_once_with(42, "No reason provided")


# ---------------------------------------------------------------------------
# create_enrollment
# ---------------------------------------------------------------------------

class TestCreateEnrollment:
    def _call(self, db=None, current_user=None, user_id=42, semester_id=1, user_license_id=42):
        enr = EnrollmentCreate(user_id=user_id, semester_id=semester_id, user_license_id=user_license_id)
        return asyncio.run(create_enrollment(
            request=MagicMock(),
            enrollment=enr,
            db=db or MagicMock(),
            current_user=current_user or _user(role=UserRole.ADMIN),
        ))

    def test_ce01_student_not_found_404(self):
        """CE-01: student not found → 404."""
        from fastapi import HTTPException
        db = _model_db(student=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_ce02_semester_not_found_404(self):
        """CE-02: semester not found → 404."""
        from fastapi import HTTPException
        student = MagicMock(); student.date_of_birth = None
        db = _model_db(student=student, semester=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_ce03_license_not_found_404(self):
        """CE-03: user_license not found → 404."""
        from fastapi import HTTPException
        student = MagicMock(); student.date_of_birth = None
        semester = MagicMock()
        db = _model_db(student=student, semester=semester, lic=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_ce04_already_enrolled_400(self):
        """CE-04: enrollment already exists → 400."""
        from fastapi import HTTPException
        student = MagicMock(); student.date_of_birth = MagicMock()
        semester = MagicMock()
        lic = MagicMock(); lic.specialization_type = "LFA_FOOTBALL_PLAYER"; lic.canonical_program_id = "LFA_FOOTBALL_PLAYER"
        existing = MagicMock()
        db = _model_db(student=student, semester=semester, lic=lic, existing=existing)
        with patch(f"{_BASE_CRUD}.is_user_eligible_for_program", return_value=(True, None)):
            with pytest.raises(HTTPException) as exc:
                self._call(db=db)
        assert exc.value.status_code == 400

    def test_ce05_missing_dob_is_denied(self):
        """CE-05: a usable enrollment profile requires date_of_birth."""
        student = MagicMock(); student.date_of_birth = None; student.name = "Alice"
        semester = MagicMock(); semester.code = "2025Q1"; semester.parent_semester_id = None
        lic = MagicMock(); lic.specialization_type = "LFA_FOOTBALL_PLAYER"; lic.canonical_program_id = "LFA_FOOTBALL_PLAYER"
        db = _model_db(student=student, semester=semester, lic=lic, existing=None)
        with patch(f"{_BASE_CRUD}.is_user_eligible_for_program", return_value=(False, "DATE_OF_BIRTH_REQUIRED")):
            with pytest.raises(Exception) as exc:
                self._call(db=db)
        assert exc.value.status_code == 403

    def test_ce06_success_with_dob_age_category(self):
        """CE-06: student has date_of_birth → age_category auto-assigned."""
        from datetime import date
        student = MagicMock(); student.date_of_birth = date(2010, 3, 1); student.name = "Bob"
        semester = MagicMock(); semester.code = "2025Q1"; semester.parent_semester_id = None
        lic = MagicMock(); lic.specialization_type = "LFA_FOOTBALL_PLAYER"; lic.canonical_program_id = "LFA_FOOTBALL_PLAYER"
        db = _model_db(student=student, semester=semester, lic=lic, existing=None)
        assignment = MagicMock(id=7, effective_category="YOUTH")
        with patch(f"{_BASE_CRUD}.is_user_eligible_for_program", return_value=(True, None)), \
             patch(f"{_BASE_CRUD}.get_or_create_current_football_assignment", return_value=assignment):
            result = self._call(db=db)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# delete_enrollment
# ---------------------------------------------------------------------------

class TestDeleteEnrollment:
    def _call(self, enrollment_id=1, db=None):
        return asyncio.run(delete_enrollment(
            request=MagicMock(),
            enrollment_id=enrollment_id,
            db=db or MagicMock(),
            current_user=_user(role=UserRole.ADMIN),
        ))

    def test_de01_not_found_404(self):
        """DE-01: enrollment not found → 404."""
        from fastapi import HTTPException
        db = MagicMock(); db.query.return_value = _q(first_val=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_de02_success(self):
        """DE-02: enrollment found → deleted, committed."""
        enr = _enrollment()
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db)
        assert result["success"] is True
        db.delete.assert_called_once_with(enr)
        db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# toggle_enrollment_active
# ---------------------------------------------------------------------------

class TestToggleEnrollmentActive:
    def _call(self, enrollment_id=1, db=None):
        return asyncio.run(toggle_enrollment_active(
            request=MagicMock(),
            enrollment_id=enrollment_id,
            db=db or MagicMock(),
            current_user=_user(role=UserRole.ADMIN),
        ))

    def test_te01_not_found_404(self):
        """TE-01: not found → 404."""
        from fastapi import HTTPException
        db = MagicMock(); db.query.return_value = _q(first_val=None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_te02_active_deactivates(self):
        """TE-02: is_active=True → deactivate() called."""
        enr = _enrollment(); enr.is_active = True
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db)
        assert result["success"] is True
        enr.deactivate.assert_called_once()
        assert "deactivated" in result["message"]

    def test_te03_inactive_reactivates(self):
        """TE-03: is_active=False → reactivate() called."""
        enr = _enrollment(); enr.is_active = False
        db = MagicMock(); db.query.return_value = _q(first_val=enr)
        result = self._call(db=db)
        assert result["success"] is True
        enr.reactivate.assert_called_once()
        assert "reactivated" in result["message"]
