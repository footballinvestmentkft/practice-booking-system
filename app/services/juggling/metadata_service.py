"""
Juggling video metadata detection via ffprobe.

Runs ffprobe as a subprocess (same pattern as card_export_service.py).
Returns a structured dict stored in juggling_videos.server_detected_metadata.

server_detected_metadata keys:
  fps, resolution, duration_seconds, codec, bitrate_kbps,
  rotation, has_audio, file_format, container, nb_streams

This is the authoritative metadata source — client_reported_metadata is NOT trusted.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


class VideoProbeError(RuntimeError):
    """Raised when ffprobe cannot read or parse the video file."""


def probe_video(file_path: Path, timeout_seconds: int = 30) -> Dict[str, Any]:
    """
    Run ffprobe on file_path and return parsed JSON output.
    Raises VideoProbeError on subprocess failure or JSON parse error.
    """
    binary = shutil.which("ffprobe") or "ffprobe"
    try:
        result = subprocess.run(
            [
                binary,
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-show_format",
                str(file_path),
            ],
            capture_output=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise VideoProbeError(
            "ffprobe binary not found — install ffmpeg (brew: ffmpeg, apt: ffmpeg)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoProbeError(
            f"ffprobe timed out after {timeout_seconds}s"
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:500]
        raise VideoProbeError(
            f"ffprobe exited with code {result.returncode}: {stderr}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VideoProbeError(
            f"ffprobe output could not be parsed as JSON: {exc}"
        ) from exc


def extract_server_metadata(probe_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalise raw ffprobe JSON into server_detected_metadata schema.

    Returns a dict with keys:
      fps, resolution, duration_seconds, codec, bitrate_kbps,
      rotation, has_audio, file_format, container, nb_streams
    """
    streams: list = probe_data.get("streams", [])
    fmt: dict = probe_data.get("format", {})

    # Find the primary video stream
    video_stream: Optional[dict] = None
    audio_streams: list = []
    for s in streams:
        if s.get("codec_type") == "video" and video_stream is None:
            video_stream = s
        elif s.get("codec_type") == "audio":
            audio_streams.append(s)

    # FPS — prefer avg_frame_rate, fall back to r_frame_rate
    fps: Optional[float] = None
    if video_stream:
        for key in ("avg_frame_rate", "r_frame_rate"):
            raw = video_stream.get(key, "")
            fps = _parse_fraction(raw)
            if fps and fps > 0:
                break

    # Resolution
    resolution: Optional[str] = None
    if video_stream:
        w = video_stream.get("width")
        h = video_stream.get("height")
        if w and h:
            resolution = f"{w}x{h}"

    # Duration
    duration_seconds: Optional[float] = None
    raw_duration = (
        (video_stream or {}).get("duration")
        or fmt.get("duration")
    )
    if raw_duration:
        try:
            duration_seconds = float(raw_duration)
        except (ValueError, TypeError):
            pass

    # Codec
    codec: Optional[str] = None
    if video_stream:
        codec = video_stream.get("codec_name")

    # Bitrate (kbps)
    bitrate_kbps: Optional[int] = None
    raw_bitrate = fmt.get("bit_rate")
    if raw_bitrate:
        try:
            bitrate_kbps = int(raw_bitrate) // 1000
        except (ValueError, TypeError):
            pass

    # Rotation — tags.rotate is authoritative; side_data_list is fallback only.
    #
    # Raw iPhone .MOV files contain BOTH tags.rotate=90 AND a Display Matrix
    # with side_data.rotation=-90 (opposite sign convention). Reading side_data
    # after tags would overwrite the correct +90 with -90, producing an unknown
    # rotation value that build_transcode_command cannot map to a transpose filter.
    #
    # iOS AVAssetExport output files contain only side_data (no tags.rotate) with
    # a positive rotation value, so the fallback branch handles them correctly.
    rotation: int = 0
    if video_stream:
        tags = video_stream.get("tags", {})
        raw_rot = tags.get("rotate", None)
        if raw_rot is not None:
            # tags.rotate is direct and correctly signed (e.g. "90" = rotate 90° CW).
            try:
                rotation = int(raw_rot)
            except (ValueError, TypeError):
                rotation = 0
        else:
            # Fallback: Display Matrix side_data when tags.rotate is absent.
            # Normalize to 0–360 so downstream code only sees standard angles
            # (handles the rare case where ffprobe emits a negative value here).
            for sd in video_stream.get("side_data_list", []):
                if sd.get("side_data_type") == "Display Matrix":
                    rot = sd.get("rotation")
                    if rot is not None:
                        try:
                            rotation = int(rot) % 360
                        except (ValueError, TypeError):
                            pass
                    break

    # Audio
    has_audio: bool = len(audio_streams) > 0

    # Format / container
    file_format: Optional[str] = fmt.get("format_name")
    container: Optional[str] = file_format.split(",")[0] if file_format else None

    return {
        "fps":              round(fps, 3) if fps else None,
        "resolution":       resolution,
        "duration_seconds": round(duration_seconds, 2) if duration_seconds else None,
        "codec":            codec,
        "bitrate_kbps":     bitrate_kbps,
        "rotation":         rotation,
        "has_audio":        has_audio,
        "file_format":      file_format,
        "container":        container,
        "nb_streams":       len(streams),
    }


def _parse_fraction(value: str) -> Optional[float]:
    """Parse '60000/1001' or '30' style FPS strings."""
    if not value or value == "0/0":
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            d = int(den)
            return float(int(num) / d) if d != 0 else None
        return float(value)
    except (ValueError, TypeError, ZeroDivisionError):
        return None
