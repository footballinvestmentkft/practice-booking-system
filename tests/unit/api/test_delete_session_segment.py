"""
Unit tests for DELETE /api/v1/sessions/{session_id}/segments/{segment_id}.

Endpoint logic (MagicMock db):
  - session not found → 404
  - segment not found → 404
  - segment belongs to a different session → 404
  - admin can delete on any session → 200, is_active=False
  - instructor owns session → 200, is_active=False
  - instructor does not own session → 403
  - segment already inactive (is_active=False) → 200, no error (idempotent)
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from fastapi import HTTPException

from app.api.api_v1.endpoints.sessions.segments import delete_session_segment
from app.models.user import UserRole


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_user(role=UserRole.ADMIN, user_id=1):
    u = MagicMock()
    u.id = user_id
    u.role = role
    return u


def _make_session(session_id=10, instructor_id=2):
    s = MagicMock()
    s.id = session_id
    s.instructor_id = instructor_id
    return s


def _make_segment(segment_id=99, session_id=10, is_active=True):
    seg = MagicMock()
    seg.id = segment_id
    seg.session_id = session_id
    seg.position = 0
    seg.label = "Warm-up"
    seg.duration_minutes = 10
    seg.skill_targets = None
    seg.is_active = is_active
    seg.created_at = datetime.now(timezone.utc)
    seg.updated_at = datetime.now(timezone.utc)
    return seg


def _db_with(session_result, segment_result):
    """Build a mock db where the first query returns session_result
    and the second returns segment_result."""
    db = MagicMock()

    q1 = MagicMock()
    q1.filter.return_value.first.return_value = session_result

    q2 = MagicMock()
    q2.filter.return_value.first.return_value = segment_result

    db.query.side_effect = [q1, q2]
    return db


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteSessionSegmentEndpoint:
    def test_session_not_found_returns_404(self):
        db = _db_with(session_result=None, segment_result=None)
        with pytest.raises(HTTPException) as exc:
            delete_session_segment(
                session_id=10,
                segment_id=99,
                db=db,
                current_user=_make_user(),
            )
        assert exc.value.status_code == 404
        assert "session" in exc.value.detail.lower()

    def test_segment_not_found_returns_404(self):
        db = _db_with(session_result=_make_session(), segment_result=None)
        with pytest.raises(HTTPException) as exc:
            delete_session_segment(
                session_id=10,
                segment_id=999,
                db=db,
                current_user=_make_user(),
            )
        assert exc.value.status_code == 404
        assert "segment" in exc.value.detail.lower()

    def test_segment_cross_session_returns_404(self):
        # Segment query returns None because the (id, session_id) filter finds nothing
        db = _db_with(
            session_result=_make_session(session_id=10),
            segment_result=None,
        )
        with pytest.raises(HTTPException) as exc:
            delete_session_segment(
                session_id=10,
                segment_id=99,
                db=db,
                current_user=_make_user(),
            )
        assert exc.value.status_code == 404

    def test_admin_can_delete_any_session(self):
        segment = _make_segment()
        db = _db_with(
            session_result=_make_session(instructor_id=99),
            segment_result=segment,
        )
        result = delete_session_segment(
            session_id=10,
            segment_id=99,
            db=db,
            current_user=_make_user(role=UserRole.ADMIN, user_id=1),
        )
        assert segment.is_active is False
        assert result is segment

    def test_instructor_owns_session_can_delete(self):
        segment = _make_segment()
        db = _db_with(
            session_result=_make_session(instructor_id=5),
            segment_result=segment,
        )
        result = delete_session_segment(
            session_id=10,
            segment_id=99,
            db=db,
            current_user=_make_user(role=UserRole.INSTRUCTOR, user_id=5),
        )
        assert segment.is_active is False
        assert result is segment

    def test_instructor_not_owner_returns_403(self):
        db = _db_with(
            session_result=_make_session(instructor_id=99),
            segment_result=_make_segment(),
        )
        with pytest.raises(HTTPException) as exc:
            delete_session_segment(
                session_id=10,
                segment_id=99,
                db=db,
                current_user=_make_user(role=UserRole.INSTRUCTOR, user_id=1),
            )
        assert exc.value.status_code == 403
        assert "own" in exc.value.detail.lower()

    def test_delete_already_inactive_is_idempotent(self):
        # Segment is already is_active=False
        segment = _make_segment(is_active=False)
        db = _db_with(
            session_result=_make_session(instructor_id=5),
            segment_result=segment,
        )
        result = delete_session_segment(
            session_id=10,
            segment_id=99,
            db=db,
            current_user=_make_user(role=UserRole.ADMIN),
        )
        # No exception, is_active stays False, segment returned
        assert segment.is_active is False
        assert result is segment
        db.commit.assert_called_once()
