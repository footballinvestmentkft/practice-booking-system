"""
Unit tests for app/api/api_v1/endpoints/tournaments/checkin.py
Covers: tournament_checkin — sync endpoint, datetime/timing branches
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.api.api_v1.endpoints.tournaments.checkin import tournament_checkin
from app.models.semester import SemesterCategory
from app.models.semester_enrollment import EnrollmentStatus

_BASE = "app.api.api_v1.endpoints.tournaments.checkin"
_REPO = f"{_BASE}.TournamentRepository"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(uid=42):
    u = MagicMock(); u.id = uid; return u


def _tournament(is_tournament=True, date_start=None):
    t = MagicMock()
    # semester_category drives the is-tournament guard (getattr was always False — fixed)
    t.semester_category = SemesterCategory.MINI_SEASON if is_tournament else SemesterCategory.ACADEMY_SEASON
    t.date_start = date_start
    return t


def _enrollment(checked_in_at=None):
    e = MagicMock()
    e.tournament_checked_in_at = checked_in_at
    return e


def _call(tournament_id=1, db=None, current_user=None):
    return tournament_checkin(
        tournament_id=tournament_id,
        db=db or MagicMock(),
        current_user=current_user or _user(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTournamentCheckin:

    def _q(self, first_val=None):
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = first_val
        return q

    def test_tc01_not_a_tournament_400(self):
        """TC-01: is_tournament=False → 400."""
        t = _tournament(is_tournament=False)
        with patch(_REPO) as MockRepo:
            MockRepo.return_value.get_or_404.return_value = t
            with pytest.raises(HTTPException) as exc:
                _call()
        assert exc.value.status_code == 400
        assert "tournament" in exc.value.detail.lower()

    def test_tc02_camp_category_not_tournament(self):
        """TC-02: CAMP semester_category → 400 (not a tournament semester)."""
        t = MagicMock()
        t.semester_category = SemesterCategory.CAMP
        with patch(_REPO) as MockRepo:
            MockRepo.return_value.get_or_404.return_value = t
            with pytest.raises(HTTPException) as exc:
                _call()
        assert exc.value.status_code == 400
        assert "tournament" in exc.value.detail.lower()

    def test_tc03_not_enrolled_403(self):
        """TC-03: player not enrolled or not approved → 403."""
        t = _tournament(is_tournament=True)
        db = MagicMock(); db.query.return_value = self._q(first_val=None)
        with patch(_REPO) as MockRepo:
            MockRepo.return_value.get_or_404.return_value = t
            with pytest.raises(HTTPException) as exc:
                _call(db=db)
        assert exc.value.status_code == 403

    def test_tc04_already_checked_in_idempotent(self):
        """TC-04: already checked in → idempotent 200 with already_checked_in status."""
        ts = datetime.now(timezone.utc)
        enr = _enrollment(checked_in_at=ts)
        t = _tournament(is_tournament=True)
        db = MagicMock(); db.query.return_value = self._q(first_val=enr)
        with patch(_REPO) as MockRepo:
            MockRepo.return_value.get_or_404.return_value = t
            result = _call(db=db)
        assert result["status"] == "already_checked_in"
        assert result["checked_in_at"] == ts.isoformat()

    def test_tc05_no_date_start_checkin_succeeds(self):
        """TC-05: tournament has no date_start → skip timing check, stamp OK."""
        enr = _enrollment(checked_in_at=None)
        t = _tournament(is_tournament=True, date_start=None)
        db = MagicMock(); db.query.return_value = self._q(first_val=enr)
        with patch(_REPO) as MockRepo:
            MockRepo.return_value.get_or_404.return_value = t
            result = _call(db=db)
        assert result["status"] == "checked_in"
        assert enr.tournament_checked_in_at is not None
        db.commit.assert_called_once()

    def test_tc06_too_early_400(self):
        """TC-06: now < window_open (30 min before start, window at 15 min) → 400."""
        start = datetime.now(timezone.utc) + timedelta(minutes=30)
        enr = _enrollment(checked_in_at=None)
        t = _tournament(is_tournament=True, date_start=start)
        db = MagicMock(); db.query.return_value = self._q(first_val=enr)
        with patch(_REPO) as MockRepo:
            MockRepo.return_value.get_or_404.return_value = t
            with pytest.raises(HTTPException) as exc:
                _call(db=db)
        assert exc.value.status_code == 400
        assert "wait" in exc.value.detail.lower()

    def test_tc07_in_window_checkin_ok(self):
        """TC-07: now in [window_open, start] (10 min before start) → check in."""
        start = datetime.now(timezone.utc) + timedelta(minutes=10)
        enr = _enrollment(checked_in_at=None)
        t = _tournament(is_tournament=True, date_start=start)
        db = MagicMock(); db.query.return_value = self._q(first_val=enr)
        with patch(_REPO) as MockRepo:
            MockRepo.return_value.get_or_404.return_value = t
            result = _call(db=db)
        assert result["status"] == "checked_in"
        assert result["user_id"] == 42

    def test_tc08_tournament_started_400(self):
        """TC-08: now > start (tournament started 5 min ago) → 400."""
        start = datetime.now(timezone.utc) - timedelta(minutes=5)
        enr = _enrollment(checked_in_at=None)
        t = _tournament(is_tournament=True, date_start=start)
        db = MagicMock(); db.query.return_value = self._q(first_val=enr)
        with patch(_REPO) as MockRepo:
            MockRepo.return_value.get_or_404.return_value = t
            with pytest.raises(HTTPException) as exc:
                _call(db=db)
        assert exc.value.status_code == 400
        assert "started" in exc.value.detail.lower()

    def test_tc09_naive_datetime_treated_as_utc(self):
        """TC-09: date_start is tz-naive → .replace(tzinfo=utc) applied, no TypeError."""
        # Build a tz-naive datetime that equals UTC+10min by stripping tzinfo from aware datetime.
        # This guarantees the value is truly 10 min in the future from UTC's perspective.
        start = (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(tzinfo=None)
        assert start.tzinfo is None
        enr = _enrollment(checked_in_at=None)
        t = _tournament(is_tournament=True, date_start=start)
        db = MagicMock(); db.query.return_value = self._q(first_val=enr)
        with patch(_REPO) as MockRepo:
            MockRepo.return_value.get_or_404.return_value = t
            result = _call(db=db)
        assert result["status"] == "checked_in"

    def test_tc10_result_includes_tournament_and_user_ids(self):
        """TC-10: successful check-in response includes tournament_id + user_id."""
        start = datetime.now(timezone.utc) + timedelta(minutes=5)
        enr = _enrollment(checked_in_at=None)
        t = _tournament(is_tournament=True, date_start=start)
        db = MagicMock(); db.query.return_value = self._q(first_val=enr)
        with patch(_REPO) as MockRepo:
            MockRepo.return_value.get_or_404.return_value = t
            result = _call(tournament_id=7, db=db, current_user=_user(uid=42))
        assert result["tournament_id"] == 7
        assert result["user_id"] == 42
        assert "checked_in_at" in result
