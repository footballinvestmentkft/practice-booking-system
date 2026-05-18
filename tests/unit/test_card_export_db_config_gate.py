"""
Regression tests — DB-backed component_config gate (F-4 closure)
================================================================

Closes the test gap identified in the CS-5 root cause audit (2026-05-18):
  - CS4C-02/03 access the DESIGNS Python fallback dict directly (no DB path).
  - CS4C-04/05 mock DB returns {} from _load_cache → falls back to DESIGNS.
  - Neither suite exercised the case where _get_design returns DB-sourced config,
    which shadows DESIGNS on the live server.

Coverage (6 tests):
  DBG-01  DB-sourced CS-5 portrait config (show_position_map=True, skill_slice=None)
          → column_driver renders PosMap CSS class when position data present
  DBG-02  DB-sourced CS-5 portrait config → 44 ex-row divs rendered (all skills)
  DBG-03  DB-sourced old portrait config (show_position_map absent, skill_slice=6)
          → no PosMap CSS class in output
  DBG-04  DB-sourced old portrait config → ≤ 21 ex-row divs (6 per category max)
  DBG-05  DB-sourced CS-5 story config → ex-sponsor-slot present
  DBG-06  DB-sourced CS-5 story config → 44 ex-row divs (all skills, skill_slice=None)

Mock strategy:
  _get_design is patched at the public_player module level (not card_design_service)
  so the route handler's routing logic uses the injected CardDesignDefinition.
  The license mock has motivation_scores with position data so position_nodes is
  non-empty → the PosMap macro can render when show_position_map=True.

  This simulates the exact path the live server takes when _maybe_reload returns
  DB-cached config, without requiring a live DB or Alembic migration.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Design definitions ────────────────────────────────────────────────────────

def _make_cs5_portrait_def():
    """FIFA CardDesignDefinition with CS-5 portrait+story config (post-migration state)."""
    from app.services.card_design_service import CardDesignDefinition
    return CardDesignDefinition(
        id="fifa",
        label="FIFA Classic",
        description="",
        is_premium=False,
        credit_cost=0,
        template="public/player_card_fifa.html",
        sort_order=0,
        archetype_id="column",
        supported_export_buckets=("square", "portrait", "story", "tiktok", "landscape", "banner"),
        animated_platforms=("instagram_square",),
        component_config={
            "portrait": {
                "skill_slice":           None,
                "show_dominant_badge":   True,
                "show_height_weight":    True,
                "show_extended_profile": True,
                "show_position_map":     True,
                "show_sponsor":          False,
                "platform_vars": {"--ex-posmap-h": "200px"},
            },
            "story": {
                "skill_slice":           None,
                "show_dominant_badge":   True,
                "show_height_weight":    True,
                "show_extended_profile": True,
                "show_position_map":     True,
                "show_sponsor":          True,
                "platform_vars": {
                    "--ex-hero-h":   "460px",
                    "--ex-posmap-h": "250px",
                },
            },
        },
    )


def _make_old_portrait_def():
    """FIFA CardDesignDefinition with pre-CS-5 config (pre-migration state)."""
    from app.services.card_design_service import CardDesignDefinition
    return CardDesignDefinition(
        id="fifa",
        label="FIFA Classic",
        description="",
        is_premium=False,
        credit_cost=0,
        template="public/player_card_fifa.html",
        sort_order=0,
        archetype_id="column",
        supported_export_buckets=("square", "portrait", "story", "tiktok", "landscape", "banner"),
        animated_platforms=("instagram_square",),
        component_config={
            "portrait": {
                "skill_slice":         6,
                "show_dominant_badge": False,
                "show_height_weight":  False,
                "show_sponsor":        False,
                "platform_vars":       {},
            },
            "story": {
                "skill_slice":         8,
                "show_dominant_badge": True,
                "show_height_weight":  True,
                "show_sponsor":        True,
                "platform_vars": {
                    "--ex-hero-h":    "460px",
                    "--ex-avatar-sz": "180px",
                },
            },
        },
    )


# ── Shared mock helpers ───────────────────────────────────────────────────────

def _make_user(user_id: int = 7) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.name = "Test Player"
    u.nickname = None
    u.age = 18
    u.gender = None
    u.nationality = "Hungarian"
    u.secondary_nationality = None
    u.current_location = None
    u.country = None
    u.date_of_birth = None
    u.xp_balance = None
    u.is_active = True
    u.email = "test@example.com"
    u.created_at = None
    u.skills = {}
    return u


def _make_license(card_variant: str = "fifa") -> MagicMock:
    lic = MagicMock()
    lic.card_variant = card_variant
    lic.card_theme = "default"
    lic.public_card_platform = None
    lic.published_card_variant = card_variant
    lic.published_card_theme = "default"
    lic.published_card_platform = None
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.is_active = True
    lic.onboarding_completed = False
    lic.card_theme_id = None
    lic.card_bg_compact_url = None
    lic.card_bg_showcase_url = None
    lic.player_card_photo_url = None
    lic.card_photo_portrait_url = None
    lic.card_photo_landscape_url = None
    lic.card_compact_photo_position = "left"
    lic.card_compact_focus_x = 50
    lic.card_compact_focus_y = 100
    lic.card_showcase_focus_x = 50
    lic.card_showcase_focus_y = 50
    lic.right_foot_score = None
    lic.left_foot_score = None
    lic.sponsor_logo_url = None
    lic.current_level = 1
    lic.max_achieved_level = 1
    lic.started_at = None
    lic.average_motivation_score = None
    # Position data present → position_nodes will be non-empty → PosMap can render
    lic.motivation_scores = {
        "position":    "striker",
        "positions":   ["striker", "centre_forward"],
        "height_cm":   180,
        "weight_kg":   75,
        "preferred_foot": "right",
    }
    return lic


def _mock_db(user=None, license_=None):
    from app.models.card_draft import CardDraft as _CardDraft

    db = MagicMock()
    _calls = [0]

    def _side_effect(*args):
        _calls[0] += 1
        q = MagicMock()
        if args and args[0] is _CardDraft:
            _draft = MagicMock()
            _draft.published_theme    = "default"
            _draft.published_variant  = (license_.published_card_variant if license_ else None) or "fifa"
            _draft.published_platform = None
            _draft.draft_theme    = "default"
            _draft.draft_variant  = _draft.published_variant
            _draft.draft_platform = None
            q.filter.return_value.first.return_value = _draft
        elif _calls[0] == 1:
            q.filter.return_value.first.return_value = user
        elif _calls[0] == 2:
            q.filter.return_value.first.return_value = license_
        else:
            q.filter.return_value.order_by.return_value.all.return_value = []
            q.filter.return_value.all.return_value = []
            q.join.return_value.filter.return_value.all.return_value = []
            q.outerjoin.return_value.filter.return_value.all.return_value = []
            q.all.return_value = []
        return q

    db.query.side_effect = _side_effect
    return db


def _render_with_design(client, platform: str, design_def, user_id: int = 7) -> str:
    """Render card with a specific CardDesignDefinition injected as the DB-sourced config."""
    from app.main import app
    from app.dependencies import get_db

    db = _mock_db(user=_make_user(user_id), license_=_make_license("fifa"))
    app.dependency_overrides[get_db] = lambda: db
    with patch("app.api.web_routes.public_player._get_design", return_value=design_def):
        try:
            r = client.get(f"/players/{user_id}/card?platform={platform}&export=1")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)


def _count_ex_rows(html: str) -> int:
    return html.count('class="ex-row"')


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── DBG-01/02: CS-5 portrait via DB-sourced config ───────────────────────────

@pytest.mark.unit
class TestDbConfigGatePortraitCS5:
    """DB-backed config with CS-5 values: PosMap renders, all 44 skills visible."""

    def test_dbg_01_posmap_rendered_when_show_position_map_true(self, client):
        """DB-sourced show_position_map=True → PosMap CSS class in output."""
        html = _render_with_design(client, "instagram_portrait", _make_cs5_portrait_def())
        assert html, "portrait (DB CS-5): route returned empty / non-200"
        assert "ex-card" in html, "portrait (DB CS-5): .ex-card not found"
        # position_nodes is non-empty (striker + centre_forward in motivation_scores)
        # column_driver renders PosMap block when show_position_map=True
        assert "ex-pos-panel-landscape" in html or "ex-pos-svg-landscape" in html, (
            "portrait (DB CS-5): PosMap not rendered — show_position_map=True with "
            "position data should produce .ex-pos-panel-landscape in HTML. "
            "Check that position_nodes is non-empty and column_driver pre_skills block fires."
        )

    def test_dbg_02_all_44_skills_rendered_when_slice_is_none(self, client):
        """DB-sourced skill_slice=None → 44 ex-row divs (all skills across 4 categories)."""
        html = _render_with_design(client, "instagram_portrait", _make_cs5_portrait_def())
        assert html, "portrait (DB CS-5): route returned empty / non-200"
        row_count = _count_ex_rows(html)
        assert row_count == 44, (
            f"portrait (DB CS-5): expected 44 ex-row divs (skill_slice=None), "
            f"got {row_count}. Breakdown: Outfield=19, Set Pieces=3, Mental=14, Physical=8."
        )


# ── DBG-03/04: Old portrait config via DB-sourced config ─────────────────────

@pytest.mark.unit
class TestDbConfigGatePortraitOld:
    """DB-backed config with pre-CS-5 values: PosMap absent, slice=6 enforced."""

    def test_dbg_03_posmap_absent_when_show_position_map_missing(self, client):
        """DB-sourced old config (no show_position_map key) → no PosMap in output."""
        html = _render_with_design(client, "instagram_portrait", _make_old_portrait_def())
        assert html, "portrait (DB old): route returned empty / non-200"
        assert "ex-card" in html, "portrait (DB old): .ex-card not found"
        assert "ex-pos-panel-landscape" not in html, (
            "portrait (DB old): PosMap rendered but should NOT be — "
            "old config has no show_position_map key."
        )

    def test_dbg_04_skill_rows_capped_at_6_with_old_slice(self, client):
        """DB-sourced skill_slice=6 → at most 21 ex-row divs (≤6 per category)."""
        html = _render_with_design(client, "instagram_portrait", _make_old_portrait_def())
        assert html, "portrait (DB old): route returned empty / non-200"
        row_count = _count_ex_rows(html)
        # 4 categories × 6 slice, but Set Pieces has only 3 → max = 6+3+6+6 = 21
        assert row_count <= 21, (
            f"portrait (DB old): expected ≤21 ex-row divs (skill_slice=6), "
            f"got {row_count}. Old config is not being applied."
        )
        assert row_count < 44, (
            f"portrait (DB old): DB-sourced old config is being ignored — "
            f"DESIGNS fallback (skill_slice=None) is taking effect instead. "
            f"This means the _get_design patch is not intercepting the call."
        )


# ── DBG-05/06: CS-5 story via DB-sourced config ──────────────────────────────

@pytest.mark.unit
class TestDbConfigGateStoryCS5:
    """DB-backed CS-5 story config: sponsor + all 44 skills."""

    def test_dbg_05_sponsor_slot_present_when_show_sponsor_true(self, client):
        """DB-sourced story show_sponsor=True → ex-sponsor-slot in output."""
        html = _render_with_design(client, "instagram_story", _make_cs5_portrait_def())
        assert html, "story (DB CS-5): route returned empty / non-200"
        assert "ex-sponsor-slot" in html, (
            "story (DB CS-5): .ex-sponsor-slot not found — "
            "show_sponsor=True but sponsor zone was not rendered."
        )

    def test_dbg_06_all_44_skills_rendered_story(self, client):
        """DB-sourced story skill_slice=None → 44 ex-row divs."""
        html = _render_with_design(client, "instagram_story", _make_cs5_portrait_def())
        assert html, "story (DB CS-5): route returned empty / non-200"
        row_count = _count_ex_rows(html)
        assert row_count == 44, (
            f"story (DB CS-5): expected 44 ex-row divs (skill_slice=None), "
            f"got {row_count}."
        )
