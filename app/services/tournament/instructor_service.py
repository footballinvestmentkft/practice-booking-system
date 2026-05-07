"""
Tournament Instructor Service - Instructor assignment logic

This module handles the instructor assignment workflow for tournaments:
- Send assignment request to instructor (grandmaster)
- Accept assignment request (activates tournament)
- Decline assignment request (keeps tournament seeking)

Functions:
    - send_instructor_request: Send assignment request to instructor
    - accept_instructor_request: Accept request and activate tournament
    - decline_instructor_request: Decline request (tournament stays seeking)
"""

from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.semester import Semester, SemesterStatus
from app.models.session import Session as SessionModel
from app.models.user import User, UserRole
from app.models.instructor_assignment import InstructorAssignmentRequest, AssignmentRequestStatus


def send_instructor_request(
    db: Session,
    semester_id: int,
    instructor_id: int,
    requested_by_admin_id: int,
    message: Optional[str] = None
) -> InstructorAssignmentRequest:
    """
    Send assignment request to instructor (grandmaster) for tournament

    IMPORTANT: This creates a PENDING request. Tournament activates only when
    instructor ACCEPTS the request.

    Workflow:
    1. Admin sends request → Status: PENDING
    2. Instructor accepts → Tournament status: READY_FOR_ENROLLMENT
    3. Instructor declines → Tournament status: SEEKING_INSTRUCTOR (stays)

    Args:
        db: Database session
        semester_id: Tournament semester ID
        instructor_id: Grandmaster instructor ID to invite
        requested_by_admin_id: Admin user ID sending the request
        message: Optional message to instructor

    Returns:
        Created InstructorAssignmentRequest object

    Raises:
        ValueError: If semester not found, instructor invalid, or duplicate request
    """
    # Get semester
    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester:
        raise ValueError(f"Tournament semester {semester_id} not found")

    # Verify current status
    if semester.status != SemesterStatus.SEEKING_INSTRUCTOR:
        raise ValueError(
            f"Tournament status must be SEEKING_INSTRUCTOR, currently: {semester.status}"
        )

    # Verify instructor exists and is instructor role
    instructor = db.query(User).filter(User.id == instructor_id).first()
    if not instructor:
        raise ValueError(f"Instructor {instructor_id} not found")

    if instructor.role != UserRole.INSTRUCTOR:
        raise ValueError(f"User {instructor_id} is not an instructor")

    # Check for existing pending request
    existing_request = db.query(InstructorAssignmentRequest).filter(
        and_(
            InstructorAssignmentRequest.semester_id == semester_id,
            InstructorAssignmentRequest.status == AssignmentRequestStatus.PENDING
        )
    ).first()

    if existing_request:
        raise ValueError(
            f"Pending request already exists for this tournament (Request ID: {existing_request.id})"
        )

    # Create assignment request
    assignment_request = InstructorAssignmentRequest(
        semester_id=semester_id,
        instructor_id=instructor_id,
        requested_by=requested_by_admin_id,
        status=AssignmentRequestStatus.PENDING,
        request_message=message or f"Please lead the '{semester.name}' tournament on {semester.start_date}"
    )

    db.add(assignment_request)
    db.commit()
    db.refresh(assignment_request)

    return assignment_request


def accept_instructor_request(
    db: Session,
    request_id: int,
    instructor_id: int
) -> Semester:
    """
    Instructor accepts tournament assignment request

    IMPORTANT: This activates the tournament by changing status to READY_FOR_ENROLLMENT.

    Args:
        db: Database session
        request_id: Assignment request ID
        instructor_id: Instructor ID (must match request)

    Returns:
        Updated semester object

    Raises:
        ValueError: If request not found, unauthorized, or invalid status
    """
    # Get request
    assignment_request = db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.id == request_id
    ).first()

    if not assignment_request:
        raise ValueError(f"Assignment request {request_id} not found")

    # Verify instructor matches
    if assignment_request.instructor_id != instructor_id:
        raise ValueError("You are not authorized to accept this request")

    # Verify request status
    if assignment_request.status != AssignmentRequestStatus.PENDING:
        raise ValueError(
            f"Request status must be PENDING, currently: {assignment_request.status}"
        )

    # Get semester
    semester = db.query(Semester).filter(
        Semester.id == assignment_request.semester_id
    ).first()

    if not semester:
        raise ValueError(f"Tournament semester not found")

    # Accept request
    assignment_request.status = AssignmentRequestStatus.ACCEPTED
    assignment_request.responded_at = datetime.now()

    # Assign instructor to semester and activate
    semester.master_instructor_id = instructor_id
    semester.status = SemesterStatus.READY_FOR_ENROLLMENT

    # Assign to all sessions
    sessions = db.query(SessionModel).filter(
        SessionModel.semester_id == semester.id
    ).all()
    for session in sessions:
        session.instructor_id = instructor_id

    db.commit()
    db.refresh(semester)

    return semester


def decline_instructor_request(
    db: Session,
    request_id: int,
    instructor_id: int,
    reason: Optional[str] = None
) -> InstructorAssignmentRequest:
    """
    Instructor declines tournament assignment request

    Tournament remains in SEEKING_INSTRUCTOR status. Admin can send new request.

    Args:
        db: Database session
        request_id: Assignment request ID
        instructor_id: Instructor ID (must match request)
        reason: Optional reason for declining

    Returns:
        Updated InstructorAssignmentRequest object

    Raises:
        ValueError: If request not found, unauthorized, or invalid status
    """
    # Get request
    assignment_request = db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.id == request_id
    ).first()

    if not assignment_request:
        raise ValueError(f"Assignment request {request_id} not found")

    # Verify instructor matches
    if assignment_request.instructor_id != instructor_id:
        raise ValueError("You are not authorized to decline this request")

    # Verify request status
    if assignment_request.status != AssignmentRequestStatus.PENDING:
        raise ValueError(
            f"Request status must be PENDING, currently: {assignment_request.status}"
        )

    # Decline request
    assignment_request.status = AssignmentRequestStatus.DECLINED
    assignment_request.responded_at = datetime.now()
    if reason:
        assignment_request.response_message = reason

    db.commit()
    db.refresh(assignment_request)

    return assignment_request


# ── Instructor prerequisite guard ─────────────────────────────────────────────

def has_master_instructor_assignment(db: Session, tournament_id: int) -> bool:
    """Return True if the tournament has a usable master instructor assignment.

    Accepted sources (checked in order):
    1. Semester.master_instructor_id — legacy field, non-NULL
    2. TournamentInstructorSlot with role=MASTER and status != ABSENT

    Args:
        db: explicit SQLAlchemy session — no internal ORM state extraction.
        tournament_id: Semester.id of the tournament.
    """
    from app.models.tournament_instructor_slot import (
        TournamentInstructorSlot,
        SlotRole,
        SlotStatus,
    )

    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        return False

    if tournament.master_instructor_id:
        return True

    # SlotRole and SlotStatus are str enums — .value yields the stored string.
    master_slot = db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.semester_id == tournament_id,
        TournamentInstructorSlot.role == SlotRole.MASTER.value,
        TournamentInstructorSlot.status != SlotStatus.ABSENT.value,
    ).first()
    return master_slot is not None
