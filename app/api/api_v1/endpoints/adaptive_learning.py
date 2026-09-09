from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone

from ....database import get_db
from ....dependencies import get_current_user
from ....models.user import User
from ....models.quiz import QuizCategory, QuestionType, QuizQuestion, Quiz, AdaptiveLearningSession
from ....services.adaptive_learning import AdaptiveLearningService

router = APIRouter()


# Request/Response schemas
class StartSessionRequest(BaseModel):
    category: QuizCategory
    session_duration_seconds: int = 180  # 3 minutes default

class AnswerQuestionRequest(BaseModel):
    question_id: int
    selected_option_id: int = None
    answer_text: str = None
    time_spent_seconds: float

class AdaptiveSessionResponse(BaseModel):
    session_id: int
    target_difficulty: float
    category: str
    session_duration_seconds: int

class QuestionResponse(BaseModel):
    id: int
    question_text: str
    question_type: str
    answer_options: List[Dict] = []
    estimated_difficulty: float = None
    concept_tags: List[str] = []
    time_limit_seconds: int = 90
    session_time_remaining: int = None

class AnswerResultResponse(BaseModel):
    is_correct: bool
    explanation: str = None
    xp_earned: int
    new_target_difficulty: float = None
    performance_trend: float = None
    mastery_update: Dict = {}
    session_stats: Dict = {}

class SessionSummaryResponse(BaseModel):
    questions_answered: int
    correct_answers: int
    success_rate: float
    xp_earned: int
    performance_trend: float
    final_difficulty: float

class LearningAnalyticsResponse(BaseModel):
    total_questions_attempted: int
    total_attempts: int
    overall_success_rate: float
    mastery_level: float
    learning_velocity: float
    recommended_difficulty: float


