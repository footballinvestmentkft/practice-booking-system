"""
🏮 GānCuju™️©️ License System Models
Marketing-oriented license progression system with cultural narratives
"""
import enum
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Boolean, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from ..database import Base


class LicenseType(enum.Enum):
    """License specialization types"""
    COACH = "COACH"
    PLAYER = "PLAYER"
    INTERNSHIP = "INTERNSHIP"


class LicenseLevel(enum.Enum):
    """All license levels across specializations"""
    
    # COACH LEVELS - LFA System (8 levels)
    COACH_LFA_PRE_ASSISTANT = "coach_lfa_pre_assistant"
    COACH_LFA_PRE_HEAD = "coach_lfa_pre_head"
    COACH_LFA_YOUTH_ASSISTANT = "coach_lfa_youth_assistant"
    COACH_LFA_YOUTH_HEAD = "coach_lfa_youth_head"
    COACH_LFA_AMATEUR_ASSISTANT = "coach_lfa_amateur_assistant"
    COACH_LFA_AMATEUR_HEAD = "coach_lfa_amateur_head"
    COACH_LFA_PRO_ASSISTANT = "coach_lfa_pro_assistant"
    COACH_LFA_PRO_HEAD = "coach_lfa_pro_head"
    
    # PLAYER LEVELS - GānCuju™️©️ System (8 levels)
    PLAYER_BAMBOO_STUDENT = "player_bamboo_student"          # 🤍 Bambusz Tanítvány (Fehér)
    PLAYER_MORNING_DEW = "player_morning_dew"                # 💛 Hajnali Harmat (Sárga)
    PLAYER_FLEXIBLE_REED = "player_flexible_reed"            # 💚 Rugalmas Nád (Zöld)
    PLAYER_SKY_RIVER = "player_sky_river"                    # 💙 Égi Folyó (Kék)
    PLAYER_STRONG_ROOT = "player_strong_root"                # 🤎 Erős Gyökér (Barna)
    PLAYER_WINTER_MOON = "player_winter_moon"                # 🩶 Téli Hold (Sötétszürke)
    PLAYER_MIDNIGHT_GUARDIAN = "player_midnight_guardian"    # 🖤 Éjfél Őrzője (Fekete)
    PLAYER_DRAGON_WISDOM = "player_dragon_wisdom"           # ❤️ Sárkány Bölcsesség (Vörös)
    
    # INTERN LEVELS - IT Career System (5 levels)
    INTERN_JUNIOR = "intern_junior"                          # 🔰 Junior Intern
    INTERN_MID_LEVEL = "intern_mid_level"                    # 📈 Mid-level Intern
    INTERN_SENIOR = "intern_senior"                          # 🎯 Senior Intern
    INTERN_LEAD = "intern_lead"                              # 👑 Lead Intern
    INTERN_PRINCIPAL = "intern_principal"                    # 🚀 Principal Intern


