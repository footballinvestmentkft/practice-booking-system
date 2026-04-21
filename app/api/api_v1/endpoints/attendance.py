from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import datetime, timezone, timedelta

from ....core import time_provider
from ....database import get_db
from ....dependencies import get_current_user, get_current_admin_or_instructor_user
from ....models.user import User
from ....models.session import Session as SessionTypel, EventCategory
from ....models.booking import Booking, BookingStatus
from ....models.attendance import Attendance, AttendanceStatus
from ....models.project import ProjectEnrollment, ProjectMilestone, ProjectMilestoneProgress, MilestoneStatus
from ....schemas.attendance import (
    Attendance as AttendanceSchema, AttendanceCreate, AttendanceUpdate,
    AttendanceWithRelations, AttendanceList, AttendanceCheckIn
)
from sqlalchemy.orm import joinedload
from app.services import segment_reward_service

router = APIRouter()


@router.post("/", response_model=AttendanceSchema)
def create_attendance(
    attendance_data: AttendanceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user)
) -> Any:
    """
    Create attendance record (Admin/Instructor only)

    For regular sessions: booking_id is required
    For tournament sessions: booking_id is optional (uses user_id + session_id)
    """
    # Get session first to determine if it's a tournament
    session = db.query(SessionTypel).filter(SessionTypel.id == attendance_data.session_id).first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )

    # 🏆 TOURNAMENT SESSION: No booking required
    if session.event_category == EventCategory.MATCH:
        # Tournament sessions ONLY support present/absent (NO late/excused)
        if attendance_data.status not in [AttendanceStatus.present, AttendanceStatus.absent]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Tournaments only support 'present' or 'absent' attendance. Received: '{attendance_data.status.value}'"
            )

        # Check if attendance already exists (by user_id + session_id)
        existing_attendance = db.query(Attendance).filter(
            Attendance.user_id == attendance_data.user_id,
            Attendance.session_id == attendance_data.session_id
        ).first()
    else:
        # 📅 REGULAR SESSION: Booking required
        if not attendance_data.booking_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="booking_id is required for regular (non-tournament) sessions"
            )

        # Check if booking exists and is confirmed
        booking = db.query(Booking).filter(Booking.id == attendance_data.booking_id).first()
        if not booking:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )

        if booking.status != BookingStatus.CONFIRMED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only create attendance for confirmed bookings"
            )

        # Check if attendance already exists (by booking_id)
        existing_attendance = db.query(Attendance).filter(Attendance.booking_id == attendance_data.booking_id).first()

    if existing_attendance:
        # UPDATE existing attendance
        existing_attendance.status = attendance_data.status
        if attendance_data.notes:
            existing_attendance.notes = attendance_data.notes
        existing_attendance.marked_by = current_user.id
        existing_attendance.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(existing_attendance)

        # Update milestone progress if attendance is marked as present
        if existing_attendance.status == AttendanceStatus.present:
            _update_milestone_sessions_on_attendance(db, existing_attendance.user_id, existing_attendance.session_id)
            # Award training segment results (no-op if session has no segments)
            segment_reward_service.award_session_segments(
                db, existing_attendance.session_id, existing_attendance.id
            )
            db.commit()

        return existing_attendance
    else:
        # CREATE new attendance
        attendance = Attendance(
            **attendance_data.model_dump(),
            marked_by=current_user.id
        )

        db.add(attendance)
        db.commit()
        db.refresh(attendance)

        # Update milestone progress if attendance is marked as present
        if attendance.status == AttendanceStatus.present:
            _update_milestone_sessions_on_attendance(db, attendance.user_id, attendance.session_id)
            # Award training segment results (no-op if session has no segments)
            segment_reward_service.award_session_segments(
                db, attendance.session_id, attendance.id
            )
            db.commit()

        return attendance


