"""Tests for challenge_snapshot_service — P0 Fairness Snapshot.

FAIR-01: generate_ms_snapshot() returns correct phase/round structure
FAIR-02: MS sequence lengths match sequence_length per phase
FAIR-03: MS tile indices are valid (0 ≤ idx < grid_tiles)
FAIR-04: generate_tt_snapshot() returns correct structure
FAIR-05: TT initial_positions are within arena bounds
FAIR-06: TT target_index is a valid object index
FAIR-07: validate_ms_snapshot() returns False on malformed snapshot
FAIR-08: validate_tt_snapshot() returns False on malformed snapshot
FAIR-09: generate_snapshot("memory_sequence", ...) returns correct game_code
FAIR-10: generate_snapshot() raises ValueError for unknown game_code
FAIR-11: validate_challenge_mode() passes "async" and "live"
FAIR-12: validate_challenge_mode() raises ValueError for invalid mode
"""
from __future__ import annotations

import math
import pytest

from app.services.challenge_snapshot_service import (
    generate_ms_snapshot,
    generate_snapshot,
    generate_tt_snapshot,
    validate_challenge_mode,
    validate_ms_snapshot,
    validate_tt_snapshot,
)

# ── Minimal game config fixtures ───────────────────────────────────────────────

_MS_CONFIG = {
    "grid_rows": 3,
    "grid_cols": 4,
    "phases": [
        {"phase": 0, "sequence_length": 3, "rounds": 3,
         "show_ms_per_item": 800, "isi_ms": 500, "recall_window_ms": 8000},
        {"phase": 1, "sequence_length": 5, "rounds": 3,
         "show_ms_per_item": 650, "isi_ms": 400, "recall_window_ms": 13000},
        {"phase": 2, "sequence_length": 7, "rounds": 3,
         "show_ms_per_item": 500, "isi_ms": 300, "recall_window_ms": 18000},
    ],
}

_TT_CONFIG = {
    "phases": [
        {"phase": 0, "rounds": 3, "object_count": 3},
        {"phase": 1, "rounds": 3, "object_count": 4},
        {"phase": 2, "rounds": 3, "object_count": 5},
    ],
    "difficulties": {
        "easy": {
            "phases": [
                {"phase": 0, "rounds": 3, "object_count": 3,
                 "object_speed": 1.00, "highlight_ms": 1500, "tracking_ms": 4000,
                 "window_ms": 3000, "distractor_flash": 0},
                {"phase": 1, "rounds": 3, "object_count": 4,
                 "object_speed": 1.15, "highlight_ms": 1500, "tracking_ms": 5000,
                 "window_ms": 3000, "distractor_flash": 0},
                {"phase": 2, "rounds": 3, "object_count": 5,
                 "object_speed": 1.30, "highlight_ms": 1500, "tracking_ms": 6000,
                 "window_ms": 3000, "distractor_flash": 0},
            ],
            "difficulty_multiplier": 1.00,
        },
        "medium": {
            "phases": [
                {"phase": 0, "rounds": 3, "object_count": 5,
                 "object_speed": 1.60, "highlight_ms": 1500, "tracking_ms": 5000,
                 "window_ms": 2500, "distractor_flash": 1},
            ],
            "difficulty_multiplier": 1.30,
        },
    },
}


# ── FAIR-01 ────────────────────────────────────────────────────────────────────

def test_fair_01_ms_phase_round_structure():
    snap = generate_ms_snapshot(_MS_CONFIG)
    assert snap["game_code"] == "memory_sequence"
    assert snap["grid_tiles"] == 12
    phases = snap["phases"]
    assert len(phases) == 3
    for i, ph in enumerate(phases):
        assert ph["phase"] == i + 1
        assert len(ph["rounds"]) == 3
        for j, rd in enumerate(ph["rounds"]):
            assert rd["round"] == j + 1
            assert "sequence" in rd


# ── FAIR-02 ────────────────────────────────────────────────────────────────────

def test_fair_02_ms_sequence_lengths():
    snap = generate_ms_snapshot(_MS_CONFIG)
    expected_lengths = [3, 5, 7]
    for pi, ph in enumerate(snap["phases"]):
        for rd in ph["rounds"]:
            assert len(rd["sequence"]) == expected_lengths[pi], (
                f"phase {pi+1} sequence length mismatch"
            )


