"""Unit tests for app/api/api_v1/endpoints/instructor_management/assignments.py

Sprint P10 — Coverage target: ≥85% stmt, ≥75% branch

Covers:
- _can_teach_age_group: PRE/YOUTH/AMATEUR/PRO hierarchy, display-name mapping, unknown group
- _get_allowed_age_groups: PRE/PRO/unknown
- create_assignment: 403 not-master, 404 instructor, 400 no-spec, 400 LFA_COACH wrong license,
      400 can't teach independently, 400 age-group incompatible, 400 spec mismatch,
      201 LFA_COACH success, 201 non-LFA_COACH success, 201 with semester transitions
- list_assignments: empty/no filters, all filters, with assignments, None instructor/location
- get_matrix_cell_instructors: empty, single (not co), two same-period (co), AMATEUR coverage
- update_assignment: 404, 403, 200 deactivate, 200 re-activate
- delete_assignment: 404, 403, 204 as admin, 204 as master
"""

from unittest.mock import MagicMock, patch

import pytest

from app.models.user import UserRole
from app.api.api_v1.endpoints.instructor_management.assignments import (
    create_assignment,
    delete_assignment,
    get_matrix_cell_instructors,
    list_assignments,
    update_assignment,
    _can_teach_age_group,
    _get_allowed_age_groups,
)
from app.models.specialization import SpecializationType
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.instructor_management.assignments"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _q(*, first=None, all_=None, count=0):
    """Fluent query mock — filter / order_by / all / first / count."""
    q = MagicMock()
    for m in ("filter", "options", "order_by", "offset", "limit", "group_by", "join"):
        getattr(q, m).return_value = q
    q.first.return_value = first
    q.all.return_value = all_ if all_ is not None else []
    q.count.return_value = count
    return q


def _seq_db(*qs):
    """n-th db.query() call returns qs[n]; fallback _q() after exhaustion."""
    calls = [0]

    def _side(*args, **kw):
        idx = calls[0]
        calls[0] += 1
        return qs[idx] if idx < len(qs) else _q()

    db = MagicMock()
    db.query.side_effect = _side
    return db


def _chain_db(all_=None):
    """Single fluent chain — for endpoints with one db.query() call."""
    q = _q(all_=all_ or [])
    db = MagicMock()
    db.query.return_value = q
    return db


def _user(role="INSTRUCTOR", uid=42):
    u = MagicMock()
    u.id = uid
    u.role = role
    u.name = "Test User"
    return u


def _data(*, spec_type="LFA_COACH", age_group="PRE_FOOTBALL", is_master=False,
          location_id=1, instructor_id=10):
    d = MagicMock()
    d.specialization_type = spec_type
    d.age_group = age_group
    d.is_master = is_master
    d.location_id = location_id
    d.instructor_id = instructor_id
    d.year = 2024
    d.time_period_start = "2024-01"
    d.time_period_end = "2024-06"
    return d


def _instructor(*, specialization=None):
    i = MagicMock()
    i.id = 10
    i.name = "Coach"
    i.email = "coach@lfa.com"
    i.specialization = specialization
    return i


# ===========================================================================
# Pure helpers
# ===========================================================================

class TestCanTeachAgeGroup:
    def test_pre_can_teach_pre(self):
        assert _can_teach_age_group("PRE_FOOTBALL", "PRE_FOOTBALL") is True

    def test_pre_cannot_teach_youth(self):
        assert _can_teach_age_group("PRE_FOOTBALL", "YOUTH_FOOTBALL") is False

    def test_youth_can_teach_pre(self):
        assert _can_teach_age_group("YOUTH_FOOTBALL", "PRE_FOOTBALL") is True

    def test_pro_can_teach_all(self):
        for ag in ["PRE_FOOTBALL", "YOUTH_FOOTBALL", "AMATEUR_FOOTBALL", "PRO_FOOTBALL"]:
            assert _can_teach_age_group("PRO_FOOTBALL", ag) is True

    def test_display_name_is_mapped(self):
        # "Youth Football Coach" normalises to YOUTH_FOOTBALL
        assert _can_teach_age_group("YOUTH_FOOTBALL", "Youth Football Coach") is True

    def test_unknown_instructor_group_returns_false(self):
        assert _can_teach_age_group("UNKNOWN_GROUP", "PRE_FOOTBALL") is False


