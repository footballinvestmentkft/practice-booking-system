"""
Unit tests for Admin CRUD routes in app/api/web_routes/admin.py

Covers:
  admin_toggle_user_status  — non-admin 403, target not found 404,
                              self-toggle blocked, active→inactive, inactive→active
  admin_edit_user_page      — non-admin 403, not found 404, renders user_edit.html
  admin_edit_user_submit    — non-admin 403, bad role, duplicate email, success
  admin_new_semester_page   — non-admin 403, renders semester_new.html
  admin_new_semester_submit — bad dates, end<=start, duplicate code, success
  admin_delete_semester     — non-admin 403, not found 404,
                              has active enrollments → deactivate, no enrollments → delete
"""
import asyncio
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app.api.web_routes.admin.users import (
    admin_edit_user_page,
    admin_edit_user_submit,
    admin_toggle_user_status,
)
from app.api.web_routes.admin.semesters import (
    admin_delete_semester,
    admin_new_semester_page,
    admin_new_semester_submit,
)
from app.models.user import UserRole

_USERS_BASE = "app.api.web_routes.admin.users"
_SEMS_BASE = "app.api.web_routes.admin.semesters"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    return MagicMock()


def _admin_user():
    u = MagicMock()
    u.id = 1
    u.role = UserRole.ADMIN
    u.email = "admin@lfa.com"
    return u


def _student_user():
    u = MagicMock()
    u.id = 2
    u.role = UserRole.STUDENT
    u.email = "student@test.com"
    u.is_active = True
    return u


def _mock_db(first_return=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = first_return
    return db


# ──────────────────────────────────────────────────────────────────────────────
# admin_toggle_user_status  (POST /admin/users/{id}/toggle-status)
# ──────────────────────────────────────────────────────────────────────────────

class TestToggleUserStatus:

    def test_non_admin_raises_403(self):
        non_admin = _student_user()
        with pytest.raises(HTTPException) as exc:
            _run(admin_toggle_user_status(
                user_id=2, request=_req(), db=_mock_db(), user=non_admin
            ))
        assert exc.value.status_code == 403

    def test_target_not_found_raises_404(self):
        admin = _admin_user()
        db = _mock_db(first_return=None)
        with pytest.raises(HTTPException) as exc:
            _run(admin_toggle_user_status(
                user_id=999, request=_req(), db=db, user=admin
            ))
        assert exc.value.status_code == 404

    def test_self_toggle_raises_400(self):
        admin = _admin_user()
        # target.id == user.id → 400
        target = MagicMock()
        target.id = admin.id
        db = _mock_db(first_return=target)
        with pytest.raises(HTTPException) as exc:
            _run(admin_toggle_user_status(
                user_id=admin.id, request=_req(), db=db, user=admin
            ))
        assert exc.value.status_code == 400

    def test_active_user_becomes_inactive(self):
        admin = _admin_user()
        target = _student_user()
        target.id = 42
        target.is_active = True
        db = _mock_db(first_return=target)
        result = _run(admin_toggle_user_status(
            user_id=42, request=_req(), db=db, user=admin
        ))
        assert target.is_active is False
        db.commit.assert_called_once()
        assert isinstance(result, RedirectResponse)
        assert "/admin/users" in result.headers["location"]

    def test_inactive_user_becomes_active(self):
        admin = _admin_user()
        target = _student_user()
        target.id = 55
        target.is_active = False
        db = _mock_db(first_return=target)
        result = _run(admin_toggle_user_status(
            user_id=55, request=_req(), db=db, user=admin
        ))
        assert target.is_active is True
        db.commit.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# admin_edit_user_page  (GET /admin/users/{id}/edit)
# ──────────────────────────────────────────────────────────────────────────────

class TestEditUserPage:

    def test_non_admin_raises_403(self):
        with pytest.raises(HTTPException) as exc:
            _run(admin_edit_user_page(
                user_id=2, request=_req(), db=_mock_db(), user=_student_user()
            ))
        assert exc.value.status_code == 403

    def test_target_not_found_raises_404(self):
        with pytest.raises(HTTPException) as exc:
            _run(admin_edit_user_page(
                user_id=999, request=_req(), db=_mock_db(None), user=_admin_user()
            ))
        assert exc.value.status_code == 404

    def test_renders_user_edit_template(self):
        target = _student_user()
        db = _mock_db(first_return=target)
        with patch(f"{_USERS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_edit_user_page(
                user_id=target.id, request=_req(), db=db, user=_admin_user()
            ))
        tmpl_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert tmpl_name == "admin/user_edit.html"


