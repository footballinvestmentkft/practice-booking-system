from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Enum as SQLEnum, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base

class QuestionType(enum.Enum):
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    TRUE_FALSE = "TRUE_FALSE"
    FILL_IN_BLANK = "FILL_IN_BLANK"
    MATCHING = "matching"
    SHORT_ANSWER = "short_answer"
    LONG_ANSWER = "long_answer"
    CALCULATION = "calculation"
    SCENARIO_BASED = "scenario_based"

class QuizCategory(enum.Enum):
    GENERAL = "GENERAL"
    MARKETING = "MARKETING"
    ECONOMICS = "ECONOMICS"
    INFORMATICS = "INFORMATICS"
    SPORTS_PHYSIOLOGY = "SPORTS_PHYSIOLOGY"
    NUTRITION = "NUTRITION"
    LESSON = "LESSON"  # Curriculum lesson-based quizzes

class QuizDifficulty(enum.Enum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"

class Quiz(Base):
    __tablename__ = "quizzes"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(SQLEnum(QuizCategory), nullable=False)
    difficulty = Column(SQLEnum(QuizDifficulty), nullable=False, default=QuizDifficulty.MEDIUM)
    time_limit_minutes = Column(Integer, nullable=False, default=15)  # időkorlát percekben
    xp_reward = Column(Integer, nullable=False, default=50)  # XP jutalom sikeres kitöltésért
    passing_score = Column(Float, nullable=False, default=70.0)  # minimum pont százalékban
    language = Column(String(10), nullable=False, default='en')
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    questions = relationship("QuizQuestion", back_populates="quiz", cascade="all, delete-orphan")
    attempts = relationship("QuizAttempt", back_populates="quiz")

class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    
    id = Column(Integer, primary_key=True, index=True)
    quiz_id = Column(Integer, ForeignKey("quizzes.id"), nullable=False)
    question_text = Column(Text, nullable=False)
    question_type = Column(SQLEnum(QuestionType), nullable=False)
    points = Column(Integer, nullable=False, default=1)
    order_index = Column(Integer, nullable=False, default=0)  # kérdések sorrendje
    explanation = Column(Text, nullable=True)  # magyarázat a helyes válaszhoz
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    quiz = relationship("Quiz", back_populates="questions")
    answer_options = relationship("QuizAnswerOption", back_populates="question", cascade="all, delete-orphan")
    user_answers = relationship("QuizUserAnswer", back_populates="question")

class QuizAnswerOption(Base):
    __tablename__ = "quiz_answer_options"
    
    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("quiz_questions.id"), nullable=False)
    option_text = Column(String(500), nullable=False)
    is_correct = Column(Boolean, nullable=False, default=False)
    order_index = Column(Integer, nullable=False, default=0)  # válaszok sorrendje
    
    # Relationships
    question = relationship("QuizQuestion", back_populates="answer_options")