# ── FAIR-03 ────────────────────────────────────────────────────────────────────

def test_fair_03_ms_tile_indices_valid():
    snap = generate_ms_snapshot(_MS_CONFIG)
    grid_tiles = snap["grid_tiles"]
    for ph in snap["phases"]:
        for rd in ph["rounds"]:
            for idx in rd["sequence"]:
                assert isinstance(idx, int)
                assert 0 <= idx < grid_tiles, f"tile index {idx} out of range [0, {grid_tiles})"


# ── FAIR-04 ────────────────────────────────────────────────────────────────────

def test_fair_04_tt_structure():
    snap = generate_tt_snapshot(_TT_CONFIG, "easy")
    assert snap["game_code"] == "target_tracking"
    assert snap["difficulty"] == "easy"
    assert "arena" in snap
    phases = snap["phases"]
    assert len(phases) == 3
    for i, ph in enumerate(phases):
        assert ph["phase"] == i + 1
        assert len(ph["rounds"]) == 3
        for j, rd in enumerate(ph["rounds"]):
            assert rd["round"] == j + 1
            assert "target_index" in rd
            assert "initial_positions" in rd
            assert "initial_angles" in rd


# ── FAIR-05 ────────────────────────────────────────────────────────────────────

def test_fair_05_tt_positions_within_bounds():
    snap = generate_tt_snapshot(_TT_CONFIG, "easy")
    arena_w = snap["arena"]["width"]
    arena_h = snap["arena"]["height"]
    for ph in snap["phases"]:
        for rd in ph["rounds"]:
            for pos in rd["initial_positions"]:
                assert 0 <= pos["x"] <= arena_w, f"x={pos['x']} out of [0, {arena_w}]"
                assert 0 <= pos["y"] <= arena_h, f"y={pos['y']} out of [0, {arena_h}]"


# ── FAIR-06 ────────────────────────────────────────────────────────────────────

def test_fair_06_tt_target_index_valid():
    snap = generate_tt_snapshot(_TT_CONFIG, "easy")
    for pi, ph in enumerate(snap["phases"]):
        object_count = ph["object_count"]
        for rd in ph["rounds"]:
            ti = rd["target_index"]
            assert isinstance(ti, int)
            assert 0 <= ti < object_count, (
                f"phase {pi+1}: target_index={ti} out of range [0, {object_count})"
            )


# ── FAIR-07 ────────────────────────────────────────────────────────────────────

def test_fair_07_validate_ms_snapshot_rejects_malformed():
    # Not a dict
    assert validate_ms_snapshot("bad") is False

    # Wrong game_code
    assert validate_ms_snapshot({"game_code": "other", "grid_tiles": 12, "phases": []}) is False

    # Missing grid_tiles
    assert validate_ms_snapshot({"game_code": "memory_sequence", "phases": []}) is False

    # Empty phases
    assert validate_ms_snapshot({
        "game_code": "memory_sequence", "grid_tiles": 12, "phases": []
    }) is False

    # Sequence length mismatch
    bad = {
        "game_code": "memory_sequence",
        "grid_tiles": 12,
        "phases": [
            {"phase": 1, "sequence_length": 3, "rounds": [{"round": 1, "sequence": [0, 1]}]}
        ]
    }
    assert validate_ms_snapshot(bad) is False

    # Out-of-range tile index
    bad2 = {
        "game_code": "memory_sequence",
        "grid_tiles": 12,
        "phases": [
            {"phase": 1, "sequence_length": 2, "rounds": [{"round": 1, "sequence": [0, 15]}]}
        ]
    }
    assert validate_ms_snapshot(bad2) is False


def test_fair_07b_validate_ms_snapshot_accepts_valid():
    snap = generate_ms_snapshot(_MS_CONFIG)
    assert validate_ms_snapshot(snap) is True


# ── FAIR-08 ────────────────────────────────────────────────────────────────────

