"""
Tournament Instructor Assignment Lifecycle

This module handles instructor assignment workflow for tournaments.

TWO SCENARIOS SUPPORTED:

SCENARIO 1 - Direct Assignment:
1. Admin creates tournament → status: SEEKING_INSTRUCTOR
2. Admin directly assigns instructor (via admin UI or direct call) → status: PENDING_INSTRUCTOR_ACCEPTANCE
3. Instructor accepts assignment → status: INSTRUCTOR_CONFIRMED
4. Admin opens enrollment (via UI) → status: ENROLLMENT_OPEN
5. Tournament becomes ready for player enrollment

SCENARIO 2 - Application Workflow:
1. Admin creates tournament → status: SEEKING_INSTRUCTOR
2. Instructor applies to tournament (POST /tournaments/{id}/instructor-applications)
3. Admin approves application → status: INSTRUCTOR_CONFIRMED
4. Admin opens enrollment (via UI) → status: ENROLLMENT_OPEN
5. Tournament becomes ready for player enrollment

Authorization:
- Only INSTRUCTOR role can apply and accept assignments
- Only ADMIN role can approve applications
- Instructor must have active LFA_COACH license

⚠️ REFACTORING NOTE (P0-1 Phase 3 - 2026-01-23):
This module was EXTRACTED from instructor.py to separate assignment lifecycle logic.
See: REFACTORING_IMPLEMENTATION_PLAN.md
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.models.user import User, UserRole
from app.models.semester import Semester
from app.models.session import Session as SessionModel
from app.models.license import UserLicense
from app.models.instructor_assignment import InstructorAssignmentRequest, AssignmentRequestStatus
from app.dependencies import get_current_user

# Shared services - refactored imports
from app.services.shared import (
    require_admin,
    require_instructor,
    LicenseValidator,
    StatusHistoryRecorder
)
from app.repositories import TournamentRepository

router = APIRouter()


# ============================================================================
# REQUEST/RESPONSE SCHEMAS
# ============================================================================

class InstructorApplicationRequest(BaseModel):
    """Request schema for instructor application"""
    application_message: Optional[str] = None


class InstructorApplicationApprovalRequest(BaseModel):
    """Request schema for admin approval"""
    response_message: Optional[str] = None


class DirectAssignmentRequest(BaseModel):
    """Request schema for direct instructor assignment"""
    instructor_id: int
    assignment_message: Optional[str] = None


class DeclineApplicationRequest(BaseModel):
    """Request schema for declining an application"""
    decline_message: Optional[str] = None


# ============================================================================
# INSTRUCTOR ASSIGNMENT LIFECYCLE ENDPOINTS
# ============================================================================

@router.post("/{tournament_id}/instructor-assignment/accept")
def accept_instructor_assignment(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Instructor accepts tournament assignment.

    This endpoint transitions a tournament to INSTRUCTOR_CONFIRMED status.
    Supports two scenarios:
    1. APPLICATION_BASED: Tournament status SEEKING_INSTRUCTOR → INSTRUCTOR_CONFIRMED
    2. OPEN_ASSIGNMENT: Tournament status PENDING_INSTRUCTOR_ACCEPTANCE → INSTRUCTOR_CONFIRMED

    **Authorization:** INSTRUCTOR role only

    **Validations:**
    - Current user is INSTRUCTOR
    - Current user has active LFA_COACH license
    - Tournament exists
    - Tournament status is SEEKING_INSTRUCTOR or PENDING_INSTRUCTOR_ACCEPTANCE

    **Actions Performed:**
    - Updates semester.master_instructor_id = current_user.id
    - Updates semester.tournament_status = INSTRUCTOR_CONFIRMED
    - Updates all associated sessions.instructor_id = current_user.id

    **Returns:**
    - Tournament details
    - Number of sessions updated
    - Confirmation message

    **Example Response:**
    ```json
    {
        "message": "Tournament assignment accepted successfully",
        "tournament_id": 123,
        "tournament_name": "Youth Football Tournament 2026",
        "tournament_status": "INSTRUCTOR_CONFIRMED",
        "instructor_id": 5,
        "instructor_name": "Coach Smith",
        "sessions_updated": 3
    }
    ```

    **Raises:**
    - 403 FORBIDDEN: User is not an instructor or lacks LFA_COACH license
    - 404 NOT FOUND: Tournament not found
    - 400 BAD REQUEST: Tournament is not in valid status for acceptance
    """
    # REFACTORED: Use shared services
    require_instructor(current_user)

    tournament_repo = TournamentRepository(db)
    tournament = tournament_repo.get_or_404(tournament_id)

    # ============================================================================
    # VALIDATION 4: Tournament status allows instructor acceptance
    # (checked before eligibility so wrong-status returns 400, not 403)
    # ============================================================================
    # Two scenarios:
    # 1. SEEKING_INSTRUCTOR: Instructor volunteers for APPLICATION_BASED tournaments
    # 2. PENDING_INSTRUCTOR_ACCEPTANCE: Admin directly assigned instructor (OPEN_ASSIGNMENT)
    valid_statuses = ["SEEKING_INSTRUCTOR", "PENDING_INSTRUCTOR_ACCEPTANCE"]

    if tournament.tournament_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tournament_status",
                "message": f"Tournament cannot accept instructor assignment in current status",
                "current_status": tournament.tournament_status,
                "required_status": "SEEKING_INSTRUCTOR or PENDING_INSTRUCTOR_ACCEPTANCE",
                "tournament_id": tournament_id,
                "tournament_name": tournament.name
            }
        )

    # Eligibility check — license, expiry, AND level vs tournament age group.
    # Runs after status check so wrong-status always returns 400 before this.
    from app.services.tournament.instructor_eligibility_service import (
        is_eligible_master_instructor,
        resolve_tournament_age_groups,
    )
    _age_groups = resolve_tournament_age_groups(tournament)
    _eligible, _reason = is_eligible_master_instructor(db, current_user.id, _age_groups)
    if not _eligible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "instructor_not_eligible",
                "message": f"Instructor not eligible for this tournament: {_reason}",
                "user_id": current_user.id,
            },
        )

    # ============================================================================
    # ACTION 1: Update tournament master_instructor_id
    # ============================================================================
    tournament.master_instructor_id = current_user.id

    # ============================================================================
    # ACTION 2: Update tournament status to INSTRUCTOR_CONFIRMED
    # ============================================================================
    # After instructor accepts, tournament is ready for admin to open enrollment
    tournament.tournament_status = "INSTRUCTOR_CONFIRMED"
    tournament.status = "INSTRUCTOR_ASSIGNED"  # Keep old status field in sync (maps to INSTRUCTOR_ASSIGNED enum)

    # ============================================================================
    # ACTION 3: Update all sessions with instructor_id
    # ============================================================================
    sessions = db.query(SessionModel).filter(
        SessionModel.semester_id == tournament_id
    ).all()

    for session in sessions:
        session.instructor_id = current_user.id

    # ============================================================================
    # COMMIT TRANSACTION
    # ============================================================================
    db.commit()
    db.refresh(tournament)

    # ============================================================================
    # RETURN SUCCESS RESPONSE
    # ============================================================================
    return {
        "message": "Tournament assignment accepted successfully",
        "tournament_id": tournament.id,
        "tournament_name": tournament.name,
        "tournament_code": tournament.code,
        "status": tournament.tournament_status,
        "instructor_id": current_user.id,
        "instructor_name": current_user.name,
        "instructor_email": current_user.email,
        "sessions_updated": len(sessions),
        "tournament_date": tournament.start_date.isoformat() if tournament.start_date else None
    }


