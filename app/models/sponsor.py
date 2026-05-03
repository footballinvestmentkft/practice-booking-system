"""Sponsor model — organizer/partner entity for Promotion Events + Audience Import"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, ForeignKey, Text, UniqueConstraint
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

    contacts         = relationship("SponsorContact", back_populates="sponsor",
                                    cascade="all, delete-orphan")
    creator          = relationship("User", foreign_keys=[created_by])
    promotion_events = relationship("Semester", back_populates="organizer_sponsor",
                                   foreign_keys="Semester.organizer_sponsor_id")
    csv_imports      = relationship("CsvImportLog", back_populates="sponsor",
                                    foreign_keys="CsvImportLog.sponsor_id")
    audience_entries = relationship("SponsorAudienceEntry", back_populates="sponsor",
                                    cascade="all, delete-orphan")
    campaigns        = relationship("SponsorCampaign", back_populates="sponsor",
                                    cascade="all, delete-orphan",
                                    order_by="SponsorCampaign.created_at.desc()")


class SponsorCampaign(Base):
    """One audience campaign / import event per sponsor.

    Each CSV import is tied to a campaign.  The same email can appear in
    multiple campaigns for the same sponsor (UNIQUE is now per campaign).
    """
    __tablename__ = "sponsor_campaigns"

    id            = Column(Integer, primary_key=True, index=True)
    sponsor_id    = Column(Integer, ForeignKey("sponsors.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    name          = Column(String(200), nullable=False)
    campaign_type = Column(String(30), nullable=False, default="IMPORT")
    event_date    = Column(Date, nullable=True)
    status        = Column(String(20), nullable=False, default="ACTIVE")
    notes         = Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    sponsor   = relationship("Sponsor", back_populates="campaigns")
    entries   = relationship("SponsorAudienceEntry", back_populates="campaign",
                             cascade="all, delete-orphan")
    creator   = relationship("User", foreign_keys=[created_by])
    semesters = relationship("Semester", foreign_keys="Semester.organizer_campaign_id",
                             back_populates="organizer_campaign")


class SponsorContact(Base):
    __tablename__ = "sponsor_contacts"

    id         = Column(Integer, primary_key=True, index=True)
    sponsor_id = Column(Integer, ForeignKey("sponsors.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    name       = Column(String(100), nullable=False)
    email      = Column(String(200), nullable=True)
    phone      = Column(String(50),  nullable=True)
    role       = Column(String(50),  nullable=True)
    is_primary = Column(Boolean, default=False, nullable=False)

    sponsor = relationship("Sponsor", back_populates="contacts")


class SponsorAudienceEntry(Base):
    """One prospect/audience record per (campaign, email).

    Import lifecycle: CSV → preview (no DB) → apply (upsert here).
    User creation is a separate explicit admin action — never automatic.
    The same email can appear in multiple campaigns for the same sponsor.
    """
    __tablename__ = "sponsor_audience_entries"
    __table_args__ = (
        UniqueConstraint("campaign_id", "email", name="uq_campaign_entry_email"),
    )

    id            = Column(Integer, primary_key=True, index=True)
    sponsor_id    = Column(Integer, ForeignKey("sponsors.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    campaign_id   = Column(Integer, ForeignKey("sponsor_campaigns.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    import_log_id = Column(Integer, ForeignKey("csv_import_logs.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                           nullable=True, index=True)

    # Identity
    first_name    = Column(String(100), nullable=False)
    last_name     = Column(String(100), nullable=False)
    email         = Column(String(200), nullable=False)
    phone         = Column(String(50),  nullable=True)
    date_of_birth = Column(Date,        nullable=True)

    # Segmentation — canonical PRE/YOUTH/AMATEUR/PRO or NULL
    age_category   = Column(String(20),  nullable=True)
    age_raw        = Column(String(30),  nullable=True)   # original CSV value, audit only
    target_segment = Column(String(200), nullable=True)
    campaign_source= Column(String(200), nullable=True)

    # Consent
    consent_given  = Column(Boolean,     nullable=False, default=False)
    consent_source = Column(String(300), nullable=True)

    # Parental (for PRE / under-13)
    parent_email   = Column(String(200), nullable=True)

    # Tournament-ready fields (P2-D)
    position       = Column(String(30),  nullable=True)   # STRIKER|MIDFIELDER|DEFENDER|GOALKEEPER
    foot_dominance = Column(Integer,     nullable=True)   # 0–100; 0=left, 100=right

    # Status: ACTIVE | SUPPRESSED | UNSUBSCRIBED | DELETED
    status = Column(String(20), nullable=False, default="SUPPRESSED")
    notes  = Column(Text,       nullable=True)

    # Audit — import
    imported_at      = Column(DateTime(timezone=True),
                              server_default=func.now(), nullable=False)
    last_imported_at = Column(DateTime(timezone=True), nullable=True)
    imported_by      = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                              nullable=True)

    # Audit — promote
    promoted_at = Column(DateTime(timezone=True), nullable=True)
    promoted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    sponsor    = relationship("Sponsor", back_populates="audience_entries")
    campaign   = relationship("SponsorCampaign", back_populates="entries")
    import_log = relationship("CsvImportLog", foreign_keys=[import_log_id])
    user       = relationship("User", foreign_keys=[user_id])
    importer   = relationship("User", foreign_keys=[imported_by])
    promoter   = relationship("User", foreign_keys=[promoted_by])
