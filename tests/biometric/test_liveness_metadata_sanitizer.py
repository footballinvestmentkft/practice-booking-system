"""
Sanitizer tests — liveness_metadata_sanitizer.py

BMS-01  None input returns None
BMS-02  Empty dict returns None
BMS-03  All allowed fields preserved intact
BMS-04  Known forbidden field device_model is dropped
BMS-05  Known forbidden field ios_version is dropped
BMS-06  Known forbidden field yaw is dropped
BMS-07  Known forbidden field roll is dropped
BMS-08  Known forbidden field landmarks is dropped
BMS-09  Known forbidden field frames is dropped
BMS-10  Known forbidden field face_match_score is dropped
BMS-11  Known forbidden field embedding is dropped
BMS-12  Unknown non-forbidden field also dropped (allow-list policy)
BMS-13  forbidden field triggers WARNING log
BMS-14  Mix: allowed + forbidden — allowed kept, forbidden dropped
BMS-15  challenge_version too long — dropped
BMS-16  steps_completed exceeds max items — dropped
BMS-17  steps_completed contains too-long item — dropped
BMS-18  total_duration_ms exceeds ceiling — dropped
BMS-19  total_duration_ms negative — dropped
BMS-20  total_duration_ms is float, not int — dropped
BMS-21  retry_count exceeds max — dropped
BMS-22  failure_reason not in whitelist — dropped
BMS-23  failure_reason None is preserved as absent (not stored)
BMS-24  All failure_reason whitelist values accepted
BMS-25  non-dict input returns None (no exception)
BMS-26  Result contains no face_match_score key under any input
BMS-27  Result contains no embedding key under any input
BMS-28  Result contains no yaw key under any input
"""
from __future__ import annotations

import logging

import pytest

