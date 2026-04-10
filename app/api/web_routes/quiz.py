"""
Quiz and adaptive learning routes
"""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from zoneinfo import ZoneInfo
from sqlalchemy.orm import joinedload

import logging

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User, UserRole
from ...models.session import Session as SessionModel, SessionType
from ...models.booking import Booking
from ...models.attendance import Attendance
from ...models.quiz import Quiz, QuizQuestion, QuizAnswerOption, QuizAttempt, QuizUserAnswer, SessionQuiz
from ...models.gamification import UserStats

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sessions/{session_id}/unlock-quiz")
async def unlock_quiz(
    request: Request,
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """
    Instructor unlocks the quiz for HYBRID sessions

    Requirements:
    - User must be instructor
    - Session must be HYBRID type
    - Instructor must own this session
    - Session must be started (in progress)
    """
    if user.role != UserRole.INSTRUCTOR:
        return RedirectResponse(url=f"/sessions/{session_id}?error=unauthorized", status_code=303)

    # Get session
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return RedirectResponse(url="/sessions?error=session_not_found", status_code=303)

    # Verify session is HYBRID
    if session.session_type != SessionType.hybrid:
        return RedirectResponse(
            url=f"/sessions/{session_id}?error=unlock_only_hybrid",
            status_code=303
        )

    # Verify instructor owns this session
    if session.instructor_id != user.id:
        return RedirectResponse(url=f"/sessions/{session_id}?error=not_your_session", status_code=303)

    # Verify session was started
    if session.actual_start_time is None:
        return RedirectResponse(
            url=f"/sessions/{session_id}?error=session_not_started_unlock",
            status_code=303
        )

    # Unlock quiz
    session.quiz_unlocked = True
    db.commit()

    logger.info("quiz_unlocked_hybrid", extra={"session_id": session_id, "instructor_id": user.id})

    return RedirectResponse(
        url=f"/sessions/{session_id}?success=quiz_unlocked",
        status_code=303
    )


# ==========================================
# QUIZ TAKING ROUTES (Web Interface)
# ==========================================

@router.get("/quizzes/{quiz_id}/take")
async def take_quiz(
    request: Request,
    quiz_id: int,
    session_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """
    Quiz taking page for students

    This is a web interface for quiz-taking linked from sessions.
    Students can take quizzes through this interface.
    """
    quiz = db.query(Quiz).options(
        joinedload(Quiz.questions).joinedload(QuizQuestion.answer_options)
    ).filter(Quiz.id == quiz_id).first()

    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    # CRITICAL: Check if this quiz is linked to a session, and if so, verify the user is BOOKED
    if session_id:
        session_quiz = db.query(SessionQuiz).filter(
            SessionQuiz.session_id == session_id,
            SessionQuiz.quiz_id == quiz_id
        ).first()

        if not session_quiz:
            raise HTTPException(status_code=404, detail="Quiz not found for this session")

        # Get the session to check start time
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Check if session has started (quiz only available after session start time)
        budapest_tz = ZoneInfo("Europe/Budapest")
        now = datetime.now(budapest_tz)
        session_start = session.date_start
        if session_start.tzinfo is None:
            session_start = session_start.replace(tzinfo=budapest_tz)

        if now < session_start:
            raise HTTPException(
                status_code=403,
                detail=f"Quiz not available yet. Session starts at {session_start.strftime('%Y-%m-%d %H:%M')}."
            )

        # Check if user is BOOKED for this session
        booking = db.query(Booking).filter(
            Booking.session_id == session_id,
            Booking.user_id == user.id,
            Booking.status == 'CONFIRMED'
        ).first()

        if not booking:
            raise HTTPException(
                status_code=403,
                detail="You must book this session before taking the quiz. Please enroll first!"
            )

    # Check if user already has an active (incomplete) attempt
    active_attempt = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == user.id,
        QuizAttempt.quiz_id == quiz_id,
        QuizAttempt.completed_at == None
    ).first()

    # If no active attempt, create one
    if not active_attempt:
        active_attempt = QuizAttempt(
            user_id=user.id,
            quiz_id=quiz_id,
            started_at=datetime.now(timezone.utc),
            total_questions=len(quiz.questions)
        )
        db.add(active_attempt)
        db.commit()
        db.refresh(active_attempt)

    # Calculate remaining time
    elapsed_seconds = (datetime.now(timezone.utc) - active_attempt.started_at).total_seconds()
    time_limit_seconds = quiz.time_limit_minutes * 60
    remaining_seconds = max(0, int(time_limit_seconds - elapsed_seconds))

    # If time expired, auto-submit with 0 score
    if remaining_seconds == 0:
        active_attempt.completed_at = datetime.now(timezone.utc)
        active_attempt.score = 0.0
        active_attempt.correct_answers = 0
        active_attempt.passed = False
        active_attempt.xp_awarded = 0
        active_attempt.time_spent_minutes = quiz.time_limit_minutes
        db.commit()

        return templates.TemplateResponse("quiz_result.html", {
            "request": request,
            "user": user,
            "quiz": quiz,
            "session": None,
            "session_id": session_id,
            "score": 0.0,
            "passed": False,
            "correct_count": 0,
            "total_questions": len(quiz.questions),
            "xp_awarded": 0,
            "time_spent": quiz.time_limit_minutes,
            "attempt_answers": [],
        })

    # Get session if provided
    session = None
    if session_id:
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()

    return templates.TemplateResponse("quiz_take.html", {
        "request": request,
        "user": user,
        "quiz": quiz,
        "session": session,
        "session_id": session_id,
        "attempt_id": active_attempt.id,
        "remaining_seconds": remaining_seconds
    })


@router.post("/quizzes/{quiz_id}/submit")
async def submit_quiz(
    request: Request,
    quiz_id: int,
    session_id: Optional[str] = Form(None),
    attempt_id: int = Form(...),
    time_spent: float = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """
    Submit quiz answers and calculate score
    Simple scoring system: Understood (pass) / Needs Review (fail)
    """
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    # CRITICAL: Check if this quiz is linked to a session, and if so, verify the user is BOOKED
    if session_id and session_id != "None":
        session_id_int = int(session_id)
        session_quiz = db.query(SessionQuiz).filter(
            SessionQuiz.session_id == session_id_int,
            SessionQuiz.quiz_id == quiz_id
        ).first()

        if not session_quiz:
            raise HTTPException(status_code=404, detail="Quiz not found for this session")

        # Get the session to check start time
        session = db.query(SessionModel).filter(SessionModel.id == session_id_int).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Check if session has started (quiz only available after session start time)
        budapest_tz = ZoneInfo("Europe/Budapest")
        now = datetime.now(budapest_tz)
        session_start = session.date_start
        if session_start.tzinfo is None:
            session_start = session_start.replace(tzinfo=budapest_tz)

        if now < session_start:
            raise HTTPException(
                status_code=403,
                detail=f"Quiz not available yet. Session starts at {session_start.strftime('%Y-%m-%d %H:%M')}."
            )

        # Check if user is BOOKED for this session
        booking = db.query(Booking).filter(
            Booking.session_id == session_id_int,
            Booking.user_id == user.id,
            Booking.status == 'CONFIRMED'
        ).first()

        if not booking:
            raise HTTPException(
                status_code=403,
                detail="You must book this session before submitting the quiz!"
            )

    # Get the active attempt — SELECT FOR UPDATE serialises concurrent submits:
    # two simultaneous requests both see completed_at=None without the lock;
    # with the lock, the second waits until the first commits, then sees
    # completed_at IS NOT NULL and raises 400.
    attempt = db.query(QuizAttempt).filter(
        QuizAttempt.id == attempt_id,
        QuizAttempt.user_id == user.id,
        QuizAttempt.quiz_id == quiz_id
    ).with_for_update().first()

    if not attempt:
        raise HTTPException(status_code=404, detail="Quiz attempt not found")

    # Check if already completed
    if attempt.completed_at:
        raise HTTPException(status_code=400, detail="Quiz already submitted")

    # Get form data
    form_data = await request.form()

    # Calculate score
    correct_count = 0
    total_points = 0
    earned_points = 0

    for question in quiz.questions:
        total_points += question.points
        field_name = f"question_{question.id}"
        selected_option_id = form_data.get(field_name)

        if selected_option_id:
            selected_option_id = int(selected_option_id)
            option = db.query(QuizAnswerOption).filter(QuizAnswerOption.id == selected_option_id).first()

            if option and option.is_correct:
                correct_count += 1
                earned_points += question.points

            # Save user answer
            user_answer = QuizUserAnswer(
                attempt_id=attempt.id,
                question_id=question.id,
                selected_option_id=selected_option_id,
                is_correct=option.is_correct if option else False
            )
            db.add(user_answer)

    # Calculate percentage score
    score = (earned_points / total_points * 100) if total_points > 0 else 0
    # CRITICAL: passing_score is stored as decimal (0.75 = 75%), score is percentage (75)
    passed = score >= (quiz.passing_score * 100)

    # Update attempt with completion
    attempt.completed_at = datetime.now(timezone.utc)
    attempt.time_spent_minutes = time_spent
    attempt.score = score
    attempt.correct_answers = correct_count
    attempt.passed = passed
    attempt.xp_awarded = quiz.xp_reward if passed else 0

    # Update user_stats with earned XP (GAMIFICATION SYNC)
    if attempt.xp_awarded > 0:
        user_stats = db.query(UserStats).filter(UserStats.user_id == user.id).first()

        if not user_stats:
            # Create user_stats if doesn't exist
            user_stats = UserStats(
                user_id=user.id,
                total_xp=attempt.xp_awarded,
                level=1
            )
            db.add(user_stats)
        else:
            # Add XP to existing total
            user_stats.total_xp = (user_stats.total_xp or 0) + attempt.xp_awarded
            # Update level (1000 XP per level)
            user_stats.level = max(1, user_stats.total_xp // 1000)

    db.commit()

    # Eager-load user answers for result review (N+1 prevention)
    attempt_with_answers = (
        db.query(QuizAttempt)
        .options(
            joinedload(QuizAttempt.user_answers)
                .joinedload(QuizUserAnswer.question)
                .joinedload(QuizQuestion.answer_options),
            joinedload(QuizAttempt.user_answers)
                .joinedload(QuizUserAnswer.selected_option),
        )
        .filter(QuizAttempt.id == attempt.id)
        .first()
    )
    attempt_answers = (
        sorted(attempt_with_answers.user_answers, key=lambda ua: ua.question.order_index)
        if attempt_with_answers else []
    )

    # Get session for back link
    session = None
    if session_id and session_id.strip():
        try:
            session = db.query(SessionModel).filter(SessionModel.id == int(session_id)).first()

            # VIRTUAL SESSION: Auto-mark attendance if quiz passed
            if session and session.session_type.value == 'virtual' and passed:
                booking = db.query(Booking).filter(
                    Booking.user_id == user.id,
                    Booking.session_id == session.id
                ).first()

                if booking:  # pragma: no branch  # booking verified CONFIRMED at lines 263-273 above
                    # Check if attendance already exists
                    existing_attendance = db.query(Attendance).filter(
                        Attendance.user_id == user.id,
                        Attendance.session_id == session.id
                    ).first()

                    if not existing_attendance:  # pragma: no branch  # idempotency; covered by instructor.py identical path
                        # Auto-create attendance as 'present' for successful quiz
                        auto_attendance = Attendance(
                            user_id=user.id,
                            session_id=session.id,
                            booking_id=booking.id,
                            status='present',
                            check_in_time=datetime.now(timezone.utc)
                        )
                        db.add(auto_attendance)
                        db.commit()
                        logger.info("attendance_auto_marked_virtual", extra={"session_id": session.id, "user": user.email})

        except ValueError:
            pass

    # Render result page
    return templates.TemplateResponse("quiz_result.html", {
        "request": request,
        "user": user,
        "quiz": quiz,
        "session": session,
        "session_id": session_id,
        "score": score,
        "passed": passed,
        "correct_count": correct_count,
        "total_questions": len(quiz.questions),
        "xp_awarded": attempt.xp_awarded,
        "time_spent": time_spent,
        "attempt_answers": attempt_answers,
    })


# ==========================================
# PERFORMANCE REVIEW ENDPOINTS (On-Site Sessions Only)
# Two-way evaluation system for On-Site training sessions
# ==========================================

