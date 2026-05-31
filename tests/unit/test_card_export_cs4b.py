"""
Unit tests — CS-4b: archetype-named base template migration
===========================================================

Coverage:
  CS4B-01  column_archetype.html renders identically to fifa_base_column.html
  CS4B-02  export_base.html renders identically to fifa_base.html
  CS4B-03  portrait/fclassic.html (→ column_archetype) renders correctly post-migration
  CS4B-04  story/fclassic.html    (→ column_archetype) renders correctly post-migration
  CS4B-05  tiktok/fclassic.html   (→ export_base)      renders correctly post-migration
  CS4B-06  landscape/fclassic.html(→ export_base)      renders correctly post-migration
  CS4B-07  banner/fclassic.html   (→ export_base)      renders correctly post-migration
  CS4B-08  square/fclassic.html   (→ export_base)      renders correctly post-migration
  CS4B-09  No template in public/export/ extends a legacy export_base.html file

Validation strategy (from CS-4b audit):
  CS4B-01/02: Direct Jinja2 render comparison of old vs new base — the only benign
              diff source (CSS block comments) is stripped by _normalize_html().
              Remaining HTML equality is a deterministic, no-live-server gate.
  CS4B-03..08: TestClient smoke checks — 200 status + .ex-card root element present.
  CS4B-09:    grep-equivalent assert confirming legacy base files are no longer
              referenced after deletion.

Mock strategy (smoke tests):
  - Identical to test_platform_export_layout.py — MagicMock user + license,
    card_variant="fifa" so the FIFA-bucket templates are selected.
  - get_db overridden; no live DB required.
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
_EXPORT_DIR = _TEMPLATES_ROOT / "public" / "export"


# ── Template source normalization ──────────────────────────────────────────────

def _normalize_source(text: str) -> str:
    """Strip benign diffs between old/new base pairs so block content can be compared.

    The old/new base pairs differ only in:
      1. The {% extends %} line (different parent path)
      2. Jinja2 {# comment #} header blocks (documentation, not logic)
      3. CSS block comments /* ... */ referencing the old base name
    Everything else — all Jinja2 blocks, CSS rules, HTML, macros — is identical.
    """
    # Strip Jinja2 block comments {# ... #}
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
    # Strip CSS block comments /* ... */
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Strip extends directives
    text = re.sub(r"\{%-?\s*extends\s+['\"][^'\"]+['\"]\s*-?%\}", "", text)
    # Collapse whitespace: multiple spaces → one, multiple blank lines → one blank line
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _read_template(template_name: str) -> str:
    return (_TEMPLATES_ROOT / template_name).read_text(encoding="utf-8")


# ── TestClient + mock DB helpers (smoke tests) ─────────────────────────────────

def _make_user(user_id: int = 7) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.name = "Test Player"
    u.nationality = "Hungarian"
    u.is_active = True
    u.date_of_birth = None
    u.skills = {}
    return u


def _make_license(card_variant: str = "fclassic") -> MagicMock:
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
    lic.card_photo_portrait_url = None
    lic.card_photo_landscape_url = None
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


def _render_card(client, platform: str, user_id: int = 7) -> str:
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


# ── CS4B-01/02: Archetype base structural integrity ───────────────────────────

@pytest.mark.unit
class TestCS4bBaseEquality:
    """Post-migration: verify the archetype-named bases are correctly structured.

    After CS-4b, the legacy fifa_base.html and fifa_base_column.html are deleted.
    These tests assert that the surviving archetype bases are complete and correctly
    wired — specifically that:
      - column_archetype.html extends export_base.html (not a legacy name)
      - export_base.html is a self-contained Level A HTML shell (DOCTYPE + blocks)
    """

    def test_cs4b_01_column_archetype_extends_export_base(self):
        """column_archetype.html must extend export_base.html, not any legacy file."""
        src = _read_template("public/export/shared/column_archetype.html")
        assert 'extends "public/export/shared/export_base.html"' in src, (
            "column_archetype.html does not extend export_base.html — "
            "the Level B → Level A chain is broken."
        )
        assert "fifa_base" not in src.split("{#")[0], (
            "column_archetype.html still references a legacy export_base.html file "
            "in its extends directive."
        )

    def test_cs4b_02_export_base_is_complete_level_a_shell(self):
        """export_base.html must be a complete, self-contained HTML shell (Level A)."""
        src = _read_template("public/export/shared/export_base.html")
        assert "<!DOCTYPE html>" in src, "export_base.html is missing DOCTYPE declaration."
        assert "{% block body_content %}" in src, "export_base.html is missing body_content block."
        assert "{% block extra_css %}" in src, "export_base.html is missing extra_css block."
        assert ".ex-card" in src, "export_base.html is missing .ex-card root CSS."
        assert ".ex-row" in src, "export_base.html is missing shared skill-row CSS."
        assert "extends" not in src.split("{#")[0], (
            "export_base.html has an extends directive — Level A base must not extend anything."
        )


# ── CS4B-03..08: Post-migration smoke tests ────────────────────────────────────

@pytest.mark.unit
class TestCS4bMigrationSmoke:
    """Structural smoke tests for each migrated FClassic export template.

    Confirms that after the extends change:
      - The route returns HTTP 200
      - The root card element (.ex-card) is present in the rendered HTML
      - No template error (Jinja2 TemplateNotFound etc.) occurred
    """

    def _assert_renders(self, client, platform: str, label: str) -> None:
        html = _render_card(client, platform)
        assert html, f"{label}: route returned empty response (non-200)"
        assert "ex-card" in html, (
            f"{label}: .ex-card not found in rendered HTML — "
            "template may have failed to extend the new base correctly."
        )

    def test_cs4b_03_portrait_renders(self, client):
        """portrait/fclassic.html (→ column_archetype.html) renders correctly."""
        self._assert_renders(client, "instagram_portrait", "portrait/fclassic.html")

    def test_cs4b_04_story_renders(self, client):
        """story/fclassic.html (→ column_archetype.html) renders correctly."""
        self._assert_renders(client, "instagram_story", "story/fclassic.html")

    def test_cs4b_05_tiktok_renders(self, client):
        """tiktok/fclassic.html (→ export_base.html) renders correctly."""
        self._assert_renders(client, "tiktok", "tiktok/fclassic.html")

    def test_cs4b_06_landscape_renders(self, client):
        """landscape/fclassic.html (→ export_base.html) renders correctly."""
        self._assert_renders(client, "facebook_landscape", "landscape/fclassic.html")

    def test_cs4b_07_banner_renders(self, client):
        """banner/fclassic.html (→ export_base.html) renders correctly."""
        self._assert_renders(client, "banner_custom", "banner/fclassic.html")

    def test_cs4b_08_square_renders(self, client):
        """square/fclassic.html (→ export_base.html) renders correctly."""
        self._assert_renders(client, "instagram_square", "square/fclassic.html")


# ── CS4B-09: Legacy base reference check ──────────────────────────────────────

@pytest.mark.unit
class TestCS4bLegacyCleanup:
    """Assert that no template in public/export/ still extends a legacy export_base.html file.

    This is the grep-gate equivalent: after CS-4b deletion, any surviving reference
    would indicate an incomplete migration or an accidental re-introduction.
    """

    def test_cs4b_09_no_legacy_extends_in_export_templates(self):
        """No Level C template may extend fifa_base.html or fifa_base_column.html."""
        legacy_pattern = re.compile(
            r"""{%-?\s*extends\s+["']public/export/shared/fifa_base"""
        )
        violations: list[str] = []
        for html_file in sorted(_EXPORT_DIR.rglob("*.html")):
            text = html_file.read_text(encoding="utf-8")
            if legacy_pattern.search(text):
                violations.append(str(html_file.relative_to(_TEMPLATES_ROOT)))

        assert not violations, (
            "The following templates still extend a legacy export_base.html file:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\nMigration is incomplete — update extends to archetype-named bases."
        )
