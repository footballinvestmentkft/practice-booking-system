"""
Student quiz endpoints
Browse quizzes, view statistics, dashboard
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User, UserRole
from .....models.quiz import QuizCategory, SessionQuiz, QuizAttempt, QuizUserAnswer, QuizQuestion
from .....models.session import Session as SessionModel, SessionType
from .....models.attendance import Attendance, AttendanceStatus
from .....models.booking import Booking, BookingStatus
from .....schemas.quiz import (
    QuizListItem, QuizPublic, QuizAttemptSummary, UserQuizStatistics, QuizDashboardOverview,
    QuizAttemptDetailResponse, QuizAnswerDetail,
)
from .....services.quiz_service import QuizService
from .helpers import get_quiz_service
from datetime import datetime, timezone

router = APIRouter()

@router.get("/available", response_model=List[QuizListItem])
def get_available_quizzes(
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service)
):
    """Get all quizzes available for the current user (not yet completed)"""
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can access quizzes"
        )
    
    quizzes = quiz_service.get_available_quizzes(current_user.id)
    
    return [
        QuizListItem(
            id=quiz.id,
            title=quiz.title,
            description=quiz.description,
            category=quiz.category,
            difficulty=quiz.difficulty,
            time_limit_minutes=quiz.time_limit_minutes,
            xp_reward=quiz.xp_reward,
            question_count=len(quiz.questions),
            is_active=quiz.is_active,
            created_at=quiz.created_at
        )
        for quiz in quizzes
    ]

@router.get("/category/{category}", response_model=List[QuizListItem])
def get_quizzes_by_category(
    category: QuizCategory,
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service)
):
    """Get all quizzes in a specific category"""
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can access quizzes"
        )
    
    quizzes = quiz_service.get_quizzes_by_category(category)
    
    return [
        QuizListItem(
            id=quiz.id,
            title=quiz.title,
            description=quiz.description,
            category=quiz.category,
            difficulty=quiz.difficulty,
            time_limit_minutes=quiz.time_limit_minutes,
            xp_reward=quiz.xp_reward,
            question_count=len(quiz.questions),
            is_active=quiz.is_active,
            created_at=quiz.created_at
        )
        for quiz in quizzes
    ]

@router.get("/{quiz_id}", response_model=QuizPublic)
def get_quiz_for_taking(
    quiz_id: int,
    session_id: int = None,  # Optional session_id for session-based quizzes
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service),
    db: Session = Depends(get_db)
):
    """
    Get quiz details for taking (without correct answers)

    🔒 RULE #5: For hybrid/virtual sessions, quiz is only available during session time
    """
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can take quizzes"
        )

    # 🔒 RULE #5: Validate session-based quiz access (hybrid/virtual only)
    if session_id:
        session = db.query(SessionTypel).filter(SessionTypel.id == session_id).first()
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found"
            )

        # Check if session is hybrid or virtual (quiz-enabled)
        if session.sport_type not in ["HYBRID", "VIRTUAL"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Quizzes are only available for HYBRID and VIRTUAL sessions"
            )

        # Check if quiz is unlocked by instructor
        if not session.quiz_unlocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Quiz has not been unlocked by the instructor yet"
            )

        # Check if current time is within session time window
        current_time = datetime.now(timezone.utc).replace(tzinfo=None)
        session_start_naive = session.date_start.replace(tzinfo=None) if session.date_start.tzinfo else session.date_start
        session_end_naive = session.date_end.replace(tzinfo=None) if session.date_end.tzinfo else session.date_end

        if current_time < session_start_naive:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Quiz is not available yet. Session has not started."
            )

        if current_time > session_end_naive:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Quiz is no longer available. Session has ended."
            )

    # Check if quiz is already completed
    if quiz_service.is_quiz_completed_by_user(current_user.id, quiz_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quiz already completed"
        )

    quiz = quiz_service.get_quiz_by_id(quiz_id)
    if not quiz or not quiz.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz not found or inactive"
        )

    # 🔒 ACCESS CONTROL: Check if quiz is linked to a session (HYBRID or VIRTUAL)
    session_quiz = db.query(SessionQuiz).filter(
        SessionQuiz.quiz_id == quiz_id,
        SessionQuiz.is_required == True
    ).first()

    if session_quiz:
        # Quiz is linked to a session - apply session-specific access control
        session = db.query(SessionModel).filter(
            SessionModel.id == session_quiz.session_id
        ).first()

        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Associated session not found"
            )

        # Check if user has a CONFIRMED booking for this session
        booking = db.query(Booking).filter(
            Booking.user_id == current_user.id,
            Booking.session_id == session.id,
            Booking.status == BookingStatus.CONFIRMED
        ).first()

        if not booking:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must have a confirmed booking for this session to access the quiz"
            )

        # 🎯 HYBRID Session: Check attendance + quiz unlock
        if session.session_type == SessionType.hybrid:
            # 1. Check if quiz is unlocked by instructor
            if not session.quiz_unlocked:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Quiz is not yet unlocked by the instructor. Please wait for the instructor to unlock it during the session."
                )

            # 2. Check if user is marked present on attendance sheet
            attendance = db.query(Attendance).filter(
                Attendance.user_id == current_user.id,
                Attendance.session_id == session.id,
                Attendance.status == AttendanceStatus.present
            ).first()

            if not attendance:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You must be marked present on the attendance sheet to access the quiz"
                )

        # 🌐 VIRTUAL Session: Check time window
        elif session.session_type == SessionType.virtual:
            current_time = datetime.now()

            # Session must be active (within date_start and date_end)
            if current_time < session.date_start:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Quiz is not yet available. Session starts at {session.date_start.isoformat()}"
                )

            if current_time > session.date_end:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Quiz is no longer available. Session has ended."
                )

    return quiz

@router.get("/attempts/my", response_model=List[QuizAttemptSummary])
def get_my_quiz_attempts(
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service)
):
    """Get current user's quiz attempts"""
    attempts = quiz_service.get_user_quiz_attempts(current_user.id)
    
    return [
        QuizAttemptSummary(
            id=attempt.id,
            quiz_title=quiz_service.get_quiz_by_id(attempt.quiz_id).title,
            quiz_category=quiz_service.get_quiz_by_id(attempt.quiz_id).category,
            started_at=attempt.started_at,
            completed_at=attempt.completed_at,
            score=attempt.score,
            passed=attempt.passed,
            xp_awarded=attempt.xp_awarded,
            time_spent_minutes=attempt.time_spent_minutes
        )
        for attempt in attempts
    ]

