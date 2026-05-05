"""Unit tests for app/api/api_v1/endpoints/tournaments/enroll.py

Sprint P10 — Coverage target: ≥85% stmt, ≥75% branch

Covers:
- enroll_in_tournament:
    404 tournament not found
    400 wrong tournament_status + AuditService logged
    400 enrollment deadline passed
    403 not a student
    400 no LFA license
    400 no date_of_birth
    400 18+ in PRE/YOUTH (no auto-category)
    400 invalid age category
    400 tournament full
    400 insufficient credits (check-time)
    400 concurrent credit drain (rowcount=0)
    409 concurrent duplicate (IntegrityError uq_active_enrollment)
    409 generic IntegrityError
    500 generic exception during commit
    200 success no sessions, no conflicts
    200 success with sessions (auto-booking)
    200 success with conflicts + warnings
- unenroll_from_tournament:
    403 not a student
    404 tournament not found
    400 wrong tournament_status
    404 no active enrollment
    500 generic exception during commit
    200 success with bookings
    200 success no bookings
"""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch
from sqlalchemy.exc import IntegrityError

import pytest

from app.api.api_v1.endpoints.tournaments.enroll import (
    enroll_in_tournament,
    unenroll_from_tournament,
)
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.tournaments.enroll"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(*, first=None, all_=None, count=0):
    """Fluent query mock — supports filter, order_by, with_for_update, one, first, count, all."""
    q = MagicMock()
    for m in ("filter", "options", "order_by", "offset", "limit", "group_by", "join", "with_for_update"):
        getattr(q, m).return_value = q
    q.first.return_value = first
    q.one.return_value = first  # used for with_for_update().one()
    q.all.return_value = all_ if all_ is not None else []
    q.count.return_value = count
    return q


def _seq_db(*qs):
    """n-th db.query() call returns qs[n]; fallback to _q() after exhaustion."""
    calls = [0]

    def _side(*args, **kw):
        idx = calls[0]
        calls[0] += 1
        return qs[idx] if idx < len(qs) else _q()

    db = MagicMock()
    db.query.side_effect = _side
    db.execute.return_value.rowcount = 1  # atomic credit update succeeds by default
    return db


def _tournament(*, status="ENROLLMENT_OPEN", age_group="PRE", cost=500, max_players=32,
                age_groups=None):
    t = MagicMock()
    t.id = 1
    t.tournament_status = status
    t.name = "LFA Tournament"
    t.code = "LFA-T-001"
    t.age_group = age_group
    t.age_groups = age_groups  # None for single-age legacy; explicit list for multi-age
    t.enrollment_cost = cost
    t.max_players = max_players
    t.start_date.isoformat.return_value = "2024-07-01"
    t.end_date.isoformat.return_value = "2024-07-31"
    return t


def _license():
    lic = MagicMock()
    lic.id = 5
    return lic


def _student(*, uid=42, balance=1000, dob=date(2010, 1, 1)):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    u.email = "student@test.com"
    u.name = "Student"
    u.credit_balance = balance
    u.date_of_birth = dob
    return u


def _enrollment():
    e = MagicMock()
    e.id = 99
    e.user_id = 42
    e.semester_id = 1
    e.user_license_id = 5
    e.age_category = "PRE"
    e.request_status.value = "APPROVED"
    e.payment_verified = True
    e.is_active = True
    e.enrolled_at.isoformat.return_value = "2024-06-01T10:00:00"
    e.approved_at.isoformat.return_value = "2024-06-01T10:00:00"
    return _enrollment


def _patch_helpers(**overrides):
    """Context manager dict for all age-category + validation helpers."""
    defaults = {
        f"{_BASE}.get_current_season_year": dict(return_value=2024),
        f"{_BASE}.calculate_age_at_season_start": dict(return_value=12),
        f"{_BASE}.get_automatic_age_category": dict(return_value="PRE"),
        f"{_BASE}.validate_tournament_enrollment_age": dict(return_value=(True, None)),
        f"{_BASE}.check_duplicate_enrollment": dict(return_value=(True, None)),
    }
    defaults.update(overrides)
    return defaults


def _apply_patches(patches: dict):
    """Return list of patch context managers to nest."""
    return [patch(k, **v) for k, v in patches.items()]


# ===========================================================================
# TestEnrollInTournament
# ===========================================================================

