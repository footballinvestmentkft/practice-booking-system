"""
Unit tests for app/api/api_v1/endpoints/auth.py
Covers: login, login_form, refresh_token, logout, read_users_me,
        change_password, register_with_invitation
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from app.api.api_v1.endpoints.auth import (
    login, login_form, refresh_token as refresh_token_endpoint,
    logout, read_users_me, change_password, register_with_invitation,
    RegisterWithInvitation,
)
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.auth"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(first_val=None):
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = first_val
    return q


def _seq_db(*vals):
    call_n = [0]
    db = MagicMock()

    def side(*args):
        n = call_n[0]
        call_n[0] += 1
        v = vals[n] if n < len(vals) else None
        return _q(first_val=v)

    db.query.side_effect = side
    return db


def _active_user(uid=42, email="user@example.com"):
    u = MagicMock()
    u.id = uid
    u.email = email
    u.password_hash = "hashed"
    u.is_active = True
    u.role = MagicMock()
    u.role.value = "STUDENT"
    return u


def _login_data(email="user@example.com", password="pass"):
    d = MagicMock()
    d.email = email
    d.password = password
    return d


def _form_data(username="user@example.com", password="pass"):
    d = MagicMock()
    d.username = username
    d.password = password
    return d


def _reg_data(**kwargs):
    """Build RegisterWithInvitation with valid defaults."""
    defaults = {
        "email": "new@example.com",
        "password": "secret123",
        "name": "New Student",
        "first_name": "New",
        "last_name": "Student",
        "nickname": "newstudent",
        "phone": "+36301234567",
        "date_of_birth": datetime(2000, 1, 1, tzinfo=timezone.utc),
        "nationality": "Hungarian",
        "gender": "male",
        "street_address": "Andrássy út 1",
        "city": "Budapest",
        "postal_code": "1061",
        "country": "Hungary",
        "invitation_code": "TESTCODE",
    }
    defaults.update(kwargs)
    return RegisterWithInvitation(**defaults)


def _invite_code(code="TESTCODE", is_valid=True, is_used=False,
                 expires_at=None, bonus_credits=50,
                 can_use_email=True, invited_email=None):
    c = MagicMock()
    c.code = code
    c.is_valid.return_value = is_valid
    c.is_used = is_used
    c.expires_at = expires_at
    c.bonus_credits = bonus_credits
    c.can_be_used_by_email.return_value = can_use_email
    c.invited_email = invited_email
    return c


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

class TestLogin:
    def _call(self, credentials=None, db=None):
        return login(
            user_credentials=credentials or _login_data(),
            db=db or MagicMock(),
        )

    def test_user_not_found_401(self):
        """L-01: user not found → 401."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with patch(f"{_BASE}.AuditService"):
            with pytest.raises(HTTPException) as exc:
                self._call(db=db)
        assert exc.value.status_code == 401

    def test_wrong_password_401(self):
        """L-02: wrong password → 401."""
        from fastapi import HTTPException
        user = _active_user()
        db = _seq_db(user)
        with patch(f"{_BASE}.verify_password", return_value=False):
            with patch(f"{_BASE}.AuditService"):
                with pytest.raises(HTTPException) as exc:
                    self._call(db=db)
        assert exc.value.status_code == 401

    def test_inactive_user_400(self):
        """L-03: inactive user → 400."""
        from fastapi import HTTPException
        user = _active_user()
        user.is_active = False
        db = _seq_db(user)
        with patch(f"{_BASE}.verify_password", return_value=True):
            with patch(f"{_BASE}.AuditService"):
                with pytest.raises(HTTPException) as exc:
                    self._call(db=db)
        assert exc.value.status_code == 400

    def test_success_returns_tokens(self):
        """L-04: valid credentials → access+refresh tokens returned."""
        user = _active_user()
        db = _seq_db(user)
        with patch(f"{_BASE}.verify_password", return_value=True):
            with patch(f"{_BASE}.create_access_token", return_value="access_tok"):
                with patch(f"{_BASE}.create_refresh_token", return_value="refresh_tok"):
                    with patch(f"{_BASE}.AuditService"):
                        with patch("app.services.gamification.GamificationService") as MockGami:
                            MockGami.return_value.check_and_unlock_achievements.return_value = []
                            result = self._call(db=db)
        assert result["access_token"] == "access_tok"
        assert result["token_type"] == "bearer"

    def test_success_gamification_exception_ignored(self):
        """L-05: achievement check raises → login still succeeds."""
        user = _active_user()
        db = _seq_db(user)
        with patch(f"{_BASE}.verify_password", return_value=True):
            with patch(f"{_BASE}.create_access_token", return_value="acc"):
                with patch(f"{_BASE}.create_refresh_token", return_value="ref"):
                    with patch(f"{_BASE}.AuditService"):
                        with patch("app.services.gamification.GamificationService") as MockGami:
                            MockGami.return_value.check_and_unlock_achievements.side_effect = Exception("gami fail")
                            result = self._call(db=db)
        assert result["access_token"] == "acc"

    def test_success_with_unlocked_achievements(self):
        """L-06: unlocked achievements → still returns tokens."""
        user = _active_user()
        db = _seq_db(user)
        with patch(f"{_BASE}.verify_password", return_value=True):
            with patch(f"{_BASE}.create_access_token", return_value="acc"):
                with patch(f"{_BASE}.create_refresh_token", return_value="ref"):
                    with patch(f"{_BASE}.AuditService"):
                        with patch("app.services.gamification.GamificationService") as MockGami:
                            MockGami.return_value.check_and_unlock_achievements.return_value = ["badge1", "badge2"]
                            result = self._call(db=db)
        assert result["access_token"] == "acc"


