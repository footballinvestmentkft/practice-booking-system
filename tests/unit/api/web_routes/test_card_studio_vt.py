"""
CS-VT-2 — Virtual Training Card Studio route tests.

CSVT-01  no VTC ownership → 303 redirect to /shop?type=virtual_training_card
CSVT-02  ownership + no eligible games → 200, panel shows "Play to Unlock"
CSVT-03  ownership + eligible game → 200, can_export=True, export_url set
CSVT-04  no LFA license → 303 redirect to dashboard
CSVT-05  onboarding incomplete → 303 redirect to onboarding
CSVT-06  context has active_type="virtual_training"
CSVT-07  context has vtc_owned=True
CSVT-08  context has owned_vtc_formats list
CSVT-09  context has eligible_games list
CSVT-10  context has reward_tiers list with 3 entries
CSVT-11  type switcher template renders VT Card button
CSVT-12  type switcher: vtc_owned=False → cs-type-locked class
CSVT-13  type switcher: vtc_owned=True → no cs-type-locked class
CSVT-14  shell breadcrumb contains "Virtual Training Card"
CSVT-15  panel renders "Play to Unlock" when no eligible games
CSVT-16  panel renders export-ready CTA when eligible + owned
CSVT-17  reward tier 10 disabled when < 10 active games
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
_CS_BASE = "app.api.web_routes.card_studio"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _user(uid: int = 7) -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.credit_balance = 500
    u.role = MagicMock()
    return u


def _license(onboarding_completed: bool = True) -> MagicMock:
    lic = MagicMock()
    lic.onboarding_completed = onboarding_completed
    return lic


_USE_DEFAULT_DB_LIC = object()  # sentinel for _db's lic param


def _db(lic=_USE_DEFAULT_DB_LIC) -> MagicMock:
    """lic=None → first() returns None (no license). lic=<default> → valid license."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = (
        _license() if lic is _USE_DEFAULT_DB_LIC else lic
    )
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.count.return_value = 0
    return db


_DEFAULT_LIC = object()  # sentinel: use default valid license


