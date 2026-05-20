"""
Unit tests — PORT-v2: Instagram Portrait standalone template
============================================================
Split Hero v3.2b + typography system — 2026-05-18

Coverage:
  PV-01  portrait/fifa.html extends export_base.html directly (Level C standalone)
  PV-02  Split hero height CSS var is 660px  (--ex-split-hero-h layout anchor)
  PV-03  Split Hero architecture: ex-split-hero, ex-photo-panel, ex-info-panel present
  PV-04  Info panel uses ex-meta-val class for meta values
  PV-05  Unknown position → no ex-posmap-inline div in output
  PV-06  Known position + show_position_map=True → ex-posmap-inline div rendered
  PV-07  PosMap guard requires primary_pos_label (Unknown never renders PosMap)
  PV-08  Portrait renders 44 skill rows (full skill coverage, skill_slice=None)
  PV-09  Portrait bucket routes to Level C file, not column_driver.html
  PV-10  Skills zone has 3-column structure (left + mid + right)
  PV-11  Long name (32 chars) renders without HTTP error — no overflow crash
  PV-12  No nationality → HTTP 200, meta list still renders
  PV-13  Photo URL absent → ex-hero-placeholder rendered (initials fallback)
  PV-14  Photo URL present → ex-hero-photo img rendered
  PV-15  Export mode renders ex-card root element
  PV-16  Typography: portrait/fifa.html imports card_ovr_badge macro (Bebas Neue)
  PV-17  OVR badge element (ex-ovr-badge) rendered in output

Regression baseline locked: 2026-05-18, PORT-v2 Split Hero v3.2b.
Baseline PNGs: qa_exports/portrait_v2/v5/  (contact_sheet + 8 individual PNGs)
Metrics JSON: qa_exports/portrait_v2/v5/metrics_baseline_port_v2_1080x1350.json
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
_PORTRAIT_TMPL = _TEMPLATES_ROOT / "public" / "export" / "portrait" / "fifa.html"


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
    variant: str = "fifa",
    position: str = "CM",
    positions: list[str] | None = None,
    portrait_url: str | None = None,
) -> MagicMock:
    lic = MagicMock()
    lic.card_variant = variant
    lic.card_theme = "default"
    lic.public_card_platform = None
    lic.published_card_variant = variant
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
        "preferred_foot": "right",
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
            d.published_variant = lic.published_card_variant or "fifa"
            d.published_platform = None
            d.draft_theme = "default"
            d.draft_variant = d.published_variant
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
    lic  = _make_license("fifa", position, positions, portrait_url)
    db   = _make_db(user, lic)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = client.get(f"/players/{user_id}/card?platform=instagram_portrait&export=1")
        return r.status_code, r.text
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── PV-01..04: Static template assertions ─────────────────────────────────────

@pytest.mark.unit
class TestPortV2Static:
    """File-level invariants — no HTTP request."""

    def test_pv_01_extends_export_base_directly(self):
        """portrait/fifa.html must extend export_base.html (Level C standalone, not column_archetype)."""
        assert _PORTRAIT_TMPL.exists(), "portrait/fifa.html missing"
        src = _PORTRAIT_TMPL.read_text(encoding="utf-8")
        assert 'extends "public/export/shared/export_base.html"' in src, (
            "PORT-v2 must extend export_base.html directly, not column_archetype"
        )
        assert 'column_archetype' not in src, (
            "PORT-v2 must NOT extend column_archetype (driver architecture)"
        )

    def test_pv_02_split_hero_height_660px(self):
        """Split hero height CSS var must be 660px (--ex-split-hero-h, layout anchor for v3.2b)."""
        src = _PORTRAIT_TMPL.read_text(encoding="utf-8")
        assert "--ex-split-hero-h:     660px" in src or "--ex-split-hero-h: 660px" in src, (
            "PORT-v2 Split Hero v3.2b must define --ex-split-hero-h: 660px"
        )

    def test_pv_03_split_hero_architecture(self):
        """Split Hero v3.1: ex-split-hero, ex-photo-panel, ex-info-panel must all be defined."""
        src = _PORTRAIT_TMPL.read_text(encoding="utf-8")
        assert "ex-split-hero" in src, (
            ".ex-split-hero class missing — Split Hero v3.1 architecture not present"
        )
        assert "ex-photo-panel" in src, (
            ".ex-photo-panel class missing — photo panel zone not defined"
        )
        assert "ex-info-panel" in src, (
            ".ex-info-panel class missing — info panel zone not defined"
        )
        assert "ex-photo-name" in src, (
            ".ex-photo-name class missing — player name on photo panel not defined"
        )
        assert 'extends "public/export/shared/column_archetype.html"' not in src, (
            "Must NOT extend column_archetype (Split Hero v3.1 is Level C standalone)"
        )

    def test_pv_04_info_panel_meta_val_class(self):
        """Info panel must use ex-meta-val class (vertical meta list, Split Hero v3.1)."""
        src = _PORTRAIT_TMPL.read_text(encoding="utf-8")
        assert "ex-meta-val" in src, (
            "ex-meta-val class missing — vertical meta list not defined in info panel"
        )
        assert "ex-meta-lbl" in src, (
            "ex-meta-lbl class missing — meta label element not defined"
        )
        assert "ex-meta-list" in src, (
            "ex-meta-list class missing — meta container not defined"
        )


# ── PV-05..09: HTTP rendering tests ───────────────────────────────────────────

@pytest.mark.unit
class TestPortV2Rendering:
    """HTTP smoke tests — verify routing and content invariants."""

    def test_pv_05_unknown_position_no_posmap(self, client):
        """Unknown position → ex-posmap-inline element must NOT be rendered (CSS class still in
        stylesheet; check for the div element, not the class string)."""
        status, html = _render(client, position="Unknown", positions=[])
        assert status == 200, f"HTTP {status}"
        assert '<div class="ex-posmap-inline">' not in html, (
            "ex-posmap-inline div rendered for Unknown position — guard broken"
        )

    def test_pv_06_known_position_posmap_rendered(self, client):
        """Known position (CM) + show_position_map=True → ex-posmap-skills div rendered in skills zone."""
        status, html = _render(client, position="CM", positions=["CM", "RB"])
        assert status == 200, f"HTTP {status}"
        assert '<div class="ex-posmap-skills">' in html, (
            "ex-posmap-skills div missing for known position — PosMap not rendered in skills zone"
        )

    def test_pv_07_posmap_guard_primary_pos_label(self, client):
        """PosMap guard: position='Unknown' sets primary_pos_label=None → no posmap div
        even if position_nodes returns non-empty list (guard checks primary_pos_label)."""
        status, html = _render(client, position="Unknown", positions=[])
        assert status == 200
        # Confirm guard is tight: div element absent even if CSS class exists in stylesheet
        assert '<div class="ex-posmap-skills">' not in html

    def test_pv_08_full_44_skill_rows(self, client):
        """Portrait must render all 44 skill rows (skill_slice=None from CS-5 config)."""
        status, html = _render(client)
        assert status == 200
        count = html.count('<div class="ex-row">')
        assert count == 44, f"Expected 44 skill rows, got {count}"

    def test_pv_09_routes_to_level_c_not_driver(self, client):
        """Portrait bucket must route to portrait/fifa.html (Level C), not column_driver.html."""
        status, html = _render(client)
        assert status == 200
        # PORT-v2 markers absent from driver
        assert "ex-hero-photo" in html or "ex-hero-placeholder" in html, (
            "Neither ex-hero-photo nor ex-hero-placeholder found — "
            "probably rendering column_driver.html instead of portrait/fifa.html"
        )
        # Driver-specific marker must NOT be present in PORT-v2 output
        assert "column_driver.html" not in html


# ── PV-10..15: Content and structure tests ────────────────────────────────────

@pytest.mark.unit
class TestPortV2Structure:
    """Structural and content assertions for PORT-v2 rendered output."""

    def test_pv_10_three_column_skills_zone(self, client):
        """Skills zone must have 3-column structure: outfield (left-col) + mid (Mental) + right (Sets+Phys)."""
        status, html = _render(client)
        assert status == 200
        assert "ex-outfield-col"     in html, "ex-outfield-col missing (Outfield column)"
        assert "ex-skills-col-mid"   in html, "ex-skills-col-mid missing (Mental column)"
        assert "ex-skills-col-right" in html, "ex-skills-col-right missing (Sets+Physical column)"

    def test_pv_11_long_name_renders_ok(self, client):
        """32-character name must render without error (no template exception)."""
        status, html = _render(client, name="Kristóf Fekete-Molnár Tibor Jr.")
        assert status == 200, f"HTTP {status} for long name"
        assert "ex-card" in html

    def test_pv_12_no_nationality_renders_ok(self, client):
        """No nationality → HTTP 200, meta list still renders other items."""
        status, html = _render(client, nationality=None)
        assert status == 200, f"HTTP {status}"
        assert "ex-meta-list" in html, "Meta list missing when nationality=None"
        # At least some meta values must render even without nationality
        assert "ex-meta-val" in html, "All meta values missing — meta list broken"

    def test_pv_13_no_photo_shows_placeholder(self, client):
        """No portrait_url → ex-hero-placeholder must be rendered."""
        status, html = _render(client, portrait_url=None)
        assert status == 200
        assert "ex-hero-placeholder" in html, (
            "ex-hero-placeholder missing — no-photo fallback broken"
        )

    def test_pv_14_photo_url_renders_img(self, client):
        """portrait_url present → ex-hero-photo img must be rendered."""
        status, html = _render(client, portrait_url="https://example.com/photo.jpg")
        assert status == 200
        assert '<img class="ex-hero-photo"' in html, (
            "ex-hero-photo img missing when portrait_url is set"
        )

    def test_pv_15_export_mode_renders_ex_card(self, client):
        """export=1 must produce a page with ex-card root element."""
        status, html = _render(client)
        assert status == 200
        assert "ex-card" in html, "ex-card root element missing in export render"

    def test_pv_16_typography_ovr_badge_macro_imported(self):
        """portrait/fifa.html must import card_ovr_badge macro (Bebas Neue OVR badge)."""
        src = _PORTRAIT_TMPL.read_text(encoding="utf-8")
        assert 'from "macros/card_ovr_badge.html"' in src, (
            "card_ovr_badge macro import missing — typography Step D not applied"
        )
        assert "ovr_badge(" in src, (
            "ovr_badge() macro call missing — OVR badge not used in template"
        )

    def test_pv_17_ovr_badge_element_in_rendered_output(self, client):
        """Rendered HTML must contain ex-ovr-badge element (circular OVR badge)."""
        status, html = _render(client)
        assert status == 200
        assert 'class="ex-ovr-badge"' in html, (
            "ex-ovr-badge element missing — card_ovr_badge macro not rendering"
        )
