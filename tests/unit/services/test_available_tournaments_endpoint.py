"""
Sprint 30 — app/api/api_v1/endpoints/tournaments/available.py
=============================================================
Target: ≥80% statement, ≥70% branch

Covers list_available_tournaments:
  * non-student → 403
  * no date_of_birth → 400
  * player_age_category determined (not None) → base query built
  * player_age_category is None + recent enrollment with age_category → uses it
  * player_age_category is None + no enrollment → defaults to "AMATEUR"
  * player_age_category is None + enrollment without age_category → defaults
  * age_group filter provided + not in visible → return []
  * age_group filter provided + in visible → filter applied
  * location_id filter applied
  * campus_id filter applied
  * start_date filter applied
  * end_date filter applied
  * tournaments found with sessions/location/campus/instructor → full dict built
  * tournament with no sessions/location/campus/instructor → None dicts
  * user already enrolled → is_enrolled=True
  * user not enrolled → is_enrolled=False
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from datetime import date, datetime

from app.api.api_v1.endpoints.tournaments.available import list_available_tournaments
from app.models.user import UserRole
from app.services.player_identity_service import PlayerIdentityPolicyError

_BASE = "app.api.api_v1.endpoints.tournaments.available"
_AGE_SVC = "app.services.age_category_service"


# ── helpers ──────────────────────────────────────────────────────────────────

def _student(has_dob=True):
    u = MagicMock()
    u.id = 42
    u.role = UserRole.STUDENT
    u.date_of_birth = date(2000, 1, 1) if has_dob else None
    return u


def _admin():
    u = MagicMock()
    u.id = 1
    u.role = UserRole.ADMIN
    return u


def _db_empty():
    """DB that returns empty list for tournament query."""
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.options.return_value = q
    q.order_by.return_value = q
    q.all.return_value = []
    q.first.return_value = None
    q.count.return_value = 0
    db.query.return_value = q
    return db


def _tournament_mock(has_sessions=False, has_location=True, has_campus=True,
                     has_instructor=True):
    t = MagicMock()
    t.id = 1
    t.code = "TOURN-001"
    t.name = "Test Tournament"
    t.start_date = MagicMock()
    t.start_date.isoformat.return_value = "2026-06-01"
    t.end_date = MagicMock()
    t.end_date.isoformat.return_value = "2026-08-01"
    t.status = MagicMock()
    t.status.value = "ENROLLMENT_OPEN"
    t.age_group = "YOUTH"
    t.specialization_type = "LFA_FOOTBALL_PLAYER"
    t.enrollment_cost = 500
    t.location_id = 1
    t.campus_id = 1
    t.master_instructor_id = 42
    t.created_at = None

    if has_sessions:
        sess = MagicMock()
        sess.id = 10
        sess.game_type = "REGULAR"
        sess.capacity = 20
        sess.date_start = MagicMock()
        sess.date_start.date.return_value.isoformat.return_value = "2026-06-15"
        sess.date_start.strftime.return_value = "10:00"
        sess.date_end = MagicMock()
        sess.date_end.strftime.return_value = "12:00"
        t.sessions = [sess]
    else:
        t.sessions = []

    t.location = MagicMock() if has_location else None
    if has_location:
        t.location.id = 1
        t.location.city = "Budapest"
        t.location.address = "Test Address"

    t.campus = MagicMock() if has_campus else None
    if has_campus:
        t.campus.id = 1
        t.campus.name = "Campus A"

    t.master_instructor = MagicMock() if has_instructor else None
    if has_instructor:
        t.master_instructor.id = 42
        t.master_instructor.name = "Coach"
        t.master_instructor.email = "coach@test.com"

    return t


_VALIDATION_BASE = "app.services.tournament.validation"


def _patch_age_svc(category="YOUTH"):
    """Patch the canonical assignment read and visibility policy."""
    context = SimpleNamespace(
        assignment=SimpleNamespace(effective_category=category)
    )
    return [
        patch(
            f"{_BASE}.get_authorized_football_player_context",
            return_value=context,
        ),
        patch(f"{_VALIDATION_BASE}.get_visible_tournament_age_groups",
              return_value=["PRE", "YOUTH", "AMATEUR", "PRO"]),
    ]


def _call(user, db, age_group=None, location_id=None, campus_id=None,
          start_date=None, end_date=None):
    return list_available_tournaments(
        age_group=age_group,
        location_id=location_id,
        campus_id=campus_id,
        start_date=start_date,
        end_date=end_date,
        db=db,
        current_user=user,
    )


# ============================================================================
# Authorization and validation
# ============================================================================

class TestAuthorization:

    def test_non_student_403(self):
        """LAT-01: non-student → 403."""
        with pytest.raises(HTTPException) as exc:
            list_available_tournaments(
                db=MagicMock(), current_user=_admin()
            )
        assert exc.value.status_code == 403

    def test_no_date_of_birth_400(self):
        """LAT-02: student without date_of_birth → 400."""
        with pytest.raises(HTTPException) as exc:
            list_available_tournaments(
                db=_db_empty(), current_user=_student(has_dob=False)
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "DATE_OF_BIRTH_REQUIRED"


# ============================================================================
# Age category determination
# ============================================================================

class TestAgeCategoryDetermination:

    def test_category_determined_directly(self):
        """ACD-01: the canonical effective assignment drives visibility."""
        patches = _patch_age_svc(category="YOUTH")
        with patches[0], patches[1]:
            result = _call(_student(), _db_empty())
        assert result == []

    def test_category_none_recent_enrollment_has_category(self):
        """ACD-02: assignment truth is used without consulting enrollment history."""
        context = SimpleNamespace(
            assignment=SimpleNamespace(effective_category="AMATEUR")
        )
        with patch(
            f"{_BASE}.get_authorized_football_player_context",
            return_value=context,
        ), patch(
            f"{_VALIDATION_BASE}.get_visible_tournament_age_groups",
            return_value=["AMATEUR", "PRO"],
        ) as mock_vis:
            result = _call(_student(), _db_empty())
        mock_vis.assert_called_once_with("AMATEUR")
        assert result == []

    def test_category_none_no_enrollment_defaults_amateur(self):
        """ACD-03: missing canonical assignment fails closed; no adult default."""
        with patch(
            f"{_BASE}.get_authorized_football_player_context",
            side_effect=PlayerIdentityPolicyError(
                "CURRENT_SEASON_ASSIGNMENT_REQUIRED"
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                _call(_student(), _db_empty())
        assert exc.value.status_code == 400
        assert exc.value.detail == "CURRENT_SEASON_ASSIGNMENT_REQUIRED"

    def test_category_none_enrollment_no_age_category_defaults_amateur(self):
        """ACD-04: a legacy enrollment projection cannot override assignment truth."""
        patches = _patch_age_svc(category="PRO")
        with patches[0], patches[1] as mock_vis:
            _call(_student(), _db_empty())
        mock_vis.assert_called_once_with("PRO")


# ============================================================================
# Filters
# ============================================================================

class TestFilters:

    def _run(self, **kwargs):
        patches = _patch_age_svc(category="YOUTH")
        with patches[0], patches[1]:
            return _call(_student(), _db_empty(), **kwargs)

    def test_age_group_not_in_visible_returns_empty(self):
        """FLT-01: age_group not in visible_age_groups → []."""
        context = SimpleNamespace(
            assignment=SimpleNamespace(effective_category="YOUTH")
        )
        with patch(
            f"{_BASE}.get_authorized_football_player_context",
            return_value=context,
        ), patch(
            f"{_VALIDATION_BASE}.get_visible_tournament_age_groups",
            return_value=["YOUTH", "AMATEUR"],
        ):
            result = _call(_student(), _db_empty(), age_group="PRE")
        assert result == []

    def test_age_group_in_visible_applied(self):
        """FLT-02: age_group in visible → filter applied, returns list."""
        result = self._run(age_group="YOUTH")
        assert result == []

    def test_location_id_filter(self):
        """FLT-03: location_id filter applied."""
        result = self._run(location_id=5)
        assert result == []

    def test_campus_id_filter(self):
        """FLT-04: campus_id filter applied."""
        result = self._run(campus_id=3)
        assert result == []

    def test_start_date_filter(self):
        """FLT-05: start_date filter applied."""
        result = self._run(start_date=date(2026, 6, 1))
        assert result == []

    def test_end_date_filter(self):
        """FLT-06: end_date filter applied."""
        result = self._run(end_date=date(2026, 12, 31))
        assert result == []


# ============================================================================
# Tournament result building
# ============================================================================

class TestTournamentResultBuilding:

    def _setup_db(self, tournament, user_enrollment=None):
        """Set up DB with one tournament result."""
        db = MagicMock()
        call_n = [0]
        def qside(*args):
            q = MagicMock()
            q.filter.return_value = q
            q.options.return_value = q
            q.order_by.return_value = q
            if call_n[0] == 0:
                # Main tournament query
                q.all.return_value = [tournament]
            elif call_n[0] == 1:
                # enrollment_count query
                q.count.return_value = 5
                q.all.return_value = []
            elif call_n[0] == 2:
                # user_enrollment query
                q.first.return_value = user_enrollment
                q.count.return_value = 0
            else:
                q.count.return_value = 1  # booking count for sessions
                q.all.return_value = []
                q.first.return_value = None
            call_n[0] += 1
            return q
        db.query.side_effect = qside
        return db

    def test_tournament_with_all_fields(self):
        """TRB-01: tournament with sessions, location, campus, instructor → full dict."""
        t = _tournament_mock(has_sessions=True, has_location=True,
                             has_campus=True, has_instructor=True)
        db = self._setup_db(t)
        patches = _patch_age_svc(category="YOUTH")
        with patches[0], patches[1]:
            result = _call(_student(), db)
        assert len(result) == 1
        r = result[0]
        assert r["location"] is not None
        assert r["campus"] is not None
        assert r["instructor"] is not None
        assert len(r["sessions"]) == 1

    def test_tournament_without_optional_fields(self):
        """TRB-02: tournament without location/campus/instructor → None dicts."""
        t = _tournament_mock(has_sessions=False, has_location=False,
                             has_campus=False, has_instructor=False)
        db = self._setup_db(t)
        patches = _patch_age_svc(category="YOUTH")
        with patches[0], patches[1]:
            result = _call(_student(), db)
        assert len(result) == 1
        r = result[0]
        assert r["location"] is None
        assert r["campus"] is None
        assert r["instructor"] is None
        assert r["sessions"] == []

    def test_user_enrolled_is_enrolled_true(self):
        """TRB-03: user_enrollment found → is_enrolled=True."""
        t = _tournament_mock()
        user_enrollment = MagicMock()
        user_enrollment.request_status = MagicMock()
        user_enrollment.request_status.value = "APPROVED"
        db = self._setup_db(t, user_enrollment=user_enrollment)
        patches = _patch_age_svc(category="YOUTH")
        with patches[0], patches[1]:
            result = _call(_student(), db)
        assert result[0]["is_enrolled"] is True
        assert result[0]["user_enrollment_status"] == "APPROVED"

    def test_user_not_enrolled_is_enrolled_false(self):
        """TRB-04: user_enrollment=None → is_enrolled=False."""
        t = _tournament_mock()
        db = self._setup_db(t, user_enrollment=None)
        patches = _patch_age_svc(category="YOUTH")
        with patches[0], patches[1]:
            result = _call(_student(), db)
        assert result[0]["is_enrolled"] is False
        assert result[0]["user_enrollment_status"] is None

    def test_enrollment_cost_none_defaults_to_500(self):
        """TRB-05: tournament.enrollment_cost=None → cost=500 in result."""
        t = _tournament_mock()
        t.enrollment_cost = None
        db = self._setup_db(t)
        patches = _patch_age_svc(category="YOUTH")
        with patches[0], patches[1]:
            result = _call(_student(), db)
        assert result[0]["tournament"]["enrollment_cost"] == 500