@router.post("/{tournament_id}/instructor-applications")
def apply_to_tournament(
    tournament_id: int,
    request_data: InstructorApplicationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    SCENARIO 2: Instructor applies to tournament.

    This endpoint allows an instructor to apply to lead a tournament that is
    seeking an instructor.

    **Authorization:** INSTRUCTOR role only

    **Validations:**
    - Current user is INSTRUCTOR
    - Current user has active LFA_COACH license
    - Tournament exists
    - Tournament status is SEEKING_INSTRUCTOR
    - No existing PENDING or ACCEPTED application from this instructor

    **Actions Performed:**
    - Creates InstructorAssignmentRequest with status PENDING
    - Records application_message from instructor

    **Returns:**
    - Application details
    - Application ID
    - Confirmation message

    **Raises:**
    - 403 FORBIDDEN: User is not an instructor or lacks LFA_COACH license
    - 404 NOT FOUND: Tournament not found
    - 400 BAD REQUEST: Invalid tournament status or duplicate application
    """
    # REFACTORED: Use shared services
    require_instructor(current_user)

    tournament_repo = TournamentRepository(db)
    tournament = tournament_repo.get_or_404(tournament_id)

    # Validate coach license with age group check
    LicenseValidator.validate_coach_license(
        db,
        current_user.id,
        age_group=tournament.age_group,
        user_email=current_user.email,
        tournament_id=tournament_id,
        tournament_name=tournament.name
    )

    # ============================================================================
    # VALIDATION 4: Tournament must be APPLICATION_BASED (not OPEN_ASSIGNMENT)
    # ============================================================================
    if tournament.assignment_type == "OPEN_ASSIGNMENT":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "direct_assignment_only",
                "message": "This tournament uses direct assignment. Instructors cannot apply - admin must directly assign.",
                "assignment_type": "OPEN_ASSIGNMENT",
                "tournament_id": tournament_id,
                "tournament_name": tournament.name
            }
        )

    # ============================================================================
    # VALIDATION 5: Tournament status is SEEKING_INSTRUCTOR
    # ============================================================================
    if tournament.tournament_status != "SEEKING_INSTRUCTOR":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tournament_status",
                "message": f"Tournament is not accepting instructor applications",
                "current_status": tournament.tournament_status,
                "required_status": "SEEKING_INSTRUCTOR",
                "tournament_id": tournament_id,
                "tournament_name": tournament.name
            }
        )

    # ============================================================================
    # VALIDATION 6: Check for existing application
    # ============================================================================
    existing_application = db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.semester_id == tournament_id,
        InstructorAssignmentRequest.instructor_id == current_user.id,
        InstructorAssignmentRequest.status.in_([
            AssignmentRequestStatus.PENDING,
            AssignmentRequestStatus.ACCEPTED
        ])
    ).first()

    if existing_application:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "duplicate_application",
                "message": f"You already have a {existing_application.status.value} application for this tournament",
                "application_id": existing_application.id,
                "application_status": existing_application.status.value
            }
        )

    # ============================================================================
    # ACTION: Create application record
    # ============================================================================
    application = InstructorAssignmentRequest(
        semester_id=tournament_id,
        instructor_id=current_user.id,
        requested_by=None,  # Instructor-initiated application
        status=AssignmentRequestStatus.PENDING,
        request_message=request_data.application_message,
        priority=0
    )

    db.add(application)
    db.commit()
    db.refresh(application)

    # ============================================================================
    # RETURN SUCCESS RESPONSE
    # ============================================================================
    return {
        "message": "Application submitted successfully",
        "application_id": application.id,
        "tournament_id": tournament.id,
        "tournament_name": tournament.name,
        "instructor_id": current_user.id,
        "instructor_name": current_user.name,
        "instructor_email": current_user.email,
        "status": application.status.value,
        "applied_at": application.created_at.isoformat(),
        "application_message": application.request_message
    }


@router.post("/{tournament_id}/instructor-applications/{application_id}/approve")
def approve_instructor_application(
    tournament_id: int,
    application_id: int,
    approval_data: InstructorApplicationApprovalRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    SCENARIO 2: Admin approves instructor application (APPLICATION_BASED).

    This endpoint allows an admin to approve an instructor's application to lead
    a tournament. For APPLICATION_BASED tournaments, approval automatically assigns
    the instructor (no further acceptance needed from instructor).

    **Authorization:** ADMIN role only

    **Validations:**
    - Current user is ADMIN
    - Tournament exists
    - Tournament must be APPLICATION_BASED (not OPEN_ASSIGNMENT)
    - Application exists and belongs to the tournament
    - Application status is PENDING
    - Tournament status is SEEKING_INSTRUCTOR

    **Actions Performed:**
    - Updates application status to ACCEPTED
    - Records responded_at timestamp
    - Records optional response_message from admin
    - Assigns instructor to tournament (master_instructor_id)
    - Updates tournament status to ONGOING
    - Creates notification for instructor

    **Returns:**
    - Application details
    - Tournament updated status

    **Raises:**
    - 403 FORBIDDEN: User is not an admin
    - 404 NOT FOUND: Tournament or application not found
    - 400 BAD REQUEST: Invalid status or already processed
    """
    # REFACTORED: Use shared services
    require_admin(current_user)

    tournament_repo = TournamentRepository(db)
    tournament = tournament_repo.get_or_404(tournament_id)

    # ============================================================================
    # VALIDATION 3: Tournament must be APPLICATION_BASED (not OPEN_ASSIGNMENT)
    # ============================================================================
    if tournament.assignment_type == "OPEN_ASSIGNMENT":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "direct_assignment_only",
                "message": "This tournament uses direct assignment. Cannot approve applications for OPEN_ASSIGNMENT tournaments.",
                "assignment_type": "OPEN_ASSIGNMENT",
                "tournament_id": tournament_id,
                "tournament_name": tournament.name
            }
        )

    # ============================================================================
    # VALIDATION 4: Application exists and belongs to tournament
    # ============================================================================
    application = db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.id == application_id,
        InstructorAssignmentRequest.semester_id == tournament_id
    ).first()

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "application_not_found",
                "message": f"Application {application_id} not found for tournament {tournament_id}",
                "application_id": application_id,
                "tournament_id": tournament_id
            }
        )

    # ============================================================================
    # VALIDATION 4: Application status is PENDING
    # ============================================================================
    if application.status != AssignmentRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_application_status",
                "message": f"Application cannot be approved in current status",
                "current_status": application.status.value,
                "required_status": "PENDING",
                "application_id": application_id
            }
        )

    # ============================================================================
    # VALIDATION 5: Tournament status is SEEKING_INSTRUCTOR
    # ============================================================================
    if tournament.tournament_status != "SEEKING_INSTRUCTOR":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tournament_status",
                "message": f"Tournament is not seeking instructor",
                "current_status": tournament.tournament_status,
                "required_status": "SEEKING_INSTRUCTOR",
                "tournament_id": tournament_id
            }
        )

    # ============================================================================
    # ACTION: Approve application
    # ============================================================================
    application.status = AssignmentRequestStatus.ACCEPTED
    application.responded_at = datetime.utcnow()
    application.response_message = approval_data.response_message

    # Get instructor details
    instructor = db.query(User).filter(User.id == application.instructor_id).first()

    # Update tournament status and assign instructor
    # For APPLICATION_BASED: Approval = automatic assignment (status → INSTRUCTOR_CONFIRMED)
    # Instructor already showed interest by applying, no further acceptance needed
    old_tournament_status = tournament.tournament_status
    tournament.master_instructor_id = application.instructor_id
    tournament.tournament_status = "INSTRUCTOR_CONFIRMED"

    # REFACTORED: Use StatusHistoryRecorder
    recorder = StatusHistoryRecorder(db)
    recorder.record_status_change(
        tournament_id=tournament.id,
        old_status=old_tournament_status,
        new_status="INSTRUCTOR_CONFIRMED",
        changed_by=current_user.id,
        reason=f"Admin approved instructor application from {instructor.name} - automatically assigned",
        metadata={
            "application_id": application.id,
            "instructor_id": instructor.id,
            "instructor_name": instructor.name,
            "assignment_type": "APPLICATION_BASED"
        }
    )

    db.commit()
    db.refresh(application)
    db.refresh(tournament)

    # ============================================================================
    # CREATE NOTIFICATION for instructor
    # ============================================================================
    from app.services.notification_service import create_tournament_application_approved_notification
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"🔍 DEBUG: Creating notification for application approval")
    logger.info(f"   - Application ID: {application.id}")
    logger.info(f"   - Instructor ID: {instructor.id}")
    logger.info(f"   - Tournament ID: {tournament.id}")
    logger.info(f"   - Tournament Name: {tournament.name}")

    try:
        notification = create_tournament_application_approved_notification(
            db=db,
            instructor_id=instructor.id,
            tournament=tournament,
            response_message=approval_data.response_message or "Your application has been approved!",
            request_id=application.id
        )

        logger.info(f"✅ DEBUG: Notification object created successfully")
        logger.info(f"   - Notification type: {notification.type}")
        logger.info(f"   - User ID: {notification.user_id}")
        logger.info(f"   - Related request ID: {notification.related_request_id}")
        logger.info(f"   - Related semester ID: {notification.related_semester_id}")

        # Commit the notification
        logger.info(f"🔍 DEBUG: About to commit notification to database...")
        db.commit()
        logger.info(f"✅ DEBUG: Notification committed successfully!")

    except Exception as e:
        logger.error(f"❌ DEBUG: Error during notification creation/commit!")
        logger.error(f"   - Error type: {type(e).__name__}")
        logger.error(f"   - Error message: {str(e)}")
        logger.error(f"   - Application ID being used: {application.id}")
        logger.error(f"   - Instructor ID being used: {instructor.id}")
        logger.error(f"   - Tournament ID being used: {tournament.id}")

        # Rollback the notification (but keep the application approval)
        db.rollback()

        # Re-raise as HTTPException with detailed info
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "notification_creation_failed",
                "message": f"Application approved successfully, but notification creation failed: {str(e)}",
                "error_type": type(e).__name__,
                "application_id": application.id,
                "tournament_id": tournament_id,
                "instructor_id": instructor.id
            }
        )

    # ============================================================================
    # RETURN SUCCESS RESPONSE
    # ============================================================================
    return {
        "message": "Application approved successfully - Instructor automatically assigned",
        "application_id": application.id,
        "tournament_id": tournament.id,
        "tournament_name": tournament.name,
        "tournament_status": tournament.tournament_status,
        "instructor_id": instructor.id,
        "instructor_name": instructor.name,
        "instructor_email": instructor.email,
        "status": application.status.value,
        "approved_at": application.responded_at.isoformat(),
        "approved_by": current_user.id,
        "approved_by_name": current_user.name,
        "response_message": application.response_message,
        "assignment_type": "APPLICATION_BASED",
        "next_step": "Tournament is now ONGOING with instructor assigned"
    }


