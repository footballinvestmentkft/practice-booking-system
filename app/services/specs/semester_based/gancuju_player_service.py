"""
GanCuju Player Service - Semester-Based Specialization

This service handles all GanCuju Player-specific logic including:
- Semester enrollment with payment verification
- Belt-based progression (8 traditional belts)
- Ancient Chinese football philosophy and technique
- Competition tracking and teaching hours

Belt Progression (8 Levels):
1. Bamboo Disciple (White Belt) - Foundation
2. Dawn Dew (Yellow Belt) - Awakening
3. Flexible Reed (Green Belt) - Growth
4. Celestial River (Blue Belt) - Flow
5. Strong Root (Brown Belt) - Mastery
6. Winter Moon (Grey Belt) - Teaching
7. Midnight Guardian (Black Belt) - Expert
8. Dragon Wisdom (Red Belt) - Grand Master

Key Characteristics:
- SEMESTER-BASED: Semester enrollment REQUIRED with payment verification
- Belt progression system (NO skills tracking like LFA Player)
- Minimum age: 5 years old
- 100 credit cost for license creation
- Philosophy and technique focused (not pure sports)
"""

from typing import Tuple, Dict, Optional, List
from datetime import datetime
from sqlalchemy.orm import Session
from app.services.specs.base_spec import BaseSpecializationService
from app.models.user import User
from app.models.license import UserLicense
from app.models.belt_promotion import BeltPromotion
from app.models.semester_enrollment import SemesterEnrollment
from app.services.canonical_policy import CanonicalProgram
from app.services.program_eligibility_service import is_user_eligible_for_program


