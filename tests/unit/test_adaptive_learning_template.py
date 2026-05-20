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


def _render_session_page(categories=None, session_language="en"):
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
        session_language=session_language,
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
    """Auto-advance removed: feedback + explanation stay until user clicks Next."""

    def test_no_auto_advance(self):
        """_scheduleNextAction must be gone — no setTimeout auto-advance."""
        html = _render_session_page()
        assert "_scheduleNextAction" not in html, \
            "_scheduleNextAction still present — auto-advance not fully removed"

    def test_show_next_button_called_after_answer(self):
        """_showNextButton() must be called in the answer handler (replaces auto-advance)."""
        html = _render_session_page()
        assert "_showNextButton()" in html, \
            "_showNextButton() not called in answer handler"

    def test_next_button_element_present(self):
        """als-next-btn-wrap and als-next-btn must exist in the question phase HTML."""
        html = _render_session_page()
        assert 'id="als-next-btn-wrap"' in html, "als-next-btn-wrap element missing"
        assert 'id="als-next-btn"' in html, "als-next-btn element missing"

    def test_next_button_hidden_by_default(self):
        """als-next-btn-wrap must start hidden (display:none)."""
        html = _render_session_page()
        assert 'id="als-next-btn-wrap" style="display:none' in html, \
            "als-next-btn-wrap is not hidden by default"

    def test_next_button_disable_guard(self):
        """alsNext must disable the button immediately to prevent double submission."""
        html = _render_session_page()
        assert "btn.disabled = true" in html, \
            "double-click guard (btn.disabled = true) missing from alsNext"

    def test_next_button_reenabled_on_error(self):
        """alsNext must re-enable the button in the catch block (slow network recovery)."""
        html = _render_session_page()
        assert "btn.disabled = false" in html, \
            "error recovery (btn.disabled = false) missing from alsNext catch block"

    def test_no_old_auto_advance_element(self):
        """als-auto-advance element must be gone."""
        html = _render_session_page()
        assert "als-auto-advance" not in html, \
            "als-auto-advance element still present in template"


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


class TestLanguageSwitcherRendering:
    """Language switcher renders correctly in the category picker phase."""

    def test_switcher_element_present(self):
        html = _render_session_page()
        assert 'id="als-lang-switcher"' in html

    def test_en_link_present_with_correct_href(self):
        html = _render_session_page()
        assert 'href="?language=en"' in html

    def test_hu_link_present_with_correct_href(self):
        html = _render_session_page()
        assert 'href="?language=hu"' in html

    def _btn_region(self, html, lang):
        """Return the rendered <a> tag region for the given lang button."""
        # The tag starts at href="?language=<lang>" and spans ~150 chars
        # (href on one line, class on next, data-lang, data-testid, text content)
        idx = html.index(f'href="?language={lang}"')
        return html[idx:idx + 180]

    def test_en_active_when_language_is_en(self):
        html = _render_session_page(session_language="en")
        assert 'active' in self._btn_region(html, 'en'), \
            "EN link must have 'active' class when session_language='en'"
        assert 'active' not in self._btn_region(html, 'hu'), \
            "HU link must NOT have 'active' class when session_language='en'"

    def test_hu_active_when_language_is_hu(self):
        html = _render_session_page(session_language="hu")
        assert 'active' not in self._btn_region(html, 'en'), \
            "EN link must NOT have 'active' class when session_language='hu'"
        assert 'active' in self._btn_region(html, 'hu'), \
            "HU link must have 'active' class when session_language='hu'"

    def test_data_testid_attributes_present(self):
        html = _render_session_page()
        assert 'data-testid="als-lang-switcher"' in html
        assert 'data-testid="als-lang-en"' in html
        assert 'data-testid="als-lang-hu"' in html

    def test_switcher_inside_picker_card(self):
        """Switcher div must appear before the category title inside .als-picker-card."""
        html = _render_session_page()
        # Use DOM-specific markers: data-testid attribute vs the rendered <p class="als-picker-title"> tag
        switcher_idx = html.index('data-testid="als-lang-switcher"')
        title_idx = html.index('class="als-picker-title"')
        assert switcher_idx < title_idx, "Language switcher must appear before the picker title"

    def test_switcher_present_with_empty_categories(self):
        """Switcher must render even when no categories are available."""
        html = _render_session_page(categories=[])
        assert 'id="als-lang-switcher"' in html