# ---------------------------------------------------------------------------
# login_form
# ---------------------------------------------------------------------------

class TestLoginForm:
    def _call(self, form_data=None, db=None):
        return login_form(
            form_data=form_data or _form_data(),
            db=db or MagicMock(),
        )

    def test_user_not_found_401(self):
        """LF-01: user not found → 401."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 401

    def test_wrong_password_401(self):
        """LF-02: wrong password → 401."""
        from fastapi import HTTPException
        user = _active_user()
        db = _seq_db(user)
        with patch(f"{_BASE}.verify_password", return_value=False):
            with pytest.raises(HTTPException) as exc:
                self._call(db=db)
        assert exc.value.status_code == 401

    def test_inactive_user_400(self):
        """LF-03: inactive → 400."""
        from fastapi import HTTPException
        user = _active_user()
        user.is_active = False
        db = _seq_db(user)
        with patch(f"{_BASE}.verify_password", return_value=True):
            with pytest.raises(HTTPException) as exc:
                self._call(db=db)
        assert exc.value.status_code == 400

    def test_success(self):
        """LF-04: success → tokens returned."""
        user = _active_user()
        db = _seq_db(user)
        with patch(f"{_BASE}.verify_password", return_value=True):
            with patch(f"{_BASE}.create_access_token", return_value="acc"):
                with patch(f"{_BASE}.create_refresh_token", return_value="ref"):
                    result = self._call(db=db)
        assert result["access_token"] == "acc"


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    def _call(self, token_data=None, db=None):
        td = MagicMock()
        td.refresh_token = "refresh_tok"
        return refresh_token_endpoint(
            token_data=token_data or td,
            db=db or MagicMock(),
        )

    def test_invalid_token_401(self):
        """RT-01: invalid refresh token → 401."""
        from fastapi import HTTPException
        with patch(f"{_BASE}.verify_token", return_value=None):
            with pytest.raises(HTTPException) as exc:
                self._call(db=MagicMock())
        assert exc.value.status_code == 401

    def test_user_not_found_401(self):
        """RT-02: user not found → 401."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with patch(f"{_BASE}.verify_token", return_value="user@example.com"):
            with pytest.raises(HTTPException) as exc:
                self._call(db=db)
        assert exc.value.status_code == 401

    def test_inactive_user_400(self):
        """RT-03: inactive user → 400."""
        from fastapi import HTTPException
        user = _active_user()
        user.is_active = False
        db = _seq_db(user)
        with patch(f"{_BASE}.verify_token", return_value="user@example.com"):
            with pytest.raises(HTTPException) as exc:
                self._call(db=db)
        assert exc.value.status_code == 400

    def test_success_returns_tokens(self):
        """RT-04: valid token → new tokens."""
        user = _active_user()
        db = _seq_db(user)
        with patch(f"{_BASE}.verify_token", return_value="user@example.com"):
            with patch(f"{_BASE}.create_access_token", return_value="new_acc"):
                with patch(f"{_BASE}.create_refresh_token", return_value="new_ref"):
                    result = self._call(db=db)
        assert result["access_token"] == "new_acc"