class LicenseMetadata(Base):
    """License level metadata with marketing content and visual assets"""
    __tablename__ = "license_metadata"

    id = Column(Integer, primary_key=True, index=True)
    specialization_type = Column(String(20), nullable=False)  # COACH, PLAYER, INTERNSHIP
    level_code = Column(String(50), nullable=False)           # e.g., 'player_bamboo_student'
    level_number = Column(Integer, nullable=False)            # 1-8 for most, 1-5 for intern
    
    # Display Information
    title = Column(String(100), nullable=False)               # "Bambusz Tanítvány"
    title_en = Column(String(100))                            # "Bamboo Student"
    subtitle = Column(String(200))                            # "A rugalmasság első leckéi"
    color_primary = Column(String(7), nullable=False)         # "#F8F8FF"
    color_secondary = Column(String(7))                       # "#E6E6FA"
    icon_emoji = Column(String(10))                           # "🤍"
    icon_symbol = Column(String(50))                          # CSS class or symbol
    
    # Marketing Content
    marketing_narrative = Column(Text)                        # Rich marketing description
    cultural_context = Column(Text)                           # Cultural/historical context
    philosophy = Column(Text)                                 # Philosophical aspects
    
    # Visual Assets
    background_gradient = Column(String(200))                 # CSS gradient definition
    css_class = Column(String(50))                           # CSS class for styling
    image_url = Column(String(500))                          # Optional image asset
    
    # Requirements
    advancement_criteria = Column(JSON)                      # JSON structure of requirements
    time_requirement_hours = Column(Integer)                  # Minimum time requirement
    project_requirements = Column(JSON)                      # Project-based requirements
    evaluation_criteria = Column(JSON)                       # Evaluation criteria
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), 
                       onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "specialization_type": self.specialization_type,
            "level_code": self.level_code,
            "level_number": self.level_number,
            "title": self.title,
            "title_en": self.title_en,
            "subtitle": self.subtitle,
            "color_primary": self.color_primary,
            "color_secondary": self.color_secondary,
            "icon_emoji": self.icon_emoji,
            "icon_symbol": self.icon_symbol,
            "marketing_narrative": self.marketing_narrative,
            "cultural_context": self.cultural_context,
            "philosophy": self.philosophy,
            "background_gradient": self.background_gradient,
            "css_class": self.css_class,
            "image_url": self.image_url,
            "advancement_criteria": self.advancement_criteria,
            "time_requirement_hours": self.time_requirement_hours,
            "project_requirements": self.project_requirements,
            "evaluation_criteria": self.evaluation_criteria
        }


