"""
Unit tests — CS-4c: component_config-based column archetype driver
==================================================================

Coverage:
  CS4C-01  column_driver.html exists and extends column_archetype.html
  CS4C-02  FIFA component_config.portrait has correct field values
  CS4C-03  FIFA component_config.story has correct field values
  CS4C-04  instagram_portrait → driver route → HTTP 200 + ex-card
  CS4C-05  instagram_story → driver route → HTTP 200 + ex-card + ex-sponsor-slot
  CS4C-06  portrait driver output normalised == Level C portrait output normalised (zero-diff)
  CS4C-07  story driver output normalised == Level C story output normalised (zero-diff)
  CS4C-08  Design without component_config falls back to file-based Level C routing

Mock strategy (smoke + zero-diff tests):
  Identical to test_card_export_cs4b.py — MagicMock user + license, card_variant="fifa".
  get_db overridden; no live DB required.
  CS4C-06/07 compare normalised rendered HTML from two TestClient requests:
    (a) driver path  (fifa, component_config populated — current default)
    (b) Level C path (same design but component_config forcibly cleared to {} so the
        router falls back to portrait/fifa.html / story/fifa.html)
  The normalisation strips whitespace-only differences so the comparison is stable.

Zero-diff gate note (CS4C-06/07):
  If these assertions fail the driver diverges from the Level C templates.
  Investigate the specific diff before merging.  These tests are the primary
  merge-blocking gate for CS-4c.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_TEMPLATES_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "app" / "templates"
)
_DRIVERS_DIR = _TEMPLATES_ROOT / "public" / "export" / "shared" / "drivers"
_EXPORT_DIR  = _TEMPLATES_ROOT / "public" / "export"


# ── Normalisation (reuse CS-4b strategy) ──────────────────────────────────────

def _normalize(text: str) -> str:
    """Strip comment-only diffs and collapse whitespace for stable HTML comparison."""
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"\{%-?\s*extends\s+['\"][^'\"]+['\"]\s*-?%\}", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


# ── Shared mock helpers ────────────────────────────────────────────────────────

def _make_user(user_id: int = 7) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.name = "Test Player"
    u.nationality = "Hungarian"
    u.is_active = True
    u.date_of_birth = None
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
    lic.player_card_photo_url = None     # must be explicit to avoid MagicMock id churn
    lic.card_photo_portrait_url = None
    lic.card_photo_landscape_url = None
    lic.motivation_scores = {}           # prevents .get() returning different MagicMock ids
    lic.compact_photo_position = "left"
    lic.compact_focus_x = 50
    lic.compact_focus_y = 20
    lic.right_foot_score = None
    lic.left_foot_score = None
    lic.sponsor_logo_url = None
    lic.current_level = 1
    lic.max_achieved_level = 1
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
            _draft.published_theme    = (license_.published_card_theme    if license_ else None) or "default"
            _draft.published_variant  = (license_.published_card_variant  if license_ else None) or "fifa"
            _draft.published_platform = (license_.published_card_platform if license_ else None)
            _draft.draft_theme    = _draft.published_theme
            _draft.draft_variant  = _draft.published_variant
            _draft.draft_platform = _draft.published_platform
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


def _render(client, platform: str, user_id: int = 7) -> str:
    from app.main import app
    from app.dependencies import get_db

    db = _mock_db(user=_make_user(user_id), license_=_make_license("fifa"))
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = client.get(f"/players/{user_id}/card?platform={platform}&export=1")
        return r.text if r.status_code == 200 else ""
    finally:
        app.dependency_overrides.pop(get_db, None)


def _render_with_empty_component_config(client, platform: str, user_id: int = 7) -> str:
    """Render via file-based Level C by zeroing out component_config in the service."""
    from app.main import app
    from app.dependencies import get_db
    from app.services import card_design_service as _cds

    original_get_design = _cds.get_design

    def _patched_get_design(design_id: str, db=None):
        defn = original_get_design(design_id, db)
        # Return a copy with empty component_config → forces file-based routing
        from dataclasses import replace
        return replace(defn, component_config={})

    db = _mock_db(user=_make_user(user_id), license_=_make_license("fifa"))
    app.dependency_overrides[get_db] = lambda: db
    with patch.object(_cds, "get_design", side_effect=_patched_get_design):
        try:
            r = client.get(f"/players/{user_id}/card?platform={platform}&export=1")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── CS4C-01/02/03: Structural + config integrity ───────────────────────────────

@pytest.mark.unit
class TestCS4cStructural:
    """Static file-level assertions — no live server."""

    def test_cs4c_01_column_driver_extends_column_archetype(self):
        """column_driver.html must exist and extend column_archetype.html."""
        driver = _DRIVERS_DIR / "column_driver.html"
        assert driver.exists(), "column_driver.html not found in drivers/"
        src = driver.read_text(encoding="utf-8")
        assert 'extends "public/export/shared/column_archetype.html"' in src, (
            "column_driver.html does not extend column_archetype.html"
        )

    def test_cs4c_02_fifa_portrait_config_fields(self):
        """FIFA component_config.portrait must have correct field values."""
        from app.services.card_design_service import DESIGNS
        cfg = DESIGNS["fifa"].component_config
        assert "portrait" in cfg, "FIFA component_config missing 'portrait' key"
        p = cfg["portrait"]
        assert p["skill_slice"] == 6
        assert p["show_dominant_badge"] is False
        assert p["show_height_weight"] is False
        assert p["show_sponsor"] is False
        assert p["platform_vars"] == {}

    def test_cs4c_03_fifa_story_config_fields(self):
        """FIFA component_config.story must have correct field values."""
        from app.services.card_design_service import DESIGNS
        cfg = DESIGNS["fifa"].component_config
        assert "story" in cfg, "FIFA component_config missing 'story' key"
        s = cfg["story"]
        assert s["skill_slice"] == 8
        assert s["show_dominant_badge"] is True
        assert s["show_height_weight"] is True
        assert s["show_sponsor"] is True
        assert "--ex-hero-h" in s["platform_vars"]
        assert s["platform_vars"]["--ex-hero-h"] == "460px"


# ── CS4C-04/05: Smoke tests (driver route active) ─────────────────────────────

@pytest.mark.unit
class TestCS4cDriverSmoke:
    """HTTP smoke tests confirming driver routing returns valid HTML."""

    def test_cs4c_04_portrait_driver_renders(self, client):
        """instagram_portrait via driver → 200 + ex-card present."""
        html = _render(client, "instagram_portrait")
        assert html, "instagram_portrait (driver): route returned empty / non-200"
        assert "ex-card" in html, (
            "instagram_portrait (driver): .ex-card not found — template may have failed"
        )

    def test_cs4c_05_story_driver_renders_with_sponsor(self, client):
        """instagram_story via driver → 200 + ex-card + sponsor slot present."""
        html = _render(client, "instagram_story")
        assert html, "instagram_story (driver): route returned empty / non-200"
        assert "ex-card" in html, (
            "instagram_story (driver): .ex-card not found"
        )
        assert "ex-sponsor-slot" in html, (
            "instagram_story (driver): .ex-sponsor-slot not found — "
            "show_sponsor=True but sponsor zone was not rendered"
        )


# ── CS4C-06/07: Zero-diff gate (merge-blocking) ───────────────────────────────

@pytest.mark.unit
class TestCS4cZeroDiff:
    """Normalised HTML equality between driver and Level C file output.

    These are the primary merge-blocking assertions for CS-4c.
    Failure means the driver diverges from the existing Level C template.
    Investigate the specific diff before proceeding.
    """

    def test_cs4c_06_portrait_driver_equals_level_c(self, client):
        """Driver-rendered portrait == Level C portrait/fifa.html rendered (normalised)."""
        driver_html   = _render(client, "instagram_portrait")
        level_c_html  = _render_with_empty_component_config(client, "instagram_portrait")
        assert driver_html, "portrait driver rendered empty"
        assert level_c_html, "portrait Level C rendered empty (fallback broken?)"
        assert _normalize(driver_html) == _normalize(level_c_html), (
            "ZERO-DIFF GATE FAILED: portrait driver output differs from Level C output.\n"
            "Driver and portrait/fifa.html are no longer equivalent — investigate before merging."
        )

    def test_cs4c_07_story_driver_equals_level_c(self, client):
        """Driver-rendered story == Level C story/fifa.html rendered (normalised)."""
        driver_html   = _render(client, "instagram_story")
        level_c_html  = _render_with_empty_component_config(client, "instagram_story")
        assert driver_html, "story driver rendered empty"
        assert level_c_html, "story Level C rendered empty (fallback broken?)"
        assert _normalize(driver_html) == _normalize(level_c_html), (
            "ZERO-DIFF GATE FAILED: story driver output differs from Level C output.\n"
            "Driver and story/fifa.html are no longer equivalent — investigate before merging."
        )


# ── CS4C-08: Fallback routing ──────────────────────────────────────────────────

@pytest.mark.unit
class TestCS4cFallback:
    """Design without component_config falls back to file-based Level C routing."""

    def test_cs4c_08_empty_component_config_uses_level_c(self, client):
        """When component_config is {}, the file-based Level C template is used."""
        html = _render_with_empty_component_config(client, "instagram_portrait")
        assert html, "fallback: route returned empty / non-200"
        assert "ex-card" in html, (
            "fallback: .ex-card not found — Level C fallback is broken"
        )
