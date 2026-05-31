"""Player card headless screenshot export service.

Security contract:
  render_url is ALWAYS constructed server-side from a validated int user_id
  and a whitelisted platform preset id — raw user input never reaches Playwright.
"""
import logging
import time
from collections import deque
from threading import Lock

logger = logging.getLogger(__name__)

# Re-exported from card_constants — the authoritative source for all
# platform dimensions and animated-capability declarations.
# External callers that already reference _export_svc.CANVAS_SIZES or
# _export_svc.is_animated_capable() continue to work without change.
from .card_constants import (  # noqa: E402
    CANVAS_SIZES,
    ANIMATED_EXPORT_CAPABLE,
    is_animated_capable,
)


_GOTO_TIMEOUT_MS  = 10_000  # 10 s — generous vs. measured 0.6 s
_VIDEO_TIMEOUT_MS = 30_000  # 30 s — covers 10 s recording + Chromium launch overhead

# Pre-roll: ms to wait after networkidle + document.fonts.ready before the main
# recording duration begins.  Allows DOMContentLoaded JS callbacks (OVR ring
# requestAnimationFrame, radar fade-in) to fire and the first CSS animation
# frame to commit, so the recording never starts in a half-initialized state.
# To change: update this constant only — do not touch duration_s.
_PRE_ROLL_MS = 400

# Video recording frame rate note:
# Playwright records via Chrome DevTools Protocol screencast at ~25 fps.
# This is not configurable through Playwright's public API without dropping
# to CDP level (Page.startScreencast with everyNthFrame).
# To change fps: replace record_video_dir approach with CDP screencast directly.
_VIDEO_FPS_NOTE = "~25 fps (CDP screencast default, not user-configurable via Playwright API)"

# ── FFmpeg MP4 encoding settings ──────────────────────────────────────────────
# libx264 CRF 22: visually lossless for animated cards at 1080p.
#   Lower value = better quality, larger file.  Range: 18 (near-lossless) → 28.
#   CRF 22 was chosen as the balanced default; adjust here only.
# preset "fast": ~2× faster encode than "medium" with ~5% file-size penalty.
#   Good for server-side on-demand generation of 5 s clips.
# yuv420p: mandatory — iOS and Instagram reject 4:4:4 (yuv444p) chroma.
# bt709 color tags: Playwright VP8 has no declared color space; without explicit tags
#   FFmpeg defaults to bt470bg (PAL), which macOS/iOS players misinterpret → color shift.
#   Tags: -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv
# movflags +faststart: relocates moov atom to file start for progressive web playback.
# Silent AAC audio track (lavfi anullsrc, 64 kbps): improves compatibility on
#   platforms that reject video-only MP4 (e.g. some Instagram upload paths).
_FFMPEG_CRF    = 22
_FFMPEG_PRESET = "fast"


class CardExportTimeoutError(Exception):
    """Raised when Playwright page load exceeds _GOTO_TIMEOUT_MS."""


class CardVideoRecordError(Exception):
    """Raised when Playwright video recording fails or produces no output."""


class CardMp4ConvertError(Exception):
    """Raised when FFmpeg WebM→MP4 conversion fails or the binary is absent."""


# ── PNG rate limiter: 5 exports / 60 s per rate_key ──────────────────────────
_EXPORT_LIMIT  = 5
_EXPORT_WINDOW = 60  # seconds
_rate_counters: dict[str, deque] = {}
_rate_lock = Lock()


def check_export_rate_limit(rate_key: str) -> bool:
    """Return True if the caller is within the PNG rate limit, False if exceeded."""
    now = time.monotonic()
    with _rate_lock:
        if rate_key not in _rate_counters:
            _rate_counters[rate_key] = deque()
        dq = _rate_counters[rate_key]
        cutoff = now - _EXPORT_WINDOW
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _EXPORT_LIMIT:
            return False
        dq.append(now)
        return True


def reset_rate_counters() -> None:
    """Test helper — clears all in-memory PNG rate counters."""
    with _rate_lock:
        _rate_counters.clear()


# ── Video rate limiter: 2 exports / 60 s per rate_key ────────────────────────
# Video recording is ~10× heavier than PNG — separate, tighter limit.
_VIDEO_LIMIT  = 2
_VIDEO_WINDOW = 60  # seconds
_video_rate_counters: dict[str, deque] = {}
_video_rate_lock = Lock()


def check_video_rate_limit(rate_key: str) -> bool:
    """Return True if the caller is within the video rate limit, False if exceeded."""
    now = time.monotonic()
    with _video_rate_lock:
        if rate_key not in _video_rate_counters:
            _video_rate_counters[rate_key] = deque()
        dq = _video_rate_counters[rate_key]
        cutoff = now - _VIDEO_WINDOW
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _VIDEO_LIMIT:
            return False
        dq.append(now)
        return True


def reset_video_rate_counters() -> None:
    """Test helper — clears all in-memory video rate counters."""
    with _video_rate_lock:
        _video_rate_counters.clear()


def _sync_take_screenshot(render_url: str, platform: str) -> bytes:  # pragma: no cover
    """Launch headless Chromium, navigate to render_url, return PNG bytes.

    Called via asyncio.to_thread from the async export endpoint so it does
    not block the event loop.

    For platform=="default" (FClassic Player native export):
      - render_url uses ?native_export=1 so body gets native-export-mode CSS
        (card fills 820px width at auto height, logo hidden, tab bar hidden).
      - Viewport is set tall (2000px) to avoid content clipping during render.
      - Clip is derived at runtime from .card-wrap getBoundingClientRect().
        This guarantees full capture even if content height changes with new
        skills or theme updates — 820×613 is the measured baseline, not a hard
        constraint.

    For all other platforms:
      - Viewport = canvas size; clip = full viewport (standard path).

    Raises:
        CardExportTimeoutError: if page.goto exceeds _GOTO_TIMEOUT_MS
        ValueError: if platform has no registered canvas size
    """
    canvas = CANVAS_SIZES.get(platform)
    if canvas is None:
        raise ValueError(f"No canvas size for platform: {platform!r}")
    w, _ = canvas

    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as _PWTimeout

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                if platform == "default":
                    # Native export: let the card render at its natural height.
                    # Use a tall viewport so content is not clipped during layout.
                    page = browser.new_page(viewport={"width": w, "height": 2000})
                    page.goto(render_url, wait_until="networkidle", timeout=_GOTO_TIMEOUT_MS)
                    card_rect = page.evaluate("""() => {
                        const el = document.querySelector('.card-wrap');
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return {
                            x: Math.round(r.left),
                            y: Math.round(r.top),
                            w: Math.round(r.width),
                            h: Math.round(r.height),
                        };
                    }""")
                    if card_rect is None:
                        raise ValueError("card-wrap element not found in default export render")
                    png = page.screenshot(
                        clip={
                            "x": card_rect["x"],
                            "y": card_rect["y"],
                            "width":  card_rect["w"],
                            "height": card_rect["h"],
                        },
                        type="png",
                    )
                else:
                    h = CANVAS_SIZES[platform][1]
                    page = browser.new_page(viewport={"width": w, "height": h})
                    page.goto(render_url, wait_until="networkidle", timeout=_GOTO_TIMEOUT_MS)
                    png = page.screenshot(
                        clip={"x": 0, "y": 0, "width": w, "height": h},
                        type="png",
                    )
            finally:
                browser.close()
    except _PWTimeout as exc:
        raise CardExportTimeoutError(str(exc)) from exc

    return png


