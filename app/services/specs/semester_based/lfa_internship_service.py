"""
LFA Internship Service - Semester-Based Specialization

This service handles all LFA Internship-specific logic including:
- Semester enrollment with payment verification
- XP-based progression system (5 semesters, 8 levels total)
- Position preference selection (1-7 from 45 available)
- Strict attendance and quiz requirements (100% required)
- UV (makeup exam) system

5-Semester Journey:
1. 🔰 INTERN JUNIOR (L1-2) - Foundation, 1,875 XP, 70% threshold
2. 📈 INTERN MID-LEVEL (L3-4) - Core Skills, 2,370 XP, 72% threshold
3. 🎯 INTERN SENIOR (L5-6) - Mastery, 2,860 XP, 74% threshold
4. 👑 INTERN LEAD (L7) - Leadership, 3,385 XP, 76% threshold
5. 🚀 INTERN PRINCIPAL (L8) - Executive, 3,900 XP, 78% threshold

Key Characteristics:
- SEMESTER-BASED: Semester enrollment REQUIRED with payment verification
- XP-based progression (not skill or belt based)
- Position selection during onboarding (1-7 positions from 30)
- Zero tolerance: 100% attendance + all quizzes passed
- XP scales +25% per semester (HYBRID > ON-SITE > VIRTUAL)
- UV (makeup) system available but Excellence impossible via UV
"""

from typing import Tuple, Dict, Optional, List
from sqlalchemy.orm import Session
from app.services.specs.base_spec import BaseSpecializationService
from app.models.license import UserLicense
from app.models.semester_enrollment import SemesterEnrollment
from app.services.canonical_policy import CanonicalProgram
from app.services.program_eligibility_service import is_user_eligible_for_program