@router.get("/{tournament_id}/instructor-applications")
def get_instructor_applications(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get all instructor applications for a tournament.

    **Authorization:** ADMIN role only

    **Returns:**
    - List of all applications for the tournament with instructor details

    **Example Response:**
    ```json
    {
        "tournament_id": 123,
        "tournament_name": "Youth Football Tournament 2026",
        "applications": [
            {
                "id": 1,
                "instructor_id": 5,
                "instructor_name": "Coach Smith",
                "instructor_email": "coach.smith@example.com",
                "status": "PENDING",
                "created_at": "2026-01-04T10:30:00",
                "request_message": "I would love to lead this tournament",
                "responded_at": null,
                "response_message": null
            }
        ]
    }
    ```
    """
    # ============================================================================
    # VALIDATION: Admin auth + Tournament exists (using shared services)
    # ============================================================================
    require_admin(current_user)
    tournament = TournamentRepository(db).get_or_404(tournament_id)

    # ============================================================================
    # FETCH: All applications for this tournament
    # ============================================================================
    applications = db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.semester_id == tournament_id
    ).all()

    # Build response with instructor details
    applications_data = []
    for app in applications:
        instructor = db.query(User).filter(User.id == app.instructor_id).first()

        applications_data.append({
            "id": app.id,
            "instructor_id": app.instructor_id,
            "instructor_name": instructor.name if instructor else "Unknown",
            "instructor_email": instructor.email if instructor else "N/A",
            "status": app.status.value,
            "created_at": app.created_at.isoformat() if app.created_at else None,
            "request_message": app.request_message,
            "responded_at": app.responded_at.isoformat() if app.responded_at else None,
            "response_message": app.response_message,
            "requested_by": app.requested_by
        })

    # ============================================================================
    # RETURN SUCCESS RESPONSE
    # ============================================================================
    return {
        "tournament_id": tournament.id,
        "tournament_name": tournament.name,
        "tournament_status": tournament.tournament_status,
        "master_instructor_id": tournament.master_instructor_id,
        "applications": applications_data,
        "total_applications": len(applications_data)
    }


@router.get("/{tournament_id}/my-application")
def get_my_tournament_application(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get the current instructor's application for a specific tournament.

    **Authorization:** INSTRUCTOR role only

    **Returns:**
    - Application details if exists
    - 404 if no application exists for this tournament

    **Example Response:**
    ```json
    {
        "id": 1,
        "tournament_id": 123,
        "tournament_name": "Youth Football Tournament 2026",
        "status": "PENDING",
        "created_at": "2026-01-04T10:00:00",
        "application_message": "I am interested in leading this tournament",
        "responded_at": null,
        "response_message": null
    }
    ```
    """
    # ============================================================================
    # VALIDATION: Instructor auth + Tournament exists (using shared services)
    # ============================================================================
    require_instructor(current_user)
    tournament = TournamentRepository(db).get_or_404(tournament_id)

    # ============================================================================
    # FETCH: Application for this tournament by current instructor
    # ============================================================================
    application = db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.semester_id == tournament_id,
        InstructorAssignmentRequest.instructor_id == current_user.id
    ).first()

    # Return 404 if no application exists
    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "application_not_found",
                "message": f"No application found for tournament {tournament_id}",
                "tournament_id": tournament_id,
                "instructor_id": current_user.id
            }
        )

    # ============================================================================
    # RETURN SUCCESS RESPONSE
    # ============================================================================
    # For direct assignments (requested_by != None), return special status
    # to indicate instructor must still accept
    display_status = application.status.value
    if application.requested_by is not None and application.status.value == "ACCEPTED":
        # Admin directly assigned - instructor must accept
        display_status = "PENDING_ACCEPTANCE"

    return {
        "id": application.id,
        "tournament_id": tournament.id,
        "tournament_name": tournament.name,
        "status": display_status,
        "requested_by": application.requested_by,  # None = instructor applied, not None = admin assigned
        "created_at": application.created_at.isoformat() if application.created_at else None,
        "application_message": application.request_message,
        "responded_at": application.responded_at.isoformat() if application.responded_at else None,
        "response_message": application.response_message
    }


