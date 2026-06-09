"""
R4V — Verify Academy ID card_status unit tests.

V-01  no licence → card_status = "no_licence"
V-02  inactive licence → card_status = "inactive"
V-03  expired licence → card_status = "expired"
V-04  onboarding_completed = False → card_status = "onboarding_required"
V-05  no photo → card_status = "photo_required"
V-06  all conditions met → card_status = "verified"
V-07  invalid public_token → 404
V-08  future expiry → card_status = "verified"
V-09  expiry in the past (exactly) → card_status = "expired"
V-10  _compute_card_status never raises for NULL photo fields
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.api.web_routes.verify import _compute_card_status


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(photo_url=None, photo_processed_url=None):
    u = MagicMock()
    u.id = 1
    u.profile_photo_url           = photo_url
    u.profile_photo_processed_url = photo_processed_url
    return u


def _licence(
    is_active=True,
    onboarding_completed=True,
    expires_at=None,
):
    lic = MagicMock()
    lic.is_active            = is_active
    lic.onboarding_completed = onboarding_completed
    lic.expires_at           = expires_at
    return lic


def _db(licence_obj=None):
    db = MagicMock()
    (db.query.return_value
       .filter.return_value
       .order_by.return_value
       .first.return_value) = licence_obj
    return db


# ── V-01 ──────────────────────────────────────────────────────────────────────

def test_v01_no_licence_returns_no_licence():
    status, _ = _compute_card_status(_user(photo_url="/p.jpg"), _db(None))
    assert status == "no_licence"


# ── V-02 ──────────────────────────────────────────────────────────────────────

def test_v02_inactive_licence_returns_inactive():
    lic = _licence(is_active=False)
    status, _ = _compute_card_status(_user(photo_url="/p.jpg"), _db(lic))
    assert status == "inactive"


# ── V-03 ──────────────────────────────────────────────────────────────────────

def test_v03_expired_licence_returns_expired():
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    lic = _licence(expires_at=past)
    status, expiry = _compute_card_status(_user(photo_url="/p.jpg"), _db(lic))
    assert status == "expired"
    assert expiry is not None  # formatted date string present


# ── V-04 ──────────────────────────────────────────────────────────────────────

def test_v04_onboarding_not_complete_returns_onboarding_required():
    lic = _licence(onboarding_completed=False)
    status, _ = _compute_card_status(_user(photo_url="/p.jpg"), _db(lic))
    assert status == "onboarding_required"


# ── V-05 ──────────────────────────────────────────────────────────────────────

def test_v05_no_photo_returns_photo_required():
    lic = _licence()
    status, _ = _compute_card_status(_user(photo_url=None, photo_processed_url=None), _db(lic))
    assert status == "photo_required"


# ── V-06 ──────────────────────────────────────────────────────────────────────

def test_v06_all_conditions_met_returns_verified():
    lic = _licence()
    status, _ = _compute_card_status(_user(photo_url="/photo.jpg"), _db(lic))
    assert status == "verified"


def test_v06b_processed_photo_also_satisfies_photo_condition():
    lic = _licence()
    status, _ = _compute_card_status(_user(photo_processed_url="/processed.png"), _db(lic))
    assert status == "verified"


# ── V-07 ──────────────────────────────────────────────────────────────────────

def test_v07_invalid_token_returns_404(monkeypatch):
    """The route handler raises HTTPException 404 when user not found."""
    from fastapi import HTTPException
    from app.api.web_routes.verify import verify_academy_id
    import uuid

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    request = MagicMock()
    request.client.host = "127.0.0.1"

    with patch("app.api.web_routes.verify.check_verify_rate_limit", return_value=True):
        with pytest.raises(HTTPException) as exc_info:
            verify_academy_id(uuid.uuid4(), request, db=db)
    assert exc_info.value.status_code == 404


# ── V-08 ──────────────────────────────────────────────────────────────────────

def test_v08_future_expiry_returns_verified():
    future = datetime.now(timezone.utc) + timedelta(days=365)
    lic = _licence(expires_at=future)
    status, expiry = _compute_card_status(_user(photo_url="/p.jpg"), _db(lic))
    assert status == "verified"
    assert expiry is not None  # expiry display string present even for future


# ── V-09 ──────────────────────────────────────────────────────────────────────

def test_v09_past_expiry_one_second_ago_returns_expired():
    just_past = datetime.now(timezone.utc) - timedelta(seconds=1)
    lic = _licence(expires_at=just_past)
    status, _ = _compute_card_status(_user(photo_url="/p.jpg"), _db(lic))
    assert status == "expired"


# ── V-10 ──────────────────────────────────────────────────────────────────────

def test_v10_null_photo_fields_do_not_raise():
    lic = _licence()
    # Should return photo_required without raising AttributeError or TypeError
    status, _ = _compute_card_status(_user(), _db(lic))
    assert status == "photo_required"
