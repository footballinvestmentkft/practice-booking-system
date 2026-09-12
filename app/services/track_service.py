"""
Track-Based Education Service
Handles track enrollment, progress tracking, and certificate generation
"""

from typing import List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..models import (
    Track, Module, UserTrackProgress, 
    UserModuleProgress, TrackProgressStatus, 
    ModuleProgressStatus, Semester
)
from .certificate_service import CertificateService


class TrackEnrollmentError(Exception):
    """Custom exception for track enrollment errors"""


class TrackService:
    """Service for managing track-based education system"""
    
    def __init__(self, db: Session):
        self.db = db
        self.certificate_service = CertificateService(db)
    
    def get_available_tracks(self, user_id: str) -> List[Track]:
        """Get tracks available for enrollment by user"""
        # Get tracks user is not already enrolled in
        enrolled_track_ids = self.db.query(UserTrackProgress.track_id)\
            .filter(UserTrackProgress.user_id == user_id)\
            .filter(UserTrackProgress.status.notin_([TrackProgressStatus.DROPPED]))\
            .subquery()
        
        available_tracks = self.db.query(Track)\
            .filter(Track.is_active == True)\
            .filter(Track.specialization_id.is_(None))\
            .filter(~Track.id.in_(enrolled_track_ids))\
            .order_by(Track.name)\
            .all()
        
        return available_tracks
    
    def check_enrollment_eligibility(self, user_id: str, track_id: str, current_semester_id: str) -> Dict[str, Any]:
        """Check if user can enroll in track this semester"""
        # Check if already enrolled in this track
        existing_enrollment = self.db.query(UserTrackProgress)\
            .filter(UserTrackProgress.user_id == user_id)\
            .filter(UserTrackProgress.track_id == track_id)\
            .filter(UserTrackProgress.status != TrackProgressStatus.DROPPED)\
            .first()
        
        if existing_enrollment:
            return {
                "eligible": False,
                "reason": "Already enrolled in this track"
            }

        track = self.db.query(Track).filter(Track.id == track_id).first()
        if not track:
            return {
                "eligible": False,
                "reason": "Track not found"
            }

        # PC3 canonical tracks are entitlement-driven and may only be exposed
        # through EducationContentService's published-release boundary.
        if isinstance(track.specialization_id, str) and track.specialization_id:
            return {
                "eligible": False,
                "reason": "Canonical education tracks cannot use legacy enrollment"
            }
        
        # Check semester enrollment limit (max 1 new track per semester)
        current_semester_enrollments = self.db.query(UserTrackProgress)\
            .join(Semester)\
            .filter(UserTrackProgress.user_id == user_id)\
            .filter(func.extract('year', UserTrackProgress.enrollment_date) == 
                   func.extract('year', datetime.now()))\
            .filter(func.extract('month', UserTrackProgress.enrollment_date) >= 
                   func.extract('month', datetime.now()) - 6)\
            .count()
        
        if current_semester_enrollments >= 1:
            return {
                "eligible": False, 
                "reason": "Maximum 1 track enrollment per semester allowed"
            }
        
        # Check track prerequisites
        if track.prerequisites:
            # Check if user has completed prerequisite tracks
            for prereq_track_code in track.prerequisites.get('required_tracks', []):
                prereq_completed = self.db.query(UserTrackProgress)\
                    .join(Track)\
                    .filter(UserTrackProgress.user_id == user_id)\
                    .filter(Track.code == prereq_track_code)\
                    .filter(UserTrackProgress.status == TrackProgressStatus.COMPLETED)\
                    .first()
                
                if not prereq_completed:
                    return {
                        "eligible": False,
                        "reason": f"Prerequisite track '{prereq_track_code}' not completed"
                    }
        
        return {"eligible": True}
    
    def enroll_user_in_track(self, user_id: str, track_id: str, current_semester_id: str) -> UserTrackProgress:
        """Enroll user in a track with validation"""
        eligibility = self.check_enrollment_eligibility(user_id, track_id, current_semester_id)
        
        if not eligibility["eligible"]:
            raise TrackEnrollmentError(eligibility["reason"])
        
        # Create track progress record
        track_progress = UserTrackProgress(
            user_id=user_id,
            track_id=track_id,
            enrollment_date=datetime.utcnow(),
            current_semester=1,
            status=TrackProgressStatus.ENROLLED
        )
        
        self.db.add(track_progress)
        self.db.commit()
        self.db.refresh(track_progress)
        
        # Initialize module progresses for mandatory modules
        self._initialize_module_progresses(track_progress)
        
        return track_progress
    
    def _initialize_module_progresses(self, track_progress: UserTrackProgress):
        """Initialize module progress records for track"""
        modules = self.db.query(Module)\
            .filter(Module.track_id == track_progress.track_id)\
            .filter(Module.is_mandatory == True)\
            .order_by(Module.order_in_track)\
            .all()
        
        for module in modules:
            module_progress = UserModuleProgress(
                user_track_progress_id=track_progress.id,
                module_id=module.id,
                status=ModuleProgressStatus.NOT_STARTED
            )
            self.db.add(module_progress)
        
        self.db.commit()
    
    def get_user_track_progress(self, user_id: str, track_id: str = None) -> List[UserTrackProgress]:
        """Get user's track progress"""
        query = self.db.query(UserTrackProgress)\
            .filter(UserTrackProgress.user_id == user_id)
        
        if track_id:
            query = query.filter(UserTrackProgress.track_id == track_id)
        
        return query.all()
    
    def start_track(self, user_track_progress_id: str) -> UserTrackProgress:
        """Start a track (move from enrolled to active)"""
        track_progress = self.db.query(UserTrackProgress)\
            .filter(UserTrackProgress.id == user_track_progress_id)\
            .first()
        
        if not track_progress:
            raise ValueError("Track progress not found")
        
        if track_progress.status != TrackProgressStatus.ENROLLED:
            raise ValueError("Track must be in enrolled status to start")
        
        track_progress.start()
        self.db.commit()
        
        return track_progress
    
    def start_module(self, user_track_progress_id: str, module_id: str) -> UserModuleProgress:
        """Start a module"""
        module_progress = self.db.query(UserModuleProgress)\
            .filter(UserModuleProgress.user_track_progress_id == user_track_progress_id)\
            .filter(UserModuleProgress.module_id == module_id)\
            .first()
        
        if not module_progress:
            # Create module progress if it doesn't exist
            module_progress = UserModuleProgress(
                user_track_progress_id=user_track_progress_id,
                module_id=module_id,
                status=ModuleProgressStatus.NOT_STARTED
            )
            self.db.add(module_progress)
        
        if module_progress.status == ModuleProgressStatus.NOT_STARTED:
            module_progress.start()
            self.db.commit()
        
        return module_progress
    
    def complete_module(self, user_track_progress_id: str, module_id: str, grade: float = None) -> UserModuleProgress:
        """Complete a module and check for track completion"""
        module_progress = self.db.query(UserModuleProgress)\
            .filter(UserModuleProgress.user_track_progress_id == user_track_progress_id)\
            .filter(UserModuleProgress.module_id == module_id)\
            .first()
        
        if not module_progress:
            raise ValueError("Module progress not found")
        
        module_progress.complete(grade)
        self.db.commit()
        
        # Check if track is complete
        track_progress = self.db.query(UserTrackProgress)\
            .filter(UserTrackProgress.id == user_track_progress_id)\
            .first()
        
        self._check_track_completion(track_progress)
        
        return module_progress
    
    def _check_track_completion(self, track_progress: UserTrackProgress):
        """Check if track is completed and handle certificate generation"""
        # Recalculate completion percentage
        track_progress.calculate_completion_percentage()
        
        # Check if all mandatory modules are completed
        if track_progress.completion_percentage >= 100.0:
            track_progress.complete()
            
            # Generate certificate if eligible
            if track_progress.is_ready_for_certificate:
                certificate = self.certificate_service.generate_certificate(track_progress)
                track_progress.certificate_id = certificate.id
            
            self.db.commit()
    
    def get_track_analytics(self, track_id: str) -> Dict[str, Any]:
        """Get analytics for a track"""
        # Total enrollments
        total_enrollments = self.db.query(UserTrackProgress)\
            .filter(UserTrackProgress.track_id == track_id)\
            .count()
        
        # Active enrollments
        active_enrollments = self.db.query(UserTrackProgress)\
            .filter(UserTrackProgress.track_id == track_id)\
            .filter(UserTrackProgress.status.in_([TrackProgressStatus.ENROLLED, TrackProgressStatus.ACTIVE]))\
            .count()
        
        # Completions
        completions = self.db.query(UserTrackProgress)\
            .filter(UserTrackProgress.track_id == track_id)\
            .filter(UserTrackProgress.status == TrackProgressStatus.COMPLETED)\
            .count()
        
        # Average completion time
        completed_progresses = self.db.query(UserTrackProgress)\
            .filter(UserTrackProgress.track_id == track_id)\
            .filter(UserTrackProgress.status == TrackProgressStatus.COMPLETED)\
            .filter(UserTrackProgress.started_at.isnot(None))\
            .filter(UserTrackProgress.completed_at.isnot(None))\
            .all()
        
        avg_completion_days = 0
        if completed_progresses:
            total_days = sum([p.duration_days for p in completed_progresses])
            avg_completion_days = total_days / len(completed_progresses)
        
        return {
            "track_id": track_id,
            "total_enrollments": total_enrollments,
            "active_enrollments": active_enrollments,
            "completions": completions,
            "completion_rate": (completions / total_enrollments * 100) if total_enrollments > 0 else 0,
            "average_completion_days": round(avg_completion_days, 1)
        }
    
    def get_user_parallel_tracks(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all tracks a user is currently enrolled in (parallel enrollment support)"""
        progresses = self.db.query(UserTrackProgress)\
            .filter(UserTrackProgress.user_id == user_id)\
            .filter(UserTrackProgress.status.in_([
                TrackProgressStatus.ENROLLED, 
                TrackProgressStatus.ACTIVE,
                TrackProgressStatus.COMPLETED
            ]))\
            .all()
        
        result = []
        for progress in progresses:
            track_data = {
                "track_progress_id": str(progress.id),
                "track": progress.track,
                "status": progress.status.value,
                "completion_percentage": progress.completion_percentage,
                "enrollment_date": progress.enrollment_date,
                "current_semester": progress.current_semester,
                "certificate_id": str(progress.certificate_id) if progress.certificate_id else None
            }
            result.append(track_data)
        
        return result
