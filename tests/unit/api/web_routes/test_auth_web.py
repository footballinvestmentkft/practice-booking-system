"""
Unit tests for app/api/web_routes/auth.py

Covers:
  home — unauthenticated → /login, authenticated → /dashboard, exception → /login
  login_page — renders login.html
  login_submit — user not found, wrong password, inactive account,
                 success non-student → /dashboard,
                 success student+DOB → /dashboard,
                 success student no-DOB → /age-verification
  logout — redirects /login + deletes cookie
  age_verification_page — non-student → /dashboard, already verified → /dashboard,
                           unverified student → renders template
  age_verification_submit — non-student, future DOB, too young, too old,
                             valid → DB commit + /dashboard, invalid format

Note: auth.py imports (UserRole, date, traceback) were fixed in Sprint 54 P0.
      create=True workarounds have been removed.
"""
import asyncio
import traceback as _traceback
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import RedirectResponse

from app.api.web_routes.auth import (
    age_verification_page,
    age_verification_submit,
    home,
    login_page,
    login_submit,
    logout,
)
from app.models.user import UserRole

_BASE = "app.api.web_routes.auth"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    return MagicMock()


def _user(role=UserRole.STUDENT, has_dob=False, is_active=True):
    u = MagicMock()
    u.role = role
    u.date_of_birth = date(2000, 1, 1) if has_dob else None
    u.is_active = is_active
    u.email = "test@test.com"
    u.password_hash = "hashed"
    return u


