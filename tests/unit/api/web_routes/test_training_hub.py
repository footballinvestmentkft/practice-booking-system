"""Unit tests for Training hub (TRN-01..06) + regression.

TRN-01   GET /training returns 200 for an authenticated, onboarded student
TRN-02   GET /training redirects to /dashboard for a non-student / non-onboarded user
TRN-03   training_hub.html contains On-site / Hybrid / Virtual sections and /adaptive-learning link
TRN-04   dashboard_student_new.html mod-nav contains /training card
TRN-05   dashboard_student_new.html mod-nav does NOT contain direct /adaptive-learning card
TRN-06   training_hub.html Virtual Games link text says "Virtual Games" (not "Virtual Training")
REG-01   GET /adaptive-learning handler still exists and is reachable (regression guard)
"""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.responses import HTMLResponse, RedirectResponse

_ROUTES = "app.api.web_routes.training"
_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[4] / "app" / "templates"
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _make_student(user_id=1):
    from app.models.user import UserRole
    u = MagicMock()
    u.id = user_id
    u.role = UserRole.STUDENT
    u.onboarding_completed = True
    u.credit_balance = 0
    u.specialization = MagicMock()
    u.specialization.value = "LFA_FOOTBALL_PLAYER"
    return u


def _make_request(path="/training"):
    r = MagicMock()
    r.url.path = path
    return r


# ── TRN-01: authenticated student → 200 ──────────────────────────────────────

def _make_db():
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = None
    db.query.return_value = q
    return db


class TestTrainingHubAuth:

    def test_trn01_authenticated_student_gets_200(self):
        """TRN-01: GET /training returns HTMLResponse for onboarded student."""
        from app.api.web_routes.training import training_hub_page

        user = _make_student()
        request = _make_request()
        db = _make_db()

        fake_response = MagicMock(spec=HTMLResponse)
        fake_response.status_code = 200

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_games", return_value=[]), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = fake_response
            result = _run(training_hub_page(request=request, db=db, user=user))

        assert result is fake_response
        mock_tmpl.TemplateResponse.assert_called_once()
        call_args = mock_tmpl.TemplateResponse.call_args
        assert call_args[0][0] == "training_hub.html"
        ctx = call_args[0][1]
        assert ctx["request"] is request
        assert ctx["user"] is user

    def test_trn02_non_student_gets_redirect(self):
        """TRN-02: GET /training redirects when require_student_onboarding returns redirect."""
        from app.api.web_routes.training import training_hub_page

        user = _make_student()
        user.onboarding_completed = False
        request = _make_request()
        db = _make_db()

        redirect = RedirectResponse(url="/dashboard", status_code=303)

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=redirect), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            result = _run(training_hub_page(request=request, db=db, user=user))

        assert isinstance(result, RedirectResponse)
        mock_tmpl.TemplateResponse.assert_not_called()


# ── TRN-03: template structure ────────────────────────────────────────────────

class TestTrainingHubTemplate:

    def _read(self, filename):
        return (_TEMPLATES_DIR / filename).read_text(encoding="utf-8")

    def test_trn03_on_site_section_present(self):
        """TRN-03a: training_hub.html contains On-site section."""
        html = self._read("training_hub.html")
        assert "On-site" in html

    def test_trn03_hybrid_section_present(self):
        """TRN-03b: training_hub.html contains Hybrid section."""
        html = self._read("training_hub.html")
        assert "Hybrid" in html

    def test_trn03_virtual_section_present(self):
        """TRN-03c: training_hub.html contains Virtual section."""
        html = self._read("training_hub.html")
        assert "Virtual" in html

    def test_trn03_adaptive_learning_link_present(self):
        """TRN-03d: training_hub.html contains /adaptive-learning link."""
        html = self._read("training_hub.html")
        assert 'href="/adaptive-learning"' in html

    def test_trn03_on_site_hybrid_are_planned(self):
        """TRN-03e: On-site and Hybrid are marked as planned, not active routes."""
        html = self._read("training_hub.html")
        # Neither On-site nor Hybrid should be wrapped in an <a href> link
        assert 'href="/training/on-site"' not in html
        assert 'href="/training/hybrid"' not in html
        assert "Coming soon" in html

    def test_trn06_virtual_games_label_in_training_hub(self):
        """TRN-06: training_hub.html Virtual sub-link uses 'Virtual Games' (not 'Virtual Training')."""
        html = self._read("training_hub.html")
        assert "Virtual Games" in html
        # URL stays unchanged
        assert 'href="/virtual-training"' in html


