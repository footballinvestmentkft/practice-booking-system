"""
LFA Player Service - SEASON-Based Specialization

This service handles all LFA Football Player-specific logic including:
- Age-based automatic categorization (PRE, YOUTH, AMATEUR, PRO)
- Season enrollment required (Semester = Season with theme/age_group)
- Session booking based on age group compatibility
- Master Instructor promotion workflow
- Football skills tracking (7 skills)

Age Group Rules (OFFICIAL):
UP (Children) Categories:
- PRE: 6-11 years old (can self-enroll)
- YOUTH: 12-18 years old (can self-enroll)

Adult Categories (14+ minimum):
- AMATEUR: 14+ years old (can self-enroll)
- PRO: 14+ years old (CANNOT self-enroll, only promoted by Master Instructor)

CRITICAL BOUNDARY: 14 years is the minimum age for adult categories.
Ages 14-18 can be in YOUTH (UP) OR AMATEUR/PRO (Adult) categories.

Key Characteristics:
- SEASON-BASED: SemesterEnrollment REQUIRED (Semester = Season)
- Payment verified per season enrollment
- Cross-age-group movement controlled by Master Instructor
- Skills tracking: heading, shooting, crossing, passing, dribbling, ball_control, defending
"""

from typing import Tuple, Dict, Optional, List
from datetime import date
from sqlalchemy.orm import Session
from app.services.specs.base_spec import BaseSpecializationService
from app.models.license import UserLicense
from app.models.football_skill_assessment import FootballSkillAssessment
from app.models.semester_enrollment import SemesterEnrollment
from app.services.canonical_policy import (
    CanonicalProgram,
    football_participation_categories,
    resolve_license_program,
)
from app.services.program_eligibility_service import is_user_eligible_for_program
from app.services.player_identity_service import (
    PlayerEntitlementConflictError,
    get_active_football_entitlement,
    get_current_player_assignment,
)
from app.skills_config import get_all_skill_keys
from app.services.canonical_policy import football_base_category, football_season


