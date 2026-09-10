"""
Quiz attempt operations
Start and submit quiz attempts
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User, UserRole
from .....schemas.quiz import (
    QuizAttemptStart, QuizAttemptSubmit, QuizAttemptResponse
)
from .....services.quiz_service import QuizService
from .....services.competency_service import CompetencyService
from .....services.adaptive_learning_service import AdaptiveLearningService
from .....services.player_participation_service import (
    complete_virtual_session_participation,
)
from .helpers import get_quiz_service

router = APIRouter()

@router.post("/start", response_model=QuizAttemptResponse)
def start_quiz_attempt(
    attempt_data: QuizAttemptStart,
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service),
    db: Session = Depends(get_db)
):
    """Start a new quiz attempt"""
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can take quizzes"
        )

    # 🔒 ACCESS CONTROL: Same checks as get_quiz_for_taking
    session_quiz = db.query(SessionQuiz).filter(
        SessionQuiz.quiz_id == attempt_data.quiz_id,
        SessionQuiz.is_required == True
    ).first()

    if session_quiz:
        # Quiz is linked to a session - apply session-specific access control
        session = db.query(SessionModel).filter(
            SessionModel.id == session_quiz.session_id
        ).first()

        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Associated session not found"
            )

        # Check if user has a CONFIRMED booking for this session
        booking = db.query(Booking).filter(
            Booking.user_id == current_user.id,
            Booking.session_id == session.id,
            Booking.status == BookingStatus.CONFIRMED
        ).first()

        if not booking:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must have a confirmed booking for this session to start the quiz"
            )

        # 🎯 HYBRID Session: Check attendance + quiz unlock
        if session.session_type == SessionType.hybrid:
            # 1. Check if quiz is unlocked by instructor
            if not session.quiz_unlocked:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Quiz is not yet unlocked by the instructor. Please wait for the instructor to unlock it during the session."
                )

            # 2. Check if user is marked present on attendance sheet
            attendance = db.query(Attendance).filter(
                Attendance.user_id == current_user.id,
                Attendance.session_id == session.id,
                Attendance.status == AttendanceStatus.present
            ).first()

            if not attendance:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You must be marked present on the attendance sheet to start the quiz"
                )

        # 🌐 VIRTUAL Session: Check time window
        elif session.session_type == SessionType.virtual:
            current_time = datetime.now()

            # Session must be active (within date_start and date_end)
            if current_time < session.date_start:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Quiz is not yet available. Session starts at {session.date_start.isoformat()}"
                )

            if current_time > session.date_end:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Quiz is no longer available. Session has ended."
                )

    try:
        attempt = quiz_service.start_quiz_attempt(current_user.id, attempt_data.quiz_id)
        return attempt
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/submit", response_model=QuizAttemptResponse)
def submit_quiz_attempt(
    submission: QuizAttemptSubmit,
    current_user: User = Depends(get_current_user),
    quiz_service: QuizService = Depends(get_quiz_service),
    db: Session = Depends(get_db)
):
    """Submit quiz attempt with answers"""
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can take quizzes"
        )

    try:
        # Submit quiz and get results
        attempt = quiz_service.submit_quiz_attempt(current_user.id, submission)

        # ==========================================
        # 🆕 HOOK 1: AUTOMATIC COMPETENCY ASSESSMENT
        # ==========================================
        # Use SEPARATE session to avoid transaction conflicts
        hook_db = None

        try:
            # Create new session for hooks
            hook_db = SessionLocal()

            # Initialize services with separate session
            comp_service = CompetencyService(hook_db)
            adapt_service = AdaptiveLearningService(hook_db)

            # Get quiz details for competency assessment
            quiz = quiz_service.get_quiz_by_id(attempt.quiz_id)

            # Assess competency from quiz (automatic based on quiz category/metadata)
            if quiz and attempt.score is not None:
                comp_service.assess_from_quiz(
                    user_id=current_user.id,
                    quiz_id=quiz.id,
                    quiz_attempt_id=attempt.id,
                    score=float(attempt.score)
                )

            # Update adaptive learning profile
            adapt_service.update_profile_metrics(current_user.id)

            # Generate new recommendations if score is low (struggling student)
            if attempt.score and attempt.score < 70:
                adapt_service.generate_recommendations(
                    user_id=current_user.id,
                    refresh=True
                )

            # Commit hook transaction
            hook_db.commit()

        except Exception as e:
            # Log error but don't fail quiz submission
            logger = logging.getLogger(__name__)
            logger.error(f"Error in post-quiz hooks for user {current_user.id}: {e}")
            if hook_db:
                hook_db.rollback()
            # Continue with quiz result response

        finally:
            # Always close the hook session
            if hook_db:
                hook_db.close()

        # ==========================================
        # 🏆 GAMIFICATION: Check for achievement unlocks
        # ==========================================
        gamification_service = GamificationService(db)
        try:
            # Check for quiz completion achievement
            unlocked = gamification_service.check_and_unlock_achievements(
                user_id=current_user.id,
                trigger_action="complete_quiz"
            )

            # Check for perfect score achievement if score is 100%
            if attempt.score and attempt.score >= 100:
                perfect_score_unlocked = gamification_service.check_and_unlock_achievements(
                    user_id=current_user.id,
                    trigger_action="quiz_perfect_score",
                    context={"score": attempt.score}
                )
                unlocked.extend(perfect_score_unlocked)

            if unlocked:
                print(f"🎉 Unlocked {len(unlocked)} achievement(s) for user {current_user.id}")
        except Exception as e:
            # Don't fail quiz submission if achievement check fails
            print(f"⚠️  Achievement check failed: {e}")

        # ==========================================
        # 🆕 AUTOMATIC ATTENDANCE FOR VIRTUAL SESSIONS
        # ==========================================
        # If this quiz is linked to a VIRTUAL session, mark automatic attendance
        try:
            session_quiz = db.query(SessionQuiz).filter(
                SessionQuiz.quiz_id == attempt.quiz_id,
                SessionQuiz.is_required == True
            ).first()

            if session_quiz:
                # Get the session
                session = db.query(SessionModel).filter(
                    SessionModel.id == session_quiz.session_id
                ).first()

                # If VIRTUAL session → automatic attendance
                if session and str(session.session_type).lower() == 'virtual':
                    attendance_result = complete_virtual_session_participation(
                        db,
                        player=current_user,
                        session_id=session.id,
                        notes=f"Auto-marked: Quiz completed with {attempt.score}%",
                        source="API_QUIZ",
                    )
                    gamification_service.award_attendance_xp(
                        attendance_id=attendance_result.attendance.id,
                        quiz_score_percent=attempt.score
                    )

        except Exception as e:
            # Don't fail quiz submission if auto-attendance fails
            print(f"⚠️  Auto-attendance failed: {e}")
            db.rollback()

        return attempt
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