class UserLicense(Base):
    """Track user license progression for each specialization"""
    __tablename__ = "user_licenses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    specialization_type = Column(String(20), nullable=False)  # COACH, PLAYER, INTERNSHIP
    current_level = Column(Integer, nullable=False, default=1)
    max_achieved_level = Column(Integer, nullable=False, default=1)
    started_at = Column(DateTime, nullable=False)
    last_advanced_at = Column(DateTime)
    instructor_notes = Column(Text)                           # Instructor feedback/notes

    # 💳 Payment tracking
    payment_reference_code = Column(String(50), nullable=True, unique=True, index=True,
                                   comment="Unique payment reference for bank transfer (e.g., INT-2025-002-X7K9)")
    payment_verified = Column(Boolean, nullable=False, default=False,
                              comment="Whether admin verified payment received for this license")
    payment_verified_at = Column(DateTime, nullable=True,
                                 comment="When admin verified the payment")

    # 🎯 NEW: Onboarding tracking
    onboarding_completed = Column(Boolean, nullable=False, default=False,
                                   comment="Whether student completed basic onboarding for this specialization")
    onboarding_completed_at = Column(DateTime, nullable=True,
                                      comment="When student completed onboarding")

    # ✅ License Activity Status
    is_active = Column(Boolean, nullable=False, default=True,
                      comment="Whether this license is currently active (can be used for teaching/enrollment)")

    # 📅 License Expiration & Renewal (Fase 2)
    issued_at = Column(DateTime, nullable=True,
                      comment="Official license issuance date (e.g., 2014-01-01 for Grand Master)")
    expires_at = Column(DateTime, nullable=True,
                       comment="License expiration date (null = no expiration yet, perpetual until first renewal)")
    last_renewed_at = Column(DateTime, nullable=True,
                            comment="When license was last renewed")
    renewal_cost = Column(Integer, nullable=False, default=1000,
                         comment="Credit cost to renew this license (default: 1000 credits)")

    # 📊 NEW: Motivation scoring (admin/instructor only - NOT visible to student)
    motivation_scores = Column(JSON, nullable=True,
                               comment="Motivation assessment scores (1-5 scale) - filled by admin/instructor")
    average_motivation_score = Column(Float, nullable=True,
                                      comment=(
                                          "Mean of the 29 onboarding self-assessment values (0-100 scale). "
                                          "Motivational profile indicator only. "
                                          "Never read by skill calculation services (EMA, baseline extraction, "
                                          "or tournament reward formulas)."
                                      ))
    motivation_last_assessed_at = Column(DateTime, nullable=True,
                                         comment="When motivation was last assessed")
    motivation_assessed_by = Column(Integer, ForeignKey("users.id"), nullable=True,
                                    comment="Admin/instructor who assessed motivation")

    # ⚽ LFA PLAYER SKILLS: 6 skill percentages (0.0-100.0) for LFA_PLAYER_* specializations
    # Format: {"heading": 75.0, "shooting": 60.0, "crossing": 55.0, "passing": 80.0, "dribbling": 70.0, "ball_control": 85.0}
    football_skills = Column(JSON, nullable=True,
                             comment="6 football skill percentages for LFA Player specializations (heading, shooting, crossing, passing, dribbling, ball_control)")
    player_card_photo_url = Column(String(512), nullable=True,
                                   comment="LFA Football Player spec-specific card photo URL (not global avatar)")
    card_photo_portrait_url = Column(String(512), nullable=True,
                                     comment="Portrait-crop photo for player card variants")
    card_photo_landscape_url = Column(String(512), nullable=True,
                                      comment="Landscape-crop photo for player card variants")
    # ── Welcome Card photos (fully independent from Player Card slots above) ──
    # NULL means "fall back to the corresponding Player Card field" at read time
    # (see _build_welcome_card_context in profile.py for the explicit fallback chain).
    wc_photo_url = Column(String(512), nullable=True,
                          comment="Welcome Card primary photo — independent from player_card_photo_url; NULL = fall back to player_card_photo_url")
    wc_photo_portrait_url = Column(String(512), nullable=True,
                                   comment="Welcome Card portrait photo — NULL = fall back to wc_photo_url then card_photo_portrait_url")
    wc_photo_landscape_url = Column(String(512), nullable=True,
                                    comment="Welcome Card landscape photo — NULL = fall back to wc_photo_url then card_photo_landscape_url")
    card_bg_compact_url = Column(String(512), nullable=True,
                                 comment="Background image for compact card variant")
    card_bg_showcase_url = Column(String(512), nullable=True,
                                  comment="Background image for showcase card variant")
    sponsor_logo_url = Column(String(512), nullable=True,
                              comment="Sponsor/partner logo URL for player card (square FClassic bottom slot)")
    card_compact_photo_position = Column(String(10), nullable=True, default="left",
                                         comment="Photo side for compact variant: left or right")
    card_compact_focus_x = Column(Integer, nullable=True, default=50,
                                   comment="Horizontal focus point % for compact photo (0-100)")
    card_compact_focus_y = Column(Integer, nullable=True, default=50,
                                   comment="Vertical focus point % for compact photo (0-100)")
    card_showcase_focus_x = Column(Integer, nullable=True, default=50,
                                    comment="Horizontal focus point % for showcase banner (0-100)")
    card_showcase_focus_y = Column(Integer, nullable=True, default=50,
                                    comment="Vertical focus point % for showcase banner (0-100)")

    # ⚽ DOMINANT FOOT SCORES: assessed strength 0.0–100.0 for each foot
    right_foot_score = Column(Float, nullable=True,
                              comment="Right-foot assessed strength 0.0–100.0 (NULL = not assessed)")
    left_foot_score  = Column(Float, nullable=True,
                              comment="Left-foot assessed strength 0.0–100.0 (NULL = not assessed)")

    # 🎨 CARD CUSTOMISATION: active theme/variant + unlocked lists
    card_theme = Column(String(50), nullable=True, default="default",
                        comment="Active card theme ID (default/midnight/arctic/gold/emerald/crimson)")
    card_variant = Column(String(50), nullable=True, default="fclassic",
                          comment="Active card variant ID (fclassic/compact/showcase)")
    unlocked_card_themes = Column(JSON, nullable=True, default=list,
                                  comment="List of unlocked card theme IDs")
    unlocked_card_variants = Column(JSON, nullable=True, default=list,
                                    comment="List of unlocked card variant IDs")
    public_card_platform = Column(String(50), nullable=True, default=None,
                                   comment="Saved public card platform ID (NULL = default)")

    # 📣 PUBLISHED PUBLIC STATE: explicitly published snapshot, decoupled from editor draft.
    # The public card route reads ONLY from these fields.  The editor writes to
    # card_theme/card_variant/public_card_platform (draft); a "Publish Card" action
    # copies draft → published so the public URL stays stable while the user edits.
    published_card_theme    = Column(String(50), nullable=True, default="default",
                                     comment="Published public card theme (stable; set by Publish action)")
    published_card_variant  = Column(String(50), nullable=True, default="fclassic",
                                     comment="Published public card variant (stable; set by Publish action)")
    published_card_platform = Column(String(50), nullable=True, default=None,
                                     comment="Published public card platform (NULL=default; stable)")

    skills_last_updated_at = Column(DateTime, nullable=True,
                                    comment="When skills were last updated")
    skills_updated_by = Column(Integer, ForeignKey("users.id"), nullable=True,
                               comment="Instructor who last updated skills")

    # 💰 CREDIT SYSTEM: Prepaid enrollment credits
    credit_balance = Column(Integer, nullable=False, default=0,
                           comment="Current credit balance available for enrollments")
    credit_purchased = Column(Integer, nullable=False, default=0,
                              comment="Total credits purchased (lifetime)")
    credit_expires_at = Column(DateTime, nullable=True,
                               comment="Credit expiration date (2 years from purchase)")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                       onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint('user_id', 'specialization_type', name='uq_user_license_spec'),
    )

    # Relationships
    user = relationship("User", back_populates="licenses", foreign_keys="[UserLicense.user_id]")
    assessor = relationship("User", foreign_keys="[UserLicense.motivation_assessed_by]")
    skills_updater = relationship("User", foreign_keys="[UserLicense.skills_updated_by]")
    progressions = relationship("LicenseProgression", back_populates="user_license",
                               cascade="all, delete-orphan")
    semester_enrollments = relationship("SemesterEnrollment", back_populates="user_license",
                                       cascade="all, delete-orphan")
    belt_promotions = relationship("BeltPromotion", back_populates="user_license",
                                   cascade="all, delete-orphan")
    credit_transactions = relationship("CreditTransaction", back_populates="user_license",
                                      cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "specialization_type": self.specialization_type,
            "current_level": self.current_level,
            "max_achieved_level": self.max_achieved_level,
            "is_active": self.is_active,  # ✅ ADDED: Include is_active flag
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "issued_at": self.issued_at.isoformat() if self.issued_at else None,  # ✅ License issuance date
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,  # ✅ License expiration date
            "last_advanced_at": self.last_advanced_at.isoformat() if self.last_advanced_at else None,
            "instructor_notes": self.instructor_notes
        }


