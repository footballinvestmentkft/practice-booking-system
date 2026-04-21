"""
Unit tests for POST /api/v1/sessions/{session_id}/segments.

Coverage:
  Schema validation (no DB access):
    - valid payload accepted
    - label empty → ValidationError
    - label too long (>200 chars) → ValidationError
    - position negative → ValidationError
    - position > 32767 → ValidationError
    - duration_minutes 0 → ValidationError
    - skill_targets with value == 0 → ValidationError
    - skill_targets with value < 0 → ValidationError
    - skill_targets with value > 0 → accepted
    - skill_targets=None → accepted (inherit from preset)

  Endpoint logic (MagicMock db):
    - session not found → 404
    - instructor owns the session → 201
    - instructor does not own the session → 403
    - duplicate position (IntegrityError) → 409
"""

import pytest
from pydantic import ValidationError
from unittest.mock import MagicMock, patch
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException

from app.schemas.session_segment import SessionSegmentCreate
from app.api.api_v1.endpoints.sessions.segments import create_session_segment
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.sessions.segments"


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schema validation — pure, no DB
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionSegmentCreateSchema:
    def test_valid_minimal(self):
        s = SessionSegmentCreate(label="Passing Drill", position=0)
        assert s.label == "Passing Drill"
        assert s.position == 0
        assert s.duration_minutes is None
        assert s.skill_targets is None

    def test_valid_full(self):
        s = SessionSegmentCreate(
            label="Finishing Drill",
            position=2,
            duration_minutes=20,
            skill_targets={"finishing": 1.5, "passing": 0.5},
        )
        assert s.skill_targets == {"finishing": 1.5, "passing": 0.5}

    def test_empty_label_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentCreate(label="", position=0)

    def test_label_too_long_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentCreate(label="x" * 201, position=0)

    def test_negative_position_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentCreate(label="Drill", position=-1)

    def test_position_over_max_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentCreate(label="Drill", position=32768)

    def test_duration_zero_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentCreate(label="Drill", position=0, duration_minutes=0)

    def test_skill_target_zero_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentCreate(
                label="Drill", position=0, skill_targets={"passing": 0.0}
            )

    def test_skill_target_negative_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentCreate(
                label="Drill", position=0, skill_targets={"passing": -0.1}
            )

    def test_skill_targets_positive_accepted(self):
        s = SessionSegmentCreate(
            label="Drill", position=0, skill_targets={"passing": 0.01}
        )
        assert s.skill_targets == {"passing": 0.01}

    def test_skill_targets_none_accepted(self):
        s = SessionSegmentCreate(label="Drill", position=0, skill_targets=None)
        assert s.skill_targets is None


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint logic — MagicMock db
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


def _make_segment(segment_id=99, session_id=10, position=0):
    seg = MagicMock()
    seg.id = segment_id
    seg.session_id = session_id
    seg.position = position
    seg.label = "Test Drill"
    seg.duration_minutes = None
    seg.skill_targets = None
    seg.is_active = True
    from datetime import datetime, timezone
    seg.created_at = datetime.now(timezone.utc)
    seg.updated_at = datetime.now(timezone.utc)
    return seg


def _valid_input(**kwargs):
    defaults = {"label": "Test Drill", "position": 0}
    defaults.update(kwargs)
    return SessionSegmentCreate(**defaults)


class TestCreateSessionSegmentEndpoint:
    def _db(self):
        return MagicMock()

    def test_session_not_found_returns_404(self):
        db = self._db()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            create_session_segment(
                session_id=999,
                segment_data=_valid_input(),
                db=db,
                current_user=_make_user(),
            )
        assert exc.value.status_code == 404
        assert "not found" in exc.value.detail.lower()

    def test_admin_can_create_on_any_session(self):
        db = self._db()
        session = _make_session(instructor_id=99)  # different owner
        segment = _make_segment()

        # db.query().filter().first() returns session on first call
        q = MagicMock()
        q.filter.return_value.first.return_value = session
        db.query.return_value = q
        db.refresh.side_effect = lambda obj: setattr(obj, "__dict__", segment.__dict__)

        result = create_session_segment(
            session_id=10,
            segment_data=_valid_input(),
            db=db,
            current_user=_make_user(role=UserRole.ADMIN, user_id=1),
        )
        db.add.assert_called_once()
        db.flush.assert_called_once()
        db.commit.assert_called_once()

    def test_instructor_owns_session_allowed(self):
        db = self._db()
        session = _make_session(instructor_id=5)

        q = MagicMock()
        q.filter.return_value.first.return_value = session
        db.query.return_value = q

        create_session_segment(
            session_id=10,
            segment_data=_valid_input(),
            db=db,
            current_user=_make_user(role=UserRole.INSTRUCTOR, user_id=5),
        )
        db.add.assert_called_once()

    def test_instructor_does_not_own_session_returns_403(self):
        db = self._db()
        session = _make_session(instructor_id=99)  # owned by user 99

        q = MagicMock()
        q.filter.return_value.first.return_value = session
        db.query.return_value = q

        with pytest.raises(HTTPException) as exc:
            create_session_segment(
                session_id=10,
                segment_data=_valid_input(),
                db=db,
                current_user=_make_user(role=UserRole.INSTRUCTOR, user_id=5),
            )
        assert exc.value.status_code == 403

    def test_duplicate_position_returns_409(self):
        db = self._db()
        session = _make_session(instructor_id=1)

        q = MagicMock()
        q.filter.return_value.first.return_value = session
        db.query.return_value = q
        db.flush.side_effect = IntegrityError("stmt", {}, Exception("unique"))

        with pytest.raises(HTTPException) as exc:
            create_session_segment(
                session_id=10,
                segment_data=_valid_input(position=0),
                db=db,
                current_user=_make_user(role=UserRole.ADMIN),
            )
        assert exc.value.status_code == 409
        assert "position 0" in exc.value.detail