@router.get("/", response_model=AttendanceList)
def list_attendance(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user),
    session_id: int = Query(None, description="Filter by session ID (optional)")
) -> Any:
    """
    List attendance records (Admin/Instructor only)

    - If session_id provided: Get attendance for that session
    - If no session_id: Get all attendance records (paginated)
    """
    query = db.query(Attendance)

    # Filter by session if provided
    if session_id is not None:
        # Check if session exists
        session = db.query(SessionTypel).filter(SessionTypel.id == session_id).first()
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found"
            )
        query = query.filter(Attendance.session_id == session_id)

    # OPTIMIZED: Eager load relationships to avoid N+1 query pattern
    query = query.options(
        joinedload(Attendance.user),
        joinedload(Attendance.session),
        joinedload(Attendance.booking),
        joinedload(Attendance.marker)
    )

    # Get all matching attendance records
    attendances = query.all()

    # Convert to response schema
    attendance_responses = []
    for attendance in attendances:
        # Filter out SQLAlchemy internal attributes and construct proper dict
        attendance_data = {
            **{k: v for k, v in attendance.__dict__.items() if not k.startswith('_')},
            'user': attendance.user,
            'session': attendance.session,
            'booking': attendance.booking,
            'marker': attendance.marker
        }
        attendance_responses.append(AttendanceWithRelations(**attendance_data))

    return AttendanceList(
        attendances=attendance_responses,
        total=len(attendance_responses)
    )


