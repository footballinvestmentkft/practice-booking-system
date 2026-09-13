from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc
from datetime import datetime
from uuid import UUID

from app.models.quiz import (
    Quiz, QuizQuestion, QuizAnswerOption, QuizAttempt, QuizUserAnswer,
    QuestionType, QuizCategory
)
from app.models.user import User
from app.models.track import LessonAssessmentQuiz
from app.schemas.quiz import (
    QuizCreate, QuizAttemptSubmit,
    QuizStatistics, UserQuizStatistics
)
from app.services.gamification import GamificationService

class QuizService:
    def __init__(self, db: Session):
        self.db = db
        self.gamification_service = GamificationService(db)

    def create_quiz(self, quiz_data: QuizCreate) -> Quiz:
        """Create a new quiz with questions and answers"""
        quiz = Quiz(
            title=quiz_data.title,
            description=quiz_data.description,
            category=quiz_data.category,
            difficulty=quiz_data.difficulty,
            time_limit_minutes=quiz_data.time_limit_minutes,
            xp_reward=quiz_data.xp_reward,
            passing_score=quiz_data.passing_score,
            is_active=quiz_data.is_active
        )
        self.db.add(quiz)
        self.db.flush()  # Get the quiz ID
        # Add questions
        for question_data in quiz_data.questions:
            question = QuizQuestion(
                quiz_id=quiz.id,
                question_text=question_data.question_text,
                question_type=question_data.question_type,
                points=question_data.points,
                order_index=question_data.order_index,
                explanation=question_data.explanation
            )
            self.db.add(question)
            self.db.flush()  # Get the question ID
            
            # Add answer options
            for option_data in question_data.answer_options:
                option = QuizAnswerOption(
                    question_id=question.id,
                    option_text=option_data.option_text,
                    is_correct=option_data.is_correct,
                    order_index=option_data.order_index
                )
                self.db.add(option)
        
        self.db.commit()
        return quiz

    def get_quiz_by_id(self, quiz_id: int) -> Optional[Quiz]:
        """Get quiz by ID with all related data"""
        return self.db.query(Quiz).filter(Quiz.id == quiz_id).first()

    def get_available_quizzes(self, user_id: int) -> List[Quiz]:
        """Get quizzes that the user hasn't completed yet"""
        completed_quiz_ids = self.db.query(QuizAttempt.quiz_id).filter(
            and_(
                QuizAttempt.user_id == user_id,
                QuizAttempt.completed_at.isnot(None)
            )
        ).subquery()
        
        return self.db.query(Quiz).filter(
            and_(
                Quiz.is_active == True,
                ~Quiz.id.in_(completed_quiz_ids)
            )
        ).order_by(Quiz.category, Quiz.difficulty, Quiz.title).all()

    def get_quizzes_by_category(self, category: QuizCategory) -> List[Quiz]:
        """Get all active quizzes in a specific category"""
        return self.db.query(Quiz).filter(
            and_(Quiz.category == category, Quiz.is_active == True)
        ).order_by(Quiz.difficulty, Quiz.title).all()

    def start_quiz_attempt(self, user_id: int, quiz_id: int) -> QuizAttempt:
        """Start a new quiz attempt"""
        mappings = self.db.query(LessonAssessmentQuiz).filter_by(quiz_id=quiz_id).all()
        if not isinstance(mappings, list):
            mappings = []
        if mappings:
            if len(mappings) != 1:
                raise ValueError("ASSESSMENT_CONTEXT_REQUIRED")
            from app.services.assessment_integrity_service import AssessmentIntegrityService
            user = self.db.get(User, user_id)
            canonical = AssessmentIntegrityService(self.db).start_attempt(
                user=user,
                assessment_id=mappings[0].lesson_assessment_id,
                locale=mappings[0].locale,
            )
            self.db.commit()
            return self.db.query(QuizAttempt).filter_by(assessment_attempt_id=canonical.id).one()
        # Check if user already completed this quiz
        existing_attempt = self.db.query(QuizAttempt).filter(
            and_(
                QuizAttempt.user_id == user_id,
                QuizAttempt.quiz_id == quiz_id,
                QuizAttempt.completed_at.isnot(None)
            )
        ).first()
        
        if existing_attempt:
            raise ValueError("Quiz already completed")
        
        # Check if user has an ongoing attempt
        ongoing_attempt = self.db.query(QuizAttempt).filter(
            and_(
                QuizAttempt.user_id == user_id,
                QuizAttempt.quiz_id == quiz_id,
                QuizAttempt.completed_at.is_(None)
            )
        ).first()
        
        if ongoing_attempt:
            return ongoing_attempt
        
        # Get quiz to count questions
        quiz = self.get_quiz_by_id(quiz_id)
        if not quiz:
            raise ValueError("Quiz not found")
        
        if not quiz.is_active:
            raise ValueError("Quiz is not active")
        
        question_count = len(quiz.questions)
        
        # Create new attempt
        attempt = QuizAttempt(
            user_id=user_id,
            quiz_id=quiz_id,
            total_questions=question_count
        )
        
        self.db.add(attempt)
        self.db.commit()
        return attempt

    def submit_quiz_attempt(self, user_id: int, submission: QuizAttemptSubmit) -> QuizAttempt:
        """Submit quiz attempt with answers and calculate score"""
        attempt = self.db.query(QuizAttempt).filter(
            and_(
                QuizAttempt.id == submission.attempt_id,
                QuizAttempt.user_id == user_id,
            )
        ).with_for_update().first()
        
        if not attempt:
            raise ValueError("Quiz attempt not found")
        if isinstance(attempt.assessment_attempt_id, UUID):
            from app.services.assessment_integrity_service import AssessmentIntegrityService
            user = self.db.get(User, user_id)
            service = AssessmentIntegrityService(self.db)
            canonical = service._attempt(attempt.assessment_attempt_id, user)
            if canonical.status == "IN_PROGRESS":
                for answer in submission.answers:
                    service.answer_source_question(
                        user=user, attempt_id=canonical.id, question_id=answer.question_id,
                        selected_option_id=answer.selected_option_id,
                        answer_text=answer.answer_text,
                        idempotency_key=f"quiz:{attempt.id}:question:{answer.question_id}",
                    )
            service.complete_attempt(
                user=user, attempt_id=canonical.id,
                idempotency_key=f"quiz:{attempt.id}:complete",
            )
            self.db.commit()
            self.db.refresh(attempt)
            return attempt
        if attempt.completed_at is not None:
            raise ValueError("Quiz attempt already completed")
        
        # Check time limit
        quiz = self.get_quiz_by_id(attempt.quiz_id)
        # Convert to UTC naive for comparison
        now = datetime.utcnow()
        started_at = attempt.started_at.replace(tzinfo=None) if attempt.started_at.tzinfo else attempt.started_at
        time_elapsed = (now - started_at).total_seconds() / 60
        if time_elapsed > quiz.time_limit_minutes:
            raise ValueError("Time limit exceeded")
        
        # Process answers
        correct_answers = 0
        total_points = 0
        earned_points = 0
        
        # Get all questions with their correct answers
        questions = self.db.query(QuizQuestion).filter(
            QuizQuestion.quiz_id == attempt.quiz_id
        ).all()
        
        question_dict = {q.id: q for q in questions}
        
        for answer_data in submission.answers:
            if answer_data.question_id not in question_dict:
                continue
                
            question = question_dict[answer_data.question_id]
            total_points += question.points
            
            is_correct = False
            
            if question.question_type in [QuestionType.MULTIPLE_CHOICE, QuestionType.TRUE_FALSE]:
                # Check if selected option is correct
                if answer_data.selected_option_id:
                    correct_option = self.db.query(QuizAnswerOption).filter(
                        and_(
                            QuizAnswerOption.id == answer_data.selected_option_id,
                            QuizAnswerOption.question_id == question.id,
                            QuizAnswerOption.is_correct == True
                        )
                    ).first()
                    
                    if correct_option:
                        is_correct = True
                        earned_points += question.points
                        
            elif question.question_type == QuestionType.FILL_IN_BLANK:
                # For fill-in-blank, check against correct answer options
                if answer_data.answer_text:
                    correct_options = self.db.query(QuizAnswerOption).filter(
                        and_(
                            QuizAnswerOption.question_id == question.id,
                            QuizAnswerOption.is_correct == True
                        )
                    ).all()
                    
                    # Case-insensitive matching
                    user_answer = answer_data.answer_text.strip().lower()
                    for option in correct_options:
                        if user_answer == option.option_text.strip().lower():
                            is_correct = True
                            earned_points += question.points
                            break
            
            if is_correct:
                correct_answers += 1
            
            # Save user answer
            user_answer = QuizUserAnswer(
                attempt_id=attempt.id,
                question_id=question.id,
                selected_option_id=answer_data.selected_option_id,
                answer_text=answer_data.answer_text,
                is_correct=is_correct
            )
            self.db.add(user_answer)
        
        # Calculate final score
        score = (earned_points / total_points) * 100 if total_points > 0 else 0
        passed = score >= quiz.passing_score
        
        # Calculate time spent (ensure both datetimes are timezone-aware UTC)
        from datetime import timezone
        
        now = datetime.now(timezone.utc)
        started_at = attempt.started_at
        
        # Convert to UTC timezone-aware if it's naive or different timezone
        if started_at.tzinfo is None:
            # If naive, assume it's UTC
            started_at = started_at.replace(tzinfo=timezone.utc)
        elif started_at.tzinfo != timezone.utc:
            # Convert to UTC if in different timezone
            started_at = started_at.astimezone(timezone.utc)
        
        # Calculate time difference in seconds
        time_diff_seconds = (now - started_at).total_seconds()
        
        # Store time in seconds (more precise)
        time_spent_seconds_raw = round(time_diff_seconds)
        time_spent_seconds = max(0, time_spent_seconds_raw)
        
        # Log if we have a timing issue for debugging
        if time_spent_seconds_raw < 0:
            print(f"Warning: Negative time calculated for attempt {attempt.id}:")
            print(f"  Started at: {started_at}")
            print(f"  Completed at: {now}")
            print(f"  Raw time diff: {time_spent_seconds_raw} seconds")
        
        # Award XP if passed
        xp_awarded = 0
        if passed:
            xp_awarded = quiz.xp_reward
            self.gamification_service.award_xp(user_id, xp_awarded, f"Quiz completed: {quiz.title}")
        
        # Update attempt
        attempt.completed_at = now
        attempt.time_spent_minutes = time_spent_seconds
        attempt.score = score
        attempt.correct_answers = correct_answers
        attempt.xp_awarded = xp_awarded
        attempt.passed = passed
        
        self.db.commit()
        
        # Check for first-time achievements if quiz was passed
        if passed:
            self.gamification_service.check_and_award_first_time_achievements(user_id)
            # Also check for newcomer welcome achievement
            self.gamification_service.check_newcomer_welcome(user_id)
        
        # Check if this quiz is an enrollment quiz for any project
        self._process_enrollment_quiz_if_applicable(attempt)
        
        return attempt
    
    def _process_enrollment_quiz_if_applicable(self, attempt: QuizAttempt):
        """Check if the completed quiz is an enrollment quiz and process accordingly"""
        from ..models.project import ProjectQuiz, ProjectEnrollmentQuiz, ProjectEnrollment, ProjectEnrollmentStatus, ProjectProgressStatus
        
        # Find if this quiz is used as an enrollment quiz for any project
        enrollment_project_quiz = self.db.query(ProjectQuiz).filter(
            ProjectQuiz.quiz_id == attempt.quiz_id,
            ProjectQuiz.quiz_type == "enrollment",
            ProjectQuiz.is_active == True
        ).first()
        
        if not enrollment_project_quiz:
            return  # Not an enrollment quiz
        
        # Check if user already has a ProjectEnrollment record for this project
        existing_project_enrollment = self.db.query(ProjectEnrollment).filter(
            ProjectEnrollment.project_id == enrollment_project_quiz.project_id,
            ProjectEnrollment.user_id == attempt.user_id
        ).first()
        
        if existing_project_enrollment:
            return  # Already has enrollment record
            
        # Check if user already has an enrollment quiz record for this project
        existing_enrollment = self.db.query(ProjectEnrollmentQuiz).filter(
            ProjectEnrollmentQuiz.project_id == enrollment_project_quiz.project_id,
            ProjectEnrollmentQuiz.user_id == attempt.user_id
        ).first()
        
        if existing_enrollment:
            return  # Already processed
        
        # Get all existing enrollment attempts for this project to calculate priority
        all_enrollments = self.db.query(ProjectEnrollmentQuiz).join(QuizAttempt).filter(
            ProjectEnrollmentQuiz.project_id == enrollment_project_quiz.project_id
        ).all()
        
        # Calculate priority: higher score = lower priority number (better ranking)
        enrollment_priority = 1
        for existing in all_enrollments:
            existing_attempt = self.db.query(QuizAttempt).filter(
                QuizAttempt.id == existing.quiz_attempt_id
            ).first()
            
            if existing_attempt and (
                existing_attempt.score > attempt.score or 
                (existing_attempt.score == attempt.score and existing_attempt.completed_at < attempt.completed_at)
            ):
                enrollment_priority += 1
        
        # Create enrollment quiz record
        enrollment_quiz_record = ProjectEnrollmentQuiz(
            project_id=enrollment_project_quiz.project_id,
            user_id=attempt.user_id,
            quiz_attempt_id=attempt.id,
            enrollment_priority=enrollment_priority,
            enrollment_confirmed=False  # User needs to confirm enrollment
        )
        
        self.db.add(enrollment_quiz_record)
        
        # 🔥 NEW: Create ProjectEnrollment record with appropriate status
        if not attempt.passed:
            # Quiz failed - create NOT_ELIGIBLE enrollment record
            failed_enrollment = ProjectEnrollment(
                project_id=enrollment_project_quiz.project_id,
                user_id=attempt.user_id,
                status=ProjectEnrollmentStatus.NOT_ELIGIBLE.value,
                progress_status=ProjectProgressStatus.PLANNING.value
            )
            self.db.add(failed_enrollment)
        
        # Update priorities for existing enrollments that should be ranked lower
        for existing in all_enrollments:
            existing_attempt = self.db.query(QuizAttempt).filter(
                QuizAttempt.id == existing.quiz_attempt_id
            ).first()
            
            if existing_attempt and (
                existing_attempt.score < attempt.score or 
                (existing_attempt.score == attempt.score and existing_attempt.completed_at > attempt.completed_at)
            ):
                existing.enrollment_priority += 1
        
        self.db.commit()

    def get_user_quiz_attempts(self, user_id: int) -> List[QuizAttempt]:
        """Get all quiz attempts for a user"""
        return self.db.query(QuizAttempt).filter(
            QuizAttempt.user_id == user_id
        ).order_by(desc(QuizAttempt.started_at)).all()

    def get_user_quiz_statistics(self, user_id: int) -> UserQuizStatistics:
        """Get quiz statistics for a user"""
        attempts = self.db.query(QuizAttempt).filter(
            QuizAttempt.user_id == user_id
        ).all()
        
        total_attempts = len(attempts)
        completed_attempts = len([a for a in attempts if a.completed_at is not None])
        passed_attempts = len([a for a in attempts if a.passed])
        total_xp = sum([a.xp_awarded for a in attempts])
        
        scores = [a.score for a in attempts if a.score is not None]
        average_score = sum(scores) / len(scores) if scores else None
        
        completion_rate = (completed_attempts / total_attempts) * 100 if total_attempts > 0 else 0
        pass_rate = (passed_attempts / completed_attempts) * 100 if completed_attempts > 0 else 0
        
        # Find favorite category (most completed quizzes)
        category_counts = {}
        for attempt in attempts:
            if attempt.completed_at:
                quiz = self.get_quiz_by_id(attempt.quiz_id)
                if quiz:
                    category_counts[quiz.category] = category_counts.get(quiz.category, 0) + 1
        
        favorite_category = max(category_counts.items(), key=lambda x: x[1])[0] if category_counts else None
        
        return UserQuizStatistics(
            user_id=user_id,
            total_quizzes_attempted=total_attempts,
            total_quizzes_completed=completed_attempts,
            total_quizzes_passed=passed_attempts,
            total_xp_earned=total_xp,
            average_score=average_score,
            completion_rate=completion_rate,
            pass_rate=pass_rate,
            favorite_category=favorite_category
        )

    def get_quiz_statistics(self, quiz_id: int) -> QuizStatistics:
        """Get statistics for a specific quiz"""
        attempts = self.db.query(QuizAttempt).filter(
            QuizAttempt.quiz_id == quiz_id
        ).all()
        
        quiz = self.get_quiz_by_id(quiz_id)
        total_attempts = len(attempts)
        completed_attempts = len([a for a in attempts if a.completed_at is not None])
        passed_attempts = len([a for a in attempts if a.passed])
        
        scores = [a.score for a in attempts if a.score is not None]
        times = [a.time_spent_minutes for a in attempts if a.time_spent_minutes is not None]
        
        average_score = sum(scores) / len(scores) if scores else None
        average_time = sum(times) / len(times) if times else None
        pass_rate = (passed_attempts / completed_attempts) * 100 if completed_attempts > 0 else 0
        
        return QuizStatistics(
            quiz_id=quiz_id,
            quiz_title=quiz.title,
            total_attempts=total_attempts,
            completed_attempts=completed_attempts,
            average_score=average_score,
            pass_rate=pass_rate,
            average_time_minutes=average_time
        )

    def get_user_ongoing_attempt(self, user_id: int, quiz_id: int) -> Optional[QuizAttempt]:
        """Get user's ongoing (not completed) attempt for a quiz"""
        return self.db.query(QuizAttempt).filter(
            and_(
                QuizAttempt.user_id == user_id,
                QuizAttempt.quiz_id == quiz_id,
                QuizAttempt.completed_at.is_(None)
            )
        ).first()

    def is_quiz_completed_by_user(self, user_id: int, quiz_id: int) -> bool:
        """Check if user has completed a specific quiz"""
        completed_attempt = self.db.query(QuizAttempt).filter(
            and_(
                QuizAttempt.user_id == user_id,
                QuizAttempt.quiz_id == quiz_id,
                QuizAttempt.completed_at.isnot(None)
            )
        ).first()
        
        return completed_attempt is not None

    def get_quiz_leaderboard(self, quiz_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top scores for a quiz"""
        results = self.db.query(
            QuizAttempt.user_id,
            QuizAttempt.score,
            QuizAttempt.time_spent_minutes,
            User.full_name
        ).join(User).filter(
            and_(
                QuizAttempt.quiz_id == quiz_id,
                QuizAttempt.completed_at.isnot(None),
                QuizAttempt.passed == True
            )
        ).order_by(desc(QuizAttempt.score), QuizAttempt.time_spent_minutes).limit(limit).all()
        
        return [
            {
                "user_id": result.user_id,
                "user_name": result.full_name,
                "score": result.score,
                "time_spent_minutes": result.time_spent_minutes
            }
            for result in results
        ]
