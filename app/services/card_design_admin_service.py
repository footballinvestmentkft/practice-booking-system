"""
Card Design Admin Service
=========================
Service layer for admin-facing card design management via JSON manifest upload.

Two-phase flow (no DB writes during preview):
  1. validate_manifest(raw_bytes, db) → DesignManifestResult (errors or preview rows)
  2. apply_manifest(db, preview_rows)  → list of applied design IDs

Security rules enforced here (not in routes):
  - id "fclassic" is permanently protected (legacy inputs sanitized via resolve_design_id) — cannot be created/updated/deactivated via manifest
  - id must match slug regex ^[a-z][a-z0-9_-]*$
  - Duplicate ids within a single manifest rejected wholesale
  - is_premium=True requires credit_cost > 0; False requires credit_cost == 0
  - supported_export_buckets: only known bucket names accepted
  - animated_platforms: only known platform IDs (CANVAS_SIZES keys) accepted
  - component_config: keys must be in the archetype's allowed buckets (CS-6 A-model);
    each config key must also be in supported_export_buckets
  - browser_template: must point to an existing file under app/templates/
  - apply_manifest runs in a single DB transaction; _invalidate_cache() called
    only after a successful commit

Scope note (CS-6):
  portrait/story (column archetype) and square (pulse archetype) export designs
  can now be created via admin manifest.
  browser_template still references an already-deployed template file on disk —
  the browser-side card design layer is not yet manifest-only. That is a separate,
  later architectural phase.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .card_design_service import _invalidate_cache

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_MANIFEST_BYTES  = 32 * 1024   # 32 KB
_PROTECTED_IDS      = frozenset({"fclassic"})
_SLUG_RE            = re.compile(r'^[a-z][a-z0-9_-]*$')
_VALID_BUCKETS      = frozenset({"square", "portrait", "story", "tiktok", "landscape", "banner"})
# CS-6 A-model: archetype_id → set of driver-eligible buckets.
# Designs with component_config must declare a known archetype_id and may only
# use buckets that belong to that archetype's driver family.
_ARCHETYPE_ALLOWED_BUCKETS: dict[str, frozenset] = {
    "column": frozenset({"portrait", "story"}),
    "pulse":  frozenset({"square"}),
}
_KNOWN_ARCHETYPES   = frozenset(_ARCHETYPE_ALLOWED_BUCKETS)
_TEMPLATES_ROOT     = Path(__file__).resolve().parent.parent / "templates"


def _valid_platform_ids() -> frozenset:
    from .card_constants import CANVAS_SIZES
    return frozenset(CANVAS_SIZES.keys()) - {"default"}


# ── Pydantic models ───────────────────────────────────────────────────────────

class BucketDriverConfig(BaseModel):
    """Per-bucket driver config for column_driver.html (CS-4c schema)."""
    skill_slice:         Optional[int] = Field(None, ge=1, le=20)
    show_dominant_badge: bool
    show_height_weight:  bool
    show_sponsor:        bool
    platform_vars:       dict[str, str] = Field(default_factory=dict)

    @field_validator("platform_vars")
    @classmethod
    def keys_must_be_css_custom_props(cls, v: dict) -> dict:
        for k, val in v.items():
            if not k.startswith("--"):
                raise ValueError(
                    f"platform_vars key {k!r} must start with '--' (CSS custom property)"
                )
            if len(val) > 100:
                raise ValueError(
                    f"platform_vars value for {k!r} too long (max 100 chars)"
                )
        return v


class DesignManifestItem(BaseModel):
    id:                       str                          = Field(..., min_length=1, max_length=50)
    label:                    str                          = Field(..., min_length=1, max_length=80)
    description:              str                          = Field("",  max_length=500)
    is_premium:               bool
    credit_cost:              int                          = Field(..., ge=0, le=9999)
    sort_order:               int                          = Field(..., ge=0, le=999)
    browser_template:         str                          = Field(..., max_length=300)
    archetype_id:             Optional[str]                = Field(None, max_length=50)
    supported_export_buckets: list[str]                    = Field(default_factory=list)
    animated_platforms:       list[str]                    = Field(default_factory=list)
    component_config:         dict[str, BucketDriverConfig] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, v: str) -> str:
        if v in _PROTECTED_IDS:
            raise ValueError(
                f"id '{v}' is protected and cannot be created or updated via manifest"
            )
        if not _SLUG_RE.match(v):
            raise ValueError(
                f"id '{v}' is invalid — must match ^[a-z][a-z0-9_-]*$ "
                "(lowercase, digits, hyphens, underscores; must start with a letter)"
            )
        return v

    @field_validator("supported_export_buckets")
    @classmethod
    def validate_buckets(cls, v: list) -> list:
        invalid = set(v) - _VALID_BUCKETS
        if invalid:
            raise ValueError(
                f"Invalid export bucket name(s): {sorted(invalid)}. "
                f"Valid values: {sorted(_VALID_BUCKETS)}"
            )
        if len(v) != len(set(v)):
            raise ValueError("Duplicate bucket names in supported_export_buckets")
        return v

    @field_validator("animated_platforms")
    @classmethod
    def validate_animated_platforms(cls, v: list) -> list:
        valid = _valid_platform_ids()
        invalid = set(v) - valid
        if invalid:
            raise ValueError(
                f"Invalid animated_platforms value(s): {sorted(invalid)}. "
                f"Valid platform IDs: {sorted(valid)}"
            )
        if len(v) != len(set(v)):
            raise ValueError("Duplicate entries in animated_platforms")
        return v

    @field_validator("browser_template")
    @classmethod
    def template_must_exist(cls, v: str) -> str:
        if not (_TEMPLATES_ROOT / v).is_file():
            raise ValueError(
                f"browser_template '{v}' does not exist under app/templates/. "
                "The template file must already be deployed on the server."
            )
        return v

    @model_validator(mode="after")
    def credit_premium_consistency(self) -> "DesignManifestItem":
        if self.is_premium and self.credit_cost == 0:
            raise ValueError("Premium designs must have credit_cost > 0")
        if not self.is_premium and self.credit_cost != 0:
            raise ValueError("Free designs must have credit_cost = 0")
        return self

    @model_validator(mode="after")
    def archetype_and_component_config_consistency(self) -> "DesignManifestItem":
        bucket_set = set(self.supported_export_buckets)

        if self.component_config:
            # archetype_id is required when component_config is non-empty
            if not self.archetype_id:
                raise ValueError(
                    "archetype_id is required when component_config is set. "
                    f"Known archetypes: {sorted(_KNOWN_ARCHETYPES)}"
                )
            if self.archetype_id not in _KNOWN_ARCHETYPES:
                raise ValueError(
                    f"archetype_id '{self.archetype_id}' is unknown. "
                    f"Known archetypes: {sorted(_KNOWN_ARCHETYPES)}"
                )
            allowed = _ARCHETYPE_ALLOWED_BUCKETS[self.archetype_id]
            for bucket_key in self.component_config:
                if bucket_key not in allowed:
                    raise ValueError(
                        f"component_config key '{bucket_key}' is not a valid bucket for "
                        f"archetype '{self.archetype_id}'. "
                        f"Allowed buckets: {sorted(allowed)}"
                    )
                if bucket_key not in bucket_set:
                    raise ValueError(
                        f"component_config has key '{bucket_key}' but '{bucket_key}' is not in "
                        "supported_export_buckets — either add it to supported_export_buckets "
                        "or remove the component_config entry."
                    )
        return self


class DesignManifest(BaseModel):
    schema_version: int                      = Field(..., ge=1, le=1)
    designs:        list[DesignManifestItem] = Field(..., min_length=1)


# ── Preview row ───────────────────────────────────────────────────────────────
ActionType = Literal["CREATE", "UPDATE", "REACTIVATE_UPDATE"]


@dataclass
class DesignPreviewRow:
    item:   DesignManifestItem
    action: ActionType
    diff:   dict[str, tuple]   # field → (old_value, new_value) for UPDATE rows


@dataclass
class DesignManifestResult:
    errors:       list[str]              = field(default_factory=list)
    preview_rows: list[DesignPreviewRow] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ── Public API ────────────────────────────────────────────────────────────────

def validate_manifest(raw_bytes: bytes, db) -> DesignManifestResult:
    """
    Parse, validate, and compute the preview diff against the current DB state.
    No DB writes occur here.

    Returns DesignManifestResult with either:
      - result.errors: non-empty list of human-readable error strings (reject all)
      - result.preview_rows: list of DesignPreviewRow (safe to apply)
    """
    result = DesignManifestResult()

    # ── JSON parse ────────────────────────────────────────────────────────────
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        result.errors.append(f"Invalid JSON: {exc}")
        return result

    # ── Schema version ────────────────────────────────────────────────────────
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        result.errors.append(
            "schema_version must be 1. "
            "Unknown versions are rejected in full — no partial apply."
        )
        return result

    # ── Pydantic validation ───────────────────────────────────────────────────
    try:
        manifest = DesignManifest.model_validate(raw)
    except Exception as exc:
        result.errors.append(f"Manifest validation failed: {exc}")
        return result

    # ── Duplicate id check within the manifest ────────────────────────────────
    seen_ids: set[str] = set()
    dup_ids:  set[str] = set()
    for item in manifest.designs:
        if item.id in seen_ids:
            dup_ids.add(item.id)
        seen_ids.add(item.id)
    if dup_ids:
        result.errors.append(
            f"Duplicate design IDs in manifest: {', '.join(sorted(dup_ids))}. "
            "The entire manifest is rejected."
        )
        return result

    # ── Load current DB state (all rows, including inactive) ──────────────────
    from app.models.card_design import CardDesign as _CardDesign
    all_rows = db.query(_CardDesign).all()
    db_map: dict[str, _CardDesign] = {r.id: r for r in all_rows}

    # ── Build preview rows ────────────────────────────────────────────────────
    _FLAT_FIELDS = ("label", "description", "is_premium", "credit_cost", "sort_order", "browser_template", "archetype_id")
    _JSONB_FIELDS = ("supported_export_buckets", "animated_platforms", "component_config")
    _ALL_FIELDS = _FLAT_FIELDS + _JSONB_FIELDS

    for item in manifest.designs:
        existing = db_map.get(item.id)
        if existing is None:
            action: ActionType = "CREATE"
            diff: dict = {}
        elif not existing.is_active:
            action = "REACTIVATE_UPDATE"
            diff = _compute_diff(existing, item, _ALL_FIELDS)
        else:
            action = "UPDATE"
            diff = _compute_diff(existing, item, _ALL_FIELDS)

        result.preview_rows.append(DesignPreviewRow(item=item, action=action, diff=diff))

    return result


def apply_manifest(db, preview_rows: list[DesignPreviewRow]) -> list[str]:
    """
    Write all preview rows to the DB in a single transaction.
    _invalidate_cache() is called only after a successful commit.

    Returns list of applied design IDs.
    Raises on any DB error — caller must handle rollback via the re-raise.
    """
    from app.models.card_design import CardDesign as _CardDesign
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    applied: list[str] = []

    try:
        for row in preview_rows:
            item = row.item
            existing = db.query(_CardDesign).filter(_CardDesign.id == item.id).first()
            cc_raw = {k: v.model_dump() for k, v in item.component_config.items()}

            if existing is None:
                db.add(_CardDesign(
                    id=item.id,
                    label=item.label,
                    description=item.description,
                    is_premium=item.is_premium,
                    credit_cost=item.credit_cost,
                    sort_order=item.sort_order,
                    browser_template=item.browser_template,
                    archetype_id=item.archetype_id,
                    supported_export_buckets=list(item.supported_export_buckets),
                    animated_platforms=list(item.animated_platforms),
                    component_config=cc_raw,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                ))
            else:
                existing.label                    = item.label
                existing.description              = item.description
                existing.is_premium               = item.is_premium
                existing.credit_cost              = item.credit_cost
                existing.sort_order               = item.sort_order
                existing.browser_template         = item.browser_template
                existing.archetype_id             = item.archetype_id
                existing.supported_export_buckets = list(item.supported_export_buckets)
                existing.animated_platforms       = list(item.animated_platforms)
                existing.component_config         = cc_raw
                existing.is_active                = True
                existing.updated_at               = now

            applied.append(item.id)

        db.commit()
        _invalidate_cache()

    except Exception:
        db.rollback()
        raise

    return applied


def get_design_usage_summary(db, design_id: str) -> dict:
    """
    Return a summary of how many users have this design active in:
      - card_drafts.draft_variant
      - card_drafts.published_variant
      - user_licenses.card_variant    (currently active browser design)
      - user_licenses.unlocked_card_variants (JSON list contains design_id)

    Used by the admin toggle endpoint to warn before deactivation.
    """
    from app.models.card_draft import CardDraft
    from app.models.license import UserLicense
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB

    draft_count = (
        db.query(CardDraft)
        .filter(CardDraft.draft_variant == design_id)
        .count()
    )
    published_count = (
        db.query(CardDraft)
        .filter(CardDraft.published_variant == design_id)
        .count()
    )
    active_license_count = (
        db.query(UserLicense)
        .filter(UserLicense.card_variant == design_id)
        .count()
    )
    unlocked_count = (
        db.query(UserLicense)
        .filter(cast(UserLicense.unlocked_card_variants, JSONB).contains([design_id]))
        .count()
    )
    return {
        "draft_count":          draft_count,
        "published_count":      published_count,
        "active_license_count": active_license_count,
        "unlocked_count":       unlocked_count,
        "total_affected": draft_count + published_count + active_license_count + unlocked_count,
    }


# ── Private helpers ───────────────────────────────────────────────────────────

def _compute_diff(existing, item: DesignManifestItem, fields: tuple) -> dict:
    """Return {field: (old, new)} for fields that differ."""
    _JSONB_FIELDS = {"supported_export_buckets", "animated_platforms", "component_config"}
    diff = {}
    for f in fields:
        old = getattr(existing, f)
        new = getattr(item, f)
        if f == "component_config":
            new_raw = {k: v.model_dump() for k, v in new.items()}
        else:
            new_raw = new
        if f in _JSONB_FIELDS:
            if json.dumps(old, sort_keys=True) != json.dumps(new_raw, sort_keys=True):
                diff[f] = (old, new_raw)
        else:
            if str(old) != str(new_raw):
                diff[f] = (old, new_raw)
    return diff
