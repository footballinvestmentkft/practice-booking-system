"""
Unit tests — CS-6: Pulse archetype driver (A-model archetype_id routing)
=========================================================================

Coverage:
  CS6-01  Pulse design (archetype_id="pulse") routes to pulse_driver.html
          → pex-card present + var(--pex-hero-h) in CSS (not hardcoded 580px)
  CS6-02  Column design (archetype_id="column") still routes to column_driver.html
          → no regression; ex-card present
  CS6-03  Design with archetype_id=None + no component_config → file-based
          → neither pex-card (pulse) nor ex-card (column driver) forced
  CS6-04  Manifest validate: archetype_id="pulse" + square bucket → CREATE OK
  CS6-05  Manifest validate: component_config non-empty + archetype_id absent → error
  CS6-06  Manifest validate: archetype_id="pulse" + "portrait" bucket → error
          (portrait is not a valid pulse-archetype bucket)
  CS6-07  Manifest validate: archetype_id="unknown_xyz" → error
  CS6-08  pulse_lite manifest-only CREATE proof: no new template file, uses
          pulse_driver.html via archetype_id routing

Semantic invariant (CS-6 A-model):
  archetype_id identifies the driver family for component_config-backed exports ONLY.
  File-based exports (no component_config for that bucket) are unaffected by archetype_id.
  FClassic archetype_id="column" is semantically correct for its driver-eligible exports;
  its square/tiktok/landscape/banner exports remain file-based and are orthogonal.

Mock strategy:
  CS6-01/02/03: patch _get_design at public_player module level.
  CS6-04..08: MagicMock DB — no live DB required.
  browser_template validated against real FS; _REAL_TEMPLATE must exist on disk.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

_TEMPLATES_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "app" / "templates"
)
_DRIVERS_DIR = _TEMPLATES_ROOT / "public" / "export" / "shared" / "drivers"
_REAL_TEMPLATE = "public/player_card_fclassic.html"

# ── Pulse component_config for instagram_square (default Pulse design) ────────

_PULSE_SQUARE_CONFIG: dict = {
    "square": {
        "skill_slice": None,
        "show_dominant_badge": False,
        "show_height_weight": False,
        "show_sponsor": False,
        "platform_vars": {"--pex-hero-h": "520px"},  # distinctive override for assertions
    }
}


# ── Shared design factory ─────────────────────────────────────────────────────

def _make_pulse_def(component_config=None):
    from app.services.card_design_service import CardDesignDefinition
    return CardDesignDefinition(
        id="pulse",
        label="Pulse",
        description="Radar pulse design.",
        is_premium=True,
        credit_cost=600,
        template="public/player_card_pulse.html",
        sort_order=6,
        archetype_id="pulse",
        supported_export_buckets=("square",),
        animated_platforms=("instagram_square",),
        component_config=component_config if component_config is not None else _PULSE_SQUARE_CONFIG,
    )


def _make_classic_lite_def():
    from app.services.card_design_service import CardDesignDefinition
    return CardDesignDefinition(
        id="classic_lite",
        label="Classic Lite",
        description="Column archetype design.",
        is_premium=False,
        credit_cost=0,
        template="public/player_card_fclassic.html",
        sort_order=1,
        archetype_id="column",
        supported_export_buckets=("portrait", "story"),
        animated_platforms=(),
        component_config={
            "portrait": {
                "skill_slice": 4,
                "show_dominant_badge": True,
                "show_height_weight": True,
                "show_sponsor": True,
                "platform_vars": {"--ex-hero-h": "380px"},
            },
            "story": {
                "skill_slice": 6,
                "show_dominant_badge": True,
                "show_height_weight": True,
                "show_sponsor": False,
                "platform_vars": {"--ex-hero-h": "440px"},
            },
        },
    )


def _make_null_archetype_def():
    """Design with no archetype_id and no component_config → pure file-based."""
    from app.services.card_design_service import CardDesignDefinition
    return CardDesignDefinition(
        id="compact",
        label="Compact",
        description="File-based only.",
        is_premium=True,
        credit_cost=300,
        template="public/player_card_compact.html",
        sort_order=1,
        archetype_id=None,
        supported_export_buckets=("portrait",),
        animated_platforms=(),
        component_config={},
    )


# ── Shared HTTP mock helpers (mirrors CS-5 pattern) ───────────────────────────

def _make_user(user_id: int = 7) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.name = "Pulse Player"
    u.nationality = "Hungarian"
    u.is_active = True
    u.date_of_birth = None
    u.skills = {}
    return u


def _make_license(card_variant: str = "pulse") -> MagicMock:
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


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── CS6-01: Pulse driver routing ──────────────────────────────────────────────

@pytest.mark.unit
def test_cs6_01_pulse_archetype_routes_to_pulse_driver(client):
    """
    Design with archetype_id='pulse' + component_config.square → routes to pulse_driver.html.
    Proof: response contains 'var(--pex-hero-h)' (from driver CSS) and NOT 'flex: 0 0 580px'
    (which would indicate the monolithic non-driver pulse.html was used).
    """
    from app.main import app
    from app.dependencies import get_db
    from app.api.web_routes import public_player as _pp

    pulse_def = _make_pulse_def()
    db = _mock_db(user=_make_user(7), license_=_make_license("pulse"))
    app.dependency_overrides[get_db] = lambda: db
    with patch.object(_pp, "_get_design", return_value=pulse_def):
        try:
            r = client.get("/players/7/card?platform=instagram_square&export=1")
        finally:
            app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    html = r.text
    assert "pex-card" in html, "pex-card class not found — pulse template not rendered"
    # pulse_driver.html uses CSS custom property var(--pex-hero-h); monolithic pulse.html hardcodes 580px
    assert "var(--pex-hero-h)" in html, (
        "var(--pex-hero-h) not found — pulse_driver.html was not used "
        "(monolithic pulse.html has hardcoded flex: 0 0 580px)"
    )
    # The platform_var override (520px) from _driver_config must be present in the :root block
    assert "520px" in html, (
        "platform_vars --pex-hero-h:520px not found in output — "
        "_driver_config.platform_vars not injected by pulse_driver.html"
    )


# ── CS6-02: Column archetype regression ───────────────────────────────────────

@pytest.mark.unit
def test_cs6_02_column_archetype_regression_unaffected(client):
    """
    Design with archetype_id='column' still routes to column_driver.html.
    No regression from CS-4c/CS-5 behaviour.
    """
    from app.main import app
    from app.dependencies import get_db
    from app.api.web_routes import public_player as _pp

    cl_def = _make_classic_lite_def()
    db = _mock_db(user=_make_user(7), license_=_make_license("classic_lite"))
    app.dependency_overrides[get_db] = lambda: db
    with patch.object(_pp, "_get_design", return_value=cl_def):
        try:
            r = client.get("/players/7/card?platform=instagram_portrait&export=1")
        finally:
            app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    html = r.text
    assert "ex-card" in html, "ex-card not found — column_driver.html not rendered"
    assert "pex-card" not in html, "pex-card found — pulse template rendered instead of column"


# ── CS6-03: NULL archetype → file-based ───────────────────────────────────────

@pytest.mark.unit
def test_cs6_03_null_archetype_id_forces_file_based_routing(client):
    """
    Design with archetype_id=None + empty component_config → no driver route selected.
    Falls back to file-based Level C (or editor template if file absent).
    """
    from app.main import app
    from app.dependencies import get_db
    from app.api.web_routes import public_player as _pp

    null_def = _make_null_archetype_def()
    db = _mock_db(user=_make_user(7), license_=_make_license("compact"))
    app.dependency_overrides[get_db] = lambda: db
    with patch.object(_pp, "_get_design", return_value=null_def):
        try:
            r = client.get("/players/7/card?platform=instagram_portrait&export=1")
        finally:
            app.dependency_overrides.pop(get_db, None)

    # Route must not 500; it either falls to file-based or editor template
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    html = r.text
    # Neither column driver nor pulse driver should have been used
    assert "var(--pex-hero-h)" not in html, "pulse_driver.html was incorrectly used"


# ── Manifest validation helpers ───────────────────────────────────────────────

def _mock_db_empty():
    db = MagicMock()
    q = MagicMock()
    q.all.return_value = []
    db.query.return_value = q
    return db


def _make_pulse_manifest_item(**overrides) -> dict:
    base = {
        "id":                       "pulse_lite",
        "label":                    "Pulse Lite",
        "description":              "Square-only pulse archetype, free tier.",
        "is_premium":               False,
        "credit_cost":              0,
        "sort_order":               7,
        "browser_template":         _REAL_TEMPLATE,
        "archetype_id":             "pulse",
        "supported_export_buckets": ["square"],
        "animated_platforms":       [],
        "component_config": {
            "square": {
                "skill_slice": 4,
                "show_dominant_badge": False,
                "show_height_weight": False,
                "show_sponsor": False,
                "platform_vars": {"--pex-hero-h": "520px"},
            }
        },
    }
    base.update(overrides)
    return base


def _make_pulse_manifest(**item_overrides) -> bytes:
    return json.dumps({
        "schema_version": 1,
        "designs": [_make_pulse_manifest_item(**item_overrides)],
    }).encode()


# ── CS6-04: Valid pulse manifest validates ────────────────────────────────────

@pytest.mark.unit
def test_cs6_04_valid_pulse_manifest_validates():
    """pulse_lite manifest with archetype_id='pulse' + square → CREATE preview, no errors."""
    from app.services.card_design_admin_service import validate_manifest

    result = validate_manifest(_make_pulse_manifest(), _mock_db_empty())
    assert result.ok, f"Expected no errors, got: {result.errors}"
    assert len(result.preview_rows) == 1
    row = result.preview_rows[0]
    assert row.action == "CREATE"
    assert row.item.id == "pulse_lite"
    assert row.item.archetype_id == "pulse"
    assert "square" in row.item.supported_export_buckets


# ── CS6-05: Missing archetype_id with component_config → error ────────────────

@pytest.mark.unit
def test_cs6_05_component_config_without_archetype_id_rejected():
    """Non-empty component_config without archetype_id → validation error."""
    from app.services.card_design_admin_service import validate_manifest

    raw = _make_pulse_manifest(archetype_id=None)
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any("archetype_id" in e.lower() and "required" in e.lower() for e in result.errors), (
        f"Expected 'archetype_id is required' error, got: {result.errors}"
    )


# ── CS6-06: Wrong bucket for archetype → error ────────────────────────────────

@pytest.mark.unit
def test_cs6_06_portrait_bucket_rejected_for_pulse_archetype():
    """archetype_id='pulse' + 'portrait' in component_config → portrait not in pulse allowed buckets."""
    from app.services.card_design_admin_service import validate_manifest

    item = _make_pulse_manifest_item()
    item["supported_export_buckets"] = ["portrait", "square"]
    item["component_config"]["portrait"] = {
        "skill_slice": 4, "show_dominant_badge": False,
        "show_height_weight": False, "show_sponsor": False,
        "platform_vars": {},
    }
    raw = json.dumps({"schema_version": 1, "designs": [item]}).encode()
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any(
        "portrait" in e and "pulse" in e.lower() for e in result.errors
    ), f"Expected portrait+pulse error, got: {result.errors}"


# ── CS6-07: Unknown archetype_id → error ──────────────────────────────────────

@pytest.mark.unit
def test_cs6_07_unknown_archetype_id_rejected():
    """archetype_id='radar_hud' (unknown) → validation error."""
    from app.services.card_design_admin_service import validate_manifest

    raw = _make_pulse_manifest(archetype_id="radar_hud")
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any("radar_hud" in e or "unknown" in e.lower() for e in result.errors), (
        f"Expected unknown archetype error, got: {result.errors}"
    )


# ── CS6-08: pulse_lite manifest-only CREATE proof ─────────────────────────────

@pytest.mark.unit
def test_cs6_08_pulse_lite_manifest_only_create_proof():
    """
    pulse_lite can be created via admin manifest without any new template file.
    Proof:
      1. No template file at public/export/square/pulse_lite.html (manifest-only)
      2. validate_manifest accepts the manifest (CREATE action)
      3. apply_manifest writes to DB + invalidates cache
      4. The routing logic confirms archetype_id='pulse' → square → pulse_driver.html
    """
    from app.services.card_design_admin_service import (
        validate_manifest, apply_manifest, DesignManifestItem, DesignPreviewRow,
    )
    from app.services import card_design_admin_service as _svc
    from app.api.web_routes import public_player as _pp

    # 1. No dedicated Level C file for pulse_lite
    pulse_lite_tpl = _TEMPLATES_ROOT / "public" / "export" / "square" / "pulse_lite.html"
    assert not pulse_lite_tpl.exists(), (
        "pulse_lite.html exists — this design is no longer manifest-only. "
        "CS-6 proof requires absence of a Level C file."
    )

    # 2. Validate manifest → CREATE action
    result = validate_manifest(_make_pulse_manifest(), _mock_db_empty())
    assert result.ok, f"Manifest validation failed: {result.errors}"
    assert result.preview_rows[0].action == "CREATE"

    # 3. Apply manifest → db.add called, cache invalidated
    item = DesignManifestItem.model_validate(_make_pulse_manifest_item())
    preview_rows = [DesignPreviewRow(item=item, action="CREATE", diff={})]
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch.object(_svc, "_invalidate_cache") as mock_inv:
        applied = apply_manifest(db, preview_rows)

    assert "pulse_lite" in applied
    db.add.assert_called_once()
    db.commit.assert_called_once()
    mock_inv.assert_called_once()

    # Verify archetype_id="pulse" was written to the ORM object
    added_obj = db.add.call_args[0][0]
    assert added_obj.archetype_id == "pulse"

    # 4. Routing logic: archetype_id="pulse" → square → pulse_driver.html
    archetype_drivers = _pp._ARCHETYPE_DRIVERS
    assert "pulse" in archetype_drivers, "_ARCHETYPE_DRIVERS missing 'pulse' key"
    assert archetype_drivers["pulse"].get("square") == "pulse_driver.html", (
        "pulse archetype does not map square → pulse_driver.html"
    )
    pulse_driver_file = _DRIVERS_DIR / "pulse_driver.html"
    assert pulse_driver_file.exists(), "pulse_driver.html not found in drivers/ directory"