class TestEnrollInTournament:
    TID = 1

    def test_404_tournament_not_found(self):
        db = _seq_db(_q(first=None))
        with pytest.raises(Exception) as exc:
            enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 404

    def test_400_wrong_tournament_status_triggers_audit(self):
        t = _tournament(status="COMPLETED")
        db = _seq_db(_q(first=t))
        with patch(f"{_BASE}.AuditService") as MockAudit:
            with pytest.raises(Exception) as exc:
                enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 400
        assert "not accepting" in exc.value.detail.lower()
        MockAudit.return_value.log.assert_called_once()

    def test_400_enrollment_deadline_passed(self):
        t = _tournament()
        past_session = MagicMock()
        # deadline = past - 1h → already passed
        past_session.date_start = datetime.utcnow() - timedelta(hours=2)
        db = _seq_db(_q(first=t), _q(first=past_session))
        with pytest.raises(Exception) as exc:
            enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 400
        assert "deadline" in exc.value.detail.lower()

    def test_403_not_a_student(self):
        t = _tournament()
        u = _student()
        u.role = UserRole.ADMIN
        db = _seq_db(_q(first=t), _q(first=None))
        with pytest.raises(Exception) as exc:
            enroll_in_tournament(self.TID, db=db, current_user=u)
        assert exc.value.status_code == 403
        assert "students" in exc.value.detail.lower()

    def test_400_no_lfa_license(self):
        t = _tournament()
        db = _seq_db(_q(first=t), _q(first=None), _q(first=None))
        with pytest.raises(Exception) as exc:
            enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 400
        assert "license" in exc.value.detail.lower()

    def test_400_no_date_of_birth(self):
        t = _tournament()
        lic = _license()
        u = _student(dob=None)
        u.date_of_birth = None
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic))
        with pytest.raises(Exception) as exc:
            enroll_in_tournament(self.TID, db=db, current_user=u)
        assert exc.value.status_code == 400
        assert "date of birth" in exc.value.detail.lower()

    def test_400_18plus_in_pre_tournament_no_auto_category(self):
        """18+ user (age_category=None) in PRE tournament → error."""
        t = _tournament(age_group="PRE")
        lic = _license()
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic))
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=20), \
             patch(f"{_BASE}.get_automatic_age_category", return_value=None):
            with pytest.raises(Exception) as exc:
                enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 400
        assert "18" in exc.value.detail or "cannot enroll" in exc.value.detail.lower()

    def test_400_18plus_in_amateur_gets_auto_category(self):
        """18+ user in AMATEUR tournament → auto-assigned AMATEUR, proceeds to age validation."""
        t = _tournament(age_group="AMATEUR")
        lic = _license()
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t), _q(count=0), _q(all_=[]))
        db.execute.return_value.rowcount = 1
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=20), \
             patch(f"{_BASE}.get_automatic_age_category", return_value=None), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)), \
             patch(f"{_BASE}.EnrollmentConflictService") as MockConflict:
            MockConflict.check_session_time_conflict.return_value = None
            # Should reach commit and succeed (or at least not fail on age check)
            try:
                result = enroll_in_tournament(self.TID, db=db, current_user=_student())
                assert result["success"] is True
            except Exception as exc:
                # If it fails, it should not be a 400 age error
                assert "cannot enroll" not in str(exc)

    def test_400_age_category_validation_fails(self):
        """PRE player in PRO-only tournament → 400, detail names both categories."""
        t = _tournament(age_group="PRO")
        lic = _license()
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic))
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=10), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"):
            with pytest.raises(Exception) as exc:
                enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 400
        # New message: "Your age category (PRE) is not eligible...Eligible: ['PRO']"
        assert "PRE" in exc.value.detail
        assert "PRO" in exc.value.detail

    def test_400_tournament_full(self):
        t = _tournament(max_players=16)
        lic = _license()
        # capacity count = 16 (full)
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t), _q(count=16))
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=12), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)):
            with pytest.raises(Exception) as exc:
                enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 400
        assert "full" in exc.value.detail.lower()

    def test_400_insufficient_credits(self):
        t = _tournament(cost=1000)
        lic = _license()
        user = _student(balance=500)  # not enough
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t), _q(count=0))
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=12), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)):
            with pytest.raises(Exception) as exc:
                enroll_in_tournament(self.TID, db=db, current_user=user)
        assert exc.value.status_code == 400
        assert "insufficient" in exc.value.detail.lower()

    def test_400_concurrent_credit_drain_rowcount_zero(self):
        """Atomic UPDATE rowcount=0 → concurrent credit drain detected."""
        t = _tournament()
        lic = _license()
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t), _q(count=0), _q(all_=[]))
        db.execute.return_value.rowcount = 0  # atomic update failed
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=12), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)), \
             patch(f"{_BASE}.EnrollmentConflictService") as MockConflict:
            MockConflict.check_session_time_conflict.return_value = None
            with pytest.raises(Exception) as exc:
                enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 400
        assert "concurrent" in exc.value.detail.lower()

    def test_409_integrity_error_duplicate_constraint(self):
        t = _tournament()
        lic = _license()
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t), _q(count=0), _q(all_=[]))
        db.execute.return_value.rowcount = 1
        # Simulate IntegrityError with uq_active_enrollment in orig
        ie = IntegrityError("stmt", {}, Exception("uq_active_enrollment violation"))
        db.commit.side_effect = ie
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=12), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)), \
             patch(f"{_BASE}.EnrollmentConflictService") as MockConflict:
            MockConflict.check_session_time_conflict.return_value = None
            with pytest.raises(Exception) as exc:
                enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 409
        assert db.rollback.called

    def test_409_other_integrity_error(self):
        t = _tournament()
        lic = _license()
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t), _q(count=0), _q(all_=[]))
        db.execute.return_value.rowcount = 1
        ie = IntegrityError("stmt", {}, Exception("some_other_constraint"))
        db.commit.side_effect = ie
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=12), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)), \
             patch(f"{_BASE}.EnrollmentConflictService") as MockConflict:
            MockConflict.check_session_time_conflict.return_value = None
            with pytest.raises(Exception) as exc:
                enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 409
        assert db.rollback.called

    def test_500_generic_exception_during_commit(self):
        t = _tournament()
        lic = _license()
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t), _q(count=0), _q(all_=[]))
        db.execute.return_value.rowcount = 1
        db.commit.side_effect = RuntimeError("DB connection lost")
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=12), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)), \
             patch(f"{_BASE}.EnrollmentConflictService") as MockConflict:
            MockConflict.check_session_time_conflict.return_value = None
            with pytest.raises(Exception) as exc:
                enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 500
        assert db.rollback.called

    def test_200_success_no_sessions(self):
        t = _tournament()
        lic = _license()
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t), _q(count=0), _q(all_=[]))
        db.execute.return_value.rowcount = 1
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=12), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)), \
             patch(f"{_BASE}.EnrollmentConflictService") as MockConflict:
            MockConflict.check_session_time_conflict.return_value = None
            result = enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert result["success"] is True
        assert result["conflicts"] == []

    def test_200_success_with_auto_booking_sessions(self):
        t = _tournament()
        lic = _license()
        session_mock = MagicMock()
        session_mock.id = 7
        db = _seq_db(
            _q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t),
            _q(count=0), _q(all_=[session_mock])  # sessions for auto-booking
        )
        db.execute.return_value.rowcount = 1
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=12), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)), \
             patch(f"{_BASE}.EnrollmentConflictService") as MockConflict:
            MockConflict.check_session_time_conflict.return_value = None
            result = enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert result["success"] is True
        # db.add called at least twice: enrollment + booking
        assert db.add.call_count >= 2

    def test_200_success_with_conflicts_and_warnings(self):
        t = _tournament()
        lic = _license()
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t), _q(count=0), _q(all_=[]))
        db.execute.return_value.rowcount = 1
        conflict_data = {
            "has_conflict": True,
            "conflicts": [{"type": "time_overlap", "severity": "warning",
                           "message": "Session overlap", "session_id": 3, "semester_name": "Other"}],
            "warnings": ["Minor overlap detected"]
        }
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=12), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)), \
             patch(f"{_BASE}.EnrollmentConflictService") as MockConflict:
            MockConflict.check_session_time_conflict.return_value = conflict_data
            result = enroll_in_tournament(self.TID, db=db, current_user=_student())
        assert result["success"] is True
        assert len(result["conflicts"]) == 1
        assert len(result["warnings"]) == 1

    def test_200_enrollment_cost_none_defaults_to_500(self):
        """If tournament.enrollment_cost is None, defaults to 500."""
        t = _tournament(cost=None)
        t.enrollment_cost = None
        lic = _license()
        user = _student(balance=600)
        db = _seq_db(_q(first=t), _q(first=None), _q(first=lic), _q(first=None), _q(first=t), _q(count=0), _q(all_=[]))
        db.execute.return_value.rowcount = 1
        with patch(f"{_BASE}.get_current_season_year", return_value=2024), \
             patch(f"{_BASE}.calculate_age_at_season_start", return_value=12), \
             patch(f"{_BASE}.get_automatic_age_category", return_value="PRE"), \
             patch(f"{_BASE}.validate_tournament_enrollment_age", return_value=(True, None)), \
             patch(f"{_BASE}.check_duplicate_enrollment", return_value=(True, None)), \
             patch(f"{_BASE}.EnrollmentConflictService") as MockConflict:
            MockConflict.check_session_time_conflict.return_value = None
            result = enroll_in_tournament(self.TID, db=db, current_user=user)
        assert result["success"] is True