def test_fair_08_validate_tt_snapshot_rejects_malformed():
    # Not a dict
    assert validate_tt_snapshot(None) is False

    # Wrong game_code
    assert validate_tt_snapshot({
        "game_code": "memory_sequence", "arena": {"width": 480, "height": 360}, "phases": []
    }) is False

    # Missing arena
    assert validate_tt_snapshot({
        "game_code": "target_tracking", "phases": []
    }) is False

    # Empty phases
    assert validate_tt_snapshot({
        "game_code": "target_tracking",
        "arena": {"width": 480, "height": 360},
        "phases": [],
    }) is False

    # target_index out of range
    bad = {
        "game_code": "target_tracking",
        "arena": {"width": 480, "height": 360},
        "phases": [{
            "phase": 1,
            "object_count": 3,
            "rounds": [{
                "round": 1,
                "target_index": 5,
                "initial_positions": [{"x": 100, "y": 100}] * 3,
                "initial_angles": [0.0, 1.0, 2.0],
            }]
        }]
    }
    assert validate_tt_snapshot(bad) is False


def test_fair_08b_validate_tt_snapshot_accepts_valid():
    snap = generate_tt_snapshot(_TT_CONFIG, "easy")
    assert validate_tt_snapshot(snap) is True


# ── FAIR-09 ────────────────────────────────────────────────────────────────────

def test_fair_09_generate_snapshot_ms_dispatch():
    snap = generate_snapshot("memory_sequence", _MS_CONFIG)
    assert snap["game_code"] == "memory_sequence"
    assert "phases" in snap
    assert "grid_tiles" in snap


# ── FAIR-10 ────────────────────────────────────────────────────────────────────

def test_fair_10_unknown_game_code_raises():
    with pytest.raises(ValueError, match="Unknown game_code"):
        generate_snapshot("unknown_game", {})


# ── FAIR-11 ────────────────────────────────────────────────────────────────────

def test_fair_11_validate_challenge_mode_valid():
    assert validate_challenge_mode("async") == "async"
    assert validate_challenge_mode("live") == "live"


# ── FAIR-12 ────────────────────────────────────────────────────────────────────

def test_fair_12_validate_challenge_mode_invalid():
    with pytest.raises(ValueError):
        validate_challenge_mode("realtime")
    with pytest.raises(ValueError):
        validate_challenge_mode("")
    with pytest.raises(ValueError):
        validate_challenge_mode("ASYNC")


# ── Additional: TT medium difficulty dispatches correctly ─────────────────────

def test_tt_medium_difficulty_dispatches():
    snap = generate_snapshot("target_tracking", _TT_CONFIG, difficulty_level="medium")
    assert snap["game_code"] == "target_tracking"
    assert snap["difficulty"] == "medium"
    # medium config has only 1 phase in our fixture
    assert len(snap["phases"]) == 1
    assert snap["phases"][0]["object_count"] == 5


# ── Additional: MS with non-default grid size ─────────────────────────────────

def test_ms_non_default_grid():
    cfg = {
        "grid_rows": 4,
        "grid_cols": 4,
        "phases": [{"phase": 0, "sequence_length": 4, "rounds": 2}],
    }
    snap = generate_ms_snapshot(cfg)
    assert snap["grid_tiles"] == 16
    for rd in snap["phases"][0]["rounds"]:
        assert len(rd["sequence"]) == 4
        for idx in rd["sequence"]:
            assert 0 <= idx < 16


# ── Additional: generate_ms_snapshot raises on missing phases ─────────────────

def test_ms_raises_on_empty_phases():
    with pytest.raises(ValueError, match="missing 'phases'"):
        generate_ms_snapshot({})


# ── Additional: generate_tt_snapshot falls back to top-level phases ───────────

def test_tt_fallback_to_toplevel_phases():
    cfg_no_diff = {
        "phases": [{"phase": 0, "rounds": 2, "object_count": 3}]
    }
    snap = generate_tt_snapshot(cfg_no_diff, "easy")
    assert snap["game_code"] == "target_tracking"
    assert len(snap["phases"]) == 1


# ══════════════════════════════════════════════════════════════════════════════
# TT-FAIR tests — direction_changes + flash_schedule (PR-L1 / Option C)
# ══════════════════════════════════════════════════════════════════════════════

