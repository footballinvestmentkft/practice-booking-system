"""
Licence Duration Unlock tests — LD-01 … LD-10

Covers:
  LD-01  duration_months=1  → cost 100 CR, expires_at ≈ now + 1 calendar month
  LD-02  duration_months=3  → cost 250 CR
  LD-03  duration_months=6  → cost 450 CR
  LD-04  duration_months=12 → cost 800 CR
  LD-05  invalid duration_months → 400
  LD-06  insufficient credits → 400
  LD-07  unlock → expires_at NOT NULL in response
  LD-08  omit duration_months in request → defaults to 1 month
  LD-09  expired licence → card_status "expired", not "verified"
  LD-10  future expires_at + onboarding + photo → card_status "verified"
"""
import uuid
from datetime import datetime, timezone

import pytest

from ..models.license import UserLicense
from ..models.user import User, UserRole
from ..core.security import get_password_hash
from ..services.licence_package import (
    UNLOCK_DURATION_COST,
    DEFAULT_DURATION_MONTHS,
    calculate_expires_at,
    is_licence_expired,
)

# Birthday that makes the user ~25 years old — passes the LFA_PLAYER 5+ age check.
_ADULT_DOB = datetime(2000, 6, 1, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(db_session, *, email: str, credits: int = 1000) -> User:
    u = User(
        name="LDTest User",
        email=email,
        password_hash=get_password_hash("pass123"),
        role=UserRole.STUDENT,
        is_active=True,
        credit_balance=credits,
        date_of_birth=_ADULT_DOB,   # required for age check (LFA_PLAYER needs 5+)
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


def _login(client, email: str, password: str = "pass123") -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.json()["access_token"]


def _unlock(client, token: str, *, duration_months: int | None = None) -> dict:
    data = {"specialization": "LFA_PLAYER"}
    if duration_months is not None:
        data["duration_months"] = str(duration_months)
    r = client.post(
        "/specialization/unlock",
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    return r


def _verify(client, public_token: str):
    return client.get(f"/verify/{public_token}")


# ---------------------------------------------------------------------------
# LD-01 … LD-04 — cost per duration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("duration_months,expected_cost", [
    (1,  100),
    (3,  250),
    (6,  450),
    (12, 800),
])
def test_ld01_to_04_cost_matches_duration(
    client, db_session, duration_months, expected_cost
):
    """LD-01..04: cost deducted matches the selected duration."""
    email = f"ld_cost_{duration_months}@test.lfa"
    user  = _make_user(db_session, email=email, credits=expected_cost + 100)
    token = _login(client, email)

    r = _unlock(client, token, duration_months=duration_months)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["cost"] == expected_cost
    assert body["duration_months"] == duration_months
    assert body["new_balance"] == (expected_cost + 100) - expected_cost


# ---------------------------------------------------------------------------
# LD-05 — invalid duration
# ---------------------------------------------------------------------------

def test_ld05_invalid_duration_returns_400(client, db_session):
    """LD-05: duration_months not in (1,3,6,12) → 400."""
    user  = _make_user(db_session, email="ld_invalid_dur@test.lfa", credits=9999)
    token = _login(client, user.email)

    r = _unlock(client, token, duration_months=7)

    assert r.status_code == 400
    # Error format: {"error": {"message": "..."}}
    msg = (r.json().get("error") or {}).get("message", "") or r.json().get("detail", "")
    assert "duration_months" in msg.lower() or "1, 3, 6, 12" in msg


# ---------------------------------------------------------------------------
# LD-06 — insufficient credits
# ---------------------------------------------------------------------------

def test_ld06_insufficient_credits_returns_400(client, db_session):
    """LD-06: balance < cost for selected duration → 400."""
    user  = _make_user(db_session, email="ld_broke@test.lfa", credits=50)
    token = _login(client, user.email)

    # 1-month = 100 CR, user only has 50
    r = _unlock(client, token, duration_months=1)

    assert r.status_code == 400
    msg = (r.json().get("error") or {}).get("message", "") or r.json().get("detail", "")
    assert "insufficient" in msg.lower() or "credits" in msg.lower()


# ---------------------------------------------------------------------------
# LD-07 — expires_at NOT NULL after unlock
# ---------------------------------------------------------------------------

def test_ld07_expires_at_not_null_after_unlock(client, db_session):
    """LD-07: licence row in DB has expires_at set (never NULL) after unlock."""
    user  = _make_user(db_session, email="ld_expiry@test.lfa", credits=500)
    token = _login(client, user.email)

    r = _unlock(client, token, duration_months=3)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("expires_at") is not None, "expires_at must not be None in response"

    # Verify in DB
    lic = db_session.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    assert lic is not None
    assert lic.expires_at is not None, "expires_at must not be NULL in the DB row"


# ---------------------------------------------------------------------------
# LD-08 — omitting duration_months defaults to 1 month
# ---------------------------------------------------------------------------

def test_ld08_omit_duration_defaults_to_1_month(client, db_session):
    """LD-08: request without duration_months → default 1 month (100 CR)."""
    user  = _make_user(db_session, email="ld_default_dur@test.lfa", credits=500)
    token = _login(client, user.email)

    # Do NOT pass duration_months
    r = _unlock(client, token)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duration_months"] == DEFAULT_DURATION_MONTHS  # 1
    assert body["cost"] == UNLOCK_DURATION_COST[DEFAULT_DURATION_MONTHS]  # 100


# ---------------------------------------------------------------------------
# LD-09 — expired licence → card_status "expired", not "verified"
# ---------------------------------------------------------------------------

def test_ld09_expired_licence_not_verified(client, db_session):
    """LD-09: licence with expires_at in the past → verify page shows Membership Expired."""
    # Build a user that would otherwise be fully verified
    user = _make_user(db_session, email="ld_expired@test.lfa", credits=0)
    user.profile_photo_url = "/static/uploads/profile_photos/fake_expired.png"
    user.public_token       = uuid.uuid4()
    db_session.flush()

    past_expiry = datetime(2020, 1, 1, tzinfo=timezone.utc)
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        started_at=datetime(2019, 1, 1, tzinfo=timezone.utc),
        payment_verified=True,
        onboarding_completed=True,
        is_active=True,            # still True — lazy sync should flip it
        expires_at=past_expiry,
    )
    db_session.add(lic)
    db_session.commit()

    r = _verify(client, str(user.public_token))
    assert r.status_code == 200
    html = r.text

    assert "Membership Expired" in html,   "Expected 'Membership Expired' in verify page"
    assert "Verified LFA Member" not in html, "Must NOT show 'Verified LFA Member' for expired licence"