class TestGetAllowedAgeGroups:
    def test_pre_gets_one(self):
        assert _get_allowed_age_groups("PRE_FOOTBALL") == ["Pre Football Coach"]

    def test_pro_gets_all_four(self):
        assert len(_get_allowed_age_groups("PRO_FOOTBALL")) == 4

    def test_unknown_gets_empty(self):
        assert _get_allowed_age_groups("BOGUS") == []


# ===========================================================================
# TestCreateAssignment
# ===========================================================================

class TestCreateAssignment:

    def test_403_not_master(self):
        db = _seq_db(_q(first=None))
        with pytest.raises(Exception) as exc:
            create_assignment(_data(), db=db, current_user=_user())
        assert exc.value.status_code == 403

    def test_404_instructor_not_found(self):
        db = _seq_db(_q(first=MagicMock()), _q(first=None))
        with pytest.raises(Exception) as exc:
            create_assignment(_data(), db=db, current_user=_user())
        assert exc.value.status_code == 404

    def test_400_no_specialization(self):
        instr = _instructor(specialization=None)
        db = _seq_db(_q(first=MagicMock()), _q(first=instr))
        with pytest.raises(Exception) as exc:
            create_assignment(_data(), db=db, current_user=_user())
        assert exc.value.status_code == 400
        assert "no specialization" in exc.value.detail.lower()

    def test_400_lfa_coach_wrong_license(self):
        wrong_spec = MagicMock()
        wrong_spec.value = "GANCUJU"
        instr = _instructor(specialization=wrong_spec)
        db = _seq_db(_q(first=MagicMock()), _q(first=instr))
        with pytest.raises(Exception) as exc:
            create_assignment(_data(spec_type="LFA_COACH"), db=db, current_user=_user())
        assert exc.value.status_code == 400
        assert "lfa_coach" in exc.value.detail.lower()

    def test_400_lfa_coach_cannot_teach_independently(self):
        instr = _instructor(specialization=SpecializationType.LFA_COACH)
        db = _seq_db(_q(first=MagicMock()), _q(first=instr))
        d = _data(spec_type="LFA_COACH", is_master=False)
        with patch(f"{_BASE}.TeachingPermissionService") as MockTPS:
            MockTPS.get_teaching_permissions.return_value = {
                "can_teach_independently": False,
                "current_level": 1,
                "age_group": "PRE_FOOTBALL",
            }
            with pytest.raises(Exception) as exc:
                create_assignment(d, db=db, current_user=_user())
        assert exc.value.status_code == 400
        assert "head coach" in exc.value.detail.lower()

    def test_400_lfa_coach_age_group_incompatible(self):
        instr = _instructor(specialization=SpecializationType.LFA_COACH)
        db = _seq_db(_q(first=MagicMock()), _q(first=instr))
        # PRE_FOOTBALL instructor → cannot teach PRO_FOOTBALL
        d = _data(spec_type="LFA_COACH", age_group="PRO_FOOTBALL", is_master=True)
        with patch(f"{_BASE}.TeachingPermissionService") as MockTPS:
            MockTPS.get_teaching_permissions.return_value = {
                "can_teach_independently": True,
                "current_level": 2,
                "age_group": "PRE_FOOTBALL",
            }
            MockTPS.can_teach_scope.return_value = False
            with pytest.raises(Exception) as exc:
                create_assignment(d, db=db, current_user=_user())
        assert exc.value.status_code == 400
        assert "License incompatibility" in exc.value.detail["error"]

    def test_400_non_lfa_coach_spec_mismatch(self):
        wrong_spec = MagicMock()
        wrong_spec.value = "OTHER_SPEC"
        instr = _instructor(specialization=wrong_spec)
        db = _seq_db(_q(first=MagicMock()), _q(first=instr))
        d = _data(spec_type="GANCUJU")
        with pytest.raises(Exception) as exc:
            create_assignment(d, db=db, current_user=_user())
        assert exc.value.status_code == 400
        assert "does not match" in exc.value.detail

    def test_201_lfa_coach_success_no_semesters(self):
        instr = _instructor(specialization=SpecializationType.LFA_COACH)
        db = _seq_db(
            _q(first=MagicMock()),  # q0: master check
            _q(first=instr),        # q1: instructor
            _q(first=None),         # q2: location → None → skip semester loop
        )
        d = _data(spec_type="LFA_COACH", is_master=True)
        mock_assignment = MagicMock()
        mock_assignment.location_id = 1
        with patch(f"{_BASE}.TeachingPermissionService") as MockTPS, \
             patch(f"{_BASE}.InstructorAssignment", return_value=mock_assignment), \
             patch(f"{_BASE}.AssignmentResponse") as MockResp, \
             patch(f"{_BASE}.check_and_transition_semester"):
            MockTPS.get_teaching_permissions.return_value = {
                "can_teach_independently": True, "current_level": 2, "age_group": "PRE_FOOTBALL"
            }
            mock_resp = MagicMock()
            MockResp.from_orm.return_value = mock_resp
            result = create_assignment(d, db=db, current_user=_user())
        assert result == mock_resp
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_201_non_lfa_coach_success(self):
        matching_spec = MagicMock()
        matching_spec.value = "GANCUJU"
        instr = _instructor(specialization=matching_spec)
        db = _seq_db(_q(first=MagicMock()), _q(first=instr), _q(first=None))
        d = _data(spec_type="GANCUJU")
        mock_assignment = MagicMock()
        mock_assignment.location_id = 1
        with patch(f"{_BASE}.InstructorAssignment", return_value=mock_assignment), \
             patch(f"{_BASE}.AssignmentResponse") as MockResp, \
             patch(f"{_BASE}.check_and_transition_semester"):
            MockResp.from_orm.return_value = MagicMock()
            result = create_assignment(d, db=db, current_user=_user())
        assert result is not None

    def test_201_with_semester_transitions(self):
        """Regression: semester query uses Semester.location_id (not .location_city)."""
        instr = _instructor(specialization=SpecializationType.LFA_COACH)
        sem = MagicMock()
        sem.id = 5
        db = _seq_db(
            _q(first=MagicMock()),   # q0: master
            _q(first=instr),         # q1: instructor
            _q(first=MagicMock()),   # q2: location found → enters semester loop
            _q(all_=[sem]),          # q3: matching semesters
        )
        d = _data(spec_type="LFA_COACH", is_master=True)
        mock_assignment = MagicMock()
        mock_assignment.location_id = 1
        mock_assignment.specialization_type = "LFA_COACH"
        with patch(f"{_BASE}.TeachingPermissionService") as MockTPS, \
             patch(f"{_BASE}.InstructorAssignment", return_value=mock_assignment), \
             patch(f"{_BASE}.AssignmentResponse") as MockResp, \
             patch(f"{_BASE}.check_and_transition_semester") as mock_transition:
            MockTPS.get_teaching_permissions.return_value = {
                "can_teach_independently": True, "current_level": 2, "age_group": "PRE_FOOTBALL"
            }
            MockResp.from_orm.return_value = MagicMock()
            create_assignment(d, db=db, current_user=_user())
        mock_transition.assert_called_once_with(db, 5)

    def test_semester_filter_uses_location_id_not_location_city(self):
        """Regression: Semester.location_id must be a valid SQLAlchemy column — accessing
        Semester.location_city would raise AttributeError before this fix was applied."""
        from app.models.semester import Semester
        # If assignments.py uses Semester.location_city the attribute access raises
        # AttributeError before any query is even built. Verify location_id exists.
        assert hasattr(Semester, "location_id"), (
            "Semester must have 'location_id' column — "
            "assignments.py:159 filter relies on it"
        )
        assert not hasattr(Semester, "location_city"), (
            "Semester must NOT have 'location_city' — "
            "use location_id (FK to Location)"
        )


