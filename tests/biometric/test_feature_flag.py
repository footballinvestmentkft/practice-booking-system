"""
Feature flag tests.

BMF-01  is_biometric_enabled() returns False by default
BMF-02  is_biometric_enabled() returns True when monkeypatched
BMF-03  require_biometric_enabled() raises HTTPException 503 when flag off
BMF-04  require_biometric_enabled() does NOT raise when flag on
BMF-05  503 detail message references BIOMETRIC_FACE_MATCHING_ENABLED
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.services.biometric.feature_flag import (
    is_biometric_enabled,
    require_biometric_enabled,
)


def _run(coro):
    return asyncio.run(coro)


# ── BMF-01 / BMF-02 ──────────────────────────────────────────────────────────

def test_bmf01_flag_off_by_default():
    assert is_biometric_enabled() is False


def test_bmf02_flag_on_when_patched(monkeypatch):
    monkeypatch.setattr(
        "app.services.biometric.feature_flag.settings.BIOMETRIC_FACE_MATCHING_ENABLED",
        True,
    )
    assert is_biometric_enabled() is True


# ── BMF-03 / BMF-04 / BMF-05 ─────────────────────────────────────────────────

def test_bmf03_require_raises_503_when_flag_off():
    with pytest.raises(HTTPException) as exc_info:
        _run(require_biometric_enabled())
    assert exc_info.value.status_code == 503


def test_bmf04_require_does_not_raise_when_flag_on(monkeypatch):
    monkeypatch.setattr(
        "app.services.biometric.feature_flag.settings.BIOMETRIC_FACE_MATCHING_ENABLED",
        True,
    )
    _run(require_biometric_enabled())   # must not raise


def test_bmf05_503_detail_mentions_flag():
    with pytest.raises(HTTPException) as exc_info:
        _run(require_biometric_enabled())
    assert "BIOMETRIC_FACE_MATCHING_ENABLED" in exc_info.value.detail