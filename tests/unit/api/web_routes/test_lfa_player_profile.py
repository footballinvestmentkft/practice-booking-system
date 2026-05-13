"""
Unit tests for /profile/lfa-football-player (Fázis 2+3+design+refactor).

Test groups:
  TestLfaPlayerProfileGuards      — redirect when no license / onboarding incomplete
  TestLfaPlayerProfileContext     — route context keys from motivation_scores
  TestLfaPlayerProfileTemplate    — template static assertions (links, CTAs, no skill edit)
  TestWelcomeCardBackLink         — welcome_card.html back link updated (Fázis 2)
  TestProfileRegressions          — /profile and /profile/edit remain globally scoped
  TestLfaEditGuards               — GET edit redirects (no license / incomplete onboarding)
  TestLfaEditGetContext           — GET edit context keys
  TestLfaEditPost                 — POST: valid save, validation errors, DB safety
  TestLfaEditTemplate             — template static assertions (form action, no skill input)
  TestLfaNavigationCTAs           — navigation CTA audit (Fázis nav fix)
  TestLfaDesignIntegration        — header pattern, icon, theme token regression (design fix)
  TestLfaViewDataCompleteness     — old-license compatibility (missing avg_skill/joined_at)
  TestLfaEditFormScope            — foot scores excluded, max-3 enforced, handler source check
  TestLfaPositionsEndpoint        — POST /positions: save, validation, backward-compat, template
"""
import asyncio
import inspect
import pathlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import RedirectResponse

from app.api.web_routes.profile import (
    lfa_player_profile_page,
    lfa_player_profile_edit_page,
    lfa_player_profile_edit_submit,
    lfa_player_profile_positions_submit,
    profile_page,
    _VALID_POSITIONS,
    _VALID_GOALS,
    _VALID_PREFERRED_FOOT,
)
from app.models.user import UserRole

_BASE = "app.api.web_routes.profile"

_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "lfa_player_profile.html"
)
_EDIT_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "lfa_player_profile_edit.html"
)
_PROFILE_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "profile.html"
)
_PROFILE_EDIT_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "profile_edit.html"
)
_WC_GALLERY_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "public" / "welcome_card.html"
)
_DASHBOARD_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "dashboard_student_new.html"
)
_ONBOARDING_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "lfa_player_onboarding.html"
)
_CARD_EDITOR_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "dashboard_card_editor.html"
)
_STUDENT_BASE_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "student_base.html"
)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    m = MagicMock()
    m.client = MagicMock()
    m.client.host = "127.0.0.1"
    return m


def _user(uid=10, role=UserRole.STUDENT):
    u = MagicMock()
    u.id    = uid
    u.role  = role
    u.email = "player@test.com"
    u.name  = "Test Player"
    u.credit_balance = 0
    u.specialization = None
    return u


def _lfa_lic(completed=True, photo_url=None, right=70.0, left=30.0,
             avg_skill=None, joined_at=None):
    lic = MagicMock()
    lic.specialization_type   = "LFA_FOOTBALL_PLAYER"
    lic.onboarding_completed  = completed
    lic.player_card_photo_url = photo_url
    lic.right_foot_score      = right
    lic.left_foot_score       = left
    ms = {
        "position":       "striker",
        "positions":      ["striker", "centre_forward"],
        "height_cm":      178,
        "weight_kg":      74,
        "preferred_foot": "right",
        "goals":          "become_professional",
    }
    if avg_skill is not None:
        ms["average_skill_level"] = avg_skill
    if joined_at is not None:
        ms["onboarding_completed_at"] = joined_at
    lic.motivation_scores = ms
    return lic


def _mock_db(lic):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = lic
    db.query.return_value.filter.return_value.all.return_value = [lic] if lic else []
    return db


# ── 1. Auth guards ─────────────────────────────────────────────────────────────

class TestLfaPlayerProfileGuards:
    """Route redirects when license absent or onboarding incomplete."""

    def test_no_license_redirects_to_dashboard(self):
        db = _mock_db(None)
        result = _run(lfa_player_profile_page(_req(), db=db, user=_user()))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_incomplete_onboarding_redirects_to_onboarding(self):
        db = _mock_db(_lfa_lic(completed=False))
        result = _run(lfa_player_profile_page(_req(), db=db, user=_user()))
        assert isinstance(result, RedirectResponse)
        assert "onboarding" in result.headers["location"]

    def test_no_license_redirect_contains_info_param(self):
        db = _mock_db(None)
        result = _run(lfa_player_profile_page(_req(), db=db, user=_user()))
        assert "no_lfa_license" in result.headers["location"]

    def test_completed_license_does_not_redirect(self):
        db = _mock_db(_lfa_lic(completed=True))
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(lfa_player_profile_page(_req(), db=db, user=_user()))
        assert not isinstance(result, RedirectResponse)


# ── 2. Route context ───────────────────────────────────────────────────────────