class GanCujuPlayerService(BaseSpecializationService):
    """
    Service for GanCuju Player specialization.

    Handles semester-based enrollment, belt progression, and ancient Chinese football training.
    """

    def __init__(self, db: Session = None):
        self.db = db

    # ========================================================================
    # BELT CONFIGURATION
    # ========================================================================

    BELT_LEVELS = [
        'BAMBOO_DISCIPLE',      # L1 - White
        'DAWN_DEW',             # L2 - Yellow
        'FLEXIBLE_REED',        # L3 - Green
        'CELESTIAL_RIVER',      # L4 - Blue
        'STRONG_ROOT',          # L5 - Brown
        'WINTER_MOON',          # L6 - Grey
        'MIDNIGHT_GUARDIAN',    # L7 - Black
        'DRAGON_WISDOM'         # L8 - Red (Grand Master)
    ]

    BELT_INFO = {
        'BAMBOO_DISCIPLE': {'name': 'Bamboo Disciple', 'color': 'White', 'stage': 'Foundation', 'level': 1},
        'DAWN_DEW': {'name': 'Dawn Dew', 'color': 'Yellow', 'stage': 'Awakening', 'level': 2},
        'FLEXIBLE_REED': {'name': 'Flexible Reed', 'color': 'Green', 'stage': 'Growth', 'level': 3},
        'CELESTIAL_RIVER': {'name': 'Celestial River', 'color': 'Blue', 'stage': 'Flow', 'level': 4},
        'STRONG_ROOT': {'name': 'Strong Root', 'color': 'Brown', 'stage': 'Mastery', 'level': 5},
        'WINTER_MOON': {'name': 'Winter Moon', 'color': 'Grey', 'stage': 'Teaching', 'level': 6},
        'MIDNIGHT_GUARDIAN': {'name': 'Midnight Guardian', 'color': 'Black', 'stage': 'Expert', 'level': 7},
        'DRAGON_WISDOM': {'name': 'Dragon Wisdom', 'color': 'Red', 'stage': 'Grand Master', 'level': 8}
    }

    MINIMUM_AGE = 5  # Minimum age for GanCuju Player

    # ========================================================================
    # OVERRIDE: BaseSpecializationService Methods
    # ========================================================================

    def is_semester_based(self) -> bool:
        """GanCuju Player is semester-based (enrollment required)"""
        return True

    def get_specialization_name(self) -> str:
        """Human-readable name"""
        return "GanCuju Player"

    # ========================================================================
    # AGE VALIDATION
    # ========================================================================

    def validate_age_eligibility(self, user, target_group: Optional[str] = None, db: Session = None) -> Tuple[bool, str]:
        """
        Validate if user's age meets minimum requirement for GanCuju.

        Args:
            user: User model instance
            target_group: Not used for GanCuju (no age groups)
            db: Database session

        Returns:
            Tuple of (is_eligible: bool, reason: str)
        """
        eligible, denial = is_user_eligible_for_program(
            db or self.db, user, CanonicalProgram.GANCUJU_PLAYER
        )
        return eligible, denial or "Eligible for GānCuju Player"

    # ========================================================================
    # SESSION BOOKING LOGIC
    # ========================================================================

    def can_book_session(self, user, session, db: Session) -> Tuple[bool, str]:
        """
        Check if GanCuju Player can book a session.

        Rules:
        1. User must have active license
        2. User must be enrolled in a semester with payment verified
        3. Session must be for GanCuju Player specialization

        Args:
            user: User model instance
            session: Session model instance
            db: Database session

        Returns:
            Tuple of (can_book: bool, reason: str)
        """
        # Check if user has active license
        has_license, error = self.validate_user_has_license(user, db)
        if not has_license:
            return False, error

        # Check semester enrollment
        enrollment = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.is_active == True
        ).first()

        if not enrollment:
            return False, "No active semester enrollment found. You must enroll in a GanCuju semester first."

        if not enrollment.payment_verified:
            return False, "Payment not verified. Please complete payment to access sessions."

        # Check if session is for GanCuju
        if not session.specialization_type or not session.specialization_type.startswith('GANCUJU'):
            return False, f"This session is not for GanCuju Player (session type: {session.specialization_type})"

        return True, "Eligible to book GanCuju session"

    # ========================================================================
    # ENROLLMENT REQUIREMENTS
    # ========================================================================

    def get_enrollment_requirements(self, user, db: Session) -> Dict:
        """
        Get what's needed for user to participate in GanCuju Player.

        Returns semester enrollment status and license info.

        Args:
            user: User model instance
            db: Database session

        Returns:
            Dictionary with structure:
            {
                "can_participate": bool,
                "missing_requirements": List[str],
                "current_status": {
                    "has_license": bool,
                    "has_semester_enrollment": bool,
                    "payment_verified": bool,
                    "current_belt": str,
                    "current_level": int
                }
            }
        """
        missing = []
        status = {
            "has_license": False,
            "has_semester_enrollment": False,
            "payment_verified": False,
            "current_belt": None,
            "current_level": 0
        }

        # Check age eligibility
        is_eligible, age_msg = self.validate_age_eligibility(user, db=db)
        if not is_eligible:
            missing.append(f"Age requirement: {age_msg}")

        # Check license
        has_license, license_error = self.validate_user_has_license(user, db)
        if has_license:
            license = db.query(UserLicense).filter(
                UserLicense.user_id == user.id,
                UserLicense.is_active == True
            ).first()

            status["has_license"] = True
            status["current_level"] = license.current_level

            # Get current belt
            current_belt = self.get_current_belt(license.id, db)
            status["current_belt"] = current_belt
        else:
            missing.append(f"Active license: {license_error}")

        # Check semester enrollment
        enrollment = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.is_active == True
        ).first()

        if enrollment:
            status["has_semester_enrollment"] = True
            status["payment_verified"] = enrollment.payment_verified

            if not enrollment.payment_verified:
                missing.append("Payment verification required")
        else:
            missing.append("Semester enrollment required")

        can_participate = len(missing) == 0
        return {
            "can_participate": can_participate,
            "missing_requirements": missing,
            "current_status": status
        }

    # ========================================================================
    # BELT PROGRESSION
    # ========================================================================

    def get_progression_status(self, user_license, db: Session) -> Dict:
        """
        Get current belt progression status for GanCuju Player.

        Args:
            user_license: UserLicense model instance
            db: Database session

        Returns:
            Dictionary with structure:
            {
                "current_level": int (1-8),
                "current_belt": str,
                "current_belt_info": Dict,
                "next_belt": Optional[str],
                "next_belt_info": Optional[Dict],
                "progress_percentage": float,
                "belt_history": List[Dict],
                "achievements": List[Dict]
            }
        """
        # Get current belt
        current_belt = self.get_current_belt(user_license.id, db)
        current_info = self.get_belt_info(current_belt)
        current_level = current_info['level']

        # Get next belt
        next_belt = self.get_next_belt(current_belt)
        next_info = self.get_belt_info(next_belt) if next_belt else None

        # Calculate progress percentage (level / 8 * 100)
        progress_pct = (current_level / 8.0) * 100.0

        # Get belt history
        history = self.get_belt_history(user_license.id, db)

        # Achievements
        achievements = []
        if current_level >= 4:
            achievements.append({"name": "Competition Ready", "description": "Reached Blue Belt (Level 4+)"})
        if current_level >= 5:
            achievements.append({"name": "Teaching Certified", "description": "Reached Brown Belt (Level 5+)"})
        if current_level >= 7:
            achievements.append({"name": "Expert Practitioner", "description": "Reached Black Belt (Level 7)"})
        if current_level == 8:
            achievements.append({"name": "Grand Master", "description": "Achieved Dragon Wisdom (Level 8)"})

        return {
            "current_level": current_level,
            "current_belt": current_belt,
            "current_belt_info": current_info,
            "next_belt": next_belt,
            "next_belt_info": next_info,
            "progress_percentage": round(progress_pct, 2),
            "belt_history": history,
            "achievements": achievements
        }

    # ========================================================================
    # BELT MANAGEMENT METHODS
    # ========================================================================

    def get_current_belt(self, user_license_id: int, db: Session) -> str:
        """Get current belt level for a user license"""
        latest_promotion = db.query(BeltPromotion).filter(
            BeltPromotion.user_license_id == user_license_id
        ).order_by(BeltPromotion.promoted_at.desc()).first()

        if latest_promotion:
            return latest_promotion.to_belt

        return self.BELT_LEVELS[0]  # Default to Bamboo Disciple

    def get_next_belt(self, current_belt: str) -> Optional[str]:
        """Get the next belt in progression sequence"""
        try:
            current_index = self.BELT_LEVELS.index(current_belt)
            if current_index < len(self.BELT_LEVELS) - 1:
                return self.BELT_LEVELS[current_index + 1]
            return None  # Already at max
        except ValueError:
            raise ValueError(f"Invalid belt level: {current_belt}")

    def get_belt_info(self, belt_level: str) -> Dict:
        """Get display information for a belt level"""
        return self.BELT_INFO.get(belt_level, {
            'name': 'Unknown',
            'color': 'Unknown',
            'stage': 'Unknown',
            'level': 0
        })

    def promote_to_next_belt(
        self,
        user_license_id: int,
        promoted_by: int,
        db: Session,
        notes: Optional[str] = None,
        exam_score: Optional[int] = None,
        exam_notes: Optional[str] = None
    ) -> BeltPromotion:
        """
        Promote student to next belt level.

        Args:
            user_license_id: UserLicense ID
            promoted_by: Instructor user ID
            db: Database session
            notes: Optional promotion notes
            exam_score: Optional exam score (0-100)
            exam_notes: Optional exam notes

        Returns:
            BeltPromotion record
        """
        current_belt = self.get_current_belt(user_license_id, db)
        next_belt = self.get_next_belt(current_belt)

        if not next_belt:
            raise ValueError("Already at highest belt (Dragon Wisdom)")

        if exam_score is not None and (exam_score < 0 or exam_score > 100):
            raise ValueError(f"Exam score must be between 0-100, got {exam_score}")

        promotion = BeltPromotion(
            user_license_id=user_license_id,
            from_belt=current_belt,
            to_belt=next_belt,
            promoted_by=promoted_by,
            promoted_at=datetime.now(),
            notes=notes,
            exam_score=exam_score,
            exam_notes=exam_notes
        )

        db.add(promotion)

        # Update license level
        license = db.query(UserLicense).filter(UserLicense.id == user_license_id).first()
        new_level = self.BELT_LEVELS.index(next_belt) + 1
        license.current_level = new_level
        license.max_achieved_level = max(license.max_achieved_level, new_level)

        db.commit()
        db.refresh(promotion)

        return promotion

    def get_belt_history(self, user_license_id: int, db: Session) -> List[Dict]:
        """Get belt promotion history"""
        promotions = db.query(BeltPromotion).filter(
            BeltPromotion.user_license_id == user_license_id
        ).order_by(BeltPromotion.promoted_at.desc()).all()

        history = []
        for promo in promotions:
            promoter = db.query(User).filter(User.id == promo.promoted_by).first()

            history.append({
                'id': promo.id,
                'from_belt': promo.from_belt,
                'from_belt_info': self.get_belt_info(promo.from_belt) if promo.from_belt else None,
                'to_belt': promo.to_belt,
                'to_belt_info': self.get_belt_info(promo.to_belt),
                'promoted_by': promo.promoted_by,
                'promoter_name': promoter.name if promoter else "Unknown",
                'promoted_at': promo.promoted_at.isoformat() if promo.promoted_at else None,
                'notes': promo.notes,
                'exam_score': promo.exam_score,
                'exam_notes': promo.exam_notes
            })

        return history