from app.services.biometric.liveness_metadata_sanitizer import (
    _FAILURE_REASON_WHITELIST,
    sanitize_liveness_metadata,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_VALID = {
    "challenge_version": "v1.0",
    "steps_completed": ["centered", "head_left", "head_right"],
    "total_duration_ms": 12400,
    "retry_count": 1,
    "failure_reason": None,
}


def _clean():
    """Return a copy of _VALID without None values (mirrors real iOS payload)."""
    return {k: v for k, v in _VALID.items() if v is not None}


# ── BMS-01 / BMS-02 ──────────────────────────────────────────────────────────

def test_bms01_none_returns_none():
    assert sanitize_liveness_metadata(None) is None


def test_bms02_empty_dict_returns_none():
    assert sanitize_liveness_metadata({}) is None


# ── BMS-03  All allowed fields ────────────────────────────────────────────────

def test_bms03_all_allowed_fields_preserved():
    payload = {
        "challenge_version": "v1.0",
        "steps_completed": ["centered", "head_left"],
        "total_duration_ms": 5000,
        "retry_count": 0,
        "failure_reason": "timeout",
    }
    result = sanitize_liveness_metadata(payload)
    assert result == payload


# ── BMS-04 … BMS-12  Forbidden / unknown fields dropped ──────────────────────

@pytest.mark.parametrize("forbidden_key,value", [
    ("device_model",     "iPhone 15 Pro"),
    ("ios_version",      "18.2"),
    ("yaw",              0.35),
    ("roll",             -0.12),
    ("landmarks",        [[0.1, 0.2], [0.3, 0.4]]),
    ("frames",           [{"ts": 1000}]),
    ("face_match_score", 0.72),
    ("embedding",        [0.1] * 512),
    ("bounding_box",     {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.6}),
])
def test_forbidden_field_dropped(forbidden_key, value):
    payload = {**_clean(), forbidden_key: value}
    result  = sanitize_liveness_metadata(payload)
    assert result is not None
    assert forbidden_key not in result


def test_bms12_unknown_non_forbidden_field_dropped():
    payload = {**_clean(), "future_field": "some_value"}
    result  = sanitize_liveness_metadata(payload)
    assert "future_field" not in result


# ── BMS-13  WARNING log on known forbidden field ──────────────────────────────

def test_bms13_forbidden_field_triggers_warning(caplog):
    payload = {**_clean(), "device_model": "iPhone 15 Pro"}
    with caplog.at_level(logging.WARNING, logger="app.services.biometric.liveness_metadata_sanitizer"):
        sanitize_liveness_metadata(payload)
    assert any("device_model" in rec.message for rec in caplog.records)


# ── BMS-14  Mix ───────────────────────────────────────────────────────────────

def test_bms14_mix_allowed_and_forbidden():
    payload = {
        "challenge_version": "v1.0",
        "steps_completed": ["centered"],
        "total_duration_ms": 3000,
        "retry_count": 0,
        "device_model": "iPhone 15",
        "yaw": 0.35,
        "ios_version": "18.2",
    }
    result = sanitize_liveness_metadata(payload)
    assert result["challenge_version"] == "v1.0"
    assert result["steps_completed"] == ["centered"]
    assert "device_model" not in result
    assert "yaw" not in result
    assert "ios_version" not in result


# ── BMS-15 … BMS-24  Type / range validation ─────────────────────────────────

def test_bms15_challenge_version_too_long():
    payload = {**_clean(), "challenge_version": "v" + "1" * 25}
    result  = sanitize_liveness_metadata(payload)
    assert "challenge_version" not in result


def test_bms16_steps_completed_exceeds_max():
    payload = {**_clean(), "steps_completed": [f"step_{i}" for i in range(11)]}
    result  = sanitize_liveness_metadata(payload)
    assert "steps_completed" not in result


def test_bms17_steps_completed_item_too_long():
    payload = {**_clean(), "steps_completed": ["x" * 51]}
    result  = sanitize_liveness_metadata(payload)
    assert "steps_completed" not in result


def test_bms18_total_duration_ms_exceeds_ceiling():
    payload = {**_clean(), "total_duration_ms": 200_000}
    result  = sanitize_liveness_metadata(payload)
    assert "total_duration_ms" not in result


def test_bms19_total_duration_ms_negative():
    payload = {**_clean(), "total_duration_ms": -1}
    result  = sanitize_liveness_metadata(payload)
    assert "total_duration_ms" not in result


def test_bms20_total_duration_ms_float_not_accepted():
    payload = {**_clean(), "total_duration_ms": 5000.5}
    result  = sanitize_liveness_metadata(payload)
    assert "total_duration_ms" not in result


def test_bms21_retry_count_exceeds_max():
    payload = {**_clean(), "retry_count": 11}
    result  = sanitize_liveness_metadata(payload)
    assert "retry_count" not in result


def test_bms22_failure_reason_not_in_whitelist():
    payload = {**_clean(), "failure_reason": "sql_injection_payload'; DROP TABLE users;--"}
    result  = sanitize_liveness_metadata(payload)
    assert "failure_reason" not in result


def test_bms23_failure_reason_none_not_stored():
    payload = {"challenge_version": "v1.0", "steps_completed": [], "total_duration_ms": 1000,
               "retry_count": 0, "failure_reason": None}
    result  = sanitize_liveness_metadata(payload)
    assert "failure_reason" not in result


@pytest.mark.parametrize("reason", list(_FAILURE_REASON_WHITELIST))
def test_bms24_failure_reason_whitelist_accepted(reason):
    payload = {**_clean(), "failure_reason": reason}
    result  = sanitize_liveness_metadata(payload)
    assert result["failure_reason"] == reason


# ── BMS-25  Non-dict input ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_input", ["string", 42, [1, 2, 3], True])
def test_bms25_non_dict_returns_none(bad_input):
    assert sanitize_liveness_metadata(bad_input) is None  # type: ignore[arg-type]


# ── BMS-26 / BMS-27 / BMS-28  Forbidden keys structurally absent ─────────────

def test_bms26_face_match_score_never_in_result():
    payload = {**_clean(), "face_match_score": 0.95}
    result  = sanitize_liveness_metadata(payload)
    assert result is None or "face_match_score" not in result


def test_bms27_embedding_never_in_result():
    payload = {**_clean(), "embedding": [0.1] * 512}
    result  = sanitize_liveness_metadata(payload)
    assert result is None or "embedding" not in result


def test_bms28_yaw_never_in_result():
    payload = {**_clean(), "yaw": 0.35}
    result  = sanitize_liveness_metadata(payload)
    assert result is None or "yaw" not in result