class LFAInternshipService(BaseSpecializationService):
    """
    Service for LFA Internship specialization.

    Handles semester-based enrollment, XP progression, and internship position management.
    """

    def __init__(self, db: Session = None):
        self.db = db

    # ========================================================================
    # AGE REQUIREMENT
    # ========================================================================

    MINIMUM_AGE = 18  # Minimum age for LFA Internship enrollment

    # ========================================================================
    # LEVEL CONFIGURATION
    # ========================================================================

    INTERN_LEVELS = [
        'INTERN_JUNIOR',      # L1-2, Semester 1
        'INTERN_MID_LEVEL',   # L3-4, Semester 2
        'INTERN_SENIOR',      # L5-6, Semester 3
        'INTERN_LEAD',        # L7, Semester 4
        'INTERN_PRINCIPAL'    # L8, Semester 5
    ]

    LEVEL_INFO = {
        'INTERN_JUNIOR': {
            'name': 'INTERN JUNIOR',
            'icon': '🔰',
            'semester': 1,
            'numeric_levels': [1, 2],
            'focus': 'Foundation & Culture',
            'total_base_xp': 1875,
            'excellence_threshold': 92,  # 92%
            'standard_threshold': 74,    # 74%
            'conditional_threshold': 70  # 70% (minimum to pass)
        },
        'INTERN_MID_LEVEL': {
            'name': 'INTERN MID-LEVEL',
            'icon': '📈',
            'semester': 2,
            'numeric_levels': [3, 4],
            'focus': 'Core Skills & Development',
            'total_base_xp': 2370,
            'excellence_threshold': 93,  # 93%
            'standard_threshold': 76,    # 76%
            'conditional_threshold': 72  # 72%
        },
        'INTERN_SENIOR': {
            'name': 'INTERN SENIOR',
            'icon': '🎯',
            'semester': 3,
            'numeric_levels': [5, 6],
            'focus': 'Mastery & Strategy',
            'total_base_xp': 2860,
            'excellence_threshold': 94,  # 94%
            'standard_threshold': 78,    # 78%
            'conditional_threshold': 74  # 74%
        },
        'INTERN_LEAD': {
            'name': 'INTERN LEAD',
            'icon': '👑',
            'semester': 4,
            'numeric_levels': [7],
            'focus': 'Leadership & Team Management',
            'total_base_xp': 3385,
            'excellence_threshold': 95,  # 95%
            'standard_threshold': 80,    # 80%
            'conditional_threshold': 76  # 76%
        },
        'INTERN_PRINCIPAL': {
            'name': 'INTERN PRINCIPAL',
            'icon': '🚀',
            'semester': 5,
            'numeric_levels': [8],
            'focus': 'Executive & Co-Founder Ready',
            'total_base_xp': 3900,
            'excellence_threshold': 96,  # 96%
            'standard_threshold': 82,    # 82%
            'conditional_threshold': 78  # 78%
        }
    }

    # XP scaling by semester (scales +25% per semester)
    XP_SCALING = {
        1: {'hybrid': 100, 'onsite': 75, 'virtual': 50},   # Junior
        2: {'hybrid': 125, 'onsite': 95, 'virtual': 65},   # Mid-Level
        3: {'hybrid': 150, 'onsite': 115, 'virtual': 75},  # Senior
        4: {'hybrid': 175, 'onsite': 130, 'virtual': 90},  # Lead
        5: {'hybrid': 200, 'onsite': 150, 'virtual': 100}  # Principal
    }

    # UV (makeup) max XP by semester
    UV_MAX_XP = {
        1: 300,  # 16% of 1,875
        2: 380,  # 16% of 2,370
        3: 400,  # 14% of 2,860
        4: 480,  # 14% of 3,385
        5: 540   # 14% of 3,900
    }

    # Available internship positions (30 total)
    INTERNSHIP_POSITIONS = {
        'Administrative': [
            'LFA Sports Director',
            'LFA Digital Marketing Manager',
            'LFA Social Media Manager',
            'LFA Advertising Specialist',
            'LFA Brand Manager',
            'LFA Event Organizer'
        ],
        'Facility Management': [
            'LFA Facility Manager',
            'LFA Technical Manager',
            'LFA Maintenance Technician',
            'LFA Energy Specialist',
            'LFA Groundskeeping Specialist',
            'LFA Security Director'
        ],
        'Commercial': [
            'LFA Retail Manager',
            'LFA Inventory Manager',
            'LFA Sales Representative',
            'LFA Webshop Manager',
            'LFA Ticket Office Manager',
            'LFA Customer Service Agent',
            'LFA VIP Relations Manager'
        ],
        'Communications': [
            'LFA Press Officer',
            'LFA Spokesperson',
            'LFA Content Creator',
            'LFA Photographer',
            'LFA Videographer'
        ],
        'Academy': [
            'LFA Talent Scout',
            'LFA Mental Coach',
            'LFA Social Worker'
        ],
        'International': [
            'LFA Regional Director',
            'LFA Liaison Officer',
            'LFA Business Development Manager'
        ]
    }

    # ========================================================================
    # OVERRIDE: BaseSpecializationService Methods
    # ========================================================================

    def is_semester_based(self) -> bool:
        """LFA Internship is semester-based (enrollment required)"""
        return True

    def get_specialization_name(self) -> str:
        """Human-readable name"""
        return "LFA Internship"

    # ========================================================================
    # AGE VALIDATION
    # ========================================================================

    def validate_age_eligibility(self, user, target_group: Optional[str] = None, db: Session = None) -> Tuple[bool, str]:
        """
        Validate age eligibility for LFA Internship.

        NOTE: LFA Internship requires minimum 18 years of age.
        The 5 levels (JUNIOR→MID-LEVEL→SENIOR→LEAD→PRINCIPAL) are PROGRESSION levels
        within the internship program, NOT age groups.

        Args:
            user: User model instance
            target_group: Not used for Internship
            db: Database session

        Returns:
            Tuple of (is_eligible: bool, reason: str)
        """
        eligible, denial = is_user_eligible_for_program(
            db or self.db, user, CanonicalProgram.INTERNSHIP
        )
        return eligible, denial or "Eligible for Internship"

    # ========================================================================
    # SESSION BOOKING LOGIC
    # ========================================================================

    def can_book_session(self, user, session, db: Session) -> Tuple[bool, str]:
        """
        Check if intern can book a session.

        Rules:
        1. User must have active license
        2. User must be enrolled in a semester with payment verified
        3. Session must be for LFA Internship specialization

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
            return False, "No active semester enrollment found. You must enroll in an Internship semester first."

        if not enrollment.payment_verified:
            return False, "Payment not verified. Please complete payment to access sessions."

        # Check if session is for LFA Internship
        if not session.specialization_type or not session.specialization_type.startswith('INTERNSHIP'):
            return False, f"This session is not for LFA Internship (session type: {session.specialization_type})"

        return True, "Eligible to book Internship session"

    # ========================================================================
    # ENROLLMENT REQUIREMENTS
    # ========================================================================

    def get_enrollment_requirements(self, user, db: Session) -> Dict:
        """
        Get what's needed for user to participate in LFA Internship.

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
                    "current_level": str,
                    "numeric_level": int,
                    "position_selected": bool,
                    "selected_positions": List[str]
                }
            }
        """
        missing = []
        status = {
            "has_license": False,
            "has_semester_enrollment": False,
            "payment_verified": False,
            "current_level": None,
            "numeric_level": 0,
            "position_selected": False,
            "selected_positions": []
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
            status["numeric_level"] = license.current_level

            # Get current intern level
            current_level = self.get_current_level(license.id, db)
            status["current_level"] = current_level

            # Check position selection
            if license.motivation_scores:
                positions = license.motivation_scores.get('selected_positions', [])
                if positions:
                    status["position_selected"] = True
                    status["selected_positions"] = positions
                else:
                    missing.append("Position selection required (1-7 positions)")
            else:
                missing.append("Position selection required (1-7 positions)")
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
    # XP PROGRESSION
    # ========================================================================

    def get_progression_status(self, user_license, db: Session) -> Dict:
        """
        Get current XP progression status for LFA Internship.

        Args:
            user_license: UserLicense model instance
            db: Database session

        Returns:
            Dictionary with structure:
            {
                "current_level": str (e.g., "INTERN_JUNIOR"),
                "numeric_level": int (1-8),
                "semester": int (1-5),
                "current_level_info": Dict,
                "next_level": Optional[str],
                "next_level_info": Optional[Dict],
                "progress_percentage": float,
                "current_xp": int,
                "total_base_xp": int,
                "xp_thresholds": Dict,
                "achievements": List[Dict]
            }
        """
        # Get current level
        current_level = self.get_current_level(user_license.id, db)
        current_info = self.get_level_info(current_level)
        semester = current_info['semester']

        # Get next level
        next_level = self.get_next_level(current_level)
        next_info = self.get_level_info(next_level) if next_level else None

        # Calculate progress percentage (semester / 5 * 100)
        progress_pct = (semester / 5.0) * 100.0

        # XP info
        current_xp = user_license.current_xp or 0
        total_base_xp = current_info['total_base_xp']

        # XP thresholds for current semester
        xp_thresholds = {
            'excellence': int(total_base_xp * current_info['excellence_threshold'] / 100),
            'standard': int(total_base_xp * current_info['standard_threshold'] / 100),
            'conditional': int(total_base_xp * current_info['conditional_threshold'] / 100)
        }

        # Achievements
        achievements = []
        if semester >= 1:
            achievements.append({"name": "Foundation Complete", "description": "Completed INTERN JUNIOR semester"})
        if semester >= 2:
            achievements.append({"name": "Core Skills Mastered", "description": "Completed INTERN MID-LEVEL semester"})
        if semester >= 3:
            achievements.append({"name": "Strategic Thinker", "description": "Completed INTERN SENIOR semester"})
        if semester >= 4:
            achievements.append({"name": "Leadership Proven", "description": "Completed INTERN LEAD semester"})
        if semester >= 5:
            achievements.append({"name": "Executive Ready", "description": "Achieved INTERN PRINCIPAL (Level 8)"})

        return {
            "current_level": current_level,
            "numeric_level": user_license.current_level,
            "semester": semester,
            "current_level_info": current_info,
            "next_level": next_level,
            "next_level_info": next_info,
            "progress_percentage": round(progress_pct, 2),
            "current_xp": current_xp,
            "total_base_xp": total_base_xp,
            "xp_thresholds": xp_thresholds,
            "achievements": achievements
        }

    # ========================================================================
    # LEVEL MANAGEMENT METHODS
    # ========================================================================

    def get_current_level(self, user_license_id: int, db: Session) -> str:
        """
        Get current intern level for a license

        Args:
            user_license_id: UserLicense ID
            db: Database session

        Returns:
            Level string (e.g., 'INTERN_JUNIOR')
        """
        license = db.query(UserLicense).filter(UserLicense.id == user_license_id).first()

        if not license:
            raise ValueError(f"License {user_license_id} not found")

        # Map numeric level to intern level
        level = license.current_level or 1
        if level <= 2:
            return 'INTERN_JUNIOR'
        elif level <= 4:
            return 'INTERN_MID_LEVEL'
        elif level <= 6:
            return 'INTERN_SENIOR'
        elif level == 7:
            return 'INTERN_LEAD'
        else:
            return 'INTERN_PRINCIPAL'

    def get_next_level(self, current_level: str) -> Optional[str]:
        """
        Get the next level in progression sequence

        Args:
            current_level: Current level string

        Returns:
            Next level, or None if already at INTERN_PRINCIPAL
        """
        try:
            current_index = self.INTERN_LEVELS.index(current_level)
            if current_index < len(self.INTERN_LEVELS) - 1:
                return self.INTERN_LEVELS[current_index + 1]
            return None  # Already at max (INTERN_PRINCIPAL)
        except ValueError:
            raise ValueError(f"Invalid level: {current_level}")

    def get_level_info(self, level: str) -> Dict:
        """
        Get display information for an intern level

        Args:
            level: Level string (e.g., 'INTERN_JUNIOR')

        Returns:
            Dictionary with level details
        """
        return self.LEVEL_INFO.get(level, {
            'name': 'Unknown',
            'icon': '❓',
            'semester': 0,
            'numeric_levels': [],
            'focus': 'Unknown',
            'total_base_xp': 0,
            'excellence_threshold': 0,
            'standard_threshold': 0,
            'conditional_threshold': 0
        })

    # ========================================================================
    # POSITION MANAGEMENT
    # ========================================================================

    def get_all_positions(self) -> Dict[str, List[str]]:
        """
        Get all available internship positions grouped by department

        Returns:
            Dictionary with departments as keys and position lists as values
        """
        return self.INTERNSHIP_POSITIONS.copy()

    def get_position_count(self) -> int:
        """
        Get total number of available positions

        Returns:
            Total count (should be 30)
        """
        return sum(len(positions) for positions in self.INTERNSHIP_POSITIONS.values())

    def validate_position_selection(self, positions: List[str]) -> Tuple[bool, str]:
        """
        Validate internship position selection

        Args:
            positions: List of selected position names

        Returns:
            Tuple of (is_valid: bool, message: str)
        """
        # Check count (1-7 positions)
        if len(positions) < 1:
            return False, "At least 1 position must be selected"
        if len(positions) > 7:
            return False, "Maximum 7 positions can be selected"

        # Check for duplicates
        if len(positions) != len(set(positions)):
            return False, "Duplicate positions are not allowed"

        # Check all positions are valid
        all_positions = []
        for dept_positions in self.INTERNSHIP_POSITIONS.values():
            all_positions.extend(dept_positions)

        for position in positions:
            if position not in all_positions:
                return False, f"Invalid position: {position}"

        return True, f"Valid selection of {len(positions)} position(s)"

    # ========================================================================
    # XP CALCULATION (PLACEHOLDER)
    # ========================================================================

    def calculate_session_xp(self, session_type: str, semester: int, attendance_status: str) -> int:
        """
        Calculate XP for a session based on type, semester, and attendance

        Args:
            session_type: 'HYBRID', 'ONSITE', or 'VIRTUAL'
            semester: 1-5
            attendance_status: 'PRESENT', 'EXCUSED', etc.

        Returns:
            XP amount (0 if absent)

        Note: This is a simplified calculation. Full implementation would use
        attendance multipliers and handle UV (makeup) XP.
        """
        if attendance_status not in ['PRESENT']:
            return 0

        session_type_lower = session_type.lower()
        if session_type_lower not in ['hybrid', 'onsite', 'virtual']:
            return 0

        xp_values = self.XP_SCALING.get(semester, self.XP_SCALING[1])
        return xp_values.get(session_type_lower, 0)
