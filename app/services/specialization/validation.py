"""
Specialization Validation & Utilities
Common validation functions and enum handling for all specializations
"""
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
import logging

from app.models.specialization import SpecializationType
from app.models.user_progress import Specialization
from app.services.specialization_config_loader import get_config_loader
from app.services.canonical_policy import resolve_program_id

logger = logging.getLogger(__name__)

# Compatibility exports. Alias support is now policy-based and has no time
# deadline; these names remain for import stability only.
DEPRECATED_MAPPINGS = {"PLAYER": "GANCUJU_PLAYER", "COACH": "LFA_COACH"}
DEPRECATION_DEADLINE = None
DEPRECATION_WARNING = "Legacy specialization alias '{old_id}' maps to '{new_id}'."

# DEPRECATION SYSTEM
def specialization_id_to_enum(specialization_id: str) -> Optional[SpecializationType]:
    """
    Convert string specialization ID to enum value.
    Supports both old names (PLAYER, COACH) and new names (GANCUJU_PLAYER, LFA_COACH).

    Args:
        specialization_id: String ID (e.g., "PLAYER", "GANCUJU_PLAYER", "LFA_COACH")

    Returns:
        SpecializationType enum or None if invalid
    """
    resolution = resolve_program_id(specialization_id)
    if not resolution.usable:
        return None
    try:
        return SpecializationType(resolution.canonical_program.value)
    except ValueError:
        return None


def handle_legacy_specialization(spec_id: str) -> str:
    """
    Handle legacy specialization IDs with deprecation warning.

    Args:
        spec_id: Potentially legacy ID (PLAYER, COACH)

    Returns:
        Mapped new ID if legacy, original ID otherwise

    Raises:
        ValueError: If after deprecation deadline
    """
    resolution = resolve_program_id(spec_id)
    if not resolution.usable:
        raise ValueError(f"Specialization ID '{spec_id}' requires manual review or is invalid")
    if resolution.status.value == "LEGACY_ALIAS":
        logger.warning("legacy_specialization_alias", extra={"legacy_id": resolution.source_id})
    return resolution.canonical_program.value


def validate_specialization_exists(db: Session, specialization_id: str) -> bool:
    """
    Check if specialization exists and is active (config-based validation).

    This is a lightweight check for API endpoints before loading full JSON config.

    Args:
        db: Database session (kept for compatibility, not used)
        specialization_id: Specialization ID to check (handles legacy IDs)

    Returns:
        bool: True if specialization exists in enum/config and is active
    """
    specialization_id = handle_legacy_specialization(specialization_id)

    # Check if specialization exists in enum
    try:
        # Check if it's a valid enum value
        valid_specs = [spec.value for spec in SpecializationType]
        if specialization_id not in valid_specs:
            return False

        # Try to load config to verify it's active
        config_loader = get_config_loader()
        try:
            config = config_loader.load_config(specialization_id)
            return config.get("is_active", True)  # Default to True if not specified
        except Exception:
            # If config doesn't exist but it's in enum, consider it valid
            return True
    except Exception:
        return False


def get_all_specializations(db: Session) -> List[Dict[str, Any]]:
    """
    Get all active specializations (HYBRID: DB + JSON)

    Process:
    1. Load active specializations from DB (is_active check)
    2. Load full definitions from JSON
    3. Merge and return

    This ensures:
    - Only active specializations are returned
    - Full content comes from JSON (Source of Truth)
    - DB maintains referential integrity

    Args:
        db: Database session

    Returns:
        List of specialization dicts with full details from JSON
    """
    config_loader = get_config_loader()

    # STEP 1: Get active specializations from DB
    db_specs = db.query(Specialization).filter_by(is_active=True).all()
    active_ids = {s.id for s in db_specs}  # Set for fast lookup

    # STEP 2 & 3: Load JSON configs for active specializations
    specializations = []

    for spec_enum in SpecializationType:
        spec_id = spec_enum.value

        # Skip if not active in DB
        if spec_id not in active_ids:
            continue

        try:
            # Load full definition from JSON (Source of Truth)
            display_info = config_loader.get_display_info(spec_enum)
            max_level = config_loader.get_max_level(spec_enum)

            specializations.append({
                'id': spec_id,
                'name': display_info['name'],
                'icon': display_info.get('icon', '⚽'),
                'description': display_info['description'],
                'max_levels': max_level,
                'min_age': display_info.get('min_age', 0),
                'color_theme': display_info.get('color_theme', '#000000')
            })
        except FileNotFoundError as e:
            # JSON missing for DB record - CRITICAL ERROR
            logger.error(f"CRITICAL: JSON config missing for active specialization {spec_id}: {e}")
            continue
        except Exception as e:
            logger.error(f"Error loading specialization {spec_id}: {e}")
            continue

    return specializations
