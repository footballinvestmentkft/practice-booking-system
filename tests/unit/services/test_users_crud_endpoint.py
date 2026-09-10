"""
Unit tests for app/api/api_v1/endpoints/users/crud.py
Covers: create_user, list_users, get_user, update_user, delete_user
"""
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, call

from app.api.api_v1.endpoints.users.crud import (
    create_user, list_users, get_user, update_user, delete_user
)
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.users.crud"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(first_val=None, all_val=None, count_val=0):
    q = MagicMock()
    q.filter.return_value = q
    q.filter_by.return_value = q
    q.options.return_value = q
    q.order_by.return_value = q
    q.offset.return_value = q
    q.limit.return_value = q
    q.join.return_value = q
    q.first.return_value = first_val
    q.all.return_value = all_val if all_val is not None else []
    q.count.return_value = count_val
    q.scalar.return_value = count_val
    return q


def _seq_db(*vals):
    """n-th db.query() returns vals[n]"""
    call_n = [0]
    db = MagicMock()

    def side(*args):
        n = call_n[0]
        call_n[0] += 1
        v = vals[n] if n < len(vals) else None
        q = _q()
        if isinstance(v, list):
            q.all.return_value = v
            q.first.return_value = v[0] if v else None
        elif isinstance(v, int):
            q.count.return_value = v
            q.scalar.return_value = v
        else:
            q.first.return_value = v
        return q

    db.query.side_effect = side
    return db


def _admin():
    u = MagicMock()
    u.id = 42
    u.role = UserRole.ADMIN
    return u


def _instructor():
    u = MagicMock()
    u.id = 42
    u.role = UserRole.INSTRUCTOR
    return u


def _student():
    u = MagicMock()
    u.id = 99
    u.role = UserRole.STUDENT
    return u


def _user_data(**kwargs):
    d = MagicMock()
    d.name = "Test User"
    d.email = "test@example.com"
    d.nickname = None
    d.password = "password123"
    d.role = UserRole.STUDENT
    d.is_active = True
    d.phone = None
    d.emergency_contact = None
    d.emergency_phone = None
    d.date_of_birth = datetime(1990, 1, 1)
    d.medical_notes = None
    d.position = None
    d.specialization = None
    for k, v in kwargs.items():
        setattr(d, k, v)
    return d


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------

class TestCreateUser:
    def _call(self, user_data=None, db=None, current_user=None):
        return create_user(
            user_data=user_data or _user_data(),
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        )

    def test_email_already_exists_400(self):
        """CU-01: duplicate email → 400."""
        from fastapi import HTTPException
        db = _seq_db(MagicMock())  # existing user found
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_invalid_specialization_400(self):
        """CU-02: bad specialization key → 400."""
        from fastapi import HTTPException
        ud = _user_data(specialization="INVALID_SPEC")
        db = _seq_db(None)  # no existing user
        with patch(f"{_BASE}.get_password_hash", return_value="hashed"):
            with pytest.raises(HTTPException) as exc:
                self._call(user_data=ud, db=db)
        assert exc.value.status_code == 400
        assert "specialization" in exc.value.detail.lower()

    def test_no_specialization_success(self):
        """CU-03: no specialization → user created."""
        db = _seq_db(None)
        ud = _user_data(specialization=None)
        with patch(f"{_BASE}.get_password_hash", return_value="hashed"):
            with patch(f"{_BASE}.User") as MockUser:
                mock_user = MagicMock()
                MockUser.return_value = mock_user
                result = self._call(user_data=ud, db=db)
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_valid_specialization_success(self):
        """CU-04: valid specialization → enum resolved and user created."""
        db = _seq_db(None)
        ud = _user_data(specialization="LFA_COACH")
        with patch(f"{_BASE}.get_password_hash", return_value="hashed"):
            with patch(f"{_BASE}.User") as MockUser:
                mock_user = MagicMock()
                MockUser.return_value = mock_user
                result = self._call(user_data=ud, db=db)
        db.add.assert_called_once()


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------

