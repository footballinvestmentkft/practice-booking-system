"""
🔒 Progress-License Coupling Enforcer
=====================================

P2 CRITICAL: Ensures atomic updates between Progress and License tables

Problem Addressed (from P2 Edge Case Analysis):
- 60% of edge cases stem from Progress ↔ License dual-table architecture
- Separate transactions can cause desync
- Window of inconsistency between updates

Solution:
- Atomic updates (both commit together or both rollback)
- Pessimistic locking (SELECT ... FOR UPDATE)
- Transaction coupling for all state changes
- Guaranteed data consistency

Usage:
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from contextlib import contextmanager

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select

from app.models.user_progress import SpecializationProgress
from app.models.license import UserLicense, LicenseProgression
from app.services.canonical_policy import CanonicalProgram, resolve_program_id

logger = logging.getLogger(__name__)


class ProgressLicenseCoupler:
    """
    Enforces atomic coupling between Progress and License updates

    Guarantees:
    1. Both tables updated in single transaction
    2. Pessimistic locking prevents race conditions
    3. Automatic rollback on any failure
    4. No desync possible
    """

    def __init__(self, db: Session):
        self.db = db

    @contextmanager
    def acquire_locks(self, user_id: int, specialization: str):
        """
        Acquire row-level locks on both Progress and License

        Uses SELECT ... FOR UPDATE to prevent concurrent modifications
        """
        try:
            # Lock Progress record
            progress = self.db.execute(
                select(SpecializationProgress)
                .where(
                    SpecializationProgress.student_id == user_id,
                    SpecializationProgress.specialization_id == specialization
                )
                .with_for_update()  # Pessimistic lock
            ).scalar_one_or_none()

            # Lock License record (create if doesn't exist)
            license = self.db.execute(
                select(UserLicense)
                .where(
                    UserLicense.user_id == user_id,
                    UserLicense.specialization_type == specialization
                )
                .with_for_update()  # Pessimistic lock
            ).scalar_one_or_none()

            resolution = resolve_program_id(specialization)
            if (
                license is None
                and resolution.canonical_program is CanonicalProgram.LFA_FOOTBALL_PLAYER
            ):
                raise ValueError("FOOTBALL_ENTITLEMENT_REQUIRES_CANONICAL_COMMAND")

            # Create Progress if missing
            if not progress:
                progress = SpecializationProgress(
                    student_id=user_id,
                    specialization_id=specialization,
                    current_level=1,
                    total_xp=0,
                    completed_sessions=0,
                    completed_projects=0
                )
                self.db.add(progress)
                self.db.flush()  # Get ID but don't commit yet

            # Create License if missing
            if not license:
                license = UserLicense(
                    user_id=user_id,
                    specialization_type=specialization,
                    current_level=1,
                    max_achieved_level=1,
                    started_at=datetime.now(timezone.utc)
                )
                self.db.add(license)
                self.db.flush()  # Get ID but don't commit yet

            yield progress, license

        except SQLAlchemyError as e:
            logger.error(f"Lock acquisition failed for user {user_id}, {specialization}: {e}")
            raise

    def update_level_atomic(
        self,
        user_id: int,
        specialization: str,
        new_level: int,
        xp_change: int = 0,
        sessions_change: int = 0,
        projects_change: int = 0,
        theory_hours_change: int = 0,
        practice_hours_change: int = 0,
        advanced_by: Optional[int] = None,
        reason: str = ""
    ) -> Dict[str, Any]:
        """
        Atomically update both Progress and License to new level

        All changes happen in single transaction with pessimistic locking.

        Args:
            user_id: User ID
            specialization: PLAYER, COACH, or INTERNSHIP
            new_level: Target level
            xp_change: XP to add (can be 0 if just updating level)
            sessions_change: Sessions completed
            projects_change: Projects completed
            theory_hours_change: Theory hours (COACH only)
            practice_hours_change: Practice hours (COACH only)
            advanced_by: User ID who authorized advancement (None = system)
            reason: Reason for advancement

        Returns:
            Dict with update results

        Raises:
            ValueError: If validation fails
            SQLAlchemyError: If database error occurs
        """
        specialization = specialization.upper()

        try:
            # Start transaction (will auto-rollback on exception)
            with self.acquire_locks(user_id, specialization) as (progress, license):

                # Validate new level
                from app.models.license import LicenseSystemHelper
                max_level = LicenseSystemHelper.get_specialization_max_level(specialization, self.db)

                if new_level < 1 or new_level > max_level:
                    raise ValueError(
                        f"Invalid level {new_level} for {specialization} (valid range: 1-{max_level})"
                    )

                # Store old values for response
                old_progress_level = progress.current_level
                old_license_level = license.current_level

                # UPDATE PROGRESS
                progress.current_level = new_level
                progress.total_xp += xp_change
                progress.completed_sessions += sessions_change
                progress.completed_projects += projects_change
                progress.last_activity = datetime.now(timezone.utc)

                # COACH-specific
                if specialization == "COACH":
                    progress.theory_hours_completed += theory_hours_change
                    progress.practice_hours_completed += practice_hours_change

                # Validate progress state
                if progress.total_xp < 0:
                    raise ValueError("Total XP cannot be negative")
                if progress.completed_sessions < 0:
                    raise ValueError("Completed sessions cannot be negative")

                # UPDATE LICENSE
                license.current_level = new_level
                license.max_achieved_level = max(license.max_achieved_level, new_level)
                license.last_advanced_at = datetime.now(timezone.utc)

                # Create LicenseProgression record if level changed
                progression = None
                if old_license_level != new_level:
                    progression = LicenseProgression(
                        user_license_id=license.id,
                        from_level=old_license_level,
                        to_level=new_level,
                        advanced_by=advanced_by,
                        advancement_reason=reason or f"Atomic level update to {new_level}",
                        requirements_met=f"XP: {progress.total_xp}, Sessions: {progress.completed_sessions}"
                    )
                    self.db.add(progression)

                # COMMIT (both Progress and License together)
                self.db.commit()

                # Refresh objects
                self.db.refresh(progress)
                self.db.refresh(license)

                logger.info(
                    f"✅ Atomic update successful: user {user_id}, {specialization}, "
                    f"Progress {old_progress_level}→{new_level}, License {old_license_level}→{new_level}"
                )

                return {
                    "success": True,
                    "message": f"Atomically updated to level {new_level}",
                    "progress": {
                        "old_level": old_progress_level,
                        "new_level": progress.current_level,
                        "total_xp": progress.total_xp,
                        "completed_sessions": progress.completed_sessions
                    },
                    "license": {
                        "old_level": old_license_level,
                        "new_level": license.current_level,
                        "max_achieved_level": license.max_achieved_level
                    },
                    "progression_created": progression is not None,
                    "xp_change": xp_change,
                    "sessions_change": sessions_change
                }

        except ValueError as e:
            # Validation error - rollback automatic
            logger.warning(f"Validation failed for atomic update: {e}")
            self.db.rollback()
            return {
                "success": False,
                "message": str(e),
                "error_type": "validation_error"
            }

        except SQLAlchemyError as e:
            # Database error - rollback automatic
            logger.error(f"Database error during atomic update: {e}")
            self.db.rollback()
            return {
                "success": False,
                "message": f"Database error: {str(e)}",
                "error_type": "database_error"
            }

        except Exception as e:
            # Unexpected error - rollback automatic
            logger.error(f"Unexpected error during atomic update: {e}")
            self.db.rollback()
            return {
                "success": False,
                "message": f"Unexpected error: {str(e)}",
                "error_type": "unknown_error"
            }

    def sync_existing_records_atomic(
        self,
        user_id: int,
        specialization: str,
        source: str = "progress"
    ) -> Dict[str, Any]:
        """
        Sync existing Progress and License to match (atomic operation)

        Args:
            user_id: User ID
            specialization: PLAYER, COACH, or INTERNSHIP
            source: "progress" or "license" (which one is source of truth)

        Returns:
            Dict with sync result
        """
        specialization = specialization.upper()

        try:
            with self.acquire_locks(user_id, specialization) as (progress, license):

                if source == "progress":
                    # Progress is source of truth
                    if progress.current_level == license.current_level:
                        return {
                            "success": True,
                            "message": "Already in sync",
                            "action": "none"
                        }

                    old_license_level = license.current_level
                    license.current_level = progress.current_level
                    license.max_achieved_level = max(license.max_achieved_level, progress.current_level)
                    license.last_advanced_at = datetime.now(timezone.utc)

                    action = f"License updated {old_license_level}→{progress.current_level}"

                elif source == "license":
                    # License is source of truth
                    if progress.current_level == license.current_level:
                        return {
                            "success": True,
                            "message": "Already in sync",
                            "action": "none"
                        }

                    old_progress_level = progress.current_level
                    progress.current_level = license.current_level
                    progress.last_activity = datetime.now(timezone.utc)

                    action = f"Progress updated {old_progress_level}→{license.current_level}"

                else:
                    raise ValueError(f"Invalid source: {source}. Must be 'progress' or 'license'")

                # COMMIT (atomic)
                self.db.commit()

                logger.info(f"✅ Atomic sync successful: {action}")

                return {
                    "success": True,
                    "message": "Atomically synced",
                    "action": action,
                    "final_level": progress.current_level
                }

        except Exception as e:
            logger.error(f"Atomic sync failed: {e}")
            self.db.rollback()
            return {
                "success": False,
                "message": str(e)
            }

    def validate_consistency(self, user_id: int, specialization: str) -> Dict[str, Any]:
        """
        Check if Progress and License are in sync

        Args:
            user_id: User ID
            specialization: PLAYER, COACH, or INTERNSHIP

        Returns:
            Dict with consistency check results
        """
        specialization = specialization.upper()

        progress = self.db.query(SpecializationProgress).filter(
            SpecializationProgress.student_id == user_id,
            SpecializationProgress.specialization_id == specialization
        ).first()

        license = self.db.query(UserLicense).filter(
            UserLicense.user_id == user_id,
            UserLicense.specialization_type == specialization
        ).first()

        if not progress and not license:
            return {
                "consistent": True,
                "message": "No records exist (consistent state)",
                "progress_exists": False,
                "license_exists": False
            }

        if not progress:
            return {
                "consistent": False,
                "message": "License exists but Progress missing",
                "progress_exists": False,
                "license_exists": True,
                "license_level": license.current_level,
                "recommended_action": "create_progress_from_license"
            }

        if not license:
            return {
                "consistent": False,
                "message": "Progress exists but License missing",
                "progress_exists": True,
                "license_exists": False,
                "progress_level": progress.current_level,
                "recommended_action": "create_license_from_progress"
            }

        # Both exist - check levels match
        if progress.current_level == license.current_level:
            return {
                "consistent": True,
                "message": "Progress and License in sync",
                "progress_exists": True,
                "license_exists": True,
                "level": progress.current_level
            }
        else:
            return {
                "consistent": False,
                "message": f"Desync detected: Progress={progress.current_level}, License={license.current_level}",
                "progress_exists": True,
                "license_exists": True,
                "progress_level": progress.current_level,
                "license_level": license.current_level,
                "difference": abs(progress.current_level - license.current_level),
                "recommended_action": "sync_to_higher_level"
            }


# Convenience wrapper for common use case
def level_up_atomic(
    db: Session,
    user_id: int,
    specialization: str,
    xp_gained: int,
    sessions_completed: int = 0,
    reason: str = "Level up"
) -> Dict[str, Any]:
    """
    Convenience function for atomic level up

    Automatically calculates new level based on current progress + XP gained

    Args:
        db: Database session
        user_id: User ID
        specialization: PLAYER, COACH, or INTERNSHIP
        xp_gained: XP to add
        sessions_completed: Sessions completed
        reason: Reason for level up

    Returns:
        Dict with update result
    """
    from app.services.specialization_service import SpecializationService

    spec_service = SpecializationService(db)
    coupler = ProgressLicenseCoupler(db)

    # Get current progress to determine new level
    progress_info = spec_service.get_student_progress(user_id, specialization)
    current_level = progress_info["current_level"]["level"]

    # Calculate new level (simplified - real logic in SpecializationService)
    # This is just for demonstration
    new_level = current_level

    # Check if XP is enough for next level
    if progress_info.get("next_level"):
        new_xp = progress_info["total_xp"] + xp_gained
        if new_xp >= progress_info["next_level"]["required_xp"]:
            new_level = progress_info["next_level"]["level"]

    # Perform atomic update
    return coupler.update_level_atomic(
        user_id=user_id,
        specialization=specialization,
        new_level=new_level,
        xp_change=xp_gained,
        sessions_change=sessions_completed,
        reason=reason
    )
