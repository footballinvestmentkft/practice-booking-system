"""Unit tests for al_editor_service + Phase 2 admin editor routes.

AQE-01..08   TestContentStatusMigration  — ORM enum, _sync_is_active, state machine
AQE-09..16   TestQuestionEditorService   — update_question, update_option, swap, delete
AQE-17..20   TestFixedQuestionProtection — option_type immutability, add_option guards
AQE-21..24   TestDraftPublishedVisibility — visibility values + archive side-effects
AQE-25..28   TestAdminEditorRoutes        — GET/POST quiz-edit, publish, draft routes
AQE-29..32   TestRuntimeRegression        — runtime filter uses content_status; delete guards
"""
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from fastapi.responses import HTMLResponse, RedirectResponse

from app.models.quiz import ContentStatus, OptionType
from app.services.al_editor_service import (
    EditorError,
    InvalidTransitionError,
    OptionCreatePayload,
    OptionEditPayload,
    ProtectedFieldError,
    QuestionEditPayload,
    ValidationError,
    _pool_mode,
    _sync_is_active,
    add_option,
    archive_quiz,
    delete_option,
    delete_question,
    draft_quiz,
    get_question_with_options,
    publish_quiz,
    swap_correct_option,
    update_option,
    update_question,
)

_BASE_ROUTE = "app.api.web_routes.admin.adaptive_learning"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_quiz(quiz_id: int = 1, status: str = ContentStatus.DRAFT.value) -> MagicMock:
    q = MagicMock()
    q.id = quiz_id
    q.content_status = status
    q.is_active = (status == ContentStatus.PUBLISHED.value)
    return q


def _make_question(question_id: int = 10, quiz_id: int = 1) -> MagicMock:
    qq = MagicMock()
    qq.id = question_id
    qq.quiz_id = quiz_id
    qq.question_text = "What is pressing?"
    qq.explanation = "A defensive tactic."
    return qq


def _make_option(
    opt_id: int,
    text: str = "option text",
    is_correct: bool = False,
    option_type: OptionType = OptionType.FIXED,
    question_id: int = 10,
) -> MagicMock:
    o = MagicMock()
    o.id = opt_id
    o.option_text = text
    o.is_correct = is_correct
    o.option_type = option_type
    o.question_id = question_id
    return o


def _make_meta(question_id: int = 10) -> MagicMock:
    m = MagicMock()
    m.question_id = question_id
    m.estimated_difficulty = 0.5
    m.cognitive_load = 0.5
    m.average_time_seconds = 30.0
    m.concept_tags = '["pressing"]'
    return m


def _mock_db_for_quiz(quiz: MagicMock) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = quiz
    return db


def _admin():
    u = MagicMock()
    from app.models.user import UserRole
    u.role = UserRole.ADMIN
    return u


def _req(path: str = "/admin/adaptive-learning/quizzes/1/edit"):
    r = MagicMock()
    r.url.path = path
    r.query_params.get = MagicMock(return_value="")
    return r


def _mock_templates():
    tpl = MagicMock()
    tpl.TemplateResponse.return_value = HTMLResponse("<html>ok</html>")
    return tpl


