"""
Unit tests for app/api/web_routes/admin.py

Covers:
  admin_users_page — 403 guard, success
  admin_semesters_page — success
  admin_coupons_page — empty + with coupon loop (is_valid)
  admin_invitation_codes_page — empty + with code (used_by/created_by lookup)
  admin_analytics_page — 5 count queries
  admin_payments_page — options/joinedload chain
  instructor_enrollments_page — empty semesters
  instructor_edit_student_skills_page — success GET + student not found 404
  instructor_update_student_skills — POST success
  motivation_assessment_page — success GET
  motivation_assessment_submit — POST success

Mock strategy:
  - db = MagicMock(); configure specific return chains per route
  - patch("app.api.web_routes.admin.templates") for TemplateResponse
  - patch("app.api.web_routes.admin.AuditService") for audit log calls
  - asyncio.run(endpoint(...)) calls async functions directly
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app.api.web_routes.admin.users import (
    admin_users_page,
    admin_reset_user_password,
)
from app.api.web_routes.admin.semesters import admin_semesters_page
from app.api.web_routes.admin.coupons import (
    admin_coupons_page,
    admin_invitation_codes_page,
)
from app.api.web_routes.admin.analytics import (
    admin_analytics_page,
    motivation_assessment_page,
    motivation_assessment_submit,
)
from app.api.web_routes.admin.finance import admin_payments_page
from app.api.web_routes.admin.credits import (
    admin_grant_credit,
    admin_deduct_credit,
    admin_grant_license,
    admin_revoke_license,
    admin_renew_license,
)
from app.api.web_routes.admin.bookings import (
    admin_bookings_page,
    admin_booking_confirm,
    admin_booking_cancel,
    admin_booking_attendance,
)
from app.models.booking import BookingStatus
from app.models.attendance import AttendanceStatus
from app.api.web_routes.instructor_dashboard import (
    instructor_enrollments_page,
    instructor_edit_student_skills_page,
    instructor_update_student_skills,
)
from app.models.user import UserRole


_USERS_BASE = "app.api.web_routes.admin.users"
_SEMS_BASE = "app.api.web_routes.admin.semesters"
_COUPONS_BASE = "app.api.web_routes.admin.coupons"
_ANALYTICS_BASE = "app.api.web_routes.admin.analytics"
_FINANCE_BASE = "app.api.web_routes.admin.finance"
_CREDITS_BASE = "app.api.web_routes.admin.credits"
_BOOKINGS_BASE = "app.api.web_routes.admin.bookings"
_INSTRUCTOR_BASE = "app.api.web_routes.instructor_dashboard"


def _admin(uid=1):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.ADMIN
    u.name = "Admin User"
    return u


def _instructor(uid=42):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.INSTRUCTOR
    u.name = "Instructor"
    return u


def _student_user(uid=99):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    u.name = "Student"
    return u


def _req():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────────
# admin_users_page
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminUsersPage:

    def test_not_admin_raises_403(self):
        user = _instructor()
        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_users_page(request=_req(), db=db, user=user))
        assert exc_info.value.status_code == 403

    def test_admin_renders_users_template(self):
        user = _admin()
        db = MagicMock()
        # count() must return int — used in max()/min() pagination math
        db.query.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.count.return_value = 0
        db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        with patch(f"{_USERS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_users_page(request=_req(), db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "admin/users.html"


# ──────────────────────────────────────────────────────────────────────────────
# admin_semesters_page
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminSemestersPage:

    def test_not_admin_raises_403(self):
        user = _instructor()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_semesters_page(request=_req(), db=MagicMock(), user=user))
        assert exc_info.value.status_code == 403

    def test_admin_renders_semesters_template(self):
        user = _admin()
        db = MagicMock()
        db.query.return_value.order_by.return_value.all.return_value = []

        with patch(f"{_SEMS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_semesters_page(request=_req(), db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "admin/semesters.html"


# ──────────────────────────────────────────────────────────────────────────────
# admin_coupons_page
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminCouponsPage:

    def test_not_admin_raises_403(self):
        user = _instructor()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_coupons_page(request=_req(), db=MagicMock(), user=user))
        assert exc_info.value.status_code == 403

    def test_empty_coupons_renders_template(self):
        user = _admin()
        db = MagicMock()
        db.query.return_value.order_by.return_value.all.return_value = []

        with patch(f"{_COUPONS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_coupons_page(request=_req(), db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "admin/coupons.html"

    def test_with_coupon_calls_is_valid(self):
        """Loop over coupons calls coupon.is_valid() for each."""
        user = _admin()
        coupon = MagicMock()
        coupon.is_valid.return_value = True
        db = MagicMock()
        db.query.return_value.order_by.return_value.all.return_value = [coupon]

        with patch(f"{_COUPONS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_coupons_page(request=_req(), db=db, user=user))

        coupon.is_valid.assert_called_once()
        assert coupon.is_currently_valid is True


# ──────────────────────────────────────────────────────────────────────────────
# admin_invitation_codes_page
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminInvitationCodesPage:

    def test_not_admin_raises_403(self):
        user = _instructor()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_invitation_codes_page(request=_req(), db=MagicMock(), user=user))
        assert exc_info.value.status_code == 403

    def test_empty_codes_renders_template(self):
        user = _admin()
        db = MagicMock()
        db.query.return_value.order_by.return_value.all.return_value = []

        with patch(f"{_COUPONS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_invitation_codes_page(request=_req(), db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "admin/invitation_codes.html"

    def test_code_with_used_and_created_by_enriched(self):
        """Code with used_by_user_id + created_by_admin_id → bulk user lookup."""
        user = _admin()
        code = MagicMock()
        code.used_by_user_id = 99
        code.created_by_admin_id = 1
        # Route now does a single bulk query: db.query(User.id, User.name).filter(User.id.in_(...)).all()
        # Returns objects with .id and .name; we return SimpleNamespace-like mocks
        u1 = MagicMock(); u1.id = 99; u1.name = "Used By User"
        u2 = MagicMock(); u2.id = 1;  u2.name = "Admin Creator"
        db = MagicMock()
        # First db.query call: InvitationCode list
        q_codes = MagicMock()
        q_codes.order_by.return_value.all.return_value = [code]
        # Second db.query call: bulk User lookup
        q_users = MagicMock()
        q_users.filter.return_value.all.return_value = [u1, u2]
        db.query.side_effect = [q_codes, q_users]

        with patch(f"{_COUPONS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_invitation_codes_page(request=_req(), db=db, user=user))

        assert code.used_by_name == "Used By User"
        assert code.created_by_name == "Admin Creator"

    def test_code_with_none_user_ids_sets_none_names(self):
        """Both IDs None → else branches: used_by_name=None, created_by_name=None."""
        user = _admin()
        code = MagicMock()
        code.used_by_user_id = None
        code.created_by_admin_id = None
        db = MagicMock()
        db.query.return_value.order_by.return_value.all.return_value = [code]

        with patch(f"{_COUPONS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_invitation_codes_page(request=_req(), db=db, user=user))

        assert code.used_by_name is None
        assert code.created_by_name is None

    def test_code_user_lookup_returns_none(self):
        """used_by_user_id set but DB returns None → ternary False → used_by_name=None."""
        user = _admin()
        code = MagicMock()
        code.used_by_user_id = 99
        code.created_by_admin_id = None  # skip created_by branch
        db = MagicMock()
        db.query.return_value.order_by.return_value.all.return_value = [code]
        db.query.return_value.filter.return_value.first.return_value = None  # user not found

        with patch(f"{_COUPONS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_invitation_codes_page(request=_req(), db=db, user=user))

        assert code.used_by_name is None


# ──────────────────────────────────────────────────────────────────────────────
# admin_analytics_page
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminAnalyticsPage:

    def test_not_admin_raises_403(self):
        user = _instructor()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_analytics_page(request=_req(), db=MagicMock(), user=user))
        assert exc_info.value.status_code == 403

    def test_renders_with_count_stats(self):
        """5 count queries: total_users, students, instructors, sessions, bookings."""
        user = _admin()
        db = MagicMock()
        db.query.return_value.count.return_value = 200        # total_users, total_sessions, total_bookings
        db.query.return_value.filter.return_value.count.return_value = 75  # students, instructors

        with patch(f"{_ANALYTICS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_analytics_page(request=_req(), db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        stats = ctx["stats"]
        assert stats["total_users"] == 200
        assert stats["total_students"] == 75


# ──────────────────────────────────────────────────────────────────────────────
# admin_payments_page
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminPaymentsPage:

    def test_not_admin_raises_403(self):
        user = _instructor()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_payments_page(request=_req(), db=MagicMock(), user=user))
        assert exc_info.value.status_code == 403

    def test_renders_payments_template(self):
        """options/joinedload chain for invoice_requests + newcomer_licenses."""
        user = _admin()
        db = MagicMock()
        # invoice_requests: options().order_by().all()
        db.query.return_value.options.return_value.order_by.return_value.all.return_value = []
        # all_enrollments: plain .all()
        db.query.return_value.all.return_value = []
        # newcomer_licenses: options().filter().order_by().all()
        db.query.return_value.options.return_value.filter.return_value.order_by.return_value.all.return_value = []

        with patch(f"{_FINANCE_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_payments_page(request=_req(), db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "admin/payments.html"

    def test_with_active_enrollments_covers_notin_branch(self):
        """all_enrollments non-empty → enrollment_license_ids truthy → notin_ filter applied."""
        user = _admin()
        enrollment = MagicMock()
        db = MagicMock()
        # invoice_requests: options().order_by().all()
        db.query.return_value.options.return_value.order_by.return_value.all.return_value = []
        # all_enrollments: plain .all()
        db.query.return_value.all.return_value = [enrollment]
        # newcomer_licenses: options().filter().order_by().all()
        db.query.return_value.options.return_value.filter.return_value.order_by.return_value.all.return_value = []

        with patch(f"{_FINANCE_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_payments_page(request=_req(), db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "admin/payments.html"


# ──────────────────────────────────────────────────────────────────────────────
# instructor_enrollments_page
# ──────────────────────────────────────────────────────────────────────────────

class TestInstructorEnrollmentsPage:

    def test_not_instructor_raises_403(self):
        user = _student_user()
        with pytest.raises(HTTPException) as exc_info:
            _run(instructor_enrollments_page(request=_req(), db=MagicMock(), user=user))
        assert exc_info.value.status_code == 403

    def test_renders_with_empty_semesters(self):
        user = _instructor()
        db = MagicMock()
        # instructor_semesters: filter().order_by().all() → []
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        with patch(f"{_INSTRUCTOR_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(instructor_enrollments_page(request=_req(), db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "instructor/enrollments.html"

    def test_with_semesters_queries_enrollments(self):
        """semester_ids non-empty → True branch → options().filter().order_by().all() called."""
        user = _instructor()
        semester = MagicMock()
        db = MagicMock()
        # instructor_semesters: filter().order_by().all() → [semester]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [semester]
        # all_enrollments: options().filter().order_by().all() → []
        db.query.return_value.options.return_value.filter.return_value.order_by.return_value.all.return_value = []

        with patch(f"{_INSTRUCTOR_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(instructor_enrollments_page(request=_req(), db=db, user=user))

        # Confirm the options chain was exercised (semester_ids True branch)
        db.query.return_value.options.return_value.filter.return_value.order_by.return_value.all.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# instructor_edit_student_skills_page (GET)
# ──────────────────────────────────────────────────────────────────────────────

class TestInstructorEditSkillsPage:

    def test_not_instructor_raises_403(self):
        user = _student_user()
        with pytest.raises(HTTPException) as exc_info:
            _run(instructor_edit_student_skills_page(
                request=_req(), student_id=99, license_id=1, db=MagicMock(), user=user
            ))
        assert exc_info.value.status_code == 403

    def test_student_not_found_raises_404(self):
        user = _instructor()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None  # student=None

        with pytest.raises(HTTPException) as exc_info:
            _run(instructor_edit_student_skills_page(
                request=_req(), student_id=99, license_id=1, db=db, user=user
            ))
        assert exc_info.value.status_code == 404
        assert "Student not found" in exc_info.value.detail

    def test_success_renders_skills_template(self):
        user = _instructor()
        student = MagicMock()
        license_obj = MagicMock()
        license_obj.user_id = 99
        license_obj.specialization_type = "LFA_PLAYER_PRE"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license_obj]

        with patch(f"{_INSTRUCTOR_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(instructor_edit_student_skills_page(
                request=_req(), student_id=99, license_id=1, db=db, user=user
            ))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "instructor/student_skills.html"

    def test_license_user_id_mismatch_raises_404(self):
        """License found but user_id doesn't match student_id → 404."""
        user = _instructor()
        student = MagicMock()
        license_obj = MagicMock()
        license_obj.user_id = 99  # Mismatch with student_id=42
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license_obj]

        with pytest.raises(HTTPException) as exc_info:
            _run(instructor_edit_student_skills_page(
                request=_req(), student_id=42, license_id=1, db=db, user=user
            ))
        assert exc_info.value.status_code == 404
        assert "License not found" in exc_info.value.detail

    def test_non_lfa_player_spec_raises_400(self):
        """License found, user_id matches, but specialization not LFA_PLAYER_ → 400."""
        user = _instructor()
        student = MagicMock()
        license_obj = MagicMock()
        license_obj.user_id = 99
        license_obj.specialization_type = "GANCUJU_PLAYER"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license_obj]

        with pytest.raises(HTTPException) as exc_info:
            _run(instructor_edit_student_skills_page(
                request=_req(), student_id=99, license_id=1, db=db, user=user
            ))
        assert exc_info.value.status_code == 400