@router.get("/attempts/{attempt_id}", response_model=QuizAttemptDetailResponse)
def get_attempt_detail(
    attempt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get detailed result of a specific quiz attempt including per-question answers"""
    attempt = (
        db.query(QuizAttempt)
        .options(
            joinedload(QuizAttempt.quiz),
            joinedload(QuizAttempt.user_answers)
                .joinedload(QuizUserAnswer.question)
                .joinedload(QuizQuestion.answer_options),
            joinedload(QuizAttempt.user_answers)
                .joinedload(QuizUserAnswer.selected_option),
        )
        .filter(
            QuizAttempt.id == attempt_id,
            QuizAttempt.user_id == current_user.id,
        )
        .first()
    )

    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found")

    if not attempt.completed_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Attempt not yet completed")

    answers = []
    for ua in sorted(attempt.user_answers, key=lambda x: x.question.order_index):
        question = ua.question
        correct_opt = next((o for o in question.answer_options if o.is_correct), None)
        answers.append(QuizAnswerDetail(
            question_id=question.id,
            question_text=question.question_text,
            question_order=question.order_index,
            selected_option_id=ua.selected_option_id,
            selected_option_text=ua.selected_option.option_text if ua.selected_option else None,
            correct_option_text=correct_opt.option_text if correct_opt else None,
            is_correct=ua.is_correct,
            answer_text=ua.answer_text,
            explanation=question.explanation,
        ))

    return QuizAttemptDetailResponse(
        id=attempt.id,
        quiz_id=attempt.quiz_id,
        user_id=attempt.user_id,
        quiz_title=attempt.quiz.title,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        score=attempt.score,
        total_questions=attempt.total_questions,
        correct_answers=attempt.correct_answers,
        xp_awarded=attempt.xp_awarded,
        passed=attempt.passed,
        time_spent_minutes=attempt.time_spent_minutes,
        answers=answers,
    )


@router.get("/statistics/my", response_model=UserQuizStatistics)
def get_my_quiz_statistics(
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service)
):
    """Get current user's quiz statistics"""
    return quiz_service.get_user_quiz_statistics(current_user.id)

@router.get("/dashboard/overview", response_model=QuizDashboardOverview)
def get_quiz_dashboard_overview(
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service)
):
    """Get quiz dashboard overview for student"""
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can access quiz dashboard"
        )
    
    available_quizzes = quiz_service.get_available_quizzes(current_user.id)
    attempts = quiz_service.get_user_quiz_attempts(current_user.id)
    stats = quiz_service.get_user_quiz_statistics(current_user.id)
    
    completed_quizzes = len([a for a in attempts if a.completed_at])
    total_xp = sum([a.xp_awarded for a in attempts])
    
    recent_attempts = [
        QuizAttemptSummary(
            id=attempt.id,
            quiz_title=quiz_service.get_quiz_by_id(attempt.quiz_id).title,
            quiz_category=quiz_service.get_quiz_by_id(attempt.quiz_id).category,
            started_at=attempt.started_at,
            completed_at=attempt.completed_at,
            score=attempt.score,
            passed=attempt.passed,
            xp_awarded=attempt.xp_awarded,
            time_spent_minutes=attempt.time_spent_minutes
        )
        for attempt in attempts[:5]  # Last 5 attempts
    ]
    
    return QuizDashboardOverview(
        available_quizzes=len(available_quizzes),
        completed_quizzes=completed_quizzes,
        total_xp_from_quizzes=total_xp,
        best_category=stats.favorite_category,
        recent_attempts=recent_attempts
    )

# Admin/Instructor endpoints
