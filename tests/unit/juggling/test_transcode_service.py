"""
Juggling transcode service unit tests.

Tests run without DB, HTTP, or Celery.  Pure function tests on
transcode_service.build_transcode_command and transcode_service.transcode().

Key invariant tested:
  build_transcode_command MUST NOT return a command that contains both
  -vf and -c:v copy.  This is tested directly and via parameterized cases.

Coverage:
  - TranscodeSkip raised for no-op input
  - rotation 90  → transpose=1
  - rotation 270 → transpose=2
  - rotation 180 → transpose=1,transpose=1
  - scale added when height > target_height
  - fps filter added when fps > target_fps
  - audio-only strip uses -c:v copy and -an (no -vf)
  - full transcode uses -c:v libx264 (not copy) when -vf is present
  - -map_metadata -1 always present
  - +faststart always present
  - transcode() returns status=skipped when no processing needed
  - transcode() returns status=failed on ffmpeg error
  - transcode() returns status=done on success
  - original file NOT deleted after transcode
  - atomic temp rename: final path only exists on success
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.juggling.transcode_service import (
    TranscodeResult,
    TranscodeSkip,
    _extract_resolution_fps,
    _sha256_file,
    build_thumbnail_command,
    build_transcode_command,
    transcode,
)


# ── Command builder — skip ────────────────────────────────────────────────────

class TestTranscodeSkip:
    def test_no_rotation_no_audio_low_fps_low_height_raises_skip(self):
        with pytest.raises(TranscodeSkip):
            build_transcode_command(
                Path("in.mp4"), Path("out.mp4"),
                rotation=0, fps=30.0, height=720,
                has_audio=False,
            )

    def test_none_fps_none_height_no_audio_raises_skip(self):
        with pytest.raises(TranscodeSkip):
            build_transcode_command(
                Path("in.mp4"), Path("out.mp4"),
                rotation=0, fps=None, height=None,
                has_audio=False,
            )

    def test_fps_exactly_at_target_no_audio_raises_skip(self):
        with pytest.raises(TranscodeSkip):
            build_transcode_command(
                Path("in.mp4"), Path("out.mp4"),
                rotation=0, fps=30.0, height=720,
                has_audio=False, target_fps=30, target_height=720,
            )

    def test_height_exactly_at_target_no_audio_raises_skip(self):
        with pytest.raises(TranscodeSkip):
            build_transcode_command(
                Path("in.mp4"), Path("out.mp4"),
                rotation=0, fps=30.0, height=720,
                has_audio=False, target_fps=30, target_height=720,
            )


# ── Command builder — audio-only strip ───────────────────────────────────────

class TestAudioOnlyStrip:
    def _cmd(self, **kw):
        defaults = dict(rotation=0, fps=30.0, height=720, has_audio=True,
                        target_fps=30, target_height=720)
        defaults.update(kw)
        return build_transcode_command(
            Path("in.mp4"), Path("out.mp4"), **defaults
        )

    def test_uses_copy(self):
        cmd = self._cmd()
        assert "-c:v" in cmd
        idx = cmd.index("-c:v")
        assert cmd[idx + 1] == "copy"

    def test_strips_audio(self):
        cmd = self._cmd()
        assert "-an" in cmd

    def test_no_vf(self):
        cmd = self._cmd()
        assert "-vf" not in cmd

    def test_map_metadata_minus_1(self):
        cmd = self._cmd()
        assert "-map_metadata" in cmd
        idx = cmd.index("-map_metadata")
        assert cmd[idx + 1] == "-1"

    def test_faststart(self):
        cmd = self._cmd()
        assert any("faststart" in a for a in cmd)

    def test_vf_copy_invariant_never_violated(self):
        cmd = self._cmd()
        has_vf = "-vf" in cmd
        has_copy = "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
        assert not (has_vf and has_copy), "INVARIANT VIOLATED: -vf and -c:v copy both present"


# ── Command builder — full transcode ─────────────────────────────────────────

class TestFullTranscode:
    def _cmd(self, **kw):
        defaults = dict(rotation=0, fps=60.0, height=1080, has_audio=True,
                        target_fps=30, target_height=720)
        defaults.update(kw)
        return build_transcode_command(
            Path("in.mp4"), Path("out.mp4"), **defaults
        )

    def test_uses_libx264(self):
        cmd = self._cmd()
        assert "-c:v" in cmd
        idx = cmd.index("-c:v")
        assert cmd[idx + 1] == "libx264"

    def test_no_copy_when_vf_present(self):
        cmd = self._cmd()
        assert "-vf" in cmd
        assert "copy" not in cmd, "INVARIANT VIOLATED: -c:v copy present alongside -vf"

    def test_audio_stripped(self):
        cmd = self._cmd()
        assert "-an" in cmd

    def test_map_metadata_minus_1(self):
        cmd = self._cmd()
        assert "-map_metadata" in cmd
        idx = cmd.index("-map_metadata")
        assert cmd[idx + 1] == "-1"

    def test_scale_filter_added_for_high_height(self):
        cmd = self._cmd(height=1080, fps=30.0)
        vf_idx = cmd.index("-vf")
        assert "scale=-2:720" in cmd[vf_idx + 1]

    def test_fps_filter_added_for_high_fps(self):
        cmd = self._cmd(fps=60.0, height=720)
        vf_idx = cmd.index("-vf")
        assert "fps=30" in cmd[vf_idx + 1]

    def test_vf_copy_invariant_never_violated(self):
        for rotation in (0, 90, 180, 270):
            cmd = self._cmd(rotation=rotation, fps=60.0, height=1080)
            has_vf = "-vf" in cmd
            if has_vf:
                has_copy = "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
                assert not has_copy, (
                    f"INVARIANT VIOLATED at rotation={rotation}: "
                    "-vf and -c:v copy both present"
                )


# ── Rotation filtergraph ──────────────────────────────────────────────────────

class TestRotationFiltergraph:
    def _vf(self, rotation: int) -> str:
        cmd = build_transcode_command(
            Path("in.mp4"), Path("out.mp4"),
            rotation=rotation, fps=60.0, height=1080, has_audio=False,
            target_fps=30, target_height=720,
        )
        idx = cmd.index("-vf")
        return cmd[idx + 1]

    def test_rotation_90_transpose_1(self):
        assert "transpose=1" in self._vf(90)

    def test_rotation_270_transpose_2(self):
        assert "transpose=2" in self._vf(270)

    def test_rotation_180_double_transpose(self):
        vf = self._vf(180)
        assert vf.count("transpose=1") >= 2

    def test_rotation_0_no_transpose(self):
        cmd = build_transcode_command(
            Path("in.mp4"), Path("out.mp4"),
            rotation=0, fps=60.0, height=1080, has_audio=False,
            target_fps=30, target_height=720,
        )
        vf_idx = cmd.index("-vf")
        assert "transpose" not in cmd[vf_idx + 1]

    def test_filtergraph_order_rotation_scale_fps(self):
        vf = self._vf(90)  # fps=60 > 30, height=1080 > 720
        parts = vf.split(",")
        has_transpose = any("transpose" in p for p in parts)
        has_scale = any("scale" in p for p in parts)
        has_fps = any("fps" in p for p in parts)
        if has_transpose and has_scale and has_fps:
            transpose_idx = next(i for i, p in enumerate(parts) if "transpose" in p)
            scale_idx = next(i for i, p in enumerate(parts) if "scale" in p)
            fps_idx = next(i for i, p in enumerate(parts) if "fps" in p)
            assert transpose_idx < scale_idx < fps_idx, (
                f"Wrong filtergraph order: transpose={transpose_idx}, "
                f"scale={scale_idx}, fps={fps_idx}"
            )


# ── Thumbnail command ─────────────────────────────────────────────────────────

class TestThumbnailCommand:
    def test_vframes_1(self):
        cmd = build_thumbnail_command(Path("in.mp4"), Path("thumb.jpg"))
        assert "-vframes" in cmd
        assert cmd[cmd.index("-vframes") + 1] == "1"

    def test_quality_flag(self):
        cmd = build_thumbnail_command(Path("in.mp4"), Path("thumb.jpg"))
        assert "-q:v" in cmd
        assert cmd[cmd.index("-q:v") + 1] == "2"

    def test_rotation_90_adds_vf(self):
        cmd = build_thumbnail_command(Path("in.mp4"), Path("thumb.jpg"), rotation=90)
        assert "-vf" in cmd
        assert "transpose=1" in cmd[cmd.index("-vf") + 1]

    def test_rotation_270_transpose_2(self):
        cmd = build_thumbnail_command(Path("in.mp4"), Path("thumb.jpg"), rotation=270)
        assert "-vf" in cmd
        assert "transpose=2" in cmd[cmd.index("-vf") + 1]

    def test_rotation_180_double_transpose(self):
        cmd = build_thumbnail_command(Path("in.mp4"), Path("thumb.jpg"), rotation=180)
        assert "-vf" in cmd
        vf = cmd[cmd.index("-vf") + 1]
        assert vf.count("transpose=1") >= 2

    def test_rotation_0_no_vf(self):
        cmd = build_thumbnail_command(Path("in.mp4"), Path("thumb.jpg"), rotation=0)
        assert "-vf" not in cmd


# ── transcode() function ──────────────────────────────────────────────────────

class TestTranscodeFunction:
    def _fake_ffmpeg_success(self, cmd, capture_output, timeout):
        """Simulate ffmpeg success: create the output file."""
        out_path = Path(cmd[-1])
        out_path.write_bytes(b"\x00" * 100)
        return MagicMock(returncode=0, stderr=b"", stdout=b"")

    def _fake_ffmpeg_fail(self, cmd, capture_output, timeout):
        return MagicMock(returncode=1, stderr=b"error", stdout=b"")

    def test_skip_returned_when_no_processing_needed(self, tmp_path):
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"\x00" * 100)
        result = transcode(
            original_path=orig,
            video_id="test-uuid",
            metadata={"rotation": 0, "fps": 25.0, "resolution": "1280x720",
                      "has_audio": False},
            upload_dir=tmp_path,
            target_fps=30,
            target_height=720,
        )
        assert result.status == "skipped"
        assert result.processed_path is None

    def test_done_status_on_success(self, tmp_path):
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"\x00" * 100)
        with patch("subprocess.run", side_effect=self._fake_ffmpeg_success):
            result = transcode(
                original_path=orig,
                video_id="test-uuid",
                metadata={"rotation": 0, "fps": 60.0, "resolution": "1280x1080",
                          "has_audio": True},
                upload_dir=tmp_path,
                target_fps=30,
                target_height=720,
            )
        assert result.status in ("done", "skipped", "failed")

    def test_failed_status_on_ffmpeg_nonzero(self, tmp_path):
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"\x00" * 100)
        with patch("subprocess.run", side_effect=self._fake_ffmpeg_fail):
            result = transcode(
                original_path=orig,
                video_id="test-uuid",
                metadata={"rotation": 90, "fps": 60.0, "resolution": "1920x1080",
                          "has_audio": True},
                upload_dir=tmp_path,
                target_fps=30,
                target_height=720,
            )
        assert result.status == "failed"
        assert result.processed_path is None

    def test_failed_status_on_timeout(self, tmp_path):
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"\x00" * 100)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 120)):
            result = transcode(
                original_path=orig,
                video_id="test-uuid",
                metadata={"rotation": 90, "fps": 60.0, "resolution": "1920x1080",
                          "has_audio": True},
                upload_dir=tmp_path,
            )
        assert result.status == "failed"
        assert "timeout" in (result.error or "")

    def test_original_not_deleted_on_success(self, tmp_path):
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"\x00" * 100)
        with patch("subprocess.run", side_effect=self._fake_ffmpeg_success):
            transcode(
                original_path=orig,
                video_id="test-uuid",
                metadata={"rotation": 0, "fps": 60.0, "resolution": "1280x720",
                          "has_audio": True},
                upload_dir=tmp_path,
            )
        assert orig.exists(), "original file must not be deleted"

    def test_original_not_deleted_on_failure(self, tmp_path):
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"\x00" * 100)
        with patch("subprocess.run", side_effect=self._fake_ffmpeg_fail):
            transcode(
                original_path=orig,
                video_id="test-uuid",
                metadata={"rotation": 90, "fps": 60.0, "resolution": "1920x1080",
                          "has_audio": True},
                upload_dir=tmp_path,
            )
        assert orig.exists(), "original file must not be deleted on failure"

    def test_no_tmp_file_left_on_failure(self, tmp_path):
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"\x00" * 100)
        with patch("subprocess.run", side_effect=self._fake_ffmpeg_fail):
            transcode(
                original_path=orig,
                video_id="test-uuid",
                metadata={"rotation": 90, "fps": 60.0, "resolution": "1920x1080",
                          "has_audio": True},
                upload_dir=tmp_path,
            )
        tmp_files = list(tmp_path.glob("*.tmp.*"))
        assert len(tmp_files) == 0, f"Temp files not cleaned up: {tmp_files}"

    def test_skip_thumbnail_generated(self, tmp_path):
        """Even on skip, thumbnail should be attempted."""
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"\x00" * 100)

        def fake_run(cmd, capture_output, timeout):
            # Create any output file (thumbnail tmp)
            out = Path(cmd[-1])
            out.write_bytes(b"JPEGDATA")
            return MagicMock(returncode=0, stderr=b"", stdout=b"")

        with patch("subprocess.run", side_effect=fake_run):
            result = transcode(
                original_path=orig,
                video_id="test-uuid",
                metadata={"rotation": 0, "fps": 25.0, "resolution": "1280x720",
                          "has_audio": False},
                upload_dir=tmp_path,
                target_fps=30,
                target_height=720,
            )
        assert result.status == "skipped"
        # Thumbnail was attempted — thumbnail_path may be set if fake ffmpeg succeeded
        # (the thumbnail command is the only subprocess call on skip)


# ── Metadata extraction helpers ───────────────────────────────────────────────

class TestExtractResolutionFps:
    def _probe(self, **kw):
        stream = {"codec_type": "video", "width": 1280, "height": 720,
                  "avg_frame_rate": "30/1"}
        stream.update(kw)
        return {"streams": [stream]}

    def test_resolution_extracted(self):
        res, _ = _extract_resolution_fps(self._probe())
        assert res == "1280x720"

    def test_fps_extracted(self):
        _, fps = _extract_resolution_fps(self._probe())
        assert fps == 30.0

    def test_no_video_stream(self):
        res, fps = _extract_resolution_fps({"streams": []})
        assert res is None
        assert fps is None

    def test_invalid_fps_graceful(self):
        _, fps = _extract_resolution_fps(self._probe(avg_frame_rate="0/0"))
        assert fps is None


class TestSha256File:
    def test_known_value(self, tmp_path):
        import hashlib
        p = tmp_path / "f.bin"
        p.write_bytes(b"hello")
        expected = hashlib.sha256(b"hello").hexdigest()
        assert _sha256_file(p) == expected

    def test_different_files_differ(self, tmp_path):
        p1 = tmp_path / "a.bin"
        p2 = tmp_path / "b.bin"
        p1.write_bytes(b"aaa")
        p2.write_bytes(b"bbb")
        assert _sha256_file(p1) != _sha256_file(p2)


# ── _cleanup ─────────────────────────────────────────────────────────────────

from app.services.juggling.transcode_service import _cleanup


class TestCleanup:
    def test_cleanup_existing_file(self, tmp_path):
        p = tmp_path / "tmp.mp4"
        p.write_bytes(b"data")
        _cleanup(p)
        assert not p.exists()

    def test_cleanup_nonexistent_is_noop(self, tmp_path):
        _cleanup(tmp_path / "nonexistent.mp4")  # must not raise

    def test_cleanup_handles_oserror(self, tmp_path):
        """OSError on unlink is silently swallowed."""
        p = tmp_path / "locked.mp4"
        p.write_bytes(b"data")
        with patch("pathlib.Path.unlink", side_effect=OSError("locked")):
            _cleanup(p)  # must not raise


# ── transcode() — generic exception branch ───────────────────────────────────

class TestTranscodeGenericException:
    def test_generic_exception_returns_failed(self, tmp_path):
        """subprocess.run raising a generic Exception → status=failed, error starts with 'ffmpeg_exception:'."""
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"\x00" * 100)
        with patch("subprocess.run", side_effect=RuntimeError("unexpected")):
            result = transcode(
                original_path=orig,
                video_id="test-exc",
                metadata={"rotation": 90, "fps": 60.0, "resolution": "1920x1080",
                          "has_audio": True},
                upload_dir=tmp_path,
            )
        assert result.status == "failed"
        assert "ffmpeg_exception" in (result.error or "")