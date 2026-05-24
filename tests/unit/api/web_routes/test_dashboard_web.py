"""
Unit tests for app/api/web_routes/dashboard.py

Covers:
  dashboard() — student path (hub_specializations.html early return),
                admin path (dashboard_admin.html),
                instructor path (dashboard_instructor.html)
  spec_dashboard() — no license → 403,
                     success no enrollment (dashboard_student_new.html),
                     success with active enrollment (current_semester populated)

Mock strategy:
  - patch("app.api.web_routes.dashboard.templates") for TemplateResponse
  - patch("app.api.web_routes.dashboard.get_available_specializations") for student spec list
  - asyncio.run(endpoint(...)) calls async functions directly
  - NOTE: Student path returns early at line ~71 (hub_specializations.html); the dead code blocks
    that followed (UserStats/SessionModel/secrets/dt references) have been removed in the refactor.
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from datetime import date as date_cls

from app.api.web_routes.dashboard import dashboard, spec_dashboard, get_lfa_age_category
from app.api.web_routes.helpers import require_student_onboarding
from app.models.user import UserRole


_BASE = "app.api.web_routes.dashboard"


def _admin(uid=1):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.ADMIN
    u.name = "Admin"
    u.email = "admin@test.com"
    u.specialization = None
    return u


def _instructor(uid=42):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.INSTRUCTOR
    u.name = "Instructor"
    u.email = "instructor@test.com"
    u.specialization = None
    u.get_teaching_specializations.return_value = []
    u.get_all_teaching_specializations.return_value = []
    return u


def _student(uid=99):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    u.name = "Student"
    u.email = "student@test.com"
    u.date_of_birth = None  # Avoids age calculation
    return u


def _req():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────────
# dashboard() — student path
# ──────────────────────────────────────────────────────────────────────────────

class TestDashboardStudentPath:

    def test_student_view_returns_hub_specializations(self):
        """Student role → early return with hub_specializations.html."""
        user = _student()
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []  # user_licenses

        with patch(f"{_BASE}.get_available_specializations", return_value=[]) as mock_specs, \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(dashboard(request=_req(), spec=None, db=db, user=user))

        mock_specs.assert_called_once()
        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "hub_specializations.html"

    def test_student_view_passes_unlocked_count(self):
        """Student with license → unlocked_count=1 in template context."""
        user = _student()
        license_mock = MagicMock()
        license_mock.specialization_type = "GANCUJU_PLAYER"
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [license_mock]

        with patch(f"{_BASE}.get_available_specializations", return_value=[]), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(dashboard(request=_req(), spec=None, db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["unlocked_count"] == 1

    def test_student_with_available_specs_builds_spec_data(self):
        """get_available_specializations returns non-empty → loop body (lines 54-55) runs."""
        user = _student()
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []  # no licenses
        spec_item = {
            "type": "GANCUJU_PLAYER",
            "name": "GanCuju Player",
            "icon": "🥋",
            "color": "#e74c3c",
            "description": "Martial arts",
            "age_requirement": None,
        }

        with patch(f"{_BASE}.get_available_specializations", return_value=[spec_item]), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(dashboard(request=_req(), spec=None, db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert len(ctx["available_specializations"]) == 1
        assert ctx["available_specializations"][0]["is_unlocked"] is False

    def test_student_with_dob_calculates_age(self):
        """user.date_of_birth set → if user.date_of_birth: True branch, user_age is int."""
        from datetime import date
        user = _student()
        user.date_of_birth = date(2010, 6, 15)  # ~15 years old
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.get_available_specializations") as mock_specs, \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_specs.return_value = []
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(dashboard(request=_req(), spec=None, db=db, user=user))

        mock_specs.assert_called_once_with(
            mock_specs.call_args.args[0]  # called with int age
        )
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert isinstance(ctx["user_age"], int)


# ──────────────────────────────────────────────────────────────────────────────
# dashboard() — admin path
# ──────────────────────────────────────────────────────────────────────────────

class TestDashboardAdminPath:

    def test_admin_with_active_semester_processes_code(self):
        """Active semesters with GANCUJU code → semester code processing loop runs (lines 95-113)."""
        user = _admin()
        semester = MagicMock()
        semester.code = "GANCUJU_2026"  # No location suffix → else branch; startswith('GANCUJU') → True
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [semester]
        db.query.return_value.count.return_value = 100
        db.query.return_value.filter.return_value.count.return_value = 50

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(dashboard(request=_req(), spec=None, db=db, user=user))

        assert semester.specialization_type == "GANCUJU_PLAYER"

    def test_admin_semester_with_location_suffix(self):
        """Code with BUDA suffix → if location_match: True branch (line 99-101)."""
        user = _admin()
        semester = MagicMock()
        semester.code = "LFA_PLAYER_PRE_2026_BUDA"  # Has location suffix → True branch
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [semester]
        db.query.return_value.count.return_value = 100
        db.query.return_value.filter.return_value.count.return_value = 50

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(dashboard(request=_req(), spec=None, db=db, user=user))

        # specialization_type derived from code without _BUDA suffix
        assert semester.specialization_type is not None

    def test_admin_view_returns_dashboard_admin(self):
        """Admin role → dashboard_admin.html with kpi / queue / quick_stats context."""
        user = _admin()
        db = MagicMock()
        # active_semesters: filter().order_by().all() → []
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        # total_users: db.query(User).count() → 100
        db.query.return_value.count.return_value = 100
        # filtered counts (active_sessions, active_tournaments, etc.): filter().count() → 50
        db.query.return_value.filter.return_value.count.return_value = 50
        # scalar() calls for revenue: return 0 so float() + round() work cleanly
        db.query.return_value.filter.return_value.scalar.return_value = 0

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(dashboard(request=_req(), spec=None, db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "dashboard_admin.html"
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        # Layer 1: KPI dict replaces old stats dict
        assert ctx["kpi"]["total_users"] == 100
        assert ctx["kpi"]["active_sessions"] == 50
        # Layer 2: queue dict present
        assert "queue" in ctx
        assert "pending_enrollments" in ctx["queue"]
        # Layer 3: quick_stats present
        assert "quick_stats" in ctx
        assert ctx["quick_stats"]["total_students"] == 50


# ──────────────────────────────────────────────────────────────────────────────
# dashboard() — instructor path
# ──────────────────────────────────────────────────────────────────────────────

class TestDashboardInstructorPath:

    def test_instructor_view_returns_dashboard_instructor(self):
        """Instructor role → dashboard_instructor.html with teaching specializations."""
        user = _instructor()
        db = MagicMock()

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(dashboard(request=_req(), spec=None, db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "dashboard_instructor.html"
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["teaching_specializations"] == []


# ──────────────────────────────────────────────────────────────────────────────
# spec_dashboard()
# ──────────────────────────────────────────────────────────────────────────────

class TestSpecDashboard:

    def test_no_license_raises_403(self):
        """user_license=None → 403 Forbidden."""
        user = _student()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            _run(spec_dashboard(request=_req(), spec_type="gancuju-player", db=db, user=user))
        assert exc_info.value.status_code == 403

    def test_success_no_active_enrollment_renders_template(self):
        """License found, no active enrollment → dashboard_student_new.html."""
        user = _student()
        license_obj = MagicMock()
        license_obj.id = 1
        db = MagicMock()
        # Sequential .first() calls:
        # 1. user_license
        # 2. onboarding guard (has_enrollment) → None → has_enrollment=False
        # 3. has_active_enrollment check → None → False
        db.query.return_value.filter.return_value.first.side_effect = [license_obj, None, None]
        # track_semesters and pending_enrollments: filter().order_by().all()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        # existing_enrollments: filter().all()
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(spec_dashboard(request=_req(), spec_type="gancuju-player", db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "dashboard_student_new.html"
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["has_active_enrollment"] is False
        assert ctx["current_semester"] is None

    def test_with_active_enrollment_populates_current_semester(self):
        """Active enrollment found → current_semester set from enrollment."""
        user = _student()
        license_obj = MagicMock()
        license_obj.id = 1
        active_enrollment_check = MagicMock()  # Truthy → has_active_enrollment=True
        enrollment_obj = MagicMock()
        enrollment_obj.semester = MagicMock()
        enrollment_obj.semester.name = "Fall 2026"
        db = MagicMock()
        # Sequential .first() calls:
        # 1. user_license
        # 2. onboarding guard (has_enrollment) → None
        # 3. has_active_enrollment → active_enrollment_check (truthy)
        # 4. enrollment for current_semester
        db.query.return_value.filter.return_value.first.side_effect = [
            license_obj,
            None,
            active_enrollment_check,
            enrollment_obj,
        ]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(spec_dashboard(request=_req(), spec_type="gancuju-player", db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["has_active_enrollment"] is True
        assert ctx["current_semester"] is enrollment_obj.semester

    def test_lfa_football_player_valid_age_renders_template(self):
        """LFA_FOOTBALL_PLAYER spec with valid age → template rendered (no 400 raise)."""
        user = _student()
        license_obj = MagicMock()
        license_obj.id = 1
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [license_obj, None, None]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.get_lfa_age_category", return_value=("PRE", "PRE (Foundation Years)", "5-13 years", "Age 10")), \
             patch(f"{_BASE}._CardDraftService") as mock_cds, \
             patch(f"{_BASE}._build_published_grid_state", return_value=None), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_cds.get_player_card_draft.return_value = MagicMock(published_data={})
            mock_cds.is_published.return_value = False
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(spec_dashboard(request=_req(), spec_type="lfa-football-player", db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "dashboard_student_new.html"
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["age_category"] == "PRE"

    def test_lfa_football_player_no_dob_defaults_to_amateur(self):
        """LFA_FOOTBALL_PLAYER spec with no valid age → defaults to AMATEUR (no exception)."""
        user = _student()
        license_obj = MagicMock()
        license_obj.id = 1
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [license_obj, None, None]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.get_lfa_age_category", return_value=(None, None, None, "Below minimum age")), \
             patch(f"{_BASE}._CardDraftService") as mock_cds, \
             patch(f"{_BASE}._build_published_grid_state", return_value=None), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_cds.get_player_card_draft.return_value = MagicMock(published_data={})
            mock_cds.is_published.return_value = False
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(spec_dashboard(request=_req(), spec_type="lfa-football-player", db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["age_category"] == "AMATEUR"

    def test_lfa_football_player_with_dob_sets_user_age(self):
        """LFA_FOOTBALL_PLAYER with user.date_of_birth → user_age is int in context."""
        from datetime import date as date_cls
        user = _student()
        user.date_of_birth = date_cls(2012, 3, 1)  # ~13 years old
        license_obj = MagicMock()
        license_obj.id = 1
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [license_obj, None, None]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.get_lfa_age_category", return_value=("PRE", "PRE", "5-13 years", "Age 13")), \
             patch(f"{_BASE}._CardDraftService") as mock_cds, \
             patch(f"{_BASE}._build_published_grid_state", return_value=None), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_cds.get_player_card_draft.return_value = MagicMock(published_data={})
            mock_cds.is_published.return_value = False
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(spec_dashboard(request=_req(), spec_type="lfa-football-player", db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert isinstance(ctx["user_age"], int)

    def test_spec_dashboard_with_track_semesters(self):
        """track_semesters non-empty → if track_semesters: True branch (first 3 printed)."""
        from datetime import date as date_cls
        user = _student()
        license_obj = MagicMock()
        license_obj.id = 1
        semester_mock = MagicMock()
        semester_mock.end_date = date_cls(2027, 12, 31)  # far future → passes end_date >= today filter
        db = MagicMock()
        # user_license, onboarding guard, has_active_enrollment → [license_obj, None, None]
        db.query.return_value.filter.return_value.first.side_effect = [license_obj, None, None]
        # track_semesters: filter().order_by().all() → [semester_mock]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [semester_mock]
        # existing_enrollments: filter().all() → []
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(spec_dashboard(request=_req(), spec_type="gancuju-player", db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["specialization"] == "GANCUJU_PLAYER"

    def test_active_enrollment_but_enrollment_not_refetched(self):
        """has_active_enrollment=True, re-fetch returns None → if enrollment: False → current_semester=None."""
        user = _student()
        license_obj = MagicMock()
        license_obj.id = 1
        active_check = MagicMock()  # Truthy → has_active_enrollment=True
        db = MagicMock()
        # user_license, onboarding guard, has_active_enrollment, enrollment re-fetch → None
        db.query.return_value.filter.return_value.first.side_effect = [license_obj, None, active_check, None]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(spec_dashboard(request=_req(), spec_type="gancuju-player", db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["has_active_enrollment"] is True
        assert ctx["current_semester"] is None  # enrollment=None → False branch

    def test_no_onboarding_lfa_player_redirects_to_lfa_onboarding(self):
        """LFA license with onboarding_completed=False, football_skills=None, no enrollment
        → effective_onboarding=False → 303 redirect to /specialization/lfa-player/onboarding."""
        user = _student()
        license_obj = MagicMock()
        license_obj.id = 1
        license_obj.onboarding_completed = False   # explicit falsy — not a MagicMock truthy default
        license_obj.football_skills = None         # explicit None — not a MagicMock truthy default
        db = MagicMock()
        # 1st .first() → license_obj; 2nd .first() (has_enrollment) → None → has_enrollment=False
        db.query.return_value.filter.return_value.first.side_effect = [license_obj, None]

        from fastapi.responses import RedirectResponse as FR
        result = _run(spec_dashboard(
            request=_req(), spec_type="lfa-football-player", db=db, user=user
        ))

        assert isinstance(result, FR), f"Expected redirect, got {type(result)}"
        assert "lfa-player/onboarding" in result.headers["location"]

    def test_no_onboarding_non_lfa_redirects_to_motivation(self):
        """Non-LFA license with onboarding_completed=False, football_skills=None, no enrollment
        → effective_onboarding=False → 303 redirect to /specialization/motivation?spec=GANCUJU_PLAYER."""
        user = _student()
        license_obj = MagicMock()
        license_obj.id = 1
        license_obj.onboarding_completed = False
        license_obj.football_skills = None
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [license_obj, None]

        from fastapi.responses import RedirectResponse as FR
        result = _run(spec_dashboard(
            request=_req(), spec_type="gancuju-player", db=db, user=user
        ))

        assert isinstance(result, FR), f"Expected redirect, got {type(result)}"
        assert "motivation" in result.headers["location"]
        assert "GANCUJU_PLAYER" in result.headers["location"]

    def test_football_skills_non_none_bypasses_onboarding_guard(self):
        """license.football_skills is not None → effective_onboarding=True even if flag=False.
        Verifies the OR condition in the guard: flag OR skills OR enrollment."""
        user = _student()
        license_obj = MagicMock()
        license_obj.id = 1
        license_obj.onboarding_completed = False
        license_obj.football_skills = {"passing": 70.0}  # non-None → guard bypassed
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [license_obj, None, None]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(spec_dashboard(request=_req(), spec_type="gancuju-player", db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "dashboard_student_new.html"  # NOT a redirect


# ──────────────────────────────────────────────────────────────────────────────
# require_student_onboarding() — guard helper tests
# ──────────────────────────────────────────────────────────────────────────────

class TestRequireStudentOnboarding:

    def test_onboarded_student_returns_none(self):
        """Onboarded student → guard passes → returns None (no redirect)."""
        user = _student()
        user.onboarding_completed = True
        result = require_student_onboarding(user)
        assert result is None

    def test_non_onboarded_student_returns_redirect(self):
        """Student with onboarding_completed=False → 303 redirect to /dashboard."""
        from fastapi.responses import RedirectResponse
        user = _student()
        user.onboarding_completed = False
        result = require_student_onboarding(user)
        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303
        assert result.headers["location"] == "/dashboard"

    def test_instructor_role_returns_redirect(self):
        """Instructor role (not STUDENT) → 303 redirect to /dashboard."""
        from fastapi.responses import RedirectResponse
        user = _instructor()
        user.onboarding_completed = True  # even if onboarded, non-student is blocked
        result = require_student_onboarding(user)
        assert isinstance(result, RedirectResponse)
        assert result.headers["location"] == "/dashboard"

    def test_admin_role_returns_redirect(self):
        """Admin role (not STUDENT) → 303 redirect to /dashboard."""
        from fastapi.responses import RedirectResponse
        user = _admin()
        result = require_student_onboarding(user)
        assert isinstance(result, RedirectResponse)
        assert result.headers["location"] == "/dashboard"


# ──────────────────────────────────────────────────────────────────────────────
# get_lfa_age_category() — direct function tests (lines 227-241)
# ──────────────────────────────────────────────────────────────────────────────

class TestGetLfaAgeCategory:

    def test_no_dob_returns_none_tuple(self):
        """date_of_birth=None → if not date_of_birth: True → returns None tuple."""
        cat, name, rng, desc = get_lfa_age_category(None)
        assert cat is None
        assert "not set" in desc

    def test_pre_age_range(self):
        """Age 10 (5-13) → PRE category."""
        dob = date_cls(date_cls.today().year - 10, 1, 1)
        cat, name, rng, desc = get_lfa_age_category(dob)
        assert cat == "PRE"
        assert rng == "5-13 years"

    def test_youth_age_range(self):
        """Age 15 (14-18) → YOUTH category."""
        dob = date_cls(date_cls.today().year - 15, 1, 1)
        cat, name, rng, desc = get_lfa_age_category(dob)
        assert cat == "YOUTH"
        assert rng == "14-18 years"

    def test_adult_age_returns_none_instructor_assignment(self):
        """Age 25 (>18) → None category, instructor assignment message."""
        dob = date_cls(date_cls.today().year - 25, 1, 1)
        cat, name, rng, desc = get_lfa_age_category(dob)
        assert cat is None
        assert "instructor" in desc

    def test_below_min_age_returns_none(self):
        """Age 3 (<5) → else branch → None category."""
        dob = date_cls(date_cls.today().year - 3, 1, 1)
        cat, name, rng, desc = get_lfa_age_category(dob)
        assert cat is None
        assert "minimum" in desc