class LFAPlayerService(BaseSpecializationService):
    """
    Service for LFA Football Player specialization.

    Handles age-based categorization, session booking, and skills progression.
    """

    def __init__(self, db: Session = None):
        self.db = db

    # ========================================================================
    # AGE GROUP CONFIGURATION
    # ========================================================================

    AGE_GROUPS = {
        'PRE': {
            'min_age': 5,
            'max_age': 13,
            'display_name': 'Pre (5-13 season age)',
            'can_self_enroll': True,
            'category': 'UP'  # Children category
        },
        'YOUTH': {
            'min_age': 14,
            'max_age': 18,
            'display_name': 'Youth (14-18 season age)',
            'can_self_enroll': True,
            'category': 'UP'  # Children category
        },
        'AMATEUR': {
            'min_age': 19,
            'max_age': None,  # No upper limit
            'display_name': 'Amateur (19+ season base; 14+ by explicit movement)',
            'can_self_enroll': True,
            'category': 'ADULT'  # Adult category (14+ minimum)
        },
        'PRO': {
            'min_age': 14,
            'max_age': None,  # No upper limit
            'display_name': 'Pro (14+ years)',
            'can_self_enroll': False,  # Only Master Instructor can promote
            'category': 'ADULT'  # Adult category (14+ minimum)
        }
    }

    # ✅ CONSOLIDATED: Use central skill list from skills_config.py (44 skills)
    # Old hardcoded list (7 skills) is deprecated
    @property
    def VALID_SKILLS(self) -> List[str]:
        """Get all valid skill keys from central configuration"""
        return get_all_skill_keys()

    # ========================================================================
    # OVERRIDE: BaseSpecializationService Methods
    # ========================================================================

    def is_session_based(self) -> bool:
        """
        LFA Player is SEASON-based (requires season enrollment).

        Note: Returns True for backward compatibility, but season enrollment IS required.
        Season = Semester with specific theme/age_group.

        Season Structure:
        - PRE: 12 seasons/year (monthly)
        - YOUTH: 4 seasons/year (quarterly)
        - AMATEUR: 1 season/year (annual 07.01-06.30)
        - PRO: 1 season/year (annual 07.01-06.30)
        """
        return True

    def get_specialization_name(self) -> str:
        """Human-readable name"""
        return "LFA Football Player"

    def validate_user_has_license(self, user, db: Session) -> Tuple[bool, Optional[str]]:
        """Require the canonical Player entitlement rather than any active license."""
        try:
            license_row = get_active_football_entitlement(db, user_id=user.id)
        except PlayerEntitlementConflictError:
            return False, "Player entitlement identity requires manual review"
        if license_row is None:
            return False, "No active license found for LFA Football Player"
        return True, None

    # ========================================================================
    # AGE GROUP CALCULATION & VALIDATION
    # ========================================================================

    def calculate_age_group(self, date_of_birth: date) -> str:
        """
        Calculate which age group user belongs to based on current age.

        IMPORTANT: Ages 14-18 can be in YOUTH (UP category) OR AMATEUR (Adult category).
        This method returns the default "natural" age group based on UP categories.
        For adult category enrollment, use validate_age_eligibility() separately.

        Age Group Rules:
        - PRE: 6-11 years (UP category)
        - YOUTH: 12-18 years (UP category)
        - AMATEUR: 14+ years (Adult category, self-enroll allowed)
        - PRO: 14+ years (Adult category, Master Instructor promotion only)

        Critical Boundary: 14 years is the minimum age for adult categories.

        Args:
            date_of_birth: User's date of birth

        Returns:
            Age group string ('PRE', 'YOUTH', 'AMATEUR', 'PRO')
            For ages 6-13: Returns natural UP category (PRE or YOUTH)
            For ages 14-18: Returns 'YOUTH' (natural UP category)
            For ages 19+: Returns 'AMATEUR' (adult category default)

        Raises:
            ValueError: If age is below minimum (6 years)
        """
        season = football_season(date.today())
        category = football_base_category(date_of_birth, season_start=season.start)
        if category is None:
            raise ValueError("Age is below minimum (5 years) for LFA Football Player")
        return category.value

    def get_age_group_from_specialization(self, spec_type: str) -> Optional[str]:
        """
        Extract age group from specialization_type.

        Args:
            spec_type: Full specialization (e.g., "LFA_PLAYER_PRE")

        Returns:
            Age group string or None if invalid

        Examples:
            >>> service.get_age_group_from_specialization("LFA_PLAYER_PRE")
            'PRE'
            >>> service.get_age_group_from_specialization("LFA_PLAYER_YOUTH")
            'YOUTH'
        """
        if not spec_type or not spec_type.startswith('LFA_PLAYER'):
            return None

        parts = spec_type.split('_')
        if len(parts) < 3:
            return None

        age_group = parts[-1]  # Last part is age group
        return age_group if age_group in self.AGE_GROUPS else None

    def validate_age_eligibility(self, user, target_group: Optional[str] = None, db: Session = None) -> Tuple[bool, str]:
        """
        Validate if user's age fits the age group rules.

        Args:
            user: User model instance
            target_group: Optional specific age group to validate against
            db: Database session (not used for LFA Player)

        Returns:
            Tuple of (is_eligible: bool, reason: str)
        """
        eligible, denial = is_user_eligible_for_program(
            db or self.db, user, CanonicalProgram.LFA_FOOTBALL_PLAYER
        )
        if not eligible:
            return False, denial or "Football eligibility denied"

        # Calculate user's natural age group
        try:
            natural_age_group = self.calculate_age_group(user.date_of_birth)
        except ValueError as e:
            return False, str(e)

        # If no target specified, check if user can be in their natural age group
        if not target_group:
            age_config = self.AGE_GROUPS[natural_age_group]
            if not age_config['can_self_enroll']:
                return False, f"Cannot self-enroll in {natural_age_group} group. Master Instructor promotion required."
            return True, f"Eligible for {natural_age_group} age group"

        # Validate against specific target group
        if target_group not in self.AGE_GROUPS:
            return False, f"Invalid age group: {target_group}"

        if target_group != natural_age_group:
            return False, "Explicit football category movement required"
        return True, f"Eligible for season base {target_group} category"

    # ========================================================================
    # SESSION BOOKING LOGIC
    # ========================================================================

    def can_book_session(self, user, session, db: Session) -> Tuple[bool, str]:
        """
        Check if LFA Player can book a session.

        Rules:
        1. User must have active license
        2. User must have active season enrollment (SemesterEnrollment with payment_verified)
        3. User's date of birth must be set
        4. Session age group must match user's license age group OR
           Master Instructor has allowed cross-age-group booking

        Season Structure (LFA Player):
        - PRE: 12 seasons/year (monthly)
        - YOUTH: 4 seasons/year (quarterly)
        - AMATEUR: 1 season/year (annual 07.01-06.30)
        - PRO: 1 season/year (annual 07.01-06.30)

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

        # Get user's license
        try:
            license = get_active_football_entitlement(db, user_id=user.id)
        except PlayerEntitlementConflictError:
            return False, "Player entitlement identity requires manual review"
        if license is None:
            return False, "No active license found for LFA Football Player"

        # ✅ CHECK SEASON ENROLLMENT (payment verified)
        season_enrollment = None
        if session.semester_id:
            season_enrollment = db.query(SemesterEnrollment).filter(
                SemesterEnrollment.user_id == user.id,
                SemesterEnrollment.semester_id == session.semester_id,
                SemesterEnrollment.is_active == True
            ).first()

            if not season_enrollment:
                return False, "No active season enrollment found. You must enroll in the current season first."

            if not season_enrollment.payment_verified:
                return False, "Season payment not verified. Please complete payment to access sessions."
        else:
            return False, "Canonical football session requires a semester assignment"

        resolution = resolve_license_program(
            getattr(license, "canonical_program_id", None), license.specialization_type
        )
        if not resolution.usable or resolution.canonical_program is not CanonicalProgram.LFA_FOOTBALL_PLAYER:
            return False, "License program identity requires manual review"

        # Extract age group from session specialization_type
        session_age_group = self.get_age_group_from_specialization(session.specialization_type)
        if not session_age_group:
            return False, "Invalid session specialization type (missing age group)"

        assignment = getattr(season_enrollment, "football_category_assignment", None)
        if assignment is None:
            return False, "Canonical football season assignment required; legacy enrollment needs manual review"
        permitted = football_participation_categories(
            assignment.season_base_category,
            assignment.effective_category,
            base_participation_retained=assignment.base_participation_retained,
        )
        if session_age_group in {category.value for category in permitted}:
            return True, "Session category is granted by the canonical football assignment"
        return False, "Session category is not granted by the canonical football assignment"

    def can_attend_age_group_session(self, user_age_group: str, session_age_group: str) -> Tuple[bool, str]:
        """
        Check if player from one age group can attend session of another age group.

        Business Rules (can be customized):
        - PRE can attend YOUTH sessions (advanced training)
        - YOUTH can attend PRE sessions (mentoring younger players)
        - AMATEUR can attend YOUTH sessions (help younger players)
        - PRO cannot attend any other sessions (too advanced)

        Args:
            user_age_group: User's age group
            session_age_group: Session's age group

        Returns:
            Tuple of (can_attend: bool, reason: str)
        """
        # Same age group always allowed
        if user_age_group == session_age_group:
            return True, "Same age group"

        return False, "Cross-category access requires a canonical season assignment"

    # ========================================================================
    # ENROLLMENT REQUIREMENTS
    # ========================================================================

    def get_enrollment_requirements(self, user, db: Session) -> Dict:
        """
        Get what's needed for user to participate in LFA Player.

        Returns license status and age group info.

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
                    "license_active": bool,
                    "age_group": str,
                    "natural_age_group": str,
                    "can_self_enroll": bool
                }
            }
        """
        missing = []
        status = {
            "has_license": False,
            "license_active": False,
            "age_group": None,
            "natural_age_group": None,
            "can_self_enroll": False
        }

        # Check date of birth
        is_valid, error = self.validate_date_of_birth(user)
        if not is_valid:
            missing.append(f"Date of birth: {error}")
        else:
            # Calculate natural age group
            try:
                natural_age_group = self.calculate_age_group(user.date_of_birth)
                status["natural_age_group"] = natural_age_group
                status["can_self_enroll"] = self.AGE_GROUPS[natural_age_group]['can_self_enroll']
            except ValueError as e:
                missing.append(str(e))

        # Check license
        has_license, license_error = self.validate_user_has_license(user, db)
        if has_license:
            license = get_active_football_entitlement(db, user_id=user.id)
            assignment = get_current_player_assignment(db, user_id=user.id)

            status["has_license"] = True
            status["license_active"] = license.is_active
            status["age_group"] = assignment.effective_category if assignment else None
            if assignment is None:
                missing.append("Canonical football season assignment required")
        else:
            missing.append(f"Active license: {license_error}")

        can_participate = len(missing) == 0
        return {
            "can_participate": can_participate,
            "missing_requirements": missing,
            "current_status": status
        }

    # ========================================================================
    # PROGRESSION & SKILLS
    # ========================================================================

    def get_progression_status(self, user_license, db: Session) -> Dict:
        """
        Get current skills assessment progress for LFA Player.

        Args:
            user_license: UserLicense model instance
            db: Database session

        Returns:
            Dictionary with structure:
            {
                "current_level": str (age group),
                "progress_percentage": float (average skill percentage),
                "skills": List[Dict] (individual skill assessments),
                "achievements": List[Dict] (completed milestones)
            }
        """
        assignment = get_current_player_assignment(db, user_id=user_license.user_id)
        age_group = assignment.effective_category if assignment else None

        # Fetch all skill assessments for this license
        assessments = db.query(FootballSkillAssessment).filter(
            FootballSkillAssessment.user_license_id == user_license.id
        ).all()

        # Calculate average skill percentage
        if assessments:
            avg_percentage = sum(a.percentage for a in assessments) / len(assessments)
        else:
            avg_percentage = 0.0

        # Format skills data
        skills_data = []
        for skill in self.VALID_SKILLS:
            assessment = next((a for a in assessments if a.skill_name == skill), None)
            if assessment:
                skills_data.append({
                    "skill_name": skill,
                    "percentage": assessment.percentage,
                    "points_earned": assessment.points_earned,
                    "points_total": assessment.points_total,
                    "last_updated": assessment.created_at.isoformat() if assessment.created_at else None
                })
            else:
                skills_data.append({
                    "skill_name": skill,
                    "percentage": 0.0,
                    "points_earned": 0,
                    "points_total": 0,
                    "last_updated": None
                })

        # Simple achievement logic (can be expanded)
        achievements = []
        if avg_percentage >= 80:
            achievements.append({"name": "Expert Player", "description": "80%+ average skill"})
        if avg_percentage >= 60:
            achievements.append({"name": "Proficient Player", "description": "60%+ average skill"})
        if len(assessments) == len(self.VALID_SKILLS):
            achievements.append({"name": "All Skills Assessed", "description": "Completed all 7 skills"})

        return {
            "current_level": age_group or "Unknown",
            "progress_percentage": round(avg_percentage, 2),
            "skills": skills_data,
            "achievements": achievements,
            "next_milestone": self._get_next_milestone(avg_percentage, age_group)
        }

    def _get_next_milestone(self, avg_percentage: float, age_group: Optional[str]) -> Optional[Dict]:
        """
        Calculate next milestone for player progression.

        Args:
            avg_percentage: Current average skill percentage
            age_group: Current age group

        Returns:
            Dictionary with next milestone info or None
        """
        if avg_percentage < 60:
            return {
                "name": "Proficient Player",
                "target_percentage": 60.0,
                "remaining": round(60.0 - avg_percentage, 2)
            }
        elif avg_percentage < 80:
            return {
                "name": "Expert Player",
                "target_percentage": 80.0,
                "remaining": round(80.0 - avg_percentage, 2)
            }
        elif age_group and age_group != 'PRO':
            # Suggest age group promotion
            next_groups = {'PRE': 'YOUTH', 'YOUTH': 'AMATEUR', 'AMATEUR': 'PRO'}
            next_group = next_groups.get(age_group)
            if next_group:
                return {
                    "name": f"Promotion to {next_group}",
                    "description": "Contact Master Instructor for age group promotion",
                    "target_percentage": None
                }

        return None

    # ========================================================================
    # MASTER INSTRUCTOR PROMOTION
    # ========================================================================

    def promote_to_higher_age_group(
        self,
        user_license: UserLicense,
        target_age_group: str,
        promoted_by_instructor_id: int,
        db: Session
    ) -> Tuple[bool, str]:
        """
        Master Instructor promotes player to a different age group.

        This is the ONLY way to move a player to PRO group or to override
        natural age-based categorization.

        Args:
            user_license: UserLicense to promote
            target_age_group: Target age group (PRE, YOUTH, AMATEUR, PRO)
            promoted_by_instructor_id: ID of Master Instructor performing promotion
            db: Database session

        Returns:
            Tuple of (success: bool, message: str)
        """
        return False, "Use the canonical replay-safe football category movement command"
