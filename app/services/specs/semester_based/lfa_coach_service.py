"""
LFA Coach Service - Semester-Based Specialization

This service handles all LFA Coach-specific logic including:
- Semester enrollment with payment verification
- 8-level certification progression (PRE → PRO, Assistant → Head)
- Teaching hours tracking
- Student feedback and performance reviews
- Age-appropriate coaching certification

Certification Levels (8 Total):
1. PRE_ASSISTANT - LFA Pre Football Assistant Coach (Ages 5-13)
2. PRE_HEAD - LFA Pre Football Head Coach (Ages 5-13)
3. YOUTH_ASSISTANT - LFA Youth Football Assistant Coach (Ages 14-18)
4. YOUTH_HEAD - LFA Youth Football Head Coach (Ages 14-18)
5. AMATEUR_ASSISTANT - LFA Amateur Football Assistant Coach (Ages 14+)
6. AMATEUR_HEAD - LFA Amateur Football Head Coach (Ages 14+)
7. PRO_ASSISTANT - LFA PRO Football Assistant Coach (Ages 14+)
8. PRO_HEAD - LFA PRO Football Head Coach (Ages 14+)

Key Characteristics:
- SEMESTER-BASED: Semester enrollment REQUIRED with payment verification
- Certification progression system (8 levels across 4 age groups)
- Teaching hours tracking and verification
- Minimum age: 14 years old (to start coaching)
- Each level requires: exam, first aid cert, teaching hours, student feedback
"""

from typing import Tuple, Dict, Optional
from sqlalchemy.orm import Session
from app.services.specs.base_spec import BaseSpecializationService
from app.models.license import UserLicense
from app.models.semester_enrollment import SemesterEnrollment
from app.services.canonical_policy import CanonicalProgram
from app.services.program_eligibility_service import is_user_eligible_for_program


