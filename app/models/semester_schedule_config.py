"""
SemesterScheduleConfig — admin-configurable weekly session generation config
for MINI_SEASON and ACADEMY_SEASON semesters.

One-to-one with Semester (unique semester_id constraint).
"""
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, SmallInteger, Time,
)
from sqlalchemy.orm import relationship

from ..database import Base


class SemesterScheduleConfig(Base):
    __tablename__ = "semester_schedule_configs"

    id = Column(Integer, primary_key=True)
    semester_id = Column(
        Integer,
        ForeignKey("semesters.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Weekly schedule parameters
    day_of_week = Column(SmallInteger, nullable=False)       # 0=Monday .. 6=Sunday
    start_time = Column(Time, nullable=False)                 # e.g. time(17, 0)
    duration_minutes = Column(Integer, nullable=False, default=90)
    sessions_per_week = Column(SmallInteger, nullable=False, default=1)  # 1 or 2

    # Campus / pitch overrides (D-D priority chain)
    campus_id = Column(Integer, ForeignKey("campuses.id", ondelete="SET NULL"), nullable=True)
    pitch_id = Column(Integer, ForeignKey("pitches.id", ondelete="SET NULL"), nullable=True)

    # Generation state
    sessions_generated = Column(Boolean, nullable=False, default=False)
    sessions_generated_at = Column(DateTime, nullable=True)
    sessions_count = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)

    # Relationships
    semester = relationship("Semester", back_populates="schedule_config_obj")
    campus = relationship("Campus")
    pitch = relationship("Pitch")
