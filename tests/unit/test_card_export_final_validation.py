"""
Final merge-readiness validation — all 7 FClassic Player export formats
=====================================================================
FV-01..35  (2026-05-18)

Validates per format:
  1. HTTP 200 render without server error
  2. OVR badge macro rendered (ex-ovr-badge element)
  3. Position map guard (Unknown → absent, known CM → present where applicable)
  4. Skill row count or category averages rendered
  5. Category color tokens present in output
  6. Self-hosted font references in base template (not CDN)
  7. OG bucket routes to og/fclassic.html (not landscape/driver)

Formats under test:
  portrait  instagram_portrait  PORT-v2  1080×1350
  story     instagram_story     STORY-v2 1080×1920
  square    instagram_square    SQ-v1    1080×1080
  landscape facebook_landscape  LS-v1    1200×630
  tiktok    tiktok              TK-v1    1080×1920
  banner    banner_custom       BN-v1    1500×500
  og        og                  OG-v1    1200×630
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_TEMPLATES_ROOT = (
    Path(__file__).resolve().parent.parent.parent / "app" / "templates"
)

# ── Platform → (platform_id, has_skill_bars, expected_rows, is_story_layout) ──
_PLATFORMS = {
    "portrait":  ("instagram_portrait",  True,  44, False),
    "story":     ("instagram_story",     True,  44, False),
    "square":    ("instagram_square",    True,  44, False),
    "landscape": ("facebook_landscape",  True,  None, False),  # sliced, varies
    "tiktok":    ("tiktok",              True,  None, False),
    "banner":    ("banner_custom",       True,  None, False),
    "og":        ("og",                  False, None, False),  # category averages, not rows
}

# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_user(name: str = "Rafael Cardoso", nationality: str = "Brazilian") -> MagicMock:
    u = MagicMock()
    u.id = 42
    u.name = name
    u.nationality = nationality
    u.is_active = True
    u.date_of_birth = None
    u.skills = {}
    u.email = "fv@test.com"
    u.nickname = None
    u.age = 22
    u.gender = "male"
    u.current_location = None
    u.country = None
    u.created_at = None
    u.xp_balance = 120
    return u


def _make_license(position: str = "ST", portrait_url: str | None = "https://example.com/photo.jpg") -> MagicMock:
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
    lic.card_photo_landscape_url = portrait_url
    lic.motivation_scores = {
        "position": position,
        "positions": [position] if position != "Unknown" else [],
        "height_cm": 181,
        "weight_kg": 75,
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
    lic.right_foot_score = 82.0
    lic.left_foot_score = 41.0
    lic.sponsor_logo_url = None
    lic.current_level = 3
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


def _render(client, platform_id: str, position: str = "ST",
            portrait_url: str | None = "https://example.com/photo.jpg") -> tuple[int, str]:
    from app.main import app
    from app.dependencies import get_db

    user = _make_user()
    lic  = _make_license(position, portrait_url)
    db   = _make_db(user, lic)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = client.get(f"/players/42/card?platform={platform_id}&export=1")
        return r.status_code, r.text
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── FV-01..07: HTTP 200 — all 7 platforms render without error ────────────────

@pytest.mark.unit
class TestFVHttp200:
    """All 7 platforms must return HTTP 200 with no server error."""

    @pytest.mark.parametrize("bucket,platform_id", [
        ("portrait",  "instagram_portrait"),
        ("story",     "instagram_story"),
        ("square",    "instagram_square"),
        ("landscape", "facebook_landscape"),
        ("tiktok",    "tiktok"),
        ("banner",    "banner_custom"),
        ("og",        "og"),
    ])
    def test_fv_http200(self, client, bucket, platform_id):
        status, html = _render(client, platform_id)
        assert status == 200, f"[{bucket}] HTTP {status} — server error"
        assert "ex-card" in html, f"[{bucket}] ex-card root element missing"
        assert "Traceback" not in html, f"[{bucket}] Python traceback in output"


# ── FV-08..14: OVR badge rendered in all 7 platforms ─────────────────────────

@pytest.mark.unit
class TestFVOVRBadge:
    """ex-ovr-badge element from card_ovr_badge macro must appear in all 7."""

    @pytest.mark.parametrize("bucket,platform_id", [
        ("portrait",  "instagram_portrait"),
        ("story",     "instagram_story"),
        ("square",    "instagram_square"),
        ("landscape", "facebook_landscape"),
        ("tiktok",    "tiktok"),
        ("banner",    "banner_custom"),
        ("og",        "og"),
    ])
    def test_fv_ovr_badge(self, client, bucket, platform_id):
        status, html = _render(client, platform_id)
        assert status == 200
        assert 'class="ex-ovr-badge"' in html, (
            f"[{bucket}] ex-ovr-badge missing — card_ovr_badge macro not rendering"
        )


# ── FV-15..21: Position map guard — Unknown position → absent ─────────────────

@pytest.mark.unit
class TestFVPosMapGuard:
    """Unknown position → position map element must be absent in all 7 platforms."""

    @pytest.mark.parametrize("bucket,platform_id,posmap_selector", [
        ("portrait",  "instagram_portrait", '<div class="ex-posmap-inline">'),
        ("story",     "instagram_story",    '<div class="ex-posmap-footer">'),
        ("square",    "instagram_square",   "ex-posmap"),
        ("landscape", "facebook_landscape", '<svg class="ex-pos-svg-landscape"'),
        ("tiktok",    "tiktok",             '<svg class="ex-pos-svg-landscape"'),
        ("banner",    "banner_custom",      '<svg class="ex-pos-svg-landscape"'),
        ("og",        "og",                 '<div class="ex-og-posmap">'),
    ])
    def test_fv_posmap_unknown_position_absent(self, client, bucket, platform_id, posmap_selector):
        status, html = _render(client, platform_id, position="Unknown")
        assert status == 200
        assert posmap_selector not in html, (
            f"[{bucket}] Position map rendered for Unknown position — guard broken"
        )


# ── FV-22..28: Skill content rendered per platform ───────────────────────────

@pytest.mark.unit
class TestFVSkillContent:
    """Skill content must be rendered in all platforms."""

    def test_fv_portrait_44_rows(self, client):
        """Portrait: exactly 44 skill rows (skill_slice=None)."""
        status, html = _render(client, "instagram_portrait")
        assert status == 200
        count = html.count('<div class="ex-row">')
        assert count == 44, f"Portrait: expected 44 skill rows, got {count}"

    def test_fv_story_44_rows(self, client):
        """Story: exactly 44 skill rows (skill_slice=None)."""
        status, html = _render(client, "instagram_story")
        assert status == 200
        count = html.count('<div class="ex-row">')
        assert count == 44, f"Story: expected 44 skill rows, got {count}"

    def test_fv_square_skill_rows_present(self, client):
        """Square: at least 30 skill rows rendered."""
        status, html = _render(client, "instagram_square")
        assert status == 200
        count = html.count('<div class="ex-row">')
        assert count >= 30, f"Square: expected ≥30 skill rows, got {count}"

    def test_fv_landscape_skill_rows_present(self, client):
        """Landscape: at least 8 skill rows rendered."""
        status, html = _render(client, "facebook_landscape")
        assert status == 200
        count = html.count('<div class="ex-row">')
        assert count >= 8, f"Landscape: expected ≥8 skill rows, got {count}"

    def test_fv_tiktok_skill_cats_rendered(self, client):
        """TikTok: at least 2 skill categories present."""
        status, html = _render(client, "tiktok")
        assert status == 200
        assert "ex-skill-cats" in html, "TikTok: ex-skill-cats missing"
        count = html.count('<div class="ex-cat"')
        assert count >= 2, f"TikTok: expected ≥2 ex-cat blocks, got {count}"

    def test_fv_banner_skill_rows_present(self, client):
        """Banner: at least 8 skill rows rendered."""
        status, html = _render(client, "banner_custom")
        assert status == 200
        count = html.count('<div class="ex-row">')
        assert count >= 8, f"Banner: expected ≥8 skill rows, got {count}"

    def test_fv_og_category_averages_rendered(self, client):
        """OG: category averages section (ex-og-cats) rendered instead of skill rows."""
        status, html = _render(client, "og")
        assert status == 200
        assert "ex-og-cats" in html, "OG: ex-og-cats (category averages) missing"
        assert '<div class="ex-row">' not in html, (
            "OG: individual skill rows should NOT render (editorial: category averages only)"
        )


# ── FV-29..31: Category color tokens ─────────────────────────────────────────

@pytest.mark.unit
class TestFVCategoryColorTokens:
    """Category color CSS variables must appear in rendered output (from export_base)."""

    def test_fv_cat_color_vars_in_base_template(self):
        """export_base.html must define all 4 category color tokens."""
        src = (_TEMPLATES_ROOT / "public/export/shared/export_base.html").read_text()
        for i, color in enumerate(["#3b82f6", "#eab308", "#22c55e", "#ef4444"]):
            assert f"--ex-cat-color-{i}" in src, f"--ex-cat-color-{i} missing from export_base"
            assert color in src, f"Category color {color} (index {i}) not in export_base"

    def test_fv_portrait_uses_cat_color_in_bars(self, client):
        """Portrait rendered HTML must contain --ex-cat-color- references for bar fills."""
        status, html = _render(client, "instagram_portrait")
        assert status == 200
        assert "ex-cat-color-0" in html, "Portrait: --ex-cat-color-0 (Outfield) not in rendered output"
        assert "ex-cat-color-2" in html, "Portrait: --ex-cat-color-2 (Mental) not in rendered output"

    def test_fv_og_uses_cat_colors_in_avg_bars(self, client):
        """OG rendered HTML must contain category colors in category average bar fills."""
        status, html = _render(client, "og")
        assert status == 200
        assert "#3b82f6" in html or "ex-cat-color" in html, (
            "OG: category color (Outfield blue) missing from category average bars"
        )


# ── FV-32..33: OG routing validation ─────────────────────────────────────────

@pytest.mark.unit
class TestFVOGRouting:
    """OG bucket must use og/fclassic.html, not landscape/fclassic.html or column_driver."""

    def test_fv_og_level_c_template_exists(self):
        """og/fclassic.html must exist as a Level C standalone file."""
        tmpl = _TEMPLATES_ROOT / "public" / "export" / "og" / "fclassic.html"
        assert tmpl.exists(), "og/fclassic.html missing — Level C template not created"
        src = tmpl.read_text()
        assert 'extends "public/export/shared/export_base.html"' in src
        assert "ex-og-photo" in src
        assert "ex-og-info" in src
        assert "ex-og-cats" in src

    def test_fv_og_renders_og_specific_elements(self, client):
        """OG HTTP render must contain og-specific classes (not landscape classes)."""
        status, html = _render(client, "og")
        assert status == 200
        assert "ex-og-photo" in html, "OG: ex-og-photo missing — routed to wrong template"
        assert "ex-og-info"  in html, "OG: ex-og-info missing — routed to wrong template"
        assert "ex-cols-row" not in html, (
            "OG: ex-cols-row present — rendering landscape/fclassic.html instead of og/fclassic.html"
        )


# ── FV-34..35: Self-hosted fonts ─────────────────────────────────────────────

@pytest.mark.unit
class TestFVFonts:
    """Self-hosted woff2 font assets and @font-face declarations must be in place."""

    def test_fv_font_files_all_present(self):
        """All 9 woff2 font files must exist in app/static/fonts/."""
        fonts_dir = (
            Path(__file__).resolve().parent.parent.parent / "app" / "static" / "fonts"
        )
        required = [
            "BebasNeue-Regular.woff2",
            "BarlowCondensed-300.woff2", "BarlowCondensed-400.woff2",
            "BarlowCondensed-600.woff2", "BarlowCondensed-700.woff2",
            "BarlowCondensed-800.woff2",
            "Rajdhani-500.woff2", "Rajdhani-600.woff2", "Rajdhani-700.woff2",
        ]
        for fname in required:
            path = fonts_dir / fname
            assert path.exists(), f"Font missing: {fname}"
            assert path.stat().st_size > 1024, f"Font suspiciously small: {fname}"

    def test_fv_export_base_font_faces_no_cdn(self):
        """export_base.html must use /static/fonts/ (not fonts.googleapis.com)."""
        src = (_TEMPLATES_ROOT / "public/export/shared/export_base.html").read_text()
        assert "BebasNeue-Regular.woff2" in src, "@font-face for Bebas Neue missing"
        assert "BarlowCondensed-700.woff2" in src, "@font-face for Barlow Condensed 700 missing"
        assert "Rajdhani-700.woff2" in src, "@font-face for Rajdhani 700 missing"
        assert "fonts.googleapis.com" not in src, (
            "CDN font reference found — must use self-hosted /static/fonts/ only"
        )
        assert "font-display: block" in src, (
            "font-display: block missing — FOUT prevention for Playwright headless export"
        )
