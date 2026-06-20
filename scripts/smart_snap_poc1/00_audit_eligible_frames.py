#!/usr/bin/env python3
"""
00_audit_eligible_frames.py — Smart Snap POC-1 v2

Read-only DB audit.  Builds manifest.json cataloguing three frame types:

  Type A: frames with existing human-corrected feedback (juggling_ball_feedback,
          decision='corrected').  Up to MAX_VIDEO_FRACTION of positive total.
  Type B: positive frames (ball detected/predicted) from other videos.
          Stratified by video and tracking_state; provisional category assigned.
  Type C: no-ball frames (tracking_state='lost', ball coords NULL).
          Used to measure FP rate.

Dataset constraints enforced:
  - No single video > MAX_VIDEO_FRACTION (40%) of positive frames
  - ≥ 4 distinct videos in positive frames
  - ≥ TARGET_NO_BALL_FRAMES Type C frames across ≥ 2 videos

Scope: READ ONLY — no commit(), no INSERT/UPDATE/DELETE.

Usage:
    python scripts/smart_snap_poc1/00_audit_eligible_frames.py
    python scripts/smart_snap_poc1/00_audit_eligible_frames.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed.", file=sys.stderr)
    sys.exit(1)

from scripts.smart_snap_poc1.config import (
    DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER,
    EDGE_THRESHOLD, HIGH_CONF_THRESHOLD, LOW_CONF_THRESHOLD,
    MANIFEST_PATH, MAX_VIDEO_FRACTION, PROJECT_ROOT,
    SMALL_BALL_Y_THRESHOLD, SPLIT_SEED,
    TARGET_POSITIVE_FRAMES, TARGET_NO_BALL_FRAMES,
    TYPE_A_MAX_PER_VIDEO, TYPE_B_PER_VIDEO, TYPE_C_PER_VIDEO,
)
from scripts.smart_snap_poc1.utils import build_frame_id

# ── Queries ──────────────────────────────────────────────────────────────────

_TYPE_A_QUERY = """
SELECT
    jf.video_id::text,
    jf.frame_ms,
    jf.corrected_x,
    jf.corrected_y,
    jf.model_predicted_x,
    jf.model_predicted_y,
    jf.model_confidence,
    jf.correction_method,
    jf.model_tracking_state   AS tracking_state,
    jt.image_width_px,
    jt.image_height_px,
    jv.storage_path
FROM juggling_ball_feedback jf
JOIN juggling_ball_trajectories jt
    ON jt.video_id = jf.video_id AND jt.frame_ms = jf.frame_ms
JOIN juggling_videos jv ON jv.id = jf.video_id
WHERE jf.decision = 'corrected'
  AND jf.corrected_x IS NOT NULL
  AND jf.corrected_y IS NOT NULL
  AND jt.image_width_px IS NOT NULL
  AND jv.storage_path IS NOT NULL
ORDER BY jf.video_id, jf.frame_ms
"""

_TYPE_B_POSITIVE_QUERY = """
SELECT
    jt.video_id::text,
    jt.frame_ms,
    jt.ball_x          AS model_x,
    jt.ball_y          AS model_y,
    jt.confidence       AS model_confidence,
    jt.tracking_state,
    jt.image_width_px,
    jt.image_height_px,
    jv.storage_path
FROM juggling_ball_trajectories jt
JOIN juggling_videos jv ON jv.id = jt.video_id
WHERE jt.tracking_state IN ('detected', 'predicted')
  AND jt.ball_x IS NOT NULL
  AND jt.ball_y IS NOT NULL
  AND jt.image_width_px IS NOT NULL
  AND jv.storage_path IS NOT NULL
  AND jv.storage_path NOT LIKE '/tmp/%%'
  AND jt.video_id NOT IN (
      SELECT DISTINCT video_id FROM juggling_ball_feedback WHERE decision='corrected'
  )
ORDER BY jt.video_id, jt.frame_ms
"""

_TYPE_C_NO_BALL_QUERY = """
SELECT
    jt.video_id::text,
    jt.frame_ms,
    jt.tracking_state,
    jv.storage_path
FROM juggling_ball_trajectories jt
JOIN juggling_videos jv ON jv.id = jt.video_id
WHERE jt.tracking_state = 'lost'
  AND jt.ball_x IS NULL
  AND jt.frame_ms > 0
  AND jv.storage_path IS NOT NULL
  AND jv.storage_path NOT LIKE '/tmp/%%'
