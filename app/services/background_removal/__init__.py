"""
background_removal package — processor factory + public interface.

get_processor() returns the active BackgroundProcessor based on
settings.BG_REMOVAL_PROCESSOR:
  "null"  → NullProcessor  (Phase 1 default; no real removal)
  "rembg" → RembgProcessor (Phase 2; requires rembg + onnxruntime-cpu)

The RembgProcessor import is deferred so that Phase 1 code never imports
rembg or onnxruntime, even if those packages happen to be installed.
"""
from __future__ import annotations

from app.config import settings

from .processor import BackgroundProcessor, NullProcessor

__all__ = ["BackgroundProcessor", "NullProcessor", "get_processor"]


def get_processor() -> BackgroundProcessor:
    """Return the processor configured by BG_REMOVAL_PROCESSOR."""
    if settings.BG_REMOVAL_PROCESSOR == "rembg":
        from .rembg_processor import RembgProcessor  # Phase 2 — deferred import
        return RembgProcessor()
    return NullProcessor()