# ──────────────────────────────────────────────────────────────────────────────
# admin_edit_user_submit  (POST /admin/users/{id}/edit)
# ──────────────────────────────────────────────────────────────────────────────

class TestEditUserSubmit:

    def test_non_admin_raises_403(self):
        with pytest.raises(HTTPException) as exc:
            _run(admin_edit_user_submit(
                user_id=2, request=_req(), name="X", email="x@x.com",
                role="STUDENT", db=_mock_db(), user=_student_user()
            ))
        assert exc.value.status_code == 403

    def test_invalid_role_raises_400(self):
        target = _student_user()
        db = _mock_db(first_return=target)
        with pytest.raises(HTTPException) as exc:
            _run(admin_edit_user_submit(
                user_id=2, request=_req(), name="Test", email="t@t.com",
                role="super_admin_invalid", db=db, user=_admin_user()
            ))
        assert exc.value.status_code == 400

    def test_duplicate_email_renders_error(self):
        target = _student_user()
        target.email = "original@test.com"

        other = MagicMock()
        other.id = 99
        other.email = "taken@test.com"

        db = MagicMock()
        # First query: get target by user_id; second: check email uniqueness → existing user
        db.query.return_value.filter.return_value.first.side_effect = [target, other]

        with patch(f"{_USERS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_edit_user_submit(
                user_id=2, request=_req(), name="Test User",
                email="taken@test.com", role="student", db=db, user=_admin_user()
            ))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx

    def test_successful_edit_commits_and_redirects(self):
        target = _student_user()
        target.email = "student@test.com"

        db = MagicMock()
        # First query: get target; email unchanged → no second query for email check
        db.query.return_value.filter.return_value.first.return_value = target

        result = _run(admin_edit_user_submit(
            user_id=2, request=_req(), name="Updated Name",
            email="student@test.com",  # same email
            role="student", db=db, user=_admin_user()
        ))
        db.commit.assert_called_once()
        assert isinstance(result, RedirectResponse)
        assert "/admin/users" in result.headers["location"]


# ──────────────────────────────────────────────────────────────────────────────
# admin_new_semester_page  (GET /admin/semesters/new)
# ──────────────────────────────────────────────────────────────────────────────

class TestNewSemesterPage:

    def test_non_admin_raises_403(self):
        with pytest.raises(HTTPException) as exc:
            _run(admin_new_semester_page(
                request=_req(), db=_mock_db(), user=_student_user()
            ))
        assert exc.value.status_code == 403

    def test_renders_semester_new_template(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        with patch(f"{_SEMS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_new_semester_page(
                request=_req(), db=db, user=_admin_user()
            ))
        tmpl_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert tmpl_name == "admin/semester_new.html"


# ──────────────────────────────────────────────────────────────────────────────
# admin_new_semester_submit  (POST /admin/semesters/new)
# ──────────────────────────────────────────────────────────────────────────────

