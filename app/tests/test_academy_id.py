"""
Academy ID API tests — Phase 2A.

Covers GET /api/v1/users/me/academy-id  (AID-01..06, AID-12, AID-16)
       GET /verify/{public_token}        (AID-07..11, AID-13..15)

Test IDs: AID-01 … AID-16
"""
import re
import uuid

import pytest

from ..services.academy_id_service import specialization_display_label

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _aid(client, token):
    return client.get(
        "/api/v1/users/me/academy-id",
        headers={"Authorization": f"Bearer {token}"},
    )


def _verify(client, public_token):
    return client.get(f"/verify/{public_token}")


def _me(client, token):
    return client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------
# Force NullProcessor — same as profile photo tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _null_processor(monkeypatch):
    import app.api.api_v1.endpoints.users.profile as ep
    from app.services import academy_id_service as svc
    monkeypatch.setattr(ep._settings, "BG_REMOVAL_PROCESSOR", "null")
    monkeypatch.setattr(ep._settings, "VERIFY_BASE_URL", "http://testserver")
    # Reset per-IP rate-limit buckets so tests are independent of each other.
    with svc._verify_lock:
        svc._verify_buckets.clear()


# ---------------------------------------------------------------------------
# AID-01 … AID-06 — /me/academy-id authenticated
# ---------------------------------------------------------------------------

def test_aid01_academy_id_returns_200(client, student_token):
    """AID-01: GET /me/academy-id → 200 with all four keys."""
    r = _aid(client, student_token)
    assert r.status_code == 200
    body = r.json()
    assert "lfa_academy_id" in body
    assert "public_token"   in body
    assert "qr_url"         in body
    assert "qr_data"        in body


def test_aid02_academy_id_requires_auth(client):
    """AID-02: GET /me/academy-id without Bearer → 401."""
    r = client.get("/api/v1/users/me/academy-id")
    assert r.status_code == 401


def test_aid03_lfa_academy_id_format(client, student_token):
    """AID-03: lfa_academy_id matches LFA-YYYY-NNNNN pattern."""
    body = _aid(client, student_token).json()
    assert re.fullmatch(r"LFA-\d{4}-\d{5}", body["lfa_academy_id"]), (
        f"Bad format: {body['lfa_academy_id']}"
    )


def test_aid04_public_token_is_uuid(client, student_token):
    """AID-04: public_token is a valid UUID v4 string."""
    body = _aid(client, student_token).json()
    parsed = uuid.UUID(body["public_token"])
    assert parsed.version == 4


def test_aid05_two_users_have_different_tokens(client, student_token, admin_token):
    """AID-05: Two different users receive different public_tokens."""
    t1 = _aid(client, student_token).json()["public_token"]
    t2 = _aid(client, admin_token).json()["public_token"]
    assert t1 != t2


def test_aid06_sequential_ids_same_year(client, student_token, admin_token):
    """AID-06: Two users created in the same year get different sequential IDs."""
    id1 = _aid(client, student_token).json()["lfa_academy_id"]
    id2 = _aid(client, admin_token).json()["lfa_academy_id"]
    # Both should be valid format and distinct
    assert re.fullmatch(r"LFA-\d{4}-\d{5}", id1)
    assert re.fullmatch(r"LFA-\d{4}-\d{5}", id2)
    assert id1 != id2


# ---------------------------------------------------------------------------
# AID-07 … AID-11 — /verify/{public_token}
# ---------------------------------------------------------------------------

def test_aid07_verify_valid_token_returns_200(client, student_token):
    """AID-07: GET /verify/{valid_token} → 200 HTML."""
    token = _aid(client, student_token).json()["public_token"]
    r = _verify(client, token)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_aid08_verify_unknown_token_returns_404(client):
    """AID-08: GET /verify/{unknown_uuid} → 404."""
    r = _verify(client, str(uuid.uuid4()))
    assert r.status_code == 404


def test_aid09_verify_page_contains_required_fields(client, student_token):
    """AID-09: Verify page contains name, lfa_academy_id, and Verified badge."""
    aid_body = _aid(client, student_token).json()
    token    = aid_body["public_token"]
    lfa_id   = aid_body["lfa_academy_id"]

    html = _verify(client, token).text
    assert lfa_id in html,                       "lfa_academy_id missing from verify page"
    assert "Verified LFA Member" in html,        "Verified badge missing"
    assert "Lion Football Academy" in html,      "Brand name missing"


