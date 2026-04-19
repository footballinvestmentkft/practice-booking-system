"""
Session CRUD operations
Create, read, update, delete sessions with authorization
"""
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from .....database import get_db
from .....dependencies import get_current_user, get_current_admin_or_instructor_user
from .....models.user import User, UserRole
from .....models.semester import Semester
from .....models.session import Session as SessionTypel, EventCategory
from .....models.booking import Booking, BookingStatus
from .....models.attendance import Attendance
from .....models.feedback import Feedback
from .....models.project import ProjectSession
from .....schemas.session import (
    Session as SessionSchema, SessionCreate, SessionUpdate,
    SessionWithStats
)

router = APIRouter()


@router.post("/", response_model=SessionSchema)
def create_session(
    session_data: SessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user)
) -> Any:
    """
    Create new session (Admin/Instructor only)

    IMPORTANT: Instructors can only create sessions for semesters where they are
    the assigned master instructor.
    """
    # 🚀 PERFORMANCE: Fetch semester once (used for both authorization and date validation)
    semester = db.query(Semester).filter(
        Semester.id == session_data.semester_id
    ).first()

    if not semester:
        raise HTTPException(
            status_code=404,
            detail=f"Semester {session_data.semester_id} not found"
        )

    # If instructor, validate master instructor authorization
    if current_user.role == UserRole.INSTRUCTOR:
        # Check if current user is the master instructor for this semester
        if semester.master_instructor_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail=f"Only the master instructor (ID: {semester.master_instructor_id}) "
                       f"can create sessions for this semester. "
                       f"You must first accept the assignment request for this semester."
            )

        # Check if session has target_specialization (additional validation)
        if hasattr(session_data, 'target_specialization') and session_data.target_specialization:
            # Check if instructor has ACTIVE qualification for this specialization
            if not current_user.can_teach_specialization(session_data.target_specialization):
                raise HTTPException(
                    status_code=403,
                    detail=f"You do not have active teaching qualification for {session_data.target_specialization}. "
                           f"Please activate this specialization in your dashboard before creating sessions."
                )

    # Validate session dates are within semester boundaries (reuse fetched semester)
    if semester:
        session_start_date = session_data.date_start.date()
        session_end_date = session_data.date_end.date()

        if session_start_date < semester.start_date:
            raise HTTPException(
                status_code=400,
                detail=f"Session start date ({session_start_date}) cannot be before semester start date ({semester.start_date})"
            )

        if session_end_date > semester.end_date:
            raise HTTPException(
                status_code=400,
                detail=f"Session end date ({session_end_date}) cannot be after semester end date ({semester.end_date})"
            )

    # Translate is_tournament_game → event_category at the API boundary.
    # The Pydantic field is kept for external callers; the DB column was dropped in M-10.
    session_dict = session_data.model_dump()
    itg = session_dict.pop("is_tournament_game", False)
    session_dict["event_category"] = EventCategory.MATCH if itg else EventCategory.TRAINING
    session = SessionTypel(**session_dict)
    db.add(session)
    db.commit()
    db.refresh(session)

    return session


