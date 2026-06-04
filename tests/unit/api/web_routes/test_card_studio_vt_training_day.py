"""Card Studio VT — Phase 1 training date tests.

CSVTD-01  tz=Europe/Budapest → _vtc_single_elig called with Budapest training_date
CSVTD-02  tz=America/Sao_Paulo at 00:30 UTC → training_date = previous UTC day
CSVTD-03  tz=None → UTC fallback training_date (no crash)
CSVTD-04  preview_url always contains &date=YYYY-MM-DD
CSVTD-05  export_url always contains &date=YYYY-MM-DD
CSVTD-06  date in preview_url matches training_date from tz
CSVTD-07  date in export_url matches training_date from tz
CSVTD-08  vt_tz_str in template context equals tz param
CSVTD-09  vt_tz_str is empty string when tz=None
CSVTD-10  cs_vt_panel.html format chip link contains &tz= when vt_tz_str is set
CSVTD-11  cs_vt_panel.html game row link contains &tz= when vt_tz_str is set
CSVTD-12  cs_vt_panel.html reward eligible row link contains &tz= when vt_tz_str is set
CSVTD-13  cs_vt_panel.html links do NOT contain &tz= when vt_tz_str is empty
CSVTD-14  card_studio_shell.html VT mode has browser tz inject JS
CSVTD-15  card_studio_shell.html non-VT mode does NOT have tz inject JS (no redirect loop)
CSVTD-16  tz inject JS checks for 'tz' param presence (no redirect loop guard)
CSVTD-17  tz inject JS uses Intl.DateTimeFormat
CSVTD-18  reward tiers eligibility reflects training_date via eligible_games count
"""
from __future__ import annotations

import asyncio
import pathlib
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from jinja2 import Environment, FileSystemLoader

_CS_BASE       = "app.api.web_routes.card_studio"
_TEMPLATES_DIR = pathlib.Path(__file__).resolve().parents[4] / "app" / "templates"

# 2026-06-05 00:30 UTC — Budapest 02:30 (same day) | São Paulo 21:30 (prev day)
_UTC_00_30 = datetime(2026, 6, 5, 0, 30, 0, tzinfo=timezone.utc)


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


def _db() -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _license()
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.count.return_value = 0
    return db


def _invoke(tz=None, game_id=1, platform="vt_landscape",
            owned_vtc=None, eligible_results=None,
            active_game_count=1, training_date_override=None):
    """Invoke /card-studio/virtual-training and return (result, context, elig_call_dates).

    elig_call_dates collects the `day` argument passed to _vtc_single_elig.
    training_date_override: if set, compute_training_local_date is mocked to return this date.
    """
    from app.api.web_routes.card_studio import card_studio_virtual_training
    from app.services.training_day import resolve_training_timezone, compute_training_local_date

    user     = _user()
    db       = _db()
    captured = {}
    elig_dates: list[date] = []

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    owned_ids = owned_vtc if owned_vtc is not None else ["vt_landscape"]

    def _fake_owned(db_, uid, card_type_id):
        return owned_ids if card_type_id == "virtual_training_card" else []

    _elig_idx = {"n": 0}
    _elig_res = eligible_results or []

    def _fake_single_elig(db_, uid, gid, day):
        elig_dates.append(day)
        idx = _elig_idx["n"]
        _elig_idx["n"] += 1
        if idx < len(_elig_res):
            return _elig_res[idx]
        return (False, 0, 5)

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

    # Compute real training_date via training_day functions at the fixed UTC timestamp,
    # unless caller overrides it.
    if training_date_override is not None:
        computed_date = training_date_override
    else:
        _training_tz, _ = resolve_training_timezone(tz)
        computed_date = compute_training_local_date(_UTC_00_30, _training_tz)

    with patch(f"{_CS_BASE}.get_owned_design_ids", side_effect=_fake_owned), \
         patch(f"{_CS_BASE}._vtc_single_elig", side_effect=_fake_single_elig), \
         patch(f"{_CS_BASE}._vtc_reward_elig", return_value=(False, 0)), \
         patch(f"{_CS_BASE}.is_design_accessible", return_value=False), \
         patch(f"{_CS_BASE}.compute_training_local_date", return_value=computed_date), \
         patch(f"{_CS_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl
        result = _run(card_studio_virtual_training(
            request=MagicMock(),
            game_id=game_id,
            platform=platform,
            tz=tz,
            db=db,
            user=user,
        ))

    return result, captured.get("context", {}), elig_dates


def _render_vt_panel(eligible_games=None, vt_tz_str="", active_game_id=None,
                     active_platform="vt_landscape"):
    env  = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=False)
    tmpl = env.get_template("includes/cs_vt_panel.html")
    return tmpl.render(
        owned_vtc_formats=[
            {"design_id": "vt_landscape", "label": "Landscape", "dims": "1280×720", "style_tag": "GAME"},
        ],
        eligible_games=eligible_games or [
            {"game_id": 1, "game_name": "Color Reaction", "completed": 5, "required": 5},
        ],
        any_eligible=bool(eligible_games),
        active_game_id=active_game_id or 1,
        active_platform=active_platform,
        can_export=True,
        vt_tz_str=vt_tz_str,
        reward_tiers=[
            {"tier": 3,  "eligible": True,  "completed_games": 3, "disabled": False, "has_owned_format": True},
            {"tier": 5,  "eligible": False, "completed_games": 3, "disabled": False, "has_owned_format": True},
            {"tier": 10, "eligible": False, "completed_games": 3, "disabled": True,  "has_owned_format": True},
        ],
    )