# ---------------------------------------------------------------------------
# logout / read_users_me
# ---------------------------------------------------------------------------

class TestLogout:
    def test_success(self):
        """LO-01: always succeeds."""
        result = logout()
        assert "logged out" in result["message"].lower()


class TestReadUsersMe:
    def test_returns_current_user(self):
        """UM-01: returns current_user."""
        user = _active_user()
        result = read_users_me(current_user=user)
        assert result is user


# ---------------------------------------------------------------------------
# change_password
# ---------------------------------------------------------------------------

class TestChangePassword:
    def _call(self, password_data=None, db=None, current_user=None):
        if password_data is None:
            password_data = MagicMock()
            password_data.old_password = "oldpass"
            password_data.new_password = "newpass"
        return change_password(
            password_data=password_data,
            db=db or MagicMock(),
            current_user=current_user or _active_user(),
        )

    def test_wrong_old_password_400(self):
        """CP-01: wrong old password → 400."""
        from fastapi import HTTPException
        with patch(f"{_BASE}.verify_password", return_value=False):
            with pytest.raises(HTTPException) as exc:
                self._call()
        assert exc.value.status_code == 400

    def test_success_updates_hash(self):
        """CP-02: correct old password → hash updated."""
        user = _active_user()
        db = MagicMock()
        with patch(f"{_BASE}.verify_password", return_value=True):
            with patch(f"{_BASE}.get_password_hash", return_value="new_hash"):
                result = self._call(db=db, current_user=user)
        assert user.password_hash == "new_hash"
        db.commit.assert_called_once()
        assert "updated" in result["message"].lower()


# ---------------------------------------------------------------------------
# register_with_invitation
# ---------------------------------------------------------------------------