class LicenseProgression(Base):
    """Track license advancement history"""
    __tablename__ = "license_progressions"

    id = Column(Integer, primary_key=True, index=True)
    user_license_id = Column(Integer, ForeignKey("user_licenses.id"), nullable=False)
    from_level = Column(Integer, nullable=False)
    to_level = Column(Integer, nullable=False)
    advanced_by = Column(Integer, ForeignKey("users.id"))     # Instructor who approved advancement
    advancement_reason = Column(Text)                         # Reason for advancement
    requirements_met = Column(Text)                           # Which requirements were satisfied
    advanced_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    user_license = relationship("UserLicense", back_populates="progressions")
    instructor = relationship("User", foreign_keys=[advanced_by])

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "user_license_id": self.user_license_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "advanced_by": self.advanced_by,
            "advancement_reason": self.advancement_reason,
            "requirements_met": self.requirements_met,
            "advanced_at": self.advanced_at.isoformat() if self.advanced_at else None
        }


# Configure relationships after all models are defined
def configure_license_relationships():
    """Configure relationships between User and license models"""
    from .user import User
    
    # Add relationships to User model if not already present
    if not hasattr(User, 'licenses'):
        User.licenses = relationship("UserLicense", foreign_keys="UserLicense.user_id", back_populates="user")