@router.get("/{session_id}", response_model=SessionWithStats)
def get_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get session by ID with statistics
    """
    session = db.query(SessionTypel).filter(SessionTypel.id == session_id).first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )

    # Calculate statistics
    booking_count = db.query(func.count(Booking.id)).filter(Booking.session_id == session.id).scalar() or 0
    confirmed_bookings = db.query(func.count(Booking.id)).filter(
        and_(Booking.session_id == session.id, Booking.status == BookingStatus.CONFIRMED)
    ).scalar() or 0
    waitlist_count = db.query(func.count(Booking.id)).filter(
        and_(Booking.session_id == session.id, Booking.status == BookingStatus.WAITLISTED)
    ).scalar() or 0
    attendance_count = db.query(func.count(Attendance.id)).filter(Attendance.session_id == session.id).scalar() or 0
    avg_rating = db.query(func.avg(Feedback.rating)).filter(Feedback.session_id == session.id).scalar()

    return SessionWithStats(
        **session.__dict__,
        semester=session.semester,
        group=session.group,
        instructor=session.instructor,
        booking_count=booking_count,
        confirmed_bookings=confirmed_bookings,
        current_bookings=confirmed_bookings,  # FIXED: Map confirmed_bookings to current_bookings for frontend
        waitlist_count=waitlist_count,
        attendance_count=attendance_count,
        average_rating=float(avg_rating) if avg_rating else None
    )


@router.patch("/{session_id}", response_model=SessionSchema)
def update_session(
    session_id: int,
    session_update: SessionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user)
) -> Any:
    """
    Update session (Admin/Instructor only)

    IMPORTANT: Instructors can only update sessions for semesters where they are
    the assigned master instructor.
    """
    session = db.query(SessionTypel).filter(SessionTypel.id == session_id).first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )

    # If instructor, validate master instructor authorization
    if current_user.role == UserRole.INSTRUCTOR:
        # Get semester to check master_instructor_id
        semester = db.query(Semester).filter(
            Semester.id == session.semester_id
        ).first()

        if semester and semester.master_instructor_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail=f"Only the master instructor (ID: {semester.master_instructor_id}) "
                       f"can update sessions for this semester."
            )

    # Update fields
    update_data = session_update.model_dump(exclude_unset=True)

    # 🔍 DEBUG: Log what we received
    print(f"🔍 BACKEND DEBUG - Session {session_id} PATCH received:")
    print(f"   credit_cost in update_data: {update_data.get('credit_cost', 'NOT_IN_PAYLOAD')}")
    print(f"   capacity in update_data: {update_data.get('capacity', 'NOT_IN_PAYLOAD')}")
    print(f"   Full update_data: {update_data}")

    # Validate updated dates are within semester boundaries (if dates are being updated)
    if 'date_start' in update_data or 'date_end' in update_data:
        semester = db.query(Semester).filter(Semester.id == session.semester_id).first()
        if semester:
            new_start_date = (update_data.get('date_start') or session.date_start).date()
            new_end_date = (update_data.get('date_end') or session.date_end).date()

            if new_start_date < semester.start_date:
                raise HTTPException(
                    status_code=400,
                    detail=f"Session start date ({new_start_date}) cannot be before semester start date ({semester.start_date})"
                )

            if new_end_date > semester.end_date:
                raise HTTPException(
                    status_code=400,
                    detail=f"Session end date ({new_end_date}) cannot be after semester end date ({semester.end_date})"
                )

    # Translate is_tournament_game → event_category at the API boundary (same as POST).
    if "is_tournament_game" in update_data:
        itg = update_data.pop("is_tournament_game")
        update_data["event_category"] = EventCategory.MATCH if itg else EventCategory.TRAINING

    for field, value in update_data.items():
        setattr(session, field, value)

    # 🔍 DEBUG: Log what was actually set on the model
    print(f"🔍 BACKEND DEBUG - After setattr loop:")
    print(f"   session.credit_cost = {session.credit_cost}")
    print(f"   session.capacity = {session.capacity}")

    db.commit()
    db.refresh(session)

    # 🔍 DEBUG: Log after DB commit
    print(f"🔍 BACKEND DEBUG - After commit + refresh:")
    print(f"   session.credit_cost = {session.credit_cost}")
    print(f"   session.capacity = {session.capacity}")

    return session


@router.delete("/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user)
) -> Any:
    """
    Delete session (Admin/Instructor only)
    IMPORTANT: Instructors can only delete sessions for semesters where they are
    the assigned master instructor.
    """
    session = db.query(SessionTypel).filter(SessionTypel.id == session_id).first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )

    # If instructor, validate master instructor authorization
    if current_user.role == UserRole.INSTRUCTOR:
        semester = db.query(Semester).filter(Semester.id == session.semester_id).first()

        if semester and semester.master_instructor_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail=f"Only the master instructor (ID: {semester.master_instructor_id}) "
                       f"can delete sessions for this semester."
            )

    # 🔒 COMPREHENSIVE RELATIONSHIP CHECK: Prevent orphaned data
    # Check all relationships before allowing deletion
    relationship_checks = []

    # Check bookings
    booking_count = db.query(func.count(Booking.id)).filter(Booking.session_id == session_id).scalar()
    if booking_count > 0:
        relationship_checks.append(("bookings", booking_count))

    # Check attendances
    attendance_count = db.query(func.count(Attendance.id)).filter(Attendance.session_id == session_id).scalar()
    if attendance_count > 0:
        relationship_checks.append(("attendance records", attendance_count))

    # Check feedbacks
    feedback_count = db.query(func.count(Feedback.id)).filter(Feedback.session_id == session_id).scalar()
    if feedback_count > 0:
        relationship_checks.append(("feedback submissions", feedback_count))

    # Check project associations
    project_session_count = db.query(func.count(ProjectSession.id)).filter(ProjectSession.session_id == session_id).scalar()
    if project_session_count > 0:
        relationship_checks.append(("project associations", project_session_count))

    # If any relationships exist, block deletion
    if relationship_checks:
        relationship_details = ", ".join([f"{count} {name}" for name, count in relationship_checks])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete session with existing related data: {relationship_details}. "
                   f"Please remove all related records before deleting the session."
        )

    db.delete(session)
    db.commit()

    return {"message": "Session deleted successfully"}