@router.post("/{booking_id}/checkin", response_model=AttendanceSchema)
def checkin(
    booking_id: int,
    checkin_data: AttendanceCheckIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Check in to a session
    """
    # Check if booking exists and belongs to current user
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found"
        )
    
    if booking.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only check in to your own bookings"
        )
    
    if booking.status != BookingStatus.CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only check in to confirmed bookings"
        )
    
    # ✅ VALIDATE CHECK-IN WINDOW: Opens 15 minutes before session start
    session = booking.session
    current_time = time_provider.now().replace(tzinfo=None)
    session_start = session.date_start.replace(tzinfo=None) if session.date_start.tzinfo else session.date_start
    session_end = session.date_end.replace(tzinfo=None) if session.date_end.tzinfo else session.date_end

    # 🔒 RULE #3: Check-in opens 15 minutes before session start
    checkin_window_start = session_start - timedelta(minutes=15)

    if current_time < checkin_window_start:
        minutes_until_checkin = (checkin_window_start - current_time).total_seconds() / 60
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Check-in opens 15 minutes before the session starts. "
                   f"Please wait {int(minutes_until_checkin)} more minutes."
        )

    if current_time > session_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session has ended. Check-in closed."
        )
    
    # Check if attendance record exists
    attendance = db.query(Attendance).filter(Attendance.booking_id == booking_id).first()
    if not attendance:
        # Create new attendance record
        attendance = Attendance(
            user_id=current_user.id,
            session_id=session.id,
            booking_id=booking_id,
            status=AttendanceStatus.present,
            check_in_time=current_time,
            notes=checkin_data.notes
        )
        db.add(attendance)
    else:
        # Update existing record
        attendance.check_in_time = current_time
        attendance.status = AttendanceStatus.present
        if checkin_data.notes:
            attendance.notes = checkin_data.notes
    
    db.commit()
    db.refresh(attendance)
    
    return attendance


@router.patch("/{attendance_id}", response_model=AttendanceSchema)
def update_attendance(
    attendance_id: int,
    attendance_update: AttendanceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user)
) -> Any:
    """
    Update attendance record (Admin/Instructor only)
    """
    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not attendance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attendance record not found"
        )

    # 🏆 TOURNAMENT VALIDATION: Check if session is a tournament game
    session = db.query(SessionTypel).filter(SessionTypel.id == attendance.session_id).first()
    if session and session.event_category == EventCategory.MATCH:
        # Tournament sessions ONLY support present/absent (NO late/excused)
        if hasattr(attendance_update, 'status') and attendance_update.status:
            if attendance_update.status not in [AttendanceStatus.present, AttendanceStatus.absent]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Tournaments only support 'present' or 'absent' attendance. Received: '{attendance_update.status.value}'"
                )

    # Update fields
    update_data = attendance_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(attendance, field, value)

    attendance.marked_by = current_user.id

    db.commit()
    db.refresh(attendance)

    return attendance


@router.get("/instructor/overview")
def get_instructor_attendance_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100)
) -> Any:
    """
    Get attendance overview for current instructor's sessions
    """
    # Verify user is instructor
    if current_user.role.value != 'instructor':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Instructor role required."
        )
    
    # Import here to avoid circular imports
    query = db.query(SessionTypel).filter(
        SessionTypel.instructor_id == current_user.id
    ).order_by(SessionTypel.date_start.desc())
    
    # Get total count
    total = query.count()
    
    # Apply pagination
    offset = (page - 1) * size
    sessions = query.offset(offset).limit(size).all()

    # OPTIMIZED: Batch fetch stats using GROUP BY (reduces 2N+1 queries to 2 queries)
    session_ids = [s.id for s in sessions]

    # Query 1: Attendance counts by session
    attendance_counts = db.query(
        Attendance.session_id,
        func.count(Attendance.id).label('count')
    ).filter(Attendance.session_id.in_(session_ids)).group_by(Attendance.session_id).all()

    attendance_map = {row.session_id: row.count for row in attendance_counts}

    # Query 2: Booking counts by session
    booking_counts = db.query(
        Booking.session_id,
        func.count(Booking.id).label('count')
    ).filter(Booking.session_id.in_(session_ids)).group_by(Booking.session_id).all()

    booking_map = {row.session_id: row.count for row in booking_counts}

    # Build response with attendance stats (no queries in loop)
    session_list = []
    for session in sessions:
        session_dict = {
            'id': session.id,
            'title': session.title,
            'description': session.description,
            'date_start': session.date_start.isoformat(),
            'date_end': session.date_end.isoformat(),
            'location': session.location,
            'capacity': session.capacity,
            'level': session.level,
            'sport_type': session.sport_type,
            'current_bookings': booking_map.get(session.id, 0),
            'attendance_count': attendance_map.get(session.id, 0),
            'created_at': session.created_at.isoformat(),
        }
        session_list.append(session_dict)
    
    return {
        'sessions': session_list,
        'total': total,
        'page': page,
        'size': size
    }


def _update_milestone_sessions_on_attendance(db: Session, user_id: int, session_id: int):
    """
    Update milestone progress when a user attends a session
    """
    active_enrollments = db.query(ProjectEnrollment).filter(
        and_(
            ProjectEnrollment.user_id == user_id,
            ProjectEnrollment.status == "active"
        )
    ).all()
    
    if not active_enrollments:
        return
    
    # For each active enrollment, update milestone progress
    for enrollment in active_enrollments:
        # Get current IN_PROGRESS milestone
        current_milestone = db.query(ProjectMilestoneProgress).filter(
            and_(
                ProjectMilestoneProgress.enrollment_id == enrollment.id,
                ProjectMilestoneProgress.status == MilestoneStatus.IN_PROGRESS.value
            )
        ).first()
        
        if current_milestone:
            # Increment sessions completed
            current_milestone.sessions_completed += 1
            current_milestone.updated_at = datetime.now(timezone.utc)
            
            # Check if milestone requirements are met for auto-submission
            milestone = db.query(ProjectMilestone).filter(
                ProjectMilestone.id == current_milestone.milestone_id
            ).first()
            
            if milestone and current_milestone.sessions_completed >= milestone.required_sessions:
                # Optional: Auto-submit milestone when requirements are met
                # current_milestone.status = MilestoneStatus.SUBMITTED.value
                # current_milestone.submitted_at = datetime.now(timezone.utc)
                pass
    
    db.commit()