def _mock_db(first_return=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = first_return
    return db


def _settings_patch():
    """Patch settings with minimal cookie config."""
    m = MagicMock()
    m.ACCESS_TOKEN_EXPIRE_MINUTES = 60
    m.COOKIE_HTTPONLY = True
    m.COOKIE_MAX_AGE = 3600
    m.COOKIE_SECURE = False
    m.COOKIE_SAMESITE = "lax"
    return m


# ──────────────────────────────────────────────────────────────────────────────
# home
# ──────────────────────────────────────────────────────────────────────────────

class TestHome:

    def test_unauthenticated_redirects_to_login(self):
        with patch(f"{_BASE}.get_current_user_optional", new_callable=AsyncMock) as mock_gcu:
            mock_gcu.return_value = None
            result = _run(home(request=_req(), db=_mock_db()))
        assert isinstance(result, RedirectResponse)
        assert "/login" in result.headers["location"]

    def test_authenticated_redirects_to_dashboard(self):
        user = _user()
        with patch(f"{_BASE}.get_current_user_optional", new_callable=AsyncMock) as mock_gcu:
            mock_gcu.return_value = user
            result = _run(home(request=_req(), db=_mock_db()))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_auth_exception_redirects_to_login(self):
        with patch(f"{_BASE}.get_current_user_optional", new_callable=AsyncMock) as mock_gcu:
            mock_gcu.side_effect = Exception("token expired")
            result = _run(home(request=_req(), db=_mock_db()))
        assert isinstance(result, RedirectResponse)
        assert "/login" in result.headers["location"]


# ──────────────────────────────────────────────────────────────────────────────
# login_page (GET /login)
# ──────────────────────────────────────────────────────────────────────────────

class TestLoginPage:

    def test_renders_login_html_template(self):
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(login_page(request=_req()))
        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "login.html"


# ──────────────────────────────────────────────────────────────────────────────
# login_submit (POST /login)
# ──────────────────────────────────────────────────────────────────────────────

class TestLoginSubmit:

    def test_user_not_found_renders_error(self):
        db = _mock_db(first_return=None)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(login_submit(request=_req(), email="nobody@test.com", password="x", next="", db=db))
        tmpl, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "login.html"
        assert "error" in ctx

    def test_wrong_password_renders_error(self):
        user = _user(is_active=True)
        db = _mock_db(first_return=user)
        with patch(f"{_BASE}.verify_password", return_value=False), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(login_submit(request=_req(), email="test@test.com", password="bad", next="", db=db))
        tmpl, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "login.html"
        assert "error" in ctx

    def test_inactive_account_renders_error(self):
        user = _user(is_active=False)
        db = _mock_db(first_return=user)
        with patch(f"{_BASE}.verify_password", return_value=True), \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(login_submit(request=_req(), email="test@test.com", password="pass", next="", db=db))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "inactive" in ctx.get("error", "").lower()

    def test_instructor_login_redirects_to_dashboard(self):
        user = _user(role=UserRole.INSTRUCTOR, is_active=True)
        db = _mock_db(first_return=user)
        s = _settings_patch()
        with patch(f"{_BASE}.verify_password", return_value=True), \
             patch(f"{_BASE}.create_access_token", return_value="tok"), \
             patch(f"{_BASE}.settings", s):
            result = _run(login_submit(request=_req(), email="i@test.com", password="p", next="", db=db))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_student_with_dob_redirects_to_dashboard(self):
        user = _user(role=UserRole.STUDENT, has_dob=True, is_active=True)
        db = _mock_db(first_return=user)
        s = _settings_patch()
        with patch(f"{_BASE}.verify_password", return_value=True), \
             patch(f"{_BASE}.create_access_token", return_value="tok"), \
             patch(f"{_BASE}.settings", s):
            result = _run(login_submit(request=_req(), email="s@test.com", password="p", next="", db=db))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_student_without_dob_redirects_to_age_verification(self):
        user = _user(role=UserRole.STUDENT, has_dob=False, is_active=True)
        db = _mock_db(first_return=user)
        s = _settings_patch()
        with patch(f"{_BASE}.verify_password", return_value=True), \
             patch(f"{_BASE}.create_access_token", return_value="tok"), \
             patch(f"{_BASE}.settings", s):
            result = _run(login_submit(request=_req(), email="s@test.com", password="p", next="", db=db))
        assert isinstance(result, RedirectResponse)
        assert "/age-verification" in result.headers["location"]


# ──────────────────────────────────────────────────────────────────────────────
# logout (GET /logout)
# ──────────────────────────────────────────────────────────────────────────────

class TestLogout:

    def test_redirects_to_login(self):
        result = _run(logout())
        assert isinstance(result, RedirectResponse)
        assert "/login" in result.headers["location"]

    def test_deletes_access_token_cookie(self):
        result = _run(logout())
        set_cookie = result.headers.get("set-cookie", "")
        assert "access_token" in set_cookie


# ──────────────────────────────────────────────────────────────────────────────
# age_verification_page (GET /age-verification)
# ──────────────────────────────────────────────────────────────────────────────

class TestAgeVerificationPage:

    def test_non_student_redirects_to_dashboard(self):
        user = _user(role=UserRole.INSTRUCTOR)
        result = _run(age_verification_page(request=_req(), db=_mock_db(), user=user))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_student_already_has_dob_redirects_to_dashboard(self):
        user = _user(role=UserRole.STUDENT, has_dob=True)
        result = _run(age_verification_page(request=_req(), db=_mock_db(), user=user))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_unverified_student_renders_template(self):
        user = _user(role=UserRole.STUDENT, has_dob=False)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(age_verification_page(request=_req(), db=_mock_db(), user=user))
        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "age_verification.html"


# ──────────────────────────────────────────────────────────────────────────────
# age_verification_submit (POST /age-verification)
# ──────────────────────────────────────────────────────────────────────────────

class TestAgeVerificationSubmit:

    def _run_submit(self, user, dob_str, db=None):
        """Run age_verification_submit with templates patched for test isolation."""
        if db is None:
            db = _mock_db()
        with patch(
            "app.services.player_identity_service.get_current_player_assignment",
            return_value=None,
        ), patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(age_verification_submit(
                request=_req(), date_of_birth=dob_str, db=db, user=user
            ))
        return result, mock_tmpl

    def test_non_student_redirects_to_dashboard(self):
        user = _user(role=UserRole.INSTRUCTOR)
        result, _ = self._run_submit(user, "1990-01-01")
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_future_date_renders_error(self):
        user = _user(role=UserRole.STUDENT)
        future = (date.today() + timedelta(days=10)).isoformat()
        _, mock_tmpl = self._run_submit(user, future)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "future" in ctx.get("error", "").lower()

    def test_too_young_renders_error(self):
        user = _user(role=UserRole.STUDENT)
        # Age 2: born 2 years ago
        dob = date(date.today().year - 2, 1, 1).isoformat()
        _, mock_tmpl = self._run_submit(user, dob)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx.get("error") == "MINIMUM_ACCOUNT_AGE"

    def test_no_unapproved_maximum_age_is_invented(self):
        user = _user(role=UserRole.STUDENT)
        dob = date(date.today().year - 130, 1, 1).isoformat()
        result, _ = self._run_submit(user, dob)
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_valid_dob_saves_and_redirects(self):
        user = _user(role=UserRole.STUDENT)
        db = MagicMock()
        dob_str = "2000-06-15"
        result, _ = self._run_submit(user, dob_str, db=db)
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]
        db.commit.assert_called_once()

    def test_invalid_date_format_renders_error(self):
        user = _user(role=UserRole.STUDENT)
        _, mock_tmpl = self._run_submit(user, "not-a-date")
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx
