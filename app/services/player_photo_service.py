"""
LFA Football Player card photo service.

Stores spec-specific photos in app/static/uploads/lfa_player_photos/:
  {user_id}_orig_{epoch}.png          — card photo (full-figure, aspect-ratio preserved, alpha kept)
  {user_id}_portrait_{epoch}.png      — variant portrait photo (9:16, alpha preserved)
  {user_id}_landscape_{epoch}.png     — variant landscape photo (16:9, alpha preserved)
  {user_id}_bg_compact_{epoch}.png    — compact-variant background (800×800 max, alpha preserved)
  {user_id}_bg_showcase_{epoch}.png   — showcase-variant background (800×800 max, alpha preserved)

Every upload writes a NEW epoch-timestamped filename, so the URL always changes.
This guarantees browser/CDN cache-busting: the old URL is never reused.

Old timestamped files and legacy fixed-name files are deleted before each save.

All slots are completely separate from any global User avatar/profile picture.
"""
import io
import time
from pathlib import Path

from PIL import Image

ALLOWED_MIME: set[str] = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES:    int = 2 * 1024 * 1024          # 2 MB — cutout/card photos
MAX_BG_BYTES: int = 8 * 1024 * 1024          # 8 MB — background photos
MAX_CARD_SIZE: tuple[int, int] = (800, 1200) # max fit box — no crop, aspect ratio preserved
PHOTO_DIR: Path = Path("app/static/uploads/lfa_player_photos")

# Variant photo target dimensions
_PORTRAIT_SIZE:  tuple[int, int] = (450, 800)   # 9:16
_LANDSCAPE_SIZE: tuple[int, int] = (800, 450)   # 16:9


def save_player_photo(file_bytes: bytes, content_type: str, user_id: int) -> str:
    """Validate, fit inside MAX_CARD_SIZE, save as PNG preserving alpha. Returns static URL.

    No cropping — the full player figure is always retained.
    thumbnail() fits the image inside (800, 1200) while keeping the original
    aspect ratio and never upscaling.  Alpha channel is preserved end-to-end
    so background-removed PNGs render transparently on the card.

    Filename: {user_id}_orig_{epoch}.png — unique per upload (cache-bust),
    _orig_ prefix avoids collisions with variant filenames.
    """
    if content_type not in ALLOWED_MIME:
        raise ValueError(f"Nem támogatott képformátum: {content_type}. Elfogadott: JPEG, PNG, WEBP")
    if len(file_bytes) > MAX_BYTES:
        raise ValueError("A fájl mérete meghaladja a 2 MB-os korlátot")

    try:
        img = Image.open(io.BytesIO(file_bytes))
    except Exception:
        raise ValueError("Érvénytelen képfájl")

    # Preserve alpha: palette (P) must be converted first to keep transparent index
    if img.mode == "P":
        img = img.convert("RGBA")
    elif img.mode not in ("RGBA", "RGB"):
        img = img.convert("RGBA")
    # RGB and RGBA pass through unchanged — no alpha destruction

    # Fit inside max box — NO crop, aspect ratio preserved, never upscales
    img.thumbnail(MAX_CARD_SIZE, Image.LANCZOS)

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)

    # Delete any previous card photo files for this user before saving the new one
    delete_player_photo(user_id)

    ts = int(time.time())
    out_path = PHOTO_DIR / f"{user_id}_orig_{ts}.png"
    img.save(out_path, "PNG", optimize=True)

    return f"/static/uploads/lfa_player_photos/{user_id}_orig_{ts}.png"


def delete_player_photo(user_id: int) -> None:
    """Remove card photo files for this user (_orig_ prefix only). Silent no-op if missing.

    Explicitly does NOT touch variant photo files (_portrait_*, _landscape_*, _bg_*).
    Those are managed by their own delete functions.
    """
    if not PHOTO_DIR.exists():
        return
    # Current-style epoch-timestamped PNG card photos
    for f in PHOTO_DIR.glob(f"{user_id}_orig_*.png"):
        f.unlink()
    # Old-style timestamped JPEG card photos (digit-only suffix, e.g. 42_1712345678.jpg)
    for f in PHOTO_DIR.glob(f"{user_id}_*.jpg"):
        stem_suffix = f.stem[len(f"{user_id}_"):]
        if stem_suffix.isdigit():
            f.unlink()
    # Legacy pre-timestamp file
    legacy = PHOTO_DIR / f"{user_id}.jpg"
    if legacy.exists():
        legacy.unlink()


# ── Variant photo helpers ──────────────────────────────────────────────────


