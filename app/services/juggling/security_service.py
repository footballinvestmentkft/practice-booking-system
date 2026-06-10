"""
Juggling video upload security validation.

Layered pre-save checks (all run before the file touches disk):
  1. Extension allowlist: .mp4, .mov, .m4v
  2. MIME allowlist: video/mp4, video/quicktime, video/x-m4v
  3. File magic bytes: ISO Base Media ftyp box (MP4/MOV container)
  4. Empty file reject
  5. File size limit (JUGGLING_VIDEO_MAX_SIZE_MB from config)

All failures raise ValueError with a machine-readable reason code.
The upload endpoint maps ValueError → HTTP 415 or 413 or 400.

Note on magic bytes:
  MP4 and MOV use the ISO Base Media File Format (ISOBMFF).
  The ftyp box is always at the start (bytes 4–7 = b"ftyp").
  Accepted ftyp brands: isom, iso2, avc1, mp41, mp42, M4V_, qt__, hvc1, hevc
  moov/wide/mdat alone are NOT used — they appear anywhere in the file.
"""
from __future__ import annotations

import hashlib
import uuid

from app.config import settings

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mov", ".m4v"})
_ALLOWED_MIMES: frozenset[str] = frozenset({
    "video/mp4", "video/quicktime", "video/x-m4v",
})
# ftyp brands accepted for MP4/MOV container identification
_ACCEPTED_FTYP_BRANDS: frozenset[bytes] = frozenset({
    b"isom", b"iso2", b"avc1", b"mp41", b"mp42",
    b"M4V ", b"qt  ", b"hvc1", b"hevc", b"iso4",
    b"iso6", b"mmp4", b"f4v ", b"MSNV", b"NDSC",
})


class VideoSecurityError(ValueError):
    """Raised when a video upload fails a pre-save security check."""


def validate_extension(filename: str) -> str:
    """
    Return the lowercased extension if allowed; raise VideoSecurityError otherwise.
    The client-supplied filename is used ONLY to extract the extension.
    It is never propagated to the filesystem.
    """
    # Guard against path traversal in the extension itself
    safe_name = filename.replace("..", "").replace("/", "").replace("\\", "")
    dot_idx = safe_name.rfind(".")
    if dot_idx == -1:
        raise VideoSecurityError(
            "unsupported_extension: no file extension found"
        )
    ext = safe_name[dot_idx:].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise VideoSecurityError(
            f"unsupported_extension: {ext!r} is not in {sorted(_ALLOWED_EXTENSIONS)}"
        )
    return ext


def validate_mime(content_type: str) -> None:
    """Raise VideoSecurityError if content_type is not in the allowlist."""
    # Normalise: strip parameters (e.g. "video/mp4; codecs=avc1")
    base_mime = content_type.split(";")[0].strip().lower()
    if base_mime not in _ALLOWED_MIMES:
        raise VideoSecurityError(
            f"unsupported_mime: {content_type!r} is not in {sorted(_ALLOWED_MIMES)}"
        )


def validate_magic_bytes(data: bytes) -> None:
    """
    Verify that the file starts with a valid ISO Base Media ftyp box.

    Structure: [4-byte box size][b"ftyp"][4-byte major brand][4-byte minor version]
               [N×4-byte compatible brands]

    We check bytes[4:8] == b"ftyp" and that major_brand is in _ACCEPTED_FTYP_BRANDS.
    This is server-side, non-spoofable, and runs before the file is saved.
    """
    if len(data) < 12:
        raise VideoSecurityError(
            "magic_bytes_invalid: file too short to contain a valid ftyp box"
        )
    box_type = data[4:8]
    if box_type != b"ftyp":
        raise VideoSecurityError(
            f"magic_bytes_invalid: expected ftyp box at offset 4, "
            f"found {box_type!r}. Not a valid MP4/MOV container."
        )
    major_brand = data[8:12]
    if major_brand not in _ACCEPTED_FTYP_BRANDS:
        raise VideoSecurityError(
            f"magic_bytes_invalid: unsupported ftyp major brand {major_brand!r}"
        )


def validate_size(file_bytes: bytes) -> None:
    """Raise VideoSecurityError if file is empty or exceeds the configured limit."""
    if len(file_bytes) == 0:
        raise VideoSecurityError("empty_file: uploaded file contains no data")
    max_bytes = settings.JUGGLING_VIDEO_MAX_SIZE_MB * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise VideoSecurityError(
            f"file_too_large: {len(file_bytes):,} bytes exceeds "
            f"limit of {settings.JUGGLING_VIDEO_MAX_SIZE_MB} MB "
            f"({max_bytes:,} bytes)"
        )


def generate_server_filename(ext: str) -> str:
    """Return a server-generated UUID filename. Client name is never used."""
    return f"{uuid.uuid4()}{ext}"


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_all_pre_save_checks(
    client_filename: str,
    content_type: str,
    file_bytes: bytes,
) -> tuple[str, str]:
    """
    Run all pre-save security checks in order.
    Returns (server_filename, checksum_sha256) on success.
    Raises VideoSecurityError on first failure.
    """
    # 1. Extension
    ext = validate_extension(client_filename)
    # 2. MIME
    validate_mime(content_type)
    # 3. Empty + size — checked before magic bytes so empty file → 400, not 415
    validate_size(file_bytes)
    # 4. Magic bytes (server-side, non-spoofable)
    validate_magic_bytes(file_bytes)
    # 5. Generate server-side filename and checksum
    server_filename = generate_server_filename(ext)
    checksum = compute_sha256(file_bytes)
    return server_filename, checksum