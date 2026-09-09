"""
Specialization Validation Service

Validates user eligibility for specializations based on:
- Age requirements
- Parental consent (for minors in LFA_COACH)
- Age group matching (for LFA_FOOTBALL_PLAYER and LFA_COACH)

WS1 routes all eligibility decisions through the canonical policy service. JSON
configuration remains descriptive content and is not an authorization source.
"""

import logging
from datetime import date
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.specialization import SpecializationType
from app.services.specialization_config_loader import get_config_loader
from app.services.canonical_policy import (
    PROGRAM_MINIMUM_AGES,
    football_base_category,
    football_season,
    resolve_program_id,
)
from app.services.program_eligibility_service import is_user_eligible_for_program

logger = logging.getLogger(__name__)


class SpecializationValidationError(Exception):
    """Raised when specialization validation fails"""


class SpecializationValidator:
    """
    Validates user eligibility for specializations.

    Key validations:
    1. Minimum age requirement
    2. Age group matching (for LFA_FOOTBALL_PLAYER, LFA_COACH)
    3. Parental consent (for LFA_COACH users under 18)
    """

    def __init__(self, db: Session):
        self.db = db
        self.config_loader = get_config_loader()

    def validate_user_for_specialization(
        self,
        user: User,
        specialization: SpecializationType,
        raise_exception: bool = True
    ) -> Dict[str, Any]:
        """
        Validate if user meets requirements for a specialization.

        Args:
            user: User instance
            specialization: SpecializationType enum
            raise_exception: If True, raise SpecializationValidationError on failure.
                           If False, return validation result dict.

        Returns:
            Dict with validation results:
            {
                'valid': bool,
                'errors': List[str],
                'warnings': List[str],
                'requirements': Dict[str, Any]
            }

        Raises:
            SpecializationValidationError: If validation fails and raise_exception=True
        """
        errors: List[str] = []
        warnings: List[str] = []

        # Load specialization config
        try:
            config = self.config_loader.load_config(specialization)
        except Exception as e:
            errors.append(f"Failed to load specialization config: {e}")
            return self._format_result(False, errors, warnings, {})

        resolution = resolve_program_id(specialization)
        if not resolution.usable:
            min_age = None
            errors.append("PROGRAM_ID_MANUAL_REVIEW_OR_INVALID")
        else:
            min_age = PROGRAM_MINIMUM_AGES[resolution.canonical_program]
            eligible, reason = is_user_eligible_for_program(self.db, user, specialization)
            if not eligible:
                errors.append(reason or "CANONICAL_ELIGIBILITY_DENIED")

        requirements = {
            'min_age': min_age,
            'specialization_name': config.get('name'),
            'has_age_groups': specialization == SpecializationType.LFA_FOOTBALL_PLAYER,
            'age_groups': config.get('age_groups') if specialization == SpecializationType.LFA_FOOTBALL_PLAYER else None,
            'canonical_policy': True,
            'requires_parental_consent_under_18': True,
        }
        is_valid = len(errors) == 0
        result = self._format_result(is_valid, errors, warnings, requirements)
        if not is_valid and raise_exception:
            raise SpecializationValidationError("; ".join(errors))
        return result

    def _format_result(
        self,
        valid: bool,
        errors: List[str],
        warnings: List[str],
        requirements: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Format validation result."""
        return {
            'valid': valid,
            'errors': errors,
            'warnings': warnings,
            'requirements': requirements
        }

    def get_eligible_specializations(self, user: User) -> List[Dict[str, Any]]:
        """
        Get list of specializations user is eligible for.

        Args:
            user: User instance

        Returns:
            List of dicts with specialization info and eligibility:
            [
                {
                    'specialization': SpecializationType,
                    'name': str,
                    'eligible': bool,
                    'errors': List[str],
                    'warnings': List[str]
                },
                ...
            ]
        """
        results = []

        for spec_enum in SpecializationType:
            validation = self.validate_user_for_specialization(
                user,
                spec_enum,
                raise_exception=False
            )

            try:
                display_info = self.config_loader.get_display_info(spec_enum)
                name = display_info['name']
            except Exception as e:
                logger.error(f"Error getting display info for specialization {spec_enum}: {e}")
                name = spec_enum.value

            results.append({
                'specialization': spec_enum,
                'specialization_id': spec_enum.value,
                'name': name,
                'eligible': validation['valid'],
                'errors': validation['errors'],
                'warnings': validation['warnings'],
                'requirements': validation['requirements']
            })

        return results

    def get_matching_age_group(
        self,
        user: User,
        specialization: SpecializationType
    ) -> Optional[Dict[str, Any]]:
        """
        Get the age group that matches the user's age.

        Args:
            user: User instance
            specialization: SpecializationType enum

        Returns:
            Age group dict or None if no match
        """
        if specialization != SpecializationType.LFA_FOOTBALL_PLAYER or user.date_of_birth is None:
            return None

        config = self.config_loader.load_config(specialization)
        age_groups = config.get('age_groups', [])
        season = football_season(date.today())
        category = football_base_category(user.date_of_birth, season_start=season.start)
        if category is None:
            return None
        for age_group in age_groups:
            if str(age_group.get('name', '')).upper() == category.value:
                return age_group

        return None


def validate_user_specialization(
    db: Session,
    user: User,
    specialization: SpecializationType
) -> Dict[str, Any]:
    """
    Convenience function to validate user for specialization.

    Args:
        db: Database session
        user: User instance
        specialization: SpecializationType enum

    Returns:
        Validation result dict

    Raises:
        SpecializationValidationError: If validation fails
    """
    validator = SpecializationValidator(db)
    return validator.validate_user_for_specialization(user, specialization, raise_exception=True)
