#!/usr/bin/env python3
"""
05_split_dataset.py — Smart Snap POC-1

Assigns each positive frame to "tuning" or "holdout" using a stratified
(by category, then by video) split.  No-ball (Type C) frames are split
independently.

Rules:
  - HOLDOUT_RATIO (30%) of each category goes to holdout.
  - Split is deterministic (SPLIT_SEED).
  - Thresholds are tuned only on the tuning set.
  - Final verdict is based only on holdout results.
  - Updates manifest.json in-place with a "split" field per frame.

No DB access.  Read-only on ground_truth.json.

Usage:
    python scripts/smart_snap_poc1/05_split_dataset.py
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.smart_snap_poc1.config import (
    HOLDOUT_RATIO,
    MANIFEST_PATH,
    SPLIT_SEED,
)


def run() -> dict:
    if not os.path.isfile(MANIFEST_PATH):
        print(f"ERROR: manifest.json not found at {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        manifest = json.load(fh)

    frames: list[dict] = manifest["frames"]
    rng = random.Random(SPLIT_SEED)

    # ── Group positive frames by category ─────────────────────────────────
    positive = [f for f in frames if not f.get("is_no_ball")]
    no_ball = [f for f in frames if f.get("is_no_ball")]

    by_category: dict[str, list[dict]] = {}
    for f in positive:
        cat = f.get("category") or "unassigned"
        by_category.setdefault(cat, []).append(f)

    # ── Assign splits ──────────────────────────────────────────────────────
    tuning_count = 0
    holdout_count = 0

    for cat, cat_frames in by_category.items():
        shuffled = list(cat_frames)
        rng.shuffle(shuffled)
        n_holdout = max(1, round(len(shuffled) * HOLDOUT_RATIO))
        for i, f in enumerate(shuffled):
            f["split"] = "holdout" if i < n_holdout else "tuning"
        holdout_count += n_holdout
        tuning_count += len(shuffled) - n_holdout

    # ── No-ball frames: same 30/70 split ──────────────────────────────────
    no_ball_shuffled = list(no_ball)
    rng.shuffle(no_ball_shuffled)
    n_nb_holdout = max(1, round(len(no_ball_shuffled) * HOLDOUT_RATIO))
    for i, f in enumerate(no_ball_shuffled):
        f["split"] = "holdout" if i < n_nb_holdout else "tuning"

    # ── Verify no overlap ─────────────────────────────────────────────────
    tuning_ids = {f["frame_id"] for f in frames if f.get("split") == "tuning"}
    holdout_ids = {f["frame_id"] for f in frames if f.get("split") == "holdout"}
    assert not (tuning_ids & holdout_ids), "Split overlap detected!"

    # ── Update manifest ────────────────────────────────────────────────────
    split_summary = {
        "tuning_positive": sum(1 for f in positive if f.get("split") == "tuning"),
        "holdout_positive": sum(1 for f in positive if f.get("split") == "holdout"),
        "tuning_no_ball": sum(1 for f in no_ball if f.get("split") == "tuning"),
        "holdout_no_ball": sum(1 for f in no_ball if f.get("split") == "holdout"),
        "holdout_ratio": HOLDOUT_RATIO,
        "split_seed": SPLIT_SEED,
    }
    manifest["split_summary"] = split_summary

    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)

    print("Dataset split complete:")
    print(f"  Tuning   — positive: {split_summary['tuning_positive']}, "
          f"no-ball: {split_summary['tuning_no_ball']}")
    print(f"  Holdout  — positive: {split_summary['holdout_positive']}, "
          f"no-ball: {split_summary['holdout_no_ball']}")
    print(f"  Holdout ratio: {HOLDOUT_RATIO:.0%}")
    print()
    print("  ⚠ Tune thresholds on TUNING set only.")
    print("  ⚠ Final verdict based on HOLDOUT results only.")

    return manifest


if __name__ == "__main__":
    run()