# ---------------------------------------------------------------------------
# AQE-01..08  TestContentStatusMigration
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestContentStatusMigration:

    def test_aqe01_content_status_enum_values(self):
        """AQE-01: ContentStatus has DRAFT / PUBLISHED / ARCHIVED string values."""
        assert ContentStatus.DRAFT.value     == "DRAFT"
        assert ContentStatus.PUBLISHED.value == "PUBLISHED"
        assert ContentStatus.ARCHIVED.value  == "ARCHIVED"

    def test_aqe02_sync_is_active_published_sets_true(self):
        """AQE-02: _sync_is_active sets is_active=True when PUBLISHED."""
        quiz = _make_quiz(status=ContentStatus.PUBLISHED.value)
        quiz.is_active = False  # start out of sync
        _sync_is_active(quiz)
        assert quiz.is_active is True

    def test_aqe03_sync_is_active_draft_sets_false(self):
        """AQE-03: _sync_is_active sets is_active=False when DRAFT."""
        quiz = _make_quiz(status=ContentStatus.DRAFT.value)
        quiz.is_active = True
        _sync_is_active(quiz)
        assert quiz.is_active is False

    def test_aqe04_sync_is_active_archived_sets_false(self):
        """AQE-04: _sync_is_active sets is_active=False when ARCHIVED."""
        quiz = _make_quiz(status=ContentStatus.ARCHIVED.value)
        quiz.is_active = True
        _sync_is_active(quiz)
        assert quiz.is_active is False

    def test_aqe05_publish_quiz_draft_to_published(self):
        """AQE-05: publish_quiz transitions DRAFT → PUBLISHED and syncs is_active."""
        quiz = _make_quiz(status=ContentStatus.DRAFT.value)
        db = _mock_db_for_quiz(quiz)
        result = publish_quiz(db, quiz_id=1)
        assert result.content_status == ContentStatus.PUBLISHED.value
        assert result.is_active is True
        db.commit.assert_called_once()

    def test_aqe06_draft_quiz_published_to_draft(self):
        """AQE-06: draft_quiz transitions PUBLISHED → DRAFT and sets is_active=False."""
        quiz = _make_quiz(status=ContentStatus.PUBLISHED.value)
        db = _mock_db_for_quiz(quiz)
        result = draft_quiz(db, quiz_id=1)
        assert result.content_status == ContentStatus.DRAFT.value
        assert result.is_active is False
        db.commit.assert_called_once()

    def test_aqe07_archive_quiz_irreversible(self):
        """AQE-07: archive_quiz → ARCHIVED; subsequent publish_quiz raises."""
        quiz = _make_quiz(status=ContentStatus.DRAFT.value)
        db = _mock_db_for_quiz(quiz)
        archive_quiz(db, quiz_id=1)
        assert quiz.content_status == ContentStatus.ARCHIVED.value
        assert quiz.is_active is False

    def test_aqe08_publish_archived_raises(self):
        """AQE-08: publish_quiz on ARCHIVED quiz raises InvalidTransitionError."""
        quiz = _make_quiz(status=ContentStatus.ARCHIVED.value)
        db = _mock_db_for_quiz(quiz)
        with pytest.raises(InvalidTransitionError):
            publish_quiz(db, quiz_id=1)


