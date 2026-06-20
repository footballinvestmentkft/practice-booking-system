# Smart Snap POC-1 — Benchmark Report v2

Generated: 2026-06-20 13:37 UTC  
Source: `benchmark_results.json`

> ℹ **ANNOTATION STATUS**: GT coordinates: 40 frames with real human annotation (02_annotate_ground_truth.py, model hidden in GT_R1/GT_R2). M2_raw/M2_loupe: 20 frames with real human taps.

---

## 1. Dataset Overview

| Metric | Value |
|--------|-------|
| Total frames | 60 |
| Type A (DB human-corrected, reference only) | 20 |
| Type B (fresh positive, no human correction) | 30 |
| Type C (no-ball, tracking_state=lost) | 10 |
| Frames with GT available | 40 |
| No-ball frames | 17 |
| Tuning set | 41 |
| Holdout set | 19 |
| Unsplit (no split assigned) | 0 |

### Category distribution

| Category | Count | Coverage |
|----------|-------|----------|
| clear_ball | 25 |  |
| edge_of_frame | 22 |  |
| low_contrast | 3 |  |
| motion_blur | 0 | ⚠ MISSING — see recording plan |
| no_ball | 10 |  |
| partial_occlusion | 0 | ⚠ MISSING — see recording plan |
| small_ball | 0 | ⚠ MISSING — see recording plan |
| unassigned | 0 |  |

### Per-video distribution (positive frames only)

| Video (first 8 chars) | Positive frames | Share |
|-----------------------|----------------|-------|
| 8e39b06f | 22 | 51.2% |
| 66ee1c8a | 9 | 20.9% |
| a3d45095 | 8 | 18.6% |
| 47466ee0 | 4 | 9.3% |

---

## 2. Data Sources — Measurement vs Estimation vs Hypothesis

### TÉNYLEGESEN MÉRT (directly measured)

- Pixel error for M3/M4/M5/M6 vs GT on extracted JPEG frames
- False-positive rate from algorithm outputs on no-ball frames
- False-refusal rate per algorithm
- Python/OpenCV latency p50/p95
- GT coordinates: 40 frames annotated via 02_annotate_ground_truth.py (model HIDDEN in GT_R1/GT_R2, independent rounds)
- M2_raw and M2_loupe: real human taps collected in same annotation session

### BECSLÉSEK

| Data | Provenance | Bias risk |
|------|-----------|-----------|
| M1 synthetic | model_predicted_x/y + σ=0.030 Monte Carlo | Not measured from real E2E tap logs |
| DB corrected_x/y (Type A reference) | juggling_ball_feedback.corrected_x/y | Annotator may have seen model overlay — not used as GT |

### HIPOTÉZISEK (not measured)

- iOS VNDetectContoursRequest latency (hypothesis: p95 < 200ms on iPhone 13+)
- Motion blur effect on Canny edge detection
- Partial occlusion effect on Hough circle fitting

---

## 3. Ground Truth Quality

| Metric | Value |
|--------|-------|
| GT frames | 40 |
| Avg inter-round agreement (px, 1920-ref) | 11.0 |
| Max inter-round agreement (px, 1920-ref) | 74.8 |
| Frames flagged for R3 review (>20px diff) | 47 |
| GT provenance — human_annotated | 19 |
| GT provenance — human_no_ball_confirmed | 15 |
| GT provenance — human_unusable_quality | 3 |
| GT provenance — two_round_average | 21 |
| GT provenance — validated_no_ball_db_lost_state | 2 |

> **GT independence status**: REAL HUMAN ANNOTATION — 40 frames.
> GT_R1 and GT_R2 annotated independently with model HIDDEN.
> gt_final = mean(R1, R2). Inter-round agreement computed per frame.

---

## 4a. Method Comparison — TUNING set

> Tuning set: thresholds calibrated here.

### Pixel error (vs GT, 1920px-reference)

| Method | Mean | Median | p90 | p95 | n |
|--------|------|--------|-----|-----|---|
| M1_synthetic_raw_tap *(SYNTHETIC)* | 277.8 | 123.4 | 838.9 | 850.5 | 21 |
| M2_human_raw_tap *(SIMULATED)* | 76.5 | 60.8 | 172.7 | 185.8 | 12 |
| M2_human_loupe_tap *(SIMULATED)* | 9.5 | 7.6 | 10.7 | 23.9 | 12 |
| M3_stored_ssd | 251.2 | 79.5 | 831.6 | 837.8 | 21 |
| M4_contour | 238.4 | 173.3 | 410.9 | 625.9 | 9 |
| M5_hough | 235.9 | 145.2 | 648.3 | 838.4 | 22 |
| M6_template_match | 376.1 | 263.1 | 760.7 | 813.2 | 21 |

### Wrong-snap rate & FP/refusal

| Method | Wrong-snap vs M1 | Wrong-snap vs M2_raw | FP rate | False-refusal |
|--------|-----------------|---------------------|---------|---------------|
| M3_stored_ssd | 9.5% | 41.7% | 91.7% | 20.7% |
| M4_contour | 62.5% | 71.4% | 50.0% | 69.0% |
| M5_hough | 52.9% | 58.3% | 100.0% | 13.8% |
| M6_template_match | 75.0% | 100.0% | 100.0% | 17.2% |

### Per-category (mean px error)

| Method | clear_ball | edge_of_frame | low_contrast | no_ball |
|--------|---|---|---|---|
| M3_stored_ssd | 141.8 | 481.9 | 439.9 | N/A |
| M4_contour | 144.1 | N/A | 840.9 | 295.6 |
| M5_hough | 155.3 | 171.2 | 933.0 | 347.9 |
| M6_template_match | 248.9 | 770.7 | 760.7 | 472.3 |

