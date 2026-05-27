"""
Background removal processor interface.

Phase 1: NullProcessor — returns the input image unchanged.
         The Remove Background button is hidden from users when this processor
         is active (BG_REMOVAL_PROCESSOR="null").

Phase 2: RembgProcessor (rembg_processor.py) — real U2Net background removal.
         Only used when BG_REMOVAL_PROCESSOR="rembg" and rembg + onnxruntime
         are installed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BackgroundProcessor(ABC):
    @abstractmethod
    def remove(self, input_png_bytes: bytes) -> bytes:
        """
        Accept PNG bytes, return PNG bytes with background removed (or unchanged).
        Input is already a valid PNG (converted and resized by save_mood_photo).
        """
        ...


class NullProcessor(BackgroundProcessor):
    """
    Phase 1 passthrough processor.  Returns the input image without any
    modification.  Used when BG_REMOVAL_PROCESSOR="null" (default).

    The user-facing "Remove Background" button is hidden when this processor
    is active — the pipeline is wired end-to-end for testing but no removal
    claim is shown to users.
    """

    def remove(self, input_png_bytes: bytes) -> bytes:
        return input_png_bytes
