from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from app.models.quiz import QuestionType, QuizCategory, QuizDifficulty

# Base schemas
class QuizAnswerOptionBase(BaseModel):
    option_text: str = Field(..., max_length=500)
    is_correct: bool = False
    order_index: int = 0

class QuizAnswerOptionCreate(QuizAnswerOptionBase):
    pass

class QuizAnswerOptionResponse(QuizAnswerOptionBase):
    id: int
    question_id: int
    
    class Config:
        from_attributes = True

class QuizAnswerOptionPublic(BaseModel):
    """Public version without is_correct field for students taking quiz"""
    id: int
    option_text: str
    order_index: int
    
    class Config:
        from_attributes = True

# Question schemas
class QuizQuestionBase(BaseModel):
    question_text: str
    question_type: QuestionType
    points: int = 1
    order_index: int = 0
    explanation: Optional[str] = None

class QuizQuestionCreate(QuizQuestionBase):
    answer_options: List[QuizAnswerOptionCreate] = []

class QuizQuestionUpdate(BaseModel):
    question_text: Optional[str] = None
    question_type: Optional[QuestionType] = None
    points: Optional[int] = None
    order_index: Optional[int] = None
    explanation: Optional[str] = None
    answer_options: Optional[List[QuizAnswerOptionCreate]] = None

class QuizQuestionResponse(QuizQuestionBase):
    id: int
    quiz_id: int
    created_at: datetime
    answer_options: List[QuizAnswerOptionResponse] = []
    
    class Config:
        from_attributes = True

class QuizQuestionPublic(BaseModel):
    """Public version for students taking quiz - without correct answers"""
    id: int
    question_text: str
    question_type: QuestionType
    points: int
    order_index: int
    answer_options: List[QuizAnswerOptionPublic] = []
    
    class Config:
        from_attributes = True

# Quiz schemas
class QuizBase(BaseModel):
    title: str = Field(..., max_length=200)
    description: Optional[str] = None
    category: QuizCategory
    difficulty: QuizDifficulty = QuizDifficulty.MEDIUM
    time_limit_minutes: int = 15
    xp_reward: int = 50
    passing_score: float = 70.0
    is_active: bool = True

class QuizCreate(QuizBase):
    questions: List[QuizQuestionCreate] = []

class QuizUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[QuizCategory] = None
    difficulty: Optional[QuizDifficulty] = None
    time_limit_minutes: Optional[int] = None
    xp_reward: Optional[int] = None
    passing_score: Optional[float] = None
    is_active: Optional[bool] = None

class QuizResponse(QuizBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    questions: List[QuizQuestionResponse] = []
    
    class Config:
        from_attributes = True

class QuizListItem(BaseModel):
    """Simplified quiz info for list views"""
    id: int
    title: str
    description: Optional[str] = None
    category: QuizCategory
    difficulty: QuizDifficulty
    time_limit_minutes: int
    xp_reward: int
    question_count: int
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class QuizPublic(BaseModel):
    """Public quiz info for students"""
    id: int
    title: str
    description: Optional[str] = None
    category: QuizCategory
    difficulty: QuizDifficulty
    time_limit_minutes: int
    xp_reward: int
    passing_score: float
    questions: List[QuizQuestionPublic] = []
    
    class Config:
        from_attributes = True

# Quiz attempt schemas
class QuizUserAnswerCreate(BaseModel):
    question_id: int
    selected_option_id: Optional[int] = None  # Multiple choice and True/False
    answer_text: Optional[str] = None  # Fill in the blank

class QuizAttemptStart(BaseModel):
    quiz_id: int

class QuizAttemptSubmit(BaseModel):
    attempt_id: int
    answers: List[QuizUserAnswerCreate]

class QuizUserAnswerResponse(BaseModel):
    id: int
    question_id: int
    selected_option_id: Optional[int] = None
    answer_text: Optional[str] = None
    is_correct: bool
    answered_at: datetime
    
    class Config:
        from_attributes = True

class QuizAttemptResponse(BaseModel):
    id: int
    user_id: int
    quiz_id: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    time_spent_minutes: Optional[float] = None
    score: Optional[float] = None
    total_questions: int
    correct_answers: int
    xp_awarded: int
    passed: bool
    user_answers: List[QuizUserAnswerResponse] = []
    
    class Config:
        from_attributes = True

class QuizAttemptSummary(BaseModel):
    """Simplified attempt info for user's quiz history"""
    id: int
    quiz_title: str
    quiz_category: QuizCategory
    started_at: datetime
    completed_at: Optional[datetime] = None
    score: Optional[float] = None
    passed: bool
    xp_awarded: int
    time_spent_minutes: Optional[float] = None

    class Config:
        from_attributes = True

class QuizAnswerDetail(BaseModel):
    """Per-question answer detail for attempt review"""
    question_id: int
    question_text: str
    question_order: int
    selected_option_id: Optional[int] = None
    selected_option_text: Optional[str] = None
    correct_option_text: Optional[str] = None  # pedagogical reveal — which option was correct
    is_correct: bool
    answer_text: Optional[str] = None  # fill-in-blank answers
    explanation: Optional[str] = None

class QuizAttemptDetailResponse(BaseModel):
    """Full attempt detail with per-question answers for student review"""
    id: int
    quiz_id: int
    user_id: int
    quiz_title: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    score: Optional[float] = None
    total_questions: int
    correct_answers: int
    xp_awarded: int
    passed: bool
    time_spent_minutes: Optional[float] = None
    answers: List[QuizAnswerDetail] = []

class QuizAttemptAdminItem(BaseModel):
    """Single attempt entry in admin list view"""
    id: int
    user_id: int
    user_email: str
    user_name: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    score: Optional[float] = None
    correct_answers: int
    total_questions: int
    passed: bool
    xp_awarded: int

class QuizAttemptsAdminResponse(BaseModel):
    """Admin view: all attempts for a quiz"""
    quiz_id: int
    quiz_title: str
    total_attempts: int
    attempts: List[QuizAttemptAdminItem] = []

# Statistics schemas
class QuizStatistics(BaseModel):
    quiz_id: int
    quiz_title: str
    total_attempts: int
    completed_attempts: int
    average_score: Optional[float] = None
    pass_rate: float = 0.0
    average_time_minutes: Optional[float] = None

class UserQuizStatistics(BaseModel):
    user_id: int
    total_quizzes_attempted: int
    total_quizzes_completed: int
    total_quizzes_passed: int
    total_xp_earned: int
    average_score: Optional[float] = None
    completion_rate: float = 0.0
    pass_rate: float = 0.0
    favorite_category: Optional[QuizCategory] = None

# Dashboard/Overview schemas
class QuizDashboardOverview(BaseModel):
    available_quizzes: int
    completed_quizzes: int
    total_xp_from_quizzes: int
    best_category: Optional[QuizCategory] = None
    recent_attempts: List[QuizAttemptSummary] = []
    
class QuizCategoryProgress(BaseModel):
    category: QuizCategory
    available_quizzes: int
    completed_quizzes: int
    average_score: Optional[float] = None
    total_xp_earned: int