"""Orientation-aware aspect-ratio gate tests (2026-07-04 physical-run fix).

The first passing tricamera capture run FAILED only on the unconditional
"effective aspect ratio is 16:9" assertion: the app correctly encodes the
sensor's landscape 1280x720 buffer and bakes portrait rotation into
preferredTransform (verified with ffprobe on the pulled .mov files —
encoded 1280x720 + rotation -90 => effective display 720x1280 = 9:16).
Under the RC-checklist J-section portrait mandate (issue #357) the 16:9
effective expectation was therefore unsatisfiable while the recording was
in fact correct.

These tests pin the corrected gate semantics:
  - portrait metadata  -> expected effective aspect 9:16 (PASSes on 9:16)
  - portrait metadata  -> a 16:9 EFFECTIVE aspect now FAILs (that would mean
    the rotation was NOT baked in)
  - landscape metadata -> expected effective aspect 16:9 (PASSes on 16:9)
  - the encoded-buffer check stays 16:9 regardless of orientation
  - the GoPro preview gate (_aspect_ratio_matches default 16:9) is unchanged
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from mc1_regression.scenarios import (  # noqa: E402
    _aspect_ratio_matches,
    _effective_aspect_gate,
    _encoded_aspect_is_16_9,
    _expected_effective_aspect,
)


def _portrait_meta(effective="9:16"):
    return {
        "fileOrientationCoarse": "portrait",
        "effectiveAspectRatio": effective,
        "actualResolution": "1280x720",
    }


def _landscape_meta(effective="16:9"):
    return {
        "fileOrientationCoarse": "landscape",
        "effectiveAspectRatio": effective,
        "actualResolution": "1280x720",
    }


# ── expected-aspect mapping ──────────────────────────────────────────────────

def test_expected_effective_aspect_portrait_is_9_16():
    assert _expected_effective_aspect("portrait") == (9, 16)


def test_expected_effective_aspect_landscape_is_16_9():
    assert _expected_effective_aspect("landscape") == (16, 9)


def test_expected_effective_aspect_unknown_has_no_expectation():
    assert _expected_effective_aspect("unknown") is None
    assert _expected_effective_aspect(None) is None


# ── effective-aspect gate ────────────────────────────────────────────────────

def test_portrait_9_16_effective_passes():
    ok, expected = _effective_aspect_gate(_portrait_meta("9:16"))
    assert ok is True
    assert expected == "9:16"


def test_portrait_16_9_effective_fails():
    # A 16:9 EFFECTIVE aspect on a portrait file would mean the rotation was
    # never baked in — exactly what the old gate wrongly demanded.
    ok, expected = _effective_aspect_gate(_portrait_meta("16:9"))
    assert ok is False
    assert expected == "9:16"


def test_landscape_16_9_effective_passes():
    ok, expected = _effective_aspect_gate(_landscape_meta("16:9"))
    assert ok is True
    assert expected == "16:9"


def test_landscape_9_16_effective_fails():
    ok, _ = _effective_aspect_gate(_landscape_meta("9:16"))
    assert ok is False


def test_unknown_orientation_fails_gate():
    ok, expected = _effective_aspect_gate({"fileOrientationCoarse": "unknown",
                                           "effectiveAspectRatio": "16:9"})
    assert ok is False
    assert expected is None


def test_missing_meta_fails_gate():
    ok, expected = _effective_aspect_gate({})
    assert ok is False
    assert expected is None


# ── encoded-buffer gate (orientation-independent 16:9) ───────────────────────

def test_encoded_1280x720_is_16_9_for_both_orientations():
    assert _encoded_aspect_is_16_9(_portrait_meta()) is True
    assert _encoded_aspect_is_16_9(_landscape_meta()) is True


def test_encoded_portrait_shaped_buffer_fails():
    meta = _portrait_meta()
    meta["actualResolution"] = "720x1280"
    assert _encoded_aspect_is_16_9(meta) is False


def test_encoded_missing_metadata_returns_none_step_skipped():
    assert _encoded_aspect_is_16_9({}) is None
    assert _encoded_aspect_is_16_9({"actualResolution": "garbage"}) is None


# ── GoPro preview gate unchanged ─────────────────────────────────────────────

def test_gopro_preview_gate_unchanged_16_9_passes():
    assert _aspect_ratio_matches("16:9") is True
    # near-16:9 real SPS dims still pass (tolerant numeric compare)
    assert _aspect_ratio_matches("1280:720") is True


def test_gopro_preview_gate_unchanged_non_16_9_fails():
    assert _aspect_ratio_matches("9:16") is False
    assert _aspect_ratio_matches("4:3") is False
    assert _aspect_ratio_matches(None) is False
