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
  CS5-06  classic_lite portrait (skill_slice=4) renders fewer skill rows than FClassic (slice=6)
  CS5-07  'square' bucket not in classic_lite supported_export_buckets → logical 422 proof

Terminology (locked in CS-5 closing report):
  CS-5 proves data-only / DB-backed manifest-compatible runtime operation.
  The actual admin-side design manifest upload workflow remains a separate, later phase.

Mock strategy:
  _get_design is patched at the public_player module level so export routing uses the
  classic_lite CardDesignDefinition (with component_config).  _get_variant falls back to
  the FClassic browser template (correct — classic_lite is absent from DESIGNS fallback dict).
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
        template="public/player_card_fclassic.html",
        sort_order=1,
        archetype_id="column",   # CS-6 A-model: required for driver routing
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
            _draft.published_variant  = (license_.published_card_variant  if license_ else None) or "fclassic"
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
    """Render with classic_lite design (patches _get_design; _get_variant falls back to FClassic)."""
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
    """Render with FClassic design (no patching — DESIGNS fallback active for 'fclassic')."""
    from app.main import app
    from app.dependencies import get_db

    db = _mock_db(user=_make_user(user_id), license_=_make_license("fclassic"))
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
    """classic_lite portrait (skill_slice=4) renders fewer skill rows than FClassic (skill_slice=6)."""

    def test_cs5_06_fewer_skill_rows_than_fifa(self, client):
        """Row count: classic_lite portrait < FClassic portrait (4 per cat vs 6 per cat)."""
        classic_html = _render_as_classic_lite(client, "instagram_portrait")
        fifa_html    = _render_as_fifa(client, "instagram_portrait")

        assert classic_html, "classic_lite portrait: empty response"
        assert fifa_html,    "FClassic portrait: empty response"

        classic_rows = classic_html.count('class="ex-row"')
        fifa_rows    = fifa_html.count('class="ex-row"')

        assert classic_rows > 0, (
            "classic_lite: zero skill rows rendered — driver may not be routing correctly"
        )
        assert classic_rows < fifa_rows, (
            f"Skill slice difference not reflected in rendered output. "
            f"classic_lite portrait (slice=4): {classic_rows} rows; "
            f"FClassic portrait (slice=6): {fifa_rows} rows. "
            f"Expected classic_lite < FClassic."
        )


# ── CS5-07 / CS5-07b: Unsupported bucket → 422 ────────────────────────────────

def _mock_db_for_export(user_id: int = 7, card_variant: str = "classic_lite"):
    """Minimal DB mock for the /card/export endpoint (model-type dispatch, no counter)."""
    from app.models.user import User as _User
    from app.models.license import UserLicense as _UserLicense

    _user = MagicMock()
    _user.id = user_id
    _user.is_active = True

    _lic = MagicMock()
    _lic.card_variant = card_variant

    db = MagicMock()

    def _side_effect(*args):
        q = MagicMock()
        if args and args[0] is _User:
            q.filter.return_value.first.return_value = _user
        elif args and args[0] is _UserLicense:
            q.filter.return_value.first.return_value = _lic
        else:
            q.filter.return_value.first.return_value = None
            q.all.return_value = []
        return q

    db.query.side_effect = _side_effect
    return db


@pytest.mark.unit
class TestCS5UnsupportedBucket:
    """'square' bucket not in classic_lite supported_export_buckets → 422.

    CS5-07:  logical/condition proof — fast, no server required.
    CS5-07b: route-level HTTP 422 — exercises the actual export_player_card endpoint
             with auth + rate-limit dependencies overridden.
    """

    def test_cs5_07_square_bucket_condition(self):
        """Logical proof: EXPORT_FORMAT_BUCKETS['instagram_square']=='square' not in classic_lite."""
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
        assert "portrait" in cl_def.supported_export_buckets
        assert "story" in cl_def.supported_export_buckets

    def test_cs5_07b_route_level_422_for_instagram_square(self, client):
        """HTTP 422 from /players/{id}/card/export?platform=instagram_square for classic_lite.

        Auth and rate-limit are overridden; _get_supported_buckets is patched to return
        classic_lite's actual supported buckets — proving the route raises 422 at line 415-418
        of public_player.py when the requested bucket is absent from supported_export_buckets.
        """
        from app.main import app
        from app.dependencies import get_db, get_current_user_web
        from app.api.web_routes import public_player as _pp
        from app.services import card_export_service as _export_svc

        from app.models.user import UserRole
        _mock_current_user = MagicMock()
        _mock_current_user.id = 7
        _mock_current_user.role = UserRole.ADMIN  # bypass ownership guard; test is about bucket validation

        db = _mock_db_for_export(user_id=7, card_variant="classic_lite")
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user_web] = lambda: _mock_current_user

        with (
            patch.object(_pp, "_get_supported_buckets", return_value=("portrait", "story")),
            patch.object(_export_svc, "check_export_rate_limit", return_value=True),
        ):
            try:
                r = client.get("/players/7/card/export?platform=instagram_square")
                assert r.status_code == 422, (
                    f"Expected HTTP 422 for classic_lite + instagram_square "
                    f"(unsupported bucket 'square'), got {r.status_code}. "
                    f"Body: {r.text[:300]}"
                )
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user_web, None)
