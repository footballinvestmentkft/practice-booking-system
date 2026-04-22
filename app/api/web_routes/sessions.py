"""
Session and calendar routes
"""
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo
from sqlalchemy.orm import joinedload

import logging

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User, UserRole
from ...models.session import Session as SessionModel, SessionType
from ...models.booking import Booking, BookingStatus
from ...models.attendance import Attendance, AttendanceHistory
from ...models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ...models.quiz import Quiz, QuizQuestion, QuizAttempt, SessionQuiz
from ...models.performance_review import InstructorSessionReview, StudentPerformanceReview
from ...models.session_segment import SessionSegment
from ...services.skill_progression._config import get_all_skill_keys
from .student_features import _spec_ctx

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Display calendar page with all sessions"""
    if user.role == UserRole.STUDENT and not user.onboarding_completed:
        return RedirectResponse(url="/dashboard", status_code=303)

    return templates.TemplateResponse(
        "calendar.html",
        {
            "request": request,
            "user": user,
            "spec_header_class": "hdr-hub",
            **_spec_ctx(user, db),
        }
    )


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Display sessions page - role-based UI for Instructor vs Student"""
    if user.role == UserRole.STUDENT and not user.onboarding_completed:
        return RedirectResponse(url="/dashboard", status_code=303)

    is_instructor = user.role == UserRole.INSTRUCTOR

    if is_instructor:
        # INSTRUCTOR VIEW: Show ALL sessions they are teaching (past, ongoing, and upcoming)
        my_sessions = db.query(SessionModel).filter(
            SessionModel.instructor_id == user.id
        ).order_by(SessionModel.date_start.asc()).all()  # Chronological order (earliest first)

        # Add enrolled count and student reviews for each session
        for session in my_sessions:
            enrolled_count = db.query(Booking).filter(
                Booking.session_id == session.id
            ).count()
            session.enrolled_count = enrolled_count
            session.instructor_name = user.name

            # Get all instructor reviews from students for this session
            student_reviews = db.query(InstructorSessionReview).filter(
                InstructorSessionReview.session_id == session.id
            ).all()
            session.student_reviews = student_reviews

        return templates.TemplateResponse(
            "sessions.html",
            {
                "request": request,
                "user": user,
                "is_instructor": True,
                "my_teaching_sessions": my_sessions,
                "upcoming_sessions": [],
                "spec_header_class": "hdr-hub",
                **_spec_ctx(user, db),
            }
        )
    else:
        # STUDENT VIEW: Show ONLY sessions from APPROVED semesters
        approved_enrollments = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            SemesterEnrollment.is_active == True
        ).all()

        approved_semester_ids = [e.semester_id for e in approved_enrollments]

        # Only show sessions from APPROVED semesters
        if approved_semester_ids:
            upcoming_sessions = db.query(SessionModel).filter(
                SessionModel.semester_id.in_(approved_semester_ids)
            ).order_by(
                SessionModel.date_start.asc()  # Chronological order (earliest first)
            ).limit(50).all()  # Show last 50 sessions
        else:
            # No approved enrollments = no sessions visible
            upcoming_sessions = []

        # Get user's bookings
        my_bookings = db.query(Booking).filter(
            Booking.user_id == user.id
        ).all()
        enrolled_session_ids = {b.session_id for b in my_bookings}

        # Add enrolled status and instructor name to sessions
        budapest_tz = ZoneInfo("Europe/Budapest")
        now = datetime.now(budapest_tz).replace(tzinfo=None)  # CRITICAL: Make naive for comparison

        for session in upcoming_sessions:
            session.is_enrolled = session.id in enrolled_session_ids

            # Calculate if booking can be cancelled (12-hour deadline + no attendance/review)
            session_start = session.date_start  # Stored as naive Budapest time
            cancellation_deadline = session_start - timedelta(hours=12)  # Also naive

            # Check attendance and review for this student
            my_attendance = None
            my_instructor_review = None
            if session.is_enrolled:
                my_attendance = db.query(Attendance).filter(
                    Attendance.session_id == session.id,
                    Attendance.user_id == user.id
                ).first()

                my_instructor_review = db.query(InstructorSessionReview).filter(
                    InstructorSessionReview.session_id == session.id,
                    InstructorSessionReview.student_id == user.id
                ).first()

            # Attach attendance info to session for template use
            session.my_attendance = my_attendance
            session.my_instructor_review = my_instructor_review

            # VIRTUAL session: Check if student completed quiz (for "COMPLETED" badge)
            session.quiz_completed = False
            if session.is_enrolled and session.session_type == SessionType.virtual:
                session_quizzes = db.query(SessionQuiz).filter(
                    SessionQuiz.session_id == session.id
                ).all()

                # Check if student passed ANY required quiz
                for sq in session_quizzes:
                    if sq.is_required:
                        passed_attempt = db.query(QuizAttempt).filter(
                            QuizAttempt.quiz_id == sq.quiz_id,
                            QuizAttempt.user_id == user.id,
                            QuizAttempt.passed == True
                        ).first()

                        if passed_attempt:
                            session.quiz_completed = True
                            break

            session.can_cancel = session.is_enrolled and now < cancellation_deadline and not my_attendance and not my_instructor_review
            session.can_book = not session.is_enrolled and now < cancellation_deadline  # Can only book if 12+ hours before session start

            # Get instructor name
            if session.instructor_id:
                instructor = db.query(User).filter(User.id == session.instructor_id).first()
                session.instructor_name = instructor.name if instructor else "TBA"
            else:
                session.instructor_name = "TBA"

            # Get enrolled count
            enrolled_count = db.query(Booking).filter(
                Booking.session_id == session.id
            ).count()
            session.enrolled_count = enrolled_count

            # Get performance review from instructor (if exists)
            session.performance_review = None
            if session.is_enrolled:
                performance_review = db.query(StudentPerformanceReview).filter(
                    StudentPerformanceReview.session_id == session.id,
                    StudentPerformanceReview.student_id == user.id
                ).first()
                session.performance_review = performance_review

        return templates.TemplateResponse(
            "sessions.html",
            {
                "request": request,
                "user": user,
                "is_instructor": False,
                "upcoming_sessions": upcoming_sessions,
                "my_teaching_sessions": [],
                "spec_header_class": "hdr-hub",
                **_spec_ctx(user, db),
            }
        )