@router.get("/instructor/my-applications")
def get_my_instructor_applications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get all tournament applications submitted by the current instructor.

    **Authorization:** INSTRUCTOR role only

    **Returns:**
    - List of instructor's own applications with tournament details
    - Application status and messages
    - Tournament information

    **Example Response:**
    ```json
    {
        "applications": [
            {
                "id": 1,
                "tournament_id": 123,
                "tournament_name": "Youth Football Tournament 2026",
                "status": "PENDING",
                "created_at": "2026-01-04T10:00:00",
                "application_message": "I am interested in leading this tournament",
                "responded_at": null,
                "response_message": null
            }
        ],
        "total_applications": 1
    }
    ```
    """
    # ============================================================================
    # VALIDATION: Instructor auth (using shared services)
    # ============================================================================
    require_instructor(current_user, detail="Only instructors can view their own applications")

    # ============================================================================
    # FETCH: All applications by this instructor
    # ============================================================================
    applications = db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.instructor_id == current_user.id
    ).order_by(InstructorAssignmentRequest.created_at.desc()).all()

    # ============================================================================
    # BUILD RESPONSE: Include tournament details for each application
    # ============================================================================
    applications_data = []

    for app in applications:
        # Get tournament details
        tournament = db.query(Semester).filter(Semester.id == app.semester_id).first()

        applications_data.append({
            "id": app.id,
            "tournament_id": app.semester_id,
            "tournament_name": tournament.name if tournament else "Unknown Tournament",
            "tournament_start_date": tournament.start_date.isoformat() if tournament and tournament.start_date else None,
            "tournament_status": tournament.tournament_status if tournament else "UNKNOWN",
            "status": app.status.value,
            "created_at": app.created_at.isoformat() if app.created_at else None,
            "application_message": app.request_message,
            "responded_at": app.responded_at.isoformat() if app.responded_at else None,
            "response_message": app.response_message
        })

    # ============================================================================
    # RETURN SUCCESS RESPONSE
    # ============================================================================
    return {
        "applications": applications_data,
        "total_applications": len(applications_data),
        "instructor_id": current_user.id,
        "instructor_name": current_user.name
    }


@router.post("/{tournament_id}/direct-assign-instructor")
def direct_assign_instructor(
    tournament_id: int,
    request_data: DirectAssignmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    SCENARIO 1: Admin directly assigns instructor to tournament.

    This endpoint allows an admin to directly assign an instructor without the
    application workflow. Creates an ACCEPTED assignment request immediately.

    **Authorization:** ADMIN role only

    **Validations:**
    - Current user is ADMIN
    - Tournament exists
    - Tournament status is SEEKING_INSTRUCTOR
    - Instructor exists and has INSTRUCTOR role
    - Instructor has active LFA_COACH license

    **Actions Performed:**
    - Creates InstructorAssignmentRequest with status ACCEPTED
    - Records assignment_message from admin
    - Sets requested_by to admin's user ID

    **Returns:**
    - Assignment details
    - Instructor information
    - Next step for instructor acceptance

    **Raises:**
    - 403 FORBIDDEN: User is not an admin
    - 404 NOT FOUND: Tournament or instructor not found
    - 400 BAD REQUEST: Invalid status or license missing
    """
    # ============================================================================
    # VALIDATION: Admin auth + Tournament exists (using shared services)
    # ============================================================================
    require_admin(current_user, detail="Only admins can directly assign instructors")
    tournament = TournamentRepository(db).get_or_404(tournament_id)

    # ============================================================================
    # VALIDATION 3: Tournament status is SEEKING_INSTRUCTOR
    # ============================================================================
    if tournament.tournament_status != "SEEKING_INSTRUCTOR":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tournament_status",
                "message": f"Tournament is not seeking instructor",
                "current_status": tournament.tournament_status,
                "required_status": "SEEKING_INSTRUCTOR",
                "tournament_id": tournament_id
            }
        )

    # ============================================================================
    # VALIDATION 4: Instructor exists and has INSTRUCTOR role
    # ============================================================================
    instructor = db.query(User).filter(User.id == request_data.instructor_id).first()

    if not instructor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "instructor_not_found",
                "message": f"Instructor {request_data.instructor_id} not found",
                "instructor_id": request_data.instructor_id
            }
        )

    if instructor.role != UserRole.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_instructor_role",
                "message": "Selected user is not an instructor",
                "user_id": instructor.id,
                "user_role": instructor.role.value,
                "required_role": "INSTRUCTOR"
            }
        )

    # ============================================================================
    # VALIDATION 5: Instructor has LFA_COACH license + sufficient level (using shared services)
    # ============================================================================
    coach_license = LicenseValidator.validate_coach_license(
        db=db,
        user_id=instructor.id,
        age_group=tournament.age_group,
        user_email=instructor.email,
        tournament_id=tournament_id,
        tournament_name=tournament.name
    )


    # ============================================================================
    # VALIDATION 6: Check for existing ACCEPTED assignment
    # ============================================================================
    existing_assignment = db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.semester_id == tournament_id,
        InstructorAssignmentRequest.instructor_id == instructor.id,
        InstructorAssignmentRequest.status == AssignmentRequestStatus.ACCEPTED
    ).first()

    if existing_assignment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "duplicate_assignment",
                "message": f"Instructor already has an ACCEPTED assignment for this tournament",
                "assignment_id": existing_assignment.id
            }
        )

    # ============================================================================
    # ACTION: Create direct assignment with ACCEPTED status
    # ============================================================================
    assignment = InstructorAssignmentRequest(
        semester_id=tournament_id,
        instructor_id=instructor.id,
        requested_by=current_user.id,  # Admin who made the direct assignment
        status=AssignmentRequestStatus.ACCEPTED,  # Directly accepted
        request_message=request_data.assignment_message,
        responded_at=datetime.utcnow(),  # Immediately responded
        priority=10  # High priority for direct assignments
    )

    db.add(assignment)

    # ============================================================================
    # ACTION: Update tournament with instructor and status
    # ============================================================================
    # Admin has assigned instructor, but instructor must still accept
    old_status = tournament.tournament_status
    tournament.master_instructor_id = instructor.id
    tournament.tournament_status = "PENDING_INSTRUCTOR_ACCEPTANCE"

    # Record status change in history (using shared service)
    recorder = StatusHistoryRecorder(db)
    recorder.record_status_change(
        tournament_id=tournament.id,
        old_status=old_status,
        new_status="PENDING_INSTRUCTOR_ACCEPTANCE",
        changed_by=current_user.id,
        reason=f"Admin directly assigned instructor {instructor.name}",
        metadata={
            "assignment_id": assignment.id,
            "instructor_id": instructor.id,
            "instructor_name": instructor.name,
            "assignment_type": "DIRECT_ASSIGNMENT"
        }
    )

    db.commit()
    db.refresh(assignment)
    db.refresh(tournament)

    # ============================================================================
    # CREATE NOTIFICATION for instructor
    # ============================================================================
    from app.services.notification_service import create_tournament_direct_invitation_notification

    create_tournament_direct_invitation_notification(
        db=db,
        instructor_id=instructor.id,
        tournament=tournament,
        invitation_message=request_data.assignment_message or "You have been selected to lead this tournament!",
        request_id=assignment.id
    )

    # Commit the notification
    db.commit()

    # ============================================================================
    # RETURN SUCCESS RESPONSE
    # ============================================================================
    return {
        "message": "Instructor directly assigned successfully",
        "assignment_id": assignment.id,
        "tournament_id": tournament.id,
        "tournament_name": tournament.name,
        "instructor_id": instructor.id,
        "instructor_name": instructor.name,
        "instructor_email": instructor.email,
        "status": assignment.status.value,
        "assigned_by": current_user.id,
        "assigned_by_name": current_user.name,
        "assigned_at": assignment.responded_at.isoformat(),
        "assignment_message": assignment.request_message,
        "next_step": f"Instructor must accept assignment via POST /tournaments/{tournament_id}/instructor-assignment/accept"
    }


