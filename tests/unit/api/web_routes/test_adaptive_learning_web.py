"""
Unit tests for AL session start v4 (module_prefix required) + new module/category endpoints.

Decision matrix under test (al_session_start):
  T-1: No active session                          → resumed=false, new session
  T-2: Same lang+cat+module active               → resumed=true (prompt)
  T-3: Same lang+cat+module + force_new          → retired + new session
  T-4: Different category active                 → auto-retire + new session
  T-5: Different module, same cat+lang           → auto-retire + new session
  T-6: Different language, same cat+module       → auto-retire + new session
  T-7: module_prefix missing                     → 422
  T-8: invalid module_prefix (not in DB)         → 422
  T-9: pool_exhausted from service               → route returns session_complete + reason

Endpoint tests:
  CAT-01: /available-categories?language=en → all categories listed
  CAT-02: invalid language → 422
  MOD-01: /modules?language=en&category=LESSON → list with module_prefix + display_name
  MOD-02: /modules?language=hu&category=LESSON → HU display names, no 'AL — ' prefix
  MOD-03: /modules for empty category → empty list (200)
  MOD-04: invalid language → 422
  MOD-05: invalid category → 422
"""
import asyncio
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

from app.api.web_routes.adaptive_learning import (
    al_session_start,
    al_session_next_question,
    al_available_categories,
    al_modules,
)
from app.models.quiz import QuizCategory

_BASE = "app.api.web_routes.adaptive_learning"
_MODULE = "AL — Training Theory"
_MODULE_HU = "AL — Edzéselmélet"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(uid=42):
    u = MagicMock()
    u.id = uid
    return u


def _existing_session(
    category=QuizCategory.LESSON,
    language="en",
    module_prefix=_MODULE,
    elapsed_s=60,
    time_limit=180,
    questions=3,
    correct=2,
):
    s = MagicMock()
    s.id = 77
    s.category = category
    s.language = language
    s.module_prefix = module_prefix
    s.session_start_time = datetime.now(timezone.utc) - timedelta(seconds=elapsed_s)
    s.session_time_limit_seconds = time_limit
    s.questions_presented = questions
    s.questions_correct = correct
    s.ended_at = None
    return s


def _new_session_obj(sid=100):
    s = MagicMock()
    s.id = sid
    s.session_start_time = datetime.now(timezone.utc)
    return s


def _db_start(module_q_count=15, existing=None):
    """
    Mock for al_session_start — two queries:
      1. module validation count → scalar()
      2. existing session lookup → first()
    """
    db = MagicMock()

    mod_q = MagicMock()
    mod_q.join.return_value = mod_q
    mod_q.filter.return_value = mod_q
    mod_q.scalar.return_value = module_q_count

    exist_q = MagicMock()
    exist_q.filter.return_value = exist_q
    exist_q.order_by.return_value = exist_q
    exist_q.first.return_value = existing

    db.query.side_effect = [mod_q, exist_q]
    return db


def _run_start(
    category="LESSON",
    module_prefix=_MODULE,
    time_limit=180,
    language="en",
    force_new=False,
    db_obj=None,
    user_obj=None,
    new_sid=100,
):
    """Call al_session_start synchronously and return parsed JSON body."""
    new_sess = _new_session_obj(new_sid)
    with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
        MockSvc.return_value.start_adaptive_session.return_value = new_sess
        resp = asyncio.run(al_session_start(
            request=MagicMock(),
            category=category,
            module_prefix=module_prefix,
            time_limit=time_limit,
            language=language,
            force_new=force_new,
            db=db_obj or _db_start(),
            user=user_obj or _user(),
        ))
    return json.loads(resp.body)


def _db_modules(rows=None):
    """Mock for al_modules — one chained query returning rows."""
    db = MagicMock()
    q = MagicMock()
    q.join.return_value = q
    q.filter.return_value = q
    q.group_by.return_value = q
    q.having.return_value = q
    q.order_by.return_value = q
    q.all.return_value = rows or []
    db.query.return_value = q
    return db


def _db_categories(rows=None):
    """Mock for al_available_categories — one chained query returning rows."""
    db = MagicMock()
    q = MagicMock()
    q.join.return_value = q
    q.filter.return_value = q
    q.group_by.return_value = q
    q.having.return_value = q
    q.all.return_value = rows or []
    db.query.return_value = q
    return db


# ── al_session_start tests ────────────────────────────────────────────────────