# Fixtures used by TT-FAIR tests.
# Hard config: direction change enabled (interval 2000 ms), 2 distractor flashes,
# tracking 6000 ms → expected n_changes = ceil(6000/2000)+1 = 4 per round.
_TT_HARD_CONFIG = {
    "difficulties": {
        "hard": {
            "phases": [
                {
                    "phase": 0, "rounds": 2, "object_count": 4,
                    "object_speed": 2.0, "highlight_ms": 1000,
                    "tracking_ms": 6000, "window_ms": 2500,
                    "distractor_flash": 2,
                },
            ],
            "difficulty_multiplier": 1.50,
            "direction_change": {
                "enabled": True,
                "interval_ms": 2000,
            },
            "flash_config": {
                "flash_duration_ms": 400,
                "flash_gap_ms": 500,
                "max_concurrent_flashes": 1,
                "allow_repeat_flash": False,
                "repeat_gap_ms": 2000,
            },
        },
    }
}

# Easy config without direction_change / flash → direction_changes = [] per round.
_TT_EASY_NO_DC = {
    "difficulties": {
        "easy": {
            "phases": [
                {
                    "phase": 0, "rounds": 2, "object_count": 3,
                    "object_speed": 1.0, "highlight_ms": 1500,
                    "tracking_ms": 4000, "window_ms": 3000,
                    "distractor_flash": 0,
                },
            ],
            "difficulty_multiplier": 1.00,
        },
    }
}


# ── TT-FAIR-01 ────────────────────────────────────────────────────────────────

def test_tt_fair_01_direction_changes_key_present_when_enabled():
    snap = generate_tt_snapshot(_TT_HARD_CONFIG, "hard")
    for ph in snap["phases"]:
        for rd in ph["rounds"]:
            assert "direction_changes" in rd, "direction_changes key missing from round"


# ── TT-FAIR-02 ────────────────────────────────────────────────────────────────

def test_tt_fair_02_direction_changes_length():
    snap = generate_tt_snapshot(_TT_HARD_CONFIG, "hard")
    # tracking_ms=6000, interval_ms=2000 → ceil(6000/2000)+1 = 4
    expected_n = math.ceil(6000 / 2000) + 1
    for ph in snap["phases"]:
        for rd in ph["rounds"]:
            assert len(rd["direction_changes"]) == expected_n, (
                f"expected {expected_n} direction-change entries, "
                f"got {len(rd['direction_changes'])}"
            )


# ── TT-FAIR-03 ────────────────────────────────────────────────────────────────

def test_tt_fair_03_direction_changes_angles_valid():
    snap = generate_tt_snapshot(_TT_HARD_CONFIG, "hard")
    obj_count = snap["phases"][0]["object_count"]
    for ph in snap["phases"]:
        for rd in ph["rounds"]:
            for entry in rd["direction_changes"]:
                assert len(entry) == obj_count, (
                    f"direction-change entry has {len(entry)} angles, expected {obj_count}"
                )
                for angle in entry:
                    assert isinstance(angle, float)
                    assert 0.0 <= angle <= 2 * math.pi, f"angle {angle} out of [0, 2π]"


# ── TT-FAIR-04 ────────────────────────────────────────────────────────────────

def test_tt_fair_04_direction_changes_empty_when_disabled():
    snap = generate_tt_snapshot(_TT_EASY_NO_DC, "easy")
    for ph in snap["phases"]:
        for rd in ph["rounds"]:
            assert rd["direction_changes"] == [], (
                "direction_changes should be [] when direction_change not configured"
            )


# ── TT-FAIR-05 ────────────────────────────────────────────────────────────────

def test_tt_fair_05_flash_schedule_key_present():
    snap = generate_tt_snapshot(_TT_HARD_CONFIG, "hard")
    for ph in snap["phases"]:
        for rd in ph["rounds"]:
            assert "flash_schedule" in rd, "flash_schedule key missing from round"


# ── TT-FAIR-06 ────────────────────────────────────────────────────────────────

def test_tt_fair_06_flash_schedule_entry_required_keys():
    snap = generate_tt_snapshot(_TT_HARD_CONFIG, "hard")
    required = {"distractor_index", "t_offset_ms", "duration_ms", "color"}
    for ph in snap["phases"]:
        for rd in ph["rounds"]:
            for ev in rd["flash_schedule"]:
                missing = required - ev.keys()
                assert not missing, f"flash_schedule entry missing keys: {missing}"
                assert ev["t_offset_ms"] >= 0
                assert ev["duration_ms"] > 0