class TestLanguageSwitcherJsGuard:
    """JS showPhase() guard hides switcher in non-category phases."""

    def test_showphase_hides_switcher_in_loading(self):
        html = _render_session_page()
        assert "name === 'category'" in html, (
            "showPhase must contain name === 'category' conditional for switcher visibility"
        )

    def test_show_phase_contains_als_lang_switcher_reference(self):
        html = _render_session_page()
        assert "als-lang-switcher" in html
        # The showPhase function must reference the switcher element
        showphase_start = html.index("window.showPhase")
        showphase_end = html.index("};", showphase_start) + 2
        showphase_body = html[showphase_start:showphase_end]
        assert "als-lang-switcher" in showphase_body, (
            "showPhase() must control als-lang-switcher visibility"
        )

    def test_als_start_from_picker_hides_switcher_immediately(self):
        html = _render_session_page()
        start_fn_start = html.index("window.alsStartFromPicker")
        start_fn_end = html.index("};", start_fn_start) + 2
        start_fn_body = html[start_fn_start:start_fn_end]
        assert "als-lang-switcher" in start_fn_body, (
            "alsStartFromPicker() must hide als-lang-switcher as defense-in-depth"
        )

    def test_switcher_hidden_in_non_category_phases_via_showphase(self):
        """showPhase for non-category phases must set display='none' on switcher."""
        html = _render_session_page()
        showphase_start = html.index("window.showPhase")
        showphase_end = html.index("};", showphase_start) + 2
        showphase_body = html[showphase_start:showphase_end]
        assert "display" in showphase_body and "none" in showphase_body, (
            "showPhase() must set display:none on switcher for non-category phases"
        )

    def test_language_embedded_in_start_url(self):
        """alsInit URL must include session_language for the selected language."""
        html = _render_session_page(session_language="en")
        assert '&language=en' in html or "session_language" in html, (
            "alsInit URL must embed the session language"
        )


class TestAnsweredStateHide:
    """After submitting an answer, question card and options must be hidden.
    Only feedback + Next button remain visible until the user advances."""

    def test_question_card_has_id(self):
        """als-question-card must carry an id= so JS can target it."""
        html = _render_session_page()
        assert 'id="als-question-card"' in html, \
            'als-question-card div missing id="als-question-card"'

    def test_show_answered_state_helper_present(self):
        """_showAnsweredState() helper must exist in the template JS."""
        html = _render_session_page()
        assert "_showAnsweredState" in html, \
            "_showAnsweredState helper function missing from template"

    def test_show_answered_state_not_in_answer_callback(self):
        """_showAnsweredState() must NOT be called inside alsAnswer's .then() callback.

        P1 UX fix: highlights must stay visible until the user clicks Next.
        The hide must happen in alsNext, not synchronously in the answer callback.
        """
        html = _render_session_page()
        start = html.index("function alsAnswer(")
        end = html.index("\n    }", start)
        body = html[start:end]
        assert "_showAnsweredState()" not in body, (
            "_showAnsweredState() must not be called inside alsAnswer — "
            "it fires in alsNext so the user can see the correct-answer highlight"
        )

    def test_show_answered_state_called_in_als_next(self):
        """_showAnsweredState() must be called at the start of window.alsNext.

        This is the UX fix: hide happens on Next click, after user sees highlights.
        """
        html = _render_session_page()
        start = html.index("window.alsNext = function ()")
        end = html.index("\n    };", start)
        body = html[start:end]
        assert "_showAnsweredState()" in body, (
            "_showAnsweredState() must be called inside window.alsNext — "
            "options should be hidden when the user requests the next question"
        )

    def test_show_answered_state_hides_question_card(self):
        """_showAnsweredState must set display:none on als-question-card."""
        html = _render_session_page()
        fn_start = html.index("function _showAnsweredState(")
        fn_end = html.index("}", fn_start)
        fn_body = html[fn_start:fn_end]
        assert "als-question-card" in fn_body, \
            "_showAnsweredState does not reference als-question-card"
        assert "display" in fn_body and "none" in fn_body, \
            "_showAnsweredState does not set display:none on question card"

    def test_show_answered_state_hides_options(self):
        """_showAnsweredState must set display:none on als-options."""
        html = _render_session_page()
        fn_start = html.index("function _showAnsweredState(")
        fn_end = html.index("}", fn_start)
        fn_body = html[fn_start:fn_end]
        assert "als-options" in fn_body, \
            "_showAnsweredState does not reference als-options"

    def test_render_question_restores_question_card(self):
        """renderQuestion must restore als-question-card visibility (display='')."""
        html = _render_session_page()
        fn_start = html.index("function renderQuestion(")
        fn_end = html.index("\n    }", fn_start)
        fn_body = html[fn_start:fn_end]
        assert "als-question-card" in fn_body, \
            "renderQuestion does not restore als-question-card"

    def test_render_question_restores_options(self):
        """renderQuestion must restore als-options visibility (display='')."""
        html = _render_session_page()
        fn_start = html.index("function renderQuestion(")
        fn_end = html.index("\n    }", fn_start)
        fn_body = html[fn_start:fn_end]
        assert "als-options" in fn_body and "display" in fn_body, \
            "renderQuestion does not restore als-options display"

    def test_error_path_does_not_hide_question(self):
        """On POST /answer failure (!res.ok), _showAnsweredState must NOT be called
        so the question remains visible for retry."""
        html = _render_session_page()
        fn_start = html.index("function alsAnswer(")
        fn_end = html.index("\n    }", fn_start)
        fn_body = html[fn_start:fn_end]
        # Find the !res.ok error branch
        err_start = fn_body.index("if (!res.ok)")
        err_end = fn_body.index("return;", err_start) + len("return;")
        err_branch = fn_body[err_start:err_end]
        assert "_showAnsweredState" not in err_branch, \
            "_showAnsweredState must NOT be called in the !res.ok error branch"
