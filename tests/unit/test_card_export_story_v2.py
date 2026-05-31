"""
Unit tests — STORY-v2: Instagram Story standalone template
==========================================================

Coverage:
  SV-01  story/fclassic.html extends export_base.html directly (Level C standalone)
  SV-02  Hero height CSS var is 500px  (story layout anchor for 1080×1920 canvas)
  SV-03  OVR block and name block both use bottom: 20px  (alignment invariant)
  SV-04  Meta value max-width is 110px  (no nationality truncation on larger canvas)
  SV-05  Sponsor slot CSS class present in template source (show_sponsor path)
  SV-06  Unknown position → no ex-posmap-footer; sponsor slot still renders
  SV-07  Known position + show_position_map=True → ex-posmap-footer rendered
  SV-08  show_sponsor=True in component_config → ex-sponsor-slot in output
  SV-09  Story renders 44 skill rows (skill_slice=None)
  SV-10  Story bucket routes to Level C file, not column_driver.html
  SV-11  Two-column skills zone present (ex-skills-col-left + ex-skills-col-right)
  SV-12  Long name (32 chars) renders without HTTP error
  SV-13  No nationality → HTTP 200, meta strip still renders
  SV-14  Photo absent → ex-hero-placeholder rendered
  SV-15  Photo present → ex-hero-photo img rendered
  SV-16  Typography: story/fclassic.html imports card_ovr_badge macro (Bebas Neue)
  SV-17  OVR badge element (ex-ovr-badge) rendered in output

Regression baseline locked: 2026-05-18, STORY-v2 pixel-stable sign-off.
Baseline PNGs: qa_exports/story_v2/baseline/  (contact_sheet.png + 8 individual PNGs)
Layout invariants: hero=500px, meta=64px, posmap=250px, sponsor=100px,
                   OVR/name bottom=20px, left=19 rows, right=25 rows, overflow=0
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_TEMPLATES_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "app" / "templates"
)
_STORY_TMPL = _TEMPLATES_ROOT / "public" / "export" / "story" / "fclassic.html"


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_user(name: str = "Bence Kovács", nationality: str = "Hungarian") -> MagicMock:
    u = MagicMock()
    u.id = 7
    u.name = name
    u.nationality = nationality
    u.is_active = True
    u.date_of_birth = None
    u.skills = {}
    u.email = "test@test.com"
    u.nickname = None
    u.age = None
    u.gender = None
    u.current_location = None
    u.country = None
    u.created_at = None
    u.xp_balance = 0
    return u


def _make_license(
    position: str = "CM",
    positions: list[str] | None = None,
    portrait_url: str | None = None,
) -> MagicMock:
    lic = MagicMock()
    lic.card_variant = "fclassic"
    lic.card_theme = "default"
    lic.public_card_platform = None
    lic.published_card_variant = "fclassic"
    lic.published_card_theme = "default"
    lic.published_card_platform = None
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.is_active = True
    lic.onboarding_completed = True
    lic.card_theme_id = None
    lic.card_bg_compact_url = None
    lic.card_bg_showcase_url = None
    lic.player_card_photo_url = portrait_url
    lic.card_photo_portrait_url = portrait_url
    lic.card_photo_landscape_url = None
    lic.motivation_scores = {
        "position": position,
        "positions": positions if positions is not None else ([position] if position != "Unknown" else []),
        "height_cm": 178,
        "weight_kg": 72,
    }
    lic.compact_photo_position = "left"
    lic.compact_focus_x = 50
    lic.compact_focus_y = 20
    lic.card_compact_photo_position = "left"
    lic.card_compact_focus_x = 50
    lic.card_compact_focus_y = 20
    lic.card_showcase_focus_x = 50
    lic.card_showcase_focus_y = 50
    lic.right_foot_score = 72.0
    lic.left_foot_score = 45.0
    lic.sponsor_logo_url = None
    lic.current_level = 4
    lic.max_achieved_level = 5
    lic.started_at = None
    lic.average_motivation_score = None
    return lic


def _make_db(user: MagicMock, lic: MagicMock) -> MagicMock:
    from app.models.card_draft import CardDraft as _CD

    db = MagicMock()
    calls = [0]

    def _se(*args):
        calls[0] += 1
        q = MagicMock()
        if args and args[0] is _CD:
            d = MagicMock()
            d.published_theme = "default"
            d.published_variant = "fclassic"
            d.published_platform = None
            d.draft_theme = "default"
            d.draft_variant = "fclassic"
            d.draft_platform = None
            q.filter.return_value.first.return_value = d
        elif calls[0] == 1:
            q.filter.return_value.first.return_value = user
        elif calls[0] == 2:
            q.filter.return_value.first.return_value = lic
        else:
            q.filter.return_value.order_by.return_value.all.return_value = []
            q.filter.return_value.all.return_value = []
            q.join.return_value.filter.return_value.all.return_value = []
            q.outerjoin.return_value.filter.return_value.all.return_value = []
            q.all.return_value = []
        return q

    db.query.side_effect = _se
    return db


def _render(
    client,
    position: str = "CM",
    positions: list[str] | None = None,
    name: str = "Bence Kovács",
    nationality: str = "Hungarian",
    portrait_url: str | None = None,
    user_id: int = 7,
) -> tuple[int, str]:
    from app.main import app
    from app.dependencies import get_db

    user = _make_user(name, nationality)
    lic  = _make_license(position, positions, portrait_url)
    db   = _make_db(user, lic)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = client.get(f"/players/{user_id}/card?platform=instagram_story&export=1")
        return r.status_code, r.text
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── SV-01..05: Static template assertions ─────────────────────────────────────

@pytest.mark.unit
class TestStoryV2Static:
    """File-level invariants — no HTTP request."""

    def test_sv_01_extends_export_base_directly(self):
        """story/fclassic.html must extend export_base.html (Level C standalone, not column_archetype)."""
        assert _STORY_TMPL.exists(), "story/fclassic.html missing"
        src = _STORY_TMPL.read_text(encoding="utf-8")
        assert 'extends "public/export/shared/export_base.html"' in src, (
            "STORY-v2 must extend export_base.html directly"
        )
        assert 'extends "public/export/shared/column_archetype.html"' not in src, (
            "STORY-v2 must NOT extend column_archetype.html (dead-code CS-4b architecture)"
        )

    def test_sv_02_hero_height_500px(self):
        """Story hero CSS var must be 500px (9:16 canvas layout anchor)."""
        src = _STORY_TMPL.read_text(encoding="utf-8")
        assert "--ex-hero-h:     500px" in src or "--ex-hero-h: 500px" in src, (
            "STORY-v2 hero height must be 500px"
        )

    def test_sv_03_ovr_and_name_same_bottom(self):
        """OVR block and name block must both use bottom: 20px (story alignment invariant)."""
        src = _STORY_TMPL.read_text(encoding="utf-8")
        ovr_m  = re.search(r'\.ex-ovr-block\s*\{[^}]*bottom:\s*(\d+)px', src, re.DOTALL)
        name_m = re.search(r'\.ex-hero-name-block\s*\{[^}]*bottom:\s*(\d+)px', src, re.DOTALL)
        assert ovr_m,  ".ex-ovr-block CSS block not found"
        assert name_m, ".ex-hero-name-block CSS block not found"
        assert ovr_m.group(1) == name_m.group(1), (
            f"OVR bottom ({ovr_m.group(1)}px) ≠ name bottom ({name_m.group(1)}px)"
        )
        assert ovr_m.group(1) == "20", f"Expected bottom: 20px, got {ovr_m.group(1)}px"

    def test_sv_04_meta_value_max_width_110px(self):
        """Meta value max-width must be ≥100px (story uses 110px for larger canvas)."""
        src = _STORY_TMPL.read_text(encoding="utf-8")
        m = re.search(r'\.ex-meta-value\s*\{[^}]*max-width:\s*(\d+)px', src, re.DOTALL)
        assert m, ".ex-meta-value max-width not found"
        assert int(m.group(1)) >= 100, f"max-width {m.group(1)}px too small"

    def test_sv_05_sponsor_slot_css_defined(self):
        """ex-sponsor-slot CSS class must be defined in story/fclassic.html."""
        src = _STORY_TMPL.read_text(encoding="utf-8")
        assert "ex-sponsor-slot" in src, (
            "ex-sponsor-slot class missing — sponsor zone not defined"
        )


# ── SV-06..10: HTTP rendering tests ───────────────────────────────────────────

@pytest.mark.unit
class TestStoryV2Rendering:
    """HTTP smoke tests — routing and content invariants."""

    def test_sv_06_unknown_position_no_posmap_but_sponsor(self, client):
        """Unknown position → no PosMap div; sponsor slot still renders."""
        status, html = _render(client, position="Unknown", positions=[])
        assert status == 200
        assert '<div class="ex-posmap-footer">' not in html, (
            "PosMap rendered for Unknown position"
        )
        assert "ex-sponsor-slot" in html, (
            "Sponsor slot missing — show_sponsor=True should always render it"
        )

    def test_sv_07_known_position_posmap_rendered(self, client):
        """CM position → ex-posmap-footer present (show_position_map=True in story config)."""
        status, html = _render(client, position="CM", positions=["CM", "RB"])
        assert status == 200
        assert '<div class="ex-posmap-footer">' in html, (
            "PosMap missing for known position"
        )

    def test_sv_08_sponsor_slot_always_rendered(self, client):
        """Story show_sponsor=True in component_config → ex-sponsor-slot always present."""
        status, html = _render(client)
        assert status == 200
        assert "ex-sponsor-slot" in html, (
            "ex-sponsor-slot missing — story component_config has show_sponsor=True"
        )

    def test_sv_09_full_44_skill_rows(self, client):
        """Story must render all 44 skill rows (skill_slice=None)."""
        status, html = _render(client)
        assert status == 200
        count = html.count('<div class="ex-row">')
        assert count == 44, f"Expected 44 skill rows, got {count}"

    def test_sv_10_routes_to_level_c_not_driver(self, client):
        """Story bucket must route to story/fclassic.html (Level C), not column_driver.html."""
        status, html = _render(client)
        assert status == 200
        assert "ex-hero-photo" in html or "ex-hero-placeholder" in html, (
            "Neither ex-hero-photo nor ex-hero-placeholder found — "
            "probably rendering column_driver.html instead of story/fclassic.html"
        )


# ── SV-11..15: Structure and content tests ────────────────────────────────────

@pytest.mark.unit
class TestStoryV2Structure:

    def test_sv_11_two_column_skills_zone(self, client):
        """Skills zone must have both left and right column containers."""
        status, html = _render(client)
        assert status == 200
        assert "ex-skills-col-left"  in html
        assert "ex-skills-col-right" in html

    def test_sv_12_long_name_renders_ok(self, client):
        status, html = _render(client, name="Kristóf Fekete-Molnár Tibor Jr.")
        assert status == 200
        assert "ex-card" in html

    def test_sv_13_no_nationality_renders_ok(self, client):
        status, html = _render(client, nationality=None)
        assert status == 200
        assert "ex-meta-strip" in html

    def test_sv_14_no_photo_shows_placeholder(self, client):
        status, html = _render(client, portrait_url=None)
        assert status == 200
        assert "ex-hero-placeholder" in html

    def test_sv_15_photo_url_renders_img(self, client):
        status, html = _render(client, portrait_url="https://example.com/photo.jpg")
        assert status == 200
        assert '<img class="ex-hero-photo"' in html

    def test_sv_16_typography_ovr_badge_macro_imported(self):
        """story/fclassic.html must import card_ovr_badge macro (Bebas Neue OVR badge)."""
        src = _STORY_TMPL.read_text(encoding="utf-8")
        assert 'from "macros/card_ovr_badge.html"' in src, (
            "card_ovr_badge macro import missing — typography Step E not applied"
        )
        assert "ovr_badge(" in src, (
            "ovr_badge() macro call missing — OVR badge not used in template"
        )

    def test_sv_17_ovr_badge_element_in_rendered_output(self, client):
        """Rendered HTML must contain ex-ovr-badge element (circular OVR badge)."""
        status, html = _render(client)
        assert status == 200
        assert 'class="ex-ovr-badge"' in html, (
            "ex-ovr-badge element missing — card_ovr_badge macro not rendering"
        )
