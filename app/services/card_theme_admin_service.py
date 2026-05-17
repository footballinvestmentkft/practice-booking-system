"""
Card Theme Admin Service
========================
Service layer for admin-facing theme management via JSON manifest upload.

Two-phase flow (no DB writes during preview):
  1. validate_manifest(raw_bytes) → ThemeManifestResult (errors or preview rows)
  2. apply_manifest(db, preview_rows) → list of applied ThemeDefinition

Security rules enforced here (not in routes):
  - id "default" is permanently protected from create/update/deactivate
  - id must match slug regex: ^[a-z][a-z0-9_-]*$
  - Duplicate ids within a single manifest rejected wholesale
  - is_premium=True requires credit_cost > 0; False requires credit_cost == 0
  - Max manifest size (32 KB) enforced in the route; not re-checked here
  - apply_manifest runs in a single DB transaction; _invalidate_cache() called
    only after successful commit
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .card_theme_service import ThemeDefinition, _invalidate_cache

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_MANIFEST_BYTES = 32 * 1024   # 32 KB
_PROTECTED_IDS = frozenset({"default"})
_SLUG_RE = re.compile(r'^[a-z][a-z0-9_-]*$')

# ── Pydantic manifest item ────────────────────────────────────────────────────

class ThemeManifestItem(BaseModel):
    id:               str   = Field(..., min_length=1, max_length=50)
    label:            str   = Field(..., min_length=1, max_length=80)
    is_premium:       bool
    credit_cost:      int   = Field(..., ge=0, le=9999)
    sort_order:       int   = Field(..., ge=0, le=999)
    panel_bg:         str   = Field(..., max_length=300)
    body_bg:          str   = Field(..., max_length=100)
    tab_bg:           str   = Field(..., max_length=100)
    accent:           str   = Field(..., max_length=100)
    page_bg:          str   = Field(..., max_length=100)
    dot_color:        str   = Field(..., max_length=100)
    is_light_body_bg: bool
    text_faint:       str   = Field(..., max_length=100)
    val_neutral:      str   = Field(..., max_length=100)
    skill_up:         str   = Field(..., max_length=100)
    skill_dn:         str   = Field(..., max_length=100)

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, v: str) -> str:
        if v in _PROTECTED_IDS:
            raise ValueError(f"id '{v}' is protected and cannot be created or updated via manifest")
        if not _SLUG_RE.match(v):
            raise ValueError(
                f"id '{v}' is invalid — must match ^[a-z][a-z0-9_-]*$ "
                "(lowercase, digits, hyphens, underscores; start with letter)"
            )
        return v

    @model_validator(mode="after")
    def credit_cost_premium_consistency(self) -> "ThemeManifestItem":
        if self.is_premium and self.credit_cost == 0:
            raise ValueError("Premium themes must have credit_cost > 0")
        if not self.is_premium and self.credit_cost != 0:
            raise ValueError("Free themes must have credit_cost = 0")
        return self


class ThemeManifest(BaseModel):
    schema_version: int   = Field(..., ge=1, le=1)
    themes:         list[ThemeManifestItem] = Field(..., min_length=1)


# ── Preview row ───────────────────────────────────────────────────────────────
ActionType = Literal["CREATE", "UPDATE", "REACTIVATE_UPDATE"]

@dataclass
class ThemePreviewRow:
    item:    ThemeManifestItem
    action:  ActionType
    diff:    dict[str, tuple]  # field → (old_value, new_value) for UPDATE rows


@dataclass
class ThemeManifestResult:
    errors:       list[str]         = field(default_factory=list)
    preview_rows: list[ThemePreviewRow] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ── Public API ────────────────────────────────────────────────────────────────

def validate_manifest(raw_bytes: bytes, db) -> ThemeManifestResult:
    """
    Parse, validate, and compute the preview diff against the current DB state.
    No DB writes occur here.

    Returns ThemeManifestResult with either:
      - result.errors: non-empty list of human-readable error strings (reject all)
      - result.preview_rows: list of ThemePreviewRow (safe to apply)
    """
    result = ThemeManifestResult()

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
        manifest = ThemeManifest.model_validate(raw)
    except Exception as exc:
        result.errors.append(f"Manifest validation failed: {exc}")
        return result

    # ── Duplicate id check within the manifest ────────────────────────────────
    seen_ids: set[str] = set()
    dup_ids:  set[str] = set()
    for item in manifest.themes:
        if item.id in seen_ids:
            dup_ids.add(item.id)
        seen_ids.add(item.id)
    if dup_ids:
        result.errors.append(
            f"Duplicate theme IDs in manifest: {', '.join(sorted(dup_ids))}. "
            "The entire manifest is rejected."
        )
        return result

    # ── Load current DB state (all rows, including inactive) ──────────────────
    from app.models.card_theme import CardTheme as _CardTheme
    all_rows = db.query(_CardTheme).all()
    db_map: dict[str, _CardTheme] = {r.id: r for r in all_rows}

    # ── Build preview rows ────────────────────────────────────────────────────
    _CSS_FIELDS = (
        "panel_bg", "body_bg", "tab_bg", "accent", "page_bg", "dot_color",
        "is_light_body_bg", "text_faint", "val_neutral", "skill_up", "skill_dn",
    )
    _ALL_FIELDS = ("label", "is_premium", "credit_cost", "sort_order") + _CSS_FIELDS

    for item in manifest.themes:
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

        result.preview_rows.append(ThemePreviewRow(item=item, action=action, diff=diff))

    return result


def apply_manifest(db, preview_rows: list[ThemePreviewRow]) -> list[str]:
    """
    Write all preview rows to the DB in a single transaction.
    _invalidate_cache() is called only after a successful commit.

    Returns list of applied theme IDs.

    Raises on any DB error — caller must handle rollback in that case.
    The route layer wraps this in try/except to ensure proper rollback.
    """
    from app.models.card_theme import CardTheme as _CardTheme
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    applied: list[str] = []

    try:
        for row in preview_rows:
            item = row.item
            existing = db.query(_CardTheme).filter(_CardTheme.id == item.id).first()

            if existing is None:
                # CREATE
                db.add(_CardTheme(
                    id=item.id,
                    label=item.label,
                    is_premium=item.is_premium,
                    credit_cost=item.credit_cost,
                    sort_order=item.sort_order,
                    panel_bg=item.panel_bg,
                    body_bg=item.body_bg,
                    tab_bg=item.tab_bg,
                    accent=item.accent,
                    page_bg=item.page_bg,
                    dot_color=item.dot_color,
                    is_light_body_bg=item.is_light_body_bg,
                    text_faint=item.text_faint,
                    val_neutral=item.val_neutral,
                    skill_up=item.skill_up,
                    skill_dn=item.skill_dn,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                ))
            else:
                # UPDATE or REACTIVATE_UPDATE
                existing.label           = item.label
                existing.is_premium      = item.is_premium
                existing.credit_cost     = item.credit_cost
                existing.sort_order      = item.sort_order
                existing.panel_bg        = item.panel_bg
                existing.body_bg         = item.body_bg
                existing.tab_bg          = item.tab_bg
                existing.accent          = item.accent
                existing.page_bg         = item.page_bg
                existing.dot_color       = item.dot_color
                existing.is_light_body_bg = item.is_light_body_bg
                existing.text_faint      = item.text_faint
                existing.val_neutral     = item.val_neutral
                existing.skill_up        = item.skill_up
                existing.skill_dn        = item.skill_dn
                existing.is_active       = True
                existing.updated_at      = now

            applied.append(item.id)

        db.commit()
        _invalidate_cache()

    except Exception:
        db.rollback()
        raise

    return applied


def get_theme_usage_summary(db, theme_id: str) -> dict:
    """
    Return a summary of how many users have this theme in:
      - card_drafts.draft_theme
      - card_drafts.published_theme
      - user_licenses.unlocked_card_themes (JSON list contains theme_id)

    Used by the admin toggle endpoint to warn before deactivation.
    """
    from app.models.card_draft import CardDraft
    from app.models.license import UserLicense

    draft_count = (
        db.query(CardDraft)
        .filter(CardDraft.draft_theme == theme_id)
        .count()
    )
    published_count = (
        db.query(CardDraft)
        .filter(CardDraft.published_theme == theme_id)
        .count()
    )
    # PostgreSQL JSON containment — cast JSON → JSONB so @> operator is available
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB
    unlocked_count = (
        db.query(UserLicense)
        .filter(cast(UserLicense.unlocked_card_themes, JSONB).contains([theme_id]))
        .count()
    )
    return {
        "draft_count":     draft_count,
        "published_count": published_count,
        "unlocked_count":  unlocked_count,
        "total_affected":  draft_count + published_count + unlocked_count,
    }


# ── Private helpers ───────────────────────────────────────────────────────────

def _compute_diff(existing, item: ThemeManifestItem, fields: tuple) -> dict:
    """Return {field: (old, new)} for fields that differ."""
    diff = {}
    for f in fields:
        old = getattr(existing, f)
        new = getattr(item, f)
        if str(old) != str(new):
            diff[f] = (old, new)
    return diff