@pytest.mark.unit
class TestAlSessionStartV4:
    """v4: module_prefix required, exact lang+cat+module resume policy."""

    def test_fresh_user_returns_new_session(self):
        """T-1: no active session → resumed=false, new session."""
        data = _run_start(db_obj=_db_start(existing=None))

        assert data["resumed"] is False
        assert data["session_id"] == 100
        assert data["previous_session_retired"] is False
        assert data["force_new"] is False
        assert data["module_prefix"] == _MODULE
        assert data["language"] == "en"

    def test_exact_match_returns_resumed_true(self):
        """T-2: same lang+cat+module active → resumed=true, prompt shown."""
        existing = _existing_session(
            category=QuizCategory.LESSON, language="en", module_prefix=_MODULE
        )
        data = _run_start(
            category="LESSON", language="en", module_prefix=_MODULE,
            db_obj=_db_start(existing=existing),
        )

        assert data["resumed"] is True
        assert data["session_id"] == 77
        assert data["category"] == "LESSON"
        assert data["module_prefix"] == _MODULE
        assert "questions_presented" in data
        assert "current_score" in data
        assert "time_remaining_seconds" in data

    def test_exact_match_force_new_retires_and_creates(self):
        """T-3: exact match + force_new=true → retire + new session."""
        existing = _existing_session(
            category=QuizCategory.LESSON, language="en", module_prefix=_MODULE
        )
        data = _run_start(
            category="LESSON", language="en", module_prefix=_MODULE,
            force_new=True,
            db_obj=_db_start(existing=existing),
            new_sid=200,
        )

        assert data["resumed"] is False
        assert data["force_new"] is True
        assert data["previous_session_retired"] is True
        assert data["session_id"] == 200

    def test_different_category_auto_retires(self):
        """T-4: active LESSON session, start NUTRITION → auto-retire, new session."""
        existing = _existing_session(category=QuizCategory.LESSON, language="en", module_prefix=_MODULE)
        data = _run_start(
            category="NUTRITION", language="en", module_prefix="AL — Nutrition Basics",
            db_obj=_db_start(existing=existing),
            new_sid=300,
        )

        assert data["resumed"] is False
        assert data["previous_session_retired"] is True
        assert data["session_id"] == 300
        assert data["category"] == "NUTRITION"

    def test_different_module_same_category_auto_retires(self):
        """T-5: different module, same cat+lang → auto-retire, no prompt."""
        existing = _existing_session(
            category=QuizCategory.LESSON, language="en", module_prefix=_MODULE
        )
        data = _run_start(
            category="LESSON", language="en", module_prefix="AL — Motor Abilities",
            db_obj=_db_start(existing=existing),
            new_sid=400,
        )

        assert data["resumed"] is False
        assert data["previous_session_retired"] is True
        assert data["session_id"] == 400
        assert data["module_prefix"] == "AL — Motor Abilities"

    def test_different_language_auto_retires(self):
        """T-6: different language, same cat+module → auto-retire."""
        existing = _existing_session(
            category=QuizCategory.LESSON, language="en", module_prefix=_MODULE
        )
        data = _run_start(
            category="LESSON", language="hu", module_prefix=_MODULE_HU,
            db_obj=_db_start(existing=existing),
            new_sid=500,
        )

        assert data["resumed"] is False
        assert data["previous_session_retired"] is True
        assert data["session_id"] == 500
        assert data["language"] == "hu"

    def test_missing_module_prefix_returns_422(self):
        """T-7: module_prefix empty string → 422."""
        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = asyncio.run(al_session_start(
                request=MagicMock(),
                category="LESSON",
                module_prefix="   ",  # whitespace only
                time_limit=180,
                language="en",
                force_new=False,
                db=MagicMock(),
                user=_user(),
            ))
        data = json.loads(resp.body)
        assert resp.status_code == 422
        assert "module_prefix" in data["error"]

    def test_invalid_module_prefix_returns_422(self):
        """T-8: module_prefix not found in DB (count=0) → 422."""
        db = _db_start(module_q_count=0, existing=None)
        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = asyncio.run(al_session_start(
                request=MagicMock(),
                category="LESSON",
                module_prefix="AL — Does Not Exist",
                time_limit=180,
                language="en",
                force_new=False,
                db=db,
                user=_user(),
            ))
        data = json.loads(resp.body)
        assert resp.status_code == 422
        assert "insufficient" in data["error"] or "not found" in data["error"]

    def test_pool_exhausted_passes_through(self):
        """T-9: service returns pool_exhausted → route returns it unchanged."""
        mock_session = MagicMock()
        mock_session.ended_at = None

        with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_BASE}._session_guard", return_value=(mock_session, None)), \
             patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.get_next_question.return_value = {
                "session_complete": True,
                "reason": "pool_exhausted",
            }
            resp = asyncio.run(al_session_next_question(
                session_id=77,
                request=MagicMock(),
                db=MagicMock(),
                user=_user(),
                exclude_ids="",
            ))
        data = json.loads(resp.body)
        assert data["session_complete"] is True
        assert data["reason"] == "pool_exhausted"