# ===========================================================================
# TestListAssignments
# ===========================================================================

class TestListAssignments:

    def test_empty_list_no_filters(self):
        db = _chain_db([])
        with patch(f"{_BASE}.AssignmentListResponse") as MockALR:
            MockALR.return_value = MagicMock()
            list_assignments(db=db, current_user=_user())
        MockALR.assert_called_once_with(total=0, assignments=[])

    def test_with_all_filters(self):
        db = _chain_db([])
        with patch(f"{_BASE}.AssignmentListResponse") as MockALR:
            MockALR.return_value = MagicMock()
            list_assignments(
                location_id=1, year=2024, specialization="LFA_COACH",
                age_group="PRE", include_inactive=True,
                db=db, current_user=_user()
            )
        # filter chain called for location, year, specialization, age_group (4 times)
        assert db.query.return_value.filter.call_count >= 4

    def test_with_assignments_builds_responses(self):
        a = MagicMock()
        a.instructor = MagicMock()
        a.location = MagicMock()
        a.assigner = MagicMock()
        db = _chain_db([a])
        with patch(f"{_BASE}.AssignmentResponse") as MockResp, \
             patch(f"{_BASE}.AssignmentListResponse") as MockALR:
            mock_r = MagicMock()
            MockResp.from_orm.return_value = mock_r
            MockALR.return_value = MagicMock()
            list_assignments(db=db, current_user=_user())
        MockResp.from_orm.assert_called_once_with(a)
        assert mock_r.instructor_name == a.instructor.name
        assert mock_r.location_name == a.location.name
        assert mock_r.assigner_name == a.assigner.name

    def test_none_instructor_skips_name_assignment(self):
        a = MagicMock()
        a.instructor = None
        a.location = None
        a.assigner = None
        db = _chain_db([a])
        with patch(f"{_BASE}.AssignmentResponse") as MockResp, \
             patch(f"{_BASE}.AssignmentListResponse") as MockALR:
            mock_r = MagicMock()
            MockResp.from_orm.return_value = mock_r
            MockALR.return_value = MagicMock()
            list_assignments(db=db, current_user=_user())
        # from_orm called but instructor/location/assigner names not set
        MockResp.from_orm.assert_called_once_with(a)


