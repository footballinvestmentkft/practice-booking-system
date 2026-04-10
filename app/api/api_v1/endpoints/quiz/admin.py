"""
Admin quiz management
Create, manage, and view quiz statistics
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User, UserRole
from .....models.quiz import Quiz, QuizAttempt
from .....schemas.quiz import (
    QuizCreate, QuizResponse, QuizListItem, QuizStatistics,
    QuizAttemptsAdminResponse, QuizAttemptAdminItem,
)
from .....services.quiz_service import QuizService
from .helpers import get_quiz_service

router = APIRouter()

@router.post("/", response_model=QuizResponse)
def create_quiz(
    quiz_data: QuizCreate,
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service)
):
    """Create a new quiz (instructors/admins only)"""
    if current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors and admins can create quizzes"
        )
    
    quiz = quiz_service.create_quiz(quiz_data)
    return quiz

@router.get("/admin/{quiz_id}", response_model=QuizResponse)
def get_quiz_admin(
    quiz_id: int,
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service)
):
    """Get quiz with all details including correct answers (admin view)"""
    if current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors and admins can view quiz details"
        )
    
    quiz = quiz_service.get_quiz_by_id(quiz_id)
    if not quiz:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz not found"
        )
    
    return quiz

@router.get("/admin/all", response_model=List[QuizListItem])
def get_all_quizzes_admin(
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service),
    db: Session = Depends(get_db)
):
    """Get all quizzes for admin/instructor management"""
    if current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors and admins can manage quizzes"
        )
    
    from app.models.quiz import Quiz
    quizzes = db.query(Quiz).order_by(Quiz.category, Quiz.title).all()
    
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

@router.get("/admin/{quiz_id}/attempts", response_model=QuizAttemptsAdminResponse)
def get_quiz_attempts_admin(
    quiz_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all attempts for a quiz (admin/instructor only)"""
    if current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors and admins can view quiz attempts",
        )

    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found")

    attempts = (
        db.query(QuizAttempt)
        .options(joinedload(QuizAttempt.user))
        .filter(QuizAttempt.quiz_id == quiz_id)
        .order_by(QuizAttempt.started_at.desc())
        .all()
    )

    items = [
        QuizAttemptAdminItem(
            id=a.id,
            user_id=a.user_id,
            user_email=a.user.email,
            user_name=(f"{a.user.first_name or ''} {a.user.last_name or ''}".strip() or a.user.email),
            started_at=a.started_at,
            completed_at=a.completed_at,
            score=a.score,
            correct_answers=a.correct_answers,
            total_questions=a.total_questions,
            passed=a.passed,
            xp_awarded=a.xp_awarded,
        )
        for a in attempts
    ]

    return QuizAttemptsAdminResponse(
        quiz_id=quiz.id,
        quiz_title=quiz.title,
        total_attempts=len(items),
        attempts=items,
    )


@router.get("/statistics/{quiz_id}", response_model=QuizStatistics)
def get_quiz_statistics(
    quiz_id: int,
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service)
):
    """Get statistics for a specific quiz"""
    if current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors and admins can view quiz statistics"
        )
    
    return quiz_service.get_quiz_statistics(quiz_id)

@router.get("/leaderboard/{quiz_id}")
def get_quiz_leaderboard(
    quiz_id: int,
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service)
):
    """Get leaderboard for a specific quiz"""
    if current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors and admins can view leaderboards"
        )
    
    return quiz_service.get_quiz_leaderboard(quiz_id)