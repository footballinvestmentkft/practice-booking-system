"""
Unit tests for app/api/api_v1/endpoints/licenses/skills.py
Covers: get_football_skills, update_football_skills, get_user_all_football_skills
Note: all endpoints are async → use asyncio.run()
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch

from app.api.api_v1.endpoints.licenses.skills import (
    get_football_skills,
    update_football_skills,
    get_user_all_football_skills,
)
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.licenses.skills"

VALID_SKILLS = {
    "heading": 70,
    "shooting": 65,
    "crossing": 55,
    "passing": 80,
    "dribbling": 75,
    "ball_control": 60,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(first_val=None, all_val=None):
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = all_val if all_val is not None else []
    q.first.return_value = first_val
    return q


def _user(uid=42, role=UserRole.STUDENT):
    u = MagicMock()
    u.id = uid
    u.name = "Test User"
    u.role = role
    return u


def _instructor(uid=42):
    return _user(uid=uid, role=UserRole.INSTRUCTOR)


def _admin():
    return _user(uid=42, role=UserRole.ADMIN)


def _license(lid=10, user_id=42, spec="LFA_PLAYER_PRE"):
    lic = MagicMock()
    lic.id = lid
    lic.user_id = user_id
    lic.specialization_type = spec
    lic.canonical_program_id = (
        "LFA_FOOTBALL_PLAYER"
        if spec == "LFA_FOOTBALL_PLAYER" or spec.startswith("LFA_PLAYER")
        else None
    )
    lic.football_skills = None
    lic.skills_updated_by = None
    lic.skills_last_updated_at = None
    return lic


# ---------------------------------------------------------------------------
# get_football_skills
# ---------------------------------------------------------------------------

class TestGetFootballSkills:
    def _call(self, license_id=10, db=None, current_user=None):
        return asyncio.run(get_football_skills(
            license_id=license_id,
            current_user=current_user or _user(uid=42),
            db=db or MagicMock(),
        ))

    def test_gfs01_not_found_404(self):
        """GFS-01: license not found → 404."""
        from fastapi import HTTPException
        q = _q(first_val=None)
        db = MagicMock(); db.query.return_value = q
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_gfs02_not_own_student_403(self):
        """GFS-02: student viewing other's license → 403."""
        from fastapi import HTTPException
        lic = _license(user_id=99)
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, current_user=_user(uid=42))
        assert exc.value.status_code == 403

    def test_gfs03_not_lfa_player_400(self):
        """GFS-03: non-LFA_PLAYER_ specialization → 400."""
        from fastapi import HTTPException
        lic = _license(user_id=42, spec="COACH")
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_gfs04_no_skills_returns_null(self):
        """GFS-04: license found, no skills → skills=None."""
        lic = _license(user_id=42, spec="LFA_PLAYER_PRE")
        lic.football_skills = None
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        result = self._call(db=db)
        assert result["skills"] is None
        assert result["message"] == "Skills not yet assessed"

    def test_gfs05_skills_no_updater(self):
        """GFS-05: skills set, no updater → updated_by_name=None."""
        lic = _license(user_id=42, spec="LFA_PLAYER_PRE")
        lic.football_skills = VALID_SKILLS
        lic.skills_updated_by = None
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        result = self._call(db=db)
        assert result["skills"] == VALID_SKILLS
        assert result["skills_updated_by_name"] is None

    def test_gfs06_skills_with_updater_found(self):
        """GFS-06: updater found in DB → name returned."""
        lic = _license(user_id=42, spec="LFA_PLAYER_PRE")
        lic.football_skills = VALID_SKILLS
        lic.skills_updated_by = 99
        updater = MagicMock(); updater.name = "Coach Toth"
        call_n = [0]
        def qside(*args):
            n = call_n[0]; call_n[0] += 1
            return _q(first_val=lic) if n == 0 else _q(first_val=updater)
        db = MagicMock(); db.query.side_effect = qside
        result = self._call(db=db)
        assert result["skills_updated_by_name"] == "Coach Toth"

    def test_gfs07_updater_not_found_name_none(self):
        """GFS-07: updater id set but not in DB → name=None."""
        lic = _license(user_id=42, spec="LFA_PLAYER_PRE")
        lic.football_skills = VALID_SKILLS
        lic.skills_updated_by = 99
        call_n = [0]
        def qside(*args):
            n = call_n[0]; call_n[0] += 1
            return _q(first_val=lic) if n == 0 else _q(first_val=None)
        db = MagicMock(); db.query.side_effect = qside
        result = self._call(db=db)
        assert result["skills_updated_by_name"] is None

    def test_gfs08_instructor_can_view_other(self):
        """GFS-08: instructor viewing other's license → allowed."""
        lic = _license(user_id=99, spec="LFA_PLAYER_PRE")
        lic.football_skills = None
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        result = self._call(db=db, current_user=_instructor(uid=42))
        assert "skills" in result


