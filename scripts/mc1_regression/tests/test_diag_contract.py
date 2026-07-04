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
PROCESSOR_SWIFT = (REPO_ROOT / "ios" / "LFAEducationCenter" / "MultiCamera"
                   / "LivePoseOverlayProcessor.swift")

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from mc1_regression.lib import (  # noqa: E402
    DIAG_FRESHNESS_SKEW_TOLERANCE_S,
    STALE_DIAG_SENTINEL_KEY,
    load_fresh_diag,
)


# ── Contract extraction helpers ──────────────────────────────────────────────

def swift_pose_writer_keys() -> set[str]:
    """Keys PoseOverlayDiagWriter actually emits per panel: the
    diagnosticSnapshot dictionary literal's string keys, plus the
    sourceFramesSeen field panelDict() adds on top."""
    src = PROCESSOR_SWIFT.read_text(encoding="utf-8")
    snap = re.search(r"var diagnosticSnapshot: \[String: Any\] \{\s*\[(.*?)\]\s*\}", src, re.S)
    assert snap, "diagnosticSnapshot dictionary not found in LivePoseOverlayProcessor.swift"
    keys = set(re.findall(r'"(\w+)":', snap.group(1)))
    if re.search(r'd\["sourceFramesSeen"\]\s*=', src):
        keys.add("sourceFramesSeen")
    return keys


def scenario_pose_reader_keys() -> set[str]:
    """Keys the tricamera scenario reads off a per-panel dict (panel.get(...))."""
    src = SCENARIOS_PY.read_text(encoding="utf-8")
    return set(re.findall(r'panel\.get\("(\w+)"', src))


# ── Contract tests ───────────────────────────────────────────────────────────

def test_pose_overlay_reader_keys_are_subset_of_writer_keys():
    writer = swift_pose_writer_keys()
    reader = scenario_pose_reader_keys()
    assert reader, "scenarios.py reads no per-panel keys — extraction regex broke?"
    missing = reader - writer
    assert not missing, (
        f"scenarios.py reads per-panel key(s) {sorted(missing)} that "
        f"PoseOverlayDiagWriter never writes (writer emits: {sorted(writer)}). "
        f"This is exactly the framesReceivedByProcessor bug class — fix the key "
        f"name on one side."
    )


def test_pose_overlay_gate_uses_frames_received():
    """The frame-traffic gate must read the writer's real counter key."""
    assert "framesReceived" in scenario_pose_reader_keys()


def test_phantom_key_framesreceivedbyprocessor_is_gone():
    src = SCENARIOS_PY.read_text(encoding="utf-8")
    assert "framesReceivedByProcessor" not in src, (
        "framesReceivedByProcessor resurfaced in scenarios.py — this key has "
        "never existed in the Swift writer and guarantees a false FAIL."
    )


def test_writer_emits_all_five_diagnostic_counters():
    expected = {"framesReceived", "framesProcessed", "visionDetectionSuccesses",
                "framesWithSkeletonPoints", "lastFrameReceivedAt"}
    assert expected <= swift_pose_writer_keys()


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