# ──────────────────────────────────────────────────────────────────────────────
# instructor_update_student_skills (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestInstructorUpdateSkills:

    def test_not_instructor_raises_403(self):
        user = _student_user()
        with pytest.raises(HTTPException) as exc_info:
            _run(instructor_update_student_skills(
                request=_req(), student_id=99, license_id=1, db=MagicMock(), user=user,
                heading=70.0, shooting=70.0, crossing=70.0,
                passing=70.0, dribbling=70.0, ball_control=70.0,
                instructor_notes="",
            ))
        assert exc_info.value.status_code == 403

    def test_student_not_found_raises_404(self):
        user = _instructor()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            _run(instructor_update_student_skills(
                request=_req(), student_id=99, license_id=1, db=db, user=user,
                heading=70.0, shooting=70.0, crossing=70.0,
                passing=70.0, dribbling=70.0, ball_control=70.0,
                instructor_notes="",
            ))
        assert exc_info.value.status_code == 404

    def test_license_mismatch_raises_404(self):
        user = _instructor()
        student = MagicMock()
        license_obj = MagicMock()
        license_obj.user_id = 99  # Mismatch with student_id=42
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license_obj]
        with pytest.raises(HTTPException) as exc_info:
            _run(instructor_update_student_skills(
                request=_req(), student_id=42, license_id=1, db=db, user=user,
                heading=70.0, shooting=70.0, crossing=70.0,
                passing=70.0, dribbling=70.0, ball_control=70.0,
                instructor_notes="",
            ))
        assert exc_info.value.status_code == 404

    def test_non_lfa_player_spec_raises_400(self):
        user = _instructor()
        student = MagicMock()
        license_obj = MagicMock()
        license_obj.user_id = 99
        license_obj.specialization_type = "GANCUJU_PLAYER"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license_obj]
        with pytest.raises(HTTPException) as exc_info:
            _run(instructor_update_student_skills(
                request=_req(), student_id=99, license_id=1, db=db, user=user,
                heading=70.0, shooting=70.0, crossing=70.0,
                passing=70.0, dribbling=70.0, ball_control=70.0,
                instructor_notes="",
            ))
        assert exc_info.value.status_code == 400

    def test_skill_out_of_range_returns_template_with_error(self):
        """Skill value > 100 → TemplateResponse with error (no raise)."""
        user = _instructor()
        student = MagicMock()
        license_obj = MagicMock()
        license_obj.user_id = 99
        license_obj.specialization_type = "LFA_PLAYER_PRE"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license_obj]

        with patch(f"{_INSTRUCTOR_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(instructor_update_student_skills(
                request=_req(), student_id=99, license_id=1, db=db, user=user,
                heading=150.0,  # out of range
                shooting=70.0, crossing=70.0,
                passing=70.0, dribbling=70.0, ball_control=70.0,
                instructor_notes="",
            ))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx.get("error") is not None

    def test_success_updates_and_renders(self):
        user = _instructor()
        student = MagicMock()
        student.email = "student@test.com"
        license_obj = MagicMock()
        license_obj.user_id = 99
        license_obj.specialization_type = "LFA_PLAYER_PRE"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license_obj]

        with patch(f"{_INSTRUCTOR_BASE}.templates") as mock_tmpl, \
             patch(f"{_INSTRUCTOR_BASE}.AuditService") as mock_audit:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            mock_audit.return_value.log.return_value = None
            _run(instructor_update_student_skills(
                request=_req(), student_id=99, license_id=1, db=db, user=user,
                heading=75.0, shooting=80.0, crossing=70.0,
                passing=85.0, dribbling=90.0, ball_control=65.0,
                instructor_notes="Good progress",
            ))

        db.commit.assert_called_once()
        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "instructor/student_skills.html"
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx.get("success") is True


