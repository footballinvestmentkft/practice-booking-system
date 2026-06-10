"""
Juggling security service unit tests — JS-01..JS-15.

Tests run against security_service.py directly (no HTTP layer needed).
"""
from __future__ import annotations

import struct

import pytest

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


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_ftyp_box(brand: bytes = b"isom") -> bytes:
    """Minimal valid ftyp box: [size=20][ftyp][brand][minor_ver=0][compat_brand]"""
    size = 20
    return struct.pack(">I", size) + b"ftyp" + brand + b"\x00\x00\x00\x00" + brand


def _mp4_bytes(brand: bytes = b"isom") -> bytes:
    """Return bytes that start with a valid ftyp box followed by zeros."""
    return _make_ftyp_box(brand) + b"\x00" * 100


# ── Extension tests ───────────────────────────────────────────────────────────

def test_js01_valid_extensions_accepted():
    """JS-01: .mp4, .mov, .m4v are accepted."""
    assert validate_extension("video.mp4") == ".mp4"
    assert validate_extension("clip.MOV") == ".mov"
    assert validate_extension("session.m4v") == ".m4v"


def test_js02_invalid_extension_raises():
    """JS-02: .avi, .mkv, .webm, .mp3 are rejected."""
    for name in ("video.avi", "video.mkv", "video.webm", "audio.mp3"):
        with pytest.raises(VideoSecurityError, match="unsupported_extension"):
            validate_extension(name)


def test_js03_path_traversal_in_filename_safe():
    """JS-03: path traversal characters in client filename do not raise on extension extraction."""
    # The .. and / are stripped before extension extraction — no crash, just normal rejection
    with pytest.raises(VideoSecurityError):
        validate_extension("../../etc/passwd")


def test_js04_no_extension_raises():
    """JS-04: filename with no extension raises."""
    with pytest.raises(VideoSecurityError, match="unsupported_extension"):
        validate_extension("videofile")


# ── MIME tests ────────────────────────────────────────────────────────────────

def test_js05_valid_mimes_accepted():
    """JS-05: video/mp4, video/quicktime, video/x-m4v are accepted."""
    validate_mime("video/mp4")
    validate_mime("video/quicktime")
    validate_mime("video/x-m4v")
    validate_mime("video/mp4; codecs=avc1")  # with parameters


def test_js06_invalid_mime_raises():
    """JS-06: image/jpeg, application/octet-stream are rejected."""
    for mime in ("image/jpeg", "application/octet-stream", "video/avi"):
        with pytest.raises(VideoSecurityError, match="unsupported_mime"):
            validate_mime(mime)


# ── Magic bytes tests ─────────────────────────────────────────────────────────

def test_js07_valid_ftyp_isom_accepted():
    """JS-07: ftyp box with isom brand is accepted."""
    validate_magic_bytes(_mp4_bytes(b"isom"))


def test_js08_valid_ftyp_brands_accepted():
    """JS-08: All accepted ftyp brands pass."""
    for brand in (b"iso2", b"avc1", b"mp41", b"mp42", b"qt  ", b"hvc1"):
        validate_magic_bytes(_mp4_bytes(brand))


def test_js09_jpeg_magic_rejected():
    """JS-09: JPEG magic bytes (FF D8 FF) with .mp4 extension — magic check fails."""
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 20
    with pytest.raises(VideoSecurityError, match="magic_bytes_invalid"):
        validate_magic_bytes(jpeg_bytes)


def test_js10_too_short_bytes_rejected():
    """JS-10: File shorter than 12 bytes fails magic check."""
    with pytest.raises(VideoSecurityError, match="magic_bytes_invalid"):
        validate_magic_bytes(b"\x00" * 8)


def test_js11_moov_only_rejected():
    """JS-11: File starting with moov (no ftyp) is rejected — moov alone is insufficient."""
    moov_bytes = b"\x00\x00\x00\x08" + b"moov" + b"\x00" * 100
    with pytest.raises(VideoSecurityError, match="magic_bytes_invalid"):
        validate_magic_bytes(moov_bytes)


# ── Size tests ────────────────────────────────────────────────────────────────

def test_js12_empty_file_raises():
    """JS-12: Empty file (0 bytes) raises with empty_file code."""
    with pytest.raises(VideoSecurityError, match="empty_file"):
        validate_size(b"")


def test_js13_oversized_file_raises(monkeypatch):
    """JS-13: File exceeding JUGGLING_VIDEO_MAX_SIZE_MB raises file_too_large."""
    from app.services.juggling import security_service as ss
    monkeypatch.setattr(ss.settings, "JUGGLING_VIDEO_MAX_SIZE_MB", 1)
    big = b"\x00" * (2 * 1024 * 1024)  # 2 MB > 1 MB limit
    with pytest.raises(VideoSecurityError, match="file_too_large"):
        validate_size(big)


# ── Filename / checksum tests ─────────────────────────────────────────────────

def test_js14_server_filename_is_uuid_not_client_name():
    """JS-14: server-generated filename is UUID-based, never client name."""
    fname = generate_server_filename(".mp4")
    assert fname.endswith(".mp4")
    assert "client" not in fname
    assert "/" not in fname
    assert ".." not in fname
    # Should be UUID4 format: 8-4-4-4-12 hex chars
    stem = fname[:-4]
    assert len(stem) == 36  # UUID string length


def test_js15_checksum_sha256_is_hex_64():
    """JS-15: compute_sha256 returns 64-char hex string."""
    digest = compute_sha256(b"hello world")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_js16_run_all_checks_pass_returns_filename_and_checksum():
    """JS-16: run_all_pre_save_checks returns (server_filename, checksum) on valid input."""
    data = _mp4_bytes()
    server_fname, checksum = run_all_pre_save_checks(
        client_filename="myvideo.mp4",
        content_type="video/mp4",
        file_bytes=data,
    )
    assert server_fname.endswith(".mp4")
    assert len(checksum) == 64
    # Client filename is NOT used in server filename
    assert "myvideo" not in server_fname


def test_js17_run_all_checks_order_extension_first():
    """JS-17: Extension check runs first — .avi rejected before MIME or magic checked."""
    data = _mp4_bytes()  # valid bytes but wrong extension
    with pytest.raises(VideoSecurityError, match="unsupported_extension"):
        run_all_pre_save_checks("video.avi", "video/mp4", data)


def test_js18_empty_file_returns_400_error_not_415():
    """JS-18: Empty file raises empty_file (not magic_bytes_invalid) so endpoint returns 400."""
    with pytest.raises(VideoSecurityError, match="empty_file"):
        run_all_pre_save_checks("video.mp4", "video/mp4", b"")