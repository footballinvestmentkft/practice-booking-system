"""
Juggling Video Annotation Helper — LOCAL DEV TOOL ONLY v2.

Runs on http://127.0.0.1:8001 — NEVER binds to 0.0.0.0.
This script is NOT part of the production FastAPI application.
It does NOT connect to the main app, the production DB, or any external service.

Usage:
    .venv/bin/python scripts/juggling_annotation_helper.py
    # Then open http://127.0.0.1:8001 in a browser.

Contact taxonomy: loaded at startup from datasets/juggling/contact_types_v1.json.
No contact type is hardcoded in Python or JavaScript.

Security invariants:
  - Only files listed in dataset_manifest.json are accessible
  - Path(filename).name == filename enforced before any disk access
  - Resolved path must be under UPLOADS_DIR (symlink escape blocked)
  - CORS restricted to localhost:8001 only
  - thigh→hip auto-migration: FORBIDDEN
  - custom_other: excluded_from_training always true
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

# ── Runtime constants ─────────────────────────────────────────────────────────

HOST = "127.0.0.1"   # SECURITY: NEVER change to 0.0.0.0
PORT = 8001

ROOT            = Path(__file__).resolve().parent.parent
UPLOADS_DIR     = (ROOT / "app/uploads/juggling").resolve()
ANNOTATIONS_DIR = (ROOT / "datasets/juggling/annotations").resolve()
MANIFEST_PATH   = (ROOT / "datasets/juggling/dataset_manifest.json").resolve()
TAXONOMY_PATH   = (ROOT / "datasets/juggling/contact_types_v1.json").resolve()


# ── Taxonomy loading (single source-of-truth) ─────────────────────────────────

def _load_taxonomy() -> dict:
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


_taxonomy: dict = _load_taxonomy()

_ALL_CONTACT_TYPES = frozenset(_taxonomy["all_keys"])
_STABLE_TYPES      = frozenset(_taxonomy["stable_keys"])
_RIGHT_TYPES       = frozenset(_taxonomy["right_stable_keys"])
_LEFT_TYPES        = frozenset(_taxonomy["left_stable_keys"])
_CENTER_TYPES      = frozenset(_taxonomy["center_stable_keys"])

# Legacy v1 body_part values — rejected with an instructive error
_LEGACY_V1_BODY_PARTS      = frozenset(["foot", "knee", "thigh", "shoulder", "head", "chest"])
_LEGACY_AMBIGUOUS           = frozenset(["foot", "knee", "shoulder"])
_LEGACY_FORBIDDEN_MIGRATION = frozenset(["thigh"])  # thigh→hip FORBIDDEN


# ── Field policy ──────────────────────────────────────────────────────────────

HUMAN_FIELDS: frozenset[str] = frozenset({
    "difficulty", "total_juggling_count", "count_confidence",
    "dominant_body_part", "body_parts_used",
    "ball_visible_quality", "lighting_quality", "camera_stability",
    "multi_person_present", "multiple_balls_present",
    "expected_validity", "invalidity_reason", "notes",
    "annotator", "annotation_date",
    "second_annotator", "second_annotator_count", "inter_annotator_agreement",
    "privacy_review_status",
    "ownership_confirmed", "consent_confirmed",
    "recognizable_third_party_present", "minor_present", "external_source",
    "contact_events",
})

REQUIRED_FOR_ANNOTATED: frozenset[str] = frozenset({
    "total_juggling_count", "dominant_body_part", "body_parts_used",
    "difficulty", "ball_visible_quality", "lighting_quality",
    "camera_stability", "multi_person_present", "multiple_balls_present",
    "expected_validity", "annotator", "annotation_date",
})

_PRIVACY_REQUIRED: dict[str, bool] = {
    "ownership_confirmed":              True,
    "consent_confirmed":                True,
    "recognizable_third_party_present": False,
    "minor_present":                    False,
    "external_source":                  False,
}

# v2 enum values — dominant_body_part uses abstract regions (hip, not thigh; back added)
_VALID_ENUMS: dict[str, set] = {
    "difficulty":            {"easy", "medium", "hard"},
    "count_confidence":      {"high", "medium", "low"},
    "dominant_body_part":    {"foot", "knee", "hip", "chest", "shoulder", "head", "back", "mixed"},
    "ball_visible_quality":  {"excellent", "good", "partial", "poor"},
    "lighting_quality":      {"excellent", "good", "variable", "poor"},
    "camera_stability":      {"tripod", "handheld_stable", "handheld_moving", "tracking"},
    "expected_validity":     {"valid", "borderline", "invalid"},
    "privacy_review_status": {"pending", "approved", "rejected"},
}

# body_parts_used abstract region keys (v2: hip replaces thigh; back added)
_VALID_BODY_PARTS_USED_KEYS = frozenset({"foot", "knee", "hip", "chest", "shoulder", "head", "back"})

# contact_events v2 enums
_VALID_ANNOTATION_CONFIDENCE = frozenset({"certain", "probable", "uncertain"})
_VALID_ANNOTATION_SOURCE     = frozenset({"manual_annotator", "manual_user", "model_prediction", "user_corrected"})
_VALID_REVIEW_STATUS_STABLE  = frozenset({"pending", "confirmed", "corrected", "rejected"})
_VALID_REVIEW_STATUS_CUSTOM  = frozenset({
    "pending_taxonomy_review", "reclassified", "promotion_candidate", "promoted", "approved_unclassified"
})
_VALID_EXPLICIT_SIDES        = frozenset({"left", "right", "center", "unknown"})

_VALID_CUSTOM_LABEL_PATTERN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _save_manifest(manifest: dict) -> None:
    _atomic_write_json(MANIFEST_PATH, manifest)


def _allowed_files() -> dict[str, str]:
    return {v["filename"]: v["video_id"] for v in _load_manifest()["videos"]}


def _safe_video_path(filename: str) -> Path:
    if not filename:
        raise HTTPException(status_code=400, detail="Empty filename.")
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Path traversal detected.")
    allowed = _allowed_files()
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="Video not in manifest allowlist.")
    candidate = (UPLOADS_DIR / filename).resolve()
    try:
        candidate.relative_to(UPLOADS_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal detected.")
    if candidate.is_symlink():
        raise HTTPException(status_code=400, detail="Symlinks not permitted.")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk.")
    return candidate


def _atomic_write_json(path: Path, data: dict) -> None:
    bak = path.with_suffix(path.suffix + ".bak")
    tmp = path.with_suffix(path.suffix + ".tmp")
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    try:
        if path.exists():
            shutil.copy2(path, bak)
        tmp.write_text(content, encoding="utf-8")
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _derived_side(contact_type: str) -> Optional[str]:
    """Return the side implied by a stable contact_type key, or None for custom_other."""
    if contact_type in _RIGHT_TYPES:
        return "right"
    if contact_type in _LEFT_TYPES:
        return "left"
    if contact_type in _CENTER_TYPES:
        return "center"
    return None  # custom_other


def _compute_annotation_status(data: dict) -> str:
    for field in REQUIRED_FOR_ANNOTATED:
        val = data.get(field)
        if val is None:
            return "human_review_pending"
        if isinstance(val, str) and not val.strip():
            return "human_review_pending"
    return "annotated"


def _compute_privacy_status(data: dict) -> str:
    if data.get("privacy_review_status") == "rejected":
        return "rejected"
    for field, required in _PRIVACY_REQUIRED.items():
        if data.get(field) != required:
            return "pending"
    return "approved"


def _compute_dataset_eligibility(annotation_status: str, privacy_status: str) -> str:
    if privacy_status != "approved":
        return "privacy_pending"
    if annotation_status == "annotated":
        return "eligible"
    return "pending_human_review"


# ── Contact event v2 validator ────────────────────────────────────────────────

def _validate_contact_event_v2(evt: dict, i: int) -> Optional[str]:
    """Return an error string if the v2 contact event is invalid, else None."""

    # event_id
    if not isinstance(evt.get("event_id"), int):
        return f"contact_events[{i}].event_id must be an integer"
    # timestamp_ms
    if not isinstance(evt.get("timestamp_ms"), int) or evt["timestamp_ms"] < 0:
        return f"contact_events[{i}].timestamp_ms must be a non-negative integer"

    ctype = evt.get("contact_type")

    # Legacy v1 body_part field detection
    if ctype is None and "body_part" in evt:
        bp = evt["body_part"]
        if bp in _LEGACY_FORBIDDEN_MIGRATION:
            return (
                f"contact_events[{i}]: Legacy v1 body_part='{bp}' detected. "
                f"thigh→hip auto-migration is FORBIDDEN. Manual re-labeling to a v2 contact_type required."
            )
        if bp in _LEGACY_V1_BODY_PARTS:
            return (
                f"contact_events[{i}]: Legacy v1 body_part='{bp}' detected. "
                f"Use contact_type from the 18-type taxonomy. Manual re-labeling required before saving."
            )

    if not ctype:
        return f"contact_events[{i}].contact_type is required"

    # Validate contact_type: check v2 taxonomy FIRST.
    # Note: 'chest' and 'head' are valid v2 contact_types AND were v1 body_parts —
    # taxonomy membership wins; only types NOT in the taxonomy fall through to legacy checks.
    if ctype not in _ALL_CONTACT_TYPES:
        if ctype in _LEGACY_FORBIDDEN_MIGRATION:
            return (
                f"contact_events[{i}].contact_type='{ctype}': this is a legacy v1 body_part. "
                f"thigh→hip auto-migration is FORBIDDEN. Re-label manually with a v2 contact_type."
            )
        if ctype in _LEGACY_V1_BODY_PARTS:
            return (
                f"contact_events[{i}].contact_type='{ctype}': this is a legacy v1 body_part, "
                f"not a valid v2 contact_type. Choose a specific type from the 18-type taxonomy "
                f"(e.g. right_instep, left_knee, right_shoulder)."
            )
        return (
            f"contact_events[{i}].contact_type '{ctype}' not in taxonomy. "
            f"Valid: {sorted(_ALL_CONTACT_TYPES)}"
        )

    # annotation_confidence
    if evt.get("annotation_confidence") not in _VALID_ANNOTATION_CONFIDENCE:
        return f"contact_events[{i}].annotation_confidence must be one of {sorted(_VALID_ANNOTATION_CONFIDENCE)}"

    # annotation_source (optional — helper defaults to manual_annotator)
    src = evt.get("annotation_source")
    if src is not None and src not in _VALID_ANNOTATION_SOURCE:
        return f"contact_events[{i}].annotation_source '{src}' invalid"

    # review_status (optional — helper sets default)
    rs = evt.get("review_status")
    if rs is not None:
        if ctype == "custom_other" and rs not in _VALID_REVIEW_STATUS_CUSTOM:
            return f"contact_events[{i}].review_status '{rs}' invalid for custom_other"
        if ctype != "custom_other" and rs not in _VALID_REVIEW_STATUS_STABLE:
            return f"contact_events[{i}].review_status '{rs}' invalid for stable contact_type"

    side = evt.get("side")

    # Side consistency for stable types
    if ctype in _RIGHT_TYPES and side != "right":
        return (
            f"contact_events[{i}]: '{ctype}' is a right-side type — side must be 'right', got {side!r}. "
            f"Side is derived from contact_type and must not be set independently."
        )
    if ctype in _LEFT_TYPES and side != "left":
        return (
            f"contact_events[{i}]: '{ctype}' is a left-side type — side must be 'left', got {side!r}. "
            f"Side is derived from contact_type and must not be set independently."
        )
    if ctype in _CENTER_TYPES and side != "center":
        return (
            f"contact_events[{i}]: '{ctype}' is a center type (no laterality) — "
            f"side must be 'center', got {side!r}."
        )

    # custom_other rules
    if ctype == "custom_other":
        custom_label = evt.get("custom_label")
        if not custom_label:
            return f"contact_events[{i}]: custom_other requires a non-empty custom_label"
        if not isinstance(custom_label, str):
            return f"contact_events[{i}].custom_label must be a string"
        if len(custom_label) > 40:
            return f"contact_events[{i}].custom_label exceeds 40 characters"
        if not all(c in _VALID_CUSTOM_LABEL_PATTERN_CHARS for c in custom_label):
            return f"contact_events[{i}].custom_label must only contain lowercase letters, digits, and underscores"
        if custom_label in _ALL_CONTACT_TYPES:
            return f"contact_events[{i}].custom_label '{custom_label}' clashes with an existing stable key"

        custom_desc = evt.get("custom_description")
        if not custom_desc:
            return f"contact_events[{i}]: custom_other requires a non-empty custom_description"
        if not isinstance(custom_desc, str):
            return f"contact_events[{i}].custom_description must be a string"
        if len(custom_desc) > 200:
            return f"contact_events[{i}].custom_description exceeds 200 characters"

        if side not in _VALID_EXPLICIT_SIDES:
            return (
                f"contact_events[{i}]: custom_other requires explicit side "
                f"(left/right/center/unknown), got {side!r}"
            )
    else:
        # Stable type: custom fields must be absent or null
        if evt.get("custom_label") is not None:
            return f"contact_events[{i}]: stable contact_type '{ctype}' must have custom_label=null"
        if evt.get("custom_description") is not None:
            return f"contact_events[{i}]: stable contact_type '{ctype}' must have custom_description=null"

    return None


def _fill_contact_event_defaults(evt: dict) -> dict:
    """Add computed/default fields to a validated contact event before saving."""
    ctype   = evt["contact_type"]
    is_cust = (ctype == "custom_other")
    merged  = dict(evt)
    merged.setdefault("annotation_source",     "manual_annotator")
    merged.setdefault("review_status",          "pending_taxonomy_review" if is_cust else "pending")
    merged.setdefault("excluded_from_training", True)
    merged.setdefault("excluded_from_count",    merged["review_status"] == "rejected")
    merged.setdefault("promotion_candidate",    False)
    merged.setdefault("taxonomy_version",       "v1")
    merged.setdefault("annotator",              None)
    merged.setdefault("note",                   None)
    # Derive side for stable types
    if ctype != "custom_other":
        merged["side"] = _derived_side(ctype)
    # custom_other always excluded from training
    if is_cust:
        merged["excluded_from_training"] = True
    merged.setdefault("custom_label",       None)
    merged.setdefault("custom_description", None)
    return merged


# ── Human field validator ─────────────────────────────────────────────────────

def _validate_human_fields(body: dict) -> Optional[str]:
    """Return error string if body contains invalid values, else None."""

    # Enum validation
    for field, valid_set in _VALID_ENUMS.items():
        if field in body and body[field] is not None:
            val = body[field]
            if val not in valid_set:
                return f"Invalid value for '{field}': {val!r}. Allowed: {sorted(valid_set)}"

    # dominant_body_part: thigh is deprecated in v2
    if body.get("dominant_body_part") == "thigh":
        return (
            "'dominant_body_part'='thigh' is deprecated in schema v2. "
            "Use 'hip' for csípő contact. thigh→hip auto-migration is FORBIDDEN."
        )

    # Numeric validation
    if "total_juggling_count" in body and body["total_juggling_count"] is not None:
        val = body["total_juggling_count"]
        if not isinstance(val, int) or val < 0:
            return f"'total_juggling_count' must be a non-negative integer, got {val!r}"

    # Boolean fields
    for bool_field in (
        "multi_person_present", "multiple_balls_present",
        "ownership_confirmed", "consent_confirmed",
        "recognizable_third_party_present", "minor_present", "external_source",
    ):
        if bool_field in body and body[bool_field] is not None:
            if not isinstance(body[bool_field], bool):
                return f"'{bool_field}' must be a boolean, got {body[bool_field]!r}"

    # body_parts_used — v2 abstract regions (hip replaces thigh; back added)
    if "body_parts_used" in body and body["body_parts_used"] is not None:
        bpu = body["body_parts_used"]
        if not isinstance(bpu, dict):
            return "'body_parts_used' must be an object"
        for k, v in bpu.items():
            if k == "thigh":
                return (
                    "'body_parts_used.thigh' is deprecated in schema v2. "
                    "Use 'body_parts_used.hip' instead. Auto-migration is FORBIDDEN."
                )
            if k not in _VALID_BODY_PARTS_USED_KEYS:
                return f"Unknown body_parts_used key: {k!r}. Valid: {sorted(_VALID_BODY_PARTS_USED_KEYS)}"
            if v is not None and not isinstance(v, bool):
                return f"body_parts_used.{k} must be bool or null"

    # contact_events — v2 validation
    if "contact_events" in body and body["contact_events"] is not None:
        events = body["contact_events"]
        if not isinstance(events, list):
            return "'contact_events' must be an array"
        for i, evt in enumerate(events):
            if not isinstance(evt, dict):
                return f"contact_events[{i}] must be an object"
            error = _validate_contact_event_v2(evt, i)
            if error:
                return error

    return None


def _update_manifest_entry(video_id: str, annotation: dict) -> None:
    manifest = _load_manifest()
    ann_status  = annotation.get("annotation_status",    "human_review_pending")
    priv_status = annotation.get("privacy_review_status","pending")
    eligibility = _compute_dataset_eligibility(ann_status, priv_status)
    for entry in manifest["videos"]:
        if entry["video_id"] == video_id:
            entry["annotation_status"]    = ann_status
            entry["privacy_review_status"] = priv_status
            entry["dataset_eligibility"]   = eligibility
            break
    summary = manifest.get("summary", {})
    vids    = manifest["videos"]
    summary["metadata_ready"]         = sum(1 for v in vids if v.get("annotation_status") == "metadata_ready")
    summary["annotated_videos"]       = sum(1 for v in vids if v.get("annotation_status") == "annotated")
    summary["reviewed_videos"]        = sum(1 for v in vids if v.get("annotation_status") == "reviewed")
    summary["privacy_approved_videos"] = sum(1 for v in vids if v.get("privacy_review_status") == "approved")
    manifest["summary"] = summary
    _save_manifest(manifest)


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Juggling Annotation Helper v2",
    description="Local dev tool — localhost:8001 only",
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8001", "http://localhost:8001"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_HTML)


@app.get("/media/{filename}")
def serve_video(filename: str):
    path = _safe_video_path(filename)
    return FileResponse(
        path=str(path),
        media_type="video/mp4",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/manifest")
def get_manifest():
    return _load_manifest()


@app.get("/api/taxonomy")
def get_taxonomy():
    """Returns the contact type taxonomy (source-of-truth). Used by the browser UI."""
    return _taxonomy


@app.get("/api/annotation/{video_id}")
def get_annotation(video_id: str):
    allowed = {v["video_id"] for v in _load_manifest()["videos"]}
    if video_id not in allowed:
        raise HTTPException(status_code=404, detail="video_id not in manifest.")
    path = ANNOTATIONS_DIR / f"{video_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Annotation file not found.")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/annotation/{video_id}")
async def save_annotation(video_id: str, request: Request):
    manifest = _load_manifest()
    allowed  = {v["video_id"] for v in manifest["videos"]}
    if video_id not in allowed:
        raise HTTPException(status_code=404, detail="video_id not in manifest.")

    ann_path = ANNOTATIONS_DIR / f"{video_id}.json"
    if not ann_path.exists():
        raise HTTPException(status_code=404, detail="Annotation skeleton not found.")

    existing = json.loads(ann_path.read_text(encoding="utf-8"))

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON body.")

    # Reject non-human fields
    blocked = set(body.keys()) - HUMAN_FIELDS
    if blocked:
        raise HTTPException(
            status_code=422,
            detail=f"Objective fields are read-only and cannot be set: {sorted(blocked)}"
        )

    # Field-level validation
    error = _validate_human_fields(body)
    if error:
        raise HTTPException(status_code=422, detail=error)

    # Merge: only human fields overwrite; objective fields preserved
    merged = {**existing}
    for k, v in body.items():
        if k in HUMAN_FIELDS:
            merged[k] = v

    # Fill contact_event defaults
    if merged.get("contact_events"):
        merged["contact_events"] = [
            _fill_contact_event_defaults(evt)
            for evt in merged["contact_events"]
        ]

    # Derive status (never auto-set "reviewed")
    merged["annotation_status"]    = _compute_annotation_status(merged)
    merged["privacy_review_status"] = _compute_privacy_status(merged)
    if merged.get("annotation_status") == "reviewed":
        merged["annotation_status"] = "annotated"

    try:
        _atomic_write_json(ann_path, merged)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Save failed: {exc}")

    try:
        _update_manifest_entry(video_id, merged)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Manifest update failed: {exc}")

    eligibility = _compute_dataset_eligibility(
        merged["annotation_status"], merged["privacy_review_status"]
    )
    return {
        "ok": True,
        "video_id": video_id,
        "annotation_status":    merged["annotation_status"],
        "privacy_review_status": merged["privacy_review_status"],
        "dataset_eligibility":   eligibility,
    }


# ── Embedded HTML/JS UI ───────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Juggling Annotation Helper v2</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; background: #0f0f0f; color: #e0e0e0; }
  h1 { color: #fff; padding: 16px 24px; margin: 0; background: #1a1a2e; border-bottom: 1px solid #333; font-size: 1.1em; }
  #list-view { padding: 24px; display: flex; flex-wrap: wrap; gap: 16px; }
  .video-card { background: #1e1e2e; border: 1px solid #333; border-radius: 8px; padding: 16px; width: 260px; cursor: pointer; transition: border-color .2s; }
  .video-card:hover { border-color: #4a9eff; }
  .video-card h3 { margin: 0 0 8px; color: #4a9eff; font-size: .95em; }
  .video-card p { margin: 2px 0; font-size: .82em; color: #aaa; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: .75em; font-weight: 600; margin-top: 6px; }
  .badge-metadata_ready { background: #1d3557; color: #69b4ff; }
  .badge-human_review_pending { background: #2d2200; color: #ffcc44; }
  .badge-annotated { background: #0d2b0d; color: #44dd44; }
  .badge-privacy-pending { background: #2b1a00; color: #ff9944; }
  .badge-privacy-approved { background: #0d2b0d; color: #44dd44; }

  #annotate-view { display: none; padding: 16px 24px; }
  #back-btn { background: #333; border: none; color: #aaa; padding: 6px 14px; border-radius: 6px; cursor: pointer; margin-bottom: 16px; font-size: .85em; }
  #back-btn:hover { background: #444; color: #fff; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
  @media(max-width:900px) { .two-col { grid-template-columns: 1fr; } }

  #player-panel { position: sticky; top: 16px; }
  video { width: 100%; border-radius: 8px; background: #000; border: 1px solid #333; }
  .player-controls { display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .player-controls button { background: #2a2a3a; border: 1px solid #444; color: #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: .8em; }
  .player-controls button:hover { background: #3a3a4a; }
  #time-display { font-size: .85em; color: #aaa; min-width: 90px; }
  #speed-btn { min-width: 52px; }

  .form-section { background: #1e1e2e; border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  .form-section h3 { margin: 0 0 12px; font-size: .9em; color: #4a9eff; text-transform: uppercase; letter-spacing: .05em; }
  .field-row { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
  .field-row label { min-width: 220px; font-size: .83em; color: #bbb; padding-top: 4px; }
  .field-row label .req { color: #ff6644; }
  .field-row input[type=number], .field-row input[type=text], .field-row select, .field-row textarea {
    background: #12121f; border: 1px solid #444; color: #e0e0e0; padding: 5px 8px; border-radius: 4px; font-size: .85em; }
  .field-row input[type=number] { width: 80px; }
  .field-row input[type=text], .field-row select { width: 200px; }
  .field-row textarea { width: 300px; height: 60px; resize: vertical; }
  .radio-group, .checkbox-group { display: flex; gap: 10px; flex-wrap: wrap; }
  .radio-group label, .checkbox-group label { display: flex; align-items: center; gap: 4px; font-size: .83em; color: #ccc; min-width: auto; padding-top: 0; }
  .bool-group { display: flex; gap: 10px; }
  .bool-group label { display: flex; align-items: center; gap: 4px; font-size: .83em; color: #ccc; min-width: auto; padding-top: 0; }

  #contact-events-list { margin-top: 8px; }
  .contact-event { background: #12121f; border: 1px solid #444; border-radius: 4px; padding: 8px 10px; margin-bottom: 6px; font-size: .82em; }
  .ce-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
  .ce-ts { color: #4a9eff; min-width: 60px; font-weight: 600; }
  .ce-type-select, .ce-conf-select { background: #1a1a2e; border: 1px solid #555; color: #ddd; padding: 3px 6px; border-radius: 3px; font-size: .8em; }
  .ce-side-derived { color: #888; font-size: .78em; padding: 2px 6px; background: #1a1a1a; border: 1px solid #333; border-radius: 3px; }
  .ce-side-select { background: #1a1a2e; border: 1px solid #555; color: #ddd; padding: 3px 6px; border-radius: 3px; font-size: .8em; }
  .ce-del-btn { background: #3a1010; border: 1px solid #8b3333; color: #ff6666; padding: 2px 8px; border-radius: 3px; cursor: pointer; margin-left: auto; }
  .custom-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 4px; }
  .custom-label-inp { background: #12121f; border: 1px solid #664; color: #ddd; padding: 3px 6px; border-radius: 3px; font-size: .8em; width: 160px; }
  .custom-desc-inp { background: #12121f; border: 1px solid #664; color: #ddd; padding: 3px 6px; border-radius: 3px; font-size: .8em; width: 220px; }
  .custom-warn { color: #ffcc44; font-size: .75em; }
  .legacy-event { border-color: #884400 !important; }
  .legacy-badge { background: #2b1a00; color: #ff9944; padding: 2px 6px; border-radius: 3px; font-size: .75em; }
  .legacy-warn { color: #ff9944; font-size: .75em; }

  #save-btn { background: #1a4a1a; border: 1px solid #44aa44; color: #aaffaa; padding: 10px 28px; border-radius: 6px; cursor: pointer; font-size: .95em; font-weight: 600; }
  #save-btn:hover { background: #1e5a1e; }
  #save-status { margin-top: 10px; padding: 8px 14px; border-radius: 6px; font-size: .85em; display: none; }
  .status-ok  { background: #0d2b0d; color: #44dd44; border: 1px solid #227722; }
  .status-err { background: #2b0d0d; color: #ff6666; border: 1px solid #881111; }
  #unsaved-indicator { display: none; font-size: .8em; color: #ffcc44; margin-left: 12px; }
  .privacy-warning { background: #2b1a00; border: 1px solid #884400; color: #ffcc44; padding: 8px 12px; border-radius: 6px; font-size: .82em; margin-bottom: 10px; }
  .ce-note { font-size: .8em; color: #666; }
</style>
</head>
<body>
<h1>🎯 Juggling Annotation Helper v2 &nbsp;<span style="font-weight:300;color:#666;font-size:.85em">local dev tool · 127.0.0.1:8001</span></h1>

<div id="list-view"><p style="color:#666;padding:8px">Betöltés…</p></div>
<div id="annotate-view">
  <button id="back-btn" onclick="showList()">← Vissza a listához</button>
  <div class="two-col">
    <div id="player-panel">
      <video id="player" controls preload="metadata"></video>
      <div class="player-controls">
        <button onclick="seek(-5)">-5s</button>
        <button onclick="stepFrame(-1)">◁</button>
        <button onclick="stepFrame(1)">▷</button>
        <button onclick="seek(5)">+5s</button>
        <button id="speed-btn" onclick="toggleSpeed()">0.5×</button>
        <span id="time-display">0.000 / 0.000</span>
      </div>
      <div style="margin-top:12px">
        <button onclick="addContactAtCurrentTime()" style="background:#1a2a3a;border:1px solid #336;color:#88aaff;padding:6px 14px;border-radius:5px;cursor:pointer;font-size:.83em">
          📍 Érintés rögzítése az aktuális időponthoz
        </button>
      </div>
    </div>
    <div id="form-panel">
      <div class="form-section" id="privacy-section">
        <h3>Privacy &amp; Consent <span class="req">*</span></h3>
        <div id="privacy-warning-box"></div>
        <div class="field-row"><label>Saját felvétel (ownership_confirmed) <span class="req">*</span></label>
          <div class="bool-group">
            <label><input type="radio" name="ownership_confirmed" value="true"> Igen</label>
            <label><input type="radio" name="ownership_confirmed" value="false"> Nem</label>
            <label><input type="radio" name="ownership_confirmed" value="null" checked> —</label>
          </div></div>
        <div class="field-row"><label>Hozzájárulás (consent_confirmed) <span class="req">*</span></label>
          <div class="bool-group">
            <label><input type="radio" name="consent_confirmed" value="true"> Igen</label>
            <label><input type="radio" name="consent_confirmed" value="false"> Nem</label>
            <label><input type="radio" name="consent_confirmed" value="null" checked> —</label>
          </div></div>
        <div class="field-row"><label>Felismerhető 3. személy? <span class="req">*</span></label>
          <div class="bool-group">
            <label><input type="radio" name="recognizable_third_party_present" value="true"> Igen</label>
            <label><input type="radio" name="recognizable_third_party_present" value="false"> Nem</label>
            <label><input type="radio" name="recognizable_third_party_present" value="null" checked> —</label>
          </div></div>
        <div class="field-row"><label>Kiskorú látható? <span class="req">*</span></label>
          <div class="bool-group">
            <label><input type="radio" name="minor_present" value="true"> Igen</label>
            <label><input type="radio" name="minor_present" value="false"> Nem</label>
            <label><input type="radio" name="minor_present" value="null" checked> —</label>
          </div></div>
        <div class="field-row"><label>Külső forrás? <span class="req">*</span></label>
          <div class="bool-group">
            <label><input type="radio" name="external_source" value="true"> Igen</label>
            <label><input type="radio" name="external_source" value="false"> Nem</label>
            <label><input type="radio" name="external_source" value="null" checked> —</label>
          </div></div>
        <div class="field-row"><label>Privacy review státusz</label>
          <select id="privacy_review_status">
            <option value="">— pending —</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
          </select></div>
      </div>

      <div class="form-section">
        <h3>Juggling Annotation <span class="req">*</span></h3>
        <div class="field-row"><label>Érintésszám <span class="req">*</span></label>
          <input type="number" id="total_juggling_count" min="0" placeholder="0"></div>
        <div class="field-row"><label>Számolási biztonság <span class="req">*</span></label>
          <select id="count_confidence">
            <option value="">—</option>
            <option value="high">high (±0)</option>
            <option value="medium">medium (±1)</option>
            <option value="low">low (±2+)</option>
          </select></div>
        <div class="field-row"><label>Domináns testrész <span class="req">*</span></label>
          <select id="dominant_body_part">
            <option value="">—</option>
            <option value="foot">lábfej (foot)</option>
            <option value="knee">térd (knee)</option>
            <option value="hip">csípő (hip)</option>
            <option value="chest">mellkas (chest)</option>
            <option value="shoulder">váll (shoulder)</option>
            <option value="head">fej (head)</option>
            <option value="back">hát (back)</option>
            <option value="mixed">vegyes (mixed)</option>
          </select></div>
        <div class="field-row"><label>Testrészek (body_parts_used) <span class="req">*</span></label>
          <div class="checkbox-group">
            <label><input type="checkbox" id="bpu_foot"> lábfej</label>
            <label><input type="checkbox" id="bpu_knee"> térd</label>
            <label><input type="checkbox" id="bpu_hip"> csípő</label>
            <label><input type="checkbox" id="bpu_chest"> mellkas</label>
            <label><input type="checkbox" id="bpu_shoulder"> váll</label>
            <label><input type="checkbox" id="bpu_head"> fej</label>
            <label><input type="checkbox" id="bpu_back"> hát</label>
          </div></div>
        <div class="field-row"><label>Nehézség <span class="req">*</span></label>
          <div class="radio-group">
            <label><input type="radio" name="difficulty" value="easy"> easy</label>
            <label><input type="radio" name="difficulty" value="medium"> medium</label>
            <label><input type="radio" name="difficulty" value="hard"> hard</label>
          </div></div>
        <div class="field-row"><label>Labda láthatóság <span class="req">*</span></label>
          <select id="ball_visible_quality">
            <option value="">—</option>
            <option value="excellent">excellent</option>
            <option value="good">good</option>
            <option value="partial">partial</option>
            <option value="poor">poor</option>
          </select></div>
        <div class="field-row"><label>Fényviszony <span class="req">*</span></label>
          <select id="lighting_quality">
            <option value="">—</option>
            <option value="excellent">excellent</option>
            <option value="good">good</option>
            <option value="variable">variable</option>
            <option value="poor">poor</option>
          </select></div>
        <div class="field-row"><label>Kamera stabilitás <span class="req">*</span></label>
          <select id="camera_stability">
            <option value="">—</option>
            <option value="tripod">tripod</option>
            <option value="handheld_stable">handheld stable</option>
            <option value="handheld_moving">handheld moving</option>
            <option value="tracking">tracking</option>
          </select></div>
        <div class="field-row"><label>Több személy? <span class="req">*</span></label>
          <div class="bool-group">
            <label><input type="radio" name="multi_person_present" value="true"> Igen</label>
            <label><input type="radio" name="multi_person_present" value="false"> Nem</label>
            <label><input type="radio" name="multi_person_present" value="null" checked> —</label>
          </div></div>
        <div class="field-row"><label>Több labda? <span class="req">*</span></label>
          <div class="bool-group">
            <label><input type="radio" name="multiple_balls_present" value="true"> Igen</label>
            <label><input type="radio" name="multiple_balls_present" value="false"> Nem</label>
            <label><input type="radio" name="multiple_balls_present" value="null" checked> —</label>
          </div></div>
        <div class="field-row"><label>Érvényesség <span class="req">*</span></label>
          <div class="radio-group">
            <label><input type="radio" name="expected_validity" value="valid"> valid</label>
            <label><input type="radio" name="expected_validity" value="borderline"> borderline</label>
            <label><input type="radio" name="expected_validity" value="invalid"> invalid</label>
          </div></div>
        <div class="field-row" id="invalidity_reason_row" style="display:none">
          <label>Érvénytelenség oka</label>
          <input type="text" id="invalidity_reason" placeholder="reason..."></div>
        <div class="field-row"><label>Megjegyzés</label>
          <textarea id="notes" placeholder="Opcionális megjegyzés..."></textarea></div>
        <div class="field-row"><label>Annotátor <span class="req">*</span></label>
          <input type="text" id="annotator" placeholder="pl. ZL" style="width:120px"></div>
        <div class="field-row"><label>Annotáció dátuma <span class="req">*</span></label>
          <input type="text" id="annotation_date" placeholder="2026-06-13" style="width:130px"></div>
      </div>

      <div class="form-section">
        <h3>Contact Events (opcionális)</h3>
        <div id="contact-events-list"><p class="ce-note">Betöltés…</p></div>
        <p class="ce-note">Kattints az "Érintés rögzítése" gombra a lejátszó alatt.</p>
      </div>

      <div style="display:flex;align-items:center">
        <button id="save-btn" onclick="saveAnnotation()">💾 Mentés</button>
        <span id="unsaved-indicator">● nem mentett változás</span>
      </div>
      <div id="save-status"></div>
    </div>
  </div>
</div>

<script>
let currentVideoId = null;
let currentAnnotation = null;
let currentFps = 30;
let contactEvents = [];
let isDirty = false;
let taxonomy = null;

// ── Taxonomy load ──────────────────────────────────────────────────────────

async function loadTaxonomy() {
  const resp = await fetch('/api/taxonomy');
  taxonomy = await resp.json();
}

function derivedSide(contactType) {
  if (!taxonomy) return null;
  if (taxonomy.side_policy.right_prefix_keys.includes(contactType)) return 'right';
  if (taxonomy.side_policy.left_prefix_keys.includes(contactType))  return 'left';
  if (taxonomy.side_policy.center_keys.includes(contactType))        return 'center';
  return null; // custom_other: explicit required
}

function buildContactTypePicker(currentValue, eventId) {
  let html = `<select class="ce-type-select" onchange="onContactTypeChange(${eventId},this.value)">`;
  taxonomy.groups.forEach(g => {
    html += `<optgroup label="${g.group_label_hu}">`;
    g.contact_types.forEach(ct => {
      const sel = ct.key === currentValue ? 'selected' : '';
      html += `<option value="${ct.key}" ${sel}>${ct.label_hu}</option>`;
    });
    html += '</optgroup>';
  });
  html += '</select>';
  return html;
}

function onContactTypeChange(eventId, newType) {
  const evt = contactEvents.find(e => e.event_id === eventId);
  if (!evt) return;
  evt.contact_type = newType;
  const side = derivedSide(newType);
  if (side) {
    evt.side = side;
  } else if (newType === 'custom_other') {
    evt.side = evt.side && ['left','right','center','unknown'].includes(evt.side) ? evt.side : null;
    evt.custom_label       = evt.custom_label       || '';
    evt.custom_description = evt.custom_description || '';
    evt.excluded_from_training = true;
    evt.review_status = 'pending_taxonomy_review';
  }
  markDirty();
  renderContactEvents();
}

// ── List view ──────────────────────────────────────────────────────────────

async function init() {
  await loadTaxonomy();
  loadList();
}

async function loadList() {
  const resp = await fetch('/api/manifest');
  const manifest = await resp.json();
  const container = document.getElementById('list-view');
  container.innerHTML = '';
  manifest.videos.forEach(v => {
    const card = document.createElement('div');
    card.className = 'video-card';
    const annBadge  = v.annotation_status    || 'metadata_ready';
    const privBadge = v.privacy_review_status || 'pending';
    card.innerHTML = `
      <h3>${v.video_id}</h3>
      <p>Duration: ${v.duration_seconds}s</p>
      <p>Orientation: ${v.display_orientation || '—'}</p>
      <p>Resolution: ${v.processed_resolution || v.original_resolution}</p>
      <span class="badge badge-${annBadge}">${annBadge}</span>
      <span class="badge badge-privacy-${privBadge === 'approved' ? 'approved' : 'pending'}"
        style="margin-left:4px">privacy: ${privBadge}</span>
    `;
    card.onclick = () => openAnnotation(v.video_id, v.filename, v.fps || 30);
    container.appendChild(card);
  });
}

// ── Annotation view ────────────────────────────────────────────────────────

function showList() {
  if (isDirty && !confirm('Nem mentett változások elvesznek. Folytatja?')) return;
  isDirty = false;
  document.getElementById('list-view').style.display    = 'flex';
  document.getElementById('annotate-view').style.display = 'none';
  loadList();
}

async function openAnnotation(videoId, filename, fps) {
  currentVideoId = videoId;
  currentFps     = fps || 30;
  document.getElementById('list-view').style.display    = 'none';
  document.getElementById('annotate-view').style.display = 'block';
  const player = document.getElementById('player');
  player.src         = '/media/' + encodeURIComponent(filename);
  player.playbackRate = 0.5;
  document.getElementById('speed-btn').textContent = '0.5×';
  player.ontimeupdate = updateTimeDisplay;
  const resp = await fetch('/api/annotation/' + videoId);
  currentAnnotation = await resp.json();
  populateForm(currentAnnotation);
  isDirty = false;
  document.getElementById('unsaved-indicator').style.display = 'none';
  document.getElementById('save-status').style.display       = 'none';
}

// ── Video controls ─────────────────────────────────────────────────────────

function seek(delta) {
  const p = document.getElementById('player');
  p.currentTime = Math.max(0, Math.min(p.duration || 0, p.currentTime + delta));
}

function stepFrame(dir) {
  const p = document.getElementById('player');
  p.currentTime = Math.max(0, Math.min(p.duration || 0, p.currentTime + dir / currentFps));
}

function toggleSpeed() {
  const p = document.getElementById('player');
  const btn = document.getElementById('speed-btn');
  if (p.playbackRate === 0.5) { p.playbackRate = 1;   btn.textContent = '1×'; }
  else                         { p.playbackRate = 0.5; btn.textContent = '0.5×'; }
}

function updateTimeDisplay() {
  const p   = document.getElementById('player');
  const fmt = t => isNaN(t) ? '0.000' : t.toFixed(3);
  document.getElementById('time-display').textContent = fmt(p.currentTime) + ' / ' + fmt(p.duration);
}

// ── Form populate ──────────────────────────────────────────────────────────

function radVal(name, val) {
  const els = document.querySelectorAll(`input[name="${name}"]`);
  const strVal = val === null ? 'null' : String(val);
  els.forEach(el => { el.checked = el.value === strVal; });
}

function populateForm(ann) {
  radVal('ownership_confirmed', ann.ownership_confirmed);
  radVal('consent_confirmed', ann.consent_confirmed);
  radVal('recognizable_third_party_present', ann.recognizable_third_party_present);
  radVal('minor_present', ann.minor_present);
  radVal('external_source', ann.external_source);
  setSelect('privacy_review_status', ann.privacy_review_status || '');
  updatePrivacyWarning(ann);

  setNum('total_juggling_count', ann.total_juggling_count);
  setSelect('count_confidence', ann.count_confidence || '');
  setSelect('dominant_body_part', ann.dominant_body_part || '');
  const bpu = ann.body_parts_used || {};
  ['foot','knee','hip','chest','shoulder','head','back'].forEach(p => {
    const el = document.getElementById('bpu_' + p);
    if (el) el.checked = bpu[p] === true;
  });
  radVal('difficulty', ann.difficulty);
  setSelect('ball_visible_quality',  ann.ball_visible_quality  || '');
  setSelect('lighting_quality',      ann.lighting_quality      || '');
  setSelect('camera_stability',      ann.camera_stability      || '');
  radVal('multi_person_present',    ann.multi_person_present);
  radVal('multiple_balls_present',  ann.multiple_balls_present);
  radVal('expected_validity',       ann.expected_validity);
  setText('invalidity_reason', ann.invalidity_reason || '');
  toggleInvalidityRow(ann.expected_validity);
  setText('notes',            ann.notes            || '');
  setText('annotator',        ann.annotator        || '');
  setText('annotation_date',  ann.annotation_date  || new Date().toISOString().slice(0,10));

  contactEvents = (ann.contact_events || []).map(e => ({...e}));
  renderContactEvents();

  document.querySelectorAll('#form-panel input, #form-panel select, #form-panel textarea').forEach(el => {
    el.addEventListener('change', markDirty);
    el.addEventListener('input',  markDirty);
  });
}

function setSelect(id, val) { const el = document.getElementById(id); if (el) el.value = val || ''; }
function setNum(id, val) { const el = document.getElementById(id); if (el) el.value = val !== null && val !== undefined ? val : ''; }
function setText(id, val) { const el = document.getElementById(id); if (el) el.value = val || ''; }
function markDirty() { isDirty = true; document.getElementById('unsaved-indicator').style.display = 'inline'; }

function toggleInvalidityRow(val) {
  document.getElementById('invalidity_reason_row').style.display =
    (val === 'invalid' || val === 'borderline') ? 'flex' : 'none';
}
document.querySelectorAll('input[name="expected_validity"]').forEach(el => {
  el.addEventListener('change', () => toggleInvalidityRow(el.value));
});

function updatePrivacyWarning(ann) {
  const box = document.getElementById('privacy-warning-box');
  const approved = ann.ownership_confirmed === true &&
    ann.consent_confirmed === true &&
    ann.recognizable_third_party_present === false &&
    ann.minor_present === false &&
    ann.external_source === false;
  box.innerHTML = approved
    ? '<div style="color:#44dd44;font-size:.82em;margin-bottom:8px">✓ Privacy feltételek teljesültek.</div>'
    : '<div class="privacy-warning">⚠ Privacy review még nem jóváhagyott. Töltsd ki az összes privacy mezőt!</div>';
}

// ── Contact events ─────────────────────────────────────────────────────────

function addContactAtCurrentTime() {
  if (!taxonomy) { alert('Taxonomy nem töltött be.'); return; }
  const p   = document.getElementById('player');
  const ts  = Math.round((p.currentTime || 0) * 1000);
  const nextId = contactEvents.length ? Math.max(...contactEvents.map(e => e.event_id)) + 1 : 1;
  const defaultType = taxonomy.stable_keys[0] || 'right_instep';
  contactEvents.push({
    event_id: nextId, timestamp_ms: ts,
    contact_type: defaultType,
    side: derivedSide(defaultType),
    annotation_confidence: 'probable',
    annotation_source: 'manual_annotator',
    review_status: 'pending',
    excluded_from_training: true,
    excluded_from_count: false,
    promotion_candidate: false,
    custom_label: null, custom_description: null,
    taxonomy_version: 'v1', note: null,
  });
  renderContactEvents();
  markDirty();
}

function deleteContactEvent(id) {
  contactEvents = contactEvents.filter(e => e.event_id !== id);
  renderContactEvents();
  markDirty();
}

function renderContactEvents() {
  const container = document.getElementById('contact-events-list');
  if (!taxonomy) { container.innerHTML = '<p class="ce-note">Taxonomy betöltés…</p>'; return; }
  container.innerHTML = '';
  if (contactEvents.length === 0) {
    container.innerHTML = '<p class="ce-note">Még nincs rögzített érintési esemény.</p>';
    return;
  }
  contactEvents.forEach(evt => {
    const isCustom = evt.contact_type === 'custom_other';
    const isLegacy = !evt.contact_type && evt.body_part;
    const ts = (evt.timestamp_ms / 1000).toFixed(3) + 's';
    const side = evt.side || derivedSide(evt.contact_type) || '—';

    const div = document.createElement('div');
    div.className = 'contact-event' + (isLegacy ? ' legacy-event' : '');

    if (isLegacy) {
      div.innerHTML = `
        <div class="ce-row">
          <span class="ce-ts">${ts}</span>
          <span class="legacy-badge">⚠ Legacy v1: body_part=${evt.body_part}</span>
          <span class="legacy-warn">Kézi újracímkézés szükséges!</span>
          <button class="ce-del-btn" onclick="deleteContactEvent(${evt.event_id})">✕</button>
        </div>
        <div class="ce-row">
          Új contact_type: ${buildContactTypePicker('', evt.event_id)}
        </div>`;
    } else {
      let html = `<div class="ce-row">
        <span class="ce-ts">${ts}</span>
        ${buildContactTypePicker(evt.contact_type, evt.event_id)}`;

      if (isCustom) {
        html += `<select class="ce-side-select" onchange="updateContactField(${evt.event_id},'side',this.value)">
          ${['left','right','center','unknown'].map(s =>
            `<option value="${s}" ${evt.side===s?'selected':''}>${s}</option>`).join('')}
        </select>`;
      } else {
        html += `<span class="ce-side-derived">▸ ${side}</span>`;
      }

      html += `<select class="ce-conf-select" onchange="updateContactField(${evt.event_id},'annotation_confidence',this.value)">
        ${['certain','probable','uncertain'].map(c =>
          `<option value="${c}" ${evt.annotation_confidence===c?'selected':''}>${c}</option>`).join('')}
      </select>
      <button class="ce-del-btn" onclick="deleteContactEvent(${evt.event_id})">✕</button>
      </div>`;

      if (isCustom) {
        html += `<div class="custom-row">
          <span class="custom-warn">⚠ Trainingből kizárva — taxonomy review szükséges</span>
        </div>
        <div class="custom-row">
          <input class="custom-label-inp" type="text" maxlength="40" placeholder="custom_label *"
            value="${evt.custom_label||''}"
            oninput="updateContactField(${evt.event_id},'custom_label',this.value)">
          <input class="custom-desc-inp" type="text" maxlength="200" placeholder="custom_description *"
            value="${evt.custom_description||''}"
            oninput="updateContactField(${evt.event_id},'custom_description',this.value)">
        </div>`;
      }

      div.innerHTML = html;
    }
    container.appendChild(div);
  });
}

function updateContactField(id, field, val) {
  const evt = contactEvents.find(e => e.event_id === id);
  if (evt) { evt[field] = val; markDirty(); }
}

// ── Save ───────────────────────────────────────────────────────────────────

function getRadioVal(name) {
  const el = document.querySelector(`input[name="${name}"]:checked`);
  if (!el) return null;
  if (el.value === 'null')  return null;
  if (el.value === 'true')  return true;
  if (el.value === 'false') return false;
  return el.value;
}

async function saveAnnotation() {
  const bpu = {};
  ['foot','knee','hip','chest','shoulder','head','back'].forEach(p => {
    const el = document.getElementById('bpu_' + p);
    bpu[p] = el ? el.checked : false;
  });
  const countRaw = document.getElementById('total_juggling_count').value;

  const eventsPayload = contactEvents.length
    ? contactEvents.map(evt => {
        const isCustom = evt.contact_type === 'custom_other';
        return {
          event_id:             evt.event_id,
          timestamp_ms:         evt.timestamp_ms,
          contact_type:         evt.contact_type,
          side:                 evt.side || derivedSide(evt.contact_type),
          annotation_confidence: evt.annotation_confidence,
          annotation_source:    evt.annotation_source || 'manual_annotator',
          review_status:        evt.review_status || (isCustom ? 'pending_taxonomy_review' : 'pending'),
          excluded_from_training: isCustom ? true : (evt.excluded_from_training || false),
          excluded_from_count:  evt.excluded_from_count  || false,
          promotion_candidate:  evt.promotion_candidate  || false,
          custom_label:         isCustom ? (evt.custom_label        || null) : null,
          custom_description:   isCustom ? (evt.custom_description  || null) : null,
          taxonomy_version:     'v1',
          note:                 evt.note || null,
        };
      })
    : null;

  const body = {
    ownership_confirmed:              getRadioVal('ownership_confirmed'),
    consent_confirmed:                getRadioVal('consent_confirmed'),
    recognizable_third_party_present: getRadioVal('recognizable_third_party_present'),
    minor_present:                    getRadioVal('minor_present'),
    external_source:                  getRadioVal('external_source'),
    privacy_review_status:            document.getElementById('privacy_review_status').value || null,
    total_juggling_count:             countRaw !== '' ? parseInt(countRaw, 10) : null,
    count_confidence:                 document.getElementById('count_confidence').value || null,
    dominant_body_part:               document.getElementById('dominant_body_part').value || null,
    body_parts_used: bpu,
    difficulty:                       getRadioVal('difficulty'),
    ball_visible_quality:             document.getElementById('ball_visible_quality').value  || null,
    lighting_quality:                 document.getElementById('lighting_quality').value      || null,
    camera_stability:                 document.getElementById('camera_stability').value      || null,
    multi_person_present:             getRadioVal('multi_person_present'),
    multiple_balls_present:           getRadioVal('multiple_balls_present'),
    expected_validity:                getRadioVal('expected_validity'),
    invalidity_reason:                document.getElementById('invalidity_reason').value || null,
    notes:                            document.getElementById('notes').value             || null,
    annotator:                        document.getElementById('annotator').value          || null,
    annotation_date:                  document.getElementById('annotation_date').value   || null,
    contact_events: eventsPayload,
  };

  const statusEl = document.getElementById('save-status');
  statusEl.style.display = 'none';
  const resp   = await fetch('/api/annotation/' + currentVideoId, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const result = await resp.json();
  statusEl.style.display = 'block';
  if (resp.ok) {
    statusEl.className = 'status-ok';
    statusEl.textContent = `✅ Mentve — ann: ${result.annotation_status}, privacy: ${result.privacy_review_status}, eligibility: ${result.dataset_eligibility}`;
    isDirty = false;
    document.getElementById('unsaved-indicator').style.display = 'none';
    currentAnnotation = {...currentAnnotation, ...body, annotation_status: result.annotation_status};
    updatePrivacyWarning(currentAnnotation);
  } else {
    statusEl.className = 'status-err';
    statusEl.textContent = `❌ Hiba: ${result.detail || JSON.stringify(result)}`;
  }
}

window.addEventListener('beforeunload', e => {
  if (isDirty) { e.preventDefault(); e.returnValue = ''; }
});

init();
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    assert HOST == "127.0.0.1", "SECURITY: annotation helper must only bind to localhost"
    print(f"Annotation helper v2 running on http://{HOST}:{PORT}")
    print(f"Taxonomy: {TAXONOMY_PATH.name} ({len(_ALL_CONTACT_TYPES)} contact types)")
    print("Press Ctrl+C to stop.")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