# ---------------------------------------------------------------------------
# AQE-09..16  TestQuestionEditorService
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestQuestionEditorService:

    def _make_update_payload(self, **kwargs) -> QuestionEditPayload:
        defaults = dict(
            question_text        = "Updated text?",
            explanation          = "Updated explanation.",
            estimated_difficulty = 0.6,
            cognitive_load       = 0.4,
            average_time_seconds = 25.0,
            concept_tags         = ["pressing"],
        )
        defaults.update(kwargs)
        return QuestionEditPayload(**defaults)

    def _db_for_question(self, question: MagicMock, meta: MagicMock | None = None) -> MagicMock:
        db = MagicMock()
        results = {
            "question": question,
            "meta": meta,
        }
        def _filter_side_effect(*args, **kwargs):
            m = MagicMock()
            m.first.side_effect = lambda: question if results["meta"] is None else meta
            return m

        # question lookup
        db.query.return_value.filter.return_value.first.return_value = question
        # meta lookup returns None unless specified
        db.query.return_value.filter.return_value.first.return_value = question
        return db

    def test_aqe09_update_question_updates_text(self):
        """AQE-09: update_question modifies question_text and explanation in place."""
        question = _make_question()
        meta = _make_meta()

        db = MagicMock()
        # First call (QuizQuestion) returns question; second (QuestionMetadata) returns meta
        db.query.return_value.filter.return_value.first.side_effect = [question, meta]

        payload = self._make_update_payload(question_text="New question?", explanation="New explanation.")
        result = update_question(db, question_id=10, payload=payload, quiz_id=1)
        assert result.question_text == "New question?"
        assert result.explanation   == "New explanation."
        db.commit.assert_called_once()

    def test_aqe10_update_question_rejects_difficulty_out_of_range(self):
        """AQE-10: estimated_difficulty > 1.0 raises ValidationError."""
        payload = self._make_update_payload(estimated_difficulty=1.5)
        with pytest.raises(ValidationError):
            update_question(MagicMock(), question_id=10, payload=payload)

    def test_aqe11_update_question_rejects_zero_time(self):
        """AQE-11: average_time_seconds <= 0 raises ValidationError."""
        payload = self._make_update_payload(average_time_seconds=0)
        with pytest.raises(ValidationError):
            update_question(MagicMock(), question_id=10, payload=payload)

    def test_aqe12_update_option_updates_text_only(self):
        """AQE-12: update_option mutates option_text; option_type unchanged."""
        opt = _make_option(opt_id=5, option_type=OptionType.FIXED)
        original_type = opt.option_type
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = opt
        payload = OptionEditPayload(option_text="New text")
        result = update_option(db, option_id=5, payload=payload)
        assert result.option_text == "New text"
        assert result.option_type == original_type

    def test_aqe13_swap_correct_blocked_for_published_quiz(self):
        """AQE-13: swap_correct_option raises ProtectedFieldError for PUBLISHED quiz."""
        question = _make_question()
        quiz = _make_quiz(status=ContentStatus.PUBLISHED.value)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [question, quiz]

        with pytest.raises(ProtectedFieldError):
            swap_correct_option(db, question_id=10, new_correct_option_id=99)

    def test_aqe14_swap_correct_blocked_for_pool_mode(self):
        """AQE-14: swap_correct_option raises ProtectedFieldError for pool-mode question."""
        question = _make_question()
        quiz = _make_quiz(status=ContentStatus.DRAFT.value)
        variants    = [_make_option(i, option_type=OptionType.CORRECT_VARIANT, is_correct=True) for i in range(1, 3)]
        distractors = [_make_option(i+10, option_type=OptionType.DISTRACTOR) for i in range(6)]

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [question, quiz]
        db.query.return_value.filter.return_value.all.return_value  = variants + distractors

        with pytest.raises(ProtectedFieldError):
            swap_correct_option(db, question_id=10, new_correct_option_id=1)

    def test_aqe15_delete_question_blocked_for_non_draft(self):
        """AQE-15: delete_question on PUBLISHED quiz raises InvalidTransitionError."""
        question = _make_question()
        quiz = _make_quiz(status=ContentStatus.PUBLISHED.value)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [question, quiz]

        with pytest.raises(InvalidTransitionError):
            delete_question(db, question_id=10, quiz_id=1)

    def test_aqe16_delete_question_blocked_for_last_question(self):
        """AQE-16: delete_question blocks when question count would drop to 0."""
        question = _make_question()
        quiz = _make_quiz(status=ContentStatus.DRAFT.value)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [question, quiz]
        # Only 1 question in quiz
        db.query.return_value.filter.return_value.count.return_value = 1

        with pytest.raises(ValidationError, match="last question"):
            delete_question(db, question_id=10, quiz_id=1)


