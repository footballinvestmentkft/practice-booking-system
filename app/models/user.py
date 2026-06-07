from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Enum, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum
import uuid
from typing import Optional

from ..database import Base
from .specialization import SpecializationType


class UserRole(enum.Enum):
    ADMIN          = "admin"
    SPORT_DIRECTOR = "sport_director"  # Location-level: manages all campuses at a location
    INSTRUCTOR     = "instructor"
    STUDENT        = "student"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    nickname = Column(String, nullable=True)

    # 🆕 NEW: Separate first/last name for better data structure
    first_name = Column(String, nullable=True, comment="User first name (given name)")
    last_name = Column(String, nullable=True, comment="User last name (family name)")

    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.STUDENT)
    is_active = Column(Boolean, default=True)
    onboarding_completed = Column(Boolean, default=False, comment="Set to True when student completes FIRST license onboarding (motivation questionnaire). Note: UserLicense.onboarding_completed tracks EACH specialization separately.")
    phone = Column(String, nullable=True)
    emergency_contact = Column(String, nullable=True)
    emergency_phone = Column(String, nullable=True)
    date_of_birth = Column(DateTime, nullable=True)
    medical_notes = Column(String, nullable=True)
    interests = Column(String, nullable=True)  # JSON string of interests array
    position = Column(String, nullable=True)  # Football position (goalkeeper, defender, midfielder, forward, coach)

    # 🆕 NEW: Additional profile fields
    nationality = Column(String, nullable=True, comment="Primary nationality ISO 3166-1 alpha-2 (e.g. HU, DE)")
    secondary_nationality = Column(String, nullable=True, comment="Optional second nationality ISO 3166-1 alpha-2 (e.g. BR)")
    gender = Column(String, nullable=True, comment="User's gender (Male, Female, Non-binary, Other)")
    current_location = Column(String, nullable=True, comment="User's current location (e.g., Budapest, Hungary)")

    # 🆕 NEW: Address fields for invoicing and registration
    street_address = Column(String, nullable=True, comment="Street address (e.g., Main Street 123)")
    city = Column(String, nullable=True, comment="City name")
    postal_code = Column(String, nullable=True, comment="Postal/ZIP code")
    country = Column(String, nullable=True, comment="Country name")
    
    # 🎓 NEW: Specialization field (nullable for backward compatibility)
    specialization = Column(
        Enum(SpecializationType), 
        nullable=True,
        comment="User's chosen specialization track (Player/Coach)"
    )
    
    # 💰 NEW: Payment verification fields
    payment_verified = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether student has paid semester fees"
    )
    payment_verified_at = Column(
        DateTime,
        nullable=True,
        comment="Timestamp when payment was verified"
    )
    payment_verified_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
        comment="Admin who verified the payment"
    )

    # 💳 CENTRALIZED CREDIT SYSTEM: User-level credits (spec-independent)
    credit_balance = Column(
        Integer,
        nullable=False,
        default=0,
        comment="Current available credits (can be used across all specializations)"
    )
    credit_purchased = Column(
        Integer,
        nullable=False,
        default=0,
        comment="Total credits purchased by this user (for transaction history)"
    )
    credit_payment_reference = Column(
        String(50),
        nullable=True,
        unique=True,
        comment="Unique payment reference code for credit purchases (közlemény)"
    )

    # ⭐ XP SYSTEM: Experience Points (separate from credits)
    xp_balance = Column(
        Integer,
        nullable=False,
        default=0,
        comment="Current XP (Experience Points) - earned through training and tournaments"
    )

    # 🪪 ACADEMY ID: Phase 2A — lfa_academy_id + public_token
    # lfa_academy_id: human-readable, shown on the card (e.g. LFA-2026-00142)
    # public_token:   non-guessable UUID — used exclusively in QR verify URL
    #   /verify/{public_token}  — never log, never show to other users.
    lfa_academy_id = Column(
        String(20),
        unique=True,
        nullable=True,  # nullable until backfill completes; lazy-assigned on first /me/academy-id call
        index=True,
        comment="Human-readable Academy ID: LFA-YYYY-NNNNN",
    )
    public_token = Column(
        UUID(as_uuid=True),
        unique=True,
        nullable=True,  # DB default gen_random_uuid() set in migration
        index=True,
        default=uuid.uuid4,
        comment="Non-guessable UUID for /verify/{token} QR URL — do not log",
    )

    # 🪪 PROFILE PHOTO: Academy ID Card photo (Phase 1)
    # Managed via POST/DELETE /api/v1/users/me/profile-photo.
    # Background removal pipeline mirrors mood photo flow:
    #   status: none → uploaded → processing → ready/failed
    #   processed_url: NULL until BG_REMOVAL_PROCESSOR=rembg produces transparent PNG.
    profile_photo_url = Column(
        String(512),
        nullable=True,
        comment="Raw uploaded profile photo URL (app/static/uploads/profile_photos/)"
    )
    profile_photo_processed_url = Column(
        String(512),
        nullable=True,
        comment="Background-removed transparent PNG — NULL until rembg processor runs"
    )
    profile_photo_status = Column(
        String(20),
        nullable=True,
        comment="none/uploaded/processing/ready/failed — NULL treated as none"
    )

    # 📄 NEW: NDA acceptance fields
    nda_accepted = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether student has accepted the NDA"
    )
    nda_accepted_at = Column(
        DateTime,
        nullable=True,
        comment="Timestamp when NDA was accepted"
    )
    nda_ip_address = Column(
        String,
        nullable=True,
        comment="IP address from which NDA was accepted"
    )

    # 👨‍👩‍👧 NEW: Parental consent fields (required for LFA_COACH under 18)
    parental_consent = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether parental consent has been given (required for users under 18 in LFA_COACH)"
    )
    parental_consent_at = Column(
        DateTime,
        nullable=True,
        comment="Timestamp when parental consent was given"
    )
    parental_consent_by = Column(
        String,
        nullable=True,
        comment="Name of parent/guardian who gave consent"
    )

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    creator = relationship("User", remote_side=[id], foreign_keys=[created_by], post_update=True)
    created_users = relationship("User", foreign_keys=[created_by], remote_side=[created_by], overlaps="creator", post_update=True)
    
    # 💰 NEW: Payment verification relationships
    payment_verifier = relationship("User", remote_side=[id], foreign_keys=[payment_verified_by], post_update=True)
    groups = relationship("Group", secondary="group_users", back_populates="users")
    bookings = relationship("Booking", back_populates="user")
    attendances = relationship("Attendance", foreign_keys="Attendance.user_id", back_populates="user")
    feedbacks = relationship("Feedback", back_populates="user")
    notifications = relationship("Notification", back_populates="user")
    taught_sessions = relationship("Session", back_populates="instructor")
    marked_attendances = relationship("Attendance", foreign_keys="Attendance.marked_by", back_populates="marker")
    
    # Project relationships
    instructed_projects = relationship("Project", back_populates="instructor")
    project_enrollments = relationship("ProjectEnrollment", back_populates="user")
    
    # Gamification relationships (will be added after UserAchievement/UserStats are defined)

    # 👨‍🏫 NEW: Instructor specialization qualifications
    instructor_specializations = relationship("InstructorSpecialization",
                                             foreign_keys="InstructorSpecialization.user_id",
                                             back_populates="instructor",
                                             cascade="all, delete-orphan")

    # Message relationships
    sent_messages = relationship("Message", back_populates="sender", foreign_keys="Message.sender_id")
    received_messages = relationship("Message", back_populates="recipient", foreign_keys="Message.recipient_id")

    # Semester enrollment relationships
    semester_enrollments = relationship("SemesterEnrollment", foreign_keys="SemesterEnrollment.user_id", back_populates="user")

    # Invoice request relationships
    invoice_requests = relationship("InvoiceRequest", back_populates="user", cascade="all, delete-orphan")

    # Invitation code relationships
    redeemed_invitation_codes = relationship(
        "InvitationCode",
        foreign_keys="InvitationCode.used_by_user_id",
        back_populates="used_by_user"
    )
    created_invitation_codes = relationship(
        "InvitationCode",
        foreign_keys="InvitationCode.created_by_admin_id",
        back_populates="created_by_admin"
    )

    # 💰 Credit transaction relationships (user-level rewards and purchases)
    credit_transactions = relationship(
        "CreditTransaction",
        foreign_keys="CreditTransaction.user_id",
        back_populates="user",
        passive_deletes=True,
    )

    # ⭐ XP transaction relationships (experience points history)
    xp_transactions = relationship(
        "XPTransaction",
        foreign_keys="XPTransaction.user_id",
        back_populates="user"
    )

    # 🎯 Skill reward relationships (tournament and training skill points)
    skill_rewards = relationship(
        "SkillReward",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    # 🏆 Tournament participation & badge relationships
    tournament_participations = relationship(
        "TournamentParticipation",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    tournament_badges = relationship(
        "TournamentBadge",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    # 🎓 NEW: Specialization helper properties and methods
    @property
    def specialization_display(self) -> str:
        """Get user-friendly specialization display name (HYBRID: loads from JSON)"""
        if not self.specialization:
            return "Nincs kiválasztva"

        loader = SpecializationConfigLoader()
        try:
            display_info = loader.get_display_info(self.specialization)
            return display_info.get('name', str(self.specialization.value))
        except Exception:
            return str(self.specialization.value)

    @property
    def specialization_icon(self) -> str:
        """Get specialization emoji icon (HYBRID: loads from JSON)"""
        if not self.specialization:
            return "❓"

        loader = SpecializationConfigLoader()
        try:
            display_info = loader.get_display_info(self.specialization)
            return display_info.get('icon', '🎯')
        except Exception:
            return "🎯"
    
    @property
    def has_specialization(self) -> bool:
        """Check if user has chosen a specialization"""
        return self.specialization is not None
    
    # 🎓 NEW: Session access logic with specialization (preserves Mbappé logic)
    def can_access_session(self, session) -> bool:
        """
        Check if user can access session based on specialization
        ⚠️ CRITICAL: This preserves Mbappé cross-semester logic
        """
        # Cross-semester logic for Mbappé (preserve existing logic)
        if self.email == "mbappe@lfa.com":
            return True  # Mbappé can access ALL sessions
        
        # If user has no specialization, allow access (backward compatibility)
        if not self.specialization:
            return True
            
        # If session has no specialization requirement, allow access
        if not hasattr(session, 'target_specialization') or not session.target_specialization:
            return True
            
        # If session is mixed specialization, allow access
        if hasattr(session, 'mixed_specialization') and session.mixed_specialization:
            return True
            
        # Check specialization match
        return session.target_specialization == self.specialization
    
    # 🎓 NEW: Project access logic with specialization  
    def can_enroll_in_project(self, project) -> bool:
        """Check if user can enroll in project based on specialization"""
        # If user has no specialization, allow enrollment (backward compatibility)
        if not self.specialization:
            return True
            
        # If project has no specialization requirement, allow enrollment
        if not hasattr(project, 'target_specialization') or not project.target_specialization:
            return True
            
        # If project is mixed specialization, allow enrollment
        if hasattr(project, 'mixed_specialization') and project.mixed_specialization:
            return True
            
        # Check specialization match
        return project.target_specialization == self.specialization
    
    # 💰 NEW: Payment verification helper methods
    @property
    def payment_status_display(self) -> str:
        """Get user-friendly payment status display"""
        if self.payment_verified:
            return "✅ Verified"
        return "❌ Not Verified"
    
    @property
    def can_enroll_in_semester(self) -> bool:
        """Check if user can enroll in semester content based on payment"""
        # Admins and instructors can always enroll
        if self.role in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
            return True
        
        # Students must have payment verified
        return self.payment_verified
    
    def verify_payment(self, admin_user: 'User') -> None:
        """Mark payment as verified by admin"""
        self.payment_verified = True
        self.payment_verified_at = datetime.now(timezone.utc)
        self.payment_verified_by = admin_user.id
    
    def unverify_payment(self) -> None:
        """Mark payment as not verified"""
        self.payment_verified = False
        self.payment_verified_at = None
        self.payment_verified_by = None

    # 🎓 SEMESTER ENROLLMENT HELPERS
    def get_active_semester_enrollment(self, db_session, semester_id: Optional[int] = None):
        """
        Get user's active, paid enrollment for a specific semester.
        If semester_id not provided, finds enrollment matching user's current specialization.

        Returns:
            SemesterEnrollment or None
        """
        if self.role in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
            return None

        # If semester_id provided, use it directly
        if semester_id is not None:
            # Find active, paid enrollment for specific semester
            enrollment = db_session.query(SemesterEnrollment).filter(
                SemesterEnrollment.user_id == self.id,
                SemesterEnrollment.semester_id == semester_id,
                SemesterEnrollment.payment_verified == True,
                SemesterEnrollment.is_active == True
            ).first()
            return enrollment

        # No semester_id provided - find enrollment matching user's current specialization
        # This handles the case where multiple active semesters exist for different specializations
        if self.specialization:
            # Convert enum to string for database query
            spec_value = self.specialization.value if hasattr(self.specialization, 'value') else self.specialization

            # Find active enrollment matching user's specialization through user_license
            enrollment = db_session.query(SemesterEnrollment).join(
                UserLicense, SemesterEnrollment.user_license_id == UserLicense.id
            ).filter(
                SemesterEnrollment.user_id == self.id,
                UserLicense.specialization_type == spec_value,
                SemesterEnrollment.payment_verified == True,
                SemesterEnrollment.is_active == True
            ).order_by(SemesterEnrollment.enrolled_at.desc()).first()

            if enrollment:
                return enrollment

        # Fallback: no specialization, try to find ANY active enrollment
        enrollment = db_session.query(SemesterEnrollment).filter(
            SemesterEnrollment.user_id == self.id,
            SemesterEnrollment.payment_verified == True,
            SemesterEnrollment.is_active == True
        ).order_by(SemesterEnrollment.enrolled_at.desc()).first()

        return enrollment

    def has_active_semester_enrollment(self, db_session, semester_id: Optional[int] = None) -> bool:
        """
        Check if user has an active, paid enrollment for a semester.

        Returns:
            bool - True if user has active enrollment or is admin/instructor
        """
        # Admins and instructors always have access
        if self.role in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
            return True

        enrollment = self.get_active_semester_enrollment(db_session, semester_id)
        return enrollment is not None

    # 👨‍👩‍👧 NEW: Parental consent helper methods
    @property
    def age(self) -> Optional[int]:
        """Calculate user's age in years"""
        if not self.date_of_birth:
            return None
        today = datetime.now(timezone.utc).date()
        dob = self.date_of_birth.date() if isinstance(self.date_of_birth, datetime) else self.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return age

    @property
    def is_minor(self) -> bool:
        """Check if user is under 18 years old"""
        age = self.age
        return age is not None and age < 18

    @property
    def needs_parental_consent(self) -> bool:
        """Check if user needs parental consent for LFA_COACH specialization"""
        # Only needed for LFA_COACH specialization
        if self.specialization != SpecializationType.LFA_COACH:
            return False
        # And only if user is under 18
        return self.is_minor

    def give_parental_consent(self, parent_name: str) -> None:
        """Record parental consent"""
        self.parental_consent = True
        self.parental_consent_at = datetime.now(timezone.utc)
        self.parental_consent_by = parent_name

    def revoke_parental_consent(self) -> None:
        """Revoke parental consent"""
        self.parental_consent = False
        self.parental_consent_at = None
        self.parental_consent_by = None

    # 👨‍🏫 NEW: Instructor Specialization Helper Methods
    def get_teaching_specializations(self) -> list:
        """
        Get list of ACTIVE specializations this instructor is qualified to teach

        Returns:
            List of SpecializationType values (e.g., ['GANCUJU_PLAYER', 'LFA_COACH'])
        """
        # Only INSTRUCTOR role can teach (ADMIN is pure admin)
        if self.role != UserRole.INSTRUCTOR:
            return []

        return [
            spec.specialization
            for spec in self.instructor_specializations
            if spec.is_active
        ]

    def get_all_teaching_specializations(self) -> list:
        """
        Get ALL specializations (active + inactive) with their status

        Returns:
            List of dicts: [{'specialization': 'GANCUJU_PLAYER', 'is_active': True}, ...]
        """
        # Only INSTRUCTOR role can teach (ADMIN is pure admin)
        if self.role != UserRole.INSTRUCTOR:
            return []

        return [
            {
                'specialization': spec.specialization,
                'is_active': spec.is_active
            }
            for spec in self.instructor_specializations
        ]

    def can_teach_specialization(self, specialization) -> bool:
        """
        Check if instructor/admin is qualified to teach a specific specialization

        Args:
            specialization: SpecializationType enum or string

        Returns:
            True if instructor/admin is qualified and active
        """
        # Only INSTRUCTOR role can teach (ADMIN is pure admin)
        if self.role != UserRole.INSTRUCTOR:
            return False

        # Convert to string if enum
        spec_str = specialization.value if hasattr(specialization, 'value') else str(specialization)

        return any(
            spec.specialization == spec_str and spec.is_active
            for spec in self.instructor_specializations
        )

    def add_teaching_specialization(self, specialization, certified_by_id=None, notes=None):
        """
        Add a new teaching qualification for instructor/admin

        Note: This method only creates the object. You must commit() separately!
        """
        if self.role != UserRole.INSTRUCTOR:
            raise ValueError("Only instructors can have teaching specializations")

        spec_str = specialization.value if hasattr(specialization, 'value') else str(specialization)

        # Check if already exists
        existing = any(
            spec.specialization == spec_str
            for spec in self.instructor_specializations
        )

        if existing:
            return None  # Already exists

        new_spec = InstructorSpecialization(
            user_id=self.id,
            specialization=spec_str,
            certified_by=certified_by_id,
            notes=notes
        )

        self.instructor_specializations.append(new_spec)
        return new_spec