class TestNewSemesterSubmit:

    def _db_with_instructors(self, code_exists=False):
        db = MagicMock()
        # instructors query
        db.query.return_value.filter.return_value.all.return_value = []
        # code uniqueness check
        db.query.return_value.filter.return_value.first.return_value = (
            MagicMock() if code_exists else None
        )
        return db

    def test_non_admin_raises_403(self):
        with pytest.raises(HTTPException) as exc:
            _run(admin_new_semester_submit(
                request=_req(), code="X", name="Y",
                start_date="2026-06-01", end_date="2026-09-01",
                db=_mock_db(), user=_student_user()
            ))
        assert exc.value.status_code == 403

    def test_invalid_date_format_renders_error(self):
        db = self._db_with_instructors()
        with patch(f"{_SEMS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_new_semester_submit(
                request=_req(), code="TEST_2026", name="Test",
                start_date="not-a-date", end_date="also-bad",
                master_instructor_id="",
                db=db, user=_admin_user()
            ))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx

    def test_end_before_start_renders_error(self):
        db = self._db_with_instructors()
        with patch(f"{_SEMS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_new_semester_submit(
                request=_req(), code="TEST_2026", name="Test",
                start_date="2026-09-01", end_date="2026-06-01",
                master_instructor_id="",
                db=db, user=_admin_user()
            ))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx

    def test_duplicate_code_renders_error(self):
        db = self._db_with_instructors(code_exists=True)
        with patch(f"{_SEMS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_new_semester_submit(
                request=_req(), code="EXISTING_CODE", name="Test",
                start_date="2026-06-01", end_date="2026-09-01",
                specialization_type="", master_instructor_id="", location_id="",
                db=db, user=_admin_user()
            ))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx

    def test_valid_semester_commits_and_redirects(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.first.return_value = None  # code not taken

        with patch(f"{_SEMS_BASE}.Semester") as mock_sem_cls:
            new_sem = MagicMock()
            new_sem.code = "NEW_SEM_2026"
            mock_sem_cls.return_value = new_sem
            result = _run(admin_new_semester_submit(
                request=_req(), code="NEW_SEM_2026", name="New Semester",
                start_date="2026-06-01", end_date="2026-09-01",
                specialization_type="", master_instructor_id="", location_id="",
                db=db, user=_admin_user()
            ))
        db.add.assert_called_once_with(new_sem)
        db.commit.assert_called_once()
        assert isinstance(result, RedirectResponse)
        assert "/admin/semesters" in result.headers["location"]


# ──────────────────────────────────────────────────────────────────────────────
# admin_delete_semester  (POST /admin/semesters/{id}/delete)
# ──────────────────────────────────────────────────────────────────────────────

class TestDeleteSemester:

    def test_non_admin_raises_403(self):
        with pytest.raises(HTTPException) as exc:
            _run(admin_delete_semester(
                semester_id=1, request=_req(), db=_mock_db(), user=_student_user()
            ))
        assert exc.value.status_code == 403

    def test_semester_not_found_raises_404(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc:
            _run(admin_delete_semester(
                semester_id=999, request=_req(), db=db, user=_admin_user()
            ))
        assert exc.value.status_code == 404

    def test_semester_with_active_enrollments_cancelled_not_deleted(self):
        sem = MagicMock()
        sem.id = 10
        sem.code = "SEM_2026"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = sem
        db.query.return_value.filter.return_value.count.return_value = 5  # active enrollments

        result = _run(admin_delete_semester(
            semester_id=10, request=_req(), db=db, user=_admin_user()
        ))
        from app.models.semester import SemesterStatus
        assert sem.status == SemesterStatus.CANCELLED
        db.commit.assert_called_once()
        db.delete.assert_not_called()
        assert isinstance(result, RedirectResponse)

    def test_semester_without_enrollments_deleted(self):
        sem = MagicMock()
        sem.id = 11
        sem.code = "EMPTY_SEM"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = sem
        db.query.return_value.filter.return_value.count.return_value = 0  # no active enrollments

        result = _run(admin_delete_semester(
            semester_id=11, request=_req(), db=db, user=_admin_user()
        ))
        db.delete.assert_called_once_with(sem)
        db.commit.assert_called_once()
        assert isinstance(result, RedirectResponse)
        assert "/admin/semesters" in result.headers["location"]