class TestRegisterWithInvitation:
    def _call(self, reg_data=None, db=None):
        return register_with_invitation(
            registration_data=reg_data or _reg_data(),
            db=db or MagicMock(),
        )

    def test_email_already_exists_400(self):
        """RWI-01: email already registered → 400."""
        from fastapi import HTTPException
        existing = _active_user()
        db = _seq_db(existing)  # existing_user found
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_invitation_code_not_found_404(self):
        """RWI-02: code not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(None, None)  # no existing user, no invite code
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_code_used_400(self):
        """RWI-03: code already used → 400."""
        from fastapi import HTTPException
        invite = _invite_code(is_valid=False, is_used=True)
        db = _seq_db(None, invite)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_code_expired_400(self):
        """RWI-04: code expired → 400."""
        from fastapi import HTTPException
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        invite = _invite_code(is_valid=False, is_used=False, expires_at=past)
        db = _seq_db(None, invite)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_email_restricted_403(self):
        """RWI-05: code restricted to different email → 403."""
        from fastapi import HTTPException
        invite = _invite_code(is_valid=True, can_use_email=False, invited_email="other@example.com")
        db = _seq_db(None, invite)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 403

    def test_password_too_short_400(self):
        """RWI-06: password < 6 chars → 400."""
        from fastapi import HTTPException
        invite = _invite_code(is_valid=True)
        db = _seq_db(None, invite)
        reg = _reg_data(password="ab")
        with pytest.raises(HTTPException) as exc:
            self._call(reg_data=reg, db=db)
        assert exc.value.status_code == 400

    def test_invalid_first_name_400(self):
        """RWI-07: invalid first name → 400."""
        from fastapi import HTTPException
        invite = _invite_code(is_valid=True)
        db = _seq_db(None, invite)
        with patch(f"{_BASE}.validate_name", return_value=(False, "Invalid name")):
            with pytest.raises(HTTPException) as exc:
                self._call(db=db)
        assert exc.value.status_code == 400

    def test_invalid_last_name_400(self):
        """RWI-08: invalid last name → 400."""
        from fastapi import HTTPException
        invite = _invite_code(is_valid=True)
        db = _seq_db(None, invite)
        call_n = [0]
        def name_side(name, field):
            call_n[0] += 1
            return (True, "") if call_n[0] == 1 else (False, "Bad last name")
        with patch(f"{_BASE}.validate_name", side_effect=name_side):
            with pytest.raises(HTTPException) as exc:
                self._call(db=db)
        assert exc.value.status_code == 400

    def test_invalid_phone_400(self):
        """RWI-09: invalid phone → 400."""
        from fastapi import HTTPException
        invite = _invite_code(is_valid=True)
        db = _seq_db(None, invite)
        with patch(f"{_BASE}.validate_name", return_value=(True, "")):
            with patch(f"{_BASE}.validate_phone_number", return_value=(False, None, "Bad phone")):
                with pytest.raises(HTTPException) as exc:
                    self._call(db=db)
        assert exc.value.status_code == 400

    def test_invalid_address_400(self):
        """RWI-10: invalid address → 400."""
        from fastapi import HTTPException
        invite = _invite_code(is_valid=True)
        db = _seq_db(None, invite)
        with patch(f"{_BASE}.validate_name", return_value=(True, "")):
            with patch(f"{_BASE}.validate_phone_number", return_value=(True, "+36301234567", None)):
                with patch(f"{_BASE}.validate_address", return_value=(False, "Bad address")):
                    with pytest.raises(HTTPException) as exc:
                        self._call(db=db)
        assert exc.value.status_code == 400

    def test_success_registers_user(self):
        """RWI-11: all valid → user created, tokens returned."""
        invite = _invite_code(is_valid=True, bonus_credits=50)
        db = _seq_db(None, invite)
        mock_user = MagicMock()
        mock_user.email = "new@example.com"
        mock_user.id = 99
        mock_user.name = "New Student"
        with patch(f"{_BASE}.validate_name", return_value=(True, "")):
            with patch(f"{_BASE}.validate_phone_number", return_value=(True, "+36301234567", None)):
                with patch(f"{_BASE}.validate_address", return_value=(True, None)):
                    with patch(f"{_BASE}.get_password_hash", return_value="hashed"):
                        with patch(f"{_BASE}.User", return_value=mock_user):
                            with patch(f"{_BASE}.create_access_token", return_value="acc_tok"):
                                with patch(f"{_BASE}.create_refresh_token", return_value="ref_tok"):
                                    with patch(f"{_BASE}.AuditService"):
                                        result = self._call(db=db)
        from app.models.credit_transaction import CreditTransaction
        assert result["access_token"] == "acc_tok"
        # db.add called twice: new_user + bonus CreditTransaction
        assert db.add.call_count == 2
        db.add.assert_any_call(mock_user)
        bonus_arg = db.add.call_args_list[1][0][0]
        assert isinstance(bonus_arg, CreditTransaction)
        assert bonus_arg.amount == 50
        db.commit.assert_called_once()
