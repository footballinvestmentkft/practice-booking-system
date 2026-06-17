"""
Juggling POC — Video transcode, audio stripping, and thumbnail generation.

Decision matrix (evaluated in order):
  1. Skip — no rotation, no audio, fps ≤ target, height ≤ target
             → no processed file; thumbnail still generated
  2. Audio-only strip — rotation=0, no fps/scale filter needed, but has_audio=True
             → -c:v copy -an  (stream copy is safe: no -vf present)
  3. Full transcode — any rotation / fps > target / height > target
             → -c:v libx264; autorotate handles rotation, -vf only for scale/fps
             RULE: -c:v copy is FORBIDDEN whenever -vf is present

Rotation handling:
  ffmpeg autorotate (ON by default) reads the Display Matrix from the input,
  physically rotates the decoded frames, and writes an identity transform to
  the output track header (tkhd). No explicit transpose filter is needed.
  Explicit transpose would double-rotate (autorotate + transpose = 2×).
  For iOS MOV/MP4 with side_data Display Matrix rotation=90: autorotate
  produces correctly-oriented 640×628 output; tkhd is identity; AVPlayer
  plays without any additional rotation.

Filtergraph (when used):
  scale → fps  (rotation handled by autorotate, not by filter)

Thumbnail:
  Always generated from original_path (even on skip).
  ffmpeg autorotate handles rotation; no explicit -vf needed.
  ffmpeg: -ss 0 -i <original> -vframes 1 -q:v 2 <uuid_thumb.jpg>

Atomic write:
  All outputs written to <dest>.tmp then os.replace() to <dest>.
  Temp files cleaned up on any error.

Original safety:
  original_path is NEVER deleted or modified.

Scope boundary (NEVER add):
  FootAndBall / MediaPipe / ONNX / contact detection / streaming endpoint.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TARGET_FPS: int = 30
TARGET_HEIGHT: int = 720


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class TranscodeResult:
    """Result returned by transcode(); consumed by the Celery task."""
    status: str                               # "done" | "skipped" | "failed"
    error: Optional[str] = None
    processed_path: Optional[Path] = None    # None when status != done
    thumbnail_path: Optional[Path] = None    # None only when thumbnail generation failed
    audio_stripped: bool = False
    processed_resolution: Optional[str] = None
    processed_fps: Optional[float] = None
    processed_file_size_bytes: Optional[int] = None
    checksum_processed: Optional[str] = None


# ── ffmpeg command builders ───────────────────────────────────────────────────

class TranscodeSkip(Exception):
    """Raised when no processing is needed for this video."""


def build_transcode_command(
    input_path: Path,
    output_path: Path,
    rotation: int,
    fps: Optional[float],
    height: Optional[int],
    has_audio: bool,
    target_fps: int = TARGET_FPS,
    target_height: int = TARGET_HEIGHT,
) -> list[str]:
    """
    Return the ffmpeg argv list for video normalization.

    Raises TranscodeSkip when no processing is needed.

    Rotation contract:
      ffmpeg autorotate (ON by default) is intentionally left enabled. It reads
      the Display Matrix / rotate tag from the input, physically rotates the
      decoded frames, and writes an identity transform to the output tkhd box.
      AVPlayer therefore sees no rotation hint and renders pixels as-is.
      Explicit -vf transpose is NOT added for rotation — it would double-rotate
      (autorotate + transpose = two 90° turns). The `rotation` parameter is only
      used to decide whether a full re-encode is required (instead of TranscodeSkip
      or audio-only stream-copy).
    """
    needs_rotation = rotation not in (0, None)
    needs_scale = (height is not None) and (height > target_height)
    needs_fps = (fps is not None) and (fps > target_fps)
    # needs_vf: scale/fps require an explicit vf filtergraph.
    # rotation does NOT add a filter — autorotate handles it in the decoder.
    needs_vf = needs_scale or needs_fps
    # needs_full_transcode: anything that requires a full re-encode.
    # rotation forces a re-encode so that autorotate can rewrite the tkhd matrix.
    needs_full_transcode = needs_rotation or needs_vf

    if not needs_full_transcode and not has_audio:
        raise TranscodeSkip("no_processing_needed")

    binary = shutil.which("ffmpeg") or "ffmpeg"
    cmd: list[str] = [binary, "-y", "-i", str(input_path)]

    if needs_full_transcode:
        if needs_vf:
            # Build filtergraph: scale → fps (no transpose — autorotate handles rotation)
            filters: list[str] = []
            if needs_scale:
                filters.append(f"scale=-2:{target_height}")
            if needs_fps:
                filters.append(f"fps={target_fps}")
            if filters:
                cmd += ["-vf", ",".join(filters)]
        # RULE: -c:v libx264 for full transcode; -c:v copy is forbidden here
        cmd += ["-c:v", "libx264", "-crf", "23", "-preset", "medium"]
        cmd += ["-an"]  # always strip audio in processed output
    else:
        # Audio-only strip: stream-copy video (-c:v copy bypasses the decoder
        # so autorotate never fires, which is fine because rotation=0 here)
        cmd += ["-c:v", "copy", "-an"]

    # Strip all metadata from the output (removes rotate tag, clears any
    # residual metadata — the tkhd rotation is already cleared by the re-encode)
    cmd += ["-map_metadata", "-1"]
    cmd += ["-movflags", "+faststart"]
    cmd += [str(output_path)]
    return cmd


def build_thumbnail_command(
    input_path: Path,
    output_path: Path,
    rotation: int = 0,
) -> list[str]:
    """Return ffmpeg argv for first-frame JPEG with rotation correction.

    ffmpeg autorotate (ON by default) reads the Display Matrix / rotate tag
    from the input and correctly orients the output frame. No explicit
    transpose or -noautorotate is needed.

    The `rotation` parameter is accepted for API compatibility but unused —
    autorotate handles all rotation cases correctly.
    """
    binary = shutil.which("ffmpeg") or "ffmpeg"
    cmd: list[str] = [binary, "-y", "-ss", "0", "-i", str(input_path)]
    cmd += ["-vframes", "1", "-q:v", "2", str(output_path)]
    return cmd


# ── Metadata helpers ──────────────────────────────────────────────────────────

def _probe_file(path: Path, timeout: int = 30) -> dict:
    """Run ffprobe on path; return parsed JSON or {} on failure."""
    binary = shutil.which("ffprobe") or "ffprobe"
    try:
        result = subprocess.run(
            [binary, "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", str(path)],
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def _extract_resolution_fps(probe: dict) -> tuple[Optional[str], Optional[float]]:
    """Return (WxH, fps) from a processed-file probe, or (None, None)."""
    for stream in probe.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        w, h = stream.get("width"), stream.get("height")
        resolution = f"{w}x{h}" if (w and h) else None
        fps: Optional[float] = None
        for key in ("avg_frame_rate", "r_frame_rate"):
            raw = stream.get(key, "")
            if not raw or raw == "0/0":
                continue
            try:
                if "/" in raw:
                    n, d = raw.split("/", 1)
                    v = float(int(n) / int(d)) if int(d) != 0 else None
                else:
                    v = float(raw)
                if v and v > 0:
                    fps = round(v, 3)
                    break
            except (ValueError, ZeroDivisionError):
                pass
        return resolution, fps
    return None, None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


# ── Thumbnail helper ──────────────────────────────────────────────────────────

def _generate_thumbnail(
    original_path: Path,
    thumbnail_path: Path,
    rotation: int,
    timeout: int,
) -> bool:
    """Extract first frame to thumbnail_path. Returns True on success."""
    tmp = thumbnail_path.with_suffix(".tmp.jpg")
    cmd = build_thumbnail_command(original_path, tmp, rotation)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if r.returncode != 0:
            logger.warning(
                "thumbnail_ffmpeg_error",
                extra={"returncode": r.returncode,
                       "stderr": r.stderr.decode("utf-8", errors="replace")[:200]},
            )
            _cleanup(tmp)
            return False
        os.replace(str(tmp), str(thumbnail_path))
        return True
    except Exception as exc:
        logger.warning("thumbnail_exception", extra={"error": str(exc)})
        _cleanup(tmp)
        return False


# ── Main entry point ──────────────────────────────────────────────────────────

def transcode(
    original_path: Path,
    video_id: str,
    metadata: dict,
    upload_dir: Path,
    target_fps: int = TARGET_FPS,
    target_height: int = TARGET_HEIGHT,
    timeout_seconds: int = 120,
) -> TranscodeResult:
    """
    Run the full P2 pipeline for one video.

    Steps:
      1. Generate thumbnail from original (always, even on skip)
      2. Build transcode command; if TranscodeSkip → return status=skipped
      3. Run ffmpeg (temp → atomic rename)
      4. Probe processed file for output metadata
      5. Return TranscodeResult

    Does NOT delete or modify original_path.
    Does NOT set any DB fields — the caller (Celery task) does that.
    """
    upload_dir.mkdir(parents=True, exist_ok=True)

    rotation: int = int(metadata.get("rotation") or 0)
    fps: Optional[float] = metadata.get("fps")
    resolution: Optional[str] = metadata.get("resolution")
    has_audio: bool = bool(metadata.get("has_audio", False))

    height: Optional[int] = None
    if resolution:
        try:
            height = int(resolution.split("x")[1])
        except (IndexError, ValueError):
            pass

    thumbnail_dest = upload_dir / f"{video_id}_thumb.jpg"
    processed_dest = upload_dir / f"{video_id}_proc.mp4"

    # ── Step 1: thumbnail ─────────────────────────────────────────────────────
    thumb_ok = _generate_thumbnail(original_path, thumbnail_dest, rotation, timeout_seconds)
    actual_thumb = thumbnail_dest if thumb_ok else None

    # ── Step 2: transcode decision ────────────────────────────────────────────
    tmp_proc = upload_dir / f"{video_id}_proc.tmp.mp4"
    try:
        cmd = build_transcode_command(
            input_path=original_path,
            output_path=tmp_proc,
            rotation=rotation,
            fps=fps,
            height=height,
            has_audio=has_audio,
            target_fps=target_fps,
            target_height=target_height,
        )
    except TranscodeSkip:
        logger.info(
            "transcode_skipped",
            extra={"video_id": video_id, "rotation": rotation,
                   "fps": fps, "height": height, "has_audio": has_audio},
        )
        return TranscodeResult(status="skipped", thumbnail_path=actual_thumb)

    # ── Step 3: run ffmpeg ────────────────────────────────────────────────────
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _cleanup(tmp_proc)
        return TranscodeResult(
            status="failed",
            error="ffmpeg_timeout",
            thumbnail_path=actual_thumb,
        )
    except Exception as exc:
        _cleanup(tmp_proc)
        return TranscodeResult(
            status="failed",
            error=f"ffmpeg_exception:{exc}",
            thumbnail_path=actual_thumb,
        )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:400]
        logger.warning(
            "transcode_ffmpeg_error",
            extra={"video_id": video_id, "returncode": result.returncode,
                   "stderr": stderr},
        )
        _cleanup(tmp_proc)
        return TranscodeResult(
            status="failed",
            error=f"ffmpeg_exit_{result.returncode}:{stderr[:200]}",
            thumbnail_path=actual_thumb,
        )

    # Atomic rename
    os.replace(str(tmp_proc), str(processed_dest))

    # ── Step 4: probe output metadata ─────────────────────────────────────────
    probe = _probe_file(processed_dest, timeout=30)
    out_resolution, out_fps = _extract_resolution_fps(probe)
    out_size = processed_dest.stat().st_size
    out_checksum = _sha256_file(processed_dest)

    logger.info(
        "transcode_done",
        extra={"video_id": video_id, "resolution": out_resolution,
               "fps": out_fps, "size": out_size},
    )

    return TranscodeResult(
        status="done",
        processed_path=processed_dest,
        thumbnail_path=actual_thumb,
        audio_stripped=True,
        processed_resolution=out_resolution,
        processed_fps=out_fps,
        processed_file_size_bytes=out_size,
        checksum_processed=out_checksum,
    )