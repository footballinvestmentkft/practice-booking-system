"""Writer↔reader contract tests for the MC1 diag artifacts (P0 hardening).

The 2026-07-04 review found scenarios.py gating the tricamera proof on a JSON
key ("framesReceivedByProcessor") that the Swift writer never emits (it writes
"framesReceived") — every panel gate read 0 and the scenario could never PASS.
These tests pin the cross-language key contract by parsing BOTH sides from
source, so any future rename on either side fails fast in CI instead of on a
physical test day.

Also covers the stale-artifact freshness loader (lib.load_fresh_diag), which
is the runtime half of the same evidence-integrity guarantee.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_PY = REPO_ROOT / "scripts" / "mc1_regression" / "scenarios.py"
IOS_MC = REPO_ROOT / "ios" / "LFAEducationCenter" / "MultiCamera"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from mc1_regression.lib import (  # noqa: E402
    DIAG_FRESHNESS_SKEW_TOLERANCE_S,
    STALE_DIAG_SENTINEL_KEY,
    load_fresh_diag,
)


# ── MC2-PR3 removal guard ─────────────────────────────────────────────────────
#
# The pose-overlay writer↔reader contract was retired when MC2-PR3 deleted the
# MPC live-panel layer (writer, deep link and reader together — key-contract
# rule). These tests keep it retired: an orphaned reader would silently read
# nothing on a physical test day, and a resurrected writer would mean the
# main-thread frame-processing path (the 2026-07-12 iPad freeze RCA) is back.

def test_pose_overlay_reader_is_gone():
    src = SCENARIOS_PY.read_text(encoding="utf-8")
    assert not re.findall(r'panel\.get\("(\w+)"', src), (
        "scenarios.py reads per-panel pose keys, but PoseOverlayDiagWriter was "
        "removed in MC2-PR3 — an orphaned reader can only produce false FAILs."
    )
    assert "pose-overlay-diag" not in src and "pose_overlay_diag" not in src


def test_pose_overlay_writer_stays_removed():
    assert not (IOS_MC / "LivePoseOverlayProcessor.swift").exists(), (
        "LivePoseOverlayProcessor.swift resurfaced — live pose inference on the "
        "instructor dashboard was removed in MC2-PR3 (iPad freeze RCA)."
    )
    for mpc_file in ("CameraStreamService.swift", "CameraFramePublisher.swift",
                     "RemoteCameraView.swift"):
        assert not (IOS_MC / mpc_file).exists(), (
            f"{mpc_file} resurfaced — the MPC streaming layer was fully retired "
            f"in MC2-PR3 (backend-orchestrated no-MPC architecture)."
        )


# ── Freshness loader tests (stale-artifact protection) ───────────────────────

NOW = datetime.now(timezone.utc)


def _write(tmp_path: Path, payload: dict) -> str:
    p = tmp_path / "diag.json"
    p.write_text(json.dumps(payload))
    return str(p)


def test_fresh_diag_is_accepted(tmp_path):
    path = _write(tmp_path, {"timestamp": NOW.isoformat(), "udpPacketsReceived": 5})
    diag, reason = load_fresh_diag(path, NOW - timedelta(seconds=30))
    assert reason is None
    assert diag["udpPacketsReceived"] == 5


def test_sentinel_is_rejected(tmp_path):
    path = _write(tmp_path, {STALE_DIAG_SENTINEL_KEY: True,
                             "invalidated_at": NOW.isoformat(), "run_id": "x"})
    diag, reason = load_fresh_diag(path, NOW - timedelta(seconds=30))
    assert diag is None
    assert "sentinel" in reason


def test_stale_timestamp_is_rejected(tmp_path):
    stale_ts = NOW - timedelta(seconds=DIAG_FRESHNESS_SKEW_TOLERANCE_S + 3600)
    path = _write(tmp_path, {"timestamp": stale_ts.isoformat()})
    diag, reason = load_fresh_diag(path, NOW)
    assert diag is None
    assert "predates scenario start" in reason


def test_missing_timestamp_is_rejected(tmp_path):
    path = _write(tmp_path, {"udpPacketsReceived": 99})
    diag, reason = load_fresh_diag(path, NOW)
    assert diag is None
    assert "no freshness timestamp" in reason


def test_generated_at_accepted_for_skeleton_output(tmp_path):
    """skeleton_output.json uses generated_at (SkeletonProcessor) instead of
    timestamp — both must satisfy the freshness check."""
    path = _write(tmp_path, {"generated_at": NOW.isoformat(), "sampled_frames": 12})
    diag, reason = load_fresh_diag(path, NOW - timedelta(seconds=30))
    assert reason is None
    assert diag["sampled_frames"] == 12


def test_swift_iso8601_zulu_format_parses(tmp_path):
    """ISO8601DateFormatter emits e.g. 2026-07-04T10:20:30Z — the Z suffix must
    parse (datetime.fromisoformat rejects 'Z' before Python 3.11)."""
    zulu = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = _write(tmp_path, {"timestamp": zulu})
    diag, reason = load_fresh_diag(path, NOW - timedelta(seconds=30))
    assert reason is None, f"Zulu-suffixed ISO8601 must be accepted, got: {reason}"


def test_unparseable_json_is_rejected(tmp_path):
    p = tmp_path / "diag.json"
    p.write_text("{not json")
    diag, reason = load_fresh_diag(str(p), NOW)
    assert diag is None
    assert "unreadable" in reason