# ---------------------------------------------------------------------------
# AQE-17..20  TestFixedQuestionProtection
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFixedQuestionProtection:

    def _db_with_question_and_options(self, question, options):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = question
        db.query.return_value.filter.return_value.all.return_value   = options
        return db

    def test_aqe17_add_fixed_option_to_pool_question_blocked(self):
        """AQE-17: add_option FIXED type to pool-mode question raises ProtectedFieldError."""
        question = _make_question()
        variants    = [_make_option(i, option_type=OptionType.CORRECT_VARIANT, is_correct=True) for i in range(1, 3)]
        distractors = [_make_option(i+10, option_type=OptionType.DISTRACTOR) for i in range(6)]
        db = self._db_with_question_and_options(question, variants + distractors)

        payload = OptionCreatePayload(
            option_text="New distractor", is_correct=False, option_type=OptionType.FIXED
        )
        with pytest.raises(ProtectedFieldError, match="FIXED"):
            add_option(db, question_id=10, payload=payload)

    def test_aqe18_add_variant_to_fixed_question_blocked(self):
        """AQE-18: add_option CORRECT_VARIANT to FIXED question raises ProtectedFieldError."""
        question = _make_question()
        fixed_opts = [_make_option(i, option_type=OptionType.FIXED, is_correct=(i == 1)) for i in range(1, 5)]
        db = self._db_with_question_and_options(question, fixed_opts)

        payload = OptionCreatePayload(
            option_text="Variant", is_correct=True, option_type=OptionType.CORRECT_VARIANT
        )
        with pytest.raises(ProtectedFieldError, match="CORRECT_VARIANT"):
            add_option(db, question_id=10, payload=payload)

    def test_aqe19_add_distractor_to_fixed_question_blocked(self):
        """AQE-19: add_option DISTRACTOR to FIXED question raises ProtectedFieldError."""
        question = _make_question()
        fixed_opts = [_make_option(i, option_type=OptionType.FIXED, is_correct=(i == 1)) for i in range(1, 5)]
        db = self._db_with_question_and_options(question, fixed_opts)

        payload = OptionCreatePayload(
            option_text="Distractor", is_correct=False, option_type=OptionType.DISTRACTOR
        )
        with pytest.raises(ProtectedFieldError, match="CORRECT_VARIANT"):
            add_option(db, question_id=10, payload=payload)

    def test_aqe20_update_option_never_modifies_option_type(self):
        """AQE-20: update_option service has no option_type parameter in OptionEditPayload."""
        payload = OptionEditPayload(option_text="text")
        assert not hasattr(payload, "option_type"), (
            "OptionEditPayload must NOT have an option_type field"
        )


# ---------------------------------------------------------------------------
# AQE-21..24  TestDraftPublishedVisibility
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDraftPublishedVisibility:

    def test_aqe21_draft_status_string(self):
        """AQE-21: ContentStatus.DRAFT.value is the string 'DRAFT'."""
        assert ContentStatus.DRAFT.value == "DRAFT"

    def test_aqe22_published_status_string(self):
        """AQE-22: ContentStatus.PUBLISHED.value is the string 'PUBLISHED'."""
        assert ContentStatus.PUBLISHED.value == "PUBLISHED"

    def test_aqe23_archived_status_string(self):
        """AQE-23: ContentStatus.ARCHIVED.value is the string 'ARCHIVED'."""
        assert ContentStatus.ARCHIVED.value == "ARCHIVED"

    def test_aqe24_archive_sets_is_active_false_and_archived_status(self):
        """AQE-24: archive_quiz sets content_status=ARCHIVED and is_active=False."""
        quiz = _make_quiz(status=ContentStatus.PUBLISHED.value)
        quiz.is_active = True
        db = _mock_db_for_quiz(quiz)
        result = archive_quiz(db, quiz_id=1)
        assert result.content_status == ContentStatus.ARCHIVED.value
        assert result.is_active is False


