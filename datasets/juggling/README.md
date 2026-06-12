# Juggling Detection Dataset

## Purpose

This directory contains the ground-truth annotation dataset for the LFA Juggling
detection pipeline. It supports two goals:

**Eval / Acceptance Dataset:** 20+ annotated clips used to measure detection
accuracy during Phase B (ball detection), Phase C (pose), Phase D (contact),
and Phase E (juggling count).

**Training Dataset (future):** The same clips — extended with frame-level
bounding box and contact-event annotations — form the training set for the
self-trained ball detector.

## Directory Layout

```
datasets/juggling/
├── README.md                      ← this file
├── licence_policy.md              ← no-paid-model policy (non-negotiable)
├── annotation_schema_v1.json      ← JSON schema for all annotation files
├── dataset_manifest.json          ← inventory of all videos + checksums
├── label_workflow.md              ← step-by-step labeling instructions
├── self_training_roadmap.md       ← Phase B0/B1/B2 training plan
├── annotations/                   ← one JSON per video (schema v1)
│   └── .gitkeep
├── benchmark_results/             ← Phase B/C/D/E benchmark reports (JSON)
│   └── .gitkeep
└── sample/                        ← tiny synthetic clip for CI (< 100 KB)
    └── .gitkeep
```

## Storage Policy (MANDATORY)

| Component | Location | Committed to git? |
|---|---|---|
| Video files (`*.mp4`, `*.mov`) | `datasets/juggling/videos/` local only, mirrored on Seafile | **NO** |
| Annotation JSON files | `datasets/juggling/annotations/` | **YES** |
| Manifest + schema | `datasets/juggling/` | **YES** |
| Benchmark result JSONs | `datasets/juggling/benchmark_results/` | **YES** |
| Trained models (`*.onnx`, `*.pt`) | `local_models/juggling/` gitignored | **NO** |
| Synthetic sample clip | `datasets/juggling/sample/` (< 100 KB) | **YES** |

Video files are referenced by `checksum_sha256` in `dataset_manifest.json`.
If a video file is modified, its checksum will differ — the annotation is
invalidated and must be re-verified.

## Quick Start: Annotating a New Video

1. Record or copy the video to `datasets/juggling/videos/<video_id>.mp4`
2. Copy `annotation_schema_v1.json` as a template to `annotations/<video_id>.json`
3. Fill in all required fields (see `label_workflow.md` for step-by-step)
4. Update `dataset_manifest.json` with the new entry
5. Commit only the JSON files — never the video

## Phase A Acceptance Criteria

- Minimum 20 annotated clips
- Difficulty split: easy ≥ 5 / medium ≥ 10 / hard ≥ 5
- Every clip: `total_juggling_count`, `body_parts_used`, `expected_validity` filled
- At least 5 clips with a second-annotator cross-check
- `dataset_manifest.json` has `checksum_sha256` for every video
- No video files committed to git
