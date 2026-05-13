from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
import random
import math

from ..models.quiz import (
    Quiz, QuizQuestion, UserQuestionPerformance, AdaptiveLearningSession,
    QuestionMetadata, QuizCategory
)


_SESSION_DUE_CAP = 3


class AdaptiveLearningService:
    """Adaptív tanulási algoritmusok és logika"""

    def __init__(self, db: Session):
        self.db = db
    def start_adaptive_session(
        self,
        user_id: int,
        category: QuizCategory,
        session_duration_seconds: int = 180,
        language: str = "en",
        module_prefix: str | None = None,
    ) -> AdaptiveLearningSession:
        """Új adaptív tanulási session indítása időkorláttal"""
        session = AdaptiveLearningSession(
            user_id=user_id,
            category=category,
            language=language,
            module_prefix=module_prefix,
            target_difficulty=self._calculate_target_difficulty(user_id, category, language=language),
            performance_trend=0.0,
            session_time_limit_seconds=session_duration_seconds,
            session_start_time=datetime.now(timezone.utc)
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session
    
    def get_next_question(
        self, user_id: int, session_id: int, exclude_ids: set[int] | None = None
    ) -> Optional[Dict]:
        """Következő kérdés kiválasztása adaptív algoritmussal és időkorlát ellenőrzés"""
        session = self.db.query(AdaptiveLearningSession).filter(
            AdaptiveLearningSession.id == session_id
        ).first()
        if not session:
            return None
        # Check if session time limit has expired
        if self._is_session_time_expired(session):
            return {"session_complete": True, "reason": "time_expired"}

        # Get user's performance data (language-scoped)
        performance_data = self._get_user_performance_data(
            user_id, session.category, language=session.language
        )

        # Select question based on adaptive algorithm
        candidate_questions = self._get_candidate_questions(
            session.category, session.target_difficulty,
            language=session.language, module_prefix=session.module_prefix,
        )

        if not candidate_questions:
            return {"session_complete": True, "reason": "pool_exhausted"}

        # Apply weighted selection (recency penalty replaces hard exclude for small pools)
        selected_question = self._select_weighted_question(
            candidate_questions, performance_data, session, exclude_ids=exclude_ids
        )

        if not selected_question:
            return {"session_complete": True, "reason": "pool_exhausted"}

        # Increment session_due_shown when a due question is served
        due_ids = {p.question_id for p in performance_data["due_for_review"]}
        was_due = selected_question.id in due_ids
        if was_due:
            session.session_due_shown = (session.session_due_shown or 0) + 1
            self.db.commit()

        # Return question with session info
        return {
            "id": selected_question.id,
            "text": selected_question.question_text,
            "options": [{"id": opt.id, "text": opt.option_text} for opt in selected_question.answer_options],
            "type": selected_question.question_type.value if selected_question.question_type else "multiple_choice",
            "difficulty": self._get_question_difficulty(selected_question.id),
            "session_time_remaining": self._get_session_time_remaining(session),
            "was_due": was_due,
        }
    
    def record_answer(self, user_id: int, session_id: int, question_id: int, 
                     is_correct: bool, time_spent_seconds: float) -> Dict:
        """Válasz rögzítése és adaptív súlyok frissítése"""
        
        # Update session
        session = self.db.query(AdaptiveLearningSession).filter(
            AdaptiveLearningSession.id == session_id
        ).first()
        
        if session:
            session.questions_presented += 1
            if is_correct:
                session.questions_correct += 1
                
            # Update performance trend
            session.performance_trend = self._calculate_performance_trend(session)
            
            # Adjust target difficulty
            session.target_difficulty = self._adjust_target_difficulty(
                session.target_difficulty, 
                is_correct, 
                session.performance_trend
            )
        
        # Update user question performance
        self._update_user_question_performance(user_id, question_id, is_correct, time_spent_seconds)
        
        # Update question metadata
        self._update_question_metadata(question_id, is_correct, time_spent_seconds)
        
        self.db.commit()

        score_delta = 1 if is_correct else -1
        if session:
            score = (session.questions_correct or 0) * 2 - (session.questions_presented or 0)
        else:
            score = 0

        return {
            "score_delta": score_delta,
            "score": score,
            "new_target_difficulty": session.target_difficulty if session else None,
            "performance_trend": session.performance_trend if session else None,
            "mastery_update": self._get_mastery_update(user_id, question_id)
        }
    
    def end_session(self, session_id: int) -> Dict:
        """Session befejezése és eredmények összegzése"""
        session = self.db.query(AdaptiveLearningSession).filter(
            AdaptiveLearningSession.id == session_id
        ).first()
        
        if not session:
            return {}
            
        session.ended_at = datetime.now(timezone.utc)

        success_rate = (session.questions_correct / session.questions_presented) if session.questions_presented > 0 else 0
        score = (session.questions_correct or 0) * 2 - (session.questions_presented or 0)
        xp = self._calculate_session_xp(score)
        session.xp_earned = xp

        self.db.commit()

        return {
            "questions_answered": session.questions_presented,
            "correct_answers": session.questions_correct,
            "success_rate": success_rate,
            "xp_earned": xp,
            "score": score,
            "performance_trend": session.performance_trend,
            "final_difficulty": session.target_difficulty
        }
    
    def get_user_learning_analytics(
        self, user_id: int, category: QuizCategory = None, language: str | None = None
    ) -> Dict:
        """Felhasználói tanulási analitika"""

        # Get overall performance
        query = self.db.query(UserQuestionPerformance).filter(
            UserQuestionPerformance.user_id == user_id
        )

        if category or language:
            query = query.join(QuizQuestion).join(Quiz)
            if category:
                query = query.filter(Quiz.category == category)
            if language:
                query = query.filter(Quiz.language == language)

        performances = query.all()
        
        if not performances:
            return {
                "total_questions_attempted": 0,
                "total_attempts": 0,
                "overall_success_rate": 0.0,
                "mastery_level": 0.0,
                "learning_velocity": 0.0,
                "recommended_difficulty": 0.5
            }
        
        # Calculate statistics
        total_attempts = sum(p.total_attempts for p in performances)
        total_correct = sum(p.correct_attempts for p in performances)
        overall_success_rate = total_correct / total_attempts if total_attempts > 0 else 0.0
        
        average_mastery = sum(p.mastery_level for p in performances) / len(performances)
        
        # Calculate learning velocity (improvement over time)
        recent_performances = [p for p in performances if p.last_attempted_at and 
                             p.last_attempted_at > datetime.now(timezone.utc) - timedelta(days=7)]
        
        learning_velocity = 0.0
        if len(recent_performances) > 0:
            recent_success_rate = sum(p.success_rate for p in recent_performances) / len(recent_performances)
            learning_velocity = recent_success_rate - overall_success_rate
        
        return {
            "total_questions_attempted": len(performances),
            "total_attempts": total_attempts,
            "overall_success_rate": overall_success_rate,
            "mastery_level": average_mastery,
            "learning_velocity": learning_velocity,
            "recommended_difficulty": 0.5  # Default difficulty, will be calculated separately
        }
    
    # Private helper methods
    
    def _calculate_target_difficulty(
        self, user_id: int, category: QuizCategory, language: str = "en"
    ) -> float:
        """Célnehézség számítása felhasználói teljesítmény alapján"""
        analytics = self.get_user_learning_analytics(user_id, category, language=language)
        
        base_difficulty = 0.5  # Default medium difficulty
        
        # Adjust based on success rate
        if analytics["overall_success_rate"] > 0.8:
            base_difficulty += 0.2  # Increase difficulty for high performers
        elif analytics["overall_success_rate"] < 0.6:
            base_difficulty -= 0.2  # Decrease difficulty for struggling learners
            
        # Adjust based on learning velocity
        base_difficulty += analytics["learning_velocity"] * 0.1
        
        # Clamp between 0.1 and 0.9
        return max(0.1, min(0.9, base_difficulty))
    
    def _get_user_performance_data(
        self, user_id: int, category: QuizCategory, language: str = "en"
    ) -> Dict:
        """Felhasználói teljesítményadatok összegyűjtése"""
        performances = self.db.query(UserQuestionPerformance).join(QuizQuestion).join(Quiz).filter(
            and_(
                UserQuestionPerformance.user_id == user_id,
                Quiz.category == category,
                Quiz.language == language,
            )
        ).all()

        now = datetime.now(timezone.utc)
        return {
            "all_performances": performances,
            "weak_concepts": [p for p in performances if p.mastery_level < 0.6],
            "strong_concepts": [p for p in performances if p.mastery_level > 0.8],
            "due_for_review": [p for p in performances if p.next_review_at and
                               p.next_review_at <= now],
        }
    
    def _get_candidate_questions(
        self,
        category: QuizCategory,
        target_difficulty: float,
        language: str = "en",
        module_prefix: str | None = None,
    ) -> List[QuizQuestion]:
        """Jelölt kérdések kiválasztása kategória, nehézség, nyelv és (opcionálisan) modul alapján."""
        difficulty_range = 0.2

        base_filters = [
            Quiz.category == category,
            Quiz.language == language,
            Quiz.is_active == True,
        ]
        if module_prefix:
            base_filters.append(Quiz.title.like(f"{module_prefix} -%"))

        # Try difficulty-filtered query (LEFT JOIN — metadata optional)
        questions = (
            self.db.query(QuizQuestion)
            .join(Quiz)
            .outerjoin(QuestionMetadata, QuestionMetadata.question_id == QuizQuestion.id)
            .filter(
                *base_filters,
                and_(
                    QuestionMetadata.estimated_difficulty >= target_difficulty - difficulty_range,
                    QuestionMetadata.estimated_difficulty <= target_difficulty + difficulty_range,
                ),
            )
            .all()
        )

        # Fall back to all questions matching base filters (no metadata required)
        if not questions:
            questions = (
                self.db.query(QuizQuestion)
                .join(Quiz)
                .filter(*base_filters)
                .all()
            )

        return questions
    
    def _calculate_question_weight(
        self,
        q_id: int,
        due_ids: set,
        perf_map: dict,
        session_due_shown: int,
        exclude_ids: set,
    ) -> float:
        perf = perf_map.get(q_id)
        mastery = perf.mastery_level if perf else None
        dw = perf.difficulty_weight if perf else 1.5
        in_due = q_id in due_ids and session_due_shown < _SESSION_DUE_CAP

        if in_due:
            w = 2.5
        elif q_id in due_ids:
            w = min(dw, 1.8)
        elif mastery is not None and mastery < 0.6:
            w = min(dw, 1.8)
        elif mastery is None:
            w = 1.2
        else:
            w = 1.0

        if q_id in exclude_ids:
            w *= 0.1

        return max(0.05, w)

    def _select_weighted_question(
        self,
        candidates: List[QuizQuestion],
        performance_data: Dict,
        session: AdaptiveLearningSession,
        exclude_ids: set | None = None,
    ) -> Optional[QuizQuestion]:
        """Súlyozott véletlenszerű kérdésválasztó — session cap és recency penalty."""
        if not candidates:
            return None
        due_ids = {p.question_id for p in performance_data["due_for_review"]}
        perf_map = {p.question_id: p for p in performance_data["all_performances"]}
        session_due_shown = session.session_due_shown or 0
        exc = exclude_ids or set()
        weights = [
            self._calculate_question_weight(q.id, due_ids, perf_map, session_due_shown, exc)
            for q in candidates
        ]
        return random.choices(candidates, weights=weights, k=1)[0]
    
    def _calculate_performance_trend(self, session: AdaptiveLearningSession) -> float:
        """Teljesítménytrend számítása"""
        if session.questions_presented < 3:
            return session.performance_trend
            
        recent_success_rate = session.questions_correct / session.questions_presented
        
        # Simple trend calculation: positive if doing well, negative if struggling
        if recent_success_rate > 0.7:
            return min(1.0, session.performance_trend + 0.1)
        elif recent_success_rate < 0.5:
            return max(-1.0, session.performance_trend - 0.1)
        else:
            return session.performance_trend * 0.9  # Decay towards neutral
    
    def _adjust_target_difficulty(self, current_difficulty: float, is_correct: bool, trend: float) -> float:
        """Célnehézség dinamikus állítása"""
        adjustment = 0.05
        
        if is_correct and trend > 0.5:
            # Performing well, increase difficulty
            return min(0.9, current_difficulty + adjustment)
        elif not is_correct and trend < -0.5:
            # Struggling, decrease difficulty
            return max(0.1, current_difficulty - adjustment)
        else:
            # Small adjustments
            return current_difficulty + (adjustment if is_correct else -adjustment) * 0.5
    
    def _update_user_question_performance(self, user_id: int, question_id: int, 
                                        is_correct: bool, time_spent: float):
        """Felhasználói kérdésteljesítmény frissítése"""
        performance = self.db.query(UserQuestionPerformance).filter(
            and_(
                UserQuestionPerformance.user_id == user_id,
                UserQuestionPerformance.question_id == question_id
            )
        ).first()
        
        if not performance:
            performance = UserQuestionPerformance(
                user_id=user_id,
                question_id=question_id,
                total_attempts=0,
                correct_attempts=0,
                mastery_level=0.0,
                difficulty_weight=1.0
            )
            self.db.add(performance)
        
        performance.total_attempts = (performance.total_attempts or 0) + 1
        if is_correct:
            performance.correct_attempts = (performance.correct_attempts or 0) + 1
            
        performance.last_attempt_correct = is_correct
        performance.last_attempted_at = datetime.now(timezone.utc)
        
        # Update mastery level using exponential moving average
        new_mastery = 1.0 if is_correct else 0.0
        performance.mastery_level = (performance.mastery_level or 0.0) * 0.8 + new_mastery * 0.2
        
        # Schedule next review using spaced repetition
        if is_correct:
            # Longer intervals for correct answers
            interval_days = min(30, math.pow(2, performance.mastery_level * 5))
        else:
            # Shorter intervals for incorrect answers
            interval_days = max(1, 3 * performance.mastery_level)
            
        performance.next_review_at = datetime.now(timezone.utc) + timedelta(days=interval_days)
        
        # Update difficulty weight
        performance.difficulty_weight = max(0.5, 2.0 - performance.mastery_level)
    
    def _update_question_metadata(self, question_id: int, is_correct: bool, time_spent: float):
        """Kérdés metaadatok frissítése globális statisztikákkal"""
        metadata = self.db.query(QuestionMetadata).filter(
            QuestionMetadata.question_id == question_id
        ).first()
        
        if not metadata:
            metadata = QuestionMetadata(question_id=question_id)
            self.db.add(metadata)
        
        # Update global success rate (simple moving average)
        current_rate = metadata.global_success_rate or 0.5
        new_rate = 1.0 if is_correct else 0.0
        metadata.global_success_rate = current_rate * 0.95 + new_rate * 0.05
        
        # Update average time
        current_time = metadata.average_time_seconds or 60.0
        metadata.average_time_seconds = current_time * 0.95 + time_spent * 0.05
        
        # Adjust difficulty estimate based on performance
        if metadata.global_success_rate > 0.8:
            metadata.estimated_difficulty = max(0.1, metadata.estimated_difficulty - 0.01)
        elif metadata.global_success_rate < 0.4:
            metadata.estimated_difficulty = min(0.9, metadata.estimated_difficulty + 0.01)
            
        metadata.last_analytics_update = datetime.now(timezone.utc)
    
    def _calculate_session_xp(self, score: int) -> int:
        XP_PER_POINT = 10
        return max(0, score) * XP_PER_POINT

    def _calculate_adaptive_xp(self, question_id: int, is_correct: bool, time_spent: float) -> int:
        """Adaptív XP számítása"""
        if not is_correct:
            return 5  # Consolation XP for attempt
            
        # Base XP
        base_xp = 25
        
        # Difficulty bonus
        metadata = self.db.query(QuestionMetadata).filter(
            QuestionMetadata.question_id == question_id
        ).first()
        
        if metadata:
            difficulty_bonus = int(metadata.estimated_difficulty * 20)  # 0-20 bonus
            base_xp += difficulty_bonus
        
        # Time bonus (faster = more XP, up to 50% bonus)
        if metadata and metadata.average_time_seconds:
            time_ratio = metadata.average_time_seconds / time_spent
            time_bonus = min(0.5, max(0, (time_ratio - 1) * 0.25))
            base_xp = int(base_xp * (1 + time_bonus))
        
        return base_xp
    
    def _get_mastery_update(self, user_id: int, question_id: int) -> Dict:
        """Aktuális mastery szint lekérdezése"""
        performance = self.db.query(UserQuestionPerformance).filter(
            and_(
                UserQuestionPerformance.user_id == user_id,
                UserQuestionPerformance.question_id == question_id
            )
        ).first()
        
        if performance:
            return {
                "mastery_level": performance.mastery_level,
                "success_rate": performance.success_rate,
                "next_review": performance.next_review_at.isoformat() if performance.next_review_at else None
            }
        
        return {"mastery_level": 0.0, "success_rate": 0.0, "next_review": None}
    
    def _is_session_time_expired(self, session: AdaptiveLearningSession) -> bool:
        """Ellenőrzi, hogy lejárt-e a session időkorlátja"""
        if not session.session_start_time or not session.session_time_limit_seconds:
            return False
            
        elapsed_seconds = (datetime.now(timezone.utc) - session.session_start_time).total_seconds()
        return elapsed_seconds >= session.session_time_limit_seconds
    
    def _get_session_time_remaining(self, session: AdaptiveLearningSession) -> int:
        """Visszaadja a session fennmaradó idejét másodpercben"""
        if not session.session_start_time or not session.session_time_limit_seconds:
            return 0
            
        elapsed_seconds = (datetime.now(timezone.utc) - session.session_start_time).total_seconds()
        remaining = session.session_time_limit_seconds - elapsed_seconds
        return max(0, int(remaining))
    
    def _get_question_difficulty(self, question_id: int) -> float:
        """Kérdés nehézségi szintjének lekérdezése"""
        metadata = self.db.query(QuestionMetadata).filter(
            QuestionMetadata.question_id == question_id
        ).first()
        
        return metadata.estimated_difficulty if metadata else 0.5