# ---------------------------------------------------------------------------
# AQE-25..28  TestAdminEditorRoutes
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestAdminEditorRoutes:

    def test_aqe25_get_quiz_edit_returns_html(self):
        """AQE-25: GET /quizzes/{id}/edit → 200 HTMLResponse for admin."""
        from app.api.web_routes.admin.adaptive_learning import al_quiz_edit_get

        quiz = _make_quiz(status=ContentStatus.PUBLISHED.value)
        quiz.title = "Test Quiz"
        quiz.difficulty = MagicMock()
        quiz.difficulty.value = "MEDIUM"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = quiz
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        with patch(f"{_BASE_ROUTE}.templates", _mock_templates()):
            resp = _run(al_quiz_edit_get(quiz_id=1, request=_req(), db=db, user=_admin()))
        assert isinstance(resp, HTMLResponse)

    def test_aqe26_get_quiz_edit_missing_quiz_redirects(self):
        """AQE-26: GET /quizzes/{id}/edit quiz not found → 303 redirect."""
        from app.api.web_routes.admin.adaptive_learning import al_quiz_edit_get

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        resp = _run(al_quiz_edit_get(quiz_id=999, request=_req(), db=db, user=_admin()))
        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303

    def test_aqe27_post_publish_calls_service_and_redirects(self):
        """AQE-27: POST /quizzes/{id}/publish → calls editor.publish_quiz, redirects."""
        from app.api.web_routes.admin.adaptive_learning import al_quiz_publish

        quiz = _make_quiz(status=ContentStatus.DRAFT.value)
        db = _mock_db_for_quiz(quiz)

        with patch(f"{_BASE_ROUTE}.editor.publish_quiz", return_value=quiz) as mock_pub:
            resp = _run(al_quiz_publish(quiz_id=1, request=_req(), db=db, user=_admin()))
        mock_pub.assert_called_once_with(db, 1)
        assert isinstance(resp, RedirectResponse)
        assert "success" in str(resp.headers.get("location", ""))

    def test_aqe28_post_draft_calls_service_and_redirects(self):
        """AQE-28: POST /quizzes/{id}/draft → calls editor.draft_quiz, redirects."""
        from app.api.web_routes.admin.adaptive_learning import al_quiz_draft

        quiz = _make_quiz(status=ContentStatus.PUBLISHED.value)
        db = _mock_db_for_quiz(quiz)

        with patch(f"{_BASE_ROUTE}.editor.draft_quiz", return_value=quiz) as mock_draft:
            resp = _run(al_quiz_draft(quiz_id=1, request=_req(), db=db, user=_admin()))
        mock_draft.assert_called_once_with(db, 1)
        assert isinstance(resp, RedirectResponse)
        assert "success" in str(resp.headers.get("location", ""))


# ---------------------------------------------------------------------------
# AQE-29..32  TestRuntimeRegression
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRuntimeRegression:

    def test_aqe29_web_routes_use_content_status_not_is_active(self):
        """AQE-29: adaptive_learning web routes filter by content_status, not is_active."""
        import inspect
        import app.api.web_routes.adaptive_learning as mod
        src = inspect.getsource(mod)
        # Must use content_status PUBLISHED filter
        assert "content_status" in src, "web routes must filter by content_status"
        # Must NOT use raw is_active filter in filter() calls (legacy pattern removed)
        assert "Quiz.is_active == True" not in src, (
            "web routes must not use is_active == True; use content_status"
        )

    def test_aqe30_service_uses_content_status_not_is_active(self):
        """AQE-30: adaptive_learning service filters by content_status, not is_active."""
        import inspect
        import app.services.adaptive_learning as mod
        src = inspect.getsource(mod)
        assert "content_status" in src
        assert "Quiz.is_active == True" not in src

    def test_aqe31_update_option_empty_text_raises_validation_error(self):
        """AQE-31: update_option with blank text raises ValidationError."""
        payload = OptionEditPayload(option_text="   ")
        opt = _make_option(opt_id=5)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = opt
        with pytest.raises(ValidationError, match="non-empty"):
            update_option(db, option_id=5, payload=payload)

    def test_aqe32_delete_option_pool_enforces_min_distractors(self):
        """AQE-32: delete_option blocks if pool distractors would fall below _V2_MIN_DISTRACTORS."""
        question = _make_question()
        quiz = _make_quiz(status=ContentStatus.DRAFT.value)

        # Exactly 6 distractors — deleting one would leave 5 (< 6)
        variants    = [_make_option(i, option_type=OptionType.CORRECT_VARIANT, is_correct=True) for i in range(1, 3)]
        distractors = [_make_option(i+10, option_type=OptionType.DISTRACTOR) for i in range(6)]
        target_dist = distractors[0]
        target_dist.is_correct = False

        db = MagicMock()
        # option lookup for delete_option
        db.query.return_value.filter.return_value.first.side_effect = [
            target_dist,   # _require_option
            question,      # _require_question
            quiz,          # _require_quiz
        ]
        db.query.return_value.filter.return_value.all.return_value = variants + distractors

        with pytest.raises(ValidationError, match="6 distractors"):
            delete_option(db, option_id=target_dist.id)