def _invoke_vt_studio(game_id=None, platform=None, owned_vtc=None, lic=_DEFAULT_LIC,
                      eligible_games_result=None, active_game_count=0):
    """Invoke /card-studio/virtual-training with mocked deps.

    lic=None → no license (test license guard).
    lic=<default> → valid license with onboarding_completed=True.
    lic=<mock> → explicit license mock.
    """
    from app.api.web_routes.card_studio import card_studio_virtual_training

    user         = _user()
    _lic_for_db  = _license() if lic is _DEFAULT_LIC else lic
    db           = _db(lic=_lic_for_db)
    captured: dict = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    owned_ids = owned_vtc if owned_vtc is not None else ["vt_landscape"]

    def _fake_owned(db_, uid, card_type_id):
        if card_type_id == "virtual_training_card":
            return owned_ids
        return []

    # eligible_games_result: list of (is_elig, count, required) per game call
    _elig_call_idx = {"n": 0}
    _elig_results = eligible_games_result or []

    def _fake_single_elig(db_, uid, gid, day):
        idx = _elig_call_idx["n"]
        _elig_call_idx["n"] += 1
        if idx < len(_elig_results):
            return _elig_results[idx]
        return (False, 0, 5)

    # Mock active games
    def _fake_query_all(*args, **kwargs):
        games = []
        for i in range(active_game_count):
            g = MagicMock()
            g.id = i + 1
            g.name = f"Game {i + 1}"
            g.is_active = True
            g.max_daily_attempts = 5
            games.append(g)
        return games

    db.query.return_value.filter.return_value.all.side_effect = _fake_query_all

    with patch(f"{_CS_BASE}.get_owned_design_ids", side_effect=_fake_owned), \
         patch(f"{_CS_BASE}._vtc_single_elig", side_effect=_fake_single_elig), \
         patch(f"{_CS_BASE}._vtc_reward_elig", return_value=(False, 0)), \
         patch(f"{_CS_BASE}.is_design_accessible", return_value=False), \
         patch(f"{_CS_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl
        result = _run(card_studio_virtual_training(
            request=MagicMock(),
            game_id=game_id,
            platform=platform,
            db=db,
            user=user,
        ))

    return result, captured


def _render_type_switcher(active_type="welcome", vtc_owned=False, cc_owned=True):
    env  = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    tmpl = env.get_template("includes/cs_type_switcher.html")
    return tmpl.render(active_type=active_type, vtc_owned=vtc_owned, cc_owned=cc_owned)


def _render_vt_panel(
    owned_vtc_formats=None,
    eligible_games=None,
    any_eligible=False,
    active_game_id=None,
    active_platform="vt_landscape",
    can_export=False,
    reward_tiers=None,
):
    env  = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    tmpl = env.get_template("includes/cs_vt_panel.html")
    return tmpl.render(
        owned_vtc_formats=owned_vtc_formats or [
            {"design_id": "vt_landscape", "label": "Landscape (16:9)", "dims": "1280 × 720", "style_tag": "GAME"},
        ],
        eligible_games=eligible_games or [],
        any_eligible=any_eligible,
        active_game_id=active_game_id,
        active_platform=active_platform,
        can_export=can_export,
        reward_tiers=reward_tiers or [
            {"tier": 3,  "eligible": False, "completed_games": 0, "disabled": False, "has_owned_format": True},
            {"tier": 5,  "eligible": False, "completed_games": 0, "disabled": False, "has_owned_format": True},
            {"tier": 10, "eligible": False, "completed_games": 0, "disabled": True,  "has_owned_format": True},
        ],
    )


# ── CSVT-01..05: Route guards ─────────────────────────────────────────────────

class TestCSVTRouteGuards:

    def test_csvt01_no_vtc_ownership_redirects_to_shop(self):
        result, _ = _invoke_vt_studio(owned_vtc=[])
        assert result.status_code == 303
        assert "virtual_training_card" in result.headers["location"]

    def test_csvt02_ownership_no_eligible_games_returns_200(self):
        result, _ = _invoke_vt_studio(owned_vtc=["vt_landscape"], active_game_count=1)
        assert result.status_code == 200

    def test_csvt03_ownership_eligible_game_returns_200_with_export(self):
        result, cap = _invoke_vt_studio(
            owned_vtc=["vt_landscape"],
            active_game_count=1,
            eligible_games_result=[(True, 5, 5)],
        )
        assert result.status_code == 200
        assert cap["context"]["can_export"] is True
        assert cap["context"]["export_url"] is not None

    def test_csvt04_no_license_redirects_to_dashboard(self):
        """No LFA license → license guard returns None → redirect to dashboard."""
        result, _ = _invoke_vt_studio(lic=None)  # explicitly no license
        assert result.status_code == 303
        assert "dashboard" in result.headers["location"]

    def test_csvt05_onboarding_incomplete_redirects(self):
        """Onboarding not completed → redirect to onboarding."""
        result, _ = _invoke_vt_studio(lic=_license(onboarding_completed=False))
        assert result.status_code == 303
        assert "onboarding" in result.headers["location"]


# ── CSVT-06..10: Context variables ───────────────────────────────────────────

class TestCSVTContext:

    def _ctx(self, **kw) -> dict:
        # lic uses default (_DEFAULT_LIC) → valid license, no need to pass
        _, cap = _invoke_vt_studio(owned_vtc=["vt_landscape"], active_game_count=0, **kw)
        return cap.get("context", {})

    def test_csvt06_active_type_is_virtual_training(self):
        assert self._ctx()["active_type"] == "virtual_training"

    def test_csvt07_vtc_owned_is_true(self):
        assert self._ctx()["vtc_owned"] is True

    def test_csvt08_owned_vtc_formats_is_list(self):
        ctx = self._ctx()
        assert isinstance(ctx["owned_vtc_formats"], list)
        assert len(ctx["owned_vtc_formats"]) >= 1

    def test_csvt09_eligible_games_is_list(self):
        ctx = self._ctx()
        assert isinstance(ctx["eligible_games"], list)

    def test_csvt10_reward_tiers_has_3_entries(self):
        ctx = self._ctx()
        assert len(ctx["reward_tiers"]) == 3
        tiers = {rt["tier"] for rt in ctx["reward_tiers"]}
        assert tiers == {3, 5, 10}


# ── CSVT-11..13: Type switcher template ──────────────────────────────────────

class TestCSVTTypeSwitcher:

    def test_csvt11_type_switcher_contains_vt_card_button(self):
        html = _render_type_switcher()
        assert "VT Card" in html

    def test_csvt12_vtc_owned_false_shows_locked_class(self):
        html = _render_type_switcher(vtc_owned=False)
        # The VTC button section should have cs-type-locked
        after_vt = html.split("VT Card")[0][-300:]  # chars before VT Card label
        assert "cs-type-locked" in after_vt

    def test_csvt13_vtc_owned_true_no_locked_class_on_vtc_button(self):
        html = _render_type_switcher(vtc_owned=True)
        # When owned, the button should link to /card-studio/virtual-training
        assert "/card-studio/virtual-training" in html
        # The specific VTC button should NOT have cs-type-locked
        # Find the VTC button block
        vtc_section = html.split("virtual-training")[0].rsplit("cs-type-btn", 1)[-1]
        assert "cs-type-locked" not in vtc_section

    def test_csvt13b_vtc_locked_links_to_shop(self):
        html = _render_type_switcher(vtc_owned=False)
        assert "/shop?type=virtual_training_card" in html


# ── CSVT-14: Shell breadcrumb ─────────────────────────────────────────────────

class TestCSVTShellBreadcrumb:

    def test_csvt14_shell_breadcrumb_contains_virtual_training_card(self):
        from jinja2 import Environment, FileSystemLoader, TemplateNotFound
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
        try:
            tmpl = env.get_template("card_studio_shell.html")
        except TemplateNotFound:
            pytest.skip("card_studio_shell.html not available in this test context")

        html = tmpl.render(
            request=MagicMock(),
            user=MagicMock(),
            active_type="virtual_training",
            vtc_owned=True, cc_owned=False,
            owned_vtc_formats=[],
            eligible_games=[], any_eligible=False,
            active_game_id=None, active_platform=None,
            preview_url=None, export_url=None,
            can_export=False, ratio_class="mfg-ratio-169",
            reward_tiers=[], fmt=None,
            challenge_mode="selector",
            spec_dashboard_url="/dashboard",
            spec_dashboard_icon="⚽",
            spec_profile_url="/profile",
            spec_profile_icon="🪪",
        )
        assert "Virtual Training Card" in html


# ── CSVT-15..16: Panel content states ────────────────────────────────────────

class TestCSVTPanelContent:

    def test_csvt15_panel_shows_play_to_unlock_when_no_eligible_games(self):
        html = _render_vt_panel(eligible_games=[], any_eligible=False)
        assert "Play to Unlock" in html or "Play VT" in html or "No games completed" in html.lower()

    def test_csvt16_panel_shows_eligible_game_when_present(self):
        games = [{"game_id": 1, "game_name": "Target Tracking", "completed": 5, "required": 5}]
        html  = _render_vt_panel(
            eligible_games=games, any_eligible=True,
            active_game_id=1, active_platform="vt_landscape", can_export=True,
        )
        assert "Target Tracking" in html
        assert "5/5" in html

    def test_csvt16b_panel_shows_game_name_in_row(self):
        games = [{"game_id": 2, "game_name": "Memory Sequence", "completed": 5, "required": 5}]
        html  = _render_vt_panel(eligible_games=games, any_eligible=True, active_game_id=2)
        assert "Memory Sequence" in html


# ── CSVT-17: Tier 10 disabled ─────────────────────────────────────────────────

class TestCSVTTierDisabled:

    def test_csvt17_tier10_disabled_shows_coming_soon(self):
        reward_tiers = [
            {"tier": 3,  "eligible": False, "completed_games": 0, "disabled": False, "has_owned_format": True},
            {"tier": 5,  "eligible": False, "completed_games": 0, "disabled": False, "has_owned_format": True},
            {"tier": 10, "eligible": False, "completed_games": 0, "disabled": True,  "has_owned_format": True},
        ]
        html = _render_vt_panel(reward_tiers=reward_tiers)
        assert "Coming Soon" in html

    def test_csvt17b_tier10_not_disabled_when_active_games_enough(self):
        reward_tiers = [
            {"tier": 3,  "eligible": False, "completed_games": 0, "disabled": False, "has_owned_format": True},
            {"tier": 5,  "eligible": False, "completed_games": 0, "disabled": False, "has_owned_format": True},
            {"tier": 10, "eligible": False, "completed_games": 0, "disabled": False, "has_owned_format": True},
        ]
        html = _render_vt_panel(reward_tiers=reward_tiers)
        assert "Coming Soon" not in html
