"""
🏮 GānCuju™️©️ License Service
Handles license progression, advancement, and marketing content delivery
"""
import logging
from sqlalchemy import or_
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any

from ..models.license import LicenseMetadata, UserLicense, LicenseProgression, LicenseSystemHelper, LicenseType
from ..models.user import User
from .progress_license_sync_service import ProgressLicenseSyncService
from .canonical_policy import CanonicalProgram, resolve_program_id


class LicenseService:
    """Service for managing GānCuju™️©️ license system"""

    def __init__(self, db: Session):
        self.db = db

    def get_all_license_metadata(self, specialization: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all license metadata, optionally filtered by specialization"""
        query = self.db.query(LicenseMetadata)
        if specialization:
            query = query.filter(LicenseMetadata.specialization_type == specialization.upper())
        
        metadata = query.order_by(
            LicenseMetadata.specialization_type,
            LicenseMetadata.level_number
        ).all()
        return [meta.to_dict() for meta in metadata]

    def get_license_metadata_by_level(self, specialization: str, level: int) -> Optional[Dict[str, Any]]:
        """Get specific license level metadata"""
        metadata = self.db.query(LicenseMetadata).filter(
            LicenseMetadata.specialization_type == specialization.upper(),
            LicenseMetadata.level_number == level
        ).first()
        return metadata.to_dict() if metadata else None

    def get_user_licenses(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all licenses for a user with metadata"""
        user_licenses = self.db.query(UserLicense).filter(
            UserLicense.user_id == user_id
        ).all()
        result = []
        for license in user_licenses:
            license_data = license.to_dict()
            
            # Add current level metadata
            current_meta = self.get_license_metadata_by_level(
                license.specialization_type, 
                license.current_level
            )
            license_data['current_level_metadata'] = current_meta
            
            # Add next level metadata if available
            max_level = LicenseSystemHelper.get_specialization_max_level(license.specialization_type, self.db)
            if license.current_level < max_level:
                next_meta = self.get_license_metadata_by_level(
                    license.specialization_type,
                    license.current_level + 1
                )
                license_data['next_level_metadata'] = next_meta
            
            # Add progression history
            progressions = self.db.query(LicenseProgression).filter(
                LicenseProgression.user_license_id == license.id
            ).order_by(LicenseProgression.advanced_at.desc()).limit(5).all()
            
            license_data['recent_progressions'] = [prog.to_dict() for prog in progressions]
            
            result.append(license_data)
        
        return result

    def get_or_create_user_license(self, user_id: int, specialization: str) -> UserLicense:
        """Get or create a user license for a specialization"""
        resolution = resolve_program_id(specialization)
        if not resolution.usable:
            raise ValueError("License program identity requires manual review")
        canonical = resolution.canonical_program.value
        proven_storage_ids = {
            CanonicalProgram.GANCUJU_PLAYER: ("GANCUJU_PLAYER", "PLAYER"),
            CanonicalProgram.LFA_COACH: ("LFA_COACH", "COACH"),
            CanonicalProgram.INTERNSHIP: ("INTERNSHIP",),
            CanonicalProgram.LFA_FOOTBALL_PLAYER: ("LFA_FOOTBALL_PLAYER",),
        }[resolution.canonical_program]

        matches = self.db.query(UserLicense).filter(
            UserLicense.user_id == user_id,
            or_(
                UserLicense.canonical_program_id == canonical,
                UserLicense.specialization_type.in_(proven_storage_ids),
            ),
        ).all()
        if len(matches) > 1:
            raise ValueError("Conflicting license identities require manual review")

        user_license = matches[0] if matches else None
        if user_license is None:
            user_license = UserLicense(
                user_id=user_id,
                specialization_type=canonical,
                canonical_program_id=canonical,
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc)
            )
            self.db.add(user_license)
            self.db.commit()
            self.db.refresh(user_license)
        
        return user_license

    def advance_license(
        self, 
        user_id: int, 
        specialization: str, 
        target_level: int,
        advanced_by: int,
        reason: str = "",
        requirements_met: str = ""
    ) -> Dict[str, Any]:
        """Advance a user's license to a new level.

        Invariant: after validate_advancement passes, target_level > current_level
        is always True (validate_advancement rejects target <= current).
        Therefore the level_changed guard below line 151 is always True at runtime.
        """
        specialization = specialization.upper()
        
        # Get or create user license
        license = self.get_or_create_user_license(user_id, specialization)
        
        # Validate advancement (P0 FIX: pass db session)
        max_level = LicenseSystemHelper.get_specialization_max_level(specialization, self.db)
        is_valid, message = LicenseSystemHelper.validate_advancement(
            license.current_level, target_level, max_level
        )
        
        if not is_valid:
            return {
                "success": False,
                "message": message,
                "license": license.to_dict()
            }
        
        # Create progression record
        progression = LicenseProgression(
            user_license_id=license.id,
            from_level=license.current_level,
            to_level=target_level,
            advanced_by=advanced_by,
            advancement_reason=reason,
            requirements_met=requirements_met
        )
        
        # Update license
        old_level = license.current_level
        license.current_level = target_level
        license.max_achieved_level = max(license.max_achieved_level, target_level)
        license.last_advanced_at = datetime.now(timezone.utc)

        self.db.add(progression)
        self.db.commit()
        self.db.refresh(license)

        # 🔄 P1: AUTO-SYNC License → Progress after advancement
        # Use separate session for sync to avoid contaminating main transaction
        sync_result = None
        # NOTE: level_changed is always True here — validate_advancement (called above)
        # already rejected any path where target_level <= old_level.  The guard is kept
        # as a defensive safety net but the False-branch is structurally unreachable.
        level_changed = (old_level != target_level)
        if level_changed:
            from app.database import SessionLocal
            sync_db = SessionLocal()
            try:
                sync_service = ProgressLicenseSyncService(sync_db)
                sync_result = sync_service.sync_license_to_progress(
                    user_id=user_id,
                    specialization=specialization
                )
                sync_db.commit()
                if not sync_result.get('success'):
                    # Log warning but don't fail the advancement
                    logger = logging.getLogger(__name__)
                    logger.warning(
                        f"Auto-sync (License→Progress) failed for user {user_id}, {specialization}: {sync_result.get('message')}"
                    )
            except Exception as e:
                # Log error but don't fail the license advancement
                # Sync uses separate session, so failure doesn't affect main operation
                sync_db.rollback()
                logger = logging.getLogger(__name__)
                logger.error(f"Auto-sync exception for user {user_id}: {str(e)}")
            finally:
                sync_db.close()

        return {
            "success": True,
            "message": f"Successfully advanced to level {target_level}",
            "license": license.to_dict(),
            "progression": progression.to_dict(),
            "sync_result": sync_result  # Include sync result for transparency
        }

    def get_specialization_progression_path(self, specialization: str) -> List[Dict[str, Any]]:
        """Get the complete progression path for a specialization"""
        specialization = specialization.upper()
        
        metadata = self.db.query(LicenseMetadata).filter(
            LicenseMetadata.specialization_type == specialization
        ).order_by(LicenseMetadata.level_number).all()
        
        return [meta.to_dict() for meta in metadata]

    def get_user_license_dashboard(self, user_id: int) -> Dict[str, Any]:
        """Get comprehensive license dashboard data for a user"""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"error": "User not found"}
        
        # Get all user licenses
        licenses = self.get_user_licenses(user_id)
        
        # Get available specializations
        available_specializations = []
        for spec_type in LicenseType:
            spec_name = spec_type.value
            metadata = self.get_all_license_metadata(spec_name)
            if metadata:
                available_specializations.append({
                    "type": spec_name,
                    "display_name": self._get_specialization_display_name(spec_name),
                    "max_level": LicenseSystemHelper.get_specialization_max_level(spec_name, self.db),
                    "levels": metadata
                })
        
        # Calculate overall progress
        total_possible_levels = sum(
            LicenseSystemHelper.get_specialization_max_level(spec.value, self.db) 
            for spec in LicenseType
        )
        
        current_total_levels = sum(license.get('current_level', 0) for license in licenses)
        overall_progress = (current_total_levels / total_possible_levels * 100) if total_possible_levels > 0 else 0
        
        return {
            "user": {
                "id": user.id,
                "name": user.name,
                "specialization": user.specialization.value if user.specialization else None
            },
            "licenses": licenses,
            "available_specializations": available_specializations,
            "overall_progress": {
                "current_levels": current_total_levels,
                "total_possible": total_possible_levels,
                "percentage": round(overall_progress, 1)
            },
            "recent_activity": self._get_recent_license_activity(user_id)
        }

    def _get_specialization_display_name(self, specialization: str) -> str:
        """Get display name for specialization"""
        display_names = {
            "COACH": "Coach (LFA Edzői Licensz)",
            "PLAYER": "Player (GānCuju™️©️ Rendszer)",
            "INTERNSHIP": "Internship (IT Karrier)"
        }
        return display_names.get(specialization, specialization)

    def _get_recent_license_activity(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent license progression activity for a user"""
        progressions = self.db.query(LicenseProgression).join(
            UserLicense, LicenseProgression.user_license_id == UserLicense.id
        ).filter(
            UserLicense.user_id == user_id
        ).order_by(
            LicenseProgression.advanced_at.desc()
        ).limit(limit).all()
        
        activity = []
        for prog in progressions:
            # Get license info
            license = self.db.query(UserLicense).filter(UserLicense.id == prog.user_license_id).first()
            
            # Get level metadata
            from_meta = self.get_license_metadata_by_level(license.specialization_type, prog.from_level)
            to_meta = self.get_license_metadata_by_level(license.specialization_type, prog.to_level)
            
            activity.append({
                "progression": prog.to_dict(),
                "specialization": license.specialization_type,
                "from_level": from_meta,
                "to_level": to_meta
            })
        
        return activity

    def get_license_requirements_check(self, user_id: int, specialization: str, target_level: int) -> Dict[str, Any]:
        """Check if user meets requirements for license advancement"""
        specialization = specialization.upper()
        
        # Get current license
        license = self.get_or_create_user_license(user_id, specialization)
        
        # Get target level metadata
        target_meta = self.get_license_metadata_by_level(specialization, target_level)
        if not target_meta:
            return {"error": "Target level not found"}
        
        # Validate advancement possibility
        max_level = LicenseSystemHelper.get_specialization_max_level(specialization, self.db)
        is_valid, message = LicenseSystemHelper.validate_advancement(
            license.current_level, target_level, max_level
        )
        
        if not is_valid:
            return {"error": message}
        
        # Get requirements from metadata
        requirements = target_meta.get('advancement_criteria', {})
        
        # Check each requirement (this would be expanded with actual requirement checking logic)
        requirement_status = {}
        for req_type, req_value in requirements.items():
            requirement_status[req_type] = self._check_requirement(user_id, req_type, req_value)
        
        all_met = all(status.get('met', False) for status in requirement_status.values())
        
        return {
            "user_id": user_id,
            "specialization": specialization,
            "current_level": license.current_level,
            "target_level": target_level,
            "target_metadata": target_meta,
            "requirements": requirement_status,
            "all_requirements_met": all_met,
            "can_advance": all_met
        }

    def _check_requirement(self, user_id: int, req_type: str, req_value: Any) -> Dict[str, Any]:
        """Check a specific requirement for a user"""
        # This would be expanded with actual requirement checking logic
        # For now, return a placeholder structure
        return {
            "type": req_type,
            "required": req_value,
            "current": 0,  # Would be calculated based on user data
            "met": False,  # Would be determined based on comparison
            "description": f"Requirement: {req_type} = {req_value}"
        }

    def get_marketing_content(self, specialization: str, level: Optional[int] = None) -> Dict[str, Any]:
        """Get marketing content for license levels"""
        specialization = specialization.upper()
        
        query = self.db.query(LicenseMetadata).filter(
            LicenseMetadata.specialization_type == specialization
        )
        
        if level is not None:
            query = query.filter(LicenseMetadata.level_number == level)
            metadata = query.first()
            return metadata.to_dict() if metadata else {}
        else:
            metadata = query.order_by(LicenseMetadata.level_number).all()
            return {
                "specialization": specialization,
                "display_name": self._get_specialization_display_name(specialization),
                "levels": [meta.to_dict() for meta in metadata]
            }