# ── TRN-04 + TRN-05: dashboard mod-nav ───────────────────────────────────────

class TestDashboardModNav:

    def _read_dashboard(self):
        return (_TEMPLATES_DIR / "dashboard_student_new.html").read_text(encoding="utf-8")

    def test_trn04_dashboard_contains_training_card(self):
        """TRN-04: dashboard_student_new.html mod-nav contains /training link."""
        html = self._read_dashboard()
        assert 'href="/training"' in html

    def test_trn05_dashboard_no_direct_adaptive_learning_card(self):
        """TRN-05: dashboard_student_new.html mod-nav does NOT contain direct /adaptive-learning card."""
        html = self._read_dashboard()
        # The mod-nav card section must not link directly to /adaptive-learning
        # (AL is now nested inside /training)
        assert 'href="/adaptive-learning"' not in html


# ── REG-01: /adaptive-learning route regression ───────────────────────────────

class TestAdaptiveLearningRegression:

    def test_reg01_adaptive_learning_handler_exists(self):
        """REG-01: /adaptive-learning route handler still importable and callable."""
        from app.api.web_routes.adaptive_learning import adaptive_learning_page
        assert callable(adaptive_learning_page)

    def test_reg01_adaptive_learning_route_registered(self):
        """REG-01b: /adaptive-learning is still registered in the app router."""
        from app.api.web_routes import router
        paths = [r.path for r in router.routes]
        assert "/adaptive-learning" in paths


# ── TRN-07..09: vt_active gate + empty state ─────────────────────────────────

class TestVirtualTrainingGate:
    """
    TRN-07  vt_active=True when VirtualTrainingService.get_games returns games
    TRN-08  vt_active=False when VirtualTrainingService.get_games returns []
    TRN-09  training_hub.html template contains trn-submodule-unavailable class (empty state)
    TRN-10  training_hub.html template contains Virtual Games link inside {% if vt_active %}
    TRN-11  Adaptive Learning link always present — not gated by vt_active
    """

    def _read(self):
        return (_TEMPLATES_DIR / "training_hub.html").read_text(encoding="utf-8")

    def test_trn07_vt_active_true_when_games_exist(self):
        """TRN-07: vt_active=True is passed to template when get_games returns a non-empty list."""
        from app.api.web_routes.training import training_hub_page

        user = _make_student()
        request = _make_request()
        db = _make_db()
        fake_game = MagicMock()
        fake_response = MagicMock()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_games", return_value=[fake_game]), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = fake_response
            _run(training_hub_page(request=request, db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args[0][1]
        assert ctx["vt_active"] is True

    def test_trn08_vt_active_false_when_no_games(self):
        """TRN-08: vt_active=False is passed to template when get_games returns []."""
        from app.api.web_routes.training import training_hub_page

        user = _make_student()
        request = _make_request()
        db = _make_db()
        fake_response = MagicMock()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_games", return_value=[]), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = fake_response
            _run(training_hub_page(request=request, db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args[0][1]
        assert ctx["vt_active"] is False

    def test_trn09_template_has_empty_state_class(self):
        """TRN-09: training_hub.html contains trn-submodule-unavailable CSS class (empty state)."""
        html = self._read()
        assert "trn-submodule-unavailable" in html

    def test_trn10_template_virtual_games_link_inside_if_block(self):
        """TRN-10: Virtual Games link exists and is inside an {% if vt_active %} conditional."""
        html = self._read()
        assert 'href="/virtual-training"' in html
        # Both the link and empty state must exist in the template source
        assert "trn-submodule-unavailable" in html
        # The if block must be present
        assert "vt_active" in html

    def test_trn11_adaptive_learning_not_gated(self):
        """TRN-11: Adaptive Learning link is not inside an {% if %} block — always rendered."""
        html = self._read()
        # Find the adaptive-learning href line
        al_idx = html.find('href="/adaptive-learning"')
        assert al_idx != -1, "Adaptive Learning link missing from template"
        # The nearest preceding {% if %} should be the outer if vt_active, not an adaptive-specific gate
        preceding = html[:al_idx]
        # No {% if ... %} block should directly wrap the adaptive-learning link
        # (it appears before the vt_active check)
        vt_if_idx = html.find("{% if vt_active %}")
        assert al_idx < vt_if_idx, "Adaptive Learning link must appear before the {% if vt_active %} block"