### Per-video (mean px error)

| Method | 47466ee0 | 66ee1c8a | 8e39b06f | a3d45095 |
|--------|---|---|---|---|
| M3_stored_ssd | 204.9 | 492.6 | 74.6 | 551.2 |
| M4_contour | N/A | 840.9 | 163.1 | N/A |
| M5_hough | 439.8 | 566.5 | 112.7 | 232.7 |
| M6_template_match | 455.5 | 563.6 | 248.8 | 906.8 |

---

## 4b. Method Comparison — HOLDOUT set

> **Verdict is based on HOLDOUT set only.**

### Pixel error (vs GT, 1920px-reference)

| Method | Mean | Median | p90 | p95 | n |
|--------|------|--------|-----|-----|---|
| M1_synthetic_raw_tap *(SYNTHETIC)* | 242.4 | 115.6 | 807.7 | 926.8 | 12 |
| M2_human_raw_tap *(SIMULATED)* | 70.7 | 56.1 | 110.1 | 112.9 | 8 |
| M2_human_loupe_tap *(SIMULATED)* | 7.4 | 7.2 | 11.3 | 13.2 | 8 |
| M3_stored_ssd | 211.2 | 68.3 | 817.4 | 936.6 | 12 |
| M4_contour | 332.5 | 175.5 | 726.7 | 843.2 | 4 |
| M5_hough | 217.3 | 97.9 | 644.8 | 803.6 | 12 |
| M6_template_match | 338.3 | 268.3 | 624.2 | 871.1 | 11 |

### Wrong-snap rate & FP/refusal

| Method | Wrong-snap vs M1 | Wrong-snap vs M2_raw | FP rate | False-refusal |
|--------|-----------------|---------------------|---------|---------------|
| M3_stored_ssd | 16.7% | 75.0% | 80.0% | 14.3% |
| M4_contour | 50.0% | 66.7% | 40.0% | 71.4% |
| M5_hough | 30.0% | 62.5% | 100.0% | 14.3% |
| M6_template_match | 100.0% | 100.0% | 100.0% | 21.4% |

### Per-category (mean px error)

| Method | clear_ball | edge_of_frame | low_contrast | no_ball |
|--------|---|---|---|---|
| M3_stored_ssd | 69.9 | 653.4 | 14.8 | N/A |
| M4_contour | 123.4 | 959.6 | N/A | N/A |
| M5_hough | 72.4 | 498.0 | N/A | 516.0 |
| M6_template_match | 213.9 | 1118.0 | N/A | 446.2 |

### Per-video (mean px error)

| Method | 47466ee0 | 66ee1c8a | 8e39b06f | a3d45095 |
|--------|---|---|---|---|
| M3_stored_ssd | 987.5 | 14.8 | 69.9 | 486.3 |
| M4_contour | 959.6 | N/A | 123.4 | N/A |
| M5_hough | 817.6 | N/A | 72.4 | 196.4 |
| M6_template_match | 871.1 | N/A | 213.9 | 268.3 |

---

## 5. Latency — Python/OpenCV (TÉNYLEGESEN MÉRT)

> **iOS VNDetectContoursRequest latency: N/A — POC-2 physical iPhone benchmark required.**

| Method | p50 (ms) | p95 (ms) | n |
|--------|----------|----------|---|
| M3_stored_ssd | 0.0 | 0.0 | 60 |
| M4_contour | 0.3 | 0.8 | 60 |
| M5_hough | 0.7 | 2.0 | 60 |
| M6_template_match | 0.5 | 0.7 | 60 |
---

## 6. Acceptance Gate Evaluation (HOLDOUT set)

> Primary wrong-snap gate uses M2_raw (simulated human tap) as baseline,
> not M1 synthetic. This is stricter and more operationally relevant.

| Gate | Threshold | Best Method | Value | Status |
|------|-----------|-------------|-------|--------|
| Improves mean error vs M1 synthetic | < 242.4px | M3_stored_ssd | 211.2px | ✅ PASS |
| Wrong-snap rate vs M2_raw | < 5% | M5_hough | 62.5% | ❌ FAIL |
| No-ball FP rate | < 10% | M4_contour | 40.0% | ❌ FAIL |
| Latency p95 (Python) | < 500ms | M3_stored_ssd | 0.0ms | ✅ PASS |
| iOS latency p95 | < 500ms | — | N/A | 🔲 POC-2 required |

---

## 7. Recording Plan for Missing Categories

The following categories have 0 frames and cannot be assessed from existing data.
New video recordings are required before the full acceptance gate set can be evaluated.

### `motion_blur`

Record juggling with fast lateral ball movement (> 5m/s). Use 30fps camera (not 60fps) to maximize motion blur effect. Target: 10+ frames where ball trail is visible and tracker loses the ball mid-motion.

### `partial_occlusion`

Record juggling with ball passing behind the player's foot, shin, or arm. Include moments where the ball is ~50% hidden by body part. Target: 10+ frames with partial occlusion confirmed by human annotation.

### `small_ball`

Record juggling from a distance (> 5m camera-to-player) so the ball appears < 20px in diameter. Alternatively, use drone footage from above. Target: 10+ frames where model confidence is < 0.5 due to ball size.


---

## 8. Verdict (HOLDOUT set)

### NEED MORE DATA

**Missing categories: motion_blur, partial_occlusion, small_ball. Robustness cannot be assessed across all required scenarios.**

Gate summary: 2 PASS | 2 FAIL | 0 N/A (Python benchmark only)

---

*Report generated by `04_report.py` | 2026-06-20 13:37 UTC*

> **CONSTRAINT**: No automatic snap production integration. No main merge.
> Both require separate explicit approval after validated POC-1 holdout results.