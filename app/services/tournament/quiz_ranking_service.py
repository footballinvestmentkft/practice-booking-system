"""
Quiz Ranking Service

Computes tournament rankings from quiz attempt scores.
Used for virtual promotional events where quiz performance determines final rank.

Only participants with a completed QuizAttempt (completed_at IS NOT NULL) are ranked.
Participants who did not complete the quiz are excluded from the ranking.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.quiz import Quiz, QuizAttempt, SessionQuiz
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.session import Session as SessionModel, SessionType

import logging

_logger = logging.getLogger(__name__)


def auto_rank_from_quiz(db: Session, session_id: int) -> List[dict]:
    """
    Compute tournament rankings from quiz attempt scores for a virtual session.

    Steps:
    1. Validate session is virtual and has a linked quiz (via SessionQuiz).
    2. Resolve enrolled participants for the parent tournament.
    3. Query completed QuizAttempts, ORDER BY score DESC.
    4. Assign ranks (1 = highest score). Ties receive the same rank.
    5. Write results to session.rounds_data["round_results"]["1"] in standard IR format.
    6. Persist and return ranked list.

    Participants who did not complete the quiz are excluded (not ranked).

    Args:
        db: SQLAlchemy session
        session_id: ID of the virtual session

    Returns:
        List of dicts: [{"user_id": int, "score": float, "rank": int}, ...]
        Sorted by rank ASC.

    Raises:
        ValueError: if session not found, not virtual, or no SessionQuiz link.
    """
    # ── 1. Load session ──────────────────────────────────────────────────────
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    if session.session_type != SessionType.virtual:
        raise ValueError(
            f"Session {session_id} is not virtual (type={session.session_type}). "
            "auto_rank_from_quiz only supports virtual sessions."
        )

    tournament_id = session.semester_id

    # ── 2. Resolve linked quiz ───────────────────────────────────────────────
    session_quiz = (
        db.query(SessionQuiz)
        .filter(SessionQuiz.session_id == session_id)
        .first()
    )
    if session_quiz is None:
        raise ValueError(
            f"Session {session_id} has no linked quiz (SessionQuiz not found). "
            "Create a SessionQuiz link before calling auto_rank_from_quiz."
        )

    quiz_id = session_quiz.quiz_id

    # ── 3. Enrolled user_ids ─────────────────────────────────────────────────
    enrolled_user_ids: List[int] = [
        row.user_id
        for row in db.query(SemesterEnrollment.user_id).filter(
            SemesterEnrollment.semester_id == tournament_id,
            SemesterEnrollment.is_active == True,
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
        ).all()
    ]

    if not enrolled_user_ids:
        _logger.warning(
            f"[quiz-ranking] No enrolled participants for tournament {tournament_id} / "
            f"session {session_id}. Returning empty ranking."
        )
        return []

    # ── 4. Completed QuizAttempts for enrolled participants ──────────────────
    attempts = (
        db.query(QuizAttempt)
        .filter(
            QuizAttempt.quiz_id == quiz_id,
            QuizAttempt.user_id.in_(enrolled_user_ids),
            QuizAttempt.completed_at.isnot(None),
        )
        .order_by(desc(QuizAttempt.score))
        .all()
    )

    if not attempts:
        _logger.info(
            f"[quiz-ranking] No completed quiz attempts for quiz {quiz_id} / "
            f"tournament {tournament_id}. Returning empty ranking."
        )
        return []

    # ── 5. Assign ranks (ties → same rank) ──────────────────────────────────
    ranked: List[dict] = []
    current_rank = 1
    for i, attempt in enumerate(attempts):
        if i > 0 and attempt.score < attempts[i - 1].score:
            current_rank = i + 1
        ranked.append({
            "user_id": attempt.user_id,
            "score": float(attempt.score) if attempt.score is not None else 0.0,
            "rank": current_rank,
        })

    # ── 6. Write to session.rounds_data (standard IR format) ────────────────
    from sqlalchemy.orm.attributes import flag_modified

    rd = session.rounds_data or {}
    rd["total_rounds"] = 1
    rd["completed_rounds"] = 1
    rd.setdefault("round_results", {})
    rd["round_results"]["1"] = {"results": ranked}
    session.rounds_data = rd
    flag_modified(session, "rounds_data")
    session.session_status = "completed"
    db.commit()

    _logger.info(
        f"[quiz-ranking] Ranked {len(ranked)} participants for session {session_id} "
        f"(tournament {tournament_id}, quiz {quiz_id})."
    )

    return ranked