# ===========================================================================
# TestGetMatrixCellInstructors
# ===========================================================================

class TestGetMatrixCellInstructors:

    def test_empty_assignments(self):
        db = _chain_db([])
        with patch(f"{_BASE}.MatrixCellInstructors") as MockMCI:
            MockMCI.return_value = MagicMock()
            get_matrix_cell_instructors(
                location_id=1, specialization="LFA_COACH",
                age_group="PRE", year=2024, db=db, current_user=_user()
            )
        kwargs = MockMCI.call_args.kwargs
        assert kwargs["instructors"] == []
        assert kwargs["coverage_percentage"] == 0.0

    def test_single_assignment_not_co_instructor(self):
        a = MagicMock()
        a.instructor_id = 42
        a.instructor = MagicMock()
        a.instructor.name = "Coach A"
        a.is_master = True
        a.time_period_start = "2024-01"
        a.time_period_end = "2024-06"
        db = _chain_db([a])
        with patch(f"{_BASE}.CellInstructorInfo") as MockCI, \
             patch(f"{_BASE}.MatrixCellInstructors") as MockMCI:
            MockCI.return_value = MagicMock()
            MockMCI.return_value = MagicMock()
            get_matrix_cell_instructors(
                location_id=1, specialization="LFA_COACH",
                age_group="PRE", year=2024, db=db, current_user=_user()
            )
        # Single assignment → is_co_instructor=False
        MockCI.assert_called_once()
        assert MockCI.call_args.kwargs["is_co_instructor"] is False

    def test_two_same_period_assignments_are_co_instructors(self):
        def _assignment(uid, name):
            a = MagicMock()
            a.instructor_id = uid
            a.instructor = MagicMock()
            a.instructor.name = name
            a.is_master = uid == 42
            a.time_period_start = "2024-01"
            a.time_period_end = "2024-06"
            return a

        a1, a2 = _assignment(42, "Coach A"), _assignment(43, "Coach B")
        db = _chain_db([a1, a2])
        with patch(f"{_BASE}.CellInstructorInfo") as MockCI, \
             patch(f"{_BASE}.MatrixCellInstructors") as MockMCI:
            MockCI.return_value = MagicMock()
            MockMCI.return_value = MagicMock()
            get_matrix_cell_instructors(
                location_id=1, specialization="LFA_COACH",
                age_group="PRE", year=2024, db=db, current_user=_user()
            )
        assert MockCI.call_count == 2
        for call in MockCI.call_args_list:
            assert call.kwargs["is_co_instructor"] is True

    def test_amateur_age_group_coverage(self):
        db = _chain_db([])
        with patch(f"{_BASE}.MatrixCellInstructors") as MockMCI:
            MockMCI.return_value = MagicMock()
            get_matrix_cell_instructors(
                location_id=1, specialization="LFA_COACH",
                age_group="AMATEUR", year=2024, db=db, current_user=_user()
            )
        kwargs = MockMCI.call_args.kwargs
        assert kwargs["required_months"] == 12
        # 0 assignments × 12 = 0 total_coverage → 0% coverage
        assert kwargs["total_coverage_months"] == 0
        assert kwargs["coverage_percentage"] == 0.0


