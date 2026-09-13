from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
import enum

from ..database import Base


class BadgeType(enum.Enum):
    """Different types of achievements/badges"""
    RETURNING_STUDENT = "returning_student"
    VETERAN_STUDENT = "veteran_student"
    MASTER_STUDENT = "master_student"
    ATTENDANCE_STAR = "attendance_star"
    PUNCTUAL_STUDENT = "punctual_student"
    FEEDBACK_CHAMPION = "feedback_champion"
    SEMESTER_WARRIOR = "semester_warrior"

    # New first-time achievement badges
    FIRST_QUIZ_COMPLETED = "first_quiz_completed"
    FIRST_PROJECT_ENROLLED = "first_project_enrolled"
    QUIZ_ENROLLMENT_COMBO = "quiz_enrollment_combo"
    NEWCOMER_WELCOME = "newcomer_welcome"

    # 🆕 SPECIALIZATION-SPECIFIC BADGES
    # Level progression
    FIRST_LEVEL_UP = "first_level_up"
    SKILL_MILESTONE = "skill_milestone"
    ADVANCED_SKILL = "advanced_skill"
    MASTER_LEVEL = "master_level"

    # Specialization dedication
    PLAYER_DEDICATION = "player_dedication"
    COACH_DEDICATION = "coach_dedication"
    INTERNSHIP_DEDICATION = "internship_dedication"

    # Other
    PROJECT_COMPLETE = "project_complete"


class UserAchievement(Base):
    """Track user achievements and badges"""
    __tablename__ = "user_achievements"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    achievement_id = Column(Integer, ForeignKey("achievements.id"), nullable=True)  # 🆕 NEW: Link to achievement definition
    badge_type = Column(String, nullable=False)  # BadgeType enum values
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    icon = Column(String, nullable=True)  # Emoji or icon identifier
    earned_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    semester_count = Column(Integer, nullable=True)  # For semester-based badges
    specialization_id = Column(String(50), ForeignKey('specializations.id'), nullable=True)  # 🆕 NEW
    # PC5 canonical grants. Nullable fields preserve all legacy achievement rows.
    canonical_key = Column(String(100), nullable=True)
    progression_outcome_id = Column(UUID(as_uuid=True), ForeignKey("player_progression_outcomes.id", ondelete="RESTRICT"), nullable=True)

    # Relationships
    user = relationship("User", back_populates="achievements")
    achievement = relationship("Achievement", back_populates="user_achievements")


class UserStats(Base):
    """Extended user statistics for gamification"""
    __tablename__ = "user_stats"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    
    # Semester tracking
    semesters_participated = Column(Integer, default=0)
    first_semester_date = Column(DateTime, nullable=True)
    current_streak = Column(Integer, default=0)  # Consecutive semesters
    
    # Session statistics
    total_bookings = Column(Integer, default=0)
    total_attended = Column(Integer, default=0)
    total_cancelled = Column(Integer, default=0)
    attendance_rate = Column(Float, default=0.0)
    
    # Engagement metrics
    feedback_given = Column(Integer, default=0)
    average_rating_given = Column(Float, default=0.0)
    punctuality_score = Column(Float, default=0.0)  # How often they check in on time
    
    # Experience points and level
    total_xp = Column(Integer, default=0)
    level = Column(Integer, default=1)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    user = relationship("User", back_populates="stats")


# Configure relationships after all models are defined
def configure_relationships():
    """Configure relationships between User and gamification models"""
    from .user import User
    
    # Add relationships to User model
    User.achievements = relationship("UserAchievement", back_populates="user")
    User.stats = relationship("UserStats", back_populates="user", uselist=False)