# ──────────────────────────────────────────────────────────────────────────────
# motivation_assessment_page (GET)
# ──────────────────────────────────────────────────────────────────────────────

class TestMotivationAssessmentPage:

    def test_non_admin_raises_403(self):
        user = _student_user()
        with pytest.raises(HTTPException) as exc_info:
            _run(motivation_assessment_page(
                request=_req(), student_id=99, specialization="LFA_PLAYER_PRE",
                db=MagicMock(), user=user,
            ))
        assert exc_info.value.status_code == 403

    def test_renders_assessment_template(self):
        user = _admin()
        student = MagicMock()
        license_obj = MagicMock()
        license_obj.motivation_scores = None  # No existing scores
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license_obj]

        with patch(f"{_ANALYTICS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(motivation_assessment_page(
                request=_req(), student_id=99, specialization="LFA_PLAYER_PRE",
                db=db, user=user,
            ))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "admin/motivation_assessment.html"
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["existing_scores"] is False

    def test_student_not_found_raises_404(self):
        """student=None → 404."""
        user = _admin()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            _run(motivation_assessment_page(
                request=_req(), student_id=99, specialization="LFA_PLAYER_PRE",
                db=db, user=user,
            ))
        assert exc_info.value.status_code == 404

    def test_license_not_found_raises_404(self):
        """Student found but license=None → 404."""
        user = _admin()
        student = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, None]

        with pytest.raises(HTTPException) as exc_info:
            _run(motivation_assessment_page(
                request=_req(), student_id=99, specialization="LFA_PLAYER_PRE",
                db=db, user=user,
            ))
        assert exc_info.value.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# motivation_assessment_submit (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestMotivationAssessmentSubmit:

    def test_non_admin_raises_403(self):
        user = _student_user()
        with pytest.raises(HTTPException) as exc_info:
            _run(motivation_assessment_submit(
                request=_req(), student_id=99, specialization="LFA_PLAYER_PRE",
                goal_clarity=3, commitment_level=3, engagement=3,
                progress_mindset=3, initiative=3, notes="",
                db=MagicMock(), user=user,
            ))
        assert exc_info.value.status_code == 403

    def test_submit_valid_scores_redirects(self):
        user = _admin()
        license_obj = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = license_obj

        result = _run(motivation_assessment_submit(
            request=_req(), student_id=99, specialization="LFA_PLAYER_PRE",
            goal_clarity=4, commitment_level=4, engagement=3,
            progress_mindset=5, initiative=4, notes="Good",
            db=db, user=user,
        ))

        assert isinstance(result, RedirectResponse)
        assert "/admin/users" in result.headers["location"]
        db.commit.assert_called_once()
        assert license_obj.motivation_scores is not None

    def test_invalid_score_raises_400(self):
        """Score < 1 → HTTPException 400 before any DB query."""
        user = _admin()
        db = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            _run(motivation_assessment_submit(
                request=_req(), student_id=99, specialization="LFA_PLAYER_PRE",
                goal_clarity=0,  # invalid: < 1
                commitment_level=3, engagement=3,
                progress_mindset=3, initiative=3, notes="",
                db=db, user=user,
            ))
        assert exc_info.value.status_code == 400

    def test_empty_instructor_notes_skips_note_update(self):
        """instructor_notes='' → if instructor_notes.strip(): False branch (line 575->578)."""
        user = _instructor()
        student = MagicMock()
        student.email = "student@test.com"
        license_obj = MagicMock()
        license_obj.user_id = 99
        license_obj.specialization_type = "LFA_PLAYER_PRE"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [student, license_obj]

        with patch(f"{_INSTRUCTOR_BASE}.templates") as mock_tmpl, \
             patch(f"{_INSTRUCTOR_BASE}.AuditService") as mock_audit:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            mock_audit.return_value.log.return_value = None
            _run(instructor_update_student_skills(
                request=_req(), student_id=99, license_id=1, db=db, user=user,
                heading=75.0, shooting=80.0, crossing=70.0,
                passing=85.0, dribbling=90.0, ball_control=65.0,
                instructor_notes="",  # Empty → False branch
            ))

        db.commit.assert_called_once()
        # license.instructor_notes should NOT have been set (empty notes skipped)

    def test_submit_license_not_found_raises_404(self):
        """All scores valid, but license not found → 404."""
        user = _admin()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            _run(motivation_assessment_submit(
                request=_req(), student_id=99, specialization="LFA_PLAYER_PRE",
                goal_clarity=3, commitment_level=3, engagement=3,
                progress_mindset=3, initiative=3, notes="",
                db=db, user=user,
            ))
        assert exc_info.value.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# admin_reset_user_password (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminPasswordReset:

    def test_non_admin_raises_403(self):
        user = _student_user()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_reset_user_password(
                user_id=1, request=_req(),
                new_password="validpass123", db=MagicMock(), user=user,
            ))
        assert exc_info.value.status_code == 403

    def test_password_too_short_raises_400(self):
        user = _admin()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_reset_user_password(
                user_id=1, request=_req(),
                new_password="short", db=MagicMock(), user=user,
            ))
        assert exc_info.value.status_code == 400

    def test_user_not_found_raises_404(self):
        user = _admin()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_reset_user_password(
                user_id=999, request=_req(),
                new_password="validpass123", db=db, user=user,
            ))
        assert exc_info.value.status_code == 404

    def test_success_redirects(self):
        user = _admin()
        target = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = target

        with patch(f"{_USERS_BASE}.get_password_hash", return_value="hashed_pw"):
            result = _run(admin_reset_user_password(
                user_id=5, request=_req(),
                new_password="newSecure99", db=db, user=user,
            ))

        assert isinstance(result, RedirectResponse)
        assert "/admin/users/5/edit" in result.headers["location"]
        assert target.password_hash == "hashed_pw"
        db.commit.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# admin_grant_credit (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminCreditGrant:

    def test_non_admin_raises_403(self):
        user = _student_user()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_grant_credit(
                user_id=1, request=_req(),
                amount=100, reason="test", db=MagicMock(), user=user,
            ))
        assert exc_info.value.status_code == 403

    def test_negative_amount_raises_400(self):
        user = _admin()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_grant_credit(
                user_id=1, request=_req(),
                amount=-1, reason="bad", db=MagicMock(), user=user,
            ))
        assert exc_info.value.status_code == 400

    def test_over_limit_raises_400(self):
        user = _admin()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_grant_credit(
                user_id=1, request=_req(),
                amount=50001, reason="too much", db=MagicMock(), user=user,
            ))
        assert exc_info.value.status_code == 400

    def test_user_not_found_raises_404(self):
        user = _admin()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_grant_credit(
                user_id=999, request=_req(),
                amount=100, reason="test", db=db, user=user,
            ))
        assert exc_info.value.status_code == 404

    def test_success_creates_transaction_and_redirects(self):
        user = _admin()
        target = MagicMock()
        target.id = 5
        target.credit_balance = 1000
        target.credit_purchased = 1000
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = target

        result = _run(admin_grant_credit(
            user_id=5, request=_req(),
            amount=300, reason="Competition reward", db=db, user=user,
        ))

        assert isinstance(result, RedirectResponse)
        assert "/admin/users/5/edit" in result.headers["location"]
        assert target.credit_balance == 1300
        db.add.assert_called_once()
        db.commit.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# admin_deduct_credit (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminCreditDeduct:

    def test_amount_exceeds_balance_raises_400(self):
        user = _admin()
        target = MagicMock()
        target.credit_balance = 100
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = target

        with pytest.raises(HTTPException) as exc_info:
            _run(admin_deduct_credit(
                user_id=5, request=_req(),
                amount=500, reason="too much", db=db, user=user,
            ))
        assert exc_info.value.status_code == 400

    def test_success_decreases_balance_and_redirects(self):
        user = _admin()
        target = MagicMock()
        target.id = 5
        target.credit_balance = 500
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = target

        result = _run(admin_deduct_credit(
            user_id=5, request=_req(),
            amount=200, reason="Correction", db=db, user=user,
        ))

        assert isinstance(result, RedirectResponse)
        assert target.credit_balance == 300
        db.add.assert_called_once()
        db.commit.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# admin_grant_license (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminLicenseGrant:

    def test_invalid_specialization_redirects_with_error(self):
        """Invalid spec → redirect with ?error=invalid_spec (not JSON 400)."""
        user = _admin()
        target = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = target

        result = _run(admin_grant_license(
            user_id=5, request=_req(),
            specialization_type="INVALID_SPEC", reason="test", db=db, user=user,
        ))
        assert isinstance(result, RedirectResponse)
        loc = result.headers["location"]
        assert "error=invalid_spec" in loc
        assert "/admin/users/5/edit" in loc

    def test_duplicate_active_license_redirects_with_error(self):
        """Duplicate active license → redirect with ?error=duplicate_license (not JSON 400)."""
        user = _admin()
        target = MagicMock()
        target.id = 5
        existing_license = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [target, existing_license]

        result = _run(admin_grant_license(
            user_id=5, request=_req(),
            specialization_type="LFA_FOOTBALL_PLAYER", reason="dup", db=db, user=user,
        ))
        assert isinstance(result, RedirectResponse)
        loc = result.headers["location"]
        assert "error=duplicate_license" in loc
        assert "LFA_FOOTBALL_PLAYER" in loc
        assert "/admin/users/5/edit" in loc

    def test_success_redirects(self):
        user = _admin()
        target = MagicMock()
        target.id = 5
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [target, None]  # target found, no existing license

        result = _run(admin_grant_license(
            user_id=5, request=_req(),
            specialization_type="LFA_FOOTBALL_PLAYER", reason="Manual",
            expires_at="",  # blank = perpetual; must pass explicitly (Form default not evaluated in direct calls)
            db=db, user=user,
        ))

        assert isinstance(result, RedirectResponse)
        assert "/admin/users/5/edit" in result.headers["location"]
        assert db.add.call_count == 2  # UserLicense + LicenseProgression
        db.commit.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# admin_revoke_license (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminLicenseRevoke:

    def test_license_not_found_raises_404(self):
        user = _admin()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            _run(admin_revoke_license(
                user_id=5, license_id=99, request=_req(),
                reason="test", db=db, user=user,
            ))
        assert exc_info.value.status_code == 404

    def test_already_revoked_license_redirects_with_error(self):
        """Re-revoking an already-revoked license → redirect with ?error=already_revoked (no-op)."""
        user = _admin()
        license_obj = MagicMock()
        license_obj.id = 10
        license_obj.is_active = False  # already revoked
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = license_obj

        result = _run(admin_revoke_license(
            user_id=5, license_id=10, request=_req(),
            reason="duplicate revoke", db=db, user=user,
        ))
        assert isinstance(result, RedirectResponse)
        loc = result.headers["location"]
        assert "error=already_revoked" in loc
        assert "/admin/users/5/edit" in loc
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_success_deactivates_and_redirects(self):
        user = _admin()
        license_obj = MagicMock()
        license_obj.id = 10
        license_obj.is_active = True
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = license_obj

        result = _run(admin_revoke_license(
            user_id=5, license_id=10, request=_req(),
            reason="Policy violation", db=db, user=user,
        ))

        assert isinstance(result, RedirectResponse)
        assert license_obj.is_active is False
        db.add.assert_called_once()
        db.commit.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# admin_renew_license (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminLicenseRenew:
    """Unit tests for POST /admin/users/{id}/renew-license/{license_id}."""

    def test_non_admin_raises_403(self):
        user = _student_user()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_renew_license(
                user_id=5, license_id=10, request=_req(),
                new_expires_at="2027-12-31", reason="test", db=MagicMock(), user=user,
            ))
        assert exc_info.value.status_code == 403

    def test_license_not_found_raises_404(self):
        user = _admin()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_renew_license(
                user_id=5, license_id=99, request=_req(),
                new_expires_at="2027-12-31", reason="test", db=db, user=user,
            ))
        assert exc_info.value.status_code == 404

    def test_revoked_license_redirects_with_error(self):
        """Renewing a revoked license → redirect with error=cannot_renew_revoked."""
        user = _admin()
        license_obj = MagicMock()
        license_obj.id = 10
        license_obj.is_active = False
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = license_obj

        result = _run(admin_renew_license(
            user_id=5, license_id=10, request=_req(),
            new_expires_at="2027-12-31", reason="test", db=db, user=user,
        ))
        assert isinstance(result, RedirectResponse)
        assert "error=cannot_renew_revoked" in result.headers["location"]
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_past_date_raises_400(self):
        user = _admin()
        license_obj = MagicMock()
        license_obj.is_active = True
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = license_obj

        with pytest.raises(HTTPException) as exc_info:
            _run(admin_renew_license(
                user_id=5, license_id=10, request=_req(),
                new_expires_at="2000-01-01", reason="test", db=db, user=user,
            ))
        assert exc_info.value.status_code == 400

    def test_invalid_date_format_raises_400(self):
        user = _admin()
        license_obj = MagicMock()
        license_obj.is_active = True
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = license_obj

        with pytest.raises(HTTPException) as exc_info:
            _run(admin_renew_license(
                user_id=5, license_id=10, request=_req(),
                new_expires_at="not-a-date", reason="test", db=db, user=user,
            ))
        assert exc_info.value.status_code == 400

    def test_success_updates_expiry_and_creates_renewed_progression(self):
        user = _admin()
        license_obj = MagicMock()
        license_obj.id = 10
        license_obj.is_active = True
        license_obj.current_level = 2
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = license_obj

        result = _run(admin_renew_license(
            user_id=5, license_id=10, request=_req(),
            new_expires_at="2027-06-30", reason="Annual renewal",
            db=db, user=user,
        ))

        assert isinstance(result, RedirectResponse)
        assert "#licenses" in result.headers["location"]
        assert license_obj.expires_at is not None
        assert license_obj.last_renewed_at is not None
        db.add.assert_called_once()
        db.commit.assert_called_once()
        added_prog = db.add.call_args[0][0]
        assert added_prog.requirements_met == "RENEWED"
        assert added_prog.advancement_reason == "Annual renewal"


