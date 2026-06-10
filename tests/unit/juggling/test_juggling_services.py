"""
Juggling service unit tests — CI coverage suite.

Pure function tests for security_service, quality_service, feature_flag.
No DB, no HTTP, no Celery. Fast and isolated.

These complement app/tests/test_juggling_*.py which cover the full
HTTP layer but are not included in the CI coverage run.
"""
from __future__ import annotations

import struct

import pytest


# ── security_service ─────────────────────────────────────────────────────────

from app.services.juggling.security_service import (
    VideoSecurityError,
    compute_sha256,
    generate_server_filename,
    run_all_pre_save_checks,
    validate_extension,
    validate_magic_bytes,
    validate_mime,
    validate_size,
)


def _ftyp_mp4(size_bytes: int = 200) -> bytes:
    box = struct.pack(">I", 20) + b"ftyp" + b"isom" + b"\x00\x00\x00\x00" + b"isom"
    return box + b"\x00" * size_bytes


class TestSecurityServiceExtension:
    def test_mp4_accepted(self):
        assert validate_extension("clip.mp4") == ".mp4"

    def test_mov_accepted(self):
        assert validate_extension("clip.MOV") == ".mov"

    def test_m4v_accepted(self):
        assert validate_extension("session.m4v") == ".m4v"

    def test_avi_rejected(self):
        with pytest.raises(VideoSecurityError, match="unsupported_extension"):
            validate_extension("clip.avi")

    def test_mkv_rejected(self):
        with pytest.raises(VideoSecurityError, match="unsupported_extension"):
            validate_extension("clip.mkv")

    def test_no_extension_rejected(self):
        with pytest.raises(VideoSecurityError, match="unsupported_extension"):
            validate_extension("noextension")

    def test_path_traversal_stripped(self):
        with pytest.raises(VideoSecurityError):
            validate_extension("../../etc/passwd")


class TestSecurityServiceMime:
    def test_video_mp4_accepted(self):
        validate_mime("video/mp4")

    def test_video_quicktime_accepted(self):
        validate_mime("video/quicktime")

    def test_video_x_m4v_accepted(self):
        validate_mime("video/x-m4v")

    def test_mime_with_params_accepted(self):
        validate_mime("video/mp4; codecs=avc1")

    def test_image_jpeg_rejected(self):
        with pytest.raises(VideoSecurityError, match="unsupported_mime"):
            validate_mime("image/jpeg")

    def test_octet_stream_rejected(self):
        with pytest.raises(VideoSecurityError, match="unsupported_mime"):
            validate_mime("application/octet-stream")


class TestSecurityServiceMagicBytes:
    def test_isom_ftyp_accepted(self):
        validate_magic_bytes(_ftyp_mp4())

    def test_avc1_brand_accepted(self):
        box = struct.pack(">I", 20) + b"ftyp" + b"avc1" + b"\x00\x00\x00\x00" + b"avc1"
        validate_magic_bytes(box + b"\x00" * 100)

    def test_qt_brand_accepted(self):
        box = struct.pack(">I", 20) + b"ftyp" + b"qt  " + b"\x00\x00\x00\x00" + b"qt  "
        validate_magic_bytes(box + b"\x00" * 100)

    def test_hvc1_brand_accepted(self):
        box = struct.pack(">I", 20) + b"ftyp" + b"hvc1" + b"\x00\x00\x00\x00" + b"hvc1"
        validate_magic_bytes(box + b"\x00" * 100)

    def test_jpeg_magic_rejected(self):
        with pytest.raises(VideoSecurityError, match="magic_bytes_invalid"):
            validate_magic_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    def test_too_short_rejected(self):
        with pytest.raises(VideoSecurityError, match="magic_bytes_invalid"):
            validate_magic_bytes(b"\x00" * 8)

    def test_moov_alone_rejected(self):
        moov = b"\x00\x00\x00\x08" + b"moov" + b"\x00" * 100
        with pytest.raises(VideoSecurityError, match="magic_bytes_invalid"):
            validate_magic_bytes(moov)

    def test_unknown_brand_rejected(self):
        box = struct.pack(">I", 20) + b"ftyp" + b"UNKN" + b"\x00\x00\x00\x00" + b"UNKN"
        with pytest.raises(VideoSecurityError, match="magic_bytes_invalid"):
            validate_magic_bytes(box + b"\x00" * 100)


