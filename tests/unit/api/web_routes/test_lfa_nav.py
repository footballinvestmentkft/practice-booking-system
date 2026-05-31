"""LFA spec navigation context tests — NAV-01..NAV-08.

NAV-01  /my-cards route passes explicit spec_dashboard_url (multi-spec safe)
NAV-02  /my-cards route passes explicit spec_profile_url (multi-spec safe)
NAV-03  /dashboard/lfa-football-player route passes explicit spec_dashboard_url
NAV-04  /dashboard/lfa-football-player route passes explicit spec_profile_url
NAV-05  spec_subpage_hdr.html renders quicknav when _lfa_qn via spec_dashboard_url
NAV-06  spec_subpage_hdr.html renders quicknav when _lfa_qn via user.specialization
NAV-07  spec_subpage_hdr.html does NOT render quicknav for GānCuju spec
NAV-08  dashboard_card_editor.html back link points to /my-cards (not /profile/lfa-football-player)
"""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

_TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _make_lfa_student(user_id=1):
    from app.models.user import UserRole
    u = MagicMock()
    u.id = user_id
    u.role = UserRole.STUDENT
    u.onboarding_completed = True
    u.credit_balance = 0
    u.specialization = MagicMock()
    u.specialization.value = "LFA_FOOTBALL_PLAYER"
    return u


def _make_gancuju_student(user_id=2):
    from app.models.user import UserRole
    u = MagicMock()
    u.id = user_id
    u.role = UserRole.STUDENT
    u.onboarding_completed = True
    u.credit_balance = 0
    u.specialization = MagicMock()
    u.specialization.value = "GANCUJU_PLAYER"
    return u


def _make_request(path="/my-cards"):
    r = MagicMock()
    r.url.path = path
    return r


def _make_db():
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.filter_by.return_value = q
    q.first.return_value = None
    q.all.return_value = []
    q.count.return_value = 0
    q.limit.return_value = q
    q.order_by.return_value = q
    db.query.return_value = q
    return db


# ── NAV-01..02: /my-cards explicit spec context ────────────────────────────────

class TestMyCardsSpecContext:

    def _call_my_cards(self):
        from app.api.web_routes.my_cards import my_cards_hub

        user = _make_lfa_student()
        db   = _make_db()
        request = _make_request("/my-cards")
        request.query_params.get = lambda k, default=None: default
        captured = {}

        def capture(*args, **kwargs):
            captured.update(args[1] if len(args) > 1 else kwargs.get("context", {}))
            resp = MagicMock()
            resp.status_code = 200
            return resp

        free_design = MagicMock()
        free_design.id = "fclassic"
        free_design.label = "FClassic Player"
        free_design.credit_cost = 0
        free_design.is_premium = False

        with patch("app.api.web_routes.my_cards.get_all_designs", return_value=[free_design]), \
             patch("app.api.web_routes.my_cards.is_design_accessible", return_value=False), \
             patch("app.api.web_routes.my_cards.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.side_effect = capture
            _run(my_cards_hub(request=request, db=db, user=user))

        return captured

    def test_nav01_my_cards_passes_spec_dashboard_url(self):
        """NAV-01: /my-cards route passes spec_dashboard_url='/dashboard/lfa-football-player'."""
        ctx = self._call_my_cards()
        assert ctx.get("spec_dashboard_url") == "/dashboard/lfa-football-player"

    def test_nav02_my_cards_passes_spec_profile_url(self):
        """NAV-02: /my-cards route passes spec_profile_url='/profile/lfa-football-player'."""
        ctx = self._call_my_cards()
        assert ctx.get("spec_profile_url") == "/profile/lfa-football-player"


# ── NAV-03..04: /dashboard/lfa-football-player explicit spec context ───────────

_DASHBOARD_PY = (
    Path(__file__).resolve().parents[4] / "app" / "api" / "web_routes" / "dashboard.py"
)


class TestDashboardSpecContext:

    def _read_dashboard_route(self):
        return _DASHBOARD_PY.read_text(encoding="utf-8")

    def test_nav03_dashboard_passes_spec_dashboard_url(self):
        """NAV-03: dashboard.py TemplateResponse context contains spec_dashboard_url key."""
        src = self._read_dashboard_route()
        assert '"spec_dashboard_url": "/dashboard/lfa-football-player"' in src

    def test_nav04_dashboard_passes_spec_profile_url(self):
        """NAV-04: dashboard.py TemplateResponse context contains spec_profile_url key."""
        src = self._read_dashboard_route()
        assert '"spec_profile_url": "/profile/lfa-football-player"' in src


# ── NAV-05..07: spec_subpage_hdr.html quicknav rendering ─────────────────────

class TestSpecSubpageHdrTemplate:

    def _read(self):
        return (_TEMPLATES_DIR / "includes" / "spec_subpage_hdr.html").read_text(encoding="utf-8")

    def test_nav05_quicknav_triggered_by_spec_dashboard_url(self):
        """NAV-05: spec_subpage_hdr.html renders quicknav via spec_dashboard_url trigger."""
        html = self._read()
        # The second branch of _lfa_qn checks spec_dashboard_url
        assert "(spec_dashboard_url | default('')) == '/dashboard/lfa-football-player'" in html

    def test_nav06_quicknav_triggered_by_user_specialization(self):
        """NAV-06: spec_subpage_hdr.html renders quicknav via user.specialization trigger."""
        html = self._read()
        assert "LFA_FOOTBALL_PLAYER" in html
        assert "spec-quicknav" in html

    def test_nav07_quicknav_gated_by_lfa_qn(self):
        """NAV-07: spec_subpage_hdr.html quicknav <nav> element is inside {% if _lfa_qn %} — not rendered for GānCuju."""
        html = self._read()
        assert "{% if _lfa_qn %}" in html
        # The <nav> element must appear after the gate, not the CSS .spec-quicknav selector
        gate_idx = html.index("{% if _lfa_qn %}")
        nav_elem_idx = html.index('<nav class="spec-quicknav"')
        assert nav_elem_idx > gate_idx, "<nav class='spec-quicknav'> must appear after {% if _lfa_qn %} gate"


# ── NAV-08: card editor back link ─────────────────────────────────────────────

class TestCardEditorBackLink:

    def _read_editor(self):
        return (_TEMPLATES_DIR / "dashboard_card_editor.html").read_text(encoding="utf-8")

    def test_nav08_card_editor_back_link_points_to_my_cards(self):
        """NAV-08: dashboard_card_editor.html back link is /my-cards (not /profile/lfa-football-player)."""
        html = self._read_editor()
        assert 'href="/my-cards"' in html
        assert 'href="/profile/lfa-football-player"' not in html


# ── NAV bonus: dashboard template uses spec_subpage_hdr include ───────────────

class TestDashboardHeaderUnification:

    def _read_dashboard(self):
        return (_TEMPLATES_DIR / "dashboard_student_new.html").read_text(encoding="utf-8")

    def test_dashboard_header_uses_spec_subpage_hdr_include(self):
        """dashboard_student_new.html student_header block uses spec_subpage_hdr include."""
        html = self._read_dashboard()
        assert 'includes/spec_subpage_hdr.html' in html

    def test_dashboard_header_no_redundant_student_nav(self):
        """dashboard_student_new.html no longer has redundant student-nav element."""
        html = self._read_dashboard()
        # The old inline student-nav with hardcoded LFA Player link should be gone
        assert '<nav class="student-nav"' not in html
        assert 'href="/dashboard/lfa-football-player" class="nav-item active"' not in html
