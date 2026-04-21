"""
Session segment creation endpoint.

POST /api/v1/sessions/{session_id}/segments
  — Admin or Instructor only.
  — Instructors may only add segments to sessions they own (instructor_id == current user).
  — Returns 409 if the (session_id, position) pair already exists.
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
from .....schemas.session_segment import SessionSegmentCreate, SessionSegmentRead

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
