"""Sponsor model — organizer/partner entity for Promotion Events"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Sponsor(Base):
    __tablename__ = "sponsors"

    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String(100), nullable=False)
    code           = Column(String(20), unique=True, nullable=False, index=True)
    brand_category = Column(String(50), nullable=True)
    city           = Column(String(100), nullable=True)
    country        = Column(String(50), nullable=True)
    contact_email  = Column(String(200), nullable=True)
    website        = Column(String(255), nullable=True)
    notes          = Column(Text, nullable=True)
    is_active      = Column(Boolean, default=True, nullable=False)
    created_at     = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by     = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    contacts         = relationship("SponsorContact", back_populates="sponsor", cascade="all, delete-orphan")
    creator          = relationship("User", foreign_keys=[created_by])
    promotion_events = relationship("Semester", back_populates="organizer_sponsor",
                                   foreign_keys="Semester.organizer_sponsor_id")


class SponsorContact(Base):
    __tablename__ = "sponsor_contacts"

    id         = Column(Integer, primary_key=True, index=True)
    sponsor_id = Column(Integer, ForeignKey("sponsors.id", ondelete="CASCADE"), nullable=False, index=True)
    name       = Column(String(100), nullable=False)
    email      = Column(String(200), nullable=True)
    phone      = Column(String(50), nullable=True)
    role       = Column(String(50), nullable=True)
    is_primary = Column(Boolean, default=False, nullable=False)

    sponsor = relationship("Sponsor", back_populates="contacts")
