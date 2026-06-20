#!/usr/bin/env python3
"""
03_benchmark.py — Smart Snap POC-1 v2

Runs M1–M6 against the ground truth and emits benchmark_results.json.

Baselines (separate, not interchangeable):
  M1  Synthetic raw tap   — model_x/y + Gaussian noise (SYNTHETIC, σ=0.03)
  M2_raw  Human raw tap   — mean of 3 reps from ground_truth.json (SIMULATED)
  M2_loupe Human loupe tap — mean of 3 reps from ground_truth.json (SIMULATED)

Methods:
  M3  Stored SSD          — model_x/y unchanged (no image processing)
  M4  Local contour snap  — Canny + findContours within ROI
  M5  ROI Hough circles   — HoughCircles within ROI
  M6  Template matching   — synthetic filled-circle template

Wrong-snap rate reported against BOTH M1 (synthetic) and M2_raw (simulated human).
M2_loupe acts as the "aspirational" baseline — a snap must be competitive with
a zoomed human tap to be considered useful.

Separate aggregation is produced for tuning and holdout sets.
Final verdict is based on holdout only.

No DB access.

Usage:
    python scripts/smart_snap_poc1/03_benchmark.py
    python scripts/smart_snap_poc1/03_benchmark.py --no-m6
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
from PIL import Image

from scripts.smart_snap_poc1.algorithms import (
    M3StoredSSD, M4Contour, M5Hough, M6TemplateMatch, SnapResult,
)
from scripts.smart_snap_poc1.config import (
    BENCHMARK_RESULTS_PATH,
    DATASET_DIR,
    GROUND_TRUTH_PATH,
    M1_N_SIMULATIONS,
    M1_SIGMA_NORM,
    MANIFEST_PATH,
)
from scripts.smart_snap_poc1.metrics import (
    aggregate,
    false_positive_rate,
    false_refusal_rate,
    latency_summary,
    pixel_error,
    wrong_snap_rate,
)

# ── Loaders ──────────────────────────────────────────────────────────────────

def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_image(frame_id: str) -> Optional[np.ndarray]:
    path = os.path.join(DATASET_DIR, f"{frame_id}.jpg")
    if not os.path.isfile(path):
        return None
    return np.array(Image.open(path))


# ── M1 synthetic ─────────────────────────────────────────────────────────────

def _run_m1_synthetic(
    model_x: float, model_y: float,
    gt_x: float, gt_y: float,
    img_w: int, img_h: int,
    rng: random.Random,
) -> dict:
    errors: list[float] = []
    for _ in range(M1_N_SIMULATIONS):
        tx = max(0.0, min(1.0, model_x + rng.gauss(0, M1_SIGMA_NORM)))
        ty = max(0.0, min(1.0, model_y + rng.gauss(0, M1_SIGMA_NORM)))
        errors.append(pixel_error(tx, ty, gt_x, gt_y, img_w, img_h))
    return {
        "method": "M1_synthetic_raw_tap",
        "data_source": "SYNTHETIC",
        "n_draws": M1_N_SIMULATIONS,
        "sigma_norm": M1_SIGMA_NORM,
        **aggregate(errors),
    }


# ── Per-frame benchmark ───────────────────────────────────────────────────────

def benchmark_frame(
    frame: dict,
    gt_entry: Optional[dict],
    algorithms: list,
    rng: random.Random,
) -> dict:
    frame_id = frame["frame_id"]
    img_w = frame.get("image_width_px") or 640
    img_h = frame.get("image_height_px") or 480

    # GT overrides manifest: if GT has is_no_ball, trust that over manifest type
    gt_is_no_ball = gt_entry.get("is_no_ball") if gt_entry else None
    is_no_ball = gt_is_no_ball if gt_is_no_ball is not None else frame.get("is_no_ball", False)
    # Exclude unusable frames from both positive and no-ball benchmark categories
    gt_prov = (gt_entry or {}).get("gt_provenance", "")
    is_excluded = gt_prov == "human_unusable_quality"
    if is_excluded:
        is_no_ball = False  # won't count as no-ball FP test
    has_gt = (
        gt_entry is not None
        and gt_entry.get("gt_final") is not None
        and not is_no_ball
        and not is_excluded
    )

    gt_x = gt_entry["gt_final"]["x"] if has_gt else None
    gt_y = gt_entry["gt_final"]["y"] if has_gt else None

    model_x = frame.get("model_x")
    model_y = frame.get("model_y")
    model_conf = frame.get("model_confidence")

    result: dict = {
        "frame_id": frame_id,
        "type": frame["type"],
        "video_id": frame.get("video_id", "")[:8],
        "category": frame.get("category") or "unassigned",
        "category_source": frame.get("category_source", "unknown"),
        "split": frame.get("split"),
        "has_gt": has_gt,
        "is_no_ball": is_no_ball,
        "gt_provenance": (gt_entry or {}).get("gt_provenance", "none"),
        "gt_agreement_px": (gt_entry or {}).get("gt_agreement_px"),
        "gt_review_required": (gt_entry or {}).get("gt_review_required", False),
        "img_w": img_w,
        "img_h": img_h,
        "methods": {},
    }

    # ── M1: synthetic raw tap ─────────────────────────────────────────────
    if has_gt and model_x is not None:
        result["methods"]["M1_synthetic_raw_tap"] = _run_m1_synthetic(
            model_x, model_y, gt_x, gt_y, img_w, img_h, rng
        )
    else:
        result["methods"]["M1_synthetic_raw_tap"] = {
            "method": "M1_synthetic_raw_tap", "skipped": True,
            "reason": "no_model_prediction" if model_x is None else "no_gt",
        }

    # ── M2_raw: human raw tap (simulated) ────────────────────────────────
    raw_tap = (gt_entry or {}).get("human_raw_tap")
    if raw_tap and has_gt:
        raw_x = raw_tap.get("x")
        raw_y = raw_tap.get("y")
        if raw_x is not None and raw_y is not None:
            reps = raw_tap.get("reps", [])
            rep_errors = [
                pixel_error(r["x"], r["y"], gt_x, gt_y, img_w, img_h)
                for r in reps if "x" in r and "y" in r
            ]
            mean_err = pixel_error(raw_x, raw_y, gt_x, gt_y, img_w, img_h)
            result["methods"]["M2_human_raw_tap"] = {
                "method": "M2_human_raw_tap",
                "data_source": raw_tap.get("data_source", "SIMULATED"),
                "x": raw_x, "y": raw_y,
                "pixel_error": mean_err,
                "rep_errors": rep_errors,
                "n_reps": len(reps),
            }
        else:
            result["methods"]["M2_human_raw_tap"] = {
                "method": "M2_human_raw_tap", "skipped": True,
                "reason": "no_coords",
            }
    else:
        result["methods"]["M2_human_raw_tap"] = {
            "method": "M2_human_raw_tap", "skipped": True,
            "reason": "no_raw_tap_annotation" if not raw_tap else "no_gt",
        }

    # ── M2_loupe: human loupe tap (simulated) ────────────────────────────
    loupe_tap = (gt_entry or {}).get("human_loupe_tap")
    if loupe_tap and has_gt:
        lx = loupe_tap.get("x")
        ly = loupe_tap.get("y")
        if lx is not None and ly is not None:
            loupe_err = pixel_error(lx, ly, gt_x, gt_y, img_w, img_h)
            result["methods"]["M2_human_loupe_tap"] = {
                "method": "M2_human_loupe_tap",
                "data_source": loupe_tap.get("data_source", "SIMULATED"),
                "x": lx, "y": ly,
                "pixel_error": loupe_err,
            }
        else:
            result["methods"]["M2_human_loupe_tap"] = {
                "method": "M2_human_loupe_tap", "skipped": True,
                "reason": "no_coords",
            }
    else:
        result["methods"]["M2_human_loupe_tap"] = {
            "method": "M2_human_loupe_tap", "skipped": True,
            "reason": "no_loupe_annotation" if not loupe_tap else "no_gt",
        }

    # ── M3–M6: algorithm methods ──────────────────────────────────────────
    img = _load_image(frame_id)
    tap_x = model_x if model_x is not None else 0.5
    tap_y = model_y if model_y is not None else 0.5

    for algo in algorithms:
        name = algo.name
        if img is None:
            result["methods"][name] = {"method": name, "skipped": True, "reason": "image_not_extracted"}
            continue

        snap: SnapResult = algo(img, tap_x, tap_y,
                                model_x=model_x, model_y=model_y,
                                model_confidence=model_conf)
        entry: dict = {
            "method": name,
            "data_source": "ALGORITHM",
            "found": snap.found,
            "refined_x": snap.refined_x,
            "refined_y": snap.refined_y,
            "confidence": snap.confidence,
            "refusal_reason": snap.refusal_reason,
            "latency_ms": snap.latency_ms,
        }
        if has_gt and snap.found and snap.refined_x is not None:
            entry["pixel_error"] = pixel_error(
                snap.refined_x, snap.refined_y, gt_x, gt_y, img_w, img_h
            )
        if is_no_ball:
            entry["false_positive"] = snap.found

        result["methods"][name] = entry

    return result


# ── Aggregation helpers ───────────────────────────────────────────────────────

def _agg_set(frame_results: list[dict], method: str, baseline_m1: str) -> dict:
    """Aggregate metrics for one method over a set of frames."""
    errors_all: list[float] = []
    by_cat: dict[str, list[float]] = {}
    by_vid: dict[str, list[float]] = {}
    latencies: list[float] = []
    no_ball_fp: list[bool] = []
    found_flags: list[bool] = []

    m1_baseline_per_frame: list[float | None] = []
    m2_raw_baseline_per_frame: list[float | None] = []
    m2_loupe_baseline_per_frame: list[float | None] = []
    snap_err_per_frame: list[float | None] = []

    for fr in frame_results:
        m = fr["methods"].get(method, {})
        if m.get("skipped"):
            continue

        lat = m.get("latency_ms")
        if lat is not None:
            latencies.append(lat)

        if fr.get("is_no_ball"):
            fp = m.get("false_positive")
            if fp is not None:
                no_ball_fp.append(fp)
            continue

        found = m.get("found")
        if found is not None:
            found_flags.append(bool(found))

        err = m.get("pixel_error") or (m.get("mean") if method == "M1_synthetic_raw_tap" else None)
        if err is not None and fr.get("has_gt"):
            errors_all.append(err)
            cat = fr.get("category") or "unassigned"
            by_cat.setdefault(cat, []).append(err)
            vid = fr.get("video_id", "unknown")
            by_vid.setdefault(vid, []).append(err)

            snap_err_per_frame.append(err)
            # M1 baseline for this frame
            m1_m = fr["methods"].get("M1_synthetic_raw_tap", {})
            m1_baseline_per_frame.append(m1_m.get("mean") if not m1_m.get("skipped") else None)
            # M2_raw baseline
            m2r = fr["methods"].get("M2_human_raw_tap", {})
            m2_raw_baseline_per_frame.append(m2r.get("pixel_error") if not m2r.get("skipped") else None)
            # M2_loupe baseline
            m2l = fr["methods"].get("M2_human_loupe_tap", {})
            m2_loupe_baseline_per_frame.append(m2l.get("pixel_error") if not m2l.get("skipped") else None)

    # Paired wrong-snap rates
    def _paired_wsr(snaps, baselines):
        pairs = [(s, b) for s, b in zip(snaps, baselines) if s is not None and b is not None]
        if not pairs:
            return None
        return wrong_snap_rate([p[0] for p in pairs], [p[1] for p in pairs])

    # Confidence distribution (M3–M6 only)
    conf_values = [
        fr["methods"].get(method, {}).get("confidence")
        for fr in frame_results
        if fr["methods"].get(method, {}).get("confidence") is not None
    ]

    return {
        "overall": aggregate(errors_all),
        "by_category": {c: aggregate(v) for c, v in by_cat.items() if v},
        "by_video": {v: aggregate(e) for v, e in by_vid.items() if e},
        "latency": latency_summary(latencies),
        "wrong_snap_rate_vs_m1_synthetic": _paired_wsr(snap_err_per_frame, m1_baseline_per_frame),
        "wrong_snap_rate_vs_m2_raw": _paired_wsr(snap_err_per_frame, m2_raw_baseline_per_frame),
        "wrong_snap_rate_vs_m2_loupe": _paired_wsr(snap_err_per_frame, m2_loupe_baseline_per_frame),
        "false_positive_rate": false_positive_rate(no_ball_fp) if no_ball_fp else None,
        "false_refusal_rate": false_refusal_rate(found_flags) if found_flags else None,
        "confidence_distribution": aggregate(conf_values) if conf_values else None,
        "n_no_ball_frames_tested": len(no_ball_fp),
    }


def aggregate_results(frame_results: list[dict]) -> dict:
    method_names = sorted({
        name for fr in frame_results
        for name, m in fr["methods"].items() if not m.get("skipped")
    })

    tuning = [fr for fr in frame_results if fr.get("split") == "tuning"]
    holdout = [fr for fr in frame_results if fr.get("split") == "holdout"]
    unsplit = [fr for fr in frame_results if fr.get("split") is None]

    aggregated: dict = {}
    for method in method_names:
        aggregated[method] = {
            "all": _agg_set(frame_results, method, "M1_synthetic_raw_tap"),
            "tuning": _agg_set(tuning, method, "M1_synthetic_raw_tap") if tuning else None,
            "holdout": _agg_set(holdout, method, "M1_synthetic_raw_tap") if holdout else None,
            "unsplit": _agg_set(unsplit, method, "M1_synthetic_raw_tap") if unsplit else None,
        }

    return aggregated


# ── Annotation warning ────────────────────────────────────────────────────────

def _build_annotation_warning(gt: dict) -> str:
    real_human = sum(
        1 for e in gt.values()
        if e.get("gt_provenance") in ("human_annotated", "two_round_average")
        and e.get("gt_final") is not None
    )
    simulated = sum(
        1 for e in gt.values()
        if e.get("gt_provenance") == "provisional_simulation"
    )
    real_m2 = sum(
        1 for e in gt.values()
        if (e.get("human_raw_tap") or {}).get("data_source") == "human"
    )
    if simulated == 0 and real_human > 0:
        return (
            f"GT coordinates: {real_human} frames with real human annotation "
            f"(02_annotate_ground_truth.py, model hidden in GT_R1/GT_R2). "
            f"M2_raw/M2_loupe: {real_m2} frames with real human taps."
        )
    return (
        f"MIXED: {real_human} real human GT frames, {simulated} provisional simulation frames. "
        "Verify per-frame data_source before treating M2 baselines as ground truth."
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def run(include_m6: bool = True) -> None:
    for path, name in [(MANIFEST_PATH, "manifest.json"), (GROUND_TRUTH_PATH, "ground_truth.json")]:
        if not os.path.isfile(path):
            print(f"ERROR: {name} missing. Run previous pipeline steps first.", file=sys.stderr)
            sys.exit(1)

    manifest = _load_json(MANIFEST_PATH)
    gt: dict = _load_json(GROUND_TRUTH_PATH).get("frames", {})

    algorithms = [M3StoredSSD(), M4Contour(), M5Hough()]
    if include_m6:
        algorithms.append(M6TemplateMatch())

    rng = random.Random(42)
    frame_results: list[dict] = []

    for frame in manifest["frames"]:
        fid = frame["frame_id"]
        gt_entry = gt.get(fid)
        print(f"  {fid}  type={frame['type']}  split={frame.get('split','?')}  "
              f"cat={frame.get('category','?')}  gt={'✓' if gt_entry else '—'}")
        frame_results.append(benchmark_frame(frame, gt_entry, algorithms, rng))

    agg = aggregate_results(frame_results)

    # ── Summary counts ────────────────────────────────────────────────────
    n_gt = sum(1 for fr in frame_results if fr["has_gt"])
    n_no_ball = sum(1 for fr in frame_results if fr["is_no_ball"])
    n_tuning = sum(1 for fr in frame_results if fr.get("split") == "tuning")
    n_holdout = sum(1 for fr in frame_results if fr.get("split") == "holdout")
    cat_counts: dict[str, int] = {}
    for fr in frame_results:
        cat_counts[fr["category"]] = cat_counts.get(fr["category"], 0) + 1

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "poc": "smart_snap_poc1",
        "schema_version": "2.0",
        "annotation_data_warning": _build_annotation_warning(gt),
        "summary": {
            "total_frames": len(frame_results),
            "frames_with_gt": n_gt,
            "no_ball_frames": n_no_ball,
            "tuning_frames": n_tuning,
            "holdout_frames": n_holdout,
            "unsplit_frames": len(frame_results) - n_tuning - n_holdout,
            "algorithms": [a.name for a in algorithms],
            "category_distribution": cat_counts,
        },
        "per_frame": frame_results,
        "aggregated": agg,
    }

    with open(BENCHMARK_RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, default=str)

    print(f"\nBenchmark complete → benchmark_results.json")
    print(f"  Frames: {len(frame_results)} total, {n_gt} with GT, {n_no_ball} no-ball")
    print(f"  Split:  tuning={n_tuning}, holdout={n_holdout}")
    print()

    # Print method overview using holdout if available, else all
    set_label = "holdout" if n_holdout > 0 else "all"
    print(f"  Method overview ({set_label} set, mean pixel error vs GT):")
    for method, stats in agg.items():
        s = stats.get(set_label) or stats.get("all") or {}
        overall = s.get("overall", {})
        mean_str = f"{overall.get('mean', 0):.1f}px" if overall.get("mean") is not None else "N/A"
        wsr_m1 = s.get("wrong_snap_rate_vs_m1_synthetic")
        wsr_raw = s.get("wrong_snap_rate_vs_m2_raw")
        fp = s.get("false_positive_rate")
        print(f"    {method:<28} mean={mean_str:<10} "
              f"wsr_vs_M1={f'{wsr_m1:.1%}' if wsr_m1 is not None else 'N/A':<8} "
              f"wsr_vs_M2raw={f'{wsr_raw:.1%}' if wsr_raw is not None else 'N/A':<8} "
              f"FP={'N/A' if fp is None else f'{fp:.1%}'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-m6", action="store_true")
    args = parser.parse_args()
    run(include_m6=not args.no_m6)