class LicenseSystemHelper:
    """Helper class for license system operations"""

    @staticmethod
    def get_specialization_max_level(specialization: str, db = None) -> int:
        """
        Get maximum level for a specialization - DB is source of truth.

        P0 FIX: Changed from hardcoded dict to dynamic DB query.
        Fallback to defaults only if DB unavailable.

        Args:
            specialization: PLAYER, COACH, or INTERNSHIP
            db: Optional SQLAlchemy session for DB query

        Returns:
            Maximum level count from DB or fallback default
        """
        # ✅ FIX: spec.max_levels was removed in P0 security fix
        # Use hardcoded defaults directly (DB table no longer has max_levels column)

        # Normalize specialization_type format (handle both "LFA_COACH" and "COACH")
        spec_normalized = specialization.upper()
        if spec_normalized.startswith("LFA_"):
            spec_normalized = spec_normalized.replace("LFA_FOOTBALL_PLAYER", "PLAYER").replace("LFA_COACH", "COACH")

        max_levels_map = {
            "COACH": 8,
            "PLAYER": 8,
            "INTERNSHIP": 3,
            # Legacy/alternative names
            "LFA_COACH": 8,
            "LFA_FOOTBALL_PLAYER": 8
        }
        return max_levels_map.get(spec_normalized, 8)  # Default to 8 if unknown
    
    @staticmethod
    def get_level_metadata(specialization: str, level: int) -> Optional[str]:
        """Get level code for specialization and level number"""
        level_maps = {
            "COACH": {
                1: LicenseLevel.COACH_LFA_PRE_ASSISTANT.value,
                2: LicenseLevel.COACH_LFA_PRE_HEAD.value,
                3: LicenseLevel.COACH_LFA_YOUTH_ASSISTANT.value,
                4: LicenseLevel.COACH_LFA_YOUTH_HEAD.value,
                5: LicenseLevel.COACH_LFA_AMATEUR_ASSISTANT.value,
                6: LicenseLevel.COACH_LFA_AMATEUR_HEAD.value,
                7: LicenseLevel.COACH_LFA_PRO_ASSISTANT.value,
                8: LicenseLevel.COACH_LFA_PRO_HEAD.value,
            },
            "PLAYER": {
                1: LicenseLevel.PLAYER_BAMBOO_STUDENT.value,
                2: LicenseLevel.PLAYER_MORNING_DEW.value,
                3: LicenseLevel.PLAYER_FLEXIBLE_REED.value,
                4: LicenseLevel.PLAYER_SKY_RIVER.value,
                5: LicenseLevel.PLAYER_STRONG_ROOT.value,
                6: LicenseLevel.PLAYER_WINTER_MOON.value,
                7: LicenseLevel.PLAYER_MIDNIGHT_GUARDIAN.value,
                8: LicenseLevel.PLAYER_DRAGON_WISDOM.value,
            },
            "INTERNSHIP": {
                1: LicenseLevel.INTERN_JUNIOR.value,
                2: LicenseLevel.INTERN_MID_LEVEL.value,
                3: LicenseLevel.INTERN_SENIOR.value,
                4: LicenseLevel.INTERN_LEAD.value,
                5: LicenseLevel.INTERN_PRINCIPAL.value,
            }
        }
        return level_maps.get(specialization, {}).get(level)
    
    @staticmethod
    def validate_advancement(current_level: int, target_level: int, max_level: int) -> tuple[bool, str]:
        """Validate if license advancement is possible"""
        if target_level <= current_level:
            return False, "Target level must be higher than current level"
        
        if target_level > current_level + 1:
            return False, "Can only advance one level at a time"
        
        if target_level > max_level:
            return False, f"Maximum level for this specialization is {max_level}"
        
        return True, "Advancement is valid"