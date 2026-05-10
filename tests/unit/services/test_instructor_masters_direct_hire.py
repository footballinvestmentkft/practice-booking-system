"""
Unit tests for app/api/api_v1/endpoints/instructor_management/masters/direct_hire.py
Covers: create_direct_hire_offer
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import date

from app.api.api_v1.endpoints.instructor_management.masters.direct_hire import (
    create_direct_hire_offer,
)
from app.models.user import UserRole
from app.models.specialization import SpecializationType

_BASE = "app.api.api_v1.endpoints.instructor_management.masters.direct_hire"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(first_val=None, all_val=None):
    q = MagicMock()
    q.filter.return_value = q
    q.in_.return_value = q
    q.all.return_value = all_val if all_val is not None else []
    q.first.return_value = first_val
    return q


def _seq_db(*vals):
    call_n = [0]
    db = MagicMock()

    def side(*args):
        n = call_n[0]
        call_n[0] += 1
        v = vals[n] if n < len(vals) else None
        q = _q()
        if isinstance(v, list):
            q.all.return_value = v
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


def _location(lid=1):
    loc = MagicMock()
    loc.id = lid
    loc.name = "Budapest"
    loc.city = "Budapest"
    return loc


def _instructor(uid=7, specialization=SpecializationType.LFA_COACH):
    u = MagicMock()
    u.id = uid
    u.name = "Test Instructor"
    u.email = "instructor@example.com"
    u.specialization = specialization
    return u


def _hire_data(location_id=1, instructor_id=7, override=False, deadline_days=14):
    d = MagicMock()
    d.location_id = location_id
    d.instructor_id = instructor_id
    d.contract_start = MagicMock()
    d.contract_start.year = 2026
    d.contract_end = MagicMock()
    d.override_availability = override
    d.offer_deadline_days = deadline_days
    return d


def _avail_result(match_score=80, warnings=None):
    r = MagicMock()
    r.match_score = match_score
    r.warnings = warnings or []
    r.instructor_availability = []
    r.contract_coverage = []
    return r


def _permissions(can_teach=True, age_group="YOUTH_FOOTBALL", level=4):
    return {
        "can_teach_independently": can_teach,
        "age_group": age_group,
        "current_level": level,
    }


# ---------------------------------------------------------------------------
# create_direct_hire_offer
# ---------------------------------------------------------------------------

class TestCreateDirectHireOffer:
    def _call(self, data=None, db=None, current_user=None):
        return create_direct_hire_offer(
            data=data or _hire_data(),
            db=db or MagicMock(),
            current_user=current_user or _admin(),
        )

    def test_location_not_found_404(self):
        """CDHO-01: location not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_instructor_not_found_404(self):
        """CDHO-02: instructor not found → 404."""
        from fastapi import HTTPException
        db = _seq_db(_location(), None)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 404

    def test_location_has_active_master_400(self):
        """CDHO-03: location already has active master → 400."""
        from fastapi import HTTPException
        instr = _instructor()
        existing = MagicMock()  # existing active master
        db = _seq_db(_location(), instr, existing)
        with pytest.raises(HTTPException) as exc:
            self._call(db=db)
        assert exc.value.status_code == 400

    def test_instructor_already_master_elsewhere_400(self):
        """CDHO-04: instructor already master elsewhere → 400."""
        from fastapi import HTTPException
        db = _seq_db(_location(), _instructor(), None)
        with patch(f"{_BASE}.check_instructor_has_active_master_position", return_value=True):
            with patch(f"{_BASE}.get_instructor_active_master_location", return_value="Győr"):
                with pytest.raises(HTTPException) as exc:
                    self._call(db=db)
        assert exc.value.status_code == 400

    def test_poor_availability_no_override_400(self):
        """CDHO-05: availability match <50 without override → 400."""
        from fastapi import HTTPException
        db = _seq_db(_location(), _instructor(), None)
        with patch(f"{_BASE}.check_instructor_has_active_master_position", return_value=False):
            with patch(f"{_BASE}.check_availability_match", return_value=_avail_result(match_score=30)):
                with pytest.raises(HTTPException) as exc:
                    self._call(db=db, data=_hire_data(override=False))
        assert exc.value.status_code == 400

    def test_poor_availability_with_override_continues(self):
        """CDHO-06: availability match <50 but override=True → does NOT block.
        Also patches Semester (production bug: Semester.location_city attr missing)."""
        from fastapi import HTTPException
        instr = _instructor()
        db = _seq_db(_location(), instr, None, [])  # semesters=[]
        with patch(f"{_BASE}.check_instructor_has_active_master_position", return_value=False):
            with patch(f"{_BASE}.check_availability_match", return_value=_avail_result(match_score=30)):
                with patch(f"{_BASE}.Semester") as MockSemester:
                    MockSemester.location_city = MagicMock()
                    MockSemester.status = MagicMock()
                    MockSemester.status.in_.return_value = MagicMock()
                    with patch(f"{_BASE}.TeachingPermissionService") as MockTPS:
                        MockTPS.get_teaching_permissions.return_value = _permissions()
                        with patch(f"{_BASE}.LocationMasterInstructor") as MockMaster:
                            MockMaster.return_value = MagicMock()
                            with patch(f"{_BASE}.MasterOfferResponse") as MockResp:
                                MockResp.return_value = MagicMock()
                                # Should not raise
                                self._call(db=db, data=_hire_data(override=True))

    def test_no_specialization_400(self):
        """CDHO-07: instructor has no specialization → 400."""
        from fastapi import HTTPException
        instr = _instructor(specialization=None)
        db = _seq_db(_location(), instr, None)
        with patch(f"{_BASE}.check_instructor_has_active_master_position", return_value=False):
            with patch(f"{_BASE}.check_availability_match", return_value=_avail_result(match_score=90)):
                with pytest.raises(HTTPException) as exc:
                    self._call(db=db)
        assert exc.value.status_code == 400

    def test_wrong_specialization_400(self):
        """CDHO-08: instructor specialization != LFA_COACH → 400."""
        from fastapi import HTTPException
        instr = _instructor(specialization=SpecializationType.LFA_FOOTBALL_PLAYER)
        db = _seq_db(_location(), instr, None)
        with patch(f"{_BASE}.check_instructor_has_active_master_position", return_value=False):
            with patch(f"{_BASE}.check_availability_match", return_value=_avail_result(match_score=90)):
                with pytest.raises(HTTPException) as exc:
                    self._call(db=db)
        assert exc.value.status_code == 400

    def test_cannot_teach_independently_400(self):
        """CDHO-09: instructor is assistant coach (can_teach_independently=False) → 400."""
        from fastapi import HTTPException
        instr = _instructor()
        db = _seq_db(_location(), instr, None)
        with patch(f"{_BASE}.check_instructor_has_active_master_position", return_value=False):
            with patch(f"{_BASE}.check_availability_match", return_value=_avail_result(match_score=90)):
                with patch(f"{_BASE}.LicenseValidator"):
                    with patch(f"{_BASE}.TeachingPermissionService") as MockTPS:
                        MockTPS.get_teaching_permissions.return_value = _permissions(can_teach=False)
                        with pytest.raises(HTTPException) as exc:
                            self._call(db=db)
        assert exc.value.status_code == 400

    def test_incompatible_semester_400(self):
        """CDHO-10: semester incompatible with instructor age group → 400.
        Patches Semester (production bug: Semester.location_city missing)."""
        from fastapi import HTTPException
        instr = _instructor()
        semester = MagicMock()
        semester.id = 1
        semester.code = "SEM-2026"
        db = _seq_db(_location(), instr, None, [semester])
        with patch(f"{_BASE}.check_instructor_has_active_master_position", return_value=False):
            with patch(f"{_BASE}.check_availability_match", return_value=_avail_result(match_score=90)):
                with patch(f"{_BASE}.LicenseValidator"):
                    with patch(f"{_BASE}.Semester") as MockSem:
                        MockSem.location_city = MagicMock()
                        MockSem.status = MagicMock()
                        with patch(f"{_BASE}.TeachingPermissionService") as MockTPS:
                            MockTPS.get_teaching_permissions.return_value = _permissions(age_group="YOUTH_FOOTBALL")
                            with patch(f"{_BASE}.get_semester_age_group", return_value="ADULT_FOOTBALL"):
                                with patch(f"{_BASE}.can_teach_age_group", return_value=False):
                                    with patch(f"{_BASE}.get_allowed_age_groups", return_value=["YOUTH_FOOTBALL"]):
                                        with pytest.raises(HTTPException) as exc:
                                            self._call(db=db)
        assert exc.value.status_code == 400

    def test_success_no_semesters(self):
        """CDHO-11: all checks pass, no location semesters → offer created."""
        instr = _instructor()
        mock_master = MagicMock()
        mock_master.id = 100
        mock_master.location_id = 1
        mock_master.instructor_id = 7
        db = _seq_db(_location(), instr, None, [])  # semesters=[]

        with patch(f"{_BASE}.check_instructor_has_active_master_position", return_value=False):
            with patch(f"{_BASE}.check_availability_match", return_value=_avail_result(match_score=90)):
                with patch(f"{_BASE}.Semester") as MockSem:
                    MockSem.location_city = MagicMock()
                    MockSem.status = MagicMock()
                    with patch(f"{_BASE}.TeachingPermissionService") as MockTPS:
                        MockTPS.get_teaching_permissions.return_value = _permissions()
                        with patch(f"{_BASE}.LocationMasterInstructor", return_value=mock_master):
                            with patch(f"{_BASE}.MasterOfferResponse") as MockResp:
                                MockResp.return_value = MagicMock()
                                result = self._call(db=db)
        db.add.assert_called_once_with(mock_master)
        db.commit.assert_called_once()

    def test_success_compatible_semesters(self):
        """CDHO-12: compatible semesters → no error, offer created."""
        instr = _instructor()
        semester = MagicMock()
        mock_master = MagicMock()
        db = _seq_db(_location(), instr, None, [semester])

        with patch(f"{_BASE}.check_instructor_has_active_master_position", return_value=False):
            with patch(f"{_BASE}.check_availability_match", return_value=_avail_result(match_score=90)):
                with patch(f"{_BASE}.Semester") as MockSem:
                    MockSem.location_city = MagicMock()
                    MockSem.status = MagicMock()
                    with patch(f"{_BASE}.TeachingPermissionService") as MockTPS:
                        MockTPS.get_teaching_permissions.return_value = _permissions(age_group="YOUTH_FOOTBALL")
                        with patch(f"{_BASE}.get_semester_age_group", return_value="YOUTH_FOOTBALL"):
                            with patch(f"{_BASE}.can_teach_age_group", return_value=True):
                                with patch(f"{_BASE}.LocationMasterInstructor", return_value=mock_master):
                                    with patch(f"{_BASE}.MasterOfferResponse") as MockResp:
                                        MockResp.return_value = MagicMock()
                                        self._call(db=db)
        db.add.assert_called_once_with(mock_master)