def _sync_record_video(  # pragma: no cover
    render_url: str,
    platform: str,
    duration_s: int = 5,
) -> bytes:
    """Launch headless Chromium, record the animated card for duration_s, return WebM bytes.

    Called via asyncio.to_thread from the async video export endpoint so it does
    not block the event loop.

    Playwright writes a .webm file to a temp dir when context.close() is called.
    The render_url must include ?animated=1 so the template activates its CSS
    animation block — this function does not add that param itself.

    Raises:
        CardVideoRecordError: if recording times out, render URL returns an error
                              status code, or produces no WebM file.
        ValueError: if platform has no registered canvas size
    """
    import pathlib
    import tempfile

    canvas = CANVAS_SIZES.get(platform)
    if canvas is None:
        raise ValueError(f"No canvas size for platform: {platform!r}")
    w, h = canvas

    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as _PWTimeout

    logger.info("video recording started — url=%s platform=%s duration_s=%s", render_url, platform, duration_s)

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={"width": w, "height": h},
                    record_video_dir=tmp_dir,
                    record_video_size={"width": w, "height": h},
                )
                page = context.new_page()
                response = page.goto(render_url, wait_until="networkidle", timeout=_VIDEO_TIMEOUT_MS)

                # Guard: if the render URL returns an error page (404/500/etc.)
                # Playwright would silently record the white error page and return
                # it as a "successful" WebM.  Fail fast instead.
                http_status = response.status if response else 0
                logger.info(
                    "video render URL responded — status=%s url=%s",
                    http_status, render_url,
                )
                if http_status >= 400:
                    raise CardVideoRecordError(
                        f"Render URL returned HTTP {http_status} — "
                        f"check user ID, license, and template for {render_url!r}"
                    )

                # Font readiness: await document.fonts.ready so DM Mono (Google
                # Fonts CDN) is fully loaded before recording begins.
                # page.evaluate() awaits the returned Promise in Playwright sync API.
                page.evaluate("() => document.fonts.ready")
                # Pre-roll: let DOMContentLoaded JS callbacks (OVR ring
                # requestAnimationFrame, radar fade-in) fire and the first
                # CSS animation frame commit before starting the timed recording.
                page.wait_for_timeout(_PRE_ROLL_MS)
                page.wait_for_timeout(duration_s * 1000)
                context.close()   # triggers WebM finalization
                browser.close()

            webm_files = list(pathlib.Path(tmp_dir).glob("*.webm"))
            if not webm_files:
                raise CardVideoRecordError("No WebM file produced by Playwright")
            webm_size = webm_files[0].stat().st_size
            logger.info("video recording complete — webm_size=%s bytes", webm_size)
            return webm_files[0].read_bytes()
    except _PWTimeout as exc:
        raise CardVideoRecordError(str(exc)) from exc


def _webm_to_mp4(webm_bytes: bytes) -> bytes:  # pragma: no cover
    """Convert WebM bytes to MP4 (H.264/AAC) using FFmpeg.

    Encoding pipeline:
      - libx264, CRF=_FFMPEG_CRF, preset=_FFMPEG_PRESET
      - yuv420p: mandatory for iOS + Instagram compatibility (4:2:0 chroma subsampling)
      - movflags=+faststart: moov atom at file start for web streaming
      - Silent AAC stereo track (lavfi anullsrc, 64 kbps): improves upload
        compatibility on platforms that reject video-only MP4
      - -shortest: output duration matches the video stream

    Called via asyncio.to_thread from the async export endpoint.

    Raises:
        CardMp4ConvertError: if the ffmpeg binary is absent, returns non-zero,
                             times out (>60 s), or produces no output file.
    """
    import pathlib
    import subprocess
    import tempfile
    from contextlib import ExitStack

    with tempfile.TemporaryDirectory() as tmp:
        in_path  = pathlib.Path(tmp) / "input.webm"
        out_path = pathlib.Path(tmp) / "output.mp4"
        in_path.write_bytes(webm_bytes)

        cmd = [
            "ffmpeg", "-y",
            "-i",          str(in_path),
            # Silent audio source for platform compatibility
            "-f",          "lavfi",
            "-i",          "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-map",        "0:v:0",
            "-map",        "1:a:0",
            # Video: H.264, quality/speed settings documented in _FFMPEG_CRF / _FFMPEG_PRESET
            "-c:v",        "libx264",
            "-crf",        str(_FFMPEG_CRF),
            "-preset",     _FFMPEG_PRESET,
            "-pix_fmt",    "yuv420p",
            # Color space: explicit bt709 tags prevent macOS/iOS misreading VP8's
            # undeclared color space as bt470bg (PAL), which shifts perceived colors.
            "-colorspace",        "bt709",
            "-color_primaries",   "bt709",
            "-color_trc",         "bt709",
            "-color_range",       "tv",
            # Audio: AAC, 64 kbps, ends when video ends
            "-c:a",        "aac",
            "-b:a",        "64k",
            "-shortest",
            # Container: progressive web playback
            "-movflags",   "+faststart",
            str(out_path),
        ]

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,  # suppresses ffmpeg progress noise in logs
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise CardMp4ConvertError(
                "ffmpeg binary not found — install ffmpeg (apt: ffmpeg, brew: ffmpeg)"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise CardMp4ConvertError(
                f"ffmpeg exited with code {exc.returncode}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CardMp4ConvertError("ffmpeg timed out after 60 s") from exc

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise CardMp4ConvertError("ffmpeg produced no output file")

        return out_path.read_bytes()