class TestListUsers:
    def _call(self, current_user=None, db=None, **kwargs):
        defaults = dict(page=1, size=50, skip=None, limit=None,
                        role=None, is_active=None, search=None)
        defaults.update(kwargs)
        return list_users(
            db=db or MagicMock(),
            current_user=current_user or _admin(),
            **defaults,
        )

    def test_student_user_403(self):
        """LU-01: student → 403."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(current_user=_student())
        assert exc.value.status_code == 403

    def test_admin_no_filters(self):
        """LU-02: admin, no filters → all users returned."""
        users = [MagicMock(), MagicMock()]
        q = _q(all_val=users, count_val=2)
        db = MagicMock()
        db.query.return_value = q
        with patch(f"{_BASE}.UserList") as MockList:
            self._call(db=db)
        MockList.assert_called_once()

    def test_instructor_sees_students_only(self):
        """LU-03: instructor → filter by STUDENT role applied."""
        q = _q(all_val=[], count_val=0)
        db = MagicMock()
        db.query.return_value = q
        with patch(f"{_BASE}.UserList"):
            self._call(current_user=_instructor(), db=db)
        # filter called at least once (for student role)
        assert q.filter.call_count >= 1

    def test_role_filter_applied(self):
        """LU-04: role filter → query filtered."""
        q = _q(all_val=[], count_val=0)
        db = MagicMock()
        db.query.return_value = q
        with patch(f"{_BASE}.UserList"):
            self._call(role=UserRole.STUDENT, db=db)
        assert q.filter.call_count >= 1

    def test_is_active_filter(self):
        """LU-05: is_active=True → filter applied."""
        q = _q(all_val=[], count_val=0)
        db = MagicMock()
        db.query.return_value = q
        with patch(f"{_BASE}.UserList"):
            self._call(is_active=True, db=db)
        assert q.filter.call_count >= 1

    def test_search_filter(self):
        """LU-06: search → filter applied."""
        q = _q(all_val=[], count_val=0)
        db = MagicMock()
        db.query.return_value = q
        with patch(f"{_BASE}.UserList"):
            self._call(search="john", db=db)
        assert q.filter.call_count >= 1

    def test_skip_limit_pagination(self):
        """LU-07: skip/limit mode → backward compatible pagination."""
        q = _q(all_val=[], count_val=5)
        db = MagicMock()
        db.query.return_value = q
        with patch(f"{_BASE}.UserList"):
            self._call(skip=10, limit=5, db=db)
        q.offset.assert_called_once_with(10)
        q.limit.assert_called_once_with(5)


# ---------------------------------------------------------------------------
# get_user
# ---------------------------------------------------------------------------

class TestGetUser:
    def _call(self, user_id=7, db=None, current_user=None):
        return get_user(
            user_id=user_id,
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        )

    def test_not_found_404(self):
        """GU-01: user not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_found_returns_stats(self):
        """GU-02: user found → get_user_statistics called, UserWithStats returned."""
        mock_user = MagicMock()
        q = _q()
        q.first.return_value = mock_user
        db = MagicMock()
        db.query.return_value = q

        with patch(f"{_BASE}.get_user_statistics", return_value={"total_bookings": 3, "completed_sessions": 2, "feedback_count": 1}):
            with patch(f"{_BASE}.UserWithStats") as MockStats:
                MockStats.return_value = MagicMock()
                result = self._call(db=db)
        MockStats.assert_called_once()


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------

class TestUpdateUser:
    def _call(self, user_id=7, db=None, current_user=None, update=None):
        if update is None:
            update = MagicMock()
            update.email = None
            update.model_dump.return_value = {"name": "New Name"}
        return update_user(
            user_id=user_id,
            user_update=update,
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        )

    def test_user_not_found_404(self):
        """UU-01: user not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_email_conflict_400(self):
        """UU-02: email change conflicts → 400."""
        from fastapi import HTTPException
        existing = MagicMock()
        existing.email = "old@example.com"
        db = _seq_db(existing)  # user found

        update = MagicMock()
        update.email = "taken@example.com"
        update.model_dump.return_value = {"email": "taken@example.com"}

        with patch(f"{_BASE}.validate_email_unique", return_value=False):
            with pytest.raises(HTTPException) as exc:
                self._call(db=db, update=update)
        assert exc.value.status_code == 400

    def test_email_same_no_conflict_check(self):
        """UU-03: email unchanged → no uniqueness check."""
        existing = MagicMock()
        existing.email = "same@example.com"
        existing.role = UserRole.INSTRUCTOR
        existing.date_of_birth = None
        db = _seq_db(existing)

        update = MagicMock()
        update.email = "same@example.com"
        update.model_dump.return_value = {}

        with patch(f"{_BASE}.validate_email_unique") as mock_check:
            self._call(db=db, update=update)
        mock_check.assert_not_called()

    def test_success_updates_fields(self):
        """UU-04: valid update → setattr called for each field."""
        existing = MagicMock()
        existing.email = "old@example.com"
        existing.role = UserRole.INSTRUCTOR
        existing.date_of_birth = None
        db = _seq_db(existing)

        update = MagicMock()
        update.email = "new@example.com"
        update.model_dump.return_value = {"name": "New Name", "email": "new@example.com"}

        with patch(f"{_BASE}.validate_email_unique", return_value=True):
            self._call(db=db, update=update)
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(existing)

    def test_no_email_in_update(self):
        """UU-05: email=None in update → no uniqueness check."""
        existing = MagicMock()
        existing.role = UserRole.INSTRUCTOR
        existing.date_of_birth = None
        db = _seq_db(existing)

        update = MagicMock()
        update.email = None
        update.model_dump.return_value = {"name": "New"}

        with patch(f"{_BASE}.validate_email_unique") as mock_check:
            self._call(db=db, update=update)
        mock_check.assert_not_called()


# ---------------------------------------------------------------------------
# delete_user
# ---------------------------------------------------------------------------

class TestDeleteUser:
    def _call(self, user_id=7, db=None, current_user=None):
        return delete_user(
            user_id=user_id,
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        )

    def test_not_found_404(self):
        """DU-01: user not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_cannot_delete_self_400(self):
        """DU-02: deleting own account → 400."""
        from fastapi import HTTPException
        user = MagicMock()
        user.id = 42
        db = _seq_db(user)
        admin = _admin()  # admin.id = 42
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, current_user=admin)
        assert exc.value.status_code == 400

    def test_success_deactivates_user(self):
        """DU-03: valid delete → is_active=False, commit called."""
        user = MagicMock()
        user.id = 7
        db = _seq_db(user)
        result = self._call(db=db)
        assert user.is_active is False
        db.commit.assert_called_once()
        assert "deactivated" in result["message"].lower()
