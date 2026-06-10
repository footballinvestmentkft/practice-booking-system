"""
Juggling feature flag tests — JFF-01..JFF-06.

Verifies that every juggling endpoint returns HTTP 503 when
JUGGLING_POC_ENABLED=False (default).
"""
from __future__ import annotations

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_jff01_consent_post_returns_503_when_disabled(client, student_token):
    """JFF-01: POST juggling-consent → 503 when flag off."""
    r = client.post(
        "/api/v1/users/me/juggling-consent",
        json={"service_consent": True},
        headers=_auth(student_token),
    )
    assert r.status_code == 503, r.text


def test_jff02_consent_get_returns_503_when_disabled(client, student_token):
    """JFF-02: GET juggling-consent → 503 when flag off."""
    r = client.get(
        "/api/v1/users/me/juggling-consent",
        headers=_auth(student_token),
    )
    assert r.status_code == 503, r.text


def test_jff03_upload_init_returns_503_when_disabled(client, student_token):
    """JFF-03: POST upload-init → 503 when flag off."""
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "uploaded_video"},
        headers=_auth(student_token),
    )
    assert r.status_code == 503, r.text


def test_jff04_upload_file_returns_503_when_disabled(client, student_token):
    """JFF-04: POST upload file → 503 when flag off."""
    fake_id = "00000000-0000-0000-0000-000000000001"
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{fake_id}/upload",
        files={"file": ("test.mp4", b"\x00" * 8, "video/mp4")},
        headers=_auth(student_token),
    )
    assert r.status_code == 503, r.text


def test_jff05_complete_returns_503_when_disabled(client, student_token):
    """JFF-05: POST complete → 503 when flag off."""
    fake_id = "00000000-0000-0000-0000-000000000001"
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{fake_id}/complete",
        headers=_auth(student_token),
    )
    assert r.status_code == 503, r.text


def test_jff06_quality_returns_503_when_disabled(client, student_token):
    """JFF-06: GET quality → 503 when flag off."""
    fake_id = "00000000-0000-0000-0000-000000000001"
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{fake_id}/quality",
        headers=_auth(student_token),
    )
    assert r.status_code == 503, r.text
