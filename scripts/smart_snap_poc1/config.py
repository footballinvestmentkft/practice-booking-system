"""Shared configuration for Smart Snap POC-1 scripts."""
from __future__ import annotations

import os

# ── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))

DATASET_DIR = os.path.join(BASE_DIR, "dataset", "raw")
MANIFEST_PATH = os.path.join(BASE_DIR, "manifest.json")
GROUND_TRUTH_PATH = os.path.join(BASE_DIR, "ground_truth.json")
BENCHMARK_RESULTS_PATH = os.path.join(BASE_DIR, "benchmark_results.json")
REPORT_PATH = os.path.join(BASE_DIR, "report.md")

# ── Database ─────────────────────────────────────────────────────────────────
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DB_NAME = os.environ.get("DB_NAME", "lfa_intern_system")

# ── Dataset targets ──────────────────────────────────────────────────────────
TARGET_POSITIVE_FRAMES = 50    # frames where ball is present (with GT)
TARGET_NO_BALL_FRAMES = 10     # frames where no ball is present
MIN_POSITIVE_FRAMES = 30
MIN_NO_BALL_FRAMES = 5         # minimum for FP-rate evaluation
MAX_VIDEO_FRACTION = 0.40      # no single video may exceed this share of positive frames

# Per-video quotas used in audit
TYPE_A_MAX_PER_VIDEO = 20      # cap on Type A frames from any single video
TYPE_B_PER_VIDEO = 8           # target Type B frames per video (positive)
TYPE_C_PER_VIDEO = 4           # target no-ball frames per video (Type C)

TARGET_CATEGORIES = [
    "clear_ball",
    "motion_blur",
    "partial_occlusion",
    "edge_of_frame",
    "small_ball",
    "low_contrast",
    "no_ball",
]

# ── Frame extraction ─────────────────────────────────────────────────────────
FRAME_JPEG_QUALITY = 85

# ── ROI settings ─────────────────────────────────────────────────────────────
M4_ROI_RATIO = 0.12
M5_ROI_RATIO = 0.15
M6_ROI_RATIO = 0.15
M6_TEMPLATE_RADIUS_PX = 18

# ── M4 contour thresholds ────────────────────────────────────────────────────
M4_CANNY_LOW = 30
M4_CANNY_HIGH = 120
M4_MIN_CONTOUR_AREA_PX = 15
M4_CIRCULARITY_MIN = 0.2

# ── M5 Hough thresholds ──────────────────────────────────────────────────────
M5_HOUGH_DP = 1.2
M5_HOUGH_PARAM1 = 60
M5_HOUGH_PARAM2 = 18
M5_HOUGH_MIN_RADIUS_PX = 4
M5_HOUGH_MAX_RADIUS_PX = 60

# ── M1 synthetic raw tap simulation ─────────────────────────────────────────
M1_SIGMA_NORM = 0.03    # Gaussian sigma in normalised [0,1] coords
M1_N_SIMULATIONS = 100  # Monte Carlo draws per frame

# ── Human annotation simulation (02b_seed_provisional_gt) ───────────────────
# IMPORTANT: These are simulation parameters for infrastructure testing only.
# They do NOT represent real human annotation data.
ANNOT_SIM_R1_SIGMA = 0.008   # σ for Round 1 annotation noise (no model overlay)
ANNOT_SIM_R2_SIGMA = 0.005   # σ for Round 2 annotation noise (model overlay visible)
ANNOT_SIM_RAW_TAP_SIGMA = 0.030  # σ for raw tap simulation (larger — coarser tap)
ANNOT_SIM_LOUPE_SIGMA = 0.006    # σ for loupe tap simulation (finer — zoomed)
ANNOT_SIM_REPS = 3               # repetitions per frame for raw/loupe modes
ANNOT_SIM_SEED_R1 = 101
ANNOT_SIM_SEED_R2 = 202
ANNOT_SIM_SEED_RAW = 303
ANNOT_SIM_SEED_LOUPE = 404

# ── GT agreement ────────────────────────────────────────────────────────────
GT_AGREEMENT_THRESHOLD_PX = 20  # if R1–R2 > this, frame needs R3 review

# ── Auto category thresholds ─────────────────────────────────────────────────
EDGE_THRESHOLD = 0.12            # ball within 12% of any edge → edge_of_frame
LOW_CONF_THRESHOLD = 0.40        # model confidence < 0.40 → low_contrast candidate
HIGH_CONF_THRESHOLD = 0.60       # model confidence ≥ 0.60 → clear_ball candidate
SMALL_BALL_Y_THRESHOLD = 0.18    # ball_y < this → small_ball (ball high/far = small)

# ── Tuning / holdout split ────────────────────────────────────────────────────
HOLDOUT_RATIO = 0.30             # 30% held out for final evaluation
SPLIT_SEED = 42                  # reproducible split