# ──────────────────────────────────────────────────────────────────────────────
# admin_bookings_page (GET)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminBookingsPage:

    def test_not_admin_raises_403(self):
        user = _instructor()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_bookings_page(request=_req(), db=MagicMock(), user=user))
        assert exc_info.value.status_code == 403

    def test_renders_bookings_template(self):
        user = _admin()
        db = MagicMock()
        # Main paginated query: db.query(Booking).options(...).count/order_by/.../all
        db.query.return_value.options.return_value.count.return_value = 0
        db.query.return_value.options.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        # Stats: db.query(sqlfunc.count(...)).filter(...).scalar()
        db.query.return_value.filter.return_value.scalar.return_value = 0
        # Sessions dropdown: db.query(SessionModel).join(...).distinct().order_by(...).limit(100).all()
        db.query.return_value.join.return_value.distinct.return_value.order_by.return_value.limit.return_value.all.return_value = []

        with patch(f"{_BOOKINGS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(admin_bookings_page(request=_req(), db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "admin/bookings.html"

    def test_invalid_status_filter_silently_ignored(self):
        """Invalid status_filter raises ValueError in BookingStatus() — caught, no filter applied."""
        user = _admin()
        db = MagicMock()
        db.query.return_value.options.return_value.count.return_value = 0
        db.query.return_value.options.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        db.query.return_value.filter.return_value.scalar.return_value = 0
        db.query.return_value.join.return_value.distinct.return_value.order_by.return_value.limit.return_value.all.return_value = []

        with patch(f"{_BOOKINGS_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            # should not raise
            _run(admin_bookings_page(
                request=_req(), status_filter="TOTALLY_INVALID", db=db, user=user
            ))

        assert mock_tmpl.TemplateResponse.called


# ──────────────────────────────────────────────────────────────────────────────
# admin_booking_confirm (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminBookingConfirm:

    def test_not_admin_raises_403(self):
        user = _instructor()
        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_booking_confirm(booking_id=1, request=_req(), db=db, user=user))
        assert exc_info.value.status_code == 403

    def test_booking_not_found_raises_404(self):
        user = _admin()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_booking_confirm(booking_id=1, request=_req(), db=db, user=user))
        assert exc_info.value.status_code == 404

    def test_already_confirmed_raises_400(self):
        user = _admin()
        booking_mock = MagicMock()
        booking_mock.status = BookingStatus.CONFIRMED
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = booking_mock
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_booking_confirm(booking_id=1, request=_req(), db=db, user=user))
        assert exc_info.value.status_code == 400

    def test_capacity_exceeded_raises_409(self):
        """Session at capacity: confirmed_count >= capacity → 409."""
        user = _admin()
        booking_mock = MagicMock()
        booking_mock.status = BookingStatus.PENDING
        booking_mock.session_id = 42
        session_mock = MagicMock()
        session_mock.capacity = 5
        db = MagicMock()
        # First .first() → booking, second .first() → session
        db.query.return_value.filter.return_value.first.side_effect = [booking_mock, session_mock]
        # confirmed_count == capacity
        db.query.return_value.filter.return_value.scalar.return_value = 5
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_booking_confirm(booking_id=1, request=_req(), db=db, user=user))
        assert exc_info.value.status_code == 409

    def test_success_sets_status_confirmed(self):
        """Happy path: session has no capacity limit → booking confirmed."""
        user = _admin()
        booking_mock = MagicMock()
        booking_mock.status = BookingStatus.PENDING
        booking_mock.session_id = 42
        session_mock = MagicMock()
        session_mock.capacity = None  # no capacity limit
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [booking_mock, session_mock]

        result = _run(admin_booking_confirm(booking_id=1, request=_req(), db=db, user=user))

        assert booking_mock.status == BookingStatus.CONFIRMED
        db.commit.assert_called_once()
        assert result.status_code == 200


