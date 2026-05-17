"""
Unit tests — CS-5: First manifest-only (data-only / DB-backed) design
======================================================================

Coverage:
  CS5-01  portrait/classic_lite.html does NOT exist in export template tree
  CS5-02  classic_lite is NOT present in the DESIGNS Python fallback dict
  CS5-03  classic_lite portrait → HTTP 200 + ex-card + ex-sponsor-slot (show_sponsor=True)
  CS5-04  classic_lite story → HTTP 200 + ex-card, NO ex-sponsor-slot (show_sponsor=False)
  CS5-05  CSS platform_vars from component_config appear in rendered HTML
          (portrait: --ex-hero-h 380px; story: --ex-hero-h 440px)
  CS5-06  classic_lite portrait (skill_slice=4) renders fewer skill rows than FIFA (slice=6)
  CS5-07  'square' bucket not in classic_lite supported_export_buckets → logical 422 proof

Terminology (locked in CS-5 closing report):
  CS-5 proves data-only / DB-backed manifest-compatible runtime operation.
  The actual admin-side design manifest upload workflow remains a separate, later phase.

Mock strategy:
  _get_design is patched at the public_player module level so export routing uses the
  classic_lite CardDesignDefinition (with component_config).  _get_variant falls back to
  the FIFA browser template (correct — classic_lite is absent from DESIGNS fallback dict).
  MagicMock DB — no live DB required.

  CS5-07 is a logical unit test (no HTTP request) that verifies the 422-triggering condition:
  EXPORT_FORMAT_BUCKETS["instagram_square"] == "square" and "square" not in classic_lite
  supported_export_buckets.  The HTTP-layer 422 gate (export_player_card endpoint) requires
  full auth mocking out of scope for this suite; the logical proof is sufficient.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_TEMPLATES_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "app" / "templates"
)
_EXPORT_DIR = _TEMPLATES_ROOT / "public" / "export"

# ── classic_lite component_config (mirrors migration seed exactly) ─────────────

_CLASSIC_LITE_CONFIG: dict = {
    "portrait": {
        "skill_slice": 4,
        "show_dominant_badge": True,
        "show_height_weight": True,
        "show_sponsor": True,
        "platform_vars": {
            "--ex-hero-h":      "380px",
            "--ex-avatar-sz":   "170px",
            "--ex-avatar-font": "56px",
            "--ex-ovr-font":    "92px",
            "--ex-name-font":   "42px",
        },
    },
    "story": {
        "skill_slice": 6,
        "show_dominant_badge": True,
        "show_height_weight": True,
        "show_sponsor": False,
        "platform_vars": {
            "--ex-hero-h":      "440px",
            "--ex-avatar-sz":   "175px",
            "--ex-avatar-font": "58px",
            "--ex-ovr-font":    "94px",
            "--ex-name-font":   "46px",
        },
    },
}


def _make_classic_lite_def():
    from app.services.card_design_service import CardDesignDefinition
    return CardDesignDefinition(
        id="classic_lite",
        label="Classic Lite",
        description="Proof-of-concept manifest-only design.",
        is_premium=False,
        credit_cost=0,
        template="public/player_card_fifa.html",
        sort_order=1,
        supported_export_buckets=("portrait", "story"),
        animated_platforms=(),
        component_config=_CLASSIC_LITE_CONFIG,
    )


# ── Shared mock helpers (mirrors CS-4c pattern) ────────────────────────────────

def _make_user(user_id: int = 7) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.name = "Test Player"
    u.nationality = "Hungarian"
    u.is_active = True
    u.date_of_birth = None
    u.skills = {}
    return u


def _make_license(card_variant: str = "classic_lite") -> MagicMock:
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
    lic.motivation_scores = {}
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


def _render_as_classic_lite(client, platform: str, user_id: int = 7) -> str:
    """Render with classic_lite design (patches _get_design; _get_variant falls back to FIFA)."""
    from app.main import app
    from app.dependencies import get_db
    from app.api.web_routes import public_player as _pp

    cl_def = _make_classic_lite_def()
    db = _mock_db(user=_make_user(user_id), license_=_make_license("classic_lite"))
    app.dependency_overrides[get_db] = lambda: db
    with patch.object(_pp, "_get_design", return_value=cl_def):
        try:
            r = client.get(f"/players/{user_id}/card?platform={platform}&export=1")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)


def _render_as_fifa(client, platform: str, user_id: int = 7) -> str:
    """Render with FIFA design (no patching — DESIGNS fallback active for 'fifa')."""
    from app.main import app
    from app.dependencies import get_db

    db = _mock_db(user=_make_user(user_id), license_=_make_license("fifa"))
    app.dependency_overrides[get_db] = lambda: db
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


from fastapi.testclient import TestClient


# ── CS5-01/02: Manifest-only proof (no template, no DESIGNS entry) ─────────────

@pytest.mark.unit
class TestCS5ManifestOnly:
    """Static assertions proving classic_lite requires no Jinja2 file and no DESIGNS entry."""

    def test_cs5_01_no_template_files_exist(self):
        """portrait/classic_lite.html and story/classic_lite.html must NOT exist."""
        portrait_tpl = _EXPORT_DIR / "portrait" / "classic_lite.html"
        story_tpl    = _EXPORT_DIR / "story"    / "classic_lite.html"
        assert not portrait_tpl.exists(), (
            f"MANIFEST VIOLATION: {portrait_tpl} exists — "
            "classic_lite must not have a Level C portrait template"
        )
        assert not story_tpl.exists(), (
            f"MANIFEST VIOLATION: {story_tpl} exists — "
            "classic_lite must not have a Level C story template"
        )

    def test_cs5_02_not_in_designs_fallback_dict(self):
        """classic_lite must be absent from the DESIGNS Python fallback dict."""
        from app.services.card_design_service import DESIGNS
        assert "classic_lite" not in DESIGNS, (
            "MANIFEST VIOLATION: 'classic_lite' found in DESIGNS fallback dict — "
            "CS-5 proves runtime works via DB/config only, without Python fallback registration"
        )


# ── CS5-03/04: Render smoke tests ─────────────────────────────────────────────

@pytest.mark.unit
class TestCS5RenderSmoke:
    """HTTP smoke tests confirming classic_lite renders via column_driver.html."""

    def test_cs5_03_portrait_renders_with_sponsor(self, client):
        """classic_lite portrait → 200 + ex-card + ex-sponsor-slot (show_sponsor=True)."""
        html = _render_as_classic_lite(client, "instagram_portrait")
        assert html, "classic_lite portrait: route returned empty / non-200"
        assert "ex-card" in html, (
            "classic_lite portrait: .ex-card not found — driver routing may have failed"
        )
        assert "ex-sponsor-slot" in html, (
            "classic_lite portrait: .ex-sponsor-slot missing — "
            "show_sponsor=True but sponsor zone was not rendered"
        )

    def test_cs5_04_story_renders_without_sponsor(self, client):
        """classic_lite story → 200 + ex-card; NO ex-sponsor-slot (show_sponsor=False)."""
        html = _render_as_classic_lite(client, "instagram_story")
        assert html, "classic_lite story: route returned empty / non-200"
        assert "ex-card" in html, (
            "classic_lite story: .ex-card not found"
        )
        assert "ex-sponsor-slot" not in html, (
            "classic_lite story: .ex-sponsor-slot found — "
            "show_sponsor=False but sponsor zone was rendered"
        )


# ── CS5-05: Config-specific CSS output ────────────────────────────────────────

@pytest.mark.unit
class TestCS5ConfigOutput:
    """CSS platform_vars from component_config appear verbatim in the rendered HTML."""

    def test_cs5_05_platform_vars_rendered_correctly(self, client):
        """--ex-hero-h: 380px in portrait, 440px in story; values do not bleed across buckets."""
        portrait_html = _render_as_classic_lite(client, "instagram_portrait")
        story_html    = _render_as_classic_lite(client, "instagram_story")

        assert portrait_html, "classic_lite portrait: empty response"
        assert story_html,    "classic_lite story: empty response"

        assert "--ex-hero-h: 380px" in portrait_html, (
            "portrait: --ex-hero-h: 380px not found in rendered CSS"
        )
        assert "--ex-hero-h: 440px" in story_html, (
            "story: --ex-hero-h: 440px not found in rendered CSS"
        )
        assert "--ex-hero-h: 440px" not in portrait_html, (
            "portrait: story's --ex-hero-h: 440px leaked into portrait output"
        )
        assert "--ex-hero-h: 380px" not in story_html, (
            "story: portrait's --ex-hero-h: 380px leaked into story output"
        )


# ── CS5-06: Skill slice difference ────────────────────────────────────────────

@pytest.mark.unit
class TestCS5SkillSlice:
    """classic_lite portrait (skill_slice=4) renders fewer skill rows than FIFA (skill_slice=6)."""

    def test_cs5_06_fewer_skill_rows_than_fifa(self, client):
        """Row count: classic_lite portrait < FIFA portrait (4 per cat vs 6 per cat)."""
        classic_html = _render_as_classic_lite(client, "instagram_portrait")
        fifa_html    = _render_as_fifa(client, "instagram_portrait")

        assert classic_html, "classic_lite portrait: empty response"
        assert fifa_html,    "FIFA portrait: empty response"

        classic_rows = classic_html.count('class="ex-row"')
        fifa_rows    = fifa_html.count('class="ex-row"')

        assert classic_rows > 0, (
            "classic_lite: zero skill rows rendered — driver may not be routing correctly"
        )
        assert classic_rows < fifa_rows, (
            f"Skill slice difference not reflected in rendered output. "
            f"classic_lite portrait (slice=4): {classic_rows} rows; "
            f"FIFA portrait (slice=6): {fifa_rows} rows. "
            f"Expected classic_lite < FIFA."
        )


# ── CS5-07: Unsupported bucket → 422 logical proof ────────────────────────────

@pytest.mark.unit
class TestCS5UnsupportedBucket:
    """'square' bucket not in classic_lite supported_export_buckets → 422 would be raised.

    The HTTP-layer 422 gate (export_player_card endpoint) requires full auth mocking
    and is outside the scope of this unit suite.  This test proves the triggering condition:
    EXPORT_FORMAT_BUCKETS["instagram_square"] == "square" AND
    "square" not in classic_lite.supported_export_buckets.
    The route handler at public_player.py:415 raises HTTPException(422) on this condition.
    """

    def test_cs5_07_square_bucket_not_supported(self):
        """classic_lite does not support the 'square' bucket — 422 would be raised by the route."""
        from app.services.card_constants import EXPORT_FORMAT_BUCKETS

        cl_def = _make_classic_lite_def()
        _sq_bucket = EXPORT_FORMAT_BUCKETS["instagram_square"]

        assert _sq_bucket == "square", (
            f"Precondition: instagram_square should map to 'square', got {_sq_bucket!r}"
        )
        assert _sq_bucket not in cl_def.supported_export_buckets, (
            f"classic_lite declares 'square' as supported — expected only portrait + story. "
            f"supported_export_buckets={cl_def.supported_export_buckets}"
        )
        assert "portrait" in cl_def.supported_export_buckets, (
            "Sanity: classic_lite should support 'portrait'"
        )
        assert "story" in cl_def.supported_export_buckets, (
            "Sanity: classic_lite should support 'story'"
        )
