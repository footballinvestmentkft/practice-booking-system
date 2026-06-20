#!/usr/bin/env python3
"""
02b_seed_provisional_gt.py — Smart Snap POC-1

⚠ PROVISIONAL SIMULATION — NOT REAL HUMAN ANNOTATION ⚠

Generates a ground_truth.json for infrastructure testing by simulating:
  - Round 1 annotation (model overlay HIDDEN): DB corrected_x/y + Gaussian noise
  - Round 2 annotation (model overlay visible): DB corrected_x/y + smaller noise
  - GT final: (R1 + R2) / 2
  - Human raw tap: DB corrected_x/y + large Gaussian noise (3 reps)
  - Human loupe tap: DB corrected_x/y + small Gaussian noise (3 reps)

PURPOSE: Validate annotation infrastructure and benchmark pipeline mechanics.
         The simulated R1/R2 are DIFFERENT (different random seeds) to produce
         non-trivial inter-round agreement measurements, unlike the previous
         approach where R1=R2 exactly.

IMPORTANT: These coordinates are NOT real human annotations.
  - All entries carry  gt_provenance = "provisional_simulation"
  - The report MUST clearly separate these from real human annotation results.
  - Real human annotation requires a human to run 02_annotate_ground_truth.py.

For Type A frames (db_corrected_x/y available): noise is centred on the
human-corrected DB coordinate (best available reference).
For Type B positive frames (only model_x/y available): noise is centred on
model prediction. These carry larger expected error.
For Type C no-ball frames: gt_status = VALIDATED_NO_BALL, no coordinates.

No DB writes.  Reads manifest.json only.

Usage:
    python scripts/smart_snap_poc1/02b_seed_provisional_gt.py
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.smart_snap_poc1.config import (
    ANNOT_SIM_LOUPE_SIGMA,
    ANNOT_SIM_R1_SIGMA,
    ANNOT_SIM_R2_SIGMA,
    ANNOT_SIM_RAW_TAP_SIGMA,
    ANNOT_SIM_REPS,
    ANNOT_SIM_SEED_LOUPE,
    ANNOT_SIM_SEED_R1,
    ANNOT_SIM_SEED_R2,
    ANNOT_SIM_SEED_RAW,
    GROUND_TRUTH_PATH,
    GT_AGREEMENT_THRESHOLD_PX,
    MANIFEST_PATH,
)
from scripts.smart_snap_poc1.metrics import pixel_error

# ── Helpers ──────────────────────────────────────────────────────────────────

def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _noisy_coord(
    x: float,
    y: float,
    sigma: float,
    rng: random.Random,
) -> tuple[float, float]:
    return (
        _clamp01(x + rng.gauss(0, sigma)),
        _clamp01(y + rng.gauss(0, sigma)),
    )


def _agreement_px(
    x1: float, y1: float,
    x2: float, y2: float,
    img_w: int,
    img_h: int,
) -> float:
    return pixel_error(x1, y1, x2, y2, img_w, img_h)


# ── Main ─────────────────────────────────────────────────────────────────────

def run() -> None:
    if not os.path.isfile(MANIFEST_PATH):
        print(f"ERROR: manifest.json not found at {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        manifest = json.load(fh)

    rng_r1 = random.Random(ANNOT_SIM_SEED_R1)
    rng_r2 = random.Random(ANNOT_SIM_SEED_R2)
    rng_raw = random.Random(ANNOT_SIM_SEED_RAW)
    rng_loupe = random.Random(ANNOT_SIM_SEED_LOUPE)

    gt_frames: dict = {}
    stats = {
        "type_a": 0, "type_b": 0, "type_c": 0,
        "flagged_review": 0, "no_reference": 0,
    }

    for frame in manifest.get("frames", []):
        fid = frame["frame_id"]
        img_w = frame.get("image_width_px") or 640
        img_h = frame.get("image_height_px") or 480

        # ── Type C: no-ball ───────────────────────────────────────────────
        if frame.get("is_no_ball"):
            gt_frames[fid] = {
                "frame_id": fid,
                "type": frame["type"],
                "is_no_ball": True,
                "gt_final": None,
                "gt_provenance": "validated_no_ball_db_lost_state",
                "gt_agreement_px": None,
                "gt_review_required": False,
                "annotation_round_1": None,
                "annotation_round_2": None,
                "human_raw_tap": None,
                "human_loupe_tap": None,
                "db_reference": {"x": None, "y": None},
                "simulation_note": "No-ball frame. FP measured per algorithm.",
            }
            stats["type_c"] += 1
            continue

        # ── Positive frames: need a reference coordinate ──────────────────
        ref_x = frame.get("db_corrected_x")
        ref_y = frame.get("db_corrected_y")
        ref_source = "db_corrected"

        if ref_x is None or ref_y is None:
            ref_x = frame.get("model_x")
            ref_y = frame.get("model_y")
            ref_source = "model_predicted"

        if ref_x is None or ref_y is None:
            stats["no_reference"] += 1
            continue

        # ── Two-round annotation simulation ───────────────────────────────
        # R1: annotator clicks WITHOUT seeing model overlay → larger noise
        r1_x, r1_y = _noisy_coord(ref_x, ref_y, ANNOT_SIM_R1_SIGMA, rng_r1)
        # R2: annotator clicks WITH model overlay visible → smaller noise
        r2_x, r2_y = _noisy_coord(ref_x, ref_y, ANNOT_SIM_R2_SIGMA, rng_r2)

        agreement_px = _agreement_px(r1_x, r1_y, r2_x, r2_y, img_w, img_h)
        review_required = agreement_px > GT_AGREEMENT_THRESHOLD_PX

        gt_x = (r1_x + r2_x) / 2.0
        gt_y = (r1_y + r2_y) / 2.0

        # ── Human raw tap simulation (3 reps, larger noise) ───────────────
        raw_reps: list[dict] = []
        raw_errors: list[float] = []
        for i in range(ANNOT_SIM_REPS):
            rx, ry = _noisy_coord(ref_x, ref_y, ANNOT_SIM_RAW_TAP_SIGMA, rng_raw)
            err = _agreement_px(rx, ry, gt_x, gt_y, img_w, img_h)
            raw_reps.append({"rep": i + 1, "x": rx, "y": ry, "pixel_error_vs_gt": err})
            raw_errors.append(err)

        raw_mean = sum(raw_errors) / len(raw_errors)
        raw_median = sorted(raw_errors)[len(raw_errors) // 2]

        # ── Human loupe tap simulation (3 reps, smaller noise) ───────────
        loupe_reps: list[dict] = []
        loupe_errors: list[float] = []
        for i in range(ANNOT_SIM_REPS):
            lx, ly = _noisy_coord(ref_x, ref_y, ANNOT_SIM_LOUPE_SIGMA, rng_loupe)
            err = _agreement_px(lx, ly, gt_x, gt_y, img_w, img_h)
            loupe_reps.append({"rep": i + 1, "x": lx, "y": ly, "pixel_error_vs_gt": err})
            loupe_errors.append(err)

        loupe_mean = sum(loupe_errors) / len(loupe_errors)

        if review_required:
            stats["flagged_review"] += 1

        if frame["type"] == "A":
            stats["type_a"] += 1
        else:
            stats["type_b"] += 1

        gt_frames[fid] = {
            "frame_id": fid,
            "type": frame["type"],
            "is_no_ball": False,
            "gt_final": {"x": gt_x, "y": gt_y},
            "gt_provenance": "provisional_simulation",
            "gt_agreement_px": round(agreement_px, 2),
            "gt_review_required": review_required,
            "annotation_round_1": {"x": r1_x, "y": r1_y, "model_hidden": True},
            "annotation_round_2": {"x": r2_x, "y": r2_y, "model_hidden": False},
            "human_raw_tap": {
                "reps": raw_reps,
                "mean_error_vs_gt": round(raw_mean, 2),
                "median_error_vs_gt": round(raw_median, 2),
                "data_source": "SIMULATED",
                "x": sum(r["x"] for r in raw_reps) / len(raw_reps),
                "y": sum(r["y"] for r in raw_reps) / len(raw_reps),
            },
            "human_loupe_tap": {
                "reps": loupe_reps,
                "mean_error_vs_gt": round(loupe_mean, 2),
                "data_source": "SIMULATED",
                "x": sum(r["x"] for r in loupe_reps) / len(loupe_reps),
                "y": sum(r["y"] for r in loupe_reps) / len(loupe_reps),
            },
            "db_reference": {
                "x": ref_x if ref_source == "db_corrected" else None,
                "y": ref_y if ref_source == "db_corrected" else None,
                "source": ref_source,
            },
            "simulation_note": (
                "PROVISIONAL SIMULATION — coordinates are NOT real human annotations. "
                f"Ref source: {ref_source}. "
                "Run 02_annotate_ground_truth.py for real human annotation."
            ),
        }

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "poc": "smart_snap_poc1",
        "schema_version": "2.0",
        "annotation_warning": (
            "⚠ ALL ENTRIES IN THIS FILE ARE PROVISIONAL SIMULATION. "
            "GT coordinates are derived from DB corrections or model predictions "
            "plus Gaussian noise — NOT from a real human annotation session. "
            "Results based on this data cannot be used for production decisions. "
            "Real human annotation requires running 02_annotate_ground_truth.py."
        ),
        "simulation_params": {
            "r1_sigma": ANNOT_SIM_R1_SIGMA,
            "r2_sigma": ANNOT_SIM_R2_SIGMA,
            "raw_tap_sigma": ANNOT_SIM_RAW_TAP_SIGMA,
            "loupe_sigma": ANNOT_SIM_LOUPE_SIGMA,
            "reps": ANNOT_SIM_REPS,
            "gt_agreement_threshold_px": GT_AGREEMENT_THRESHOLD_PX,
        },
        "stats": {
            **stats,
            "total_gt_frames": stats["type_a"] + stats["type_b"],
            "flagged_review_fraction": (
                round(stats["flagged_review"] / max(stats["type_a"] + stats["type_b"], 1), 3)
            ),
        },
        "frames": gt_frames,
    }

    with open(GROUND_TRUTH_PATH, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, default=str)

    total_gt = stats["type_a"] + stats["type_b"]
    print(f"ground_truth.json v2 written (PROVISIONAL SIMULATION)")
    print(f"  Type A GT frames:        {stats['type_a']}")
    print(f"  Type B GT frames:        {stats['type_b']}")
    print(f"  Type C (no-ball):        {stats['type_c']}")
    print(f"  Flagged for R3 review:   {stats['flagged_review']} / {total_gt} "
          f"({stats['flagged_review'] / max(total_gt, 1):.1%})")
    print(f"  No reference (skipped):  {stats['no_reference']}")
    print()
    print("  ⚠ PROVISIONAL SIMULATION — not real human annotation")
    print("  ⚠ Run 02_annotate_ground_truth.py for real human GT")


if __name__ == "__main__":
    run()
