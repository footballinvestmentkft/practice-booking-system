from __future__ import annotations

from .processor import BackgroundProcessor


class RembgProcessor(BackgroundProcessor):
    """
    Phase 2 processor.  Uses rembg (default u2net ONNX model) to remove the
    background from a PNG image.  Returns RGBA PNG bytes with the
    background replaced by transparency.

    rembg is imported inside remove() so the web process can import this
    class without requiring rembg to be installed; the package is only
    needed in the Celery worker that calls remove().

    Any exception from rembg.remove() propagates to the caller
    (remove_background_task), which catches it and calls
    apply_removal_failure() to mark the record as 'failed'.
    """

    def remove(self, input_png_bytes: bytes) -> bytes:
        import rembg
        return rembg.remove(input_png_bytes)