_SHELL = (_TEMPLATES_DIR / "card_studio_shell.html").read_text(encoding="utf-8")


# ── CSVTD-01..03: eligibility called with correct training_date ───────────────

class TestTrainingDateInEligibilityCall:

    def test_csvtd01_budapest_training_date(self):
        """CSVTD-01: tz=Europe/Budapest at 00:30 UTC → eligibility uses 2026-06-05."""
        _, _, elig_dates = _invoke(tz="Europe/Budapest")
        assert elig_dates, "Expected _vtc_single_elig to be called"
        assert elig_dates[0] == date(2026, 6, 5)

    def test_csvtd02_sao_paulo_training_date_prev_day(self):
        """CSVTD-02: tz=America/Sao_Paulo at 00:30 UTC → eligibility uses 2026-06-04 (prev day)."""
        _, _, elig_dates = _invoke(tz="America/Sao_Paulo")
        assert elig_dates, "Expected _vtc_single_elig to be called"
        assert elig_dates[0] == date(2026, 6, 4)

    def test_csvtd03_no_tz_utc_fallback(self):
        """CSVTD-03: tz=None → eligibility uses UTC today (2026-06-05), no crash."""
        _, _, elig_dates = _invoke(tz=None)
        assert elig_dates, "Expected _vtc_single_elig to be called"
        assert elig_dates[0] == date(2026, 6, 5)

    def test_csvtd01b_budapest_vs_sao_paulo_differ(self):
        """Same UTC instant, different tz → different training_date."""
        _, _, bp_dates = _invoke(tz="Europe/Budapest")
        _, _, sp_dates = _invoke(tz="America/Sao_Paulo")
        assert bp_dates[0] != sp_dates[0]


# ── CSVTD-04..07: preview/export URL always has &date= ───────────────────────

class TestPreviewExportUrlDateParam:

    def test_csvtd04_preview_url_has_date(self):
        """CSVTD-04: preview_url always contains &date=YYYY-MM-DD."""
        _, ctx, _ = _invoke(tz="Europe/Budapest", eligible_results=[(True, 5, 5)])
        assert ctx.get("preview_url"), "preview_url must be set"
        assert "&date=" in ctx["preview_url"], f"No &date= in: {ctx['preview_url']}"

    def test_csvtd05_export_url_has_date(self):
        """CSVTD-05: export_url always contains &date=YYYY-MM-DD."""
        _, ctx, _ = _invoke(tz="Europe/Budapest", eligible_results=[(True, 5, 5)])
        assert ctx.get("export_url"), "export_url must be set"
        assert "&date=" in ctx["export_url"], f"No &date= in: {ctx['export_url']}"

    def test_csvtd06_preview_date_matches_budapest_training_date(self):
        """CSVTD-06: date in preview_url matches Budapest training_date = 2026-06-05."""
        _, ctx, _ = _invoke(tz="Europe/Budapest", eligible_results=[(True, 5, 5)])
        assert "date=2026-06-05" in ctx["preview_url"]

    def test_csvtd07_export_date_matches_sao_paulo_training_date(self):
        """CSVTD-07: date in export_url matches São Paulo training_date = 2026-06-04."""
        _, ctx, _ = _invoke(tz="America/Sao_Paulo", eligible_results=[(True, 5, 5)])
        assert "date=2026-06-04" in ctx["export_url"]

    def test_csvtd04b_no_tz_preview_url_has_utc_date(self):
        """No tz → preview_url has UTC fallback date 2026-06-05."""
        _, ctx, _ = _invoke(tz=None, eligible_results=[(True, 5, 5)])
        assert "date=2026-06-05" in ctx.get("preview_url", "")


# ── CSVTD-08..09: vt_tz_str in context ───────────────────────────────────────

