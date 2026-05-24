"""
PP — Public Profile dashboard entry point tests.

Covers:
  spec_dashboard() route context for LFA_FOOTBALL_PLAYER (PP-01..06)
  dashboard_student_new.html Public Profile section (PP-07..09)
  lfa_public_profile_editor.html back-link (PP-10)
  player_profile.html Edit Grid CTA visibility (PP-11..12)

All tests use MagicMock — no real DB or HTTP server required.

Test list:
  TestSpecDashboardContext   PP-01  context has public_profile_url for LFA_FOOTBALL_PLAYER
                             PP-02  public_profile_url == /players/{user.id}
                             PP-03  grid_editor_url == /dashboard/lfa-football-player/public-profile-editor
                             PP-04  is_profile_published reflects CardDraftService.is_published()
                             PP-05  empty published grid → profile_grid_filled_slots == 0
                             PP-06  non-LFA spec → public_profile_url is None in context
  TestDashboardTemplate      PP-07  dashboard_student_new.html contains Public Profile section
                             PP-08  View Profile link uses /players/ URL pattern
                             PP-09  Edit Grid link targets public-profile-editor
  TestEditorTemplate         PP-10  lfa_public_profile_editor.html contains ← Dashboard link
  TestPublicProfileTemplate  PP-11  anonymous view → Edit Grid button absent from non-own-profile block
                             PP-12  own_profile state → Edit Grid button present
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.api.web_routes.dashboard import spec_dashboard
from app.models.user import UserRole

# ── Module-level patch base ────────────────────────────────────────────────────

_BASE = "app.api.web_routes.dashboard"

# ── Template fixtures ──────────────────────────────────────────────────────────

_TMPL_DIR    = Path(__file__).parent.parent.parent / "app" / "templates"
_DASH_HTML   = (_TMPL_DIR / "dashboard_student_new.html").read_text(encoding="utf-8")
_EDITOR_HTML = (_TMPL_DIR / "dashboard" / "lfa_public_profile_editor.html").read_text(encoding="utf-8")
_PLAYER_HTML = (_TMPL_DIR / "public" / "player_profile.html").read_text(encoding="utf-8")

# ── Helpers ────────────────────────────────────────────────────────────────────

def _student(uid: int = 77):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    u.date_of_birth = None
    return u


def _req():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


def _make_db(license_obj, *, active_enrollment: bool = False):
    """Build a minimal db mock for spec_dashboard with LFA_FOOTBALL_PLAYER spec."""
    db = MagicMock()
    # .first() side_effect sequence:
    #   1 = user_license
    #   2 = has_enrollment (onboarding guard) — None → False, triggers onboarding_completed check
    #   3 = has_active_enrollment
    first_returns = [license_obj, None]
    first_returns.append(MagicMock() if active_enrollment else None)
    if active_enrollment:
        enrollment_obj = MagicMock()
        enrollment_obj.semester = MagicMock()
        first_returns.append(enrollment_obj)
    db.query.return_value.filter.return_value.first.side_effect = first_returns
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    db.query.return_value.filter.return_value.all.return_value = []
    return db


def _lfa_license(uid: int = 77):
    lic = MagicMock()
    lic.id = 1
    lic.user_id = uid
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.onboarding_completed = True
    lic.football_skills = None
    lic.public_card_platform = None
    return lic


# ── PP-01..06: spec_dashboard() context ───────────────────────────────────────

class TestSpecDashboardContext:

    def _run_lfa(self, user, db, card_draft, is_published_val: bool, pub_grid):
        with (
            patch(f"{_BASE}.templates") as mock_tmpl,
            patch(f"{_BASE}._CardDraftService") as mock_cds,
            patch(f"{_BASE}._build_published_grid_state", return_value=pub_grid),
        ):
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            mock_cds.get_player_card_draft.return_value = card_draft
            mock_cds.is_published.return_value = is_published_val
            _run(spec_dashboard(request=_req(), spec_type="lfa-football-player", db=db, user=user))
            return mock_tmpl.TemplateResponse.call_args.args[1]

    def test_pp_01_lfa_context_has_public_profile_url(self):
        """PP-01: spec_dashboard for LFA_FOOTBALL_PLAYER passes public_profile_url to template."""
        user = _student(uid=77)
        lic  = _lfa_license(uid=77)
        db   = _make_db(lic)
        draft = MagicMock()
        draft.published_data = {}
        ctx = self._run_lfa(user, db, draft, False, None)
        assert "public_profile_url" in ctx

    def test_pp_02_public_profile_url_contains_user_id(self):
        """PP-02: public_profile_url == /players/{user.id}."""
        user = _student(uid=99)
        lic  = _lfa_license(uid=99)
        db   = _make_db(lic)
        draft = MagicMock()
        draft.published_data = {}
        ctx = self._run_lfa(user, db, draft, False, None)
        assert ctx["public_profile_url"] == f"/players/{user.id}"

    def test_pp_03_grid_editor_url_is_correct(self):
        """PP-03: grid_editor_url == /dashboard/lfa-football-player/public-profile-editor."""
        user = _student(uid=77)
        lic  = _lfa_license(uid=77)
        db   = _make_db(lic)
        draft = MagicMock()
        draft.published_data = {}
        ctx = self._run_lfa(user, db, draft, False, None)
        assert ctx["grid_editor_url"] == "/dashboard/lfa-football-player/public-profile-editor"

    def test_pp_04_is_profile_published_reflects_service(self):
        """PP-04: is_profile_published mirrors CardDraftService.is_published() return value."""
        user = _student(uid=77)
        lic  = _lfa_license(uid=77)
        db   = _make_db(lic)
        draft = MagicMock()
        draft.published_data = {}
        ctx = self._run_lfa(user, db, draft, True, None)
        assert ctx["is_profile_published"] is True

    def test_pp_05_empty_published_grid_gives_zero_filled_slots(self):
        """PP-05: build_published_grid_state returns None → profile_grid_filled_slots == 0."""
        user = _student(uid=77)
        lic  = _lfa_license(uid=77)
        db   = _make_db(lic)
        draft = MagicMock()
        draft.published_data = {}
        ctx = self._run_lfa(user, db, draft, False, None)
        assert ctx["profile_grid_filled_slots"] == 0

    def test_pp_06_non_lfa_spec_public_profile_url_is_none(self):
        """PP-06: non-LFA specialization → public_profile_url is None (feature not active)."""
        user = _student(uid=77)
        lic  = MagicMock()
        lic.id = 2
        lic.specialization_type = "GANCUJU_PLAYER"
        lic.onboarding_completed = True
        lic.football_skills = None
        lic.public_card_platform = None
        db   = _make_db(lic)

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(spec_dashboard(request=_req(), spec_type="gancuju-player", db=db, user=user))
            ctx = mock_tmpl.TemplateResponse.call_args.args[1]

        assert ctx["public_profile_url"] is None


# ── PP-07..09: dashboard_student_new.html template content ────────────────────

class TestDashboardTemplate:

    def test_pp_07_dashboard_contains_public_profile_section(self):
        """PP-07: dashboard_student_new.html has a Public Profile section block."""
        assert "Public Profile" in _DASH_HTML
        assert 's-pp-section' in _DASH_HTML

    def test_pp_08_view_profile_link_uses_players_url(self):
        """PP-08: 'View Profile ↗' anchor href contains /players/ path."""
        assert "View Profile ↗" in _DASH_HTML
        assert "/players/" in _DASH_HTML

    def test_pp_09_edit_grid_link_targets_editor(self):
        """PP-09: 'Edit Grid' anchor href uses grid_editor_url context variable."""
        assert "Edit Grid" in _DASH_HTML
        assert "grid_editor_url" in _DASH_HTML


# ── PP-10: lfa_public_profile_editor.html back-link ──────────────────────────

class TestEditorTemplate:

    def test_pp_10_editor_has_dashboard_back_link(self):
        """PP-10: lfa_public_profile_editor.html contains a ← Dashboard link."""
        assert "← Dashboard" in _EDITOR_HTML
        assert "/dashboard/lfa-football-player" in _EDITOR_HTML


# ── PP-11..12: player_profile.html Edit Grid CTA visibility ──────────────────

class TestPublicProfileTemplate:

    def test_pp_11_edit_grid_button_guarded_by_own_profile_state(self):
        """PP-11: Edit Grid CTA is inside the own_profile Jinja2 branch, not rendered for others."""
        # The button must be inside {% elif fp.state == "own_profile" %} block — verify the
        # template does NOT show it unconditionally (it appears after own_profile check).
        idx_own  = _PLAYER_HTML.index('fp.state == "own_profile"')
        idx_grid = _PLAYER_HTML.index("Edit Grid")
        # Edit Grid button must appear AFTER the own_profile guard line
        assert idx_grid > idx_own

    def test_pp_12_own_profile_state_block_contains_edit_grid(self):
        """PP-12: The own_profile Jinja2 block in player_profile.html contains the Edit Grid link."""
        own_profile_block_start = _PLAYER_HTML.index('fp.state == "own_profile"')
        # Grab the next ~300 chars to confirm Edit Grid is in this block
        block_excerpt = _PLAYER_HTML[own_profile_block_start:own_profile_block_start + 400]
        assert "Edit Grid" in block_excerpt
        assert "/dashboard/lfa-football-player/public-profile-editor" in block_excerpt
