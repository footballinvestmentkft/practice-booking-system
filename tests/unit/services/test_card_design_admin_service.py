"""
Unit tests — CS-2: Card Design Admin Service
=============================================

Coverage:
  AD-01  validate_manifest: valid CREATE manifest → preview row
  AD-02  validate_manifest: invalid JSON → error
  AD-03  validate_manifest: schema_version ≠ 1 → error
  AD-04  validate_manifest: protected ID 'fclassic' → error
  AD-05  validate_manifest: invalid slug → error
  AD-06  validate_manifest: is_premium=True, credit_cost=0 → error
  AD-07  validate_manifest: component_config key not in column-driver buckets → error
  AD-08  validate_manifest: component_config bucket missing from supported_export_buckets → error
  AD-09  validate_manifest: platform_vars key without '--' prefix → error
  AD-10  validate_manifest: browser_template does not exist → error
  AD-11  validate_manifest: existing active design → UPDATE action + diff computed
  AD-12  validate_manifest: inactive existing design → REACTIVATE_UPDATE action
  AD-13  apply_manifest: CREATE → CardDesign added + cache invalidated
  AD-14  get_design_usage_summary: returns correct counters

Mock strategy:
  All tests use MagicMock DB — no live DB required.
  browser_template is patched via monkeypatch to bypass filesystem check.
  apply_manifest CREATE path patches CardDesign ORM and _invalidate_cache.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

_TEMPLATES_ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "app" / "templates"
)

# A browser_template that actually exists on disk
_REAL_TEMPLATE = "public/player_card_fclassic.html"


def _make_valid_item(**overrides) -> dict:
    base = {
        "id":                       "test_design",
        "label":                    "Test Design",
        "description":              "A test design.",
        "is_premium":               False,
        "credit_cost":              0,
        "sort_order":               5,
        "browser_template":         _REAL_TEMPLATE,
        "archetype_id":             "column",
        "supported_export_buckets": ["portrait", "story"],
        "animated_platforms":       [],
        "component_config": {
            "portrait": {
                "skill_slice":         4,
                "show_dominant_badge": True,
                "show_height_weight":  True,
                "show_sponsor":        True,
                "platform_vars":       {"--ex-hero-h": "380px"},
            },
            "story": {
                "skill_slice":         6,
                "show_dominant_badge": True,
                "show_height_weight":  True,
                "show_sponsor":        False,
                "platform_vars":       {"--ex-hero-h": "440px"},
            },
        },
    }
    base.update(overrides)
    return base


def _make_manifest(**item_overrides) -> bytes:
    return json.dumps({
        "schema_version": 1,
        "designs": [_make_valid_item(**item_overrides)],
    }).encode()


def _mock_db_empty():
    """DB mock that returns no existing rows."""
    from app.models.card_design import CardDesign as _CardDesign
    db = MagicMock()
    q = MagicMock()
    q.all.return_value = []
    db.query.return_value = q
    return db


def _mock_db_with_design(design_id: str, is_active: bool = True, **fields):
    """DB mock with a single existing CardDesign row."""
    from app.models.card_design import CardDesign as _CardDesign
    db = MagicMock()
    existing = MagicMock()
    existing.id                       = design_id
    existing.label                    = fields.get("label", "Old Label")
    existing.description              = fields.get("description", "")
    existing.is_premium               = fields.get("is_premium", False)
    existing.credit_cost              = fields.get("credit_cost", 0)
    existing.sort_order               = fields.get("sort_order", 0)
    existing.browser_template         = fields.get("browser_template", _REAL_TEMPLATE)
    existing.supported_export_buckets = fields.get("supported_export_buckets", [])
    existing.animated_platforms       = fields.get("animated_platforms", [])
    existing.component_config         = fields.get("component_config", {})
    existing.is_active                = is_active

    q = MagicMock()
    q.all.return_value = [existing]
    db.query.return_value = q
    return db


# ── AD-01: Valid CREATE manifest ──────────────────────────────────────────────

@pytest.mark.unit
def test_ad01_valid_manifest_create():
    """Valid manifest for unknown design → CREATE preview row, no errors."""
    from app.services.card_design_admin_service import validate_manifest

    db = _mock_db_empty()
    result = validate_manifest(_make_manifest(), db)

    assert result.ok, f"Expected no errors, got: {result.errors}"
    assert len(result.preview_rows) == 1
    assert result.preview_rows[0].action == "CREATE"
    assert result.preview_rows[0].item.id == "test_design"
    assert result.preview_rows[0].diff == {}


# ── AD-02: Invalid JSON ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_ad02_invalid_json():
    """Non-JSON bytes → error about invalid JSON."""
    from app.services.card_design_admin_service import validate_manifest

    result = validate_manifest(b"not valid json {{{", _mock_db_empty())
    assert not result.ok
    assert any("Invalid JSON" in e for e in result.errors)


# ── AD-03: Wrong schema_version ───────────────────────────────────────────────

@pytest.mark.unit
def test_ad03_wrong_schema_version():
    """schema_version=2 → rejected wholesale."""
    from app.services.card_design_admin_service import validate_manifest

    raw = json.dumps({"schema_version": 2, "designs": [_make_valid_item()]}).encode()
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any("schema_version" in e for e in result.errors)


# ── AD-04: Protected ID ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_ad04_protected_id_fifa_rejected():
    """id='fclassic' → validation error — protected design cannot be modified via manifest."""
    from app.services.card_design_admin_service import validate_manifest

    raw = _make_manifest(id="fclassic")
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any("protected" in e.lower() for e in result.errors)


# ── AD-05: Invalid slug ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_ad05_invalid_slug_rejected():
    """id='My Design!' → slug validation error."""
    from app.services.card_design_admin_service import validate_manifest

    raw = _make_manifest(id="My Design!")
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any("invalid" in e.lower() or "slug" in e.lower() or "^[a-z]" in e for e in result.errors)


# ── AD-06: Premium/credit inconsistency ──────────────────────────────────────

@pytest.mark.unit
def test_ad06_premium_with_zero_credit_cost_rejected():
    """is_premium=True, credit_cost=0 → model_validator error."""
    from app.services.card_design_admin_service import validate_manifest

    raw = _make_manifest(is_premium=True, credit_cost=0)
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any("credit_cost" in e.lower() or "premium" in e.lower() for e in result.errors)


# ── AD-07: component_config bucket not valid for declared archetype ────────────

@pytest.mark.unit
def test_ad07_component_config_bucket_not_valid_for_archetype():
    """component_config key 'square' with archetype_id='column' → bucket not in column allowed set."""
    from app.services.card_design_admin_service import validate_manifest

    item = _make_valid_item()
    item["archetype_id"] = "column"
    item["supported_export_buckets"] = ["square"]
    item["component_config"] = {
        "square": {
            "skill_slice": 4, "show_dominant_badge": False,
            "show_height_weight": False, "show_sponsor": False,
            "platform_vars": {},
        }
    }
    raw = json.dumps({"schema_version": 1, "designs": [item]}).encode()
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any("square" in e and "column" in e.lower() for e in result.errors)


# ── AD-08: component_config bucket not in supported_export_buckets ────────────

@pytest.mark.unit
def test_ad08_component_config_bucket_not_in_supported_buckets():
    """component_config has 'portrait' but supported_export_buckets=[] → error."""
    from app.services.card_design_admin_service import validate_manifest

    item = _make_valid_item()
    item["supported_export_buckets"] = []   # portrait not declared
    item["component_config"] = {
        "portrait": {
            "skill_slice": 4, "show_dominant_badge": False,
            "show_height_weight": False, "show_sponsor": False,
            "platform_vars": {},
        }
    }
    raw = json.dumps({"schema_version": 1, "designs": [item]}).encode()
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any("portrait" in e and "supported_export_buckets" in e for e in result.errors)


# ── AD-09: platform_vars key without '--' prefix ──────────────────────────────

@pytest.mark.unit
def test_ad09_platform_vars_invalid_key_rejected():
    """platform_vars key 'hero-h' (missing '--') → field_validator error."""
    from app.services.card_design_admin_service import validate_manifest

    item = _make_valid_item()
    item["component_config"]["portrait"]["platform_vars"] = {"hero-h": "380px"}
    raw = json.dumps({"schema_version": 1, "designs": [item]}).encode()
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any("'--'" in e or "must start with" in e.lower() for e in result.errors)


# ── AD-10: browser_template does not exist ────────────────────────────────────

@pytest.mark.unit
def test_ad10_missing_browser_template_rejected():
    """browser_template pointing to non-existent file → field_validator error."""
    from app.services.card_design_admin_service import validate_manifest

    raw = _make_manifest(browser_template="public/nonexistent_template.html")
    result = validate_manifest(raw, _mock_db_empty())
    assert not result.ok
    assert any("browser_template" in e or "does not exist" in e.lower() for e in result.errors)


# ── AD-11: UPDATE action + diff ───────────────────────────────────────────────

@pytest.mark.unit
def test_ad11_existing_active_design_produces_update_with_diff():
    """Existing active design with different label → UPDATE action, diff contains 'label'."""
    from app.services.card_design_admin_service import validate_manifest

    db = _mock_db_with_design(
        "test_design",
        is_active=True,
        label="Old Label",               # differs from manifest
        supported_export_buckets=[],
        component_config={},
    )
    result = validate_manifest(_make_manifest(), db)

    assert result.ok, result.errors
    assert len(result.preview_rows) == 1
    row = result.preview_rows[0]
    assert row.action == "UPDATE"
    assert "label" in row.diff
    assert row.diff["label"] == ("Old Label", "Test Design")


# ── AD-12: REACTIVATE_UPDATE action ──────────────────────────────────────────

@pytest.mark.unit
def test_ad12_inactive_design_produces_reactivate_update():
    """Existing inactive design → REACTIVATE_UPDATE action."""
    from app.services.card_design_admin_service import validate_manifest

    db = _mock_db_with_design(
        "test_design",
        is_active=False,
        label="Test Design",
        supported_export_buckets=[],
        component_config={},
    )
    result = validate_manifest(_make_manifest(), db)

    assert result.ok, result.errors
    assert result.preview_rows[0].action == "REACTIVATE_UPDATE"


# ── AD-13: apply_manifest CREATE ─────────────────────────────────────────────

@pytest.mark.unit
def test_ad13_apply_manifest_create_inserts_and_invalidates_cache():
    """apply_manifest with a CREATE row: db.add called, commit called, cache invalidated."""
    from app.services.card_design_admin_service import (
        validate_manifest, apply_manifest, DesignManifestItem, DesignPreviewRow,
    )
    from app.services import card_design_admin_service as _svc

    item = DesignManifestItem.model_validate(_make_valid_item())
    preview_rows = [DesignPreviewRow(item=item, action="CREATE", diff={})]

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None  # no existing row

    with patch.object(_svc, "_invalidate_cache") as mock_invalidate:
        applied = apply_manifest(db, preview_rows)

    assert "test_design" in applied
    db.add.assert_called_once()
    db.commit.assert_called_once()
    mock_invalidate.assert_called_once()


# ── AD-14: get_design_usage_summary ──────────────────────────────────────────

@pytest.mark.unit
def test_ad14_get_design_usage_summary_returns_correct_counts():
    """get_design_usage_summary aggregates draft/published/license/unlocked counts."""
    from app.services.card_design_admin_service import get_design_usage_summary

    db = MagicMock()

    call_count = [0]

    def _count_side_effect(*args):
        call_count[0] += 1
        q = MagicMock()
        # Return 2, 3, 1, 4 for successive .count() calls
        q.filter.return_value.count.return_value = [2, 3, 1, 4][min(call_count[0] - 1, 3)]
        return q

    db.query.side_effect = _count_side_effect

    summary = get_design_usage_summary(db, "classic_lite")

    assert summary["draft_count"]          == 2
    assert summary["published_count"]      == 3
    assert summary["active_license_count"] == 1
    assert summary["unlocked_count"]       == 4
    assert summary["total_affected"]       == 10
