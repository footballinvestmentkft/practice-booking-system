"""
Unit tests — OG-v1: Open Graph Editorial standalone template
============================================================
Split horizontal layout — 1200×630 — OG Editorial v1.0 (2026-05-18)

Coverage:
  OG-01  og/fifa.html extends export_base.html directly (Level C standalone)
  OG-02  Template has row layout (--ex-card-direction: row)
  OG-03  Split architecture: ex-og-photo + ex-og-info both present in source
  OG-04  OVR badge macro imported + called in template
  OG-05  OG bucket routes to og/fifa.html, not landscape/fifa.html or column_driver.html
  OG-06  HTTP 200 on GET /players/{id}/card?platform=og&export=1
  OG-07  Rendered HTML contains ex-ovr-badge element (macro rendering)
  OG-08  Rendered HTML contains ex-og-cats (category averages zone)
  OG-09  Unknown position → no ex-og-posmap div rendered
  OG-10  Known position + show_position_map=True → ex-og-posmap rendered
  OG-11  No photo → ex-og-photo-placeholder rendered
  OG-12  Photo URL present → ex-og-photo-img rendered

Regression baseline locked: 2026-05-18, OG Editorial v1.0.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_TEMPLATES_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "app" / "templates"
)
_OG_TMPL = _TEMPLATES_ROOT / "public" / "export" / "og" / "fclassic.html"


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
    lic.card_variant = "fifa"
    lic.card_theme = "default"
    lic.public_card_platform = None
    lic.published_card_variant = "fifa"
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
            d.published_variant = "fifa"
            d.published_platform = None
            d.draft_theme = "default"
            d.draft_variant = "fifa"
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
        r = client.get(f"/players/{user_id}/card?platform=og&export=1")
        return r.status_code, r.text
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── OG-01..04: Static template assertions ─────────────────────────────────────

@pytest.mark.unit
class TestOGv1Static:
    """File-level invariants — no HTTP request."""

    def test_og_01_extends_export_base_directly(self):
        """og/fifa.html must extend export_base.html (Level C standalone)."""
        assert _OG_TMPL.exists(), "og/fclassic.html missing"
        src = _OG_TMPL.read_text(encoding="utf-8")
        assert 'extends "public/export/shared/export_base.html"' in src, (
            "OG-v1 must extend export_base.html directly"
        )
        assert 'column_archetype' not in src, (
            "OG-v1 must NOT extend column_archetype (column driver architecture)"
        )

    def test_og_02_row_layout_direction(self):
        """OG template must declare row layout via --ex-card-direction: row."""
        src = _OG_TMPL.read_text(encoding="utf-8")
        assert "--ex-card-direction: row" in src, (
            "--ex-card-direction: row missing — OG is a horizontal split layout"
        )

    def test_og_03_split_architecture_classes_present(self):
        """ex-og-photo and ex-og-info must both be defined in the template source."""
        src = _OG_TMPL.read_text(encoding="utf-8")
        assert "ex-og-photo" in src, (
            "ex-og-photo class missing — photo panel zone not defined"
        )
        assert "ex-og-info" in src, (
            "ex-og-info class missing — info panel zone not defined"
        )

    def test_og_04_ovr_badge_macro_imported_and_called(self):
        """og/fifa.html must import and call card_ovr_badge macro."""
        src = _OG_TMPL.read_text(encoding="utf-8")
        assert 'from "macros/card_ovr_badge.html"' in src, (
            "card_ovr_badge macro import missing"
        )
        assert "ovr_badge(" in src, (
            "ovr_badge() macro call missing — OVR badge not used in template"
        )


# ── OG-05..08: HTTP routing and rendering tests ───────────────────────────────

@pytest.mark.unit
class TestOGv1Rendering:
    """HTTP smoke tests — routing and content invariants."""

    def test_og_05_routes_to_og_template_not_landscape(self, client):
        """OG bucket must route to og/fifa.html (Level C), not landscape or driver."""
        status, html = _render(client)
        assert status == 200, f"HTTP {status}"
        assert "ex-og-photo" in html or "ex-og-info" in html, (
            "OG-specific classes missing — probably rendering landscape/fifa.html or driver"
        )
        assert "column_driver.html" not in html

    def test_og_06_http_200_on_og_export(self, client):
        """GET /card?platform=og&export=1 must return HTTP 200."""
        status, _ = _render(client)
        assert status == 200, f"Expected 200, got HTTP {status}"

    def test_og_07_ovr_badge_rendered_in_output(self, client):
        """Rendered HTML must contain ex-ovr-badge element."""
        status, html = _render(client)
        assert status == 200
        assert 'class="ex-ovr-badge"' in html, (
            "ex-ovr-badge missing — card_ovr_badge macro not rendering"
        )

    def test_og_08_category_averages_zone_rendered(self, client):
        """Rendered HTML must contain ex-og-cats (category averages section)."""
        status, html = _render(client)
        assert status == 200
        assert "ex-og-cats" in html, (
            "ex-og-cats missing — category averages zone not rendered"
        )


# ── OG-09..12: Position map + photo tests ────────────────────────────────────

@pytest.mark.unit
class TestOGv1Structure:
    """Structural and photo fallback assertions."""

    def test_og_09_unknown_position_no_posmap(self, client):
        """Unknown position → ex-og-posmap div must NOT be rendered."""
        status, html = _render(client, position="Unknown", positions=[])
        assert status == 200
        assert '<div class="ex-og-posmap">' not in html, (
            "ex-og-posmap rendered for Unknown position — guard broken"
        )

    def test_og_10_known_position_posmap_rendered(self, client):
        """CM position → ex-og-posmap rendered (show_position_map=True in og config)."""
        status, html = _render(client, position="CM", positions=["CM", "RB"])
        assert status == 200
        assert '<div class="ex-og-posmap">' in html, (
            "ex-og-posmap missing for known position"
        )

    def test_og_11_no_photo_shows_placeholder(self, client):
        """No portrait_url → ex-og-photo-placeholder must be rendered."""
        status, html = _render(client, portrait_url=None)
        assert status == 200
        assert "ex-og-photo-placeholder" in html, (
            "ex-og-photo-placeholder missing — no-photo fallback broken"
        )

    def test_og_12_photo_url_renders_img(self, client):
        """portrait_url present → ex-og-photo-img must be rendered."""
        status, html = _render(client, portrait_url="https://example.com/photo.jpg")
        assert status == 200
        assert '<img class="ex-og-photo-img"' in html, (
            "ex-og-photo-img missing when portrait_url is set"
        )
