"""
License validation utilities

Provides reusable validation logic for user licenses,
eliminating 4 duplicated implementations across endpoints.

Usage:
    from app.services.shared.license_validator import LicenseValidator

    # Validate coach license
    license = LicenseValidator.validate_coach_license(
        db, user_id, age_group="AMATEUR"
    )

    # Just get license without age group validation
    license = LicenseValidator.get_coach_license(db, user_id)
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Optional

from ...models.license import UserLicense


class LicenseValidator:
    """
    Centralized license validation logic.

    Eliminates duplicated license checks across:
    - instructor_assignment.py (3 occurrences)
    - lifecycle.py (1 occurrence)
    """

    # ========================================================================
    # CONFIGURATION: Minimum coach levels for each age group
    # ========================================================================
    MINIMUM_COACH_LEVELS_STR = {
        "PRE": 1,       # Level 1 (lowest)
        "YOUTH": 3,     # Level 3
        "AMATEUR": 5,   # Level 5
        "PRO": 7        # Level 7 (highest)
    }

    @classmethod
    def get_coach_license(
        cls,
        db: Session,
        user_id: int,
        raise_if_missing: bool = True
    ) -> Optional[UserLicense]:
        """
        Get the highest-level coach license for a user.

        Args:
            db: Database session
            user_id: User ID to check
            raise_if_missing: If True, raises 403 if no license found

        Returns:
            UserLicense instance with highest current_level, or None if not found

        Raises:
            HTTPException(403): If no coach license found and raise_if_missing=True
        """
        now = datetime.now(timezone.utc)
        coach_license = db.query(UserLicense).filter(
            UserLicense.user_id == user_id,
            UserLicense.specialization_type == "LFA_COACH",
            UserLicense.is_active == True,  # noqa: E712
            or_(UserLicense.expires_at.is_(None), UserLicense.expires_at > now),
        ).order_by(UserLicense.current_level.desc()).first()

        if not coach_license and raise_if_missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "license_required",
                    "message": "User must have LFA_COACH license",
                    "user_id": user_id
                }
            )

        return coach_license

    @classmethod
    def validate_coach_license(
        cls,
        db: Session,
        user_id: int,
        age_group: Optional[str] = None,
        user_email: Optional[str] = None,
        tournament_id: Optional[int] = None,
        tournament_name: Optional[str] = None
    ) -> UserLicense:
        """
        Validate that user has coach license and sufficient level for age group.

        Args:
            db: Database session
            user_id: User ID to validate
            age_group: Tournament age group (PRE, YOUTH, AMATEUR, PRO)
            user_email: Optional user email for error message
            tournament_id: Optional tournament ID for error message
            tournament_name: Optional tournament name for error message

        Returns:
            UserLicense instance if validation passes

        Raises:
            HTTPException(403): If no coach license or insufficient level
        """
        # Get coach license
        coach_license = cls.get_coach_license(db, user_id, raise_if_missing=True)

        # If no age group specified, just return the license
        if not age_group:
            return coach_license

        # Validate level for age group
        required_level = cls.MINIMUM_COACH_LEVELS_STR.get(age_group)

        if required_level is None:
            # Unknown age group - log warning but allow (backward compatibility)
            return coach_license

        if coach_license.current_level < required_level:
            # Build detailed error message
            error_detail = {
                "error": "insufficient_coach_level",
                "message": f"Coach level {coach_license.current_level} is insufficient for {age_group} age group. Minimum required: Level {required_level}",
                "user_id": user_id,
                "current_coach_level": coach_license.current_level,
                "required_coach_level": required_level,
                "tournament_age_group": age_group
            }

            # Add optional context
            if user_email:
                error_detail["user_email"] = user_email
            if tournament_id:
                error_detail["tournament_id"] = tournament_id
            if tournament_name:
                error_detail["tournament_name"] = tournament_name

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error_detail
            )

        return coach_license

    @classmethod
    def get_minimum_level_for_age_group(cls, age_group: str) -> Optional[int]:
        """
        Get minimum coach level required for age group.

        Args:
            age_group: Age group string (PRE, YOUTH, AMATEUR, PRO)

        Returns:
            Minimum level (1-7) or None if age group unknown
        """
        return cls.MINIMUM_COACH_LEVELS_STR.get(age_group)

    @classmethod
    def check_level_sufficient(
        cls,
        current_level: int,
        age_group: str
    ) -> bool:
        """
        Check if coach level is sufficient for age group.

        Args:
            current_level: Coach's current level
            age_group: Age group to check

        Returns:
            True if level is sufficient, False otherwise
        """
        required_level = cls.get_minimum_level_for_age_group(age_group)

        if required_level is None:
            # Unknown age group - assume sufficient (backward compatibility)
            return True

        return current_level >= required_level


# Export main class
__all__ = ["LicenseValidator"]