def _delete_variant_files(user_id: int, suffix: str) -> None:
    """Delete all timestamped and legacy fixed-name files for a given variant suffix.

    Deletes:
      {user_id}_{suffix}_{epoch}.png  — current-style timestamped files (all of them)
      {user_id}_{suffix}.png          — legacy fixed-name file (no timestamp)
    """
    if not PHOTO_DIR.exists():
        return
    for f in PHOTO_DIR.glob(f"{user_id}_{suffix}_*.png"):
        f.unlink()
    legacy = PHOTO_DIR / f"{user_id}_{suffix}.png"
    if legacy.exists():
        legacy.unlink()


def _save_variant_photo(
    file_bytes: bytes,
    content_type: str,
    user_id: int,
    target_size: tuple[int, int],
    suffix: str,
    max_bytes: int = MAX_BYTES,
) -> str:
    """Save a PNG for a card variant with a unique epoch-timestamped filename.

    Every call produces a new URL ({user_id}_{suffix}_{epoch}.png), guaranteeing
    that browser and CDN caches never serve a stale image after re-upload.

    Old timestamped files and the legacy fixed-name file are deleted before saving.

    Strategy: fit (no aggressive crop). The uploaded PNG is already a prepared
    cutout — we preserve the full image by fitting it inside target_size with
    thumbnail() (aspect-ratio-safe). Alpha channel is preserved throughout.

    Returns the static URL.
    """
    if content_type not in ALLOWED_MIME:
        raise ValueError(f"Nem támogatott képformátum: {content_type}. Elfogadott: JPEG, PNG, WEBP")
    if len(file_bytes) > max_bytes:
        raise ValueError(f"A fájl mérete meghaladja a {max_bytes // (1024 * 1024)} MB-os korlátot")

    try:
        img = Image.open(io.BytesIO(file_bytes))
    except Exception:
        raise ValueError("Érvénytelen képfájl")

    # Ensure RGBA so alpha is available (JPEG input → treat as fully opaque)
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Fit inside target box — thumbnail() keeps aspect ratio, never upscales
    img.thumbnail(target_size, Image.LANCZOS)

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)

    # Delete all previous files for this slot before writing the new one
    _delete_variant_files(user_id, suffix)

    ts = int(time.time())
    out_path = PHOTO_DIR / f"{user_id}_{suffix}_{ts}.png"
    img.save(out_path, "PNG", optimize=True)

    return f"/static/uploads/lfa_player_photos/{user_id}_{suffix}_{ts}.png"


def save_portrait_photo(file_bytes: bytes, content_type: str, user_id: int) -> str:
    """Fit into 9:16 box, preserve alpha, save as PNG. Returns static URL."""
    return _save_variant_photo(file_bytes, content_type, user_id, _PORTRAIT_SIZE, "portrait")


def delete_portrait_photo(user_id: int) -> None:
    """Remove all portrait PNG files (timestamped + legacy). Silent no-op if missing."""
    _delete_variant_files(user_id, "portrait")


def save_landscape_photo(file_bytes: bytes, content_type: str, user_id: int) -> str:
    """Fit into 16:9 box, preserve alpha, save as PNG. Returns static URL."""
    return _save_variant_photo(file_bytes, content_type, user_id, _LANDSCAPE_SIZE, "landscape")


def delete_landscape_photo(user_id: int) -> None:
    """Remove all landscape PNG files (timestamped + legacy). Silent no-op if missing."""
    _delete_variant_files(user_id, "landscape")


# ── Variant background photo helpers ──────────────────────────────────────────

_BG_SIZE: tuple[int, int] = (800, 800)  # max fit box for background images


def save_compact_bg_photo(file_bytes: bytes, content_type: str, user_id: int) -> str:
    """Fit into 800×800 box, preserve alpha, save as PNG. Returns static URL."""
    return _save_variant_photo(file_bytes, content_type, user_id, _BG_SIZE, "bg_compact", max_bytes=MAX_BG_BYTES)


def delete_compact_bg_photo(user_id: int) -> None:
    """Remove all compact background PNG files (timestamped + legacy). Silent no-op if missing."""
    _delete_variant_files(user_id, "bg_compact")


def save_showcase_bg_photo(file_bytes: bytes, content_type: str, user_id: int) -> str:
    """Fit into 800×800 box, preserve alpha, save as PNG. Returns static URL."""
    return _save_variant_photo(file_bytes, content_type, user_id, _BG_SIZE, "bg_showcase", max_bytes=MAX_BG_BYTES)


def delete_showcase_bg_photo(user_id: int) -> None:
    """Remove all showcase background PNG files (timestamped + legacy). Silent no-op if missing."""
    _delete_variant_files(user_id, "bg_showcase")