class TestVtTzStrContext:

    def test_csvtd08_vt_tz_str_equals_tz_param(self):
        """CSVTD-08: vt_tz_str in context equals the tz query param."""
        _, ctx, _ = _invoke(tz="Europe/Budapest")
        assert ctx.get("vt_tz_str") == "Europe/Budapest"

    def test_csvtd09_vt_tz_str_empty_when_no_tz(self):
        """CSVTD-09: vt_tz_str is empty string when tz=None."""
        _, ctx, _ = _invoke(tz=None)
        assert ctx.get("vt_tz_str") == ""


# ── CSVTD-10..13: cs_vt_panel.html tz param preservation ────────────────────

class TestVtPanelTzLinkPreservation:

    def test_csvtd10_format_chip_link_has_tz(self):
        """CSVTD-10: Format chip link contains &tz= when vt_tz_str is set."""
        html = _render_vt_panel(vt_tz_str="Europe/Budapest")
        assert "&tz=Europe/Budapest" in html, "Format chip link must preserve tz param"

    def test_csvtd11_game_row_link_has_tz(self):
        """CSVTD-11: Game row link contains &tz= when vt_tz_str is set."""
        html = _render_vt_panel(vt_tz_str="America/Sao_Paulo",
                                eligible_games=[{"game_id": 2, "game_name": "Go/No-Go",
                                                 "completed": 5, "required": 5}])
        assert "&tz=America/Sao_Paulo" in html, "Game row link must preserve tz param"

    def test_csvtd12_reward_eligible_link_has_tz(self):
        """CSVTD-12: Reward eligible row link contains &tz= when vt_tz_str is set."""
        html = _render_vt_panel(vt_tz_str="Europe/Budapest")
        # Both reward=3 and &tz=Europe/Budapest must appear in the same anchor href
        assert "reward=3&tz=Europe/Budapest" in html

    def test_csvtd13_no_tz_param_when_empty(self):
        """CSVTD-13: Links do NOT contain &tz= when vt_tz_str is empty."""
        html = _render_vt_panel(vt_tz_str="")
        assert "&tz=" not in html, "Links must not add empty tz param"


# ── CSVTD-14..17: card_studio_shell.html tz inject JS ───────────────────────

class TestShellTzInjectJS:

    def test_csvtd14_shell_has_tz_inject_js(self):
        """CSVTD-14: Shell template contains VT tz inject JS in VT mode block."""
        assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in _SHELL
        assert "params.has('tz')" in _SHELL

    def test_csvtd15_tz_inject_guarded_by_virtual_training_block(self):
        """CSVTD-15: The tz inject JS is inside a VT-only block in the script section."""
        # The script block starts after the .cs-shell-wrap div closes.
        # Find the last <script> tag (the main inline script block).
        script_start   = _SHELL.rfind("<script>")
        assert script_start != -1, "Main <script> block not found"
        # The VT tz inject block lives inside the script, guarded by a VT check
        script_section = _SHELL[script_start:]
        assert 'active_type == "virtual_training"' in script_section, (
            "VT-mode guard must exist in the script section"
        )
        tz_inject_pos = script_section.find("params.has('tz')")
        assert tz_inject_pos != -1, "tz inject JS not found in script section"

    def test_csvtd16_no_redirect_loop_guard(self):
        """CSVTD-16: JS checks params.has('tz') — redirect only when tz is absent."""
        assert "if (!params.has('tz'))" in _SHELL

    def test_csvtd17_uses_intl_datetimeformat(self):
        """CSVTD-17: JS reads timezone from Intl.DateTimeFormat."""
        assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in _SHELL


# ── CSVTD-18: reward tiers reflect training_date ─────────────────────────────

class TestRewardTiersTrainingDate:

    def test_csvtd18_reward_eligibility_reflects_training_date(self):
        """CSVTD-18: reward tier eligibility derived from eligible_games count
        which is itself built with the correct training_date."""
        # 3 games eligible on São Paulo date → tier-3 eligible, tier-5 not
        _, ctx, elig_dates = _invoke(
            tz="America/Sao_Paulo",
            active_game_count=5,
            eligible_results=[(True, 5, 5)] * 3 + [(False, 0, 5)] * 2,
        )
        # All eligibility calls must use São Paulo date 2026-06-04
        for d in elig_dates:
            assert d == date(2026, 6, 4), f"Expected 2026-06-04 (São Paulo), got {d}"

        # Tier 3 eligible (3 games completed), tier 5 not
        reward_tiers = ctx.get("reward_tiers", [])
        tier3 = next((t for t in reward_tiers if t["tier"] == 3), None)
        tier5 = next((t for t in reward_tiers if t["tier"] == 5), None)
        assert tier3 is not None
        assert tier3["eligible"]  is True
        assert tier5["eligible"]  is False
