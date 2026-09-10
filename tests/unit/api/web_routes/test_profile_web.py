"""
Unit tests for app/api/web_routes/profile.py

Covers:
  profile_page — non-student (no DB queries), student without specialization,
                 student with specialization+license+enrollment
  profile_edit_page — user with DOB (age calculated), user without DOB
  profile_edit_submit — invalid date format, too young, too old,
                        blocked specialization (age change), successful update → /profile

Note: profile.py missing imports (UserLicense, SemesterEnrollment, Semester,
      validate_specialization_for_age, traceback) were fixed in Sprint 54 P0.
      create=True workarounds have been removed.
"""
import asyncio
import traceback as _traceback
from datetime import date, datetime
from unittest.mock import MagicMock, patch

from fastapi.responses import RedirectResponse

from app.api.web_routes.profile import (
    profile_edit_page,
    profile_edit_submit,
    profile_page,
)
from app.models.user import UserRole

_BASE = "app.api.web_routes.profile"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    return MagicMock()


def _user(role=UserRole.STUDENT, has_dob=False, specialization=None):
    u = MagicMock()
    u.role = role
    u.date_of_birth = date(2000, 1, 1) if has_dob else None
    u.specialization = specialization
    u.credit_balance = 5
    u.credit_purchased = 10
    u.id = 99
    u.name = "Test User"
    return u


def _mock_db():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    db.query.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.first.return_value = None
    return db


# ──────────────────────────────────────────────────────────────────────────────
# profile_page (GET /profile)
# ──────────────────────────────────────────────────────────────────────────────

class TestProfilePage:

    def _run_profile(self, user, db=None):
        if db is None:
            db = _mock_db()
        with patch(f"{_BASE}.UserLicense", MagicMock()), \
             patch(f"{_BASE}.SemesterEnrollment", MagicMock()), \
             patch(f"{_BASE}.Semester", MagicMock()), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(profile_page(request=_req(), db=db, user=user))
        return result, mock_tmpl

    def test_non_student_renders_profile_template(self):
        user = _user(role=UserRole.INSTRUCTOR)
        _, mock_tmpl = self._run_profile(user)
        tmpl, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "profile.html"
        assert ctx["user"] is user
        # Non-student has empty lists
        assert ctx["user_licenses"] == []

    def test_student_without_specialization_renders_template(self):
        user = _user(role=UserRole.STUDENT, specialization=None)
        _, mock_tmpl = self._run_profile(user)
        tmpl, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "profile.html"
        assert ctx["active_license"] is None
        assert ctx["active_enrollment"] is None

    def test_student_with_specialization_queries_license(self):
        spec = MagicMock()
        spec.value = "LFA_FOOTBALL_PLAYER"
        user = _user(role=UserRole.STUDENT, specialization=spec)
        db = _mock_db()
        self._run_profile(user, db=db)
        # DB was queried (at least once for UserLicense)
        assert db.query.called

    def test_specialization_color_lfa_football_player(self):
        spec = MagicMock()
        spec.value = "LFA_FOOTBALL_PLAYER"
        user = _user(role=UserRole.STUDENT, specialization=spec)
        _, mock_tmpl = self._run_profile(user)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx.get("specialization_color") == "#f1c40f"

    def test_specialization_color_gancuju(self):
        spec = MagicMock()
        spec.value = "GANCUJU_PLAYER"
        user = _user(role=UserRole.STUDENT, specialization=spec)
        _, mock_tmpl = self._run_profile(user)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx.get("specialization_color") == "#8e44ad"

    def test_specialization_color_internship(self):
        spec = MagicMock()
        spec.value = "INTERNSHIP"
        user = _user(role=UserRole.STUDENT, specialization=spec)
        _, mock_tmpl = self._run_profile(user)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx.get("specialization_color") == "#e74c3c"

    def test_no_specialization_color_is_none(self):
        user = _user(role=UserRole.STUDENT, specialization=None)
        _, mock_tmpl = self._run_profile(user)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx.get("specialization_color") is None


# ──────────────────────────────────────────────────────────────────────────────
# profile_edit_page (GET /profile/edit)
# ──────────────────────────────────────────────────────────────────────────────

class TestProfileEditPage:

    def test_user_without_dob_renders_with_age_none(self):
        user = _user(has_dob=False)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(profile_edit_page(request=_req(), db=_mock_db(), user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx["user_age"] is None

    def test_user_with_dob_renders_with_calculated_age(self):
        user = _user(has_dob=True)
        # DOB = 2000-01-01; age ~ 25-26 years
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(profile_edit_page(request=_req(), db=_mock_db(), user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx["user_age"] is not None
        assert ctx["user_age"] >= 20

    def test_renders_profile_edit_template(self):
        user = _user()
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(profile_edit_page(request=_req(), db=_mock_db(), user=user))
        tmpl, _ = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "profile_edit.html"


# ──────────────────────────────────────────────────────────────────────────────
# profile_edit_submit (POST /profile/edit)
# ──────────────────────────────────────────────────────────────────────────────

class TestProfileEditSubmit:

    _VALID_DOB = "2000-06-15"

    def _run_edit(self, user, dob_str, db=None):
        if db is None:
            db = _mock_db()
        with patch(
            "app.services.player_identity_service.get_current_player_assignment",
            return_value=None,
        ), patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(profile_edit_submit(
                request=_req(),
                name="Test User",
                date_of_birth=dob_str,
                nationality=None,
                secondary_nationality=None,
                gender=None,
                db=db,
                user=user,
            ))
        return result, mock_tmpl

    def test_invalid_date_format_renders_error(self):
        user = _user()
        _, mock_tmpl = self._run_edit(user, "not-a-date")
        tmpl, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "profile_edit.html"
        assert "error" in ctx

    def test_too_young_renders_error(self):
        user = _user()
        dob = date(date.today().year - 2, 1, 1).isoformat()
        _, mock_tmpl = self._run_edit(user, dob)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx.get("error") == "MINIMUM_ACCOUNT_AGE"

    def test_no_unapproved_maximum_age_is_invented(self):
        user = _user()
        dob = date(date.today().year - 130, 1, 1).isoformat()
        result, _ = self._run_edit(user, dob)
        assert isinstance(result, RedirectResponse)
        assert "/profile" in result.headers["location"]

    def test_canonical_identity_policy_rejection_is_rendered(self):
        user = _user(has_dob=True)
        db = _mock_db()
        from app.services.player_identity_service import PlayerIdentityPolicyError
        with patch(
             f"{_BASE}.update_identity_profile",
             side_effect=PlayerIdentityPolicyError("SEASON_BASE_CATEGORY_CONFLICT"),
             ), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(profile_edit_submit(
                request=_req(), name="Test", date_of_birth="1999-06-01",
                nationality=None, secondary_nationality=None, gender=None,
                db=db, user=user,
            ))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx["error"] == "SEASON_BASE_CATEGORY_CONFLICT"

    def test_valid_update_commits_and_redirects(self):
        user = _user(has_dob=True)
        user.date_of_birth = date(2000, 6, 15)  # same as new DOB → no age_changed
        db = _mock_db()
        result, _ = self._run_edit(user, self._VALID_DOB, db=db)
        assert isinstance(result, RedirectResponse)
        assert "/profile" in result.headers["location"]
        db.commit.assert_called_once()

    def test_valid_update_sets_user_fields(self):
        user = _user(has_dob=True)
        user.date_of_birth = date(2000, 6, 15)
        db = _mock_db()
        self._run_edit(user, self._VALID_DOB, db=db)
        assert user.name == "Test User"
        assert user.date_of_birth == date(2000, 6, 15)