class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    quiz_id = Column(Integer, ForeignKey("quizzes.id"), nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    time_spent_minutes = Column(Float, nullable=True)  # ténylegesen eltöltött idő
    score = Column(Float, nullable=True)  # elért pont százalékban
    total_questions = Column(Integer, nullable=False)
    correct_answers = Column(Integer, nullable=False, default=0)
    xp_awarded = Column(Integer, nullable=False, default=0)
    passed = Column(Boolean, nullable=False, default=False)
    
    # Relationships
    user = relationship("User")
    quiz = relationship("Quiz", back_populates="attempts")
    user_answers = relationship("QuizUserAnswer", back_populates="attempt", cascade="all, delete-orphan")

class QuizUserAnswer(Base):
    __tablename__ = "quiz_user_answers"
    
    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey("quiz_attempts.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("quiz_questions.id"), nullable=False)
    selected_option_id = Column(Integer, ForeignKey("quiz_answer_options.id"), nullable=True)  # többválasztásos és igaz/hamis kérdésekhez
    answer_text = Column(String(1000), nullable=True)  # kiegészítős feladatokhoz
    is_correct = Column(Boolean, nullable=False, default=False)
    answered_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    attempt = relationship("QuizAttempt", back_populates="user_answers")
    question = relationship("QuizQuestion", back_populates="user_answers")
    selected_option = relationship("QuizAnswerOption")


class SessionQuiz(Base):
    """Junction table linking sessions to quizzes (for HYBRID and VIRTUAL sessions)"""
    __tablename__ = "session_quizzes"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    quiz_id = Column(Integer, ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False)
    is_required = Column(Boolean, default=True)
    max_attempts = Column(Integer, nullable=True)  # NULL = unlimited (for HYBRID), 1-2 for VIRTUAL
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Ensure unique combination
    __table_args__ = (UniqueConstraint('session_id', 'quiz_id', name='uq_session_quiz'),)

    # Relationships
    session = relationship("Session", foreign_keys=[session_id])
    quiz = relationship("Quiz", foreign_keys=[quiz_id])


# ADAPTIVE LEARNING MODELS

class UserQuestionPerformance(Base):
    """Adaptív tanuláshoz - egyedi kérdések teljesítményének nyomonkövetése"""
    __tablename__ = "user_question_performance"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("quiz_questions.id"), nullable=False)
    
    # Performance metrics
    total_attempts = Column(Integer, default=0)
    correct_attempts = Column(Integer, default=0)
    last_attempt_correct = Column(Boolean, default=False)
    last_attempted_at = Column(DateTime(timezone=True), nullable=True)
    
    # Adaptive learning weights
    difficulty_weight = Column(Float, default=1.0)  # 1.0 = normal, >1.0 = needs more practice
    next_review_at = Column(DateTime(timezone=True), nullable=True)  # spaced repetition
    mastery_level = Column(Float, default=0.0)  # 0.0-1.0 scale
    
    # Relationships
    user = relationship("User")
    question = relationship("QuizQuestion")
    
    # Unique constraint
    __table_args__ = (UniqueConstraint('user_id', 'question_id', name='unique_user_question'),)
    
    @property
    def success_rate(self):
        return (self.correct_attempts / self.total_attempts) if self.total_attempts > 0 else 0.0


class AdaptiveLearningSession(Base):
    """Adaptív tanulási session nyomonkövetése"""
    __tablename__ = "adaptive_learning_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category = Column(SQLEnum(QuizCategory), nullable=False)
    
    # Session info
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    questions_presented = Column(Integer, default=0)
    questions_correct = Column(Integer, default=0)
    xp_earned = Column(Integer, default=0)
    
    # Session language (ensures HU and EN questions never mix)
    language = Column(String(10), nullable=False, default='en')

    # Module scoping — quiz title prefix (e.g. 'AL — Edzéselmélet')
    # NULL on legacy sessions; required for all new sessions via v2 flow
    module_prefix = Column(String(200), nullable=True)

    # Adaptive algorithm data
    target_difficulty = Column(Float, default=0.5)  # 0.0-1.0
    performance_trend = Column(Float, default=0.0)  # -1.0 to 1.0
    
    # Session timing
    session_time_limit_seconds = Column(Integer, default=1800)  # 30 minutes default
    session_start_time = Column(DateTime(timezone=True), nullable=True)

    # Spaced-repetition cap: how many due questions have been served this session
    session_due_shown = Column(Integer, nullable=False, default=0)

    # Relationships
    user = relationship("User")


class QuestionMetadata(Base):
    """Kérdések metaadatai az adaptív tanuláshoz"""
    __tablename__ = "question_metadata"
    
    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("quiz_questions.id"), nullable=False)
    
    # Question characteristics
    estimated_difficulty = Column(Float, default=0.5)  # 0.0-1.0
    cognitive_load = Column(Float, default=0.5)  # 0.0-1.0
    concept_tags = Column(String(500), nullable=True)  # JSON array of concepts
    prerequisite_concepts = Column(String(500), nullable=True)  # JSON array
    
    # Learning analytics
    average_time_seconds = Column(Float, nullable=True)
    global_success_rate = Column(Float, nullable=True)
    last_analytics_update = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    question = relationship("QuizQuestion")
    
    # Unique constraint
    __table_args__ = (UniqueConstraint('question_id', name='unique_question_metadata'),)