class TestSecurityServiceSize:
    def test_empty_file_rejected(self):
        with pytest.raises(VideoSecurityError, match="empty_file"):
            validate_size(b"")

    def test_oversized_rejected(self, monkeypatch):
        from app.services.juggling import security_service as ss
        monkeypatch.setattr(ss.settings, "JUGGLING_VIDEO_MAX_SIZE_MB", 1)
        with pytest.raises(VideoSecurityError, match="file_too_large"):
            validate_size(b"\x00" * (2 * 1024 * 1024))

    def test_within_limit_accepted(self, monkeypatch):
        from app.services.juggling import security_service as ss
        monkeypatch.setattr(ss.settings, "JUGGLING_VIDEO_MAX_SIZE_MB", 100)
        validate_size(b"\x00" * 1000)


class TestSecurityServiceHelpers:
    def test_server_filename_is_uuid(self):
        fname = generate_server_filename(".mp4")
        assert fname.endswith(".mp4")
        assert len(fname[:-4]) == 36

    def test_server_filename_not_client_name(self):
        fname = generate_server_filename(".mp4")
        assert "client" not in fname
        assert "/" not in fname
        assert ".." not in fname

    def test_checksum_sha256_length(self):
        assert len(compute_sha256(b"test")) == 64

    def test_checksum_sha256_is_hex(self):
        digest = compute_sha256(b"hello")
        assert all(c in "0123456789abcdef" for c in digest)

    def test_checksum_deterministic(self):
        assert compute_sha256(b"abc") == compute_sha256(b"abc")

    def test_checksum_differs_for_different_inputs(self):
        assert compute_sha256(b"abc") != compute_sha256(b"xyz")