class LFACoachService(BaseSpecializationService):
    """
    Service for LFA Coach specialization.

    Handles semester-based enrollment, certification progression, and coaching development.
    """

    def __init__(self, db: Session = None):
        self.db = db

    # ========================================================================
    # CERTIFICATION CONFIGURATION
    # ========================================================================

    COACH_LEVELS = [
        'PRE_ASSISTANT',         # L1 - Pre (5-13) Assistant
        'PRE_HEAD',              # L2 - Pre (5-13) Head
        'YOUTH_ASSISTANT',       # L3 - Youth (14-18) Assistant
        'YOUTH_HEAD',            # L4 - Youth (14-18) Head
        'AMATEUR_ASSISTANT',     # L5 - Amateur (14+) Assistant
        'AMATEUR_HEAD',          # L6 - Amateur (14+) Head
        'PRO_ASSISTANT',         # L7 - Pro (16+) Assistant
        'PRO_HEAD'               # L8 - Pro (16+) Head
    ]

    LEVEL_INFO = {
        'PRE_ASSISTANT': {
            'name': 'LFA Pre Football Assistant Coach',
            'icon': '📋',
            'age_group': 'Pre (5-13 years)',
            'role': 'Assistant Coach',
            'level': 1,
            'min_coach_age': 14,
            'focus': 'Child psychology and development, fun-based training design',
            'requirements': {
                'certification_exam': True,
                'first_aid': True,
                'teaching_hours': 0,  # Entry level
                'student_feedback': False,
                'previous_cert': None  # Entry level
            }
        },
        'PRE_HEAD': {
            'name': 'LFA Pre Football Head Coach',
            'icon': '📋',
            'age_group': 'Pre (5-13 years)',
            'role': 'Head Coach',
            'level': 2,
            'min_coach_age': 14,
            'focus': 'Team management, parent communication, curriculum delivery',
            'requirements': {
                'certification_exam': True,
                'first_aid': True,
                'teaching_hours': 50,
                'student_feedback': True,
                'previous_cert': 'PRE_ASSISTANT'
            }
        },
        'YOUTH_ASSISTANT': {
            'name': 'LFA Youth Football Assistant Coach',
            'icon': '📋',
            'age_group': 'Youth (14-18 years)',
            'role': 'Assistant Coach',
            'level': 3,
            'min_coach_age': 14,
            'focus': 'Advanced technical coaching, tactical principles',
            'requirements': {
                'certification_exam': True,
                'first_aid': True,
                'teaching_hours': 100,
                'student_feedback': True,
                'previous_cert': 'PRE_HEAD'
            }
        },
        'YOUTH_HEAD': {
            'name': 'LFA Youth Football Head Coach',
            'icon': '📋',
            'age_group': 'Youth (14-18 years)',
            'role': 'Head Coach',
            'level': 4,
            'min_coach_age': 14,
            'focus': 'Competition strategy, match management, player pathways',
            'requirements': {
                'certification_exam': True,
                'first_aid': True,
                'teaching_hours': 200,
                'student_feedback': True,
                'previous_cert': 'YOUTH_ASSISTANT'
            }
        },
        'AMATEUR_ASSISTANT': {
            'name': 'LFA Amateur Football Assistant Coach',
            'icon': '📋',
            'age_group': 'Amateur (14+ years)',
            'role': 'Assistant Coach',
            'level': 5,
            'min_coach_age': 14,
            'focus': 'Advanced tactics, performance analysis, physical conditioning',
            'requirements': {
                'certification_exam': True,
                'first_aid': True,
                'teaching_hours': 300,
                'student_feedback': True,
                'previous_cert': 'YOUTH_HEAD'
            }
        },
        'AMATEUR_HEAD': {
            'name': 'LFA Amateur Football Head Coach',
            'icon': '📋',
            'age_group': 'Amateur (14+ years)',
            'role': 'Head Coach',
            'level': 6,
            'min_coach_age': 14,
            'focus': 'Full team operations, competitive season planning',
            'requirements': {
                'certification_exam': True,
                'first_aid': True,
                'teaching_hours': 500,
                'student_feedback': True,
                'previous_cert': 'AMATEUR_ASSISTANT'
            }
        },
        'PRO_ASSISTANT': {
            'name': 'LFA PRO Football Assistant Coach',
            'icon': '📋',
            'age_group': 'PRO (16+ years)',
            'role': 'Assistant Coach',
            'level': 7,
            'min_coach_age': 14,
            'focus': 'Elite training methodology, professional standards',
            'requirements': {
                'certification_exam': True,
                'first_aid': True,
                'teaching_hours': 800,
                'student_feedback': True,
                'previous_cert': 'AMATEUR_HEAD'
            }
        },
        'PRO_HEAD': {
            'name': 'LFA PRO Football Head Coach',
            'icon': '📋',
            'age_group': 'PRO (16+ years)',
            'role': 'Head Coach',
            'level': 8,
            'min_coach_age': 14,
            'focus': 'Professional club operations, academy director preparation',
            'requirements': {
                'certification_exam': True,
                'first_aid': True,
                'teaching_hours': 1200,
                'student_feedback': True,
                'previous_cert': 'PRO_ASSISTANT'
            }
        }
    }

    MINIMUM_AGE = 14  # Minimum age to start coaching training

    # ========================================================================
    # OVERRIDE: BaseSpecializationService Methods
    # ========================================================================

    def is_semester_based(self) -> bool:
        """LFA Coach is semester-based (enrollment required)"""
        return True

    def get_specialization_name(self) -> str:
        """Human-readable name"""
        return "LFA Coach"

    # ========================================================================
    # AGE VALIDATION
    # ========================================================================

    def validate_age_eligibility(self, user, target_group: Optional[str] = None, db: Session = None) -> Tuple[bool, str]:
        """
        Validate if user's age meets minimum requirement for LFA Coach.

        Args:
            user: User model instance
            target_group: Target certification level (optional)
            db: Database session

        Returns:
            Tuple of (is_eligible: bool, reason: str)
        """
        # Certification levels add professional requirements, never new age gates.
        eligible, denial = is_user_eligible_for_program(
            db or self.db, user, CanonicalProgram.LFA_COACH
        )
        return eligible, denial or "Eligible for LFA Coach"

    # ========================================================================
    # SESSION BOOKING LOGIC
    # ========================================================================

    def can_book_session(self, user, session, db: Session) -> Tuple[bool, str]:
        """
        Check if LFA Coach can book a session.

        Rules:
        1. User must have active license
        2. User must be enrolled in a semester with payment verified
        3. Session must be for LFA Coach specialization

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
            return False, "No active semester enrollment found. You must enroll in an LFA Coach semester first."

        if not enrollment.payment_verified:
            return False, "Payment not verified. Please complete payment to access sessions."

        # Check if session is for LFA Coach (uses target_specialization field)
        spec = getattr(session, 'target_specialization', None) or getattr(session, 'specialization_type', None)
        spec_value = spec.value if hasattr(spec, 'value') else str(spec) if spec else None
        if not spec_value or not spec_value.startswith('LFA_COACH'):
            return False, f"This session is not for LFA Coach (session type: {spec_value})"

        return True, "Eligible to book LFA Coach session"

    # ========================================================================
    # ENROLLMENT REQUIREMENTS
    # ========================================================================

    def get_enrollment_requirements(self, user, db: Session) -> Dict:
        """
        Get what's needed for user to participate in LFA Coach.

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
                    "current_certification": str,
                    "current_level": int
                }
            }
        """
        missing = []
        status = {
            "has_license": False,
            "has_semester_enrollment": False,
            "payment_verified": False,
            "current_certification": None,
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

            # Get current certification
            current_cert = self.get_current_certification(license.id, db)
            status["current_certification"] = current_cert
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
    # CERTIFICATION PROGRESSION
    # ========================================================================

    def get_progression_status(self, user_license, db: Session) -> Dict:
        """
        Get current certification progression status for LFA Coach.

        Args:
            user_license: UserLicense model instance
            db: Database session

        Returns:
            Dictionary with structure:
            {
                "current_level": int (1-8),
                "current_certification": str,
                "current_cert_info": Dict,
                "next_certification": Optional[str],
                "next_cert_info": Optional[Dict],
                "progress_percentage": float,
                "certification_history": List[Dict],
                "teaching_hours": int,
                "achievements": List[Dict]
            }
        """
        # Get current certification
        current_cert = self.get_current_certification(user_license.id, db)
        current_info = self.get_certification_info(current_cert)
        current_level = current_info['level']

        # Get next certification
        next_cert = self.get_next_certification(current_cert)
        next_info = self.get_certification_info(next_cert) if next_cert else None

        # Calculate progress percentage (level / 8 * 100)
        progress_pct = (current_level / 8.0) * 100.0

        # Get certification history (would be implemented with actual certification records)
        history = []

        # Teaching hours (would be tracked in separate table)
        teaching_hours = 0

        # Achievements
        achievements = []
        if current_level >= 2:
            achievements.append({"name": "Head Coach Qualified", "description": "Completed first Head Coach certification (PRE)"})
        if current_level >= 4:
            achievements.append({"name": "Youth Specialist", "description": "Certified to coach Youth (14-18)"})
        if current_level >= 6:
            achievements.append({"name": "Amateur Coach", "description": "Certified for Amateur (14+)"})
        if current_level >= 8:
            achievements.append({"name": "Professional Coach", "description": "Achieved PRO Head Coach (Level 8)"})

        return {
            "current_level": current_level,
            "current_certification": current_cert,
            "current_cert_info": current_info,
            "next_certification": next_cert,
            "next_cert_info": next_info,
            "progress_percentage": round(progress_pct, 2),
            "certification_history": history,
            "teaching_hours": teaching_hours,
            "achievements": achievements
        }

    # ========================================================================
    # CERTIFICATION MANAGEMENT METHODS
    # ========================================================================

    def get_current_certification(self, user_license_id: int, db: Session) -> str:
        """
        Get current certification level for a coach license

        Args:
            user_license_id: UserLicense ID
            db: Database session

        Returns:
            Certification level string (e.g., 'PRE_ASSISTANT')
        """
        license = db.query(UserLicense).filter(UserLicense.id == user_license_id).first()

        if not license:
            raise ValueError(f"License {user_license_id} not found")

        # Map numeric level to coach certification
        level = license.current_level or 1
        cert_index = min(level - 1, len(self.COACH_LEVELS) - 1)
        return self.COACH_LEVELS[cert_index]

    def get_next_certification(self, current_cert: str) -> Optional[str]:
        """
        Get the next certification in progression sequence

        Args:
            current_cert: Current certification level

        Returns:
            Next certification level, or None if already at PRO_HEAD
        """
        try:
            current_index = self.COACH_LEVELS.index(current_cert)
            if current_index < len(self.COACH_LEVELS) - 1:
                return self.COACH_LEVELS[current_index + 1]
            return None  # Already at max (PRO_HEAD)
        except ValueError:
            raise ValueError(f"Invalid certification level: {current_cert}")

    def get_certification_info(self, cert_level: str) -> Dict:
        """
        Get display information for a certification level

        Args:
            cert_level: Certification level string

        Returns:
            Dictionary with certification details
        """
        return self.LEVEL_INFO.get(cert_level, {
            'name': 'Unknown',
            'icon': '❓',
            'age_group': 'Unknown',
            'role': 'Unknown',
            'level': 0,
            'focus': 'Unknown'
        })

    def certify_next_level(
        self,
        user_license_id: int,
        certified_by: int,
        db: Session,
        exam_score: Optional[float] = None,
        notes: Optional[str] = None
    ) -> Dict:
        """
        Certify coach for next level.

        Args:
            user_license_id: UserLicense ID
            certified_by: Admin/Senior Coach user ID
            db: Database session
            exam_score: Certification exam score (percentage, 0-100)
            notes: Optional certification notes

        Returns:
            Dictionary with certification details

        TODO: This should create a certification record when that model exists
        For now, it just updates the license level
        """
        current_cert = self.get_current_certification(user_license_id, db)
        next_cert = self.get_next_certification(current_cert)

        if not next_cert:
            raise ValueError("Already at highest certification (PRO HEAD)")

        if exam_score is not None and (exam_score < 0 or exam_score > 100):
            raise ValueError(f"Exam score must be between 0-100, got {exam_score}")

        # Minimum passing score is 80%
        if exam_score is not None and exam_score < 80:
            raise ValueError(f"Exam score {exam_score}% is below passing threshold (80%)")

        # Update license level
        license = db.query(UserLicense).filter(UserLicense.id == user_license_id).first()
        new_level = self.COACH_LEVELS.index(next_cert) + 1
        license.current_level = new_level
        license.max_achieved_level = max(license.max_achieved_level, new_level)

        db.commit()
        db.refresh(license)

        return {
            'success': True,
            'message': f'Successfully certified for {next_cert}',
            'from_cert': current_cert,
            'to_cert': next_cert,
            'new_level': new_level,
            'exam_score': exam_score,
            'certified_by': certified_by,
            'notes': notes
        }