# ──────────────────────────────────────────────────────────────────────────────
# admin_booking_cancel (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminBookingCancel:

    def test_not_admin_raises_403(self):
        user = _instructor()
        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_booking_cancel(
                booking_id=1, request=_req(), reason="x", db=db, user=user
            ))
        assert exc_info.value.status_code == 403

    def test_booking_not_found_raises_404(self):
        user = _admin()
        db = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_booking_cancel(
                booking_id=1, request=_req(), reason="x", db=db, user=user
            ))
        assert exc_info.value.status_code == 404

    def test_already_cancelled_raises_400(self):
        user = _admin()
        booking_mock = MagicMock()
        booking_mock.status = BookingStatus.CANCELLED
        db = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = booking_mock
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_booking_cancel(
                booking_id=1, request=_req(), reason="x", db=db, user=user
            ))
        assert exc_info.value.status_code == 400

    def test_success_sets_cancelled_fields(self):
        """Happy path: sets CANCELLED status, notes=reason, cancelled_at."""
        user = _admin()
        booking_mock = MagicMock()
        booking_mock.status = BookingStatus.CONFIRMED
        db = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = booking_mock

        result = _run(admin_booking_cancel(
            booking_id=1, request=_req(), reason="Admin test cancel", db=db, user=user
        ))

        assert booking_mock.status == BookingStatus.CANCELLED
        assert booking_mock.notes == "Admin test cancel"
        assert booking_mock.cancelled_at is not None
        db.commit.assert_called_once()
        assert result.status_code == 200