@router.post("/{tournament_id}/instructor-applications/{application_id}/decline")
def decline_instructor_application(
    tournament_id: int,
    application_id: int,
    decline_data: DeclineApplicationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    SCENARIO 2: Admin declines instructor application.

    This endpoint allows an admin to decline an instructor's application to lead
    a tournament.

    **Authorization:** ADMIN role only

    **Validations:**
    - Current user is ADMIN
    - Tournament exists
    - Application exists and belongs to the tournament
    - Application status is PENDING

    **Actions Performed:**
    - Updates application status to DECLINED
    - Records responded_at timestamp
    - Records optional decline_message from admin

    **Returns:**
    - Application details
    - Decline confirmation

    **Raises:**
    - 403 FORBIDDEN: User is not an admin
    - 404 NOT FOUND: Tournament or application not found
    - 400 BAD REQUEST: Invalid status or already processed
    """
    # ============================================================================
    # VALIDATION: Admin auth + Tournament exists (using shared services)
    # ============================================================================
    require_admin(current_user, detail="Only admins can decline instructor applications")
    tournament = TournamentRepository(db).get_or_404(tournament_id)

    # ============================================================================
    # VALIDATION 3: Application exists and belongs to tournament
    # ============================================================================
    application = db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.id == application_id,
        InstructorAssignmentRequest.semester_id == tournament_id
    ).first()

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "application_not_found",
                "message": f"Application {application_id} not found for tournament {tournament_id}",
                "application_id": application_id,
                "tournament_id": tournament_id
            }
        )

    # ============================================================================
    # VALIDATION 4: Application status is PENDING
    # ============================================================================
    if application.status != AssignmentRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_application_status",
                "message": f"Application cannot be declined in current status",
                "current_status": application.status.value,
                "required_status": "PENDING",
                "application_id": application_id
            }
        )

    # ============================================================================
    # ACTION: Decline application
    # ============================================================================
    application.status = AssignmentRequestStatus.DECLINED
    application.responded_at = datetime.utcnow()
    application.response_message = decline_data.decline_message

    db.commit()
    db.refresh(application)

    # Get instructor details
    instructor = db.query(User).filter(User.id == application.instructor_id).first()

    # ============================================================================
    # CREATE NOTIFICATION for instructor
    # ============================================================================
    from app.services.notification_service import create_tournament_application_rejected_notification

    create_tournament_application_rejected_notification(
        db=db,
        instructor_id=instructor.id,
        tournament=tournament,
        response_message=decline_data.decline_message or "Thank you for your interest.",
        request_id=application.id
    )

    # Commit the notification
    db.commit()

    # ============================================================================
    # RETURN SUCCESS RESPONSE
    # ============================================================================
    return {
        "message": "Application declined successfully",
        "application_id": application.id,
        "tournament_id": tournament.id,
        "tournament_name": tournament.name,
        "instructor_id": instructor.id if instructor else None,
        "instructor_name": instructor.name if instructor else "Unknown",
        "instructor_email": instructor.email if instructor else "N/A",
        "status": application.status.value,
        "declined_at": application.responded_at.isoformat(),
        "declined_by": current_user.id,
        "declined_by_name": current_user.name,
        "decline_message": application.response_message
    }