@router.post("/sessions/book/{session_id}")
async def book_session(
    request: Request,
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Book a session"""
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        # Redirect back with error
        return RedirectResponse(url="/sessions?error=session_not_found", status_code=303)

    # CRITICAL: Cannot book within 12 hours before session start
    # Use Budapest timezone for comparison (sessions are stored in Budapest time)
    budapest_tz = ZoneInfo("Europe/Budapest")
    now = datetime.now(budapest_tz).replace(tzinfo=None)  # Budapest time, naive
    session_start = session.date_start  # Stored as naive Budapest time
    booking_deadline = session_start - timedelta(hours=12)

    logger.debug(
        "booking_deadline_check",
        extra={"now": str(now), "start": str(session_start), "deadline": str(booking_deadline)},
    )

    if now >= booking_deadline:
        logger.warning("booking_blocked_deadline", extra={"user": user.email, "session_id": session_id})
        return RedirectResponse(url="/sessions?error=booking_deadline_passed", status_code=303)

    # Check if already booked
    existing_booking = db.query(Booking).filter(
        Booking.user_id == user.id,
        Booking.session_id == session_id
    ).first()

    if existing_booking:
        # Already booked
        return RedirectResponse(url="/sessions?info=already_booked", status_code=303)

    # Create booking
    booking = Booking(
        user_id=user.id,
        session_id=session_id,
        status=BookingStatus.CONFIRMED
    )
    db.add(booking)
    db.commit()

    logger.info("session_booked", extra={"user": user.email, "session_id": session_id})

    # Redirect back to sessions
    return RedirectResponse(url="/sessions?success=booked", status_code=303)


@router.post("/sessions/cancel/{session_id}")
async def cancel_booking(
    request: Request,
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Cancel a booking"""
    booking = db.query(Booking).filter(
        Booking.user_id == user.id,
        Booking.session_id == session_id
    ).first()

    if not booking:
        return RedirectResponse(url="/sessions?error=booking_not_found", status_code=303)

    # Get the session to check if it has started
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return RedirectResponse(url="/sessions?error=session_not_found", status_code=303)

    # Check cancellation deadline - Use Budapest timezone (database stores naive timestamps in local time)
    budapest_tz = ZoneInfo("Europe/Budapest")
    now = datetime.now(budapest_tz)

    # Database stores timestamps WITHOUT timezone (interpreted as Budapest time)
    session_start = session.date_start
    if session_start.tzinfo is None:
        session_start = session_start.replace(tzinfo=budapest_tz)

    # CRITICAL: Cannot cancel if session has ended
    if session.actual_end_time:
        return RedirectResponse(url=f"/sessions/{session_id}?error=session_already_ended", status_code=303)

    # 12-hour cancellation deadline - cannot cancel within 12 hours of session start
    cancellation_deadline = session_start - timedelta(hours=12)

    if now >= cancellation_deadline:
        return RedirectResponse(url=f"/sessions/{session_id}?error=cancellation_deadline_passed", status_code=303)

    # Check if attendance has been marked for this booking
    attendance = db.query(Attendance).filter(Attendance.booking_id == booking.id).first()
    if attendance:
        return RedirectResponse(url=f"/sessions/{session_id}?error=attendance_already_marked", status_code=303)

    # Check if student has submitted an instructor review (CRITICAL: cannot cancel after evaluation!)
    instructor_review = db.query(InstructorSessionReview).filter(
        InstructorSessionReview.session_id == session_id,
        InstructorSessionReview.student_id == user.id
    ).first()
    if instructor_review:
        return RedirectResponse(url=f"/sessions/{session_id}?error=evaluation_already_submitted", status_code=303)

    # Delete the booking
    db.delete(booking)
    db.commit()

    logger.info("session_booking_cancelled", extra={"user": user.email, "session_id": session_id})

    return RedirectResponse(url="/sessions?success=cancelled", status_code=303)


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
async def session_details(
    request: Request,
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Session details page"""
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get instructor info
    instructor = db.query(User).filter(User.id == session.instructor_id).first()
    session.instructor_name = instructor.name if instructor else "TBA"

    # Get enrolled students with attendance status
    bookings = db.query(Booking).filter(Booking.session_id == session_id).all()
    enrolled_students = []
    for booking in bookings:
        student = db.query(User).filter(User.id == booking.user_id).first()
        if student:
            # Check if attendance has been marked
            attendance = db.query(Attendance).filter(
                Attendance.session_id == session_id,
                Attendance.user_id == student.id
            ).first()

            # Get attendance history
            history = []
            if attendance:
                history_records = db.query(AttendanceHistory).filter(
                    AttendanceHistory.attendance_id == attendance.id
                ).order_by(AttendanceHistory.created_at.desc()).all()

                for h in history_records:
                    changer = db.query(User).filter(User.id == h.changed_by).first()
                    history.append({
                        'change_type': h.change_type,
                        'old_value': h.old_value,
                        'new_value': h.new_value,
                        'reason': h.reason,
                        'created_at': h.created_at,
                        'changed_by_name': changer.name if changer else 'Unknown'
                    })

            # Get existing performance review (if any)
            student_review = None
            if attendance:
                student_review = db.query(StudentPerformanceReview).filter(
                    StudentPerformanceReview.session_id == session_id,
                    StudentPerformanceReview.student_id == student.id
                ).first()

            # CRITICAL FIX: Build performance_review dict ONLY if student_review exists
            performance_review_dict = None
            if student_review:
                performance_review_dict = {
                    'punctuality': student_review.punctuality,
                    'engagement': student_review.engagement,
                    'focus': student_review.focus,
                    'collaboration': student_review.collaboration,
                    'attitude': student_review.attitude,
                    'comments': student_review.comments,
                    'average_score': student_review.average_score
                }

            enrolled_students.append({
                'id': student.id,
                'name': student.name,
                'email': student.email,
                'booking_id': booking.id,
                'status': booking.status,
                'attendance_status': attendance.status.value if attendance else None,
                'attendance_id': attendance.id if attendance else None,
                'confirmation_status': attendance.confirmation_status.value if (attendance and attendance.confirmation_status) else None,
                'dispute_reason': attendance.dispute_reason if attendance else None,
                'pending_change_to': attendance.pending_change_to if attendance else None,
                'change_request_reason': attendance.change_request_reason if attendance else None,
                'history': history,
                'performance_review': performance_review_dict
            })

    # Check if current user is enrolled and get their attendance
    is_enrolled = any(b.user_id == user.id for b in bookings)
    is_instructor = user.role == UserRole.INSTRUCTOR and session.instructor_id == user.id

    # Get current user's attendance status (for students)
    my_attendance = None
    my_instructor_review = None
    if is_enrolled and not is_instructor:
        my_attendance = db.query(Attendance).filter(
            Attendance.session_id == session_id,
            Attendance.user_id == user.id
        ).first()

        # Get student's review of instructor/session (if exists)
        # IMPORTANT: Instructor reviews are ONLY for ON-SITE and HYBRID sessions
        # - ON-SITE/HYBRID: Student evaluates instructor after attending (present/late status)
        # - VIRTUAL: NO instructor evaluation (students evaluate quiz/content instead)
        if my_attendance:
            my_instructor_review = db.query(InstructorSessionReview).filter(
                InstructorSessionReview.session_id == session_id,
                InstructorSessionReview.student_id == user.id
            ).first()

    # Check if attendance can be marked (only during or after session time)
    budapest_tz = ZoneInfo("Europe/Budapest")
    now = datetime.now(budapest_tz).replace(tzinfo=None)  # CRITICAL: Naive Budapest time for comparison

    # Session dates are stored as naive timestamps (Budapest time)
    session_start = session.date_start  # Already naive

    # Allow marking 15 minutes before session starts
    can_mark_attendance = is_instructor and (session_start - timedelta(minutes=15)) <= now

    # Check if booking can be cancelled (12-hour deadline)
    # CANNOT cancel if: attendance exists OR instructor review exists OR past 12-hour deadline
    cancellation_deadline = session_start - timedelta(hours=12)
    can_cancel_booking = is_enrolled and not is_instructor and now < cancellation_deadline and not my_attendance and not my_instructor_review
    can_book_session = not is_enrolled and not is_instructor and now < cancellation_deadline  # Can only book if 12+ hours before session start

    # Load quiz data for HYBRID and VIRTUAL sessions
    session_quizzes = []
    if session.session_type.value in ['hybrid', 'virtual']:
        sq_records = db.query(SessionQuiz).filter(SessionQuiz.session_id == session_id).all()

        for sq in sq_records:
            quiz = db.query(Quiz).filter(Quiz.id == sq.quiz_id).first()
            if quiz:
                # Count questions
                question_count = db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz.id).count()

                # Get user's quiz attempts (for students)
                user_attempts = []
                if not is_instructor and is_enrolled:
                    attempts = db.query(QuizAttempt).filter(
                        QuizAttempt.quiz_id == quiz.id,
                        QuizAttempt.user_id == user.id
                    ).order_by(QuizAttempt.started_at.desc()).all()

                    for attempt in attempts:
                        user_attempts.append({
                            'score': attempt.score,
                            'passed': attempt.passed,
                            'completed_at': attempt.completed_at,
                            'correct_answers': attempt.correct_answers,
                            'total_questions': attempt.total_questions
                        })

                # Get all student results (for instructors)
                student_results = []
                if is_instructor:
                    # Get all enrolled students
                    for booking in bookings:
                        student = db.query(User).filter(User.id == booking.user_id).first()
                        if student:
                            # Get all attempts for this student
                            attempts = db.query(QuizAttempt).filter(
                                QuizAttempt.quiz_id == quiz.id,
                                QuizAttempt.user_id == student.id
                            ).order_by(QuizAttempt.started_at.desc()).all()

                            # Calculate best score and passed status
                            best_score = None
                            best_passed = False
                            best_correct = 0
                            last_attempt_date = None
                            all_attempts = []

                            for attempt in attempts:
                                all_attempts.append({
                                    'score': attempt.score,
                                    'passed': attempt.passed,
                                    'completed_at': attempt.completed_at,
                                    'correct_answers': attempt.correct_answers,
                                    'total_questions': attempt.total_questions,
                                    'time_spent_minutes': attempt.time_spent_minutes
                                })

                                if best_score is None or (attempt.score and attempt.score > best_score):
                                    best_score = attempt.score
                                    best_passed = attempt.passed
                                    best_correct = attempt.correct_answers

                                if attempt.completed_at and (last_attempt_date is None or attempt.completed_at > last_attempt_date):
                                    last_attempt_date = attempt.completed_at

                            student_results.append({
                                'student_id': student.id,
                                'student_name': student.name,
                                'student_email': student.email,
                                'attempts_count': len(attempts),
                                'best_score': best_score,
                                'best_passed': best_passed,
                                'best_correct': best_correct,
                                'total_questions': question_count,
                                'last_attempt_date': last_attempt_date,
                                'all_attempts': all_attempts
                            })

                session_quizzes.append({
                    'quiz': {
                        'id': quiz.id,
                        'title': quiz.title,
                        'description': quiz.description,
                        'passing_score': quiz.passing_score,
                        'time_limit_minutes': quiz.time_limit_minutes,
                        'question_count': question_count,
                        'user_attempts': user_attempts
                    },
                    'max_attempts': sq.max_attempts,
                    'is_required': sq.is_required,
                    'student_results': student_results
                })

    session_segments = (
        db.query(SessionSegment)
        .filter(
            SessionSegment.session_id == session_id,
            SessionSegment.is_active == True,
        )
        .order_by(SessionSegment.position)
        .all()
    ) if is_instructor else []

    return templates.TemplateResponse(
        "session_details.html",
        {
            "request": request,
            "user": user,
            "spec_header_class": "hdr-hub",
            "session": session,
            "enrolled_students": enrolled_students,
            "is_enrolled": is_enrolled,
            "is_instructor": is_instructor,
            "can_mark_attendance": can_mark_attendance,
            "can_cancel_booking": can_cancel_booking,
            "can_book_session": can_book_session,
            "my_attendance": my_attendance,
            "my_instructor_review": my_instructor_review,
            "session_quizzes": session_quizzes,
            "now": now,
            "session_segments": session_segments,
            "all_skill_keys": get_all_skill_keys() if is_instructor else [],
        }
    )