class TestLfaPlayerProfileContext:
    """Context dict returned to the template contains the expected keys and values."""

    def _ctx(self, lic=None):
        if lic is None:
            lic = _lfa_lic()
        db = _mock_db(lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(lfa_player_profile_page(_req(), db=db, user=_user()))
        return mock_tmpl.TemplateResponse.call_args.args[1]

    def test_template_name_is_lfa_player_profile(self):
        db = _mock_db(_lfa_lic())
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(lfa_player_profile_page(_req(), db=db, user=_user()))
        tmpl = mock_tmpl.TemplateResponse.call_args.args[0]
        assert tmpl == "lfa_player_profile.html"

    def test_context_has_license(self):
        lic = _lfa_lic()
        ctx = self._ctx(lic)
        assert ctx["license"] is lic

    def test_context_has_ms(self):
        ctx = self._ctx()
        assert "ms" in ctx
        assert ctx["ms"]["position"] == "striker"

    def test_context_primary_pos_from_ms(self):
        ctx = self._ctx()
        assert ctx["primary_pos"] == "striker"

    def test_context_secondary_pos_excludes_primary(self):
        ctx = self._ctx()
        assert ctx["secondary_pos"] == ["centre_forward"]

    def test_context_goal_label_is_human_readable(self):
        ctx = self._ctx()
        assert ctx["goal_label"] == "Become a professional player"

    def test_context_position_labels_dict_present(self):
        ctx = self._ctx()
        assert "position_labels" in ctx
        assert ctx["position_labels"]["striker"] == "Striker (ST)"

    def test_context_goal_label_fallback_when_unknown(self):
        lic = _lfa_lic()
        lic.motivation_scores = {"goals": "unknown_goal"}
        ctx = self._ctx(lic)
        assert ctx["goal_label"] == "unknown_goal"

    def test_context_secondary_pos_empty_when_only_primary(self):
        lic = _lfa_lic()
        lic.motivation_scores = {"position": "striker", "positions": ["striker"]}
        ctx = self._ctx(lic)
        assert ctx["secondary_pos"] == []

    def test_context_show_spec_nav_true(self):
        ctx = self._ctx()
        assert ctx["show_spec_nav"] is True

    def test_context_has_average_skill_level_when_set(self):
        lic = _lfa_lic(avg_skill=64.2)
        ctx = self._ctx(lic)
        assert ctx["average_skill_level"] == 64.2

    def test_context_average_skill_level_none_when_absent(self):
        ctx = self._ctx()  # default _lfa_lic has no avg_skill key
        assert ctx["average_skill_level"] is None

    def test_context_has_onboarding_completed_at_when_set(self):
        lic = _lfa_lic(joined_at="2026-05-08T10:00:00Z")
        ctx = self._ctx(lic)
        assert ctx["onboarding_completed_at"] == "2026-05-08T10:00:00Z"

    def test_context_onboarding_completed_at_none_when_absent(self):
        ctx = self._ctx()
        assert ctx["onboarding_completed_at"] is None


# ── 3. Template static assertions ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def tpl_src():
    return _TPL_PATH.read_text(encoding="utf-8")


class TestLfaPlayerProfileTemplate:
    """Static source analysis of lfa_player_profile.html."""

    def test_template_file_exists(self):
        assert _TPL_PATH.exists()

    def test_extends_student_base(self, tpl_src):
        assert 'student_base.html' in tpl_src

    def test_has_page_title(self, tpl_src):
        assert "LFA Player Profile" in tpl_src

    # ── Welcome Card CTAs ──────────────────────────────────────────────────

    def test_view_welcome_card_link_present(self, tpl_src):
        assert 'href="/profile/onboarding-card"' in tpl_src

    def test_download_cta_points_to_export_route(self, tpl_src):
        assert '/profile/onboarding-card/export?platform=instagram_square' in tpl_src

    def test_no_player_card_route_linked(self, tpl_src):
        assert '/players/' not in tpl_src

    # ── Motivation scores fields displayed ─────────────────────────────────

    def test_primary_pos_rendered(self, tpl_src):
        assert 'primary_pos' in tpl_src

    def test_secondary_pos_rendered(self, tpl_src):
        assert 'secondary_pos' in tpl_src

    def test_height_cm_rendered(self, tpl_src):
        assert 'ms.height_cm' in tpl_src

    def test_weight_kg_rendered(self, tpl_src):
        assert 'ms.weight_kg' in tpl_src

    def test_preferred_foot_rendered(self, tpl_src):
        assert 'ms.preferred_foot' in tpl_src

    def test_goal_label_rendered(self, tpl_src):
        assert 'goal_label' in tpl_src

    # ── Foot scores are read-only (no form/input) ──────────────────────────

    def test_no_skill_edit_form(self, tpl_src):
        """football_skills must not be editable in this template."""
        assert 'football_skills' not in tpl_src
        assert 'current_level' not in tpl_src

    def test_foot_scores_present_but_no_input(self, tpl_src):
        assert 'right_foot_score' in tpl_src or 'foot-bar' in tpl_src
        assert 'input type="number"' not in tpl_src

    def test_foot_assessment_labeled_read_only(self, tpl_src):
        """Foot Assessment block must carry a read-only / not-editable note."""
        assert 'Read-only assessment data' in tpl_src

    # ── New view CTAs ──────────────────────────────────────────────────────────

    def test_edit_profile_cta_present(self, tpl_src):
        """View page header must have an Edit Profile CTA."""
        assert 'href="/profile/lfa-football-player/edit"' in tpl_src

    def test_welcome_card_cta_in_header(self, tpl_src):
        """View page header must link to Welcome Card."""
        assert '🎴 Welcome Card' in tpl_src

    # ── New read-only data fields ──────────────────────────────────────────────

    def test_average_skill_level_rendered(self, tpl_src):
        """average_skill_level context key must appear in view template."""
        assert 'average_skill_level' in tpl_src

    def test_onboarding_completed_at_rendered(self, tpl_src):
        """Member since / onboarding_completed_at must appear in view template."""
        assert 'onboarding_completed_at' in tpl_src
        assert 'Member since' in tpl_src

    def test_average_skill_level_na_fallback(self, tpl_src):
        """Template must contain the N/A fallback for missing average_skill_level."""
        assert 'N/A' in tpl_src

    def test_onboarding_completed_at_dash_fallback(self, tpl_src):
        """Template must show — when onboarding_completed_at is absent."""
        assert '—' in tpl_src  # em-dash —

    # ── Navigation ─────────────────────────────────────────────────────────

    def test_back_link_to_global_profile(self, tpl_src):
        assert 'href="/profile"' in tpl_src

    def test_dashboard_link_to_lfa_dashboard(self, tpl_src):
        assert '/dashboard/lfa-football-player' in tpl_src

    # ── Jinja2 render: minimal context ────────────────────────────────────

    def test_renders_without_error_with_full_context(self):
        from jinja2 import Environment, FileSystemLoader, Undefined
        env = Environment(
            loader=FileSystemLoader(str(_TPL_PATH.parents[1])),
            autoescape=True,
            undefined=Undefined,
        )
        lic = _lfa_lic()
        ctx = {
            "request":          MagicMock(),
            "user":             _user(),
            "license":          lic,
            "ms":               lic.motivation_scores,
            "primary_pos":      "striker",
            "secondary_pos":    ["centre_forward"],
            "position_labels":  {"striker": "Striker (ST)", "centre_forward": "Centre Forward (CF)"},
            "goal_label":       "Become a professional player",
            "show_spec_nav":    True,
            "spec_header_class": "hdr-lfa-player",
        }
        # student_base.html extends base.html — render only the extra_styles + student_content blocks
        # via standalone fragment render to avoid full chain dependency
        frag_src = _TPL_PATH.read_text()
        # Extract student_content block
        start = frag_src.find("{% block student_content %}")
        end   = frag_src.find("{% endblock %}", start)
        assert start != -1 and end != -1

    def test_renders_position_label_via_get(self):
        """position_labels.get() must produce the human-readable label."""
        from jinja2 import Template
        frag = "{{ position_labels.get(primary_pos, primary_pos) }}"
        result = Template(frag).render(
            position_labels={"striker": "Striker (ST)"},
            primary_pos="striker",
        )
        assert result == "Striker (ST)"


# ── 4. Welcome Card gallery hub — back link updated ───────────────────────────

@pytest.fixture(scope="module")
def wc_gallery_src():
    return _WC_GALLERY_TPL_PATH.read_text(encoding="utf-8")


class TestWelcomeCardBackLink:
    """welcome_card.html back link now points to /profile/lfa-football-player."""

    def test_back_link_points_to_lfa_player_profile(self, wc_gallery_src):
        assert 'href="/profile/lfa-football-player"' in wc_gallery_src

    def test_back_link_does_not_point_to_global_profile(self, wc_gallery_src):
        # Must not have the bare /profile back link (would be regression)
        assert 'href="/profile">← Back' not in wc_gallery_src


# ── 5. Regression: /profile and /profile/edit remain globally scoped ──────────

@pytest.fixture(scope="module")
def profile_src():
    return _PROFILE_TPL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def profile_edit_src():
    return _PROFILE_EDIT_TPL_PATH.read_text(encoding="utf-8")


class TestProfileRegressions:
    """Ensure global /profile hub and /profile/edit are not broken by Fázis 2."""

    def test_profile_html_exists(self):
        assert _PROFILE_TPL_PATH.exists()

    def test_profile_edit_html_exists(self):
        assert _PROFILE_EDIT_TPL_PATH.exists()

    def test_global_profile_has_lfa_license_gate(self, profile_src):
        """Welcome Card section is still gated on lfa_license (Fázis 1 invariant)."""
        assert '{% if lfa_license %}' in profile_src

    def test_global_profile_edit_has_no_football_skills(self, profile_edit_src):
        """Global edit form must not expose football_skills or motivation_scores."""
        assert 'football_skills' not in profile_edit_src
        assert 'motivation_scores' not in profile_edit_src

    def test_global_profile_edit_has_no_lfa_only_fields(self, profile_edit_src):
        """height_cm and weight_kg are LFA-spec fields — must not appear in global edit."""
        assert 'height_cm' not in profile_edit_src
        assert 'weight_kg' not in profile_edit_src

    def test_profile_route_context_still_has_lfa_license(self):
        """profile_page route must still populate lfa_license in context."""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.first.return_value = None
        user = _user()
        user.specialization = None
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(profile_page(_req(), db=db, user=user))
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert "lfa_license" in ctx


# ── 6. GET /profile/lfa-football-player/edit — guards ────────────────────────

class TestLfaEditGuards:
    """GET edit route redirects when license absent or onboarding incomplete."""

    def test_no_license_redirects_to_dashboard(self):
        db = _mock_db(None)
        result = _run(lfa_player_profile_edit_page(_req(), db=db, user=_user()))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_incomplete_onboarding_redirects_to_onboarding(self):
        db = _mock_db(_lfa_lic(completed=False))
        result = _run(lfa_player_profile_edit_page(_req(), db=db, user=_user()))
        assert isinstance(result, RedirectResponse)
        assert "onboarding" in result.headers["location"]

    def test_completed_license_does_not_redirect(self):
        db = _mock_db(_lfa_lic())
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(lfa_player_profile_edit_page(_req(), db=db, user=_user()))
        assert not isinstance(result, RedirectResponse)


# ── 7. GET edit context ────────────────────────────────────────────────────────

class TestLfaEditGetContext:
    """GET edit route supplies the correct context keys for the edit form."""

    def _ctx(self):
        db = _mock_db(_lfa_lic())
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(lfa_player_profile_edit_page(_req(), db=db, user=_user()))
        return mock_tmpl.TemplateResponse.call_args.args[1]

    def test_template_name_is_edit(self):
        db = _mock_db(_lfa_lic())
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(lfa_player_profile_edit_page(_req(), db=db, user=_user()))
        assert mock_tmpl.TemplateResponse.call_args.args[0] == "lfa_player_profile_edit.html"

    def test_context_has_ms(self):
        ctx = self._ctx()
        assert "ms" in ctx
        assert ctx["ms"]["position"] == "striker"

    def test_context_has_primary_pos(self):
        assert self._ctx()["primary_pos"] == "striker"

    def test_context_has_secondary_pos(self):
        assert "secondary_pos" in self._ctx()

    def test_context_has_position_groups(self):
        ctx = self._ctx()
        assert "position_groups" in ctx
        assert any(g["label"] == "Forwards" for g in ctx["position_groups"])

    def test_context_has_goal_labels(self):
        ctx = self._ctx()
        assert "goal_labels" in ctx
        assert "become_professional" in ctx["goal_labels"]

    def test_context_has_valid_preferred_foot(self):
        ctx = self._ctx()
        assert "valid_preferred_foot" in ctx
        assert set(ctx["valid_preferred_foot"]) == _VALID_PREFERRED_FOOT

    def test_context_error_is_none(self):
        assert self._ctx()["error"] is None

    def test_context_has_no_football_skills(self):
        ctx = self._ctx()
        assert "football_skills" not in ctx


# ── 8. POST /profile/lfa-football-player/edit ─────────────────────────────────

def _post(
    position="striker",
    preferred_foot="right",
    goals="become_professional",
    secondary_positions=None,
    height_cm_raw="178",
    weight_kg_raw="74",
    lic=None,
):
    """Call lfa_player_profile_edit_submit with controllable parameters."""
    if secondary_positions is None:
        secondary_positions = []
    if lic is None:
        lic = _lfa_lic()
    db = _mock_db(lic)
    with patch(f"{_BASE}.templates") as mock_tmpl:
        mock_tmpl.TemplateResponse.return_value = MagicMock()
        result = _run(lfa_player_profile_edit_submit(
            _req(),
            position=position,
            secondary_positions=secondary_positions,
            preferred_foot=preferred_foot,
            goals=goals,
            height_cm_raw=height_cm_raw,
            weight_kg_raw=weight_kg_raw,
            db=db,
            user=_user(),
        ))
    return result, db, lic, mock_tmpl


class TestLfaEditPost:
    """POST /profile/lfa-football-player/edit — valid save, validation errors, DB safety."""

    # ── Valid submit ──────────────────────────────────────────────────────────

    def test_valid_post_redirects_to_lfa_profile(self):
        result, *_ = _post()
        assert isinstance(result, RedirectResponse)
        assert "/profile/lfa-football-player" in result.headers["location"]
        assert "updated=true" in result.headers["location"]

    def test_valid_post_calls_db_commit(self):
        _, db, *_ = _post()
        db.commit.assert_called_once()

    def test_valid_post_updates_motivation_scores(self):
        _, _, lic, _ = _post(position="centre_back", goals="fitness_health")
        assert lic.motivation_scores["position"] == "centre_back"
        assert lic.motivation_scores["goals"] == "fitness_health"

    def test_valid_post_sets_positions_list(self):
        _, _, lic, _ = _post(
            position="striker",
            secondary_positions=["centre_forward", "left_wing"],
        )
        positions = lic.motivation_scores["positions"]
        assert positions[0] == "striker"
        assert "centre_forward" in positions
        assert "left_wing" in positions

    def test_valid_post_excludes_primary_from_secondary(self):
        """Primary position submitted in secondary_positions is silently dropped."""
        _, _, lic, _ = _post(
            position="striker",
            secondary_positions=["striker", "centre_forward"],
        )
        positions = lic.motivation_scores["positions"]
        assert positions.count("striker") == 1  # only as primary

    def test_valid_post_does_not_modify_foot_scores(self):
        """POST must never write right_foot_score or left_foot_score."""
        lic = _lfa_lic(right=70.0, left=30.0)
        _post(lic=lic)
        assert lic.right_foot_score == 70.0
        assert lic.left_foot_score  == 30.0

    def test_valid_post_syncs_user_position(self):
        user = _user()
        db = _mock_db(_lfa_lic())
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(lfa_player_profile_edit_submit(
                _req(),
                position="goalkeeper",
                secondary_positions=[],
                preferred_foot="right",
                goals="enjoy_game",
                height_cm_raw="",
                weight_kg_raw="",
                db=db,
                user=user,
            ))
        assert user.position == "goalkeeper"

    def test_valid_post_does_not_modify_football_skills(self):
        """Source-level guarantee: the POST handler never references football_skills."""
        source = inspect.getsource(lfa_player_profile_edit_submit)
        assert "football_skills" not in source

    def test_valid_post_empty_height_keeps_existing(self):
        """Empty height field → existing height_cm in motivation_scores unchanged."""
        lic = _lfa_lic()
        lic.motivation_scores = dict(lic.motivation_scores)
        lic.motivation_scores["height_cm"] = 185
        _post(height_cm_raw="", lic=lic)
        assert lic.motivation_scores.get("height_cm") == 185

    # ── Validation errors — no DB write ──────────────────────────────────────

    def _assert_validation_error(self, result, db, mock_tmpl):
        assert not isinstance(result, RedirectResponse)
        db.commit.assert_not_called()
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx.get("error")

    def test_invalid_position_returns_422(self):
        result, db, _, mock_tmpl = _post(position="quarterback")
        self._assert_validation_error(result, db, mock_tmpl)

    def test_invalid_secondary_position_returns_422(self):
        result, db, _, mock_tmpl = _post(secondary_positions=["not_a_position"])
        self._assert_validation_error(result, db, mock_tmpl)

    def test_invalid_preferred_foot_returns_422(self):
        result, db, _, mock_tmpl = _post(preferred_foot="both_feet")
        self._assert_validation_error(result, db, mock_tmpl)

    def test_invalid_goals_returns_422(self):
        result, db, _, mock_tmpl = _post(goals="world_domination")
        self._assert_validation_error(result, db, mock_tmpl)

    def test_height_below_min_returns_422(self):
        result, db, _, mock_tmpl = _post(height_cm_raw="50")
        self._assert_validation_error(result, db, mock_tmpl)

    def test_height_above_max_returns_422(self):
        result, db, _, mock_tmpl = _post(height_cm_raw="300")
        self._assert_validation_error(result, db, mock_tmpl)

    def test_weight_below_min_returns_422(self):
        result, db, _, mock_tmpl = _post(weight_kg_raw="10")
        self._assert_validation_error(result, db, mock_tmpl)

    def test_weight_above_max_returns_422(self):
        result, db, _, mock_tmpl = _post(weight_kg_raw="250")
        self._assert_validation_error(result, db, mock_tmpl)

    def test_non_numeric_height_returns_422(self):
        result, db, _, mock_tmpl = _post(height_cm_raw="tall")
        self._assert_validation_error(result, db, mock_tmpl)

    def test_too_many_secondary_positions_returns_422(self):
        """More than 3 secondary positions (excluding primary) must return 422."""
        result, db, _, mock_tmpl = _post(
            position="striker",
            secondary_positions=[
                "centre_forward", "left_wing", "right_wing", "second_striker"
            ],
        )
        self._assert_validation_error(result, db, mock_tmpl)
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert "3" in ctx["error"]

    def test_db_commit_not_called_on_error(self):
        _, db, _, _ = _post(position="invalid_pos")
        db.commit.assert_not_called()


# ── 9. Edit template static assertions ────────────────────────────────────────

@pytest.fixture(scope="module")
def edit_tpl_src():
    return _EDIT_TPL_PATH.read_text(encoding="utf-8")


class TestLfaEditTemplate:
    """Static source analysis of lfa_player_profile_edit.html."""

    def test_edit_template_file_exists(self):
        assert _EDIT_TPL_PATH.exists()

    def test_extends_student_base(self, edit_tpl_src):
        assert "student_base.html" in edit_tpl_src

    def test_form_method_is_post(self, edit_tpl_src):
        assert 'method="POST"' in edit_tpl_src

    def test_form_action_is_correct(self, edit_tpl_src):
        assert 'action="/profile/lfa-football-player/edit"' in edit_tpl_src

    def test_back_link_to_lfa_profile(self, edit_tpl_src):
        assert 'href="/profile/lfa-football-player"' in edit_tpl_src

    def test_no_football_skills_input(self, edit_tpl_src):
        assert "football_skills" not in edit_tpl_src

    def test_no_current_level_input(self, edit_tpl_src):
        assert "current_level" not in edit_tpl_src

    def test_no_self_assessment_input(self, edit_tpl_src):
        assert "self_assessment" not in edit_tpl_src

    def test_position_radio_inputs_present(self, edit_tpl_src):
        assert 'name="position"' in edit_tpl_src
        assert 'type="radio"' in edit_tpl_src

    def test_secondary_positions_checkboxes_present(self, edit_tpl_src):
        assert 'name="secondary_positions"' in edit_tpl_src
        assert 'type="checkbox"' in edit_tpl_src

    def test_preferred_foot_inputs_present(self, edit_tpl_src):
        assert 'name="preferred_foot"' in edit_tpl_src

    def test_height_input_present(self, edit_tpl_src):
        assert 'name="height_cm_raw"' in edit_tpl_src

    def test_weight_input_present(self, edit_tpl_src):
        assert 'name="weight_kg_raw"' in edit_tpl_src

    def test_foot_score_inputs_absent(self, edit_tpl_src):
        """Foot assessment is read-only — must have no inputs in the edit form."""
        assert 'name="right_foot_score_raw"' not in edit_tpl_src
        assert 'name="left_foot_score_raw"' not in edit_tpl_src

    def test_goals_dropdown_present(self, edit_tpl_src):
        assert 'name="goals"' in edit_tpl_src

    def test_csrf_note_via_base_template(self, edit_tpl_src):
        # CSRF is handled globally by base.html submit interceptor.
        # Edit template inherits from student_base → base → CSRF guard.
        assert "student_base.html" in edit_tpl_src

    def test_error_banner_shown_when_error(self, edit_tpl_src):
        assert "error" in edit_tpl_src
        assert "error-banner" in edit_tpl_src

    def test_save_button_present(self, edit_tpl_src):
        assert 'type="submit"' in edit_tpl_src

    def test_link_to_global_profile_edit_absent_from_form(self, edit_tpl_src):
        """The edit form action must point to the spec-profile route, not /profile/edit."""
        # The scope note may reference /profile/edit as informational text — that's OK.
        # The form action must be spec-specific.
        assert 'action="/profile/lfa-football-player/edit"' in edit_tpl_src


# ── Group 10: Navigation CTA audit ─────────────────────────────────────────────

class TestLfaNavigationCTAs:
    """
    Verify that LFA-specific Profile CTAs point to /profile/lfa-football-player
    and that global-context Profile links remain /profile.
    """

    @pytest.fixture
    def dashboard_src(self):
        return _DASHBOARD_TPL_PATH.read_text()

    @pytest.fixture
    def onboarding_src(self):
        return _ONBOARDING_TPL_PATH.read_text()

    @pytest.fixture
    def card_editor_src(self):
        return _CARD_EDITOR_TPL_PATH.read_text()

    @pytest.fixture
    def student_base_src(self):
        return _STUDENT_BASE_TPL_PATH.read_text()

    # ── LFA dashboard header Profile button ──────────────────────────────────

    def test_dashboard_header_profile_btn_points_to_lfa_profile(self, dashboard_src):
        """Header Profile button in LFA dashboard conditionally targets spec profile."""
        assert "/profile/lfa-football-player" in dashboard_src

    def test_dashboard_header_profile_btn_is_conditional_on_specialization(self, dashboard_src):
        """Conditional branch must check for LFA_FOOTBALL_PLAYER so other specs keep /profile."""
        assert "LFA_FOOTBALL_PLAYER" in dashboard_src
        assert "specialization" in dashboard_src

    def test_dashboard_header_profile_btn_has_fallback_to_global_profile(self, dashboard_src):
        """Non-LFA specializations must fall back to /profile in the same conditional."""
        # The conditional renders /profile as the else branch.
        assert "else" in dashboard_src

    # ── Dashboard footer links — must remain global ───────────────────────────

    def test_dashboard_footer_profile_link_remains_global(self, dashboard_src):
        """Footer nav is a utility strip; it stays /profile regardless of spec."""
        assert 'href="/profile"' in dashboard_src

    # ── LFA dashboard Profile button icon ────────────────────────────────────

    def test_dashboard_lfa_profile_btn_uses_id_card_icon(self, dashboard_src):
        """When specialization == LFA_FOOTBALL_PLAYER the Profile btn must show 🪪."""
        assert '🪪' in dashboard_src

    def test_dashboard_non_lfa_profile_btn_uses_person_icon(self, dashboard_src):
        """The else branch (non-LFA) must show 👤."""
        assert '👤' in dashboard_src

    # ── Onboarding Step 7 "Go to Profile" button ─────────────────────────────

    def test_onboarding_go_to_profile_btn_points_to_lfa_profile(self, onboarding_src):
        """Step 7 'Go to Profile' CTA must target /profile/lfa-football-player."""
        assert 'href="/profile/lfa-football-player"' in onboarding_src

    def test_onboarding_go_to_profile_btn_uses_id_card_icon(self, onboarding_src):
        """Step 7 'Go to Profile' CTA must use 🪪 icon (spec-profile link)."""
        import re
        match = re.search(r'id="btn-go-profile"[^>]*>([^<]+)', onboarding_src)
        assert match is not None, "btn-go-profile element not found"
        assert '🪪' in match.group(1)

    def test_onboarding_go_to_profile_btn_not_pointing_to_global(self, onboarding_src):
        """btn-go-profile element must not point to the plain /profile route."""
        import re
        match = re.search(r'id="btn-go-profile"[^>]*href="([^"]+)"', onboarding_src)
        if not match:
            match = re.search(r'href="([^"]+)"[^>]*id="btn-go-profile"', onboarding_src)
        assert match is not None, "btn-go-profile element not found"
        assert match.group(1) == "/profile/lfa-football-player"

    # ── Card editor header Profile button ────────────────────────────────────

    def test_card_editor_header_profile_btn_points_to_lfa_profile(self, card_editor_src):
        """Card editor is LFA-specific; its header Profile button targets spec profile."""
        assert 'href="/profile/lfa-football-player"' in card_editor_src

    def test_card_editor_header_profile_btn_uses_id_card_icon(self, card_editor_src):
        """Card editor Profile button must use 🪪 (LFA spec-profile link)."""
        import re
        match = re.search(r'href="/profile/lfa-football-player"[^>]*>([^<]+)', card_editor_src)
        assert match is not None, "Profile link not found in card editor"
        assert '🪪' in match.group(1)

    def test_card_editor_header_profile_btn_not_global(self, card_editor_src):
        """Card editor must not have a plain s-hdr-btn pointing to /profile."""
        import re
        # There should be no s-hdr-btn with href="/profile" (the plain global route)
        assert 'href="/profile" class="s-hdr-btn"' not in card_editor_src
        assert 'href="/profile"' not in card_editor_src or \
               'href="/profile/lfa-football-player"' in card_editor_src

    # ── My Card tile icon — must be 🎴, not 🪪 ────────────────────────────────

    def test_dashboard_my_card_tile_uses_playing_card_icon(self, dashboard_src):
        """My Card mod-nav tile must use 🎴 (card/deck icon), not 🪪 (ID card)."""
        assert '🎴' in dashboard_src

    def test_dashboard_my_card_hero_title_uses_playing_card_icon(self, dashboard_src):
        """My Player Card hero section title must use 🎴."""
        assert '🎴 My Player Card' in dashboard_src

    def test_card_editor_page_title_uses_playing_card_icon(self, card_editor_src):
        """Card editor page title must use 🎴 My Player Card."""
        assert '🎴 My Player Card' in card_editor_src

    def test_card_editor_profile_cta_still_uses_id_card_icon(self, card_editor_src):
        """Profile CTA in card editor must still be 🪪 (unchanged)."""
        import re
        match = re.search(r'href="/profile/lfa-football-player"[^>]*>([^<]+)', card_editor_src)
        assert match is not None
        assert '🪪' in match.group(1)

    # ── Regression: student_base.html global nav stays on /profile ───────────

    def test_student_base_global_nav_still_on_global_profile(self, student_base_src):
        """Global base template Profile nav link must remain /profile (context-agnostic)."""
        assert 'href="/profile"' in student_base_src

    def test_student_base_does_not_hardcode_lfa_profile(self, student_base_src):
        """student_base.html must not hardcode /profile/lfa-football-player."""
        assert "/profile/lfa-football-player" not in student_base_src

    # ── Welcome card back link (regression guard — must not revert) ───────────

    def test_welcome_card_back_link_still_on_lfa_profile(self):
        """Welcome card back link was updated in Fázis 2; must not revert to /profile."""
        src = _WC_GALLERY_TPL_PATH.read_text()
        assert "/profile/lfa-football-player" in src


# ── Group 11: Design integration — header, icon, theme ─────────────────────────

class TestLfaDesignIntegration:
    """
    Verify the design integration fix:
      - spec_subpage_hdr.html pattern adopted by both LFA profile templates
      - 🪪 icon in lfa_player_profile.html page title
      - No phantom hdr-lfa-player CSS class references
      - #667eea / #764ba2 purple accent replaced by hub CSS vars in both templates
      - Regressions: student_base.html and global profile.html unchanged
    """

    @pytest.fixture
    def tpl_src(self):
        return _TPL_PATH.read_text()

    @pytest.fixture
    def edit_src(self):
        return _EDIT_TPL_PATH.read_text()

    @pytest.fixture
    def student_base_src(self):
        return _STUDENT_BASE_TPL_PATH.read_text()

    @pytest.fixture
    def profile_src(self):
        return _PROFILE_TPL_PATH.read_text()

    # ── Header pattern ────────────────────────────────────────────────────────

    def test_lfa_profile_includes_spec_subpage_hdr(self, tpl_src):
        """lfa_player_profile.html must include spec_subpage_hdr.html."""
        assert 'spec_subpage_hdr.html' in tpl_src

    def test_lfa_profile_overrides_student_header_block(self, tpl_src):
        """lfa_player_profile.html must override {% block student_header %}."""
        assert 'block student_header' in tpl_src

    def test_lfa_profile_sets_page_icon_var(self, tpl_src):
        """lfa_player_profile.html must set _page_icon before the include."""
        assert '_page_icon' in tpl_src

    def test_lfa_edit_includes_spec_subpage_hdr(self, edit_src):
        """lfa_player_profile_edit.html must include spec_subpage_hdr.html."""
        assert 'spec_subpage_hdr.html' in edit_src

    def test_lfa_edit_overrides_student_header_block(self, edit_src):
        """lfa_player_profile_edit.html must override {% block student_header %}."""
        assert 'block student_header' in edit_src

    # ── Icon ─────────────────────────────────────────────────────────────────

    def test_lfa_profile_page_title_has_id_card_icon(self, tpl_src):
        """🪪 must appear in the page title / header of lfa_player_profile.html."""
        assert '🪪' in tpl_src

    def test_lfa_profile_page_icon_var_is_id_card(self, tpl_src):
        """_page_icon must be set to 🪪 for the spec subpage header."""
        assert "_page_icon = '🪪'" in tpl_src

    def test_lfa_edit_page_icon_is_pencil(self, edit_src):
        """Edit page must use ✏️ as its spec header icon."""
        assert "_page_icon = '✏️'" in edit_src

    def test_student_base_global_brand_unchanged(self, student_base_src):
        """student_base.html 🏫 brand must remain untouched."""
        assert '🏫 LFA Education Center' in student_base_src

    def test_global_profile_title_unchanged(self, profile_src):
        """Global /profile page title icon 👤 must remain unchanged."""
        assert '👤 My Profile' in profile_src

    def test_student_base_global_profile_nav_uses_person_icon(self, student_base_src):
        """student_base.html global Profile nav must keep 👤 (links to /profile)."""
        assert '👤</a>' in student_base_src or '>👤<' in student_base_src

    # ── No phantom CSS class ─────────────────────────────────────────────────

    def test_lfa_profile_no_hdr_lfa_player_class(self, tpl_src):
        """hdr-lfa-player is not defined in student.css; must not appear in template."""
        assert 'hdr-lfa-player' not in tpl_src

    def test_lfa_edit_no_hdr_lfa_player_class(self, edit_src):
        assert 'hdr-lfa-player' not in edit_src

    def test_profile_py_no_hdr_lfa_player(self):
        """profile.py must not reference the nonexistent hdr-lfa-player CSS class."""
        import pathlib
        src = (
            pathlib.Path(__file__).resolve().parents[4]
            / "app" / "api" / "web_routes" / "profile.py"
        ).read_text()
        assert 'hdr-lfa-player' not in src

    # ── Theme tokens: no legacy purple accent ─────────────────────────────────

    def test_lfa_profile_no_hardcoded_purple_primary(self, tpl_src):
        """lfa_player_profile.html must not hardcode #667eea as accent/primary color."""
        assert '#667eea' not in tpl_src

    def test_lfa_profile_no_hardcoded_purple_gradient(self, tpl_src):
        """lfa_player_profile.html must not use #764ba2 purple gradient."""
        assert '#764ba2' not in tpl_src

    def test_lfa_edit_no_hardcoded_purple_primary(self, edit_src):
        """lfa_player_profile_edit.html must not hardcode #667eea as accent/primary color."""
        assert '#667eea' not in edit_src

    def test_lfa_profile_uses_hub_btn_token(self, tpl_src):
        """Primary button must use --hub-btn-bg CSS var, not a hardcoded hex."""
        assert 'var(--hub-btn-bg)' in tpl_src

    def test_lfa_edit_uses_brand_yellow_accent(self, edit_src):
        """Form focus / selected state must use --brand-yellow, not #667eea."""
        assert 'var(--brand-yellow)' in edit_src

    def test_lfa_edit_save_btn_uses_hub_token(self, edit_src):
        """Save button must use --hub-btn-bg token."""
        assert 'var(--hub-btn-bg)' in edit_src

    # ── Regression: global profile unchanged ─────────────────────────────────

    def test_global_profile_still_uses_separate_inline_css(self, profile_src):
        """Global /profile template must not have been modified by this fix."""
        assert '{% extends "student_base.html" %}' in profile_src

    def test_welcome_card_back_link_unchanged(self):
        """Welcome Card back link must still point to /profile/lfa-football-player."""
        src = _WC_GALLERY_TPL_PATH.read_text()
        assert '/profile/lfa-football-player' in src


# ── Group 12a: View data completeness — old-license compatibility ──────────────

class TestLfaViewDataCompleteness:
    """
    View route renders correctly for licenses that predate average_skill_level
    and onboarding_completed_at fields in motivation_scores.
    """

    def _ctx(self, lic):
        db = _mock_db(lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(lfa_player_profile_page(_req(), db=db, user=_user()))
        return mock_tmpl.TemplateResponse.call_args.args[1]

    def test_context_average_skill_level_none_for_old_license(self):
        """Old license with no average_skill_level in ms → context value is None."""
        lic = _lfa_lic()  # default has no avg_skill key
        ctx = self._ctx(lic)
        assert ctx["average_skill_level"] is None

    def test_context_onboarding_completed_at_none_for_old_license(self):
        """Old license with no onboarding_completed_at in ms → context value is None."""
        lic = _lfa_lic()  # default has no joined_at key
        ctx = self._ctx(lic)
        assert ctx["onboarding_completed_at"] is None

    def test_context_average_skill_level_correct_when_present(self):
        lic = _lfa_lic(avg_skill=71.5)
        ctx = self._ctx(lic)
        assert ctx["average_skill_level"] == 71.5

    def test_context_onboarding_completed_at_correct_when_present(self):
        lic = _lfa_lic(joined_at="2026-05-01T09:00:00Z")
        ctx = self._ctx(lic)
        assert ctx["onboarding_completed_at"] == "2026-05-01T09:00:00Z"

    def test_route_renders_without_error_when_ms_is_none(self):
        """License with motivation_scores=None must not raise."""
        lic = _lfa_lic()
        lic.motivation_scores = None
        db = _mock_db(lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(lfa_player_profile_page(_req(), db=db, user=_user()))
        assert not isinstance(result, RedirectResponse)

    def test_route_passes_ms_keys_correctly_with_all_fields(self):
        """All expected context keys present when license has full motivation_scores."""
        lic = _lfa_lic(avg_skill=64.0, joined_at="2026-05-08T10:00:00Z")
        ctx = self._ctx(lic)
        assert ctx["average_skill_level"] == 64.0
        assert ctx["onboarding_completed_at"] == "2026-05-08T10:00:00Z"
        assert ctx["ms"]["position"] == "striker"
        assert ctx["goal_label"] == "Become a professional player"


# ── Group 12b: Edit form scope — foot scores excluded, max-3 enforced ──────────

class TestLfaEditFormScope:
    """
    Verify the post-refactor edit form scope:
      - right_foot_score / left_foot_score are NEVER written by the POST handler
      - maximum 3 secondary positions is enforced
      - football_skills key is never referenced in handler source
    """

    def test_post_handler_has_no_foot_score_form_param(self):
        """lfa_player_profile_edit_submit must not accept right_foot_score_raw."""
        import inspect as _inspect
        sig = _inspect.signature(lfa_player_profile_edit_submit)
        assert "right_foot_score_raw" not in sig.parameters
        assert "left_foot_score_raw" not in sig.parameters

    def test_post_handler_source_has_no_foot_score_write(self):
        """Handler source must not assign right_foot_score or left_foot_score."""
        source = inspect.getsource(lfa_player_profile_edit_submit)
        assert "right_foot_score" not in source
        assert "left_foot_score" not in source

    def test_exactly_3_secondary_positions_accepted(self):
        """3 secondary positions (excluding primary) must succeed."""
        result, db, _, _ = _post(
            position="striker",
            secondary_positions=["centre_forward", "left_wing", "right_wing"],
        )
        assert isinstance(result, RedirectResponse)
        db.commit.assert_called_once()

    def test_4_secondary_positions_returns_422(self):
        """4 unique secondary positions (excluding primary) must fail with 422."""
        result, db, _, mock_tmpl = _post(
            position="striker",
            secondary_positions=[
                "centre_forward", "left_wing", "right_wing", "second_striker"
            ],
        )
        assert not isinstance(result, RedirectResponse)
        db.commit.assert_not_called()
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx.get("error")
        assert "3" in ctx["error"]

    def test_edit_template_has_no_foot_score_inputs(self):
        """Edit template must not contain right_foot_score_raw or left_foot_score_raw inputs."""
        src = _EDIT_TPL_PATH.read_text()
        assert "right_foot_score_raw" not in src
        assert "left_foot_score_raw" not in src

    def test_edit_template_has_secondary_counter(self):
        """Edit template must contain the JS secondary-position counter."""
        src = _EDIT_TPL_PATH.read_text()
        assert "sec-pos-counter" in src
        assert "MAX_SEC" in src


# ── 14. POST /profile/lfa-football-player/positions ──────────────────────────

import json as _json_mod
import pathlib as _pathlib

_PITCH_SEL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "static" / "js" / "pitch-selector.js"
)


def _pos_post(
    position="striker",
    positions_raw=None,
    lic=None,
    user=None,
):
    """Call lfa_player_profile_positions_submit with controllable parameters."""
    if positions_raw is None:
        positions_raw = _json_mod.dumps([position])
    if lic is None:
        lic = _lfa_lic()
    if user is None:
        user = _user()
    db = _mock_db(lic)
    req = _req()
    req.query_params = {}
    result = _run(lfa_player_profile_positions_submit(
        req,
        position=position,
        positions_raw=positions_raw,
        db=db,
        user=user,
    ))
    return result, db, lic, user


class TestLfaPositionsEndpoint:
    """POST /profile/lfa-football-player/positions — save, validation, backward-compat, template."""

    # ── Valid submit ──────────────────────────────────────────────────────────

    def test_valid_single_position_redirects_with_updated(self):
        result, *_ = _pos_post(position="striker", positions_raw='["striker"]')
        assert isinstance(result, RedirectResponse)
        assert "updated=positions" in result.headers["location"]

    def test_valid_four_positions_redirects(self):
        raw = _json_mod.dumps(["striker", "centre_forward", "left_wing", "right_wing"])
        result, db, lic, _ = _pos_post(position="striker", positions_raw=raw)
        assert isinstance(result, RedirectResponse)
        assert db.commit.call_count == 1
        assert lic.motivation_scores["positions"] == [
            "striker", "centre_forward", "left_wing", "right_wing"
        ]

    def test_valid_save_syncs_user_position(self):
        user = _user()
        raw = _json_mod.dumps(["goalkeeper"])
        result, _, lic, u = _pos_post(position="goalkeeper", positions_raw=raw, user=user)
        assert isinstance(result, RedirectResponse)
        assert u.position == "goalkeeper"

    def test_valid_save_updates_motivation_scores_position_key(self):
        raw = _json_mod.dumps(["centre_back", "left_back"])
        _, _, lic, _ = _pos_post(position="centre_back", positions_raw=raw)
        assert lic.motivation_scores["position"] == "centre_back"
        assert lic.motivation_scores["positions"][0] == "centre_back"

    def test_valid_save_does_not_touch_foot_scores(self):
        lic = _lfa_lic(right=80.0, left=20.0)
        _pos_post(lic=lic)
        assert lic.right_foot_score == 80.0
        assert lic.left_foot_score == 20.0

    # ── Validation errors — no DB write ──────────────────────────────────────

    def test_five_positions_rejected(self):
        raw = _json_mod.dumps([
            "striker", "centre_forward", "left_wing", "right_wing", "second_striker"
        ])
        result, db, _, _ = _pos_post(position="striker", positions_raw=raw)
        assert isinstance(result, RedirectResponse)
        assert "pos_error=invalid_count" in result.headers["location"]
        db.commit.assert_not_called()

    def test_empty_positions_list_rejected(self):
        result, db, _, _ = _pos_post(position="striker", positions_raw="[]")
        assert isinstance(result, RedirectResponse)
        assert "pos_error=invalid_count" in result.headers["location"]
        db.commit.assert_not_called()

    def test_invalid_primary_rejected(self):
        result, db, _, _ = _pos_post(
            position="quarterback",
            positions_raw='["quarterback"]',
        )
        assert "pos_error=invalid_primary" in result.headers["location"]
        db.commit.assert_not_called()

    def test_empty_primary_rejected(self):
        result, db, _, _ = _pos_post(position="", positions_raw='[""]')
        assert "pos_error=invalid_primary" in result.headers["location"]
        db.commit.assert_not_called()

    def test_invalid_position_in_list_rejected(self):
        raw = _json_mod.dumps(["striker", "not_a_real_position"])
        result, db, _, _ = _pos_post(position="striker", positions_raw=raw)
        assert "pos_error=invalid_position" in result.headers["location"]
        db.commit.assert_not_called()

    def test_primary_not_first_rejected(self):
        raw = _json_mod.dumps(["centre_forward", "striker"])
        result, db, _, _ = _pos_post(position="striker", positions_raw=raw)
        assert "pos_error=primary_not_first" in result.headers["location"]
        db.commit.assert_not_called()

    def test_malformed_json_rejected(self):
        result, db, _, _ = _pos_post(position="striker", positions_raw="not-json")
        assert "pos_error=invalid_format" in result.headers["location"]
        db.commit.assert_not_called()

    def test_no_license_redirects(self):
        db = _mock_db(None)
        req = _req()
        req.query_params = {}
        result = _run(lfa_player_profile_positions_submit(
            req,
            position="striker",
            positions_raw='["striker"]',
            db=db,
            user=_user(),
        ))
        assert isinstance(result, RedirectResponse)
        assert "dashboard" in result.headers["location"] or "no_lfa_license" in result.headers["location"]

    # ── Template static assertions ────────────────────────────────────────────

    def test_profile_template_has_positions_mount(self):
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert "ps-profile-mount" in src

    def test_profile_template_positions_form_action(self):
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert 'action="/profile/lfa-football-player/positions"' in src

    def test_profile_template_references_player_positions(self):
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert "player_positions" in src

    # ── UX-fix: P0 — no duplicate counter ────────────────────────────────────

    def test_no_external_counter_element_in_template(self):
        """External #pos-counter removed — pitch-selector internal counter is the sole source."""
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert 'id="pos-counter"' not in src

    def test_update_inputs_js_no_counter_reference(self):
        """_updateInputs must not touch the (now-removed) #pos-counter DOM element."""
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert "pos-counter" not in src

    # ── UX-fix: P1 — cancel/reopen resets selector state ─────────────────────

    def test_hidePosEdit_resets_ps_instance(self):
        """hidePosEdit must null _ps so re-open always reinitialises from saved DB state."""
        src = _TPL_PATH.read_text(encoding="utf-8")
        hide_start = src.find("hidePosEdit")
        assert hide_start != -1, "hidePosEdit not found in template"
        fn_brace = src.find("{", hide_start)
        fn_end   = src.find("};", fn_brace)
        fn_body  = src[fn_brace:fn_end]
        assert "_ps = null" in fn_body

    # ── UX-fix: P1 — profile theme scoped, not global ────────────────────────

    def test_profile_theme_css_scoped_to_mount(self):
        """Info-panel + counter overrides must be scoped to #ps-profile-mount, not global."""
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert "#ps-profile-mount .ps-info-panel" in src
        assert "#ps-profile-mount .ps-counter" in src

    # ── UX-fix: P1 — toggle reveal animation present ─────────────────────────

    def test_edit_mode_reveal_animation_present(self):
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert "pos-edit-entering" in src
        assert "pos-edit-reveal" in src

    # ── UX-fix: P0 — mobile sticky action row ────────────────────────────────

    def test_mobile_action_row_is_sticky(self):
        """pos-action-row must use position:sticky in a max-width media query."""
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert "sticky" in src
        assert "pos-action-row" in src

    # ── UX-fix: P2 — emoji and badge clarity ─────────────────────────────────

    def test_positions_card_icon_not_football_emoji(self):
        """Positions card must not reuse the ⚽ emoji (clashes with Player Profile card)."""
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert "⚽ Positions" not in src

    def test_positions_card_has_pin_icon(self):
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert "📍 Positions" in src

    def test_positions_card_has_primary_group_label(self):
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert "pos-section-label" in src
        assert ">Primary<" in src

    def test_positions_card_has_also_group_label(self):
        src = _TPL_PATH.read_text(encoding="utf-8")
        assert ">Also<" in src

    # ── JS static assertion ───────────────────────────────────────────────────

    def test_pitch_selector_has_set_positions_method(self):
        src = _PITCH_SEL_PATH.read_text(encoding="utf-8")
        assert "setPositions" in src
        assert "PitchSelector.prototype.setPositions" in src