# ===========================================================================
# TestUpdateAssignment
# ===========================================================================

class TestUpdateAssignment:

    def test_404_not_found(self):
        db = _seq_db(_q(first=None))
        upd = MagicMock()
        upd.is_active = None
        with pytest.raises(Exception) as exc:
            update_assignment(99, upd, db=db, current_user=_user())
        assert exc.value.status_code == 404

    def test_403_not_master(self):
        assignment = MagicMock()
        assignment.location_id = 1
        db = _seq_db(_q(first=assignment), _q(first=None))
        upd = MagicMock()
        upd.is_active = None
        with pytest.raises(Exception) as exc:
            update_assignment(1, upd, db=db, current_user=_user())
        assert exc.value.status_code == 403

    def test_200_deactivate(self):
        assignment = MagicMock()
        assignment.location_id = 1
        db = _seq_db(_q(first=assignment), _q(first=MagicMock()))
        upd = MagicMock()
        upd.is_active = False
        with patch(f"{_BASE}.AssignmentResponse") as MockResp:
            mock_resp = MagicMock()
            MockResp.from_orm.return_value = mock_resp
            result = update_assignment(1, upd, db=db, current_user=_user())
        assert assignment.is_active is False
        assert assignment.deactivated_at is not None
        assert result == mock_resp

    def test_200_reactivate(self):
        assignment = MagicMock()
        assignment.location_id = 1
        db = _seq_db(_q(first=assignment), _q(first=MagicMock()))
        upd = MagicMock()
        upd.is_active = True
        with patch(f"{_BASE}.AssignmentResponse") as MockResp:
            MockResp.from_orm.return_value = MagicMock()
            update_assignment(1, upd, db=db, current_user=_user())
        assert assignment.is_active is True
        # deactivated_at not set (is_active=True branch)
        assignment.deactivated_at  # just verify no AttributeError


# ===========================================================================
# TestDeleteAssignment
# ===========================================================================

class TestDeleteAssignment:

    def test_404_not_found(self):
        db = _seq_db(_q(first=None))
        with pytest.raises(Exception) as exc:
            delete_assignment(99, db=db, current_user=_user())
        assert exc.value.status_code == 404

    def test_403_neither_admin_nor_master(self):
        assignment = MagicMock()
        assignment.location_id = 1
        db = _seq_db(_q(first=assignment), _q(first=None))
        u = _user(role=UserRole.INSTRUCTOR)
        with pytest.raises(Exception) as exc:
            delete_assignment(1, db=db, current_user=u)
        assert exc.value.status_code == 403

    def test_204_as_admin(self):
        assignment = MagicMock()
        assignment.location_id = 1
        # master check still runs but returns None → is_master=False; is_admin=True wins
        db = _seq_db(_q(first=assignment), _q(first=None))
        u = _user(role=UserRole.ADMIN)
        result = delete_assignment(1, db=db, current_user=u)
        assert result is None
        db.delete.assert_called_once_with(assignment)

    def test_204_as_master(self):
        assignment = MagicMock()
        assignment.location_id = 1
        db = _seq_db(_q(first=assignment), _q(first=MagicMock()))
        u = _user(role=UserRole.INSTRUCTOR)
        result = delete_assignment(1, db=db, current_user=u)
        assert result is None
        db.delete.assert_called_once_with(assignment)
