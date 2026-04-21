"""
Unit tests for PATCH /api/v1/sessions/{session_id}/segments/{segment_id}.

Schema validation (no DB):
  - empty payload {} → ValidationError
  - label only → valid
  - position only → valid
  - skill_targets=null → valid (clear intent)
  - duration_minutes=null → valid (clear intent)
  - label=null (explicit null) → ValidationError
  - label="" → ValidationError

Endpoint logic (MagicMock db):
  - segment not found → 404
  - segment belongs to different session → 404
  - admin can update on any session → 200
  - instructor owns session → 200
  - instructor does not own → 403
  - duplicate position (IntegrityError) → 409
"""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError
from unittest.mock import MagicMock
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException

from app.schemas.session_segment import SessionSegmentUpdate
from app.api.api_v1.endpoints.sessions.segments import update_session_segment
from app.models.user import UserRole


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schema validation — pure, no DB
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionSegmentUpdateSchema:
    def test_empty_payload_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentUpdate()

    def test_label_only_valid(self):
        s = SessionSegmentUpdate(label="New Label")
        assert s.label == "New Label"
        assert s.model_fields_set == {"label"}

    def test_position_only_valid(self):
        s = SessionSegmentUpdate(position=3)
        assert s.position == 3
        assert s.model_fields_set == {"position"}

    def test_skill_targets_null_valid(self):
        s = SessionSegmentUpdate.model_validate({"skill_targets": None})
        assert s.skill_targets is None
        assert "skill_targets" in s.model_fields_set

    def test_duration_minutes_null_valid(self):
        s = SessionSegmentUpdate.model_validate({"duration_minutes": None})
        assert s.duration_minutes is None
        assert "duration_minutes" in s.model_fields_set

    def test_label_null_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentUpdate.model_validate({"label": None})

    def test_label_empty_string_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentUpdate(label="")

    def test_is_active_null_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentUpdate.model_validate({"is_active": None})

    def test_position_null_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentUpdate.model_validate({"position": None})

    def test_skill_targets_negative_value_rejected(self):
        with pytest.raises(ValidationError):
            SessionSegmentUpdate(skill_targets={"passing": -0.5})

    def test_all_fields_valid(self):
        s = SessionSegmentUpdate(
            label="Updated",
            position=1,
            duration_minutes=15,
            skill_targets={"passing": 1.0},
            is_active=False,
        )
        assert s.model_fields_set == {
            "label", "position", "duration_minutes", "skill_targets", "is_active"
        }


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
    seg.label = "Original Label"
    seg.duration_minutes = 10
    seg.skill_targets = None
    seg.is_active = True
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


class TestUpdateSessionSegmentEndpoint:
    def test_segment_not_found_returns_404(self):
        db = _db_with(
            session_result=_make_session(),
            segment_result=None,
        )
        with pytest.raises(HTTPException) as exc:
            update_session_segment(
                session_id=10,
                segment_id=999,
                patch_data=SessionSegmentUpdate(label="X"),
                db=db,
                current_user=_make_user(),
            )
        assert exc.value.status_code == 404
        assert "segment" in exc.value.detail.lower()

    def test_segment_wrong_session_returns_404(self):
        # segment_result=None simulates the filter on (id, session_id) finding nothing
        db = _db_with(
            session_result=_make_session(session_id=10),
            segment_result=None,
        )
        with pytest.raises(HTTPException) as exc:
            update_session_segment(
                session_id=10,
                segment_id=99,
                patch_data=SessionSegmentUpdate(label="X"),
                db=db,
                current_user=_make_user(),
            )
        assert exc.value.status_code == 404

    def test_admin_can_update_any_session(self):
        segment = _make_segment()
        db = _db_with(
            session_result=_make_session(instructor_id=99),
            segment_result=segment,
        )

        update_session_segment(
            session_id=10,
            segment_id=99,
            patch_data=SessionSegmentUpdate(label="Updated"),
            db=db,
            current_user=_make_user(role=UserRole.ADMIN, user_id=1),
        )
        db.flush.assert_called_once()
        db.commit.assert_called_once()

    def test_instructor_owns_session_allowed(self):
        segment = _make_segment()
        db = _db_with(
            session_result=_make_session(instructor_id=5),
            segment_result=segment,
        )

        update_session_segment(
            session_id=10,
            segment_id=99,
            patch_data=SessionSegmentUpdate(label="Updated"),
            db=db,
            current_user=_make_user(role=UserRole.INSTRUCTOR, user_id=5),
        )
        db.commit.assert_called_once()

    def test_instructor_does_not_own_returns_403(self):
        db = _db_with(
            session_result=_make_session(instructor_id=99),
            segment_result=_make_segment(),
        )
        with pytest.raises(HTTPException) as exc:
            update_session_segment(
                session_id=10,
                segment_id=99,
                patch_data=SessionSegmentUpdate(label="Updated"),
                db=db,
                current_user=_make_user(role=UserRole.INSTRUCTOR, user_id=5),
            )
        assert exc.value.status_code == 403

    def test_duplicate_position_returns_409(self):
        db = _db_with(
            session_result=_make_session(instructor_id=1),
            segment_result=_make_segment(),
        )
        db.flush.side_effect = IntegrityError("stmt", {}, Exception("unique"))

        with pytest.raises(HTTPException) as exc:
            update_session_segment(
                session_id=10,
                segment_id=99,
                patch_data=SessionSegmentUpdate(position=2),
                db=db,
                current_user=_make_user(role=UserRole.ADMIN),
            )
        assert exc.value.status_code == 409
        assert "position 2" in exc.value.detail

    def test_only_provided_fields_are_set(self):
        """model_fields_set controls which attributes are written."""
        segment = _make_segment()
        db = _db_with(
            session_result=_make_session(instructor_id=1),
            segment_result=segment,
        )

        update_session_segment(
            session_id=10,
            segment_id=99,
            patch_data=SessionSegmentUpdate(label="New Label"),
            db=db,
            current_user=_make_user(role=UserRole.ADMIN),
        )
        # label was set on the segment object
        assert segment.label == "New Label"
        # position was NOT touched (not in model_fields_set)
        assert segment.position == 0
