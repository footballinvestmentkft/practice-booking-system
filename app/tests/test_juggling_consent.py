"""
Juggling consent endpoint tests — JC-01..JC-07.

Tests run with JUGGLING_POC_ENABLED=True (monkeypatched).
"""
from __future__ import annotations

import pytest

from app.services.juggling import feature_flag as ff_module


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _enable_juggling(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_jc01_post_consent_creates_record(client, student_token):
    """JC-01: POST juggling-consent creates record and returns 200."""
    r = client.post(
        "/api/v1/users/me/juggling-consent",
        json={"service_consent": True, "training_consent": True, "admin_review_consent": False},
        headers=_auth(student_token),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["service_consent"] is True
    assert data["training_consent"] is True
    assert data["admin_review_consent"] is False
    assert data["consented_at"] is not None


def test_jc02_get_consent_returns_record(client, student_token):
    """JC-02: GET juggling-consent returns existing record."""
    client.post(
        "/api/v1/users/me/juggling-consent",
        json={"service_consent": True},
        headers=_auth(student_token),
    )
    r = client.get("/api/v1/users/me/juggling-consent", headers=_auth(student_token))
    assert r.status_code == 200, r.text
    assert r.json()["service_consent"] is True


def test_jc03_get_consent_404_when_no_record(client, student_token):
    """JC-03: GET juggling-consent returns 404 when user has no consent record."""
    r = client.get("/api/v1/users/me/juggling-consent", headers=_auth(student_token))
    assert r.status_code == 404, r.text


def test_jc04_post_consent_is_idempotent(client, student_token):
    """JC-04: POST juggling-consent twice updates the record (idempotent upsert)."""
    client.post(
        "/api/v1/users/me/juggling-consent",
        json={"service_consent": True, "training_consent": False},
        headers=_auth(student_token),
    )
    r = client.post(
        "/api/v1/users/me/juggling-consent",
        json={"service_consent": True, "training_consent": True},
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    assert r.json()["training_consent"] is True


def test_jc05_upload_init_403_without_service_consent(client, student_token):
    """JC-05: upload-init returns 403 when service_consent is missing."""
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "uploaded_video"},
        headers=_auth(student_token),
    )
    assert r.status_code == 403, r.text


def test_jc06_upload_init_403_when_service_consent_false(client, student_token):
    """JC-06: upload-init returns 403 when service_consent=False."""
    client.post(
        "/api/v1/users/me/juggling-consent",
        json={"service_consent": False},
        headers=_auth(student_token),
    )
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "uploaded_video"},
        headers=_auth(student_token),
    )
    assert r.status_code == 403, r.text


def test_jc07_upload_init_201_after_service_consent(client, student_token):
    """JC-07: upload-init returns 201 when service_consent=True."""
    client.post(
        "/api/v1/users/me/juggling-consent",
        json={"service_consent": True},
        headers=_auth(student_token),
    )
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "uploaded_video", "upload_source": "gallery"},
        headers=_auth(student_token),
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert "video_id" in data
    assert data["status"] == "pending_upload"
    assert "upload_url" in data