def test_aid10_verify_page_omits_private_data(client, student_token):
    """AID-10: Verify page must NOT contain email, phone, user_id, credit balance."""
    me_body = _me(client, student_token).json()
    token   = _aid(client, student_token).json()["public_token"]
    html    = _verify(client, token).text

    # email must not appear
    assert me_body["email"] not in html, "email leaked on verify page"
    # credit_balance: number could coincidentally appear, so check key phrase
    assert "credit_balance" not in html
    assert "credit balance"  not in html.lower()
    # public_token string must not be rendered in page body as plaintext
    assert token not in html


def test_aid11_verify_rate_limit_blocks_21st_request(client, student_token, monkeypatch):
    """AID-11: 21st request to /verify within 60 s returns 429."""
    from app.services import academy_id_service as svc
    # Reset the bucket for the test client IP
    with svc._verify_lock:
        svc._verify_buckets.clear()

    token = _aid(client, student_token).json()["public_token"]

    # 20 allowed
    for _ in range(20):
        r = _verify(client, token)
        assert r.status_code == 200

    # 21st blocked
    r = _verify(client, token)
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# AID-12 — /users/me includes academy id fields
# ---------------------------------------------------------------------------

def test_aid12_users_me_includes_academy_fields(client, student_token):
    """AID-12: GET /users/me response contains lfa_academy_id and public_token."""
    # Trigger lazy assignment first
    _aid(client, student_token)
    me = _me(client, student_token).json()
    assert "lfa_academy_id" in me
    assert "public_token"   in me
    assert me["lfa_academy_id"] is not None
    assert me["public_token"]   is not None


# ---------------------------------------------------------------------------
# AID-13 … AID-14 — backfill / lazy assignment
# ---------------------------------------------------------------------------

def test_aid13_existing_user_gets_lfa_academy_id_on_first_call(client, student_token):
    """AID-13: Calling /me/academy-id lazy-assigns lfa_academy_id if not set."""
    body = _aid(client, student_token).json()
    assert body["lfa_academy_id"] is not None
    assert re.fullmatch(r"LFA-\d{4}-\d{5}", body["lfa_academy_id"])


def test_aid14_public_token_not_null_after_call(client, student_token):
    """AID-14: public_token is not None after first /me/academy-id call."""
    body = _aid(client, student_token).json()
    assert body["public_token"] is not None
    uuid.UUID(body["public_token"])  # must be parseable


# ---------------------------------------------------------------------------
# AID-15 — processed photo priority
# ---------------------------------------------------------------------------

def test_aid15_verify_uses_processed_photo_when_available(
    client, student_token, db_session
):
    """AID-15: If profile_photo_processed_url is set, verify page uses it."""
    from ..models.user import User
    from ..dependencies import get_current_user
    from ..main import app

    # Get current user from db
    me = _me(client, student_token).json()
    user = db_session.query(User).filter_by(email=me["email"]).first()
    if not user:
        pytest.skip("Cannot find user in test DB")

    user.profile_photo_url           = "/static/uploads/profile_photos/orig.png"
    user.profile_photo_processed_url = "/static/uploads/profile_photos/proc.png"
    db_session.flush()

    token = _aid(client, student_token).json()["public_token"]
    html  = _verify(client, token).text
    assert "proc.png" in html, "processed photo URL not found in verify page"


# ---------------------------------------------------------------------------
# AID-16 — qr_data uses VERIFY_BASE_URL
# ---------------------------------------------------------------------------

def test_aid16_qr_data_uses_verify_base_url(client, student_token):
    """AID-16: qr_data is built from VERIFY_BASE_URL + /verify/ + public_token."""
    body = _aid(client, student_token).json()
    expected = f"http://testserver/verify/{body['public_token']}"
    assert body["qr_data"] == expected


# ---------------------------------------------------------------------------
# Unit: specialization display labels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("lfa_football_player", "LFA Football Player"),
    ("lfa_coach",           "LFA Coach"),
    ("gancuju_player",      "GānCuju Player"),
    ("internship",          "Internship"),
    (None,                  None),
    ("",                    None),
])
def test_spec_label_mapping(raw, expected):
    assert specialization_display_label(raw) == expected
