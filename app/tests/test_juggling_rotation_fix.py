"""
Regression tests for video rotation fix — JTR-01..JTR-12.

Root cause proved in June 2026 audit:
  ffmpeg applies Display Matrix rotation automatically during decode (autorotate).
  build_transcode_command() and build_thumbnail_command() also add an explicit
  transpose filter for the same rotation. Both fire → double rotation → sideways.

Fix:
  1. build_transcode_command(): -noautorotate before -i when needs_vf=True
  2. build_thumbnail_command(): -noautorotate before -i when rotation filters present
  3. metadata_service: tags.rotate is primary; side_data_list is fallback only
     (tags.rotate=90 + side_data=-90 must NOT overwrite to -90)

All tests are pure unit tests (no DB, no network, no ffmpeg binary required).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.juggling.transcode_service import (
    TranscodeSkip,
    build_thumbnail_command,
    build_transcode_command,
)
from app.services.juggling.metadata_service import extract_server_metadata


# ── Helpers ───────────────────────────────────────────────────────────────────

DUMMY_IN  = Path("/tmp/input.mp4")
DUMMY_OUT = Path("/tmp/output.mp4")
DUMMY_THUMB = Path("/tmp/thumb.jpg")


def _probe(width, height, tags_rotate=None, sd_rotation=None,
           fps="30/1", has_audio=False):
    """Build a minimal ffprobe-shaped dict for extract_server_metadata()."""
    tags = {}
    if tags_rotate is not None:
        tags["rotate"] = str(tags_rotate)
    side_data = []
    if sd_rotation is not None:
        side_data.append({"side_data_type": "Display Matrix", "rotation": sd_rotation})
    streams = [
        {
            "codec_type": "video",
            "width": width,
            "height": height,
            "avg_frame_rate": fps,
            "tags": tags,
            "side_data_list": side_data,
        }
    ]
    if has_audio:
        streams.append({"codec_type": "audio"})
    return {"streams": streams, "format": {}}


# ── metadata_service tests (JTR-01..JTR-04) ──────────────────────────────────

def test_jtr01_tags_rotate_wins_over_side_data_negative():
    """JTR-01: iPhone portrait .MOV: tags.rotate=90 + side_data=-90 → rotation=90.
    Before the fix, side_data loop overwrote 90 with -90, producing an unknown
    rotation value that build_transcode_command could not map to a transpose filter.
    """
    probe = _probe(1920, 1080, tags_rotate=90, sd_rotation=-90)
    meta = extract_server_metadata(probe)
    assert meta["rotation"] == 90, (
        "tags.rotate=90 must survive when side_data=-90; "
        "side_data must not overwrite the primary tags value"
    )


def test_jtr02_side_data_positive_used_when_no_tags():
    """JTR-02: iOS AVAssetExport: no tags.rotate, side_data=90 → rotation=90.
    This is the current production path (iOS pre-upload export).
    """
    probe = _probe(628, 640, tags_rotate=None, sd_rotation=90)
    meta = extract_server_metadata(probe)
    assert meta["rotation"] == 90


def test_jtr03_side_data_negative_normalized_when_no_tags():
    """JTR-03: No tags.rotate, side_data=-90 → rotation=270 (normalized 0-360).
    -90 % 360 = 270 — ensures build_transcode_command hits the transpose=2 branch.
    """
    probe = _probe(1920, 1080, tags_rotate=None, sd_rotation=-90)
    meta = extract_server_metadata(probe)
    assert meta["rotation"] == 270, (
        "side_data=-90 with no tags.rotate must normalize to 270, not stay at -90"
    )


def test_jtr04_no_rotation_metadata_gives_zero():
    """JTR-04: No tags.rotate, no side_data → rotation=0 (landscape / no correction)."""
    probe = _probe(1280, 720)
    meta = extract_server_metadata(probe)
    assert meta["rotation"] == 0


# ── build_transcode_command tests (JTR-05..JTR-09) ───────────────────────────

def test_jtr05_transcode_rotation90_has_noautorotate_before_i():
    """JTR-05: rotation=90 → -noautorotate appears in cmd and comes before -i."""
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=90, fps=30.0, height=640, has_audio=False,
    )
    assert "-noautorotate" in cmd, "-noautorotate must be present for rotation=90"
    assert cmd.index("-noautorotate") < cmd.index("-i"), (
        "-noautorotate must come before -i (input option, not output option)"
    )


def test_jtr06_transcode_rotation90_generates_transpose1():
    """JTR-06: rotation=90 → -vf transpose=1 in command."""
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=90, fps=30.0, height=640, has_audio=False,
    )
    idx = cmd.index("-vf")
    assert "transpose=1" in cmd[idx + 1], f"-vf should contain transpose=1, got: {cmd[idx+1]!r}"


def test_jtr07_transcode_rotation270_generates_transpose2():
    """JTR-07: rotation=270 → -vf transpose=2 in command."""
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=270, fps=30.0, height=640, has_audio=False,
    )
    assert "-noautorotate" in cmd
    idx = cmd.index("-vf")
    assert "transpose=2" in cmd[idx + 1]


def test_jtr08_transcode_rotation180_generates_double_transpose():
    """JTR-08: rotation=180 → -vf transpose=1,transpose=1 in command."""
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=180, fps=30.0, height=640, has_audio=False,
    )
    assert "-noautorotate" in cmd
    idx = cmd.index("-vf")
    assert "transpose=1,transpose=1" in cmd[idx + 1]


def test_jtr09_transcode_audio_only_strip_no_noautorotate():
    """JTR-09: rotation=0, has_audio=True → audio-only strip, -c:v copy, no -noautorotate.
    Stream copy bypasses the decoder so autorotate never fires; adding -noautorotate
    to a -c:v copy command would be meaningless and should not be present.
    """
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=0, fps=30.0, height=640, has_audio=True,
    )
    assert "-noautorotate" not in cmd, (
        "audio-only strip uses -c:v copy; -noautorotate must not be added"
    )
    assert "-c:v" in cmd
    idx = cmd.index("-c:v")
    assert cmd[idx + 1] == "copy"


def test_jtr10_transcode_skip_no_vf_no_audio():
    """JTR-10: rotation=0, fps≤30, height≤720, no audio → TranscodeSkip."""
    with pytest.raises(TranscodeSkip):
        build_transcode_command(
            DUMMY_IN, DUMMY_OUT,
            rotation=0, fps=29.9, height=640, has_audio=False,
        )


def test_jtr11_transcode_map_metadata_strips_rotation_tag():
    """-map_metadata -1 must appear in the full transcode command to strip the
    Display Matrix from the output (the pixels are now physically rotated; keeping
    the tag would cause AVPlayer to rotate an already-correct video a second time).
    """
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=90, fps=30.0, height=640, has_audio=False,
    )
    assert "-map_metadata" in cmd
    idx = cmd.index("-map_metadata")
    assert cmd[idx + 1] == "-1"


# ── build_thumbnail_command tests (JTR-12) ────────────────────────────────────

def test_jtr12_thumbnail_rotation90_has_noautorotate_before_i():
    """JTR-12: thumbnail with rotation=90 → -noautorotate before -i, -vf transpose=1."""
    cmd = build_thumbnail_command(DUMMY_IN, DUMMY_THUMB, rotation=90)
    assert "-noautorotate" in cmd, "-noautorotate must be present for thumbnail rotation=90"
    assert cmd.index("-noautorotate") < cmd.index("-i"), (
        "-noautorotate must come before -i"
    )
    idx = cmd.index("-vf")
    assert "transpose=1" in cmd[idx + 1]


def test_jtr12b_thumbnail_rotation0_no_noautorotate():
    """JTR-12b: thumbnail with rotation=0 → no -noautorotate, no -vf."""
    cmd = build_thumbnail_command(DUMMY_IN, DUMMY_THUMB, rotation=0)
    assert "-noautorotate" not in cmd
    assert "-vf" not in cmd
