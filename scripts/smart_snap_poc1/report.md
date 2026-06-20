# Smart Snap POC-1 — Benchmark Report v2

Generated: 2026-06-20 11:42 UTC  
Source: `benchmark_results.json`

> ⚠ **ANNOTATION WARNING**: All M2_raw, M2_loupe, and GT coordinates in
> this report are **PROVISIONAL SIMULATION** (02b_seed_provisional_gt.py),
> NOT real human annotation sessions. DB corrected coordinates are used
> as **reference only**, not as validated GT.
> Real human annotation requires running `02_annotate_ground_truth.py`.

---

## 1. Dataset Overview

| Metric | Value |
|--------|-------|
| Total frames | 60 |
| Type A (DB human-corrected, reference only) | 20 |
| Type B (fresh positive, no human correction) | 30 |
| Type C (no-ball, tracking_state=lost) | 10 |
| Frames with GT available | 50 |
| No-ball frames | 10 |
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
| 8e39b06f | 20 | 40.0% |
| 47466ee0 | 8 | 16.0% |
| 5416b28f | 8 | 16.0% |
| 66ee1c8a | 8 | 16.0% |
| a3d45095 | 6 | 12.0% |

---

## 2. Data Sources — Measurement vs Estimation vs Hypothesis

### TÉNYLEGESEN MÉRT (directly measured)

- Pixel error for M3/M4/M5/M6 vs GT on extracted JPEG frames
- False-positive rate from algorithm outputs on Type C frames
- False-refusal rate per algorithm
- Python/OpenCV latency p50/p95

### BECSLÉSEK (provisional simulation — NOT real human data)

| Data | Provenance | Bias risk |
|------|-----------|-----------|
| GT_final (R1+R2)/2 | DB corrected_x/y + σ=0.008 noise | Both rounds derived from same reference |
| Human raw tap (M2_raw) | DB corrected_x/y + σ=0.030 noise | Not measured from real E2E tap logs |
| Human loupe tap (M2_loupe) | DB corrected_x/y + σ=0.006 noise | Not measured from real loupe interaction |
| M1 synthetic | model_predicted_x/y + σ=0.030 Monte Carlo | Not measured from real E2E tap logs |
| DB corrected_x/y (reference) | juggling_ball_feedback.corrected_x/y | Annotator may have seen model overlay |

### HIPOTÉZISEK (not measured)

- iOS VNDetectContoursRequest latency (hypothesis: p95 < 200ms on iPhone 13+)
- Motion blur effect on Canny edge detection
- Partial occlusion effect on Hough circle fitting

---

## 3. Ground Truth Quality

| Metric | Value |
|--------|-------|
| GT frames | 50 |
| Avg inter-round agreement (px, 1920-ref) | 27.8 |
| Max inter-round agreement (px, 1920-ref) | 81.8 |
| Frames flagged for R3 review (>20px diff) | 26 |
| GT provenance — provisional_simulation | 50 |
| GT provenance — validated_no_ball_db_lost_state | 10 |

> **GT independence status**: PROVISIONAL SIMULATION.
> R1 and R2 are generated from the same reference coordinate with different noise seeds.
> This produces non-zero inter-round agreement but does NOT represent independent human annotation.
> A real annotation session (02_annotate_ground_truth.py) with model hidden in R1
> is required before this data can be treated as validated GT.

---

## 4a. Method Comparison — TUNING set

> Tuning set: thresholds calibrated here.

### Pixel error (vs GT, 1920px-reference)

| Method | Mean | Median | p90 | p95 | n |
|--------|------|--------|-----|-----|---|
| M1_synthetic_raw_tap *(SYNTHETIC)* | 101.2 | 102.1 | 125.9 | 155.6 | 34 |
| M2_human_raw_tap *(SIMULATED)* | 66.1 | 61.9 | 105.0 | 115.1 | 34 |
| M2_human_loupe_tap *(SIMULATED)* | 13.5 | 12.4 | 25.6 | 27.8 | 34 |
| M3_stored_ssd | 37.3 | 20.8 | 79.9 | 135.4 | 34 |
| M4_contour | 94.8 | 20.9 | 227.5 | 266.8 | 14 |
| M5_hough | 71.2 | 49.9 | 152.1 | 185.1 | 30 |
| M6_template_match | 152.3 | 127.9 | 267.9 | 308.3 | 29 |

### Wrong-snap rate & FP/refusal

| Method | Wrong-snap vs M1 | Wrong-snap vs M2_raw | FP rate | False-refusal |
|--------|-----------------|---------------------|---------|---------------|
| M3_stored_ssd | 0.0% | 20.6% | 0.0% | 0.0% |
| M4_contour | 42.9% | 42.9% | 14.3% | 58.8% |
| M5_hough | 30.0% | 43.3% | 100.0% | 11.8% |
| M6_template_match | 69.0% | 79.3% | 100.0% | 14.7% |

### Per-category (mean px error)