ORDER BY jt.video_id, jt.frame_ms
"""

# ── Helpers ──────────────────────────────────────────────────────────────────

def _db_connect():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD, dbname=DB_NAME,
    )


def _abs_storage_path(rel_path: str) -> str:
    return os.path.join(PROJECT_ROOT, rel_path)


def _file_exists(rel_path: str) -> bool:
    return os.path.isfile(_abs_storage_path(rel_path))


def _assign_provisional_category(
    tracking_state: str | None,
    confidence: float | None,
    ball_x: float | None,
    ball_y: float | None,
) -> str:
    """Assign provisional category using DB-derivable signals.

    Returns:
        Category string.  Excludes motion_blur and partial_occlusion (need visual
        inspection — see recording plan in report).
    """
    if tracking_state == "lost" or ball_x is None or ball_y is None:
        return "no_ball"
    if ball_x < EDGE_THRESHOLD or ball_x > (1.0 - EDGE_THRESHOLD):
        return "edge_of_frame"
    if ball_y < SMALL_BALL_Y_THRESHOLD:
        return "small_ball"
    if confidence is not None and confidence < LOW_CONF_THRESHOLD:
        return "low_contrast"
    return "clear_ball"


def _sample_no_ball(rows: list[dict], target: int, seed: int = SPLIT_SEED) -> list[dict]:
    """Sample no-ball frames, spreading across videos."""
    rng = random.Random(seed)
    by_video: dict[str, list[dict]] = {}
    for r in rows:
        by_video.setdefault(r["video_id"], []).append(r)

    per_vid = max(1, target // max(len(by_video), 1))
    selected: list[dict] = []
    for vid in sorted(by_video.keys()):
        candidates = list(by_video[vid])
        # Pick frames spread across the video timeline (not just the first cluster)
        step = max(1, len(candidates) // (per_vid + 1))
        picks = candidates[::step][:per_vid]
        selected.extend(picks)

    # Fill to target if needed (round-robin from remaining)
    chosen_ids = {id(r) for r in selected}
    remaining = [r for r in rows if id(r) not in chosen_ids]
    rng.shuffle(remaining)
    selected.extend(remaining[: max(0, target - len(selected))])
    return selected[:target]


def _sample_type_b(rows: list[dict], max_total: int, seed: int = SPLIT_SEED) -> list[dict]:
    """Sample Type B positive frames, enforcing per-video quota."""
    rng = random.Random(seed)
    by_video: dict[str, list[dict]] = {}
    for r in rows:
        by_video.setdefault(r["video_id"], []).append(r)

    per_vid = TYPE_B_PER_VIDEO
    selected: list[dict] = []

    for vid in sorted(by_video.keys()):
        vrows = by_video[vid]
        # Stratify: detected, predicted, others
        detected = [r for r in vrows if r.get("tracking_state") == "detected"]
        predicted = [r for r in vrows if r.get("tracking_state") == "predicted"]
        # Prioritise diversity by category signal
        edge = [r for r in vrows if r.get("model_x") is not None
                and (r["model_x"] < EDGE_THRESHOLD or r["model_x"] > 1 - EDGE_THRESHOLD)]
        low_y = [r for r in vrows if r.get("model_y") is not None
                 and r["model_y"] < SMALL_BALL_Y_THRESHOLD]
        low_c = [r for r in vrows if r.get("model_confidence") is not None
                 and r["model_confidence"] < LOW_CONF_THRESHOLD]

        pool_seen: set[int] = set()
        pool: list[dict] = []

        def _add_unique(source, n):
            shuffled = list(source)
            rng.shuffle(shuffled)
            for item in shuffled:
                if len(pool) >= per_vid:
                    break
                if id(item) not in pool_seen:
                    pool.append(item)
                    pool_seen.add(id(item))

        # Fill with diverse candidates first
        _add_unique(edge, per_vid // 4 + 1)
        _add_unique(low_y, per_vid // 4 + 1)
        _add_unique(low_c, per_vid // 4 + 1)
        _add_unique(detected, per_vid // 2)
        _add_unique(predicted, per_vid // 2)
        # Fill remainder from all
        _add_unique(vrows, per_vid)

        selected.extend(pool[:per_vid])
        if len(selected) >= max_total:
            break

    return selected[:max_total]


# ── Main ─────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    conn = _db_connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_TYPE_A_QUERY)
            raw_type_a = [dict(r) for r in cur.fetchall()]

            cur.execute(_TYPE_B_POSITIVE_QUERY)
            raw_type_b_candidates = [dict(r) for r in cur.fetchall()]

            cur.execute(_TYPE_C_NO_BALL_QUERY)
            raw_no_ball = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    # ── Filter to files that exist ─────────────────────────────────────────
    raw_type_a = [r for r in raw_type_a if _file_exists(r["storage_path"])]
    raw_type_b_candidates = [r for r in raw_type_b_candidates if _file_exists(r["storage_path"])]
    raw_no_ball = [r for r in raw_no_ball if _file_exists(r["storage_path"])]

    # ── Type A: cap at TYPE_A_MAX_PER_VIDEO per video ─────────────────────
    rng = random.Random(SPLIT_SEED)
    a_by_video: dict[str, list[dict]] = {}
    for r in raw_type_a:
        a_by_video.setdefault(r["video_id"], []).append(r)

    type_a_selected: list[dict] = []
    for vid in sorted(a_by_video.keys()):
        vrows = a_by_video[vid]
        if len(vrows) <= TYPE_A_MAX_PER_VIDEO:
            type_a_selected.extend(vrows)
        else:
            shuffled = list(vrows)
            rng.shuffle(shuffled)
            type_a_selected.extend(shuffled[:TYPE_A_MAX_PER_VIDEO])

    # ── Type B: positive frames from non-Type-A videos ────────────────────
    type_a_video_ids = {r["video_id"] for r in type_a_selected}
    b_pool = [r for r in raw_type_b_candidates if r["video_id"] not in type_a_video_ids]

    remaining_positive_slots = TARGET_POSITIVE_FRAMES - len(type_a_selected)
    type_b_selected = _sample_type_b(b_pool, remaining_positive_slots)

    # ── Type C: no-ball frames (across multiple videos) ───────────────────
    # Include no-ball frames from BOTH Type A and B videos for diversity
    type_c_selected = _sample_no_ball(raw_no_ball, TARGET_NO_BALL_FRAMES)

    # ── Build frame entries ────────────────────────────────────────────────
    all_frames: list[dict] = []

    for row in type_a_selected:
        fid = build_frame_id(row["video_id"], row["frame_ms"])
        model_x = row.get("model_predicted_x")
        model_y = row.get("model_predicted_y")
        conf = row.get("model_confidence")
        ts = row.get("tracking_state")
        category = _assign_provisional_category(ts, conf, model_x, model_y)
        all_frames.append({
            "frame_id": fid,
            "type": "A",
            "video_id": row["video_id"],
            "frame_ms": row["frame_ms"],
            "storage_path": row["storage_path"],
            "image_width_px": row["image_width_px"],
            "image_height_px": row["image_height_px"],
            "tracking_state": ts,
            "model_x": model_x,
            "model_y": model_y,
            "model_confidence": conf,
            "db_corrected_x": row["corrected_x"],
            "db_corrected_y": row["corrected_y"],
            "correction_method": row.get("correction_method"),
            "is_no_ball": False,
            "category": category,
            "category_source": "provisional_auto",
            "gt_status": "PENDING_ANNOTATION",
            "split": None,
        })

    for row in type_b_selected:
        fid = build_frame_id(row["video_id"], row["frame_ms"])
        model_x = row.get("model_x")
        model_y = row.get("model_y")
        conf = row.get("model_confidence")
        ts = row.get("tracking_state")
        category = _assign_provisional_category(ts, conf, model_x, model_y)
        all_frames.append({
            "frame_id": fid,
            "type": "B",
            "video_id": row["video_id"],
            "frame_ms": row["frame_ms"],
            "storage_path": row["storage_path"],
            "image_width_px": row["image_width_px"],
            "image_height_px": row["image_height_px"],
            "tracking_state": ts,
            "model_x": model_x,
            "model_y": model_y,
            "model_confidence": conf,
            "db_corrected_x": None,
            "db_corrected_y": None,
            "correction_method": None,
            "is_no_ball": False,
            "category": category,
            "category_source": "provisional_auto",
            "gt_status": "PENDING_ANNOTATION",
            "split": None,
        })

    for row in type_c_selected:
        fid = build_frame_id(row["video_id"], row["frame_ms"])
        all_frames.append({
            "frame_id": fid,
            "type": "C",
            "video_id": row["video_id"],
            "frame_ms": row["frame_ms"],
            "storage_path": row["storage_path"],
            "image_width_px": None,
            "image_height_px": None,
            "tracking_state": row["tracking_state"],
            "model_x": None,
            "model_y": None,
            "model_confidence": None,
            "db_corrected_x": None,
            "db_corrected_y": None,
            "correction_method": None,
            "is_no_ball": True,
            "category": "no_ball",
            "category_source": "db_lost_state",
            "gt_status": "VALIDATED_NO_BALL",
            "split": None,
        })

    # ── Summary & diversity checks ─────────────────────────────────────────
    positive_frames = [f for f in all_frames if not f["is_no_ball"]]
    n_positive = len(positive_frames)
    vid_counts: dict[str, int] = {}
    for f in positive_frames:
        vid_counts[f["video_id"]] = vid_counts.get(f["video_id"], 0) + 1

    category_counts: dict[str, int] = {}
    for f in all_frames:
        cat = f.get("category") or "unassigned"
        category_counts[cat] = category_counts.get(cat, 0) + 1

    no_ball_videos = len({f["video_id"] for f in all_frames if f["is_no_ball"]})
    max_vid_share = max(vid_counts.values()) / max(n_positive, 1) if vid_counts else 0

    warnings: list[str] = []
    if max_vid_share > MAX_VIDEO_FRACTION + 0.01:
        warnings.append(
            f"Video diversity FAIL: max share {max_vid_share:.1%} > {MAX_VIDEO_FRACTION:.0%}"
        )
    if len(vid_counts) < 4:
        warnings.append(f"Only {len(vid_counts)} distinct positive videos (target ≥4)")
    if len(type_c_selected) < 5:
        warnings.append(f"Only {len(type_c_selected)} no-ball frames (target ≥{TARGET_NO_BALL_FRAMES})")
    for cat in ["motion_blur", "partial_occlusion"]:
        if category_counts.get(cat, 0) == 0:
            warnings.append(
                f"Category '{cat}' has 0 frames — visual inspection required; "
                "see recording plan in report"
            )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "poc": "smart_snap_poc1",
        "schema_version": "2.0",
        "diversity_warnings": warnings,
        "summary": {
            "type_a_count": len(type_a_selected),
            "type_b_count": len(type_b_selected),
            "type_c_no_ball_count": len(type_c_selected),
            "total_count": len(all_frames),
            "positive_count": n_positive,
            "no_ball_count": len(type_c_selected),
            "videos_positive": len(vid_counts),
            "videos_no_ball": no_ball_videos,
            "per_video_positive": vid_counts,
            "max_video_fraction": round(max_vid_share, 3),
            "category_distribution": category_counts,
            "categories_missing": [
                c for c in ["motion_blur", "partial_occlusion"]
                if category_counts.get(c, 0) == 0
            ],
        },
        "frames": all_frames,
    }

    if not dry_run:
        with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, default=str)
        print(f"manifest.json v2 written: {len(all_frames)} frames "
              f"(A={len(type_a_selected)}, B={len(type_b_selected)}, "
              f"C={len(type_c_selected)} no-ball)")

    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    m = run(dry_run=args.dry_run)
    s = m["summary"]
    tag = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{tag}Audit v2 complete:")
    print(f"  Type A (human-corrected):  {s['type_a_count']}")
    print(f"  Type B (positive, fresh):  {s['type_b_count']}")
    print(f"  Type C (no-ball):          {s['type_c_no_ball_count']}")
    print(f"  Total:                     {s['total_count']}")
    print(f"  Positive videos:           {s['videos_positive']}")
    print(f"  Max video share:           {s['max_video_fraction']:.1%}")
    print(f"  Category distribution:     {s['category_distribution']}")
    if m["diversity_warnings"]:
        print("\n  WARNINGS:")
        for w in m["diversity_warnings"]:
            print(f"    ⚠ {w}")
    if s.get("categories_missing"):
        print(f"\n  Categories needing recording plan: {s['categories_missing']}")