# ---------------------------------------------------------------------------
# LD-10 — future expires_at + onboarding + photo → card_status "verified"
# ---------------------------------------------------------------------------

def test_ld10_future_expiry_with_photo_and_onboarding_is_verified(client, db_session):
    """LD-10: active licence + future expires_at + onboarding + photo → verified."""
    user = _make_user(db_session, email="ld_future@test.lfa", credits=0)
    user.profile_photo_url = "/static/uploads/profile_photos/fake_future.png"
    user.public_token       = uuid.uuid4()
    db_session.flush()

    now         = datetime.now(timezone.utc)
    future_exp  = calculate_expires_at(now, 6)   # 6 months from now
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        started_at=now,
        payment_verified=True,
        onboarding_completed=True,
        is_active=True,
        expires_at=future_exp,
    )
    db_session.add(lic)
    db_session.commit()

    r = _verify(client, str(user.public_token))
    assert r.status_code == 200
    html = r.text

    assert "Verified LFA Member" in html,   "Expected 'Verified LFA Member' in verify page"
    assert "Membership Expired" not in html, "Must NOT show 'Membership Expired' for future licence"


# ---------------------------------------------------------------------------
# Helper unit tests — licence_package service
# ---------------------------------------------------------------------------

def test_calculate_expires_at_uses_calendar_months():
    """calculate_expires_at uses calendar months (relativedelta), not 30-day arithmetic."""
    # 1 month from 2026-01-31 → 2026-02-28 (not 2026-03-02)
    jan31 = datetime(2026, 1, 31, 12, 0, 0, tzinfo=timezone.utc)
    result = calculate_expires_at(jan31, 1)
    assert result.year  == 2026
    assert result.month == 2
    assert result.day   == 28


def test_is_licence_expired_null_is_perpetual():
    """is_licence_expired returns False when expires_at is None (legacy perpetual)."""
    class FakeLic:
        expires_at = None
    assert is_licence_expired(FakeLic()) is False
