"""
Unit tests for registration routes in app/api/web_routes/auth.py

Covers:
  register_page    — authenticated user → /dashboard; unauthenticated → register.html
  register_submit  — short password, short names, invalid gender, future DOB,
                     too young, too old, duplicate email, invalid code, used code,
                     email-restricted code, successful registration → /dashboard
"""
import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

from fastapi.responses import RedirectResponse

from app.api.web_routes.auth import register_page, register_submit
from app.models.user import UserRole

_BASE = "app.api.web_routes.auth"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    return MagicMock()


def _user(role=UserRole.STUDENT):
    u = MagicMock()
    u.role = role
    u.id = 99
    u.email = "existing@test.com"
    return u


def _inv_code(is_valid=True, email_ok=True, bonus=100):
    code = MagicMock()
    code.is_valid.return_value = is_valid
    code.can_be_used_by_email.return_value = email_ok
    code.bonus_credits = bonus
    # P1b: handler now checks is_used and expires_at directly (not via is_valid()).
    # Set explicit values so the new early-exit checks behave predictably.
    code.is_used = not is_valid   # used=True when is_valid=False (mimics used code)
    code.expires_at = None        # no expiry unless overridden
    return code


def _mock_db(first_chain=None):
    """Return a db mock where .query().filter().first() returns first_chain value."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = first_chain
    return db


def _valid_form():
    """Return valid registration kwargs (excluding invitation_code / db)."""
    return dict(
        first_name="John",
        last_name="Doe",
        nickname="Johnny",
        email="newstudent@test.com",
        password="securepass",
        phone="+36201234567",
        date_of_birth="1995-06-15",
        nationality="HU",
        secondary_nationality=None,
        gender="Male",
        street_address="Kossuth u. 1",
        city="Budapest",
        postal_code="1011",
        country="Hungary",
        invitation_code="INV-20260101-ABCDEF",
    )


def _settings_patch():
    m = MagicMock()
    m.ACCESS_TOKEN_EXPIRE_MINUTES = 60
    m.COOKIE_HTTPONLY = True
    m.COOKIE_MAX_AGE = 3600
    m.COOKIE_SECURE = False
    m.COOKIE_SAMESITE = "lax"
    return m


# ──────────────────────────────────────────────────────────────────────────────
# GET /register
# ──────────────────────────────────────────────────────────────────────────────

class TestRegisterPage:

    def test_authenticated_user_redirected_to_dashboard(self):
        with patch(f"{_BASE}.get_current_user_optional", new_callable=AsyncMock) as mock_gcu:
            mock_gcu.return_value = _user()
            result = _run(register_page(request=_req(), db=_mock_db()))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_unauthenticated_renders_register_template(self):
        with patch(f"{_BASE}.get_current_user_optional", new_callable=AsyncMock) as mock_gcu, \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_gcu.return_value = None
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(register_page(request=_req(), db=_mock_db()))
        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "register.html"

    def test_auth_exception_still_shows_register(self):
        with patch(f"{_BASE}.get_current_user_optional", new_callable=AsyncMock) as mock_gcu, \
             patch(f"{_BASE}.templates") as mock_tmpl:
            mock_gcu.side_effect = Exception("token expired")
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(register_page(request=_req(), db=_mock_db()))
        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "register.html"


# ──────────────────────────────────────────────────────────────────────────────
# POST /register — validation errors
# ──────────────────────────────────────────────────────────────────────────────

class TestRegisterSubmitValidation:

    def _call(self, **overrides):
        kwargs = {**_valid_form(), "db": _mock_db(None), "request": _req(), **overrides}
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(register_submit(**kwargs))
        return result, mock_tmpl

    def test_short_password_returns_error(self):
        result, mock_tmpl = self._call(password="12345")
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx
        assert "6" in ctx["error"] or "password" in ctx["error"].lower()

    def test_short_first_name_returns_error(self):
        result, mock_tmpl = self._call(first_name="A")
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx

    def test_short_last_name_returns_error(self):
        result, mock_tmpl = self._call(last_name="B")
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx

    def test_short_nickname_returns_error(self):
        result, mock_tmpl = self._call(nickname="X")
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx

    def test_invalid_gender_returns_error(self):
        result, mock_tmpl = self._call(gender="Robot")
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx
        assert "gender" in ctx["error"].lower()

    def test_future_dob_returns_error(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        result, mock_tmpl = self._call(date_of_birth=future)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx
        assert "future" in ctx["error"].lower()

    def test_too_young_returns_error(self):
        # Born today → age 0 < 5
        dob = date.today().replace(year=date.today().year - 3).isoformat()
        result, mock_tmpl = self._call(date_of_birth=dob)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx
        assert "5" in ctx["error"]

    def test_too_old_returns_error(self):
        dob = "1800-01-01"
        result, mock_tmpl = self._call(date_of_birth=dob)
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx
        assert "valid" in ctx["error"].lower()

    def test_bad_date_format_returns_error(self):
        result, mock_tmpl = self._call(date_of_birth="not-a-date")
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx


# ──────────────────────────────────────────────────────────────────────────────
# POST /register — invitation code & DB errors
# ──────────────────────────────────────────────────────────────────────────────

class TestRegisterSubmitInvitationCode:

    def _mock_db_seq(self, email_user=None, code_obj=None):
        """First query (User by email) → email_user; second query (InvitationCode) → code_obj."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [email_user, code_obj]
        return db

    def test_duplicate_email_returns_error(self):
        existing = _user()
        db = self._mock_db_seq(email_user=existing)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(register_submit(request=_req(), db=db, **_valid_form()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx
        assert "already" in ctx["error"].lower() or "exist" in ctx["error"].lower()

    def test_invalid_invitation_code_returns_error(self):
        db = self._mock_db_seq(email_user=None, code_obj=None)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(register_submit(request=_req(), db=db, **_valid_form()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx
        assert "invalid" in ctx["error"].lower() or "invitation" in ctx["error"].lower()

    def test_used_invitation_code_returns_error(self):
        code = _inv_code(is_valid=False)
        db = self._mock_db_seq(email_user=None, code_obj=code)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(register_submit(request=_req(), db=db, **_valid_form()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx

    def test_email_restricted_code_returns_error(self):
        code = _inv_code(is_valid=True, email_ok=False)
        db = self._mock_db_seq(email_user=None, code_obj=code)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(register_submit(request=_req(), db=db, **_valid_form()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "error" in ctx
        # P1b+P2a: message was updated to "issued for a specific email address…"
        assert ("specific email" in ctx["error"].lower()
                or "invitation on" in ctx["error"].lower()
                or "restricted" in ctx["error"].lower()   # backward-compat guard
                or "different" in ctx["error"].lower())


# ──────────────────────────────────────────────────────────────────────────────
# POST /register — success
# ──────────────────────────────────────────────────────────────────────────────

class TestRegisterSubmitSuccess:

    def test_valid_registration_creates_user_and_redirects(self):
        code = _inv_code(is_valid=True, email_ok=True, bonus=150)
        db = MagicMock()
        # email check → None (not taken); invitation code → valid code
        db.query.return_value.filter.return_value.first.side_effect = [None, code]

        new_user = MagicMock()
        new_user.email = "newstudent@test.com"
        new_user.credit_balance = 150

        with patch(f"{_BASE}.get_password_hash", return_value="hashed_pw"), \
             patch(f"{_BASE}.User", return_value=new_user), \
             patch(f"{_BASE}.create_access_token", return_value="tok123"), \
             patch(f"{_BASE}.settings", _settings_patch()):
            result = _run(register_submit(request=_req(), db=db, **_valid_form()))

        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]
        # Cookie must be set
        assert "access_token" in result.headers.get("set-cookie", "")
