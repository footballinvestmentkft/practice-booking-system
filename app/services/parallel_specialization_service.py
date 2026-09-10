"""
🎓 Parallel Specialization Service
Manages multiple simultaneous specializations per user with semester-based progression
"""
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, date

from ..models.user import User
from ..models.license import UserLicense
from ..services.license_service import LicenseService
from ..services.canonical_policy import (
    CanonicalProgram,
    PROGRAM_MINIMUM_AGES,
    calculate_age,
    resolve_license_program,
    resolve_program_id,
)
from ..services.program_eligibility_service import is_user_eligible_for_program


class ParallelSpecializationService:
    """Service for managing multiple parallel specializations"""

    def __init__(self, db: Session):
        self.db = db
        self.license_service = LicenseService(db)
    # Ordered list of all specialization types offered in every semester
    _SPEC_TYPES = ['LFA_FOOTBALL_PLAYER', 'LFA_COACH', 'GANCUJU_PLAYER', 'INTERNSHIP']

    # Human-readable display name used in failure reason strings
    _SPEC_DISPLAY_NAMES = {
        'LFA_FOOTBALL_PLAYER': 'LFA Football Player specializáció',
        'LFA_COACH': 'LFA Coach specializáció',
        'GANCUJU_PLAYER': 'GānCuju Player specializáció',
        'INTERNSHIP': 'Gyakornoki program',
    }

    # Success reason strings: keyed by (spec_type, sem_key) where
    # sem_key=1 for semester 1 (has extra context) and 0 for semester 2+
    _SUCCESS_REASONS = {
        ('LFA_FOOTBALL_PLAYER', 1): 'LFA Football Player - alapképzés (min. 5 év) - minden követelmény teljesített',
        ('LFA_COACH', 1): 'LFA Coach - edzői képzés (min. 14 év) - minden követelmény teljesített',
        ('GANCUJU_PLAYER', 1): 'GānCuju Player (min. 5 év) - minden követelmény teljesített',
        ('INTERNSHIP', 1): 'Gyakornoki program (min. 18 év) - minden követelmény teljesített',
        ('LFA_FOOTBALL_PLAYER', 0): 'LFA Football Player (min. 5 év) - minden követelmény teljesített',
        ('LFA_COACH', 0): 'LFA Coach (min. 14 év) - minden követelmény teljesített',
        ('GANCUJU_PLAYER', 0): 'GānCuju Player (min. 5 év) - minden követelmény teljesített',
        ('INTERNSHIP', 0): 'Gyakornoki program (min. 18 év) - minden követelmény teljesített',
    }
    
    def calculate_age(self, birth_date: date) -> int:
        """Compatibility facade for the canonical age calculation."""
        return calculate_age(birth_date, date.today())
    
    def check_age_requirement(self, user_id: int, specialization: str) -> Dict[str, Any]:
        """Check if user meets age requirement for specialization"""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return {'meets_requirement': False, 'reason': 'User not found'}
        if not user.date_of_birth:
            return {'meets_requirement': False, 'reason': 'Születési dátum hiányzik a profilból'}
        resolution = resolve_program_id(specialization)
        if not resolution.usable:
            return {'meets_requirement': False, 'reason': 'Programazonosító manual review-t igényel'}
        user_age = self.calculate_age(user.date_of_birth.date())
        required_age = PROGRAM_MINIMUM_AGES[resolution.canonical_program]
        meets_requirement, denial_reason = is_user_eligible_for_program(
            self.db, user, resolution.canonical_program
        )
        return {
            'meets_requirement': meets_requirement,
            'user_age': user_age,
            'required_age': required_age,
            'reason': denial_reason or f'Életkor követelmény teljesítve ({user_age} év)'
        }

    def check_payment_requirement(self, user_id: int, specialization_type: str = None, semester: int = None) -> Dict[str, Any]:
        """
        Check if user has verified payment for specialization enrollment
        Enhanced to support semester-specific and specialization-specific verification
        """
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return {'payment_verified': False, 'reason': 'User not found'}

        # Admins and instructors can always enroll
        if user.role.value in ['admin', 'instructor']:
            return {
                'payment_verified': True,
                'reason': 'Admin/Instructor - befizetés nem szükséges',
                'verified_at': None,
                'verified_by': None,
                'payment_status_display': '✅ Admin/Instructor'
            }

        # Get current user semester and active specializations
        current_semester = semester or self.get_user_semester_count(user_id)
        current_licenses = self.get_user_active_specializations(user_id)
        current_spec_count = len(current_licenses)
        
        # Enhanced payment verification logic
        payment_verified = user.payment_verified
        
        # Special logic for multiple specializations in semester 2+
        if current_semester >= 2 and specialization_type:
            # If user already has one specialization and wants to add another
            if current_spec_count >= 1 and not any(spec['specialization_type'] == specialization_type for spec in current_licenses):
                # For second/third specialization, payment is considered verified if:
                # 1. User has basic payment verified (first specialization was paid)
                # 2. OR admin has specifically verified payment for this user
                
                payment_verified = user.payment_verified
                
                if payment_verified:
                    reason = f'Alapbefizetés ellenőrizve - további specializációk hozzáadhatók (Szemeszter {current_semester})'
                else:
                    reason = f'Alapbefizetés szükséges a specializációk hozzáadásához (Szemeszter {current_semester})'
            else:
                # First specialization or existing specialization
                reason = 'Alapbefizetés ellenőrizve és jóváhagyva' if payment_verified else 'Alapbefizetés még nem került ellenőrzésre és jóváhagyásra'
        else:
            # Semester 1 or no specific specialization
            reason = 'Alapbefizetés ellenőrizve és jóváhagyva' if payment_verified else 'Alapbefizetés még nem került ellenőrzésre és jóváhagyásra'
        
        return {
            'payment_verified': payment_verified,
            'reason': reason,
            'verified_at': user.payment_verified_at,
            'verified_by': user.payment_verified_by,
            'payment_status_display': user.payment_status_display,
            'semester_context': current_semester,
            'current_specialization_count': current_spec_count
        }

    def get_user_semester_count(self, user_id: int) -> int:
        """Calculate user's semester count based on their activity"""
        # For now, return a simple calculation
        # In production, this would be based on enrollment history
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return 1
        
        # Simple logic: if user has at least one specialization, they're in semester 2+
        licenses = self.db.query(UserLicense).filter(UserLicense.user_id == user_id).count()
        return 2 if licenses > 0 else 1

    def get_available_specializations_for_semester(self, user_id: int, semester: int) -> List[Dict[str, Any]]:
        """Get available specializations based on semester and current progress.

        Enrollment caps per semester: 1 → max 1, 2 → max 2, 3+ → max 3.
        Returns an empty list immediately when the user has reached the cap.
        """
        current_licenses = self.get_user_active_specializations(user_id)
        current_spec_codes = [spec['specialization_type'] for spec in current_licenses]
        current_count = len(current_spec_codes)

        max_specs = {1: 1, 2: 2}.get(semester, 3)
        if current_count >= max_specs:
            return []

        # Semester 1 uses slightly different success-reason strings (extra context)
        sem_key = 1 if semester == 1 else 0

        available = []
        for spec_type in self._SPEC_TYPES:
            success_reason = self._SUCCESS_REASONS[(spec_type, sem_key)]
            entry = self._evaluate_specialization_type(
                user_id, spec_type, semester, current_spec_codes, success_reason
            )
            if entry is not None:
                available.append(entry)

        return available

    def _evaluate_specialization_type(
        self,
        user_id: int,
        spec_type: str,
        semester: int,
        current_spec_codes: List[str],
        success_reason: str,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate a single specialization type for availability.

        Returns a dict ready to append to the available list, or None when:
          - the user is already enrolled in this specialization, or
          - no LicenseMetadata row exists for spec_type at level 1.

        Age and payment checks are only performed when metadata exists,
        avoiding unnecessary DB queries for unconfigured specializations.
        """
        if spec_type in current_spec_codes:
            return None

        meta = self.license_service.get_license_metadata_by_level(spec_type, 1)
        if not meta:
            return None

        age_check = self.check_age_requirement(user_id, spec_type)
        payment_check = self.check_payment_requirement(user_id, spec_type, semester)
        can_start = age_check['meets_requirement'] and payment_check['payment_verified']

        if can_start:
            reason = success_reason
        else:
            reason_parts = []
            if not age_check['meets_requirement']:
                reason_parts.append(f"Korhatár: {age_check['reason']}")
            if not payment_check['payment_verified']:
                reason_parts.append(f"Befizetés: {payment_check['reason']}")
            display = self._SPEC_DISPLAY_NAMES.get(spec_type, spec_type)
            reason = f'{display} - {"; ".join(reason_parts)}'

        return {
            **meta,
            'can_start': can_start,
            'requirement_met': can_start,
            'reason': reason,
            'age_requirement': age_check,
            'payment_requirement': payment_check,
        }

    def get_user_active_specializations(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all active specializations for a user"""
        licenses = self.db.query(UserLicense).filter(
            UserLicense.user_id == user_id
        ).all()
        
        result = []
        for license in licenses:
            # Get current level metadata
            current_meta = self.license_service.get_license_metadata_by_level(
                license.specialization_type, 
                license.current_level
            )
            
            result.append({
                'specialization_type': license.specialization_type,
                'current_level': license.current_level,
                'max_achieved_level': license.max_achieved_level,
                'started_at': license.started_at.isoformat() if license.started_at else None,
                'last_advanced_at': license.last_advanced_at.isoformat() if license.last_advanced_at else None,
                'current_level_metadata': current_meta,
                'license_id': license.id
            })
        
        return result

    def start_new_specialization(self, user_id: int, specialization: str) -> Dict[str, Any]:
        """Start a new specialization for a user"""
        resolution = resolve_program_id(specialization)
        if not resolution.usable:
            return {'success': False, 'message': 'Program identity requires manual review'}
        specialization = resolution.canonical_program.value
        if resolution.canonical_program is CanonicalProgram.LFA_FOOTBALL_PLAYER:
            return {
                'success': False,
                'message': 'FOOTBALL_ENTITLEMENT_REQUIRES_CANONICAL_COMMAND',
            }
        
        # Check if user already has this specialization
        existing = next((
            row for row in self.db.query(UserLicense).filter(UserLicense.user_id == user_id).all()
            if resolve_license_program(row.canonical_program_id, row.specialization_type).canonical_program
            is resolution.canonical_program
        ), None)
        
        if existing:
            return {
                'success': False,
                'message': f'User already has {specialization} specialization',
                'license': existing.to_dict()
            }
        
        # Validate availability
        semester = self.get_user_semester_count(user_id)
        available = self.get_available_specializations_for_semester(user_id, semester)
        
        can_start = any(
            spec['specialization_type'] == specialization and spec['can_start'] 
            for spec in available
        )
        
        if not can_start:
            return {
                'success': False,
                'message': f'{specialization} not available for current semester/progress',
                'available_specializations': available
            }
        
        # Create new license
        new_license = UserLicense(
            user_id=user_id,
            specialization_type=specialization,
            canonical_program_id=specialization,
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc)
        )
        
        self.db.add(new_license)
        self.db.commit()
        self.db.refresh(new_license)
        
        return {
            'success': True,
            'message': f'Successfully started {specialization} specialization',
            'license': new_license.to_dict()
        }

    def get_user_specialization_dashboard(self, user_id: int) -> Dict[str, Any]:
        """Get comprehensive specialization dashboard for a user"""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return {'error': 'User not found'}
        
        semester = self.get_user_semester_count(user_id)
        active_specializations = self.get_user_active_specializations(user_id)
        available_specializations = self.get_available_specializations_for_semester(user_id, semester)
        
        # Get specialization progression info
        progression_info = {
            'semester_1': {
                'description': 'Alapképzés - Player specializáció',
                'available': ['PLAYER', 'INTERNSHIP'],
                'note': 'Első szemeszterben Player vagy Internship választható'
            },
            'semester_2_plus': {
                'description': 'Haladó képzés - párhuzamos specializációk',
                'available': ['PLAYER', 'COACH (Player után)', 'INTERNSHIP'],
                'note': 'Második szemesztertől Coach is választható Player mellett'
            }
        }
        
        return {
            'user': {
                'id': user.id,
                'name': user.name,
                'email': user.email
            },
            'current_semester': semester,
            'active_specializations': active_specializations,
            'available_specializations': available_specializations,
            'progression_info': progression_info,
            'parallel_progress': {
                'total_active': len(active_specializations),
                'can_add_more': len(available_specializations) > 0,
                'next_available': available_specializations[0] if available_specializations else None
            }
        }

    def get_specialization_combinations_by_semester(self) -> Dict[int, Dict[str, Any]]:
        """Get possible specialization combinations for each semester"""
        return {
            1: {
                'max_specializations': 1,
                'available': ['PLAYER', 'COACH', 'INTERNSHIP'],
                'combinations': [
                    ['PLAYER'],
                    ['COACH'],
                    ['INTERNSHIP']
                ],
                'description': 'Első szemeszter: Egy specializáció választható (Player VAGY Coach VAGY Internship)'
            },
            2: {
                'max_specializations': 2,
                'available': ['PLAYER', 'COACH', 'INTERNSHIP'],
                'combinations': [
                    ['PLAYER', 'COACH'],
                    ['PLAYER', 'INTERNSHIP'],
                    ['COACH', 'INTERNSHIP']
                ],
                'description': 'Második szemeszter: Két specializáció párhuzamosan'
            },
            3: {
                'max_specializations': 3,
                'available': ['PLAYER', 'COACH', 'INTERNSHIP'],
                'combinations': [
                    ['PLAYER', 'COACH', 'INTERNSHIP']
                ],
                'description': 'Harmadik szemeszter: Mind a három specializáció párhuzamosan'
            }
        }

    def validate_specialization_addition(self, user_id: int, new_specialization: str) -> Dict[str, Any]:
        """Validate if a new specialization can be added"""
        semester = self.get_user_semester_count(user_id)
        available = self.get_available_specializations_for_semester(user_id, semester)
        
        new_spec_available = next(
            (spec for spec in available if spec['specialization_type'] == new_specialization.upper()),
            None
        )
        
        if not new_spec_available:
            return {
                'valid': False,
                'reason': f'{new_specialization} not available for semester {semester}',
                'available_options': [spec['specialization_type'] for spec in available]
            }
        
        if not new_spec_available['can_start']:
            return {
                'valid': False,
                'reason': new_spec_available['reason'],
                'requirements': 'Player specialization required first'
            }
        
        return {
            'valid': True,
            'reason': new_spec_available['reason'],
            'semester': semester,
            'metadata': new_spec_available
        }
