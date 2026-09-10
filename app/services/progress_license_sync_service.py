"""
🔄 Progress-License Synchronization Service
P0 CRITICAL: Keeps SpecializationProgress and UserLicense in sync

Two parallel systems track user progression:
1. SpecializationProgress (player_levels, coach_levels, internship_levels)
2. UserLicense (license_metadata with marketing content)

This service ensures they stay synchronized.
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import logging

from app.models.user_progress import SpecializationProgress
from app.models.license import UserLicense
from app.models.user import User
from app.services.canonical_policy import CanonicalProgram, resolve_program_id

logger = logging.getLogger(__name__)


class ProgressLicenseSyncService:
    """Service to synchronize SpecializationProgress and UserLicense"""

    def __init__(self, db: Session):
        self.db = db

    def sync_progress_to_license(
        self,
        user_id: int,
        specialization: str,
        synced_by: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Sync SpecializationProgress -> UserLicense (progress is source of truth)

        Use case: Student levels up through gameplay/sessions, license should reflect this.

        Args:
            user_id: User ID
            specialization: PLAYER, COACH, or INTERNSHIP
            synced_by: Admin/system user ID performing sync

        Returns:
            Dict with sync result
        """
        specialization = specialization.upper()

        # Get source of truth (SpecializationProgress)
        progress = self.db.query(SpecializationProgress).filter(
            and_(
                SpecializationProgress.student_id == user_id,
                SpecializationProgress.specialization_id == specialization
            )
        ).first()

        if not progress:
            return {
                "success": False,
                "message": f"No progress found for user {user_id} in {specialization}",
                "action": "none"
            }

        # Get or create UserLicense
        license = self.db.query(UserLicense).filter(
            and_(
                UserLicense.user_id == user_id,
                UserLicense.specialization_type == specialization
            )
        ).first()

        if not license:
            if (
                resolve_program_id(specialization).canonical_program
                is CanonicalProgram.LFA_FOOTBALL_PLAYER
            ):
                raise ValueError("FOOTBALL_ENTITLEMENT_REQUIRES_CANONICAL_COMMAND")
            # Create new license matching progress
            license = UserLicense(
                user_id=user_id,
                specialization_type=specialization,
                current_level=progress.current_level,
                max_achieved_level=progress.current_level,
                started_at=progress.created_at or datetime.now(timezone.utc),
                last_advanced_at=progress.last_activity
            )
            self.db.add(license)
            self.db.commit()
            self.db.refresh(license)

            logger.info(f"Created UserLicense for user {user_id}, {specialization}, level {progress.current_level}")

            return {
                "success": True,
                "message": "License created from progress",
                "action": "created",
                "progress_level": progress.current_level,
                "license_level": license.current_level,
                "synced": True
            }

        # Check if sync needed
        if license.current_level == progress.current_level:
            return {
                "success": True,
                "message": "Already in sync",
                "action": "none",
                "progress_level": progress.current_level,
                "license_level": license.current_level,
                "synced": True
            }

        # Sync needed
        old_license_level = license.current_level
        license.current_level = progress.current_level
        license.max_achieved_level = max(license.max_achieved_level, progress.current_level)
        license.last_advanced_at = datetime.now(timezone.utc)

        # Create progression record
        from app.models.license import LicenseProgression
        progression = LicenseProgression(
            user_license_id=license.id,
            from_level=old_license_level,
            to_level=progress.current_level,
            advanced_by=synced_by,
            advancement_reason="Auto-sync from SpecializationProgress",
            requirements_met=f"Progress level: {progress.current_level}, XP: {progress.total_xp}, Sessions: {progress.completed_sessions}"
        )

        self.db.add(progression)
        self.db.commit()
        self.db.refresh(license)

        logger.info(f"Synced UserLicense for user {user_id}, {specialization}: {old_license_level} -> {progress.current_level}")

        return {
            "success": True,
            "message": f"License updated: level {old_license_level} -> {progress.current_level}",
            "action": "updated",
            "progress_level": progress.current_level,
            "old_license_level": old_license_level,
            "new_license_level": license.current_level,
            "synced": True
        }

    def sync_license_to_progress(
        self,
        user_id: int,
        specialization: str
    ) -> Dict[str, Any]:
        """
        Sync UserLicense -> SpecializationProgress (license is source of truth)

        Use case: Admin manually advances license, progress should reflect this.

        Args:
            user_id: User ID
            specialization: PLAYER, COACH, or INTERNSHIP

        Returns:
            Dict with sync result
        """
        specialization = specialization.upper()

        # Get source of truth (UserLicense)
        license = self.db.query(UserLicense).filter(
            and_(
                UserLicense.user_id == user_id,
                UserLicense.specialization_type == specialization
            )
        ).first()

        if not license:
            return {
                "success": False,
                "message": f"No license found for user {user_id} in {specialization}",
                "action": "none"
            }

        # Get or create SpecializationProgress
        progress = self.db.query(SpecializationProgress).filter(
            and_(
                SpecializationProgress.student_id == user_id,
                SpecializationProgress.specialization_id == specialization
            )
        ).first()

        if not progress:
            # Create new progress matching license
            progress = SpecializationProgress(
                student_id=user_id,
                specialization_id=specialization,
                current_level=license.current_level,
                total_xp=0,  # No XP history available
                completed_sessions=0,
                completed_projects=0,
                last_activity=license.last_advanced_at or datetime.now(timezone.utc)
            )
            self.db.add(progress)
            self.db.commit()
            self.db.refresh(progress)

            logger.info(f"Created SpecializationProgress for user {user_id}, {specialization}, level {license.current_level}")

            return {
                "success": True,
                "message": "Progress created from license",
                "action": "created",
                "license_level": license.current_level,
                "progress_level": progress.current_level,
                "synced": True
            }

        # Check if sync needed
        if progress.current_level == license.current_level:
            return {
                "success": True,
                "message": "Already in sync",
                "action": "none",
                "license_level": license.current_level,
                "progress_level": progress.current_level,
                "synced": True
            }

        # Sync needed
        old_progress_level = progress.current_level
        progress.current_level = license.current_level
        progress.last_activity = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(progress)

        logger.info(f"Synced SpecializationProgress for user {user_id}, {specialization}: {old_progress_level} -> {license.current_level}")

        return {
            "success": True,
            "message": f"Progress updated: level {old_progress_level} -> {license.current_level}",
            "action": "updated",
            "license_level": license.current_level,
            "old_progress_level": old_progress_level,
            "new_progress_level": progress.current_level,
            "synced": True
        }

    def find_desync_issues(self, specialization: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Find all users with desync issues between Progress and License

        Args:
            specialization: Optional filter for specific specialization

        Returns:
            List of desync issues with details
        """
        issues = []

        # Query all users with both Progress and License
        query = self.db.query(
            SpecializationProgress.student_id,
            SpecializationProgress.specialization_id,
            SpecializationProgress.current_level.label('progress_level'),
            UserLicense.current_level.label('license_level'),
            User.email
        ).join(
            UserLicense,
            and_(
                SpecializationProgress.student_id == UserLicense.user_id,
                SpecializationProgress.specialization_id == UserLicense.specialization_type
            )
        ).join(
            User,
            SpecializationProgress.student_id == User.id
        )

        if specialization:
            query = query.filter(SpecializationProgress.specialization_id == specialization.upper())

        results = query.all()

        for row in results:
            if row.progress_level != row.license_level:
                issues.append({
                    "user_id": row.student_id,
                    "user_email": row.email,
                    "specialization": row.specialization_id,
                    "progress_level": row.progress_level,
                    "license_level": row.license_level,
                    "difference": abs(row.progress_level - row.license_level),
                    "recommended_action": "sync_progress_to_license" if row.progress_level > row.license_level else "sync_license_to_progress"
                })

        # Also check for Progress without License
        orphan_progress = self.db.query(
            SpecializationProgress.student_id,
            SpecializationProgress.specialization_id,
            SpecializationProgress.current_level,
            User.email
        ).outerjoin(
            UserLicense,
            and_(
                SpecializationProgress.student_id == UserLicense.user_id,
                SpecializationProgress.specialization_id == UserLicense.specialization_type
            )
        ).join(
            User,
            SpecializationProgress.student_id == User.id
        ).filter(UserLicense.id == None)

        if specialization:
            orphan_progress = orphan_progress.filter(SpecializationProgress.specialization_id == specialization.upper())

        for row in orphan_progress.all():
            issues.append({
                "user_id": row.student_id,
                "user_email": row.email,
                "specialization": row.specialization_id,
                "progress_level": row.current_level,
                "license_level": None,
                "difference": None,
                "recommended_action": "create_license_from_progress",
                "issue_type": "missing_license"
            })

        # Check for License without Progress
        orphan_license = self.db.query(
            UserLicense.user_id,
            UserLicense.specialization_type,
            UserLicense.current_level,
            User.email
        ).outerjoin(
            SpecializationProgress,
            and_(
                UserLicense.user_id == SpecializationProgress.student_id,
                UserLicense.specialization_type == SpecializationProgress.specialization_id
            )
        ).join(
            User,
            UserLicense.user_id == User.id
        ).filter(SpecializationProgress.id == None)

        if specialization:
            orphan_license = orphan_license.filter(UserLicense.specialization_type == specialization.upper())

        for row in orphan_license.all():
            issues.append({
                "user_id": row.user_id,
                "user_email": row.email,
                "specialization": row.specialization_type,
                "progress_level": None,
                "license_level": row.current_level,
                "difference": None,
                "recommended_action": "create_progress_from_license",
                "issue_type": "missing_progress"
            })

        logger.info(f"Found {len(issues)} desync issues" + (f" for {specialization}" if specialization else ""))

        return issues

    def auto_sync_all(
        self,
        sync_direction: str = "progress_to_license",
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Background job: Automatically sync all users

        Args:
            sync_direction: "progress_to_license" or "license_to_progress"
            dry_run: If True, only report what would be synced

        Returns:
            Dict with sync results
        """
        issues = self.find_desync_issues()

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "total_issues": len(issues),
                "issues": issues,
                "message": "Dry run complete. No changes made."
            }

        synced_count = 0
        failed_count = 0
        results = []

        for issue in issues:
            try:
                user_id = issue["user_id"]
                specialization = issue["specialization"]

                if issue.get("issue_type") == "missing_license":
                    result = self.sync_progress_to_license(user_id, specialization)
                elif issue.get("issue_type") == "missing_progress":
                    result = self.sync_license_to_progress(user_id, specialization)
                elif sync_direction == "progress_to_license":
                    result = self.sync_progress_to_license(user_id, specialization)
                else:
                    result = self.sync_license_to_progress(user_id, specialization)

                if result["success"]:
                    synced_count += 1
                else:
                    failed_count += 1

                results.append({
                    "user_id": user_id,
                    "specialization": specialization,
                    "result": result
                })

            except Exception as e:
                failed_count += 1
                logger.error(f"Failed to sync user {issue['user_id']}, {issue['specialization']}: {str(e)}")
                results.append({
                    "user_id": issue["user_id"],
                    "specialization": issue["specialization"],
                    "result": {"success": False, "error": str(e)}
                })

        logger.info(f"Auto-sync complete: {synced_count} synced, {failed_count} failed")

        return {
            "success": True,
            "total_issues": len(issues),
            "synced_count": synced_count,
            "failed_count": failed_count,
            "results": results
        }

    def sync_user_all_specializations(
        self,
        user_id: int,
        sync_direction: str = "progress_to_license"
    ) -> Dict[str, Any]:
        """
        Sync all specializations for a specific user

        Args:
            user_id: User ID
            sync_direction: "progress_to_license" or "license_to_progress"

        Returns:
            Dict with sync results for all specializations
        """
        specializations = ["PLAYER", "COACH", "INTERNSHIP"]
        results = {}

        for spec in specializations:
            try:
                if sync_direction == "progress_to_license":
                    result = self.sync_progress_to_license(user_id, spec)
                else:
                    result = self.sync_license_to_progress(user_id, spec)

                results[spec] = result
            except Exception as e:
                logger.error(f"Failed to sync user {user_id}, {spec}: {str(e)}")
                results[spec] = {"success": False, "error": str(e)}

        return {
            "success": True,
            "user_id": user_id,
            "sync_direction": sync_direction,
            "results": results
        }
