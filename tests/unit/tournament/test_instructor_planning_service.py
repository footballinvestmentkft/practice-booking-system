"""
Unit tests for app/services/tournament/instructor_planning_service.py

Covers the master_instructor_id sync behaviour introduced to bridge the gap
between the new TournamentInstructorSlot system and the legacy field read by
status_validator.py.

  IPS-01  add_slot(MASTER) → Semester.master_instructor_id synced
  IPS-02  remove_slot(MASTER) → Semester.master_instructor_id cleared
  IPS-03  add_slot(FIELD) → Semester.master_instructor_id unchanged
"""

import pytest
from unittest.mock import MagicMock, patch, call

from app.models.tournament_instructor_slot import SlotRole, SlotStatus


_BASE = "app.services.tournament.instructor_planning_service"


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_db(tournament=None, instructor=None, slot=None):
    """Build a minimal DB mock for instructor_planning_service calls."""
    db = MagicMock()

    # query() chains
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = None
    q.count.return_value = 0

    def _query_side_effect(model):
        return q

    db.query.side_effect = _query_side_effect

    # Patch specific return values
    if tournament is not None:
        q.first.return_value = tournament

    return db, q


def _make_tournament(master_instructor_id=None):
    t = MagicMock()
    t.id = 1
    t.master_instructor_id = master_instructor_id
    t.tournament_config_obj = None  # skip parallel_fields check
    return t


def _make_instructor(user_id=42):
    u = MagicMock()
    u.id = user_id
    return u


def _make_admin():
    from app.models.user import UserRole
    u = MagicMock()
    u.role = UserRole.ADMIN
    return u


# ── IPS-01: add_slot MASTER syncs master_instructor_id ───────────────────────


class TestAddSlotMasterSync:

    def test_add_master_slot_syncs_master_instructor_id(self):
        """IPS-01: add_slot(role=MASTER) sets tournament.master_instructor_id."""
        from app.services.tournament.instructor_planning_service import add_slot

        tournament = _make_tournament(master_instructor_id=None)
        instructor = _make_instructor(user_id=42)

        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = 0
        q.first.return_value = None

        # Return tournament for _get_tournament_or_404, instructor for user lookup,
        # None for existing_master / existing / existing_pitch checks
        call_count = [0]

        def query_side(model):
            call_count[0] += 1
            inner = MagicMock()
            inner.filter.return_value = inner
            inner.count.return_value = 0
            # First query → tournament; second → instructor; rest → None (no conflicts)
            if call_count[0] == 1:
                inner.first.return_value = tournament
            elif call_count[0] == 2:
                inner.first.return_value = instructor
            else:
                inner.first.return_value = None
            return inner

        db.query.side_effect = query_side

        result = add_slot(
            db=db,
            semester_id=1,
            instructor_id=42,
            role=SlotRole.MASTER.value,
            pitch_id=None,
            assigned_by_id=1,
        )

        # master_instructor_id must be set on the tournament object
        assert tournament.master_instructor_id == 42

    def test_add_field_slot_does_not_change_master_instructor_id(self):
        """IPS-03: add_slot(role=FIELD) leaves master_instructor_id untouched."""
        from app.services.tournament.instructor_planning_service import add_slot

        tournament = _make_tournament(master_instructor_id=99)
        instructor = _make_instructor(user_id=55)
        pitch = MagicMock()
        pitch.id = 10
        lfa_license = MagicMock()  # active LFA_COACH license

        call_count = [0]

        def query_side(model):
            call_count[0] += 1
            inner = MagicMock()
            inner.filter.return_value = inner
            inner.count.return_value = 0
            if call_count[0] == 1:
                inner.first.return_value = tournament  # _get_tournament_or_404
            elif call_count[0] == 2:
                inner.first.return_value = instructor  # instructor lookup
            elif call_count[0] == 3:
                inner.first.return_value = pitch       # pitch lookup
            elif call_count[0] == 4:
                inner.first.return_value = lfa_license  # LFA_COACH license check
            else:
                inner.first.return_value = None        # conflict checks
            return inner

        db = MagicMock()
        db.query.side_effect = query_side

        add_slot(
            db=db,
            semester_id=1,
            instructor_id=55,
            role=SlotRole.FIELD.value,
            pitch_id=10,
            assigned_by_id=1,
        )

        # FIELD slot must NOT touch master_instructor_id
        assert tournament.master_instructor_id == 99


# ── IPS-02: remove_slot MASTER clears master_instructor_id ───────────────────


class TestRemoveSlotMasterSync:

    def test_remove_master_slot_clears_master_instructor_id(self):
        """IPS-02: remove_slot() with MASTER slot clears tournament.master_instructor_id."""
        from app.services.tournament.instructor_planning_service import remove_slot

        tournament = _make_tournament(master_instructor_id=42)
        admin = _make_admin()

        slot = MagicMock()
        slot.id = 7
        slot.role = SlotRole.MASTER.value
        slot.instructor_id = 42
        slot.semester_id = 1

        call_count = [0]

        def query_side(model):
            call_count[0] += 1
            inner = MagicMock()
            inner.filter.return_value = inner
            if call_count[0] == 1:
                inner.first.return_value = slot        # _get_slot_or_404
            elif call_count[0] == 2:
                inner.first.return_value = tournament  # Semester lookup
            else:
                inner.first.return_value = None
            return inner

        db = MagicMock()
        db.query.side_effect = query_side

        remove_slot(db=db, slot_id=7, by_user=admin)

        assert tournament.master_instructor_id is None
        db.delete.assert_called_once_with(slot)
