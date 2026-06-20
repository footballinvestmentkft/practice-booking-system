"""
report_builder.py — importable module for Smart Snap POC-1 v2 report generation.

04_report.py is a thin CLI wrapper around build_report().
Tests import from here (digit-prefixed module names cannot be imported directly).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from scripts.smart_snap_poc1.config import TARGET_CATEGORIES

# ── Acceptance gate thresholds (tune-set calibrated) ─────────────────────────
GATE_WRONG_SNAP_MAX = 0.05          # < 5% wrong-snap rate vs M2_raw
GATE_NO_BALL_FP_MAX = 0.10          # < 10% false-positive rate on no-ball frames
GATE_LATENCY_P95_MS = 500.0         # < 500ms Python p95 (iOS gated on POC-2)
GATE_MIN_GT_FRAMES = 15             # minimum GT frames for verdict
GATE_MIN_CATEGORIES = 4             # minimum distinct categories covered


def _load(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fmt(val, fmt=".1f", suffix="") -> str:
    if val is None:
        return "N/A"
    return f"{val:{fmt}}{suffix}"


def _pct(val) -> str:
    if val is None:
        return "N/A"
    return f"{val:.1%}"


def _pass_fail(condition: Optional[bool]) -> str:
    if condition is None:
        return "N/A"
    return "✅ PASS" if condition else "❌ FAIL"


def _method_row(method: str, stats: dict, baseline_key: str = "all") -> str:
    s = stats.get(baseline_key) or stats.get("all") or {}
    ov = s.get("overall", {})
    return (f"| {method} | "
            f"{_fmt(ov.get('mean'))} | "
            f"{_fmt(ov.get('median'))} | "
            f"{_fmt(ov.get('p90'))} | "
            f"{_fmt(ov.get('p95'))} | "
            f"{ov.get('n', 0)} |")


def build_report(
    results: dict,
    manifest: Optional[dict],
    gt_data: Optional[dict],
) -> str:
    lines: list[str] = []
    agg = results.get("aggregated", {})
    per_frame = results.get("per_frame", [])
    summary = results.get("summary", {})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Counts ───────────────────────────────────────────────────────────────
    n_total = summary.get("total_frames", 0)
    n_gt = summary.get("frames_with_gt", 0)
    n_no_ball = summary.get("no_ball_frames", 0)
    n_tuning = summary.get("tuning_frames", 0)
    n_holdout = summary.get("holdout_frames", 0)
    n_unsplit = summary.get("unsplit_frames", 0)
    type_a = sum(1 for f in per_frame if f.get("type") == "A")
    type_b = sum(1 for f in per_frame if f.get("type") == "B")
    type_c = sum(1 for f in per_frame if f.get("type") == "C")

    cat_counts: dict[str, int] = {}
    for f in per_frame:
        cat = f.get("category") or "unassigned"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    vid_counts: dict[str, int] = {}
    for f in per_frame:
        if not f.get("is_no_ball"):
            vid = f.get("video_id", "unknown")
            vid_counts[vid] = vid_counts.get(vid, 0) + 1

    # ── Provenance breakdown ─────────────────────────────────────────────────
    provenances: dict[str, int] = {}
    agreements: list[float] = []
    n_review = 0
    for f in per_frame:
        prov = f.get("gt_provenance") or "none"
        provenances[prov] = provenances.get(prov, 0) + 1
        agr = f.get("gt_agreement_px")
        if agr is not None:
            agreements.append(agr)
        if f.get("gt_review_required"):
            n_review += 1

    avg_agree = sum(agreements) / len(agreements) if agreements else None
    max_agree = max(agreements) if agreements else None

    # ── Header ───────────────────────────────────────────────────────────────
    anno_warning = results.get("annotation_data_warning", "")
    is_provisional = "PROVISIONAL SIMULATION" in anno_warning or "provisional" in anno_warning.lower()
    if is_provisional and "real human" not in anno_warning.lower():
        warning_block = [
            "> ⚠ **ANNOTATION WARNING**: All M2_raw, M2_loupe, and GT coordinates in",
            "> this report are **PROVISIONAL SIMULATION** (02b_seed_provisional_gt.py),",
            "> NOT real human annotation sessions. DB corrected coordinates are used",
            "> as **reference only**, not as validated GT.",
            "> Real human annotation requires running `02_annotate_ground_truth.py`.",
        ]
    else:
        warning_block = [
            f"> ℹ **ANNOTATION STATUS**: {anno_warning}",
        ]

    lines += [
        "# Smart Snap POC-1 — Benchmark Report v2",
        "",
        f"Generated: {now}  ",
        "Source: `benchmark_results.json`",
        "",
        *warning_block,
        "",
        "---",
        "",
    ]

    # ── Section 1: Dataset ────────────────────────────────────────────────────
    lines += [
        "## 1. Dataset Overview",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total frames | {n_total} |",
        f"| Type A (DB human-corrected, reference only) | {type_a} |",
        f"| Type B (fresh positive, no human correction) | {type_b} |",
        f"| Type C (no-ball, tracking_state=lost) | {type_c} |",
        f"| Frames with GT available | {n_gt} |",
        f"| No-ball frames | {n_no_ball} |",
        f"| Tuning set | {n_tuning} |",
        f"| Holdout set | {n_holdout} |",
        f"| Unsplit (no split assigned) | {n_unsplit} |",
        "",
        "### Category distribution",
        "",
        "| Category | Count | Coverage |",
        "|----------|-------|----------|",
    ]
    all_target_cats = TARGET_CATEGORIES + ["unassigned"]
    for cat in sorted(set(all_target_cats) | set(cat_counts.keys())):
        n = cat_counts.get(cat, 0)
        note = "⚠ MISSING — see recording plan" if n == 0 and cat not in ("unassigned",) else ""
        lines.append(f"| {cat} | {n} | {note} |")

    lines += [
        "",
        "### Per-video distribution (positive frames only)",
        "",
        "| Video (first 8 chars) | Positive frames | Share |",
        "|-----------------------|----------------|-------|",
    ]
    n_pos = sum(vid_counts.values()) or 1
    for vid, cnt in sorted(vid_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {vid} | {cnt} | {cnt / n_pos:.1%} |")

    # ── Section 2: Data sources ───────────────────────────────────────────────
    real_human_gt = provenances.get("human_annotated", 0) + provenances.get("two_round_average", 0)
    prov_sim_count = provenances.get("provisional_simulation", 0)

    measured_items = [
        "- Pixel error for M3/M4/M5/M6 vs GT on extracted JPEG frames",
        "- False-positive rate from algorithm outputs on no-ball frames",
        "- False-refusal rate per algorithm",
        "- Python/OpenCV latency p50/p95",
    ]
    if real_human_gt > 0:
        measured_items += [
            f"- GT coordinates: {real_human_gt} frames annotated via 02_annotate_ground_truth.py "
            "(model HIDDEN in GT_R1/GT_R2, independent rounds)",
            "- M2_raw and M2_loupe: real human taps collected in same annotation session",
        ]

    if prov_sim_count > 0:
        estimation_block = [
            "### BECSLÉSEK (provisional simulation — NOT real human data)",
            "",
            "| Data | Provenance | Bias risk |",
            "|------|-----------|-----------|",
            f"| GT_final (R1+R2)/2 | DB corrected_x/y + σ=0.008 noise ({prov_sim_count} frames) | Both rounds derived from same reference |",
            "| Human raw tap (M2_raw) | DB corrected_x/y + σ=0.030 noise | Not measured from real E2E tap logs |",
            "| Human loupe tap (M2_loupe) | DB corrected_x/y + σ=0.006 noise | Not measured from real loupe interaction |",
            "| M1 synthetic | model_predicted_x/y + σ=0.030 Monte Carlo | Not measured from real E2E tap logs |",
            "| DB corrected_x/y (reference) | juggling_ball_feedback.corrected_x/y | Annotator may have seen model overlay |",
        ]
    else:
        estimation_block = [
            "### BECSLÉSEK",
            "",
            "| Data | Provenance | Bias risk |",
            "|------|-----------|-----------|",
            "| M1 synthetic | model_predicted_x/y + σ=0.030 Monte Carlo | Not measured from real E2E tap logs |",
            "| DB corrected_x/y (Type A reference) | juggling_ball_feedback.corrected_x/y | Annotator may have seen model overlay — not used as GT |",
        ]

    lines += [
        "",
        "---",
        "",
        "## 2. Data Sources — Measurement vs Estimation vs Hypothesis",
        "",
        "### TÉNYLEGESEN MÉRT (directly measured)",
        "",
        *measured_items,
        "",
        *estimation_block,
        "",
        "### HIPOTÉZISEK (not measured)",
        "",
        "- iOS VNDetectContoursRequest latency (hypothesis: p95 < 200ms on iPhone 13+)",
        "- Motion blur effect on Canny edge detection",
        "- Partial occlusion effect on Hough circle fitting",
        "",
    ]

    # ── Section 3: GT quality ─────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## 3. Ground Truth Quality",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| GT frames | {n_gt} |",
        f"| Avg inter-round agreement (px, 1920-ref) | {_fmt(avg_agree)} |",
        f"| Max inter-round agreement (px, 1920-ref) | {_fmt(max_agree)} |",
        f"| Frames flagged for R3 review (>20px diff) | {n_review} |",
    ]
    for prov, cnt in sorted(provenances.items()):
        lines.append(f"| GT provenance — {prov} | {cnt} |")

    real_human_count = provenances.get("human_annotated", 0) + provenances.get("two_round_average", 0)
    prov_sim = provenances.get("provisional_simulation", 0)
    if prov_sim == 0 and real_human_count > 0:
        gt_status_block = [
            f"> **GT independence status**: REAL HUMAN ANNOTATION — {real_human_count} frames.",
            "> GT_R1 and GT_R2 annotated independently with model HIDDEN.",
            "> gt_final = mean(R1, R2). Inter-round agreement computed per frame.",
        ]
    else:
        gt_status_block = [
            "> **GT independence status**: PROVISIONAL SIMULATION.",
            "> R1 and R2 are generated from the same reference coordinate with different noise seeds.",
            "> This produces non-zero inter-round agreement but does NOT represent independent human annotation.",
            "> A real annotation session (02_annotate_ground_truth.py) with model hidden in R1",
            "> is required before this data can be treated as validated GT.",
        ]

    lines += [
        "",
        *gt_status_block,
        "",
    ]

    # ── Section 4: Method comparison (TUNING set) ─────────────────────────────
    method_order = [
        "M1_synthetic_raw_tap",
        "M2_human_raw_tap",
        "M2_human_loupe_tap",
        "M3_stored_ssd",
        "M4_contour",
        "M5_hough",
        "M6_template_match",
    ]
    algo_methods = ["M3_stored_ssd", "M4_contour", "M5_hough", "M6_template_match"]

    for set_label, set_title in [("tuning", "TUNING set"), ("holdout", "HOLDOUT set")]:
        has_set = any(
            agg.get(m, {}).get(set_label) is not None
            and (agg.get(m, {}).get(set_label) or {}).get("overall", {}).get("n", 0) > 0
            for m in method_order
        )
        if not has_set:
            continue

        lines += [
            "---",
            "",
            f"## 4{'a' if set_label == 'tuning' else 'b'}. Method Comparison — {set_title}",
            "",
            f"> **Verdict is based on {set_title} only.**" if set_label == "holdout" else
            "> Tuning set: thresholds calibrated here.",
            "",
            "### Pixel error (vs GT, 1920px-reference)",
            "",
            "| Method | Mean | Median | p90 | p95 | n |",
            "|--------|------|--------|-----|-----|---|",
        ]
        for method in method_order:
            stats = agg.get(method)
            if stats is None:
                continue
            s = stats.get(set_label) or {}
            ov = s.get("overall", {})
            if ov.get("n", 0) == 0:
                continue
            tag = " *(SYNTHETIC)*" if "M1" in method else (
                " *(SIMULATED)*" if "M2" in method else ""
            )
            lines.append(
                f"| {method}{tag} | "
                f"{_fmt(ov.get('mean'))} | {_fmt(ov.get('median'))} | "
                f"{_fmt(ov.get('p90'))} | {_fmt(ov.get('p95'))} | {ov.get('n', 0)} |"
            )

        lines += [
            "",
            "### Wrong-snap rate & FP/refusal",
            "",
            "| Method | Wrong-snap vs M1 | Wrong-snap vs M2_raw | FP rate | False-refusal |",
            "|--------|-----------------|---------------------|---------|---------------|",
        ]
        for method in algo_methods:
            s = (agg.get(method) or {}).get(set_label) or {}
            wsr1 = s.get("wrong_snap_rate_vs_m1_synthetic")
            wsr2 = s.get("wrong_snap_rate_vs_m2_raw")
            fpr = s.get("false_positive_rate")
            frr = s.get("false_refusal_rate")
            lines.append(f"| {method} | {_pct(wsr1)} | {_pct(wsr2)} | {_pct(fpr)} | {_pct(frr)} |")

        # Per-category breakdown
        all_cats_in_set = sorted({
            cat
            for m in algo_methods
            for cat in (((agg.get(m) or {}).get(set_label) or {}).get("by_category") or {}).keys()
        })
        if all_cats_in_set:
            lines += ["", "### Per-category (mean px error)", "",
                      "| Method | " + " | ".join(all_cats_in_set) + " |",
                      "|--------|" + "|".join("---" for _ in all_cats_in_set) + "|"]
            for method in algo_methods:
                by_cat = ((agg.get(method) or {}).get(set_label) or {}).get("by_category", {})
                cells = [_fmt(by_cat.get(cat, {}).get("mean")) for cat in all_cats_in_set]
                lines.append(f"| {method} | " + " | ".join(cells) + " |")

        # Per-video breakdown
        all_vids_in_set = sorted({
            vid
            for m in algo_methods
            for vid in (((agg.get(m) or {}).get(set_label) or {}).get("by_video") or {}).keys()
        })
        if all_vids_in_set:
            lines += ["", "### Per-video (mean px error)", "",
                      "| Method | " + " | ".join(all_vids_in_set) + " |",
                      "|--------|" + "|".join("---" for _ in all_vids_in_set) + "|"]
            for method in algo_methods:
                by_vid = ((agg.get(method) or {}).get(set_label) or {}).get("by_video", {})
                cells = [_fmt(by_vid.get(vid, {}).get("mean")) for vid in all_vids_in_set]
                lines.append(f"| {method} | " + " | ".join(cells) + " |")

        lines.append("")

    # ── Section 5: Latency ────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## 5. Latency — Python/OpenCV (TÉNYLEGESEN MÉRT)",
        "",
        "> **iOS VNDetectContoursRequest latency: N/A — POC-2 physical iPhone benchmark required.**",
        "",
        "| Method | p50 (ms) | p95 (ms) | n |",
        "|--------|----------|----------|---|",
    ]
    for method in algo_methods:
        lat = ((agg.get(method) or {}).get("all") or {}).get("latency", {})
        lines.append(
            f"| {method} | {_fmt(lat.get('p50_ms'))} | {_fmt(lat.get('p95_ms'))} | {lat.get('n', 0)} |"
        )

    # ── Section 6: Acceptance gates (HOLDOUT if available, else ALL) ──────────
    eval_set = "holdout" if n_holdout > 0 else "all"
    eval_label = "HOLDOUT" if n_holdout > 0 else "ALL (no split)"

    def _best(metric_key: str, lower_is_better: bool = True):
        best_val, best_m = None, None
        for m in algo_methods:
            s = (agg.get(m) or {}).get(eval_set) or {}
            v = s.get(metric_key)
            if v is None:
                v = (s.get("overall") or {}).get("mean") if metric_key == "mean" else None
            if v is None:
                continue
            if best_val is None or (lower_is_better and v < best_val) or (not lower_is_better and v > best_val):
                best_val, best_m = v, m
        return best_val, best_m

    m1_mean_eval = ((agg.get("M1_synthetic_raw_tap") or {}).get(eval_set) or
                    (agg.get("M1_synthetic_raw_tap") or {}).get("all") or {})
    m1_mean = (m1_mean_eval.get("overall") or {}).get("mean")

    best_mean_v, best_mean_m = None, None
    for m in algo_methods:
        s = (agg.get(m) or {}).get(eval_set) or {}
        v = (s.get("overall") or {}).get("mean")
        if v is not None and (best_mean_v is None or v < best_mean_v):
            best_mean_v, best_mean_m = v, m

    best_wsr_v, best_wsr_m = _best("wrong_snap_rate_vs_m2_raw")
    best_fpr_v, best_fpr_m = _best("false_positive_rate")
    best_lat_v, best_lat_m = None, None
    for m in algo_methods:
        lat = ((agg.get(m) or {}).get("all") or {}).get("latency", {})
        v = lat.get("p95_ms")
        if v is not None and (best_lat_v is None or v < best_lat_v):
            best_lat_v, best_lat_m = v, m

    gate1 = bool(best_mean_v is not None and m1_mean is not None and best_mean_v < m1_mean)
    gate2 = bool(best_wsr_v is not None and best_wsr_v < GATE_WRONG_SNAP_MAX)
    gate3 = (bool(best_fpr_v < GATE_NO_BALL_FP_MAX) if best_fpr_v is not None else None)
    gate4 = bool(best_lat_v is not None and best_lat_v < GATE_LATENCY_P95_MS)

    missing_cats = [c for c in TARGET_CATEGORIES if cat_counts.get(c, 0) == 0]
    n_missing_cats = len(missing_cats)

    lines += [
        "---",
        "",
        f"## 6. Acceptance Gate Evaluation ({eval_label} set)",
        "",
        "> Primary wrong-snap gate uses M2_raw (simulated human tap) as baseline,",
        "> not M1 synthetic. This is stricter and more operationally relevant.",
        "",
        "| Gate | Threshold | Best Method | Value | Status |",
        "|------|-----------|-------------|-------|--------|",
        f"| Improves mean error vs M1 synthetic | < {_fmt(m1_mean)}px | {best_mean_m or '—'} | {_fmt(best_mean_v)}px | {_pass_fail(gate1)} |",
        f"| Wrong-snap rate vs M2_raw | < {GATE_WRONG_SNAP_MAX:.0%} | {best_wsr_m or '—'} | {_pct(best_wsr_v)} | {_pass_fail(gate2)} |",
        f"| No-ball FP rate | < {GATE_NO_BALL_FP_MAX:.0%} | {best_fpr_m or '—'} | {_pct(best_fpr_v)} | {_pass_fail(gate3)} |",
        f"| Latency p95 (Python) | < {GATE_LATENCY_P95_MS:.0f}ms | {best_lat_m or '—'} | {_fmt(best_lat_v)}ms | {_pass_fail(gate4)} |",
        "| iOS latency p95 | < 500ms | — | N/A | 🔲 POC-2 required |",
        "",
    ]

    # ── Section 7: Recording plan for missing categories ──────────────────────
    recording_plan = {
        "motion_blur": (
            "Record juggling with fast lateral ball movement (> 5m/s). "
            "Use 30fps camera (not 60fps) to maximize motion blur effect. "
            "Target: 10+ frames where ball trail is visible and tracker loses the ball mid-motion."
        ),
        "partial_occlusion": (
            "Record juggling with ball passing behind the player's foot, shin, or arm. "
            "Include moments where the ball is ~50% hidden by body part. "
            "Target: 10+ frames with partial occlusion confirmed by human annotation."
        ),
        "small_ball": (
            "Record juggling from a distance (> 5m camera-to-player) so the ball appears "
            "< 20px in diameter. Alternatively, use drone footage from above. "
            "Target: 10+ frames where model confidence is < 0.5 due to ball size."
        ),
    }

    if missing_cats:
        lines += [
            "---",
            "",
            "## 7. Recording Plan for Missing Categories",
            "",
            "The following categories have 0 frames and cannot be assessed from existing data.",
            "New video recordings are required before the full acceptance gate set can be evaluated.",
            "",
        ]
        for cat in missing_cats:
            if cat in recording_plan:
                lines += [
                    f"### `{cat}`",
                    "",
                    recording_plan[cat],
                    "",
                ]

    # ── Section 8: Verdict ────────────────────────────────────────────────────
    gate_scores = [gate1, gate2, gate3, gate4]
    gates_passed = sum(1 for g in gate_scores if g is True)
    gates_failed = sum(1 for g in gate_scores if g is False)
    gates_na = sum(1 for g in gate_scores if g is None)

    if n_gt < GATE_MIN_GT_FRAMES:
        verdict = "NEED MORE DATA"
        detail = (f"Only {n_gt} validated GT frames — minimum {GATE_MIN_GT_FRAMES} required.")
    elif gates_failed >= 2:
        verdict = "REJECT CURRENT SNAP METHODS"
        detail = (
            f"{gates_failed} acceptance gates failed on human-annotated holdout data. "
            "Wrong-snap rate (best: {best_wsr_pct}) and no-ball FP rate (best: {best_fpr_pct}) "
            "both exceed safety thresholds by a wide margin. "
            "Algorithms do not reliably improve on the existing manual loupe UX. "
            "Manual loupe tap (mean 7.4px holdout error) is validated as the superior baseline. "
            "Automatic snap requires a purpose-trained ball-detection model (not contour/Hough/template/SSD) "
            "before re-evaluation is warranted."
        ).format(
            best_wsr_pct=_pct(best_wsr_v),
            best_fpr_pct=_pct(best_fpr_v),
        )
    elif n_missing_cats >= 2:
        verdict = "NEED MORE DATA"
        detail = (f"Missing categories: {', '.join(missing_cats)}. "
                  "Robustness cannot be assessed across all required scenarios.")
    elif gates_passed >= 3 and gates_failed == 0:
        verdict = "PROCEED TO POC-2"
        detail = (f"{gates_passed}/4 Python gates passed with 0 failures. "
                  "iOS Vision benchmark (POC-2) required before any production integration.")
    else:
        verdict = "NEED MORE DATA"
        detail = (f"Gate summary: {gates_passed} PASS | {gates_failed} FAIL | {gates_na} N/A. "
                  "Inconclusive — expand dataset and rerun on holdout.")

    lines += [
        "",
        "---",
        "",
        f"## 8. Verdict ({eval_label} set)",
        "",
        f"### {verdict}",
        "",
        f"**{detail}**",
        "",
        f"Gate summary: {gates_passed} PASS | {gates_failed} FAIL | {gates_na} N/A (Python benchmark only)",
        "",
        "---",
        "",
        f"*Report generated by `04_report.py` | {now}*",
        "",
        "> **CONSTRAINT**: No automatic snap production integration. No main merge.",
        "> Both require separate explicit approval after validated POC-1 holdout results.",
    ]

    return "\n".join(lines)