# ---------------------------------------------------------------------------
# update_football_skills
# ---------------------------------------------------------------------------

class TestUpdateFootballSkills:
    def _call(self, license_id=10, skills_data=None, db=None, current_user=None):
        return asyncio.run(update_football_skills(
            license_id=license_id,
            skills_data=skills_data or dict(VALID_SKILLS),
            current_user=current_user or _instructor(),
            db=db or MagicMock(),
        ))

    def test_ufs01_student_403(self):
        """UFS-01: student role → 403."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(current_user=_user(uid=42, role=UserRole.STUDENT))
        assert exc.value.status_code == 403

    def test_ufs02_license_not_found_404(self):
        """UFS-02: license not found → 404."""
        from fastapi import HTTPException
        q = _q(first_val=None)
        db = MagicMock(); db.query.return_value = q
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_ufs03_not_lfa_player_400(self):
        """UFS-03: non-LFA_PLAYER_ spec → 400."""
        from fastapi import HTTPException
        lic = _license(spec="COACH")
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_ufs04_missing_skill_400(self):
        """UFS-04: missing required skill → 400."""
        from fastapi import HTTPException
        lic = _license(spec="LFA_FOOTBALL_PLAYER")
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        incomplete = dict(VALID_SKILLS)
        del incomplete["heading"]
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, skills_data=incomplete)
        assert exc.value.status_code == 400

    def test_ufs05_skill_out_of_range_400(self):
        """UFS-05: skill value > 100 → 400."""
        from fastapi import HTTPException
        lic = _license(spec="LFA_FOOTBALL_PLAYER")
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        bad = dict(VALID_SKILLS); bad["heading"] = 150
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, skills_data=bad)
        assert exc.value.status_code == 400

    def test_ufs06_negative_skill_value_400(self):
        """UFS-06: skill value < 0 → 400."""
        from fastapi import HTTPException
        lic = _license(spec="LFA_FOOTBALL_PLAYER")
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        bad = dict(VALID_SKILLS); bad["shooting"] = -5
        with pytest.raises(HTTPException) as exc:
            self._call(db=db, skills_data=bad)
        assert exc.value.status_code == 400

    def test_ufs07_success_no_notes(self):
        """UFS-07: success, no instructor_notes → committed."""
        lic = _license(spec="LFA_FOOTBALL_PLAYER")
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        with patch(f"{_BASE}.AuditService") as MockAudit, \
             patch(f"{_BASE}.PlayerProgressionService") as MockProgression:
            MockAudit.return_value.log.side_effect = lambda **_: db.commit()
            MockProgression.return_value.apply_outcome.return_value.id = "outcome-1"
            result = self._call(db=db)
        db.commit.assert_called_once()
        assert result["success"] is True
        assert "heading" in result["skills"]

    def test_ufs08_success_with_notes(self):
        """UFS-08: instructor_notes in payload → license.instructor_notes set."""
        lic = _license(spec="LFA_FOOTBALL_PLAYER")
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        skills_with_notes = dict(VALID_SKILLS)
        skills_with_notes["instructor_notes"] = "Great footwork"
        with patch(f"{_BASE}.AuditService") as MockAudit, \
             patch(f"{_BASE}.PlayerProgressionService") as MockProgression:
            MockAudit.return_value.log.side_effect = lambda **_: db.commit()
            MockProgression.return_value.apply_outcome.return_value.id = "outcome-2"
            result = self._call(db=db, skills_data=skills_with_notes)
        assert lic.instructor_notes == "Great footwork"
        assert result["success"] is True

    def test_ufs09_admin_can_update(self):
        """UFS-09: admin role → allowed."""
        lic = _license(spec="LFA_FOOTBALL_PLAYER")
        q = _q(first_val=lic)
        db = MagicMock(); db.query.return_value = q
        with patch(f"{_BASE}.AuditService") as MockAudit, \
             patch(f"{_BASE}.PlayerProgressionService") as MockProgression:
            MockAudit.return_value.log.side_effect = lambda **_: db.commit()
            MockProgression.return_value.apply_outcome.return_value.id = "outcome-3"
            result = self._call(db=db, current_user=_admin())
        assert result["success"] is True


# ---------------------------------------------------------------------------
# get_user_all_football_skills
# ---------------------------------------------------------------------------

class TestGetUserAllFootballSkills:
    def _call(self, user_id=42, db=None, current_user=None):
        return asyncio.run(get_user_all_football_skills(
            user_id=user_id,
            current_user=current_user or _user(uid=42),
            db=db or MagicMock(),
        ))

    def test_guafs01_not_own_student_403(self):
        """GUAFS-01: student viewing other's skills → 403."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._call(user_id=99, current_user=_user(uid=42))
        assert exc.value.status_code == 403

    def test_guafs02_own_no_licenses(self):
        """GUAFS-02: own, no LFA_PLAYER licenses → []."""
        call_n = [0]
        def qside(*args):
            n = call_n[0]; call_n[0] += 1
            q = MagicMock()
            q.filter.return_value = q
            q.all.return_value = []
            return q
        db = MagicMock(); db.query.side_effect = qside
        result = self._call(db=db)
        assert result == []

    def test_guafs03_with_licenses_no_updater(self):
        """GUAFS-03: licenses found, no skills_updated_by → name=None."""
        lic = _license(user_id=42, spec="LFA_PLAYER_PRE")
        lic.skills_updated_by = None
        lic.skills_last_updated_at = None
        call_n = [0]
        def qside(*args):
            n = call_n[0]; call_n[0] += 1
            q = MagicMock()
            q.filter.return_value = q
            q.all.return_value = [lic] if n == 0 else []
            return q
        db = MagicMock(); db.query.side_effect = qside
        result = self._call(db=db)
        assert len(result) == 1
        assert result[0]["skills_updated_by_name"] is None

    def test_guafs04_with_updater_batch_fetched(self):
        """GUAFS-04: skills_updated_by set → updater name in result."""
        lic = _license(user_id=42, spec="LFA_PLAYER_PRE")
        lic.skills_updated_by = 99
        lic.skills_last_updated_at = None
        updater = MagicMock(); updater.id = 99; updater.name = "Coach"
        call_n = [0]
        def qside(*args):
            n = call_n[0]; call_n[0] += 1
            q = MagicMock()
            q.filter.return_value = q
            q.all.return_value = [lic] if n == 0 else [updater]
            return q
        db = MagicMock(); db.query.side_effect = qside
        result = self._call(db=db)
        assert result[0]["skills_updated_by_name"] == "Coach"

    def test_guafs05_instructor_views_other(self):
        """GUAFS-05: instructor viewing other user → allowed."""
        call_n = [0]
        def qside(*args):
            n = call_n[0]; call_n[0] += 1
            q = MagicMock()
            q.filter.return_value = q
            q.all.return_value = []
            return q
        db = MagicMock(); db.query.side_effect = qside
        result = self._call(user_id=99, current_user=_instructor(uid=42), db=db)
        assert result == []