# ──────────────────────────────────────────────────────────────────────────────
# admin_booking_attendance (POST)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminBookingAttendance:

    def test_not_admin_raises_403(self):
        user = _instructor()
        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_booking_attendance(
                booking_id=1, request=_req(),
                attendance_status="present", notes="", db=db, user=user,
            ))
        assert exc_info.value.status_code == 403

    def test_invalid_attendance_status_raises_400(self):
        """Status not in AttendanceStatus enum values → 400."""
        user = _admin()
        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_booking_attendance(
                booking_id=1, request=_req(),
                attendance_status="flying_saucer", notes="", db=db, user=user,
            ))
        assert exc_info.value.status_code == 400

    def test_booking_not_found_raises_404(self):
        user = _admin()
        db = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            _run(admin_booking_attendance(
                booking_id=1, request=_req(),
                attendance_status="present", notes="", db=db, user=user,
            ))
        assert exc_info.value.status_code == 404

    def test_creates_new_attendance_when_none_exists(self):
        """booking.attendance is None → Attendance() created and db.add() called."""
        user = _admin()
        booking_mock = MagicMock()
        booking_mock.attendance = None  # no existing attendance
        booking_mock.user_id = 10
        booking_mock.session_id = 20
        booking_mock.id = 30
        db = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = booking_mock

        with patch(f"{_BOOKINGS_BASE}.Attendance") as mock_att_cls:
            result = _run(admin_booking_attendance(
                booking_id=30, request=_req(),
                attendance_status="present", notes="test note", db=db, user=user,
            ))

        mock_att_cls.assert_called_once()
        db.add.assert_called_once()
        db.commit.assert_called_once()
        booking_mock.update_attendance_status.assert_called_once()
        assert result.status_code == 200

    def test_updates_existing_attendance(self):
        """booking.attendance exists → status/marked_by updated, no db.add()."""
        user = _admin()
        existing_att = MagicMock()
        booking_mock = MagicMock()
        booking_mock.attendance = existing_att  # truthy — existing record
        db = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = booking_mock

        result = _run(admin_booking_attendance(
            booking_id=1, request=_req(),
            attendance_status="absent", notes="", db=db, user=user,
        ))

        assert existing_att.status == AttendanceStatus.absent
        assert existing_att.marked_by == user.id
        db.add.assert_not_called()  # update path — no new row
        db.commit.assert_called_once()
        booking_mock.update_attendance_status.assert_called_once()
        assert result.status_code == 200