| Method | clear_ball | edge_of_frame | low_contrast |
|--------|---|---|---|
| M3_stored_ssd | 61.6 | 13.4 | 10.2 |
| M4_contour | 151.7 | 41.1 | 18.4 |
| M5_hough | 87.0 | 43.0 | 142.7 |
| M6_template_match | 192.9 | 104.5 | 75.2 |

### Per-video (mean px error)

| Method | 47466ee0 | 5416b28f | 66ee1c8a | 8e39b06f | a3d45095 |
|--------|---|---|---|---|---|
| M3_stored_ssd | 10.1 | 14.8 | 15.7 | 79.9 | 16.6 |
| M4_contour | 14.4 | 174.6 | 18.4 | 151.7 | N/A |
| M5_hough | 28.8 | 41.8 | 82.9 | 93.7 | 134.0 |
| M6_template_match | 139.2 | 37.6 | 116.0 | 215.1 | 230.9 |

---

## 4b. Method Comparison — HOLDOUT set

> **Verdict is based on HOLDOUT set only.**

### Pixel error (vs GT, 1920px-reference)

| Method | Mean | Median | p90 | p95 | n |
|--------|------|--------|-----|-----|---|
| M1_synthetic_raw_tap *(SYNTHETIC)* | 96.2 | 93.9 | 117.1 | 128.6 | 16 |
| M2_human_raw_tap *(SIMULATED)* | 50.6 | 42.7 | 87.5 | 96.8 | 16 |
| M2_human_loupe_tap *(SIMULATED)* | 16.5 | 14.5 | 25.8 | 30.1 | 16 |
| M3_stored_ssd | 39.6 | 23.5 | 88.3 | 101.9 | 16 |
| M4_contour | 105.9 | 102.9 | 199.3 | 213.9 | 6 |
| M5_hough | 71.1 | 81.4 | 103.2 | 105.8 | 14 |
| M6_template_match | 168.3 | 155.2 | 306.3 | 327.8 | 13 |

### Wrong-snap rate & FP/refusal

| Method | Wrong-snap vs M1 | Wrong-snap vs M2_raw | FP rate | False-refusal |
|--------|-----------------|---------------------|---------|---------------|
| M3_stored_ssd | 0.0% | 31.2% | 0.0% | 0.0% |
| M4_contour | 50.0% | 83.3% | 0.0% | 62.5% |
| M5_hough | 21.4% | 78.6% | 100.0% | 12.5% |
| M6_template_match | 76.9% | 92.3% | 100.0% | 18.8% |

### Per-category (mean px error)

| Method | clear_ball | edge_of_frame | low_contrast |
|--------|---|---|---|
| M3_stored_ssd | 64.9 | 14.0 | 16.0 |
| M4_contour | 125.3 | 86.5 | N/A |
| M5_hough | 78.0 | 62.0 | N/A |
| M6_template_match | 218.0 | 88.8 | N/A |

### Per-video (mean px error)

| Method | 47466ee0 | 5416b28f | 66ee1c8a | 8e39b06f | a3d45095 |
|--------|---|---|---|---|---|
| M3_stored_ssd | 8.8 | 18.8 | 16.0 | 64.9 | 12.0 |
| M4_contour | 15.5 | 228.5 | N/A | 125.3 | N/A |
| M5_hough | 42.3 | 66.2 | N/A | 78.0 | 88.6 |
| M6_template_match | 140.6 | 54.2 | N/A | 218.0 | N/A |

---

## 5. Latency — Python/OpenCV (TÉNYLEGESEN MÉRT)

> **iOS VNDetectContoursRequest latency: N/A — POC-2 physical iPhone benchmark required.**

| Method | p50 (ms) | p95 (ms) | n |
|--------|----------|----------|---|
| M3_stored_ssd | 0.0 | 0.0 | 60 |
| M4_contour | 0.2 | 0.4 | 60 |
| M5_hough | 0.5 | 1.4 | 60 |
| M6_template_match | 0.2 | 0.6 | 60 |
---

## 6. Acceptance Gate Evaluation (HOLDOUT set)

> Primary wrong-snap gate uses M2_raw (simulated human tap) as baseline,
> not M1 synthetic. This is stricter and more operationally relevant.

| Gate | Threshold | Best Method | Value | Status |
|------|-----------|-------------|-------|--------|
| Improves mean error vs M1 synthetic | < 96.2px | M3_stored_ssd | 39.6px | ✅ PASS |
| Wrong-snap rate vs M2_raw | < 5% | M3_stored_ssd | 31.2% | ❌ FAIL |
| No-ball FP rate | < 10% | M3_stored_ssd | 0.0% | ✅ PASS |
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

Gate summary: 3 PASS | 1 FAIL | 0 N/A (Python benchmark only)

---

*Report generated by `04_report.py` | 2026-06-20 11:42 UTC*

> **CONSTRAINT**: No automatic snap production integration. No main merge.
> Both require separate explicit approval after validated POC-1 holdout results.