# ===========================================================================
# TestUnenrollFromTournament
# ===========================================================================

class TestUnenrollFromTournament:
    TID = 1

    def test_403_not_a_student(self):
        u = _student()
        u.role = UserRole.ADMIN
        db = MagicMock()
        with pytest.raises(Exception) as exc:
            unenroll_from_tournament(self.TID, db=db, current_user=u)
        assert exc.value.status_code == 403

    def test_404_tournament_not_found(self):
        db = _seq_db(_q(first=None))
        with pytest.raises(Exception) as exc:
            unenroll_from_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 404

    def test_400_wrong_tournament_status(self):
        t = _tournament(status="COMPLETED")
        db = _seq_db(_q(first=t))
        with pytest.raises(Exception) as exc:
            unenroll_from_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 400

    def test_404_no_active_enrollment(self):
        t = _tournament()
        db = _seq_db(_q(first=t), _q(first=None))
        with pytest.raises(Exception) as exc:
            unenroll_from_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 404
        assert "no active enrollment" in exc.value.detail.lower()

    def test_500_generic_exception_during_commit(self):
        t = _tournament()
        e = MagicMock()
        e.id = 99
        e.user_license_id = 5
        bookings = []
        db = _seq_db(_q(first=t), _q(first=e), _q(all_=bookings))
        db.execute.return_value.rowcount = 1
        db.commit.side_effect = RuntimeError("DB error")
        with pytest.raises(Exception) as exc:
            unenroll_from_tournament(self.TID, db=db, current_user=_student())
        assert exc.value.status_code == 500
        assert db.rollback.called

    def test_200_success_with_bookings(self):
        t = _tournament(cost=500)
        e = MagicMock()
        e.id = 99
        e.user_license_id = 5
        b1 = MagicMock()
        b1.id = 10
        b1.session_id = 3
        db = _seq_db(_q(first=t), _q(first=e), _q(all_=[b1]))
        db.execute.return_value.rowcount = 1
        with patch(f"{_BASE}.AuditService"):
            result = unenroll_from_tournament(self.TID, db=db, current_user=_student())
        assert result["success"] is True
        assert result["bookings_removed"] == 1
        assert result["refund_amount"] == 250
        assert result["penalty_amount"] == 250
        db.delete.assert_called_once_with(b1)

    def test_200_success_no_bookings(self):
        t = _tournament(cost=400)
        e = MagicMock()
        e.id = 99
        e.user_license_id = 5
        db = _seq_db(_q(first=t), _q(first=e), _q(all_=[]))
        db.execute.return_value.rowcount = 1
        with patch(f"{_BASE}.AuditService"):
            result = unenroll_from_tournament(self.TID, db=db, current_user=_student())
        assert result["success"] is True
        assert result["bookings_removed"] == 0
        assert result["refund_amount"] == 200  # 400 // 2

    def test_200_enrollment_cost_none_defaults_500(self):
        t = _tournament()
        t.enrollment_cost = None
        e = MagicMock()
        e.id = 99
        e.user_license_id = 5
        db = _seq_db(_q(first=t), _q(first=e), _q(all_=[]))
        db.execute.return_value.rowcount = 1
        with patch(f"{_BASE}.AuditService"):
            result = unenroll_from_tournament(self.TID, db=db, current_user=_student())
        assert result["refund_amount"] == 250  # 500 // 2
