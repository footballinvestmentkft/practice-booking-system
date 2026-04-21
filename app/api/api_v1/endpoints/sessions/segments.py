"""
Session segment endpoints.

POST   /api/v1/sessions/{session_id}/segments
PATCH  /api/v1/sessions/{session_id}/segments/{segment_id}
DELETE /api/v1/sessions/{session_id}/segments/{segment_id}
  — Admin or Instructor only.
  — Instructors may only operate on sessions they own (instructor_id == current user).
  — POST/PATCH return 409 if the (session_id, position) pair already exists.
  — DELETE is a soft delete (is_active=False); idempotent — safe to repeat.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_admin_or_instructor_user
from .....models.session import Session as SessionModel
from .....models.session_segment import SessionSegment
from .....models.user import User, UserRole
from .....schemas.session_segment import (
    SessionSegmentCreate,
    SessionSegmentRead,
    SessionSegmentUpdate,
)

router = APIRouter()


@router.post(
    "/{session_id}/segments",
    response_model=SessionSegmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a session segment",
    tags=["session-segments"],
)
def create_session_segment(
    session_id: int,
    segment_data: SessionSegmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user),
) -> Any:
    """
    Add a single drill/exercise segment to an existing session.

    - **Admin**: may add segments to any session.
    - **Instructor**: may only add segments to sessions they own
      (`session.instructor_id == current_user.id`).
    - Returns **409** if a segment at the given `position` already exists for this session.
    """
    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id)
        .first()
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Instructors may only modify their own sessions
    if current_user.role == UserRole.INSTRUCTOR:
        if session.instructor_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only add segments to sessions you own.",
            )

    segment = SessionSegment(
        session_id=session_id,
        position=segment_data.position,
        label=segment_data.label,
        duration_minutes=segment_data.duration_minutes,
        skill_targets=segment_data.skill_targets,
        is_active=True,
    )
    db.add(segment)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A segment at position {segment_data.position} already exists for this session.",
        )

    db.commit()
    db.refresh(segment)
    return segment


@router.patch(
    "/{session_id}/segments/{segment_id}",
    response_model=SessionSegmentRead,
    status_code=status.HTTP_200_OK,
    summary="Update a session segment",
    tags=["session-segments"],
)
def update_session_segment(
    session_id: int,
    segment_id: int,
    patch_data: SessionSegmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user),
) -> Any:
    """
    Partially update a segment on an existing session.

    - Only fields present in the request body are modified.
    - **Admin**: may update segments on any session.
    - **Instructor**: may only update segments on sessions they own.
    - ``null`` on ``duration_minutes`` or ``skill_targets`` clears the field.
    - Returns **409** if the new ``position`` conflicts with an existing segment.
    """
    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id)
        .first()
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    segment = (
        db.query(SessionSegment)
        .filter(
            SessionSegment.id == segment_id,
            SessionSegment.session_id == session_id,
        )
        .first()
    )
    if segment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Segment not found",
        )

    if current_user.role == UserRole.INSTRUCTOR:
        if session.instructor_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only update segments on sessions you own.",
            )

    for field in patch_data.model_fields_set:
        setattr(segment, field, getattr(patch_data, field))

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A segment at position {patch_data.position} already exists for this session.",
        )

    db.commit()
    db.refresh(segment)
    return segment


@router.delete(
    "/{session_id}/segments/{segment_id}",
    response_model=SessionSegmentRead,
    status_code=status.HTTP_200_OK,
    summary="Soft-delete a session segment",
    tags=["session-segments"],
)
def delete_session_segment(
    session_id: int,
    segment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user),
) -> Any:
    """
    Soft-delete a segment by setting ``is_active=False``.

    - **Admin**: may delete segments on any session.
    - **Instructor**: may only delete segments on sessions they own.
    - Idempotent: deleting an already-inactive segment returns 200 with no error.
    - Segment results (``session_segment_results``) are preserved — no cascade delete.
    """
    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id)
        .first()
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    segment = (
        db.query(SessionSegment)
        .filter(
            SessionSegment.id == segment_id,
            SessionSegment.session_id == session_id,
        )
        .first()
    )
    if segment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Segment not found",
        )

    if current_user.role == UserRole.INSTRUCTOR:
        if session.instructor_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only delete segments on sessions you own.",
            )

    segment.is_active = False
    db.commit()
    db.refresh(segment)
    return segment
