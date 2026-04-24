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
