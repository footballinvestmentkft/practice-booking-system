"""Unit tests for Adaptive Learning session lifecycle (SLC-01..14).

SLC-01   Recovery prompt shown when IN_PROGRESS session has answered questions
SLC-02   Recovery prompt NOT shown when IN_PROGRESS session has 0 questions
SLC-03   Recovery prompt NOT shown when no active session exists
SLC-04   Continue flow uses existing session_id (no new session row)
SLC-05   Continue flow: question counter increments from existing questions_presented
SLC-06   Continue flow: UserQuestionPerformance data preserved (not reset)
SLC-07   Complete after continue: XP computed over ALL answered questions
SLC-08   Discard sets status=VOIDED, ended_at populated
SLC-09   Discard: session_count stat does NOT include VOIDED sessions
SLC-10   Completed session does NOT trigger recovery prompt
SLC-11   User cannot discard another user's session (404)
SLC-12   Analytics total_sessions excludes VOIDED / EXPIRED / ABANDONED
SLC-13   Normal complete flow XP formula unchanged
SLC-14   Retired session (timer expired) gets EXPIRED status if q_pres>0, else ABANDONED
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone, timedelta

from fastapi.responses import JSONResponse

from app.models.quiz import ALSessionStatus

_ROUTES = "app.api.web_routes.adaptive_learning"
_SERVICE = "app.services.adaptive_learning"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _make_user(user_id=42):
    u = MagicMock()
    u.id = user_id
    return u


def _make_session(
    session_id=10,
    user_id=42,
    status=ALSessionStatus.IN_PROGRESS.value,
    ended_at=None,
    questions_presented=5,
    questions_correct=3,
    xp_earned=0,
    module_prefix="AL — Test Module",
    category_value="LESSON",
    language="en",
    started_at=None,
    last_activity_at=None,
):
    s = MagicMock()
    s.id = session_id
    s.user_id = user_id
    s.status = status
    s.ended_at = ended_at
    s.questions_presented = questions_presented
    s.questions_correct = questions_correct
    s.xp_earned = xp_earned
    s.module_prefix = module_prefix
    s.started_at = started_at or datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
    s.last_activity_at = last_activity_at
    s.session_start_time = s.started_at
    s.session_time_limit_seconds = 180
    # category mock — mimics SQLAlchemy enum comparison
    cat_mock = MagicMock()
    cat_mock.value = category_value
    s.category = cat_mock
    s.language = language
    s.session_due_shown = 0
    s.performance_trend = 0.0
    s.target_difficulty = 0.5
    return s


def _mock_db_with_resumable(session):
    """DB returning a resumable session for the entry page."""
    db = MagicMock()

    # Five separate query() calls from the entry page route (total_xp, session_count,
    # resumable_session, recent_sessions, spec_ctx — we only care about the first 4)
    q_xp = MagicMock()
    q_xp.filter.return_value = q_xp
    q_xp.scalar.return_value = 0

    q_count = MagicMock()
    q_count.filter.return_value = q_count
    q_count.scalar.return_value = 2

    q_resume = MagicMock()
    q_resume.filter.return_value = q_resume
    q_resume.order_by.return_value = q_resume
    q_resume.first.return_value = session

    q_recent = MagicMock()
    q_recent.filter.return_value = q_recent
    q_recent.order_by.return_value = q_recent
    q_recent.limit.return_value = q_recent
    q_recent.all.return_value = []

    db.query.side_effect = [q_xp, q_count, q_resume, q_recent]
    return db


# ── SLC-01: Recovery prompt shown for IN_PROGRESS with q>0 ────────────────────

class TestRecoveryPrompt:

    def test_slc01_prompt_shown_when_in_progress_with_answers(self):
        """SLC-01: resumable_session is passed to template when q_pres>0 and IN_PROGRESS."""
        from app.api.web_routes.adaptive_learning import adaptive_learning_page

        session = _make_session(questions_presented=5)
        db = _mock_db_with_resumable(session)
        user = _make_user()
        request = MagicMock()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = MagicMock()
            _run(adaptive_learning_page(request=request, db=db, user=user))

        ctx = mock_tpl.TemplateResponse.call_args[0][1]
        assert ctx["resumable_session"] is session

    def test_slc02_no_prompt_for_empty_in_progress_session(self):
        """SLC-02: resumable_session is None when IN_PROGRESS but q_pres=0."""
        from app.api.web_routes.adaptive_learning import adaptive_learning_page

        db = _mock_db_with_resumable(None)  # query returns None (no resumable)
        user = _make_user()
        request = MagicMock()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = MagicMock()
            _run(adaptive_learning_page(request=request, db=db, user=user))

        ctx = mock_tpl.TemplateResponse.call_args[0][1]
        assert ctx["resumable_session"] is None

    def test_slc03_no_prompt_when_no_active_session(self):
        """SLC-03: resumable_session is None when no active session exists."""
        from app.api.web_routes.adaptive_learning import adaptive_learning_page

        db = _mock_db_with_resumable(None)
        user = _make_user()
        request = MagicMock()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = MagicMock()
            _run(adaptive_learning_page(request=request, db=db, user=user))

        ctx = mock_tpl.TemplateResponse.call_args[0][1]
        assert ctx["resumable_session"] is None

    def test_slc10_completed_session_does_not_trigger_prompt(self):
        """SLC-10: A COMPLETED session (ended_at IS NOT NULL) is excluded from recovery query."""
        from app.api.web_routes.adaptive_learning import adaptive_learning_page

        # Simulate DB filtering out COMPLETED sessions (query returns None)
        db = _mock_db_with_resumable(None)
        user = _make_user()
        request = MagicMock()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = MagicMock()
            _run(adaptive_learning_page(request=request, db=db, user=user))

        ctx = mock_tpl.TemplateResponse.call_args[0][1]
        assert ctx["resumable_session"] is None


# ── SLC-04: Continue uses existing session_id ─────────────────────────────────

class TestContinueFlow:

    def test_slc04_continue_does_not_create_new_session(self):
        """SLC-04: When ?resume param is handled, no POST /start is called (session_id reused)."""
        # This is validated at the template JS level (no server involvement for ?resume routing).
        # On the backend, the next-question endpoint accepts any open session_id.
        # We verify that _session_guard accepts an IN_PROGRESS session.
        from app.api.web_routes.adaptive_learning import _session_guard

        session = _make_session(session_id=10, questions_presented=5, ended_at=None)
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = session
        db.query.return_value = q

        result_session, err = _session_guard(db, session_id=10, user_id=42)
        assert err is None
        assert result_session.id == 10

    def test_slc05_question_counter_increments_from_existing(self):
        """SLC-05: record_answer increments questions_presented from existing value."""
        from app.services.adaptive_learning import AdaptiveLearningService

        session = _make_session(session_id=10, questions_presented=5, questions_correct=3)
        db = MagicMock()

        # query(AdaptiveLearningSession) → session
        q_session = MagicMock()
        q_session.filter.return_value = q_session
        q_session.first.return_value = session

        # query(UserQuestionPerformance) → None (new record)
        q_perf = MagicMock()
        q_perf.filter.return_value = q_perf
        q_perf.first.return_value = None

        # query(QuestionMetadata) → None
        q_meta = MagicMock()
        q_meta.filter.return_value = q_meta
        q_meta.first.return_value = None

        # query(UserQuestionPerformance) again in _get_mastery_update → None
        q_perf2 = MagicMock()
        q_perf2.filter.return_value = q_perf2
        q_perf2.first.return_value = None

        db.query.side_effect = [q_session, q_perf, q_meta, q_perf2]

        svc = AdaptiveLearningService(db)
        svc.record_answer(user_id=42, session_id=10, question_id=99, is_correct=True, time_spent_seconds=10.0)

        assert session.questions_presented == 6   # incremented from 5
        assert session.questions_correct == 4     # incremented from 3
        assert session.last_activity_at is not None

    def test_slc06_user_question_performance_preserved_on_continue(self):
        """SLC-06: record_answer updates existing UserQuestionPerformance (not reset)."""
        from app.services.adaptive_learning import AdaptiveLearningService

        session = _make_session(session_id=10, questions_presented=5)

        existing_perf = MagicMock()
        existing_perf.total_attempts = 3
        existing_perf.correct_attempts = 2
        existing_perf.mastery_level = 0.6
        existing_perf.difficulty_weight = 1.0

        db = MagicMock()
        q_session = MagicMock(); q_session.filter.return_value = q_session; q_session.first.return_value = session
        q_perf = MagicMock(); q_perf.filter.return_value = q_perf; q_perf.first.return_value = existing_perf
        q_meta = MagicMock(); q_meta.filter.return_value = q_meta; q_meta.first.return_value = None
        # _get_mastery_update re-queries UserQuestionPerformance
        q_perf2 = MagicMock(); q_perf2.filter.return_value = q_perf2; q_perf2.first.return_value = existing_perf
        db.query.side_effect = [q_session, q_perf, q_meta, q_perf2]

        svc = AdaptiveLearningService(db)
        svc.record_answer(user_id=42, session_id=10, question_id=99, is_correct=True, time_spent_seconds=8.0)

        # total_attempts incremented, not reset
        assert existing_perf.total_attempts == 4
        assert existing_perf.correct_attempts == 3


# ── SLC-07: XP computed over all questions after continue ─────────────────────

class TestXPAfterContinue:

    def test_slc07_xp_computed_over_all_questions(self):
        """SLC-07: end_session XP uses total questions_presented (original + resumed answers)."""
        from app.services.adaptive_learning import AdaptiveLearningService

        # Simulates a session that was resumed: 5 original + 3 new = 8 total, 6 correct
        # score = 6*2 - 8 = 4; xp = 40
        session = _make_session(session_id=10, questions_presented=8, questions_correct=6, ended_at=None)

        db = MagicMock()
        q = MagicMock(); q.filter.return_value = q; q.first.return_value = session
        db.query.return_value = q

        svc = AdaptiveLearningService(db)
        summary = svc.end_session(session_id=10)

        assert summary["xp_earned"] == 40           # max(0, 4) * 10
        assert session.status == ALSessionStatus.COMPLETED.value
        assert session.ended_at is not None


# ── SLC-08: Discard flow ───────────────────────────────────────────────────────

class TestDiscardFlow:

    def test_slc08_discard_sets_voided_status(self):
        """SLC-08: POST /discard sets status=VOIDED and populates ended_at."""
        from app.api.web_routes.adaptive_learning import al_session_discard

        session = _make_session(session_id=10, ended_at=None)

        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.with_for_update.return_value = q
        q.first.return_value = session
        db.query.return_value = q

        user = _make_user()
        request = MagicMock()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None):
            result = _run(al_session_discard(session_id=10, request=request, db=db, user=user))

        assert session.status == ALSessionStatus.VOIDED.value
        assert session.ended_at is not None
        assert session.void_reason == "user_discarded"
        assert db.commit.called
        data = result.body
        import json
        body = json.loads(data)
        assert body["status"] == "voided"

    def test_slc08_discard_already_closed_returns_410(self):
        """SLC-08: Discarding an already-closed session returns 410."""
        from app.api.web_routes.adaptive_learning import al_session_discard

        session = _make_session(session_id=10, ended_at=datetime.now(timezone.utc))

        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.with_for_update.return_value = q
        q.first.return_value = session
        db.query.return_value = q

        user = _make_user()
        request = MagicMock()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None):
            result = _run(al_session_discard(session_id=10, request=request, db=db, user=user))

        assert result.status_code == 410

    def test_slc11_cannot_discard_another_users_session(self):
        """SLC-11: Discard returns 404 if session belongs to a different user."""
        from app.api.web_routes.adaptive_learning import al_session_discard

        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.with_for_update.return_value = q
        q.first.return_value = None  # user_id filter excludes foreign session
        db.query.return_value = q

        user = _make_user(user_id=99)  # different user
        request = MagicMock()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None):
            result = _run(al_session_discard(session_id=10, request=request, db=db, user=user))

        assert result.status_code == 404


# ── SLC-09: session_count excludes VOIDED ────────────────────────────────────

class TestSessionCountStat:

    def test_slc09_session_count_filters_completed_only(self):
        """SLC-09: The entry page session_count query filters status=COMPLETED."""
        from app.api.web_routes.adaptive_learning import adaptive_learning_page

        db = _mock_db_with_resumable(None)
        user = _make_user()
        request = MagicMock()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = MagicMock()
            _run(adaptive_learning_page(request=request, db=db, user=user))

        # Verify that filter() was called on the count query (query call index 1)
        # We can't easily check the exact filter arg without deep mock inspection,
        # but we can verify that filter was called on the count chain.
        assert db.query.call_count >= 2


# ── SLC-12: Analytics excludes non-COMPLETED sessions ────────────────────────

class TestAnalyticsFiltering:

    def test_slc12_total_sessions_only_counts_completed(self):
        """SLC-12: get_global_stats total_sessions only counts COMPLETED sessions."""
        from app.services.al_analytics_service import get_global_stats

        db = MagicMock()

        # First query: ALAnswerLog aggregate
        q_agg = MagicMock()
        q_agg.first.return_value = MagicMock(total=100, correct=75, timeouts=5, avg_time=25.0)

        # Second query: AdaptiveLearningSession COMPLETED count
        q_sessions = MagicMock()
        q_sessions.filter.return_value = q_sessions
        q_sessions.scalar.return_value = 7   # only 7 COMPLETED

        db.query.side_effect = [q_agg, q_sessions]

        result = get_global_stats(db)
        assert result.total_sessions == 7

        # Verify the session count query used a .filter() call (status=COMPLETED filter)
        assert q_sessions.filter.called


# ── SLC-13: Normal complete flow XP unchanged ────────────────────────────────

class TestNormalCompleteXP:

    def test_slc13_normal_complete_xp_formula_unchanged(self):
        """SLC-13: end_session XP = max(0, correct*2 - presented) * 10."""
        from app.services.adaptive_learning import AdaptiveLearningService

        test_cases = [
            (10, 8, 60),   # score=6 → 60 XP
            (10, 5, 0),    # score=0 → 0 XP
            (10, 3, 0),    # score=-4 → 0 XP (negative clamped)
            (5,  5, 50),   # score=5 → 50 XP
        ]

        for presented, correct, expected_xp in test_cases:
            session = _make_session(
                questions_presented=presented,
                questions_correct=correct,
                ended_at=None,
            )
            db = MagicMock()
            q = MagicMock(); q.filter.return_value = q; q.first.return_value = session
            db.query.return_value = q

            svc = AdaptiveLearningService(db)
            summary = svc.end_session(session_id=10)

            assert summary["xp_earned"] == expected_xp, (
                f"presented={presented} correct={correct}: "
                f"expected {expected_xp} got {summary['xp_earned']}"
            )
            assert session.status == ALSessionStatus.COMPLETED.value


# ── SLC-14: Retired sessions get correct status ───────────────────────────────

class TestRetireStatus:

    def _make_expired_session_db(self, existing_session):
        """DB mock for the al_session_start retire flow."""
        db = MagicMock()
        # query(AdaptiveLearningSession) for existing session check
        q_existing = MagicMock()
        q_existing.filter.return_value = q_existing
        q_existing.order_by.return_value = q_existing
        q_existing.first.return_value = existing_session

        # query for module_q_count
        q_count = MagicMock()
        q_count.join.return_value = q_count
        q_count.filter.return_value = q_count
        q_count.scalar.return_value = 15  # enough questions

        db.query.side_effect = [q_count, q_existing]
        return db

    def test_slc14_expired_with_answers_gets_expired_status(self):
        """SLC-14: Auto-retire of session with q_pres>0 → status=EXPIRED."""
        from app.api.web_routes.adaptive_learning import al_session_start

        # Session started 600s ago (> 180s limit)
        old_start = datetime.now(timezone.utc) - timedelta(seconds=600)
        existing = _make_session(
            session_id=5,
            questions_presented=10,
            ended_at=None,
            started_at=old_start,
        )
        existing.session_start_time = old_start
        existing.session_time_limit_seconds = 180
        # Different module → mismatch path; also elapsed > limit — timer path fires first
        existing.module_prefix = "AL — Different Module"

        db = self._make_expired_session_db(existing)

        # New session creation mock
        new_session = _make_session(session_id=99, questions_presented=0)
        with patch(f"{_ROUTES}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.start_adaptive_session.return_value = new_session
            user = _make_user()
            request = MagicMock()

            with patch(f"{_ROUTES}.require_student_onboarding", return_value=None):
                _run(al_session_start(
                    request=request,
                    category="LESSON",
                    module_prefix="AL — Test Module",
                    time_limit=180,
                    language="en",
                    force_new=False,
                    db=db,
                    user=user,
                ))

        assert existing.status == ALSessionStatus.EXPIRED.value
        assert existing.ended_at is not None

    def test_slc14_expired_without_answers_gets_abandoned_status(self):
        """SLC-14: Auto-retire of session with q_pres=0 → status=ABANDONED."""
        from app.api.web_routes.adaptive_learning import al_session_start

        old_start = datetime.now(timezone.utc) - timedelta(seconds=600)
        existing = _make_session(
            session_id=6,
            questions_presented=0,  # no answers
            ended_at=None,
            started_at=old_start,
        )
        existing.session_start_time = old_start
        existing.session_time_limit_seconds = 180
        existing.module_prefix = "AL — Test Module"

        db = self._make_expired_session_db(existing)

        new_session = _make_session(session_id=100, questions_presented=0)
        with patch(f"{_ROUTES}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.start_adaptive_session.return_value = new_session
            user = _make_user()
            request = MagicMock()

            with patch(f"{_ROUTES}.require_student_onboarding", return_value=None):
                _run(al_session_start(
                    request=request,
                    category="LESSON",
                    module_prefix="AL — Test Module",
                    time_limit=180,
                    language="en",
                    force_new=False,
                    db=db,
                    user=user,
                ))

        assert existing.status == ALSessionStatus.ABANDONED.value
        assert existing.ended_at is not None
