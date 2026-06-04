"""VTC-TPL-01..12 — VT Card template rendering tests.

Verifies:
  - All 4 templates render without Jinja2 errors given minimal context
  - Single-game HTML contains "DAILY COMPLETE", game name, completion counter
  - Reward HTML contains "{N}-GAME MASTERY" for each tier
  - Canvas dimensions are referenced correctly in the template <meta> tags
  - Optional stats fields (score, reaction, top skill delta) render gracefully when absent
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader

# Template root relative to project — Jinja2 FileSystemLoader needs a string path
_TMPL_ROOT = Path(__file__).resolve().parents[4] / "app" / "templates"

_ENV: Environment | None = None


def _env() -> Environment:
    global _ENV
    if _ENV is None:
        _ENV = Environment(loader=FileSystemLoader(str(_TMPL_ROOT)), autoescape=False)
    return _ENV


def _render(tmpl_path: str, ctx: dict) -> str:
    return _env().get_template(tmpl_path).render(**ctx)


# ── Minimal context builders ───────────────────────────────────────────────────

class _MockGame:
    def __init__(self, name: str = "Target Tracking", code: str = "target_tracking"):
        self.name = name
        self.code = code


def _single_game_ctx(**overrides: Any) -> dict:
    ctx = {
        "game":             _MockGame(),
        "attempt_date":     "2026-06-04",
        "completed_count":  5,
        "max_attempts":     5,
        "platform":         "vt_landscape",
        "player_name":      "Test Player",
        "player_overall":   72.3,
        "player_photo_url": None,
        "player_primary_pos": "CAM",
        "best_score":       87.4,
        "avg_reaction_ms":  234,
        "xp_earned":        45,
        "top_skill_delta":  {"name": "Passing", "delta": 0.15},
    }
    ctx.update(overrides)
    return ctx


def _reward_ctx(tier: int = 3, **overrides: Any) -> dict:
    ctx = {
        "tier":                 tier,
        "completed_games":      tier,
        "attempt_date":         "2026-06-04",
        "platform":             "vt_reward_landscape",
        "player_name":          "Test Player",
        "player_overall":       72.3,
        "player_photo_url":     None,
        "player_primary_pos":   "CAM",
        "completed_game_names": ["Target Tracking", "Memory Sequence", "Color Reaction"][:tier],
        "total_xp":             tier * 45,
    }
    ctx.update(overrides)
    return ctx


# ── VTC-TPL-01..04: all templates render without error ────────────────────────

class TestTemplatesRenderWithoutError:
    def test_tpl01_vt_landscape_renders(self):
        html = _render("public/export/vt/landscape.html", _single_game_ctx(platform="vt_landscape"))
        assert len(html) > 500

    def test_tpl02_vt_portrait_renders(self):
        html = _render("public/export/vt/portrait.html", _single_game_ctx(platform="vt_portrait"))
        assert len(html) > 500

    def test_tpl03_vt_reward_landscape_renders(self):
        html = _render("public/export/vt_reward/landscape.html", _reward_ctx())
        assert len(html) > 500

    def test_tpl04_vt_reward_portrait_renders(self):
        html = _render("public/export/vt_reward/portrait.html", _reward_ctx(platform="vt_reward_portrait"))
        assert len(html) > 500


# ── VTC-TPL-05..07: single-game content assertions ────────────────────────────

class TestSingleGameContent:
    @pytest.mark.parametrize("tmpl", [
        "public/export/vt/landscape.html",
        "public/export/vt/portrait.html",
    ])
    def test_tpl05_contains_daily_complete(self, tmpl):
        html = _render(tmpl, _single_game_ctx())
        assert "DAILY COMPLETE" in html

    @pytest.mark.parametrize("tmpl", [
        "public/export/vt/landscape.html",
        "public/export/vt/portrait.html",
    ])
    def test_tpl06_contains_game_name(self, tmpl):
        html = _render(tmpl, _single_game_ctx())
        assert "Target Tracking" in html

    @pytest.mark.parametrize("tmpl", [
        "public/export/vt/landscape.html",
        "public/export/vt/portrait.html",
    ])
    def test_tpl07_contains_completion_counter(self, tmpl):
        html = _render(tmpl, _single_game_ctx(completed_count=5, max_attempts=5))
        # Both values must appear somewhere in the DOM
        assert "5" in html


# ── VTC-TPL-08..10: reward content assertions ─────────────────────────────────

class TestRewardContent:
    @pytest.mark.parametrize("tier", [3, 5])
    @pytest.mark.parametrize("tmpl", [
        "public/export/vt_reward/landscape.html",
        "public/export/vt_reward/portrait.html",
    ])
    def test_tpl08_contains_game_mastery(self, tmpl, tier):
        html = _render(tmpl, _reward_ctx(tier=tier))
        assert f"{tier}-GAME MASTERY" in html

    @pytest.mark.parametrize("tmpl", [
        "public/export/vt_reward/landscape.html",
        "public/export/vt_reward/portrait.html",
    ])
    def test_tpl09_contains_completed_game_names(self, tmpl):
        html = _render(tmpl, _reward_ctx(tier=3))
        assert "Target Tracking" in html
        assert "Memory Sequence" in html

    @pytest.mark.parametrize("tmpl", [
        "public/export/vt_reward/landscape.html",
        "public/export/vt_reward/portrait.html",
    ])
    def test_tpl10_contains_total_xp(self, tmpl):
        html = _render(tmpl, _reward_ctx(tier=3, total_xp=135))
        assert "135" in html


# ── VTC-TPL-11: optional fields are graceful when absent ─────────────────────

class TestOptionalFieldsGraceful:
    @pytest.mark.parametrize("tmpl", [
        "public/export/vt/landscape.html",
        "public/export/vt/portrait.html",
    ])
    def test_tpl11_no_stats_renders_without_error(self, tmpl):
        ctx = _single_game_ctx(
            best_score=None,
            avg_reaction_ms=None,
            top_skill_delta=None,
            xp_earned=0,
            player_overall=None,
            player_photo_url=None,
            player_primary_pos=None,
        )
        html = _render(tmpl, ctx)
        assert "DAILY COMPLETE" in html

    @pytest.mark.parametrize("tmpl", [
        "public/export/vt_reward/landscape.html",
        "public/export/vt_reward/portrait.html",
    ])
    def test_tpl12_reward_no_player_data_renders(self, tmpl):
        ctx = _reward_ctx(
            player_overall=None,
            player_photo_url=None,
            player_primary_pos=None,
            completed_game_names=[],
            total_xp=0,
        )
        html = _render(tmpl, ctx)
        assert "MASTERY" in html


# ── VTC-TPL-13..14: canvas viewport meta tag ─────────────────────────────────

class TestCanvasDimensions:
    def test_tpl13_landscape_viewport_is_1280(self):
        html = _render("public/export/vt/landscape.html", _single_game_ctx())
        assert 'width=1280' in html

    def test_tpl14_portrait_viewport_is_1080(self):
        html = _render("public/export/vt/portrait.html", _single_game_ctx())
        assert 'width=1080' in html