@router.post("/start-session", response_model=AdaptiveSessionResponse)
def start_adaptive_learning_session(
    request: StartSessionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Start a new adaptive learning session
    """
    if current_user.role.value != 'student':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can start learning sessions"
        )
    
    adaptive_service = AdaptiveLearningService(db)
    session = adaptive_service.start_adaptive_session(
        current_user.id, 
        request.category, 
        request.session_duration_seconds
    )
    
    return AdaptiveSessionResponse(
        session_id=session.id,
        target_difficulty=session.target_difficulty,
        category=session.category.value,
        session_duration_seconds=session.session_time_limit_seconds
    )


@router.post("/sessions/{session_id}/next-question")
def get_next_question(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get the next adaptive question for the session
    """
    adaptive_service = AdaptiveLearningService(db)
    result = adaptive_service.get_next_question(current_user.id, session_id)
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    # Check if session is complete
    if isinstance(result, dict) and result.get("session_complete"):
        reason = result.get("reason", "unknown")
        if reason == "time_expired":
            return {"session_complete": True, "reason": "Session time limit reached"}
        elif reason == "no_questions":
            return {"session_complete": True, "reason": "No more questions available"}
        else:
            return {"session_complete": True, "reason": reason}
    
    # result is already a properly formatted dict from the service
    question_data = result
    
    return {
        "id": question_data["id"],
        "question_text": question_data["text"], 
        "question_type": question_data["type"],
        "answer_options": question_data["options"],
        "estimated_difficulty": question_data["difficulty"],
        "session_time_remaining": question_data["session_time_remaining"],
        "concept_tags": [],
        "time_limit_seconds": 90
    }


@router.post("/sessions/{session_id}/answer", response_model=AnswerResultResponse)
def submit_answer(
    session_id: int,
    request: AnswerQuestionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Submit an answer and get adaptive feedback
    """
    adaptive_service = AdaptiveLearningService(db)
    
    # Verify answer correctness
    question = db.query(QuizQuestion).filter(QuizQuestion.id == request.question_id).first()
    
    if not question:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question not found"
        )
    
    is_correct = False
    explanation = question.explanation or ""
    
    # Check answer based on question type
    if question.question_type in [QuestionType.MULTIPLE_CHOICE, QuestionType.TRUE_FALSE]:
        if request.selected_option_id:
            selected_option = db.query(QuizAnswerOption).filter(
                QuizAnswerOption.id == request.selected_option_id
            ).first()
            is_correct = selected_option.is_correct if selected_option else False
    
    elif question.question_type == QuestionType.FILL_IN_BLANK:
        # Simple text matching (can be enhanced with NLP)
        correct_answer = next((opt.option_text for opt in question.answer_options if opt.is_correct), "")
        is_correct = request.answer_text and request.answer_text.strip().lower() == correct_answer.lower()
    
    # Record answer and get adaptive feedback
    result = adaptive_service.record_answer(
        user_id=current_user.id,
        session_id=session_id,
        question_id=request.question_id,
        is_correct=is_correct,
        time_spent_seconds=request.time_spent_seconds
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    
    # Get updated session stats
    session = db.query(AdaptiveLearningSession).filter(
        AdaptiveLearningSession.id == session_id
    ).first()
    
    session_stats = {
        "questions_answered": session.questions_presented if session else 0,
        "questions_correct": session.questions_correct if session else 0,
        "xp_earned": session.xp_earned if session else 0,
        "success_rate": (session.questions_correct / session.questions_presented * 100) if session and session.questions_presented > 0 else 0
    }
    
    return AnswerResultResponse(
        is_correct=is_correct,
        explanation=explanation,
        xp_earned=result["xp_earned"],
        new_target_difficulty=result["new_target_difficulty"],
        performance_trend=result["performance_trend"],
        mastery_update=result["mastery_update"],
        session_stats=session_stats
    )


@router.post("/sessions/{session_id}/end", response_model=SessionSummaryResponse)
def end_learning_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    End the adaptive learning session and get summary
    """
    adaptive_service = AdaptiveLearningService(db)
    summary = adaptive_service.end_session(current_user.id, session_id)
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    
    return SessionSummaryResponse(
        questions_answered=summary["questions_answered"],
        correct_answers=summary["correct_answers"],
        success_rate=summary["success_rate"],
        xp_earned=summary["xp_earned"],
        performance_trend=summary["performance_trend"],
        final_difficulty=summary["final_difficulty"]
    )


@router.get("/analytics", response_model=LearningAnalyticsResponse)
def get_learning_analytics(
    category: QuizCategory = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get user's learning analytics and progress
    """
    adaptive_service = AdaptiveLearningService(db)
    analytics = adaptive_service.get_user_learning_analytics(current_user.id, category)
    
    return LearningAnalyticsResponse(
        total_questions_attempted=analytics["total_questions_attempted"],
        total_attempts=analytics["total_attempts"],
        overall_success_rate=analytics["overall_success_rate"],
        mastery_level=analytics["mastery_level"],
        learning_velocity=analytics["learning_velocity"],
        recommended_difficulty=analytics["recommended_difficulty"]
    )


@router.get("/categories", response_model=List[Dict])
def get_available_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get available learning categories
    """
    categories = []
    for category in QuizCategory:
        # Count available questions
        question_count = db.query(QuizQuestion).join(Quiz).filter(
            Quiz.category == category
        ).count()
        
        categories.append({
            "value": category.value,
            "name": category.value.replace("_", " ").title(),
            "question_count": question_count
        })
    
    return categories


@router.get("/leaderboard")
def get_adaptive_leaderboard(
    category: QuizCategory = None,
    timeframe: str = "week",  # week, month, all_time
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get adaptive learning leaderboard
    """
    now = datetime.now(timezone.utc)
    if timeframe == "week":
        since = now - timedelta(days=7)
    elif timeframe == "month":
        since = now - timedelta(days=30)
    else:
        since = None
    
    # Query sessions
    query = db.query(AdaptiveLearningSession).filter(
        AdaptiveLearningSession.ended_at.isnot(None)
    )
    
    if category:
        query = query.filter(AdaptiveLearningSession.category == category)
    
    if since:
        query = query.filter(AdaptiveLearningSession.started_at >= since)
    
    sessions = query.all()
    
    # Aggregate by user
    user_stats = {}
    for session in sessions:
        user_id = session.user_id
        if user_id not in user_stats:
            user_stats[user_id] = {
                "user_id": user_id,
                "total_xp": 0,
                "total_questions": 0,
                "total_correct": 0,
                "session_count": 0
            }
        
        user_stats[user_id]["total_xp"] += (session.xp_earned or 0)
        user_stats[user_id]["total_questions"] += session.questions_presented
        user_stats[user_id]["total_correct"] += session.questions_correct
        user_stats[user_id]["session_count"] += 1
    
    # Add user details and calculate success rates
    leaderboard = []
    for stats in user_stats.values():
        user = db.query(User).filter(User.id == stats["user_id"]).first()
        if user:
            success_rate = (stats["total_correct"] / stats["total_questions"]) if stats["total_questions"] > 0 else 0
            
            leaderboard.append({
                "user_id": user.id,
                "user_name": user.name,
                "total_xp": stats["total_xp"],
                "success_rate": success_rate,
                "questions_answered": stats["total_questions"],
                "session_count": stats["session_count"]
            })
    
    # Sort by XP (primary) and success rate (secondary)
    leaderboard.sort(key=lambda x: (x["total_xp"], x["success_rate"]), reverse=True)
    
    return {
        "leaderboard": leaderboard[:10],  # Top 10
        "user_position": next((i+1 for i, entry in enumerate(leaderboard) if entry["user_id"] == current_user.id), None),
        "timeframe": timeframe,
        "category": category.value if category else "all"
    }
