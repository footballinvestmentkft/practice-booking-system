"""
🎓 Teaching Permission Service
Validates teaching permissions based on LFA Coach license levels

RULES (LFA Coach only):
- Odd levels (1,3,5,7) = ASSISTANT Coach → Can teach WITH Master supervision
- Even levels (2,4,6,8) = HEAD Coach → Can teach independently
- LFA Player licenses = NEVER have teaching permissions
"""
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.specialization import SpecializationType
from typing import Optional, Dict
from app.services.canonical_policy import AgeCategory, CoachRole, coach_can_teach


class TeachingPermissionService:
    """Service for checking teaching permissions based on license type and level"""

    # Head Coach levels (can teach independently)
    HEAD_COACH_LEVELS = [2, 4, 6, 8]

    # Assistant Coach levels (need Master supervision)
    ASSISTANT_COACH_LEVELS = [1, 3, 5, 7]

    @staticmethod
    def can_teach_scope(current_level: int, age_group: str, role: str) -> bool:
        """Canonical 8x8 coach authorization decision."""
        try:
            return coach_can_teach(current_level, AgeCategory(age_group), CoachRole(role))
        except ValueError:
            return False

    @staticmethod
    def get_teaching_permissions(user: User, db: Session) -> Dict:
        """
        Get teaching permissions for a user based on their license

        Returns:
        {
            "can_teach_independently": bool,
            "can_teach_with_supervision": bool,
            "license_type": str,
            "current_level": int,
            "position_title": str,  # "Pre Head Coach", "Youth Assistant", etc.
            "age_group": str,       # "PRE_FOOTBALL", "YOUTH_FOOTBALL", etc.
            "warnings": List[str]
        }
        """
        result = {
            "can_teach_independently": False,
            "can_teach_with_supervision": False,
            "license_type": None,
            "current_level": None,
            "position_title": None,
            "age_group": None,
            "warnings": []
        }

        # Check if user has specialization
        if not user.specialization:
            result["warnings"].append("No specialization assigned")
            return result

        specialization = user.specialization
        result["license_type"] = specialization

        # Get current level from user_licenses table
        from app.models.license import UserLicense

        # Convert enum to string for SQL query
        spec_value = specialization.value if hasattr(specialization, 'value') else str(specialization)

        user_license = db.query(UserLicense).filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == spec_value,
            UserLicense.is_active == True
        ).first()

        if user_license is None:
            result["warnings"].append("No active license for specialization")
            return result

        current_level = int(user_license.current_level)
        result["current_level"] = current_level

        # Player licenses (LFA Football Player, GānCuju) do NOT grant teaching permissions
        if specialization in [
            SpecializationType.LFA_FOOTBALL_PLAYER.value,
            SpecializationType.GANCUJU_PLAYER.value
        ]:
            result["warnings"].append("Player licenses do not grant teaching permissions")
            return result

        # Only LFA_COACH has teaching permissions
        spec_value = specialization.value if hasattr(specialization, 'value') else specialization
        if spec_value != "LFA_COACH":
            result["warnings"].append(f"Specialization {spec_value} does not grant teaching permissions")
            return result

        # Check LFA_COACH level
        level = result["current_level"]

        # Get position details
        position_details = TeachingPermissionService._get_position_details(level)
        result["position_title"] = position_details["title"]
        result["age_group"] = position_details["age_group"]

        # Check if Head Coach (even level)
        if level in TeachingPermissionService.HEAD_COACH_LEVELS:
            result["can_teach_independently"] = True
            result["can_teach_with_supervision"] = True

        # Check if Assistant Coach (odd level)
        elif level in TeachingPermissionService.ASSISTANT_COACH_LEVELS:
            result["can_teach_with_supervision"] = True
            result["warnings"].append("Assistant Coach - requires Master Instructor supervision to teach")

        else:
            result["warnings"].append(f"Invalid LFA_COACH level: {level}")

        return result

    @staticmethod
    def _get_position_details(level: int) -> Dict:
        """Get position title and age group based on LFA_COACH level"""
        positions = {
            1: {"title": "Pre Assistant Coach", "age_group": "PRE_FOOTBALL"},
            2: {"title": "Pre Head Coach", "age_group": "PRE_FOOTBALL"},
            3: {"title": "Youth Assistant Coach", "age_group": "YOUTH_FOOTBALL"},
            4: {"title": "Youth Head Coach", "age_group": "YOUTH_FOOTBALL"},
            5: {"title": "Amateur Assistant Coach", "age_group": "AMATEUR_FOOTBALL"},
            6: {"title": "Amateur Head Coach", "age_group": "AMATEUR_FOOTBALL"},
            7: {"title": "Pro Assistant Coach", "age_group": "PRO_FOOTBALL"},
            8: {"title": "Pro Head Coach", "age_group": "PRO_FOOTBALL"}
        }

        return positions.get(level, {"title": "Unknown", "age_group": None})

    @staticmethod
    def can_teach_independently(user: User, db: Session) -> bool:
        """Quick check: Can this user teach sessions independently?"""
        permissions = TeachingPermissionService.get_teaching_permissions(user, db)
        return permissions["can_teach_independently"]

    @staticmethod
    def requires_supervision(user: User, db: Session) -> bool:
        """Quick check: Does this user require Master supervision to teach?"""
        permissions = TeachingPermissionService.get_teaching_permissions(user, db)
        return (
            permissions["can_teach_with_supervision"]
            and not permissions["can_teach_independently"]
        )

    @staticmethod
    def get_age_group_for_user(user: User, db: Session) -> Optional[str]:
        """Get the age group this user's license covers (PRE/YOUTH/AMATEUR/PRO)"""
        permissions = TeachingPermissionService.get_teaching_permissions(user, db)
        return permissions.get("age_group")
