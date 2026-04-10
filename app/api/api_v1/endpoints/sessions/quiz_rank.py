"""
Quiz-based Ranking Endpoint

POST /api/v1/sessions/{session_id}/rank-from-quiz

Computes tournament rankings from quiz attempt scores for a virtual session.
Only completed attempts are considered; participants who did not finish are excluded.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin_user_hybrid
from app.models.quiz import SessionQuiz
from app.models.session import Session as SessionModel, SessionType
from app.models.user import User
from app.services.tournament.quiz_ranking_service import auto_rank_from_quiz

import logging

_logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/{session_id}/rank-from-quiz",
    status_code=status.HTTP_200_OK,
    summary="Rank participants from quiz scores (virtual sessions only)",
    tags=["sessions", "quiz-ranking"],
)
def rank_from_quiz(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_hybrid),
):
    """
    Compute and store tournament rankings derived from quiz attempt scores.

    **Authorization:** Admin only

    **Guards:**
    - Session must exist
    - Session must be `session_type = virtual`
    - A `SessionQuiz` link must exist for this session
    - Session quiz window must be closed (`date_end < now`)

    **Ranking logic:**
    - Only participants with a **completed** `QuizAttempt` are ranked
    - Rank 1 = highest quiz score (ties receive the same rank)
    - Results are written to `session.rounds_data` in standard IR format
    - Session `session_status` is set to `completed`

    **Returns:**
    ```json
    {
        "session_id": 42,
        "quiz_id": 7,
        "ranked": [{"user_id": 1, "score": 95.0, "rank": 1}, ...],
        "total": 6,
        "excluded": 1
    }
    ```
    """
    # ── Load session ──────────────────────────────────────────────────────────
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    # ── Guard: virtual only ───────────────────────────────────────────────────
    if session.session_type != SessionType.virtual:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Session {session_id} is not virtual "
                f"(type={session.session_type}). "
                "rank-from-quiz is only supported for virtual sessions."
            ),
        )

    # ── Guard: SessionQuiz link must exist ────────────────────────────────────
    session_quiz = (
        db.query(SessionQuiz)
        .filter(SessionQuiz.session_id == session_id)
        .first()
    )
    if not session_quiz:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Session {session_id} has no linked quiz. "
                "Create a SessionQuiz association before ranking."
            ),
        )

    # ── Guard: quiz window must be closed ─────────────────────────────────────
    if session.date_end and session.date_end > datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Quiz window is still open (date_end={session.date_end.isoformat()}). "
                "Wait until the quiz window has closed before computing rankings."
            ),
        )

    # ── Compute rankings ──────────────────────────────────────────────────────
    try:
        ranked = auto_rank_from_quiz(db, session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus

    total_enrolled = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == session.semester_id,
        SemesterEnrollment.is_active == True,
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
    ).count()

    excluded = total_enrolled - len(ranked)

    _logger.info(
        f"[rank-from-quiz] session={session_id} ranked={len(ranked)} "
        f"excluded={excluded} (did not complete quiz)"
    )

    return {
        "session_id": session_id,
        "quiz_id": session_quiz.quiz_id,
        "ranked": ranked,
        "total": len(ranked),
        "excluded": excluded,
    }