# ── /available-categories tests ───────────────────────────────────────────────

@pytest.mark.unit
class TestAlAvailableCategories:
    """CAT-01 / CAT-02: category availability endpoint."""

    def test_returns_all_categories_with_flags(self):
        """CAT-01: en language → all QuizCategory values listed, LESSON has_content=true."""
        from app.models.quiz import QuizCategory

        # Simulate DB returning one row: LESSON with 744 questions
        lesson_row = MagicMock()
        lesson_row.category = QuizCategory.LESSON
        lesson_row.q_count = 744

        db = _db_categories(rows=[lesson_row])
        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = asyncio.run(al_available_categories(
                language="en",
                db=db,
                user=_user(),
            ))
        data = json.loads(resp.body)
        assert data["language"] == "en"
        cats = {c["value"]: c for c in data["categories"]}
        assert "LESSON" in cats
        assert cats["LESSON"]["has_content"] is True
        assert cats["LESSON"]["question_count"] == 744
        # All QuizCategory values present
        for cat in QuizCategory:
            assert cat.value in cats
        # Empty categories have has_content=false
        assert cats["MARKETING"]["has_content"] is False
        assert cats["MARKETING"]["question_count"] == 0

    def test_invalid_language_returns_422(self):
        """CAT-02: unsupported language → 422."""
        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = asyncio.run(al_available_categories(
                language="de",
                db=MagicMock(),
                user=_user(),
            ))
        assert resp.status_code == 422


# ── /modules tests ────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestAlModules:
    """MOD-01 – MOD-05: module discovery endpoint."""

    def _make_row(self, prefix, count):
        r = MagicMock()
        r.module_prefix = prefix
        r.q_count = count
        return r

    def test_en_lesson_returns_modules_without_al_prefix(self):
        """MOD-01: EN LESSON → list, display_name strips 'AL — '."""
        rows = [
            self._make_row("AL — Training Theory", 100),
            self._make_row("AL — Motor Abilities", 90),
        ]
        db = _db_modules(rows=rows)
        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = asyncio.run(al_modules(
                language="en", category="LESSON",
                db=db, user=_user(),
            ))
        data = json.loads(resp.body)
        assert data["language"] == "en"
        assert data["category"] == "LESSON"
        mods = {m["module_prefix"]: m for m in data["modules"]}
        assert "AL — Training Theory" in mods
        assert mods["AL — Training Theory"]["display_name"] == "Training Theory"
        assert mods["AL — Training Theory"]["question_count"] == 100
        assert mods["AL — Motor Abilities"]["display_name"] == "Motor Abilities"

    def test_hu_lesson_display_names_no_al_prefix(self):
        """MOD-02: HU LESSON → HU display names, 'AL — ' stripped."""
        rows = [
            self._make_row("AL — Edzéselmélet", 100),
            self._make_row("AL — Motoros képességek", 90),
        ]
        db = _db_modules(rows=rows)
        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = asyncio.run(al_modules(
                language="hu", category="LESSON",
                db=db, user=_user(),
            ))
        data = json.loads(resp.body)
        mods = {m["module_prefix"]: m for m in data["modules"]}
        assert mods["AL — Edzéselmélet"]["display_name"] == "Edzéselmélet"
        assert mods["AL — Motoros képességek"]["display_name"] == "Motoros képességek"

    def test_empty_category_returns_empty_list_not_404(self):
        """MOD-03: category with no valid modules → empty list, HTTP 200."""
        db = _db_modules(rows=[])
        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = asyncio.run(al_modules(
                language="en", category="SPORTS_PHYSIOLOGY",
                db=db, user=_user(),
            ))
        data = json.loads(resp.body)
        assert resp.status_code == 200
        assert data["modules"] == []
        assert data["category"] == "SPORTS_PHYSIOLOGY"

    def test_invalid_language_returns_422(self):
        """MOD-04: unsupported language → 422."""
        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = asyncio.run(al_modules(
                language="xx", category="LESSON",
                db=MagicMock(), user=_user(),
            ))
        assert resp.status_code == 422

    def test_invalid_category_returns_422(self):
        """MOD-05: unknown category value → 422."""
        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = asyncio.run(al_modules(
                language="en", category="INVALID_CAT",
                db=MagicMock(), user=_user(),
            ))
        assert resp.status_code == 422
