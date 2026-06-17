"""
Regression tests for video rotation fix — JTR-01..JTR-13.

Root cause (proved June 2026 with concrete fc759050 / 1cf14149 videos):

  Round 1 bug (pre-fix):
    ffmpeg autorotate (ON) + explicit transpose=1 = double rotation.
    -map_metadata -1 strips tags.rotate but NOT the tkhd Display Matrix.
    Result: pixels wrong AND metadata stripped.

  Round 2 bug (partial fix 5efa80ad — -noautorotate + transpose):
    Pixels are now physically correct (640×628 for rotation=90 input).
    BUT: -noautorotate preserves the input Display Matrix in the OUTPUT
    tkhd box unchanged. -map_metadata -1 does not touch the tkhd matrix.
    Result: correct pixels, but tkhd still says rotation=90 → AVPlayer
    applies another 90° → sideways on device.

  Correct fix (this commit):
    ffmpeg autorotate (ON, default) reads the Display Matrix from the input,
    physically rotates the decoded frames, AND writes an identity transform to
    the output tkhd box. No explicit transpose needed. No -noautorotate needed.
    rotation parameter in build_transcode_command() is used only to decide
    whether a full re-encode is required (needs_full_transcode), NOT to drive
    a -vf transpose filter.
    build_thumbnail_command() delegates rotation entirely to autorotate.

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
    Before the metadata fix, the side_data loop overwrote 90 with -90, producing
    a value that could not be mapped to a useful rotation decision.
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
    -90 % 360 = 270 — marks needs_rotation=True so autorotate forces a re-encode.
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


# ── build_transcode_command tests (JTR-05..JTR-11) ───────────────────────────

def test_jtr05_transcode_rotation90_no_noautorotate():
    """JTR-05: rotation=90 → -noautorotate must NOT be present.
    Autorotate must be ON (default) so that it physically rotates the frames
    AND resets the tkhd Display Matrix to identity in the output.
    Adding -noautorotate would preserve the input Display Matrix in the output,
    causing AVPlayer to double-rotate correctly-oriented pixels.
    """
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=90, fps=30.0, height=640, has_audio=False,
    )
    assert "-noautorotate" not in cmd, (
        "-noautorotate must NOT be present: autorotate must be ON to reset tkhd matrix"
    )


def test_jtr06_transcode_rotation90_no_transpose_in_vf():
    """JTR-06: rotation=90 → no transpose filter in the command.
    Autorotate handles rotation; explicit transpose would double-rotate.
    """
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=90, fps=30.0, height=640, has_audio=False,
    )
    full_cmd = " ".join(cmd)
    assert "transpose" not in full_cmd, (
        "transpose must not appear in cmd for rotation=90: autorotate handles it"
    )


def test_jtr07_transcode_rotation270_no_transpose():
    """JTR-07: rotation=270 → no transpose filter (autorotate handles it)."""
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=270, fps=30.0, height=640, has_audio=False,
    )
    assert "transpose" not in " ".join(cmd)
    assert "-noautorotate" not in cmd


def test_jtr08_transcode_rotation180_no_transpose():
    """JTR-08: rotation=180 → no transpose filter (autorotate handles it)."""
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=180, fps=30.0, height=640, has_audio=False,
    )
    assert "transpose" not in " ".join(cmd)
    assert "-noautorotate" not in cmd


def test_jtr09_transcode_audio_only_strip_no_noautorotate():
    """JTR-09: rotation=0, has_audio=True → audio-only strip, -c:v copy, no -noautorotate.
    Stream copy bypasses the decoder so autorotate never fires, which is fine
    because rotation=0 means no correction is needed anyway.
    """
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=0, fps=30.0, height=640, has_audio=True,
    )
    assert "-noautorotate" not in cmd
    assert "-c:v" in cmd
    idx = cmd.index("-c:v")
    assert cmd[idx + 1] == "copy"


def test_jtr10_transcode_skip_no_rotation_no_audio():
    """JTR-10: rotation=0, fps≤30, height≤720, no audio → TranscodeSkip."""
    with pytest.raises(TranscodeSkip):
        build_transcode_command(
            DUMMY_IN, DUMMY_OUT,
            rotation=0, fps=29.9, height=640, has_audio=False,
        )


def test_jtr11_transcode_map_metadata_present():
    """-map_metadata -1 must appear in the full transcode command to strip
    rotate tags and any other metadata from the output.
    (The tkhd rotation is already cleared by the re-encode itself.)
    """
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=90, fps=30.0, height=640, has_audio=False,
    )
    assert "-map_metadata" in cmd
    idx = cmd.index("-map_metadata")
    assert cmd[idx + 1] == "-1"


def test_jtr12_transcode_rotation90_forces_full_reencode():
    """JTR-12: rotation=90 (no scale, no fps) → full re-encode with libx264.
    Even without scale/fps filters, rotation requires a re-encode so that
    autorotate physically orients the pixels and the tkhd is written fresh.
    """
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=90, fps=29.0, height=640, has_audio=False,
    )
    assert "-c:v" in cmd
    idx = cmd.index("-c:v")
    assert cmd[idx + 1] == "libx264", (
        "rotation=90 must force libx264 re-encode (not copy or TranscodeSkip)"
    )


def test_jtr13_transcode_scale_only_no_vf_rotation():
    """JTR-13: scale needed (height > target), no rotation → -vf scale but no transpose."""
    cmd = build_transcode_command(
        DUMMY_IN, DUMMY_OUT,
        rotation=0, fps=30.0, height=1080, has_audio=False,
        target_height=720,
    )
    assert "-vf" in cmd
    idx = cmd.index("-vf")
    vf_value = cmd[idx + 1]
    assert "scale" in vf_value
    assert "transpose" not in vf_value


# ── build_thumbnail_command tests (JTR-14..JTR-15) ───────────────────────────

def test_jtr14_thumbnail_rotation90_no_noautorotate_no_vf():
    """JTR-14: thumbnail with rotation=90 → no -noautorotate, no -vf.
    Autorotate handles rotation for thumbnails exactly as for video transcode.
    """
    cmd = build_thumbnail_command(DUMMY_IN, DUMMY_THUMB, rotation=90)
    assert "-noautorotate" not in cmd, "-noautorotate must not appear in thumbnail cmd"
    assert "-vf" not in cmd, "No explicit -vf needed for thumbnail; autorotate handles it"
    assert "-vframes" in cmd
    assert "1" in cmd


def test_jtr15_thumbnail_rotation0_no_noautorotate():
    """JTR-15: thumbnail with rotation=0 → no -noautorotate, no -vf (unchanged case)."""
    cmd = build_thumbnail_command(DUMMY_IN, DUMMY_THUMB, rotation=0)
    assert "-noautorotate" not in cmd
    assert "-vf" not in cmd
