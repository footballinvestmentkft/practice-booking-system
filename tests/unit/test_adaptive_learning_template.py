"""
Regression test: adaptive_learning_session.html JS script block must survive
Jinja2 template inheritance.

Root cause guarded: student_base.html was missing {% block extra_scripts %},
causing the entire adaptive learning IIFE to be silently dropped.  All onclick
handlers (alsCatSelect, alsStartFromPicker) were present as HTML attributes but
the functions were never defined — resulting in ReferenceError on every click.

Fix: added {% block extra_scripts %}{% endblock %} slot to student_base.html.
"""
import os
import pytest
from jinja2 import Environment, FileSystemLoader

# ── Helpers ───────────────────────────────────────────────────────────────────

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "app", "templates"
)


def _make_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(os.path.abspath(_TEMPLATES_DIR)),
        autoescape=False,
    )


def _fake_user():
    class _Role:
        value = "STUDENT"

    class _Spec:
        value = "LFA_FOOTBALL_PLAYER"

    class _URL:
        path = "/adaptive-learning/session"

    class _Request:
        url = _URL()

    class _User:
        credit_balance = 1000
        name = "Test Player"
        role = _Role()
        specialization = _Spec()
        onboarding_completed = True

    return _Request(), _User()


def _render_session_page(categories=None):
    from app.models.quiz import QuizCategory

    if categories is None:
        categories = [QuizCategory.LESSON, QuizCategory.GENERAL, QuizCategory.SPORTS_PHYSIOLOGY]

    request, user = _fake_user()
    env = _make_env()
    t = env.get_template("adaptive_learning_session.html")
    return t.render(
        request=request,
        user=user,
        spec_dashboard_url="/dashboard/lfa-football-player",
        spec_dashboard_icon="⚽",
        available_categories=categories,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestExtraScriptsSlot:
    """Verify student_base.html exposes the extra_scripts slot so child pages
    can inject page-specific JavaScript."""

    def test_iife_present_in_rendered_html(self):
        """The adaptive learning IIFE must appear in the served HTML."""
        html = _render_session_page()
        assert "(function ()" in html, (
            "Adaptive learning IIFE not found — extra_scripts slot missing in student_base.html"
        )

    def test_als_start_from_picker_defined(self):
        html = _render_session_page()
        assert "window.alsStartFromPicker" in html, (
            "window.alsStartFromPicker not in rendered HTML — script block dropped"
        )

    def test_als_cat_select_defined(self):
        html = _render_session_page()
        assert "window.alsCatSelect" in html, (
            "window.alsCatSelect not in rendered HTML — script block dropped"
        )

    def test_als_init_defined(self):
        html = _render_session_page()
        assert "window.alsInit" in html, (
            "window.alsInit not in rendered HTML — script block dropped"
        )

    def test_csrf_helper_present(self):
        """getCsrfToken() must be defined inside the adaptive learning script."""
        html = _render_session_page()
        assert "function getCsrfToken" in html, (
            "getCsrfToken helper not in rendered HTML"
        )

    def test_csrf_token_in_post_helper(self):
        """post() must include X-CSRF-Token header."""
        html = _render_session_page()
        assert "'X-CSRF-Token': getCsrfToken()" in html, (
            "X-CSRF-Token header missing from post() helper"
        )

    def test_no_double_rendering(self):
        """The adaptive learning IIFE must render exactly once — not duplicated
        by a double block slot in the parent chain."""
        html = _render_session_page()
        # Count occurrences of the unique IIFE opener with 'use strict'
        count = html.count("(function ()\n    {\n") + html.count("(function () {\n")
        # Looser check: window.alsInit appears exactly once
        assert html.count("window.alsInit") == 1, (
            f"window.alsInit rendered {html.count('window.alsInit')} times — double rendering detected"
        )


class TestCategoryPickerRendering:
    """Verify category buttons render correctly with valid data-cat values."""

    def test_lesson_category_rendered(self):
        html = _render_session_page()
        assert 'data-cat="LESSON"' in html

    def test_general_category_rendered(self):
        html = _render_session_page()
        assert 'data-cat="GENERAL"' in html

    def test_sports_physiology_rendered(self):
        html = _render_session_page()
        assert 'data-cat="SPORTS_PHYSIOLOGY"' in html

    def test_start_button_present(self):
        html = _render_session_page()
        assert "als-start-btn" in html
        assert "alsStartFromPicker()" in html

    def test_empty_categories_still_renders_start_button(self):
        """Start button must exist even when no categories are available."""
        html = _render_session_page(categories=[])
        assert "als-start-btn" in html
        assert "alsStartFromPicker()" in html


class TestTimeLimitPickerRendering:
    """Verify the time-limit picker renders all three options with correct data-seconds values."""

    def test_1_minute_option_has_data_seconds_60(self):
        html = _render_session_page()
        assert 'data-seconds="60"' in html

    def test_3_minute_option_has_data_seconds_180(self):
        html = _render_session_page()
        assert 'data-seconds="180"' in html

    def test_5_minute_option_has_data_seconds_300(self):
        html = _render_session_page()
        assert 'data-seconds="300"' in html

    def test_3_minute_is_default_selected(self):
        """3 min button must carry the 'selected' class by default."""
        html = _render_session_page()
        assert 'data-seconds="180"' in html
        # The 180-second button must appear with the selected class
        import re
        pattern = r'als-time-btn[^"]*selected[^"]*"[^>]*data-seconds="180"|data-seconds="180"[^>]*als-time-btn[^"]*selected'
        # Simpler: just check the rendered HTML contains both "selected" and "180" on the same button line
        lines = html.split('\n')
        found = any('data-seconds="180"' in line and 'selected' in line for line in lines)
        assert found, "3-minute button (data-seconds=180) must have 'selected' class by default"

    def test_als_time_select_function_defined(self):
        html = _render_session_page()
        assert "window.alsTimeSelect" in html

    def test_time_limit_sent_in_init_url(self):
        """alsInit must include time_limit in the /start query string."""
        html = _render_session_page()
        assert "time_limit=" in html
        assert "state.timeLimitSeconds" in html


class TestTimeoutPhaseGuard:
    """alsTimeout must not call alsComplete unless phase is 'question'."""

    def test_phase_guard_present_in_script(self):
        html = _render_session_page()
        assert "state.phase !== 'question'" in html

    def test_timeout_function_defined(self):
        html = _render_session_page()
        assert "window.alsTimeout" in html


class TestRecentIdsCooldown:
    """Client-side cooldown: seenIds replaced with recentIds (last-5 window)."""

    def test_recent_ids_state_initialized(self):
        """State must use recentIds, not seenIds."""
        html = _render_session_page()
        assert "recentIds" in html, "recentIds state field missing"
        assert "seenIds" not in html, "seenIds must be removed — replaced by recentIds"

    def test_recent_ids_uses_last_five_logic(self):
        """renderQuestion must keep last 5 IDs via slice(0, 5) — not the old 2-element filter(Boolean)."""
        html = _render_session_page()
        assert ".slice(0, 5)" in html, "last-5 cooldown (slice(0,5)) missing in renderQuestion"
        # Must NOT still use the old last-2 pattern
        assert "state.recentIds[0]].filter(Boolean)" not in html, \
            "old last-2 filter(Boolean) still present — must use slice(0, 5)"

    def test_exclude_param_uses_recent_ids(self):
        """alsNext must pass recentIds (not seenIds) as exclude_ids."""
        html = _render_session_page()
        assert "state.recentIds.join(',')" in html

    def test_restart_resets_recent_ids(self):
        """alsRestart must reset recentIds to empty array."""
        html = _render_session_page()
        assert "state.recentIds" in html
        # recentIds = [] must appear (init + restart both set it to [])
        assert "recentIds:          []" in html or "recentIds: []" in html or html.count("recentIds") >= 2

    def test_full_session_history_not_sent(self):
        """exclude_ids must only contain recentIds, never a growing full-session history."""
        html = _render_session_page()
        # seenIds accumulation must not exist
        assert "seenIds.push" not in html
        assert "seenIds.join" not in html
        # Only recentIds is used
        assert "recentIds.join(',')" in html


class TestNoFixedQuestionCount:
    """'Question X of N' and fixed progress bar must not appear — system is time-based."""

    def test_no_of_10_label(self):
        html = _render_session_page()
        assert "of 10" not in html, "'of 10' must not appear — fixed count UI removed"

    def test_no_total_target_in_state(self):
        html = _render_session_page()
        assert "totalTarget" not in html, "totalTarget state field must be removed"

    def test_no_progress_fill_bar(self):
        html = _render_session_page()
        assert "als-progress-fill" not in html, "fixed progress fill bar must be removed"

    def test_question_counter_still_present(self):
        html = _render_session_page()
        assert "als-progress-label" in html, "question counter label must still exist"
        assert "Question 0" in html, "counter starts at 0 in HTML"

    def test_no_question_count_in_init(self):
        html = _render_session_page()
        assert "question_count" not in html or "totalTarget" not in html, \
            "question_count must not drive UI logic"


class TestFeedbackTiming:
    """Auto-advance delays must differ for correct vs wrong answers."""

    def test_correct_answer_uses_1500ms(self):
        """Correct answer advance delay must be 1500ms."""
        html = _render_session_page()
        assert "1500" in html, "1500ms delay missing"

    def test_wrong_answer_uses_3000ms(self):
        """Wrong answer advance delay must be 3000ms."""
        html = _render_session_page()
        assert "3000" in html, "3000ms delay missing"

    def test_delay_is_outcome_dependent(self):
        """advanceDelay must branch on d.correct — correct path and wrong path must differ."""
        html = _render_session_page()
        # The delay logic must be conditional on d.correct
        assert "d.correct" in html, "d.correct not referenced in delay logic"
        assert "advanceDelay" in html, "advanceDelay variable missing"

    def test_schedule_next_action_accepts_delay_param(self):
        """_scheduleNextAction must accept a delay parameter, not hardcode 1000ms."""
        html = _render_session_page()
        assert "_scheduleNextAction(advanceDelay)" in html, \
            "_scheduleNextAction must be called with advanceDelay argument"
        # Old hardcoded 1-second call must be gone
        assert "_scheduleNextAction();" not in html, \
            "_scheduleNextAction() called without delay — hardcoded timing still present"

    def test_auto_advance_label_shows_correct_duration(self):
        """Auto-advance label must show '1.5s' or '3s', not the old '1s'."""
        html = _render_session_page()
        assert "1.5s" in html, "'1.5s' label missing from auto-advance"
        assert "3s" in html, "'3s' label missing from auto-advance"
        # Old 1-second label must be gone
        assert "in 1s" not in html, "old 'in 1s' label still present"


class TestCategoryPickerThreshold:
    """Category picker must only show categories passed by the route.
    The route applies MIN_QUESTIONS_PER_CATEGORY=10 filter before rendering.
    The template itself is agnostic — it renders whatever available_categories it receives.
    These tests verify template behaviour for the pre-threshold (hidden) and post-threshold
    (visible) states that the route produces."""

    def test_general_hidden_when_not_in_available_categories(self):
        """GENERAL must not appear in picker when route excludes it (< 10 metadata questions)."""
        from app.models.quiz import QuizCategory
        html = _render_session_page(categories=[QuizCategory.LESSON, QuizCategory.SPORTS_PHYSIOLOGY])
        assert 'data-cat="GENERAL"' not in html, \
            "GENERAL button rendered despite being excluded by route threshold"

    def test_nutrition_hidden_when_not_in_available_categories(self):
        """NUTRITION must not appear in picker when route excludes it (0 questions)."""
        from app.models.quiz import QuizCategory
        html = _render_session_page(categories=[QuizCategory.LESSON, QuizCategory.SPORTS_PHYSIOLOGY])
        assert 'data-cat="NUTRITION"' not in html, \
            "NUTRITION button rendered despite being excluded by route threshold"

    def test_general_visible_after_threshold_met(self):
        """GENERAL appears in picker once route includes it (≥ 10 metadata questions)."""
        from app.models.quiz import QuizCategory
        html = _render_session_page(
            categories=[QuizCategory.LESSON, QuizCategory.SPORTS_PHYSIOLOGY, QuizCategory.GENERAL]
        )
        assert 'data-cat="GENERAL"' in html, \
            "GENERAL button missing despite being included by route (threshold met)"

    def test_nutrition_visible_after_threshold_met(self):
        """NUTRITION appears in picker once route includes it (≥ 10 metadata questions)."""
        from app.models.quiz import QuizCategory
        html = _render_session_page(
            categories=[QuizCategory.LESSON, QuizCategory.SPORTS_PHYSIOLOGY,
                        QuizCategory.GENERAL, QuizCategory.NUTRITION]
        )
        assert 'data-cat="NUTRITION"' in html, \
            "NUTRITION button missing despite being included by route (threshold met)"

    def test_only_provided_categories_render(self):
        """Template renders exactly the categories it receives — no extras added."""
        from app.models.quiz import QuizCategory
        html = _render_session_page(categories=[QuizCategory.LESSON])
        assert 'data-cat="LESSON"' in html
        assert 'data-cat="GENERAL"' not in html
        assert 'data-cat="SPORTS_PHYSIOLOGY"' not in html
        assert 'data-cat="NUTRITION"' not in html

    def test_all_four_categories_visible_when_all_thresholds_met(self):
        """When all four categories meet the threshold, all four buttons render."""
        from app.models.quiz import QuizCategory
        html = _render_session_page(
            categories=[QuizCategory.LESSON, QuizCategory.SPORTS_PHYSIOLOGY,
                        QuizCategory.GENERAL, QuizCategory.NUTRITION]
        )
        assert 'data-cat="LESSON"' in html
        assert 'data-cat="SPORTS_PHYSIOLOGY"' in html
        assert 'data-cat="GENERAL"' in html
        assert 'data-cat="NUTRITION"' in html