class TestRunAllPreSaveChecks:
    def test_valid_input_returns_filename_and_checksum(self):
        data = _ftyp_mp4()
        fname, chk = run_all_pre_save_checks("video.mp4", "video/mp4", data)
        assert fname.endswith(".mp4")
        assert len(chk) == 64

    def test_client_filename_not_in_server_filename(self):
        data = _ftyp_mp4()
        fname, _ = run_all_pre_save_checks("my_personal.mp4", "video/mp4", data)
        assert "my_personal" not in fname

    def test_extension_checked_first(self):
        with pytest.raises(VideoSecurityError, match="unsupported_extension"):
            run_all_pre_save_checks("video.avi", "video/mp4", _ftyp_mp4())

    def test_size_checked_before_magic(self):
        with pytest.raises(VideoSecurityError, match="empty_file"):
            run_all_pre_save_checks("video.mp4", "video/mp4", b"")

    def test_magic_checked_after_size(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 200
        with pytest.raises(VideoSecurityError, match="magic_bytes_invalid"):
            run_all_pre_save_checks("video.mp4", "video/mp4", jpeg)


# ── quality_service ──────────────────────────────────────────────────────────

from app.services.juggling import quality_service


def _meta(**kw):
    base = {
        "fps": 60.0, "resolution": "1280x720", "duration_seconds": 30.0,
        "codec": "hevc", "bitrate_kbps": 8000, "rotation": 0,
        "has_audio": False, "container": "mov", "nb_streams": 1,
    }
    base.update(kw)
    return base


class TestQualityServiceFpsScore:
    def test_60fps_max_score(self):
        assert quality_service._fps_score(60.0) == 1.0

    def test_30fps_mid_score(self):
        assert quality_service._fps_score(30.0) == 0.7

    def test_24fps_low_score(self):
        assert quality_service._fps_score(24.0) == 0.4

    def test_below_24fps_very_low(self):
        assert quality_service._fps_score(15.0) == 0.1

    def test_none_fps_neutral(self):
        assert quality_service._fps_score(None) == 0.5

    def test_above_60fps_capped(self):
        assert quality_service._fps_score(120.0) == 1.0


class TestQualityServiceAnalyze:
    def test_acceptable_video_returns_score(self):
        score, status, detail, reason = quality_service.analyze(
            b"\x00" * (20 * 1024 * 1024), _meta()
        )
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
        assert reason is None

    def test_fps_too_low_rejected(self):
        _, status, _, reason = quality_service.analyze(
            b"\x00" * 1000, _meta(fps=20.0)
        )
        assert reason == "fps_too_low"
        assert status == "rejected"

    def test_subject_size_score_always_null(self):
        _, _, detail, _ = quality_service.analyze(b"\x00" * 1000, _meta())
        assert detail["subject_size_score"] is None

    def test_ball_visible_score_always_null(self):
        _, _, detail, _ = quality_service.analyze(b"\x00" * 1000, _meta())
        assert detail["ball_visible_score"] is None

    def test_audio_present_warning(self):
        _, _, detail, _ = quality_service.analyze(b"\x00" * 1000, _meta(has_audio=True))
        assert detail.get("audio_present") is True

    def test_no_audio_no_warning(self):
        _, _, detail, _ = quality_service.analyze(b"\x00" * 1000, _meta(has_audio=False))
        assert "audio_present" not in detail

    def test_rotation_stored(self):
        _, _, detail, _ = quality_service.analyze(b"\x00" * 1000, _meta(rotation=90))
        assert detail["rotation"] == 90

    def test_null_metadata_handled(self):
        score, _, _, _ = quality_service.analyze(b"\x00" * 1000, None)
        assert isinstance(score, float)

    def test_score_in_unit_range(self):
        score, _, _, _ = quality_service.analyze(b"\x00" * 1000, _meta())
        assert 0.0 <= score <= 1.0

    def test_fps_acceptable_true_at_60(self):
        _, _, detail, _ = quality_service.analyze(b"\x00" * 1000, _meta(fps=60.0))
        assert detail["fps_acceptable"] is True

    def test_fps_acceptable_false_below_24(self):
        _, _, detail, _ = quality_service.analyze(b"\x00" * 1000, _meta(fps=20.0))
        assert detail["fps_acceptable"] is False

    def test_duration_acceptable_true(self):
        _, _, detail, _ = quality_service.analyze(b"\x00" * 1000, _meta())
        assert detail["duration_acceptable"] is True


# ── feature_flag ─────────────────────────────────────────────────────────────

from app.services.juggling.feature_flag import is_juggling_enabled, require_juggling_enabled
from app.services.juggling import metadata_service
import asyncio


class TestFeatureFlag:
    def test_disabled_by_default(self, monkeypatch):
        from app.services.juggling import feature_flag as ff
        monkeypatch.setattr(ff.settings, "JUGGLING_POC_ENABLED", False)
        assert ff.is_juggling_enabled() is False

    def test_enabled_when_set(self, monkeypatch):
        from app.services.juggling import feature_flag as ff
        monkeypatch.setattr(ff.settings, "JUGGLING_POC_ENABLED", True)
        assert ff.is_juggling_enabled() is True

    def test_require_raises_503_when_disabled(self, monkeypatch):
        from fastapi import HTTPException
        from app.services.juggling import feature_flag as ff
        monkeypatch.setattr(ff.settings, "JUGGLING_POC_ENABLED", False)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(require_juggling_enabled())
        assert exc.value.status_code == 503

    def test_require_passes_when_enabled(self, monkeypatch):
        from app.services.juggling import feature_flag as ff
        monkeypatch.setattr(ff.settings, "JUGGLING_POC_ENABLED", True)
        asyncio.run(require_juggling_enabled())


# ── metadata_service — branch coverage ──────────────────────────────────────

class TestParseFraction:
    def test_fraction_60000_1001(self):
        r = metadata_service._parse_fraction("60000/1001")
        assert r is not None
        assert abs(r - 59.94) < 0.01

    def test_fraction_30_1(self):
        r = metadata_service._parse_fraction("30/1")
        assert r == 30.0

    def test_plain_float(self):
        r = metadata_service._parse_fraction("25.0")
        assert r == 25.0

    def test_zero_zero(self):
        assert metadata_service._parse_fraction("0/0") is None

    def test_empty_string(self):
        assert metadata_service._parse_fraction("") is None

    def test_none_value(self):
        assert metadata_service._parse_fraction(None) is None

    def test_zero_denominator(self):
        assert metadata_service._parse_fraction("30/0") is None

    def test_invalid_string(self):
        assert metadata_service._parse_fraction("not/a/number") is None


def _probe(video_streams=None, audio_streams=None, fmt=None):
    """Build minimal ffprobe probe_data dict."""
    streams = []
    if video_streams:
        streams.extend(video_streams)
    if audio_streams:
        streams.extend(audio_streams)
    return {"streams": streams, "format": fmt or {}}


def _vs(**kw):
    """Minimal video stream dict."""
    s = {"codec_type": "video", "codec_name": "h264",
         "width": 1280, "height": 720,
         "avg_frame_rate": "60/1", "r_frame_rate": "60/1",
         "duration": "30.0", "tags": {}, "side_data_list": []}
    s.update(kw)
    return s


def _as_(**kw):
    """Minimal audio stream dict."""
    s = {"codec_type": "audio"}
    s.update(kw)
    return s


class TestExtractServerMetadata:
    def test_full_video_with_audio(self):
        d = _probe([_vs()], [_as_()], {"format_name": "mov,mp4", "bit_rate": "8000000"})
        m = metadata_service.extract_server_metadata(d)
        assert m["codec"] == "h264"
        assert m["resolution"] == "1280x720"
        assert m["fps"] == 60.0
        assert m["duration_seconds"] == 30.0
        assert m["has_audio"] is True
        assert m["bitrate_kbps"] == 8000
        assert m["container"] == "mov"
        assert m["nb_streams"] == 2

    def test_no_streams(self):
        m = metadata_service.extract_server_metadata({"streams": [], "format": {}})
        assert m["fps"] is None
        assert m["resolution"] is None
        assert m["has_audio"] is False
        assert m["nb_streams"] == 0

    def test_fps_fallback_to_r_frame_rate(self):
        vs = _vs(avg_frame_rate="0/0", r_frame_rate="30/1")
        d = _probe([vs])
        m = metadata_service.extract_server_metadata(d)
        assert m["fps"] == 30.0

    def test_no_width_height(self):
        vs = _vs()
        del vs["width"]
        del vs["height"]
        d = _probe([vs])
        m = metadata_service.extract_server_metadata(d)
        assert m["resolution"] is None

    def test_duration_from_format_when_no_stream_duration(self):
        vs = _vs()
        vs.pop("duration", None)
        d = _probe([vs], fmt={"duration": "45.0"})
        m = metadata_service.extract_server_metadata(d)
        assert m["duration_seconds"] == 45.0

    def test_invalid_duration_ignored(self):
        vs = _vs(duration="invalid")
        d = _probe([vs])
        m = metadata_service.extract_server_metadata(d)
        assert m["duration_seconds"] is None

    def test_invalid_bitrate_ignored(self):
        d = _probe([_vs()], fmt={"bit_rate": "notanumber"})
        m = metadata_service.extract_server_metadata(d)
        assert m["bitrate_kbps"] is None

    def test_rotation_from_tags(self):
        vs = _vs(tags={"rotate": "90"})
        d = _probe([vs])
        m = metadata_service.extract_server_metadata(d)
        assert m["rotation"] == 90

    def test_rotation_from_side_data(self):
        vs = _vs(side_data_list=[{"side_data_type": "Display Matrix", "rotation": 270}])
        d = _probe([vs])
        m = metadata_service.extract_server_metadata(d)
        assert m["rotation"] == 270

    def test_rotation_invalid_tag_falls_back_to_zero(self):
        vs = _vs(tags={"rotate": "bad"})
        d = _probe([vs])
        m = metadata_service.extract_server_metadata(d)
        assert m["rotation"] == 0

    def test_multiple_video_streams_uses_first(self):
        vs1 = _vs(codec_name="h264")
        vs2 = _vs(codec_name="hevc")
        d = _probe([vs1, vs2])
        m = metadata_service.extract_server_metadata(d)
        assert m["codec"] == "h264"

    def test_no_audio_stream(self):
        d = _probe([_vs()])
        m = metadata_service.extract_server_metadata(d)
        assert m["has_audio"] is False

    def test_no_format_name(self):
        d = _probe([_vs()], fmt={})
        m = metadata_service.extract_server_metadata(d)
        assert m["container"] is None

    def test_side_data_non_display_matrix_ignored(self):
        vs = _vs(side_data_list=[{"side_data_type": "Other", "rotation": 180}])
        d = _probe([vs])
        m = metadata_service.extract_server_metadata(d)
        assert m["rotation"] == 0

    def test_side_data_rotation_none_ignored(self):
        vs = _vs(side_data_list=[{"side_data_type": "Display Matrix", "rotation": None}])
        d = _probe([vs])
        m = metadata_service.extract_server_metadata(d)
        assert m["rotation"] == 0


# ── consent_service ──────────────────────────────────────────────────────────

class TestConsentService:
    def test_has_service_consent_false_when_no_record(self, monkeypatch):
        from app.services.juggling import consent_service
        mock_db = object()
        monkeypatch.setattr(consent_service, "get_consent", lambda uid, db: None)
        assert consent_service.has_service_consent(1, mock_db) is False

    def test_has_service_consent_false_when_false(self, monkeypatch):
        from app.services.juggling import consent_service
        from unittest.mock import MagicMock
        record = MagicMock()
        record.service_consent = False
        monkeypatch.setattr(consent_service, "get_consent", lambda uid, db: record)
        assert consent_service.has_service_consent(1, object()) is False

    def test_has_service_consent_true_when_true(self, monkeypatch):
        from app.services.juggling import consent_service
        from unittest.mock import MagicMock
        record = MagicMock()
        record.service_consent = True
        monkeypatch.setattr(consent_service, "get_consent", lambda uid, db: record)
        assert consent_service.has_service_consent(1, object()) is True