# ── TT-FAIR-07 ────────────────────────────────────────────────────────────────

def test_tt_fair_07_flash_schedule_excludes_target():
    snap = generate_tt_snapshot(_TT_HARD_CONFIG, "hard")
    for ph in snap["phases"]:
        for rd in ph["rounds"]:
            target = rd["target_index"]
            for ev in rd["flash_schedule"]:
                assert ev["distractor_index"] != target, (
                    f"target_index={target} appears as distractor in flash_schedule"
                )


# ── TT-FAIR-08 ────────────────────────────────────────────────────────────────

def test_tt_fair_08_validate_tt_snapshot_accepts_new_format():
    snap = generate_tt_snapshot(_TT_HARD_CONFIG, "hard")
    assert validate_tt_snapshot(snap) is True


# ── TT-FAIR-09 ────────────────────────────────────────────────────────────────

def test_tt_fair_09_validate_rejects_wrong_direction_changes_length():
    snap = generate_tt_snapshot(_TT_HARD_CONFIG, "hard")
    # Corrupt first round's first direction_changes entry to wrong length
    snap["phases"][0]["rounds"][0]["direction_changes"][0] = [1.0]  # wrong length (need 4)
    assert validate_tt_snapshot(snap) is False


# ── TT-FAIR-10 ────────────────────────────────────────────────────────────────

def test_tt_fair_10_validate_backward_compat_old_snapshot():
    """Old snapshots without direction_changes/flash_schedule must still pass."""
    old_snap = {
        "game_code":  "target_tracking",
        "difficulty": "easy",
        "arena":      {"width": 480, "height": 360},
        "phases": [{
            "phase": 1,
            "object_count": 3,
            "rounds": [{
                "round": 1,
                "target_index": 0,
                "initial_positions": [{"x": 100, "y": 100}, {"x": 200, "y": 200}, {"x": 300, "y": 150}],
                "initial_angles": [1.0, 2.0, 3.0],
                # No direction_changes, no flash_schedule — legacy format
            }],
        }],
    }
    assert validate_tt_snapshot(old_snap) is True


# ── TT-FAIR-11 ────────────────────────────────────────────────────────────────

def test_tt_fair_11_two_calls_produce_different_values():
    snap1 = generate_tt_snapshot(_TT_HARD_CONFIG, "hard")
    snap2 = generate_tt_snapshot(_TT_HARD_CONFIG, "hard")
    # With overwhelming probability angles differ; structural keys must match.
    r1 = snap1["phases"][0]["rounds"][0]
    r2 = snap2["phases"][0]["rounds"][0]
    assert "direction_changes" in r1 and "direction_changes" in r2
    assert "flash_schedule" in r1 and "flash_schedule" in r2
    # The two snapshots should not be identical (probability of collision ≈ 0)
    assert snap1 != snap2, "Two independent snapshots should not be identical"


# ── TT-FAIR-12..14: Template pattern verification ─────────────────────────────
# These tests confirm the challenge-mode fairness branches exist in the JS
# by searching the rendered template source.  They are white-box but critical:
# if someone removes or renames the key, the test catches it before runtime.

import pathlib

_TT_TEMPLATE = pathlib.Path(__file__).parents[3] / "app" / "templates" / "virtual_training_target_tracking.html"


def test_tt_fair_12_template_uses_snap_direction_changes():
    src = _TT_TEMPLATE.read_text(encoding="utf-8")
    assert "snapRd.direction_changes" in src, (
        "trackingPhase() must reference snapRd.direction_changes for challenge fairness"
    )


def test_tt_fair_13_template_uses_snap_flash_schedule():
    src = _TT_TEMPLATE.read_text(encoding="utf-8")
    assert "snapRd.flash_schedule" in src, (
        "trackingPhase() must reference snapRd.flash_schedule for challenge fairness"
    )


def test_tt_fair_14_template_retains_generate_flash_schedule_fallback():
    src = _TT_TEMPLATE.read_text(encoding="utf-8")
    assert "generateFlashSchedule(" in src, (
        "generateFlashSchedule() fallback must be retained for normal training mode"
    )
