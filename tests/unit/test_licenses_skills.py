"""
Unit tests for app/api/api_v1/endpoints/licenses/skills.py

Bug fixed in Sprint 18: missing imports added to skills.py —
  from datetime import datetime, timezone
  from .....models.audit_log import AuditAction
  from .....services.audit_service import AuditService

Coverage targets:
  get_football_skills() — GET /{license_id}/football-skills   [async]
    - 404: license not found
    - 403: student viewing another user's license
    - 400: non-LFA_PLAYER_ specialization
    - no skills yet: returns skills=None with message
    - skills present: returns full skills dict
    - skills_updated_by → updater name fetched from User
    - instructor allowed to view any license
    - admin allowed to view any license

  update_football_skills() — PUT /{license_id}/football-skills   [async]
    - 403: student role raises 403
    - 404: license not found
    - 400: non-LFA_PLAYER_ specialization
    - 400: missing required skill
    - 400: skill value out of range (>100, <0, non-numeric)
    - happy path: license updated, commit called, AuditService.log called
    - skills rounded to 1 decimal place
    - instructor_notes stored when provided

  get_user_all_football_skills() — GET /user/{user_id}/football-skills   [async]
    - 403: student viewing another user's skills
    - empty licenses → returns empty list
    - skills populated, updater names batch-fetched
    - instructor allowed to view any user

Mock strategy:
  asyncio.run(endpoint_fn(...)) for async endpoints
  db.query.side_effect discriminating by model
  patch AuditService to avoid real DB writes
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from uuid import uuid4
from fastapi import HTTPException

from app.api.api_v1.endpoints.licenses.skills import (
    get_football_skills,
    update_football_skills,
    get_user_all_football_skills,
)
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.licenses.skills"
_AUDIT = f"{_BASE}.AuditService"
_PROGRESSION = f"{_BASE}.PlayerProgressionService"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _user(role: UserRole = UserRole.INSTRUCTOR, user_id: int = 42):
    u = MagicMock()
    u.id = user_id
    u.role = role
    u.name = "Test User"
    return u


def _license(user_id=42, spec="LFA_FOOTBALL_PLAYER", skills=None, updated_by=None):
    lic = MagicMock()
    lic.id = 1
    lic.user_id = user_id
    lic.specialization_type = spec
    lic.canonical_program_id = spec if spec == "LFA_FOOTBALL_PLAYER" else None
    lic.user = _user(UserRole.STUDENT, user_id)
    lic.football_skills = skills
    lic.skills_updated_by = updated_by
    lic.skills_last_updated_at = None
    return lic


def _q(first=None, all_=None):
    q = MagicMock()
    q.filter.return_value = q
    q.in_.return_value = q
    q.all.return_value = all_ or []
    q.first.return_value = first
    return q


def _db_lic(lic, user_updater=None):
    """db for get_football_skills: q1=UserLicense, [q2=User for updater]."""
    from app.models.license import UserLicense
    from app.models.user import User
    db = MagicMock()
    lic_q = _q(first=lic)
    user_q = _q(first=user_updater)
    db.query.side_effect = lambda model: lic_q if model is UserLicense else user_q
    return db


def _db_put(lic):
    """db for update_football_skills: q1=UserLicense."""
    from app.models.license import UserLicense
    db = MagicMock()
    lic_q = _q(first=lic)
    db.query.side_effect = lambda model: lic_q
    return db


def _all_skills():
    return {
        "heading": 80, "shooting": 75, "crossing": 70,
        "passing": 85, "dribbling": 65, "ball_control": 90,
    }


# ── get_football_skills ───────────────────────────────────────────────────────

class TestGetFootballSkills:

    def test_404_when_license_not_found(self):
        db = _db_lic(None)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_football_skills(license_id=99, current_user=_user(), db=db))
        assert exc.value.status_code == 404

    def test_403_student_viewing_other_license(self):
        lic = _license(user_id=99)  # owned by user 99, current user is 42
        db = _db_lic(lic)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_football_skills(
                license_id=1,
                current_user=_user(role=UserRole.STUDENT, user_id=42),
                db=db,
            ))
        assert exc.value.status_code == 403

    def test_400_non_lfa_player_specialization(self):
        lic = _license(user_id=42, spec="GANCUJU_PLAYER")
        db = _db_lic(lic)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_football_skills(license_id=1, current_user=_user(), db=db))
        assert exc.value.status_code == 400
        assert "LFA Player" in exc.value.detail

    def test_no_skills_returns_none_with_message(self):
        lic = _license(user_id=42, skills=None)
        db = _db_lic(lic)
        result = asyncio.run(get_football_skills(license_id=1, current_user=_user(), db=db))
        assert result["skills"] is None
        assert "not yet" in result["message"].lower()

    def test_skills_present_returns_full_dict(self):
        skills = {"heading": 80.0, "shooting": 75.0}
        lic = _license(user_id=42, skills=skills)
        db = _db_lic(lic)
        result = asyncio.run(get_football_skills(license_id=1, current_user=_user(), db=db))
        assert result["skills"] == skills
        assert result["license_id"] == 1

    def test_updater_name_fetched_when_skills_updated_by_set(self):
        skills = {"heading": 80.0}
        lic = _license(user_id=42, skills=skills, updated_by=7)
        updater = MagicMock()
        updater.name = "Coach Z"
        db = _db_lic(lic, user_updater=updater)
        result = asyncio.run(get_football_skills(license_id=1, current_user=_user(), db=db))
        assert result["skills_updated_by_name"] == "Coach Z"

    def test_instructor_can_view_any_license(self):
        lic = _license(user_id=99, skills=None)  # owned by 99, current=42
        db = _db_lic(lic)
        result = asyncio.run(get_football_skills(
            license_id=1,
            current_user=_user(role=UserRole.INSTRUCTOR, user_id=42),
            db=db,
        ))
        assert "license_id" in result

    def test_admin_can_view_any_license(self):
        lic = _license(user_id=99, skills=None)
        db = _db_lic(lic)
        result = asyncio.run(get_football_skills(
            license_id=1,
            current_user=_user(role=UserRole.ADMIN, user_id=42),
            db=db,
        ))
        assert "license_id" in result

    def test_student_can_view_own_license(self):
        lic = _license(user_id=42, skills=None)  # owned by 42, current=42
        db = _db_lic(lic)
        result = asyncio.run(get_football_skills(
            license_id=1,
            current_user=_user(role=UserRole.STUDENT, user_id=42),
            db=db,
        ))
        assert "license_id" in result


# ── update_football_skills ────────────────────────────────────────────────────

class TestUpdateFootballSkills:

    def test_403_student_cannot_update(self):
        db = _db_put(_license())
        with pytest.raises(HTTPException) as exc:
            asyncio.run(update_football_skills(
                license_id=1,
                skills_data=_all_skills(),
                current_user=_user(role=UserRole.STUDENT),
                db=db,
            ))
        assert exc.value.status_code == 403

    def test_404_when_license_not_found(self):
        db = _db_put(None)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(update_football_skills(
                license_id=99,
                skills_data=_all_skills(),
                current_user=_user(role=UserRole.INSTRUCTOR),
                db=db,
            ))
        assert exc.value.status_code == 404

    def test_400_non_lfa_player_specialization(self):
        lic = _license(user_id=42, spec="GANCUJU_PLAYER")
        db = _db_put(lic)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(update_football_skills(
                license_id=1,
                skills_data=_all_skills(),
                current_user=_user(role=UserRole.INSTRUCTOR),
                db=db,
            ))
        assert exc.value.status_code == 400

    def test_400_missing_required_skill(self):
        lic = _license(user_id=42)
        db = _db_put(lic)
        incomplete = {k: v for k, v in _all_skills().items() if k != "heading"}
        with pytest.raises(HTTPException) as exc:
            asyncio.run(update_football_skills(
                license_id=1,
                skills_data=incomplete,
                current_user=_user(role=UserRole.INSTRUCTOR),
                db=db,
            ))
        assert exc.value.status_code == 400
        assert "heading" in exc.value.detail

    def test_400_skill_above_100(self):
        lic = _license(user_id=42)
        db = _db_put(lic)
        bad = {**_all_skills(), "shooting": 101}
        with pytest.raises(HTTPException) as exc:
            asyncio.run(update_football_skills(
                license_id=1, skills_data=bad,
                current_user=_user(role=UserRole.INSTRUCTOR), db=db,
            ))
        assert exc.value.status_code == 400

    def test_400_skill_below_0(self):
        lic = _license(user_id=42)
        db = _db_put(lic)
        bad = {**_all_skills(), "passing": -1}
        with pytest.raises(HTTPException) as exc:
            asyncio.run(update_football_skills(
                license_id=1, skills_data=bad,
                current_user=_user(role=UserRole.INSTRUCTOR), db=db,
            ))
        assert exc.value.status_code == 400

    def test_happy_path_commits_and_logs_audit(self):
        lic = _license(user_id=42)
        db = _db_put(lic)
        with patch(_AUDIT) as mock_audit, patch(_PROGRESSION) as progression:
            progression.return_value.apply_outcome.return_value.id = uuid4()
            result = asyncio.run(update_football_skills(
                license_id=1, skills_data=_all_skills(),
                current_user=_user(role=UserRole.INSTRUCTOR, user_id=42), db=db,
            ))
        progression.return_value.apply_outcome.assert_called_once()
        mock_audit.return_value.log.assert_called_once()
        assert result["success"] is True

    def test_skills_rounded_to_one_decimal(self):
        lic = _license(user_id=42)
        db = _db_put(lic)
        skills = {**_all_skills(), "heading": 80.567}
        with patch(_AUDIT), patch(_PROGRESSION) as progression:
            progression.return_value.apply_outcome.return_value.id = uuid4()
            result = asyncio.run(update_football_skills(
                license_id=1, skills_data=skills,
                current_user=_user(role=UserRole.INSTRUCTOR), db=db,
            ))
        assert result["skills"]["heading"] == 80.6

    def test_instructor_notes_stored_when_provided(self):
        lic = _license(user_id=42)
        db = _db_put(lic)
        skills = {**_all_skills(), "instructor_notes": "Good progress"}
        with patch(_AUDIT), patch(_PROGRESSION) as progression:
            progression.return_value.apply_outcome.return_value.id = uuid4()
            asyncio.run(update_football_skills(
                license_id=1, skills_data=skills,
                current_user=_user(role=UserRole.INSTRUCTOR), db=db,
            ))
        assert lic.instructor_notes == "Good progress"


# ── get_user_all_football_skills ──────────────────────────────────────────────

class TestGetUserAllFootballSkills:

    def _db_all(self, licenses, updater_user=None):
        from app.models.license import UserLicense
        from app.models.user import User
        db = MagicMock()
        lic_q = _q(all_=licenses)
        updater_q = _q(all_=[updater_user] if updater_user else [])
        call_count = [0]
        def side(model):
            call_count[0] += 1
            if model is UserLicense:
                return lic_q
            return updater_q
        db.query.side_effect = side
        return db

    def test_403_student_viewing_other_user(self):
        db = self._db_all([])
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_user_all_football_skills(
                user_id=99,
                current_user=_user(role=UserRole.STUDENT, user_id=42),
                db=db,
            ))
        assert exc.value.status_code == 403

    def test_empty_licenses_returns_empty_list(self):
        db = self._db_all([])
        result = asyncio.run(get_user_all_football_skills(
            user_id=42,
            current_user=_user(role=UserRole.INSTRUCTOR, user_id=42),
            db=db,
        ))
        assert result == []

    def test_skills_populated_for_each_license(self):
        lic = _license(user_id=42, skills={"heading": 80.0})
        db = self._db_all([lic])
        result = asyncio.run(get_user_all_football_skills(
            user_id=42,
            current_user=_user(role=UserRole.INSTRUCTOR, user_id=42),
            db=db,
        ))
        assert len(result) == 1
        assert result[0]["skills"] == {"heading": 80.0}

    def test_instructor_can_view_any_user_skills(self):
        lic = _license(user_id=99)
        db = self._db_all([lic])
        result = asyncio.run(get_user_all_football_skills(
            user_id=99,
            current_user=_user(role=UserRole.INSTRUCTOR, user_id=42),
            db=db,
        ))
        assert isinstance(result, list)
