"""Club model — persistent football club entity"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Club(Base):
    __tablename__ = "clubs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(20), unique=True, nullable=False, index=True)
    city = Column(String(100), nullable=True)
    country = Column(String(50), nullable=True)
    contact_email = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    teams = relationship("Team", back_populates="club", foreign_keys="Team.club_id")
    csv_imports = relationship("CsvImportLog", back_populates="club")
    creator = relationship("User", foreign_keys=[created_by])
    organized_promotion_events = relationship(
        "Semester",
        back_populates="organizer_club",
        foreign_keys="Semester.organizer_club_id",
    )


class CsvImportLog(Base):
    __tablename__ = "csv_import_logs"

    id = Column(Integer, primary_key=True, index=True)
    club_id = Column(Integer, ForeignKey("clubs.id"), nullable=True, index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    filename = Column(String(255), nullable=True)
    total_rows = Column(Integer, default=0, nullable=False)
    rows_created = Column(Integer, default=0, nullable=False)
    rows_updated = Column(Integer, default=0, nullable=False)
    rows_skipped = Column(Integer, default=0, nullable=False)
    rows_failed = Column(Integer, default=0, nullable=False)
    errors = Column(JSON, default=list, nullable=False)
    status = Column(String(20), default="DONE", nullable=False)  # PROCESSING, DONE, FAILED

    # Relationships
    club = relationship("Club", back_populates="csv_imports")
    uploader = relationship("User", foreign_keys=[uploaded_by])
