"""
Unit tests for app/api/web_routes/instructor_dashboard.py

Covers:
  instructor_enrollments_page — not_instructor, no_semesters (if False), with_semesters (if True)
  instructor_edit_student_skills_page — not_instructor, student_not_found, license_not_found,
                                         not_lfa_player, success
  instructor_update_student_skills — not_instructor, student_not_found, license_not_found,
                                      not_lfa_player, invalid_range, success_with_notes,
                                      success_without_notes

Mock strategy:
  - asyncio.run(endpoint(...)) direct call — bypasses FastAPI DI
  - AuditService + AuditAction imported in source (fixed Sprint 54 P1)
  - db.query().filter().all() → return_value chain for list queries
  - db.query().filter().first() → side_effect list for multi-query routes
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.api.web_routes.instructor_dashboard import (
    instructor_enrollments_page,
    instructor_edit_student_skills_page,
    instructor_update_student_skills,
)
from app.models.user import UserRole


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_BASE = "app.api.web_routes.instructor_dashboard"


def _instructor(uid=42):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.INSTRUCTOR
    u.name = "Coach"
    return u


def _student_user(uid=7):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    return u


def _req():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────────
# instructor_enrollments_page
# ──────────────────────────────────────────────────────────────────────────────

class TestInstructorEnrollmentsPage:

    def test_not_instructor_raises_403(self):
        """User is not instructor → 403."""
        user = _student_user()
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            _run(instructor_enrollments_page(request=_req(), db=db, user=user))
        assert exc.value.status_code == 403

    def test_no_semesters_returns_empty_groups(self):
        """No semesters → semester_ids=[] → if semester_ids: False → all_enrollments=[]."""
        user = _instructor()
        db = MagicMock()
        # Semester query returns empty list
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(instructor_enrollments_page(request=_req(), db=db, user=user))

        mock_tmpl.TemplateResponse.assert_called_once()
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["instructor_semesters"] == []
        assert all(g["total_count"] == 0 for g in ctx["specialization_groups"].values())

    def test_with_semesters_queries_enrollments(self):
        """Semesters found → semester_ids non-empty → if semester_ids: True → query enrollments."""
        user = _instructor()
        sem = MagicMock()
        sem.id = 10
        db = MagicMock()
        # Semester query returns one semester
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [sem]
        # Enrollment options chain returns empty list
        db.query.return_value.options.return_value.filter.return_value.order_by.return_value.all.return_value = []

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(instructor_enrollments_page(request=_req(), db=db, user=user))

        mock_tmpl.TemplateResponse.assert_called_once()
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["instructor_semesters"] == [sem]


# ──────────────────────────────────────────────────────────────────────────────
# instructor_edit_student_skills_page
# ──────────────────────────────────────────────────────────────────────────────

class TestInstructorEditStudentSkillsPage:

    def test_not_instructor_raises_403(self):
        user = _student_user()
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            _run(instructor_edit_student_skills_page(
                request=_req(), student_id=7, license_id=1, db=db, user=user
            ))
        assert exc.value.status_code == 403

    def test_student_not_found_raises_404(self):
        user = _instructor()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            _run(instructor_edit_student_skills_page(
                request=_req(), student_id=7, license_id=1, db=db, user=user
            ))
        assert exc.value.status_code == 404
        assert "Student not found" in exc.value.detail

    def test_license_not_found_raises_404(self):
        user = _instructor()
        student = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, None]

        with pytest.raises(HTTPException) as exc:
            _run(instructor_edit_student_skills_page(
                request=_req(), student_id=7, license_id=1, db=db, user=user
            ))
        assert exc.value.status_code == 404
        assert "License not found" in exc.value.detail

    def test_not_lfa_player_raises_400(self):
        user = _instructor()
        student = MagicMock()
        license = MagicMock()
        license.user_id = 7
        license.specialization_type = "GANCUJU_PLAYER"  # Not LFA_PLAYER_
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license]

        with pytest.raises(HTTPException) as exc:
            _run(instructor_edit_student_skills_page(
                request=_req(), student_id=7, license_id=1, db=db, user=user
            ))
        assert exc.value.status_code == 400

    def test_lfa_player_success_renders_template(self):
        user = _instructor()
        student = MagicMock()
        license = MagicMock()
        license.user_id = 7
        license.specialization_type = "LFA_FOOTBALL_PLAYER"
        license.canonical_program_id = "LFA_FOOTBALL_PLAYER"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(instructor_edit_student_skills_page(
                request=_req(), student_id=7, license_id=1, db=db, user=user
            ))

        mock_tmpl.TemplateResponse.assert_called_once()
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["student"] is student
        assert ctx["license"] is license
        assert ctx["specialization_display"] == "LFA Football Player"


# ──────────────────────────────────────────────────────────────────────────────
# instructor_update_student_skills (POST)
# ──────────────────────────────────────────────────────────────────────────────

_VALID_SKILLS = dict(
    heading=75.0, shooting=80.0, crossing=65.0,
    passing=70.0, dribbling=85.0, ball_control=90.0,
    instructor_notes="Good progress",
)

_VALID_SKILLS_NO_NOTES = dict(
    heading=50.0, shooting=50.0, crossing=50.0,
    passing=50.0, dribbling=50.0, ball_control=50.0,
    instructor_notes="",
)


class TestInstructorUpdateStudentSkills:

    def test_not_instructor_raises_403(self):
        user = _student_user()
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            _run(instructor_update_student_skills(
                request=_req(), student_id=7, license_id=1, db=db, user=user,
                **_VALID_SKILLS,
            ))
        assert exc.value.status_code == 403

    def test_student_not_found_raises_404(self):
        user = _instructor()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            _run(instructor_update_student_skills(
                request=_req(), student_id=7, license_id=1, db=db, user=user,
                **_VALID_SKILLS,
            ))
        assert exc.value.status_code == 404
        assert "Student not found" in exc.value.detail

    def test_license_not_found_raises_404(self):
        user = _instructor()
        student = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, None]

        with pytest.raises(HTTPException) as exc:
            _run(instructor_update_student_skills(
                request=_req(), student_id=7, license_id=1, db=db, user=user,
                **_VALID_SKILLS,
            ))
        assert exc.value.status_code == 404

    def test_not_lfa_player_raises_400(self):
        user = _instructor()
        student = MagicMock()
        license = MagicMock()
        license.user_id = 7
        license.specialization_type = "INTERNSHIP"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license]

        with pytest.raises(HTTPException) as exc:
            _run(instructor_update_student_skills(
                request=_req(), student_id=7, license_id=1, db=db, user=user,
                **_VALID_SKILLS,
            ))
        assert exc.value.status_code == 400

    def test_invalid_skill_range_returns_error_template(self):
        """Skill value > 100 → return template with error (line 194 True branch)."""
        user = _instructor()
        student = MagicMock()
        student.email = "test@lfa.hu"
        license = MagicMock()
        license.user_id = 7
        license.specialization_type = "LFA_FOOTBALL_PLAYER"
        license.canonical_program_id = "LFA_FOOTBALL_PLAYER"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(instructor_update_student_skills(
                request=_req(), student_id=7, license_id=1, db=db, user=user,
                heading=150.0,  # Invalid: > 100
                shooting=50.0, crossing=50.0, passing=50.0,
                dribbling=50.0, ball_control=50.0,
                instructor_notes="",
            ))

        mock_tmpl.TemplateResponse.assert_called_once()
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert "error" in ctx

    def test_valid_skills_with_notes_commits_and_returns_success(self):
        """Valid skills + notes → commit + AuditService.log + success template."""
        user = _instructor()
        student = MagicMock()
        student.email = "player@lfa.hu"
        license = MagicMock()
        license.user_id = 7
        license.specialization_type = "LFA_FOOTBALL_PLAYER"
        license.canonical_program_id = "LFA_FOOTBALL_PLAYER"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license]

        with patch(f"{_BASE}.templates") as mock_tmpl, \
             patch(f"{_BASE}.AuditService") as mock_audit_svc, \
             patch(f"{_BASE}.AuditAction"):
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(instructor_update_student_skills(
                request=_req(), student_id=7, license_id=1, db=db, user=user,
                **_VALID_SKILLS,
            ))

        db.commit.assert_called()
        db.refresh.assert_called_with(license)
        mock_audit_svc.assert_called_once_with(db)
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx.get("success") is True

    def test_valid_skills_no_notes_skips_instructor_notes_update(self):
        """instructor_notes='' → if instructor_notes.strip(): False → no notes update."""
        user = _instructor()
        student = MagicMock()
        student.email = "player2@lfa.hu"
        license = MagicMock()
        license.user_id = 7
        license.specialization_type = "LFA_FOOTBALL_PLAYER"
        license.canonical_program_id = "LFA_FOOTBALL_PLAYER"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license]

        with patch(f"{_BASE}.templates") as mock_tmpl, \
             patch(f"{_BASE}.AuditService"), \
             patch(f"{_BASE}.AuditAction"):
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(instructor_update_student_skills(
                request=_req(), student_id=7, license_id=1, db=db, user=user,
                **_VALID_SKILLS_NO_NOTES,
            ))

        db.commit.assert_called()
        # instructor_notes should NOT be set on license since notes was empty
        assert not hasattr(license, 'instructor_notes') or \
               license.instructor_notes != ""


import pytest
