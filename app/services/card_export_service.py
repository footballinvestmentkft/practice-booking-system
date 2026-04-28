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

# Social canvas sizes — "default" is intentionally absent (not an export target)
CANVAS_SIZES: dict[str, tuple[int, int]] = {
    "square":    (1080, 1080),
    "story":     (1080, 1920),
    "landscape": (1920, 1080),
    "banner":    (1500,  500),
    "og":        (1200,  630),
}

_GOTO_TIMEOUT_MS = 10_000  # 10 s — generous vs. measured 0.6 s


class CardExportTimeoutError(Exception):
    """Raised when Playwright page load exceeds _GOTO_TIMEOUT_MS."""


# ── In-memory rate limiter: 5 exports / 60 s per rate_key ────────────────────
_EXPORT_LIMIT  = 5
_EXPORT_WINDOW = 60  # seconds
_rate_counters: dict[str, deque] = {}
_rate_lock = Lock()


def check_export_rate_limit(rate_key: str) -> bool:
    """Return True if the caller is within the rate limit, False if exceeded."""
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
    """Test helper — clears all in-memory rate counters."""
    with _rate_lock:
        _rate_counters.clear()


def _sync_take_screenshot(render_url: str, platform: str) -> bytes:
    """Launch headless Chromium, navigate to render_url, return PNG bytes.

    Called via asyncio.to_thread from the async export endpoint so it does
    not block the event loop.

    Raises:
        CardExportTimeoutError: if page.goto exceeds _GOTO_TIMEOUT_MS
        ValueError: if platform has no registered canvas size
    """
    canvas = CANVAS_SIZES.get(platform)
    if canvas is None:
        raise ValueError(f"No canvas size for platform: {platform!r}")
    w, h = canvas

    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as _PWTimeout

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
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
