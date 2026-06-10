"""
Biometric security monitoring tests — PR-8.

Rate limiter unit tests:
  BSEC-01  user_key format — no PII, contains endpoint_group and user_id
  BSEC-02  ip_key hashes IP — no plaintext IP in key
  BSEC-03  _hash_ip returns exactly 16 hex chars
  BSEC-04  check_rate_limit allows first N requests (in-memory fallback)
  BSEC-05  check_rate_limit blocks request N+1 (in-memory fallback)
  BSEC-06  enforce_rate_limit raises HTTP 429 when limit exceeded
  BSEC-07  enforce_rate_limit 429 response has Retry-After header
  BSEC-08  enforce_rate_limit writes EVT_RATE_LIMITED when db provided
  BSEC-09  enforce_rate_limit with only user_id: no IP key checked
  BSEC-10  record_verify_outcome resets counter on "verified"
  BSEC-11  record_verify_outcome increments counter on "rejected"
  BSEC-12  record_verify_outcome writes EVT_VERIFY_ABUSE_DETECTED at threshold
  BSEC-31  _hash_ip(None) returns "unknown" sentinel
  BSEC-32  _is_fail_open() returns False when env var unset
  BSEC-33  _is_fail_open() returns True when BIOMETRIC_RATE_LIMIT_FAIL_OPEN=true
  BSEC-34  enforce_rate_limit raises 503 in production fail-closed (no Redis)
  BSEC-35  enforce_rate_limit bypasses in production fail-open (no Redis)

Metrics unit tests:
  BSEC-13  biometric_metrics.increment creates counter
  BSEC-14  biometric_metrics.get returns correct value
  BSEC-15  biometric_metrics.reset clears all counters
  BSEC-16  unknown label key sanitized to "unknown" (no PII injection)
  BSEC-17  unknown label value sanitized to "unknown"
  BSEC-18  separate label combinations stored independently

Audit event constant tests:
  BSEC-19  EVT_RATE_LIMITED in ALL_EVENT_TYPES
  BSEC-20  EVT_VERIFY_ABUSE_DETECTED in ALL_EVENT_TYPES
  BSEC-21  EVT_DISCLOSURE_STALE_ATTEMPT in ALL_EVENT_TYPES
  BSEC-22  EVT_ADMIN_OVERRIDE_SELF_ATTEMPT in ALL_EVENT_TYPES
  BSEC-23  EVT_ADMIN_HISTORY_ACCESSED in ALL_EVENT_TYPES
  BSEC-24  assert_disclosure_current writes EVT_DISCLOSURE_STALE_ATTEMPT for stale version

Endpoint security integration tests:
  BSEC-25  verify endpoint propagates 429 when enforce_rate_limit fires
  BSEC-26  verify endpoint calls record_verify_outcome with correct outcome
  BSEC-27  admin history endpoint writes EVT_ADMIN_HISTORY_ACCESSED audit
  BSEC-28  admin override self-attempt writes EVT_ADMIN_OVERRIDE_SELF_ATTEMPT
  BSEC-29  admin override self-attempt returns 403 self_override_forbidden
  BSEC-30  admin override increments M_ADMIN_OVERRIDE metric after success
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.biometric import BiometricVerificationLog
from app.models.user import User, UserRole
from app.services.biometric.audit_log import (
    ALL_EVENT_TYPES,
    EVT_ADMIN_HISTORY_ACCESSED,
    EVT_ADMIN_OVERRIDE_SELF_ATTEMPT,
    EVT_DISCLOSURE_STALE_ATTEMPT,
    EVT_RATE_LIMITED,
    EVT_VERIFY_ABUSE_DETECTED,
)
from app.services.biometric.metrics import (
    M_ADMIN_OVERRIDE,
    biometric_metrics,
)
from app.services.biometric.rate_limiter import (
    VERIFY,
    _hash_ip,
    _is_fail_open,
    admin_key,
    check_rate_limit,
    enforce_rate_limit,
    ip_key,
    record_verify_outcome,
    user_key,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(fn):
    import inspect
    if inspect.iscoroutine(fn):
        return asyncio.run(fn)
    return fn


def _unique_key(suffix: str = "") -> str:
    """Return a key that won't collide with other tests."""
    return f"bsec_test:{uuid.uuid4().hex}{suffix}"


def _force_mem_fallback(monkeypatch):
    """Disable Redis so the in-memory fallback is used."""
    monkeypatch.setattr("app.services.biometric.rate_limiter._redis_available", False)
    monkeypatch.setattr("app.services.biometric.rate_limiter._redis_client", None)


def _mock_request(ip: str = "192.168.1.1"):
    req = MagicMock()
    req.headers.get = lambda key, default=None: None
    req.client.host = ip
    return req


def _make_admin(db) -> User:
    admin = User(
        name="BSEC Admin",
        email=f"bsec_admin_{uuid.uuid4().hex[:8]}@test.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
        date_of_birth=date(1980, 1, 1),
    )
    db.add(admin)
    db.flush()
    return admin


def _make_student(db) -> User:
    student = User(
        name="BSEC Student",
        email=f"bsec_student_{uuid.uuid4().hex[:8]}@test.com",
        password_hash="hashed",
        role=UserRole.STUDENT,
        date_of_birth=date(1998, 1, 1),
    )
    db.add(student)
    db.flush()
    return student


# ── BSEC-01..03  Key helpers ──────────────────────────────────────────────────

def test_bsec01_user_key_format():
    k = user_key("verify", 99)
    assert k == "biometric_rl:user:verify:99"
    assert "99" in k
    assert "verify" in k


def test_bsec02_ip_key_hashes_ip():
    ip = "203.0.113.42"
    k = ip_key("verify", ip)
    assert ip not in k, "Plaintext IP must not appear in rate limit key"
    assert "biometric_rl:ip:verify:" in k


def test_bsec03_hash_ip_returns_16_hex_chars():
    digest = _hash_ip("10.0.0.1")
    assert len(digest) == 16
    assert all(c in "0123456789abcdef" for c in digest)


# ── BSEC-04..05  In-memory check_rate_limit ───────────────────────────────────

def test_bsec04_check_rate_limit_allows_first_requests(monkeypatch):
    _force_mem_fallback(monkeypatch)
    key = _unique_key(":bsec04")
    # VERIFY limit is 5 / 900s; first 5 should be allowed
    for _ in range(5):
        assert check_rate_limit(key, VERIFY) is True


def test_bsec05_check_rate_limit_blocks_after_limit(monkeypatch):
    _force_mem_fallback(monkeypatch)
    key = _unique_key(":bsec05")
    for _ in range(5):
        check_rate_limit(key, VERIFY)
    # 6th must be blocked
    assert check_rate_limit(key, VERIFY) is False


# ── BSEC-06..09  enforce_rate_limit ──────────────────────────────────────────

def test_bsec06_enforce_rate_limit_raises_429_when_exceeded(monkeypatch):
    _force_mem_fallback(monkeypatch)
    uid = abs(hash(_unique_key(":bsec06"))) % 10_000_000 + 20_000_000

    for _ in range(5):
        check_rate_limit(user_key(VERIFY, uid), VERIFY)

    with pytest.raises(HTTPException) as exc_info:
        enforce_rate_limit(endpoint_group=VERIFY, user_id=uid)
    assert exc_info.value.status_code == 429


def test_bsec07_enforce_rate_limit_429_has_retry_after_header(monkeypatch):
    _force_mem_fallback(monkeypatch)
    uid = abs(hash(_unique_key(":bsec07"))) % 10_000_000 + 30_000_000

    for _ in range(5):
        check_rate_limit(user_key(VERIFY, uid), VERIFY)

    with pytest.raises(HTTPException) as exc_info:
        enforce_rate_limit(endpoint_group=VERIFY, user_id=uid)
    headers = exc_info.value.headers or {}
    assert "Retry-After" in headers
    assert int(headers["Retry-After"]) > 0


def test_bsec08_enforce_rate_limit_writes_evt_rate_limited(db, monkeypatch):
    _force_mem_fallback(monkeypatch)
    student = _make_student(db)

    for _ in range(5):
        check_rate_limit(user_key(VERIFY, student.id), VERIFY)

    with pytest.raises(HTTPException):
        enforce_rate_limit(
            endpoint_group=VERIFY,
            user_id=student.id,
            db=db,
            audit_user_id=student.id,
        )

    log = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student.id,
        BiometricVerificationLog.event_type == EVT_RATE_LIMITED,
    ).first()
    assert log is not None, "EVT_RATE_LIMITED must be written when db is provided"


def test_bsec09_enforce_rate_limit_user_only_no_ip(monkeypatch):
    """When ip=None, only the user_id key is checked (no IP key)."""
    _force_mem_fallback(monkeypatch)
    uid = abs(hash(_unique_key(":bsec09"))) % 10_000_000 + 40_000_000
    # Should not raise on first call even with ip=None
    enforce_rate_limit(endpoint_group=VERIFY, user_id=uid, ip=None)


# ── BSEC-10..12  record_verify_outcome ───────────────────────────────────────

def test_bsec10_record_verify_outcome_resets_on_verified(db, monkeypatch):
    _force_mem_fallback(monkeypatch)
    student = _make_student(db)

    # Register 2 rejections
    for _ in range(2):
        record_verify_outcome(user_id=student.id, outcome="rejected", db=db)

    # A "verified" outcome should reset
    record_verify_outcome(user_id=student.id, outcome="verified", db=db)

    # After reset, 2 more rejections should NOT trigger abuse yet
    from app.services.biometric.rate_limiter import (
        _VERIFY_ABUSE_THRESHOLD, _verify_abuse_key, _mem_store,
    )
    key = _verify_abuse_key(student.id)
    with __import__("app.services.biometric.rate_limiter", fromlist=["_mem_lock"])._mem_lock:
        count = _mem_store.get(key, (0, 0))[0]
    assert count == 0, "Abuse counter must be reset to 0 after 'verified' outcome"


def test_bsec11_record_verify_outcome_increments_on_rejected(db, monkeypatch):
    _force_mem_fallback(monkeypatch)
    student = _make_student(db)

    record_verify_outcome(user_id=student.id, outcome="rejected", db=db)

    from app.services.biometric.rate_limiter import _verify_abuse_key, _mem_store
    key = _verify_abuse_key(student.id)
    import app.services.biometric.rate_limiter as rl
    with rl._mem_lock:
        count = _mem_store.get(key, (0, 0))[0]
    assert count == 1


def test_bsec12_record_verify_outcome_writes_abuse_detected_at_threshold(db, monkeypatch):
    _force_mem_fallback(monkeypatch)
    student = _make_student(db)

    from app.services.biometric.rate_limiter import _VERIFY_ABUSE_THRESHOLD
    for _ in range(_VERIFY_ABUSE_THRESHOLD):
        record_verify_outcome(user_id=student.id, outcome="rejected", db=db)

    log = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student.id,
        BiometricVerificationLog.event_type == EVT_VERIFY_ABUSE_DETECTED,
    ).first()
    assert log is not None, "EVT_VERIFY_ABUSE_DETECTED must be written at threshold"


# ── BSEC-13..18  Metrics ─────────────────────────────────────────────────────

def test_bsec13_metrics_increment_creates_counter():
    biometric_metrics.reset()
    biometric_metrics.increment("biometric_verify_attempt_total", outcome="verified")
    assert biometric_metrics.get("biometric_verify_attempt_total", outcome="verified") == 1


def test_bsec14_metrics_get_returns_correct_value():
    biometric_metrics.reset()
    for _ in range(7):
        biometric_metrics.increment("biometric_verify_rejected_total")
    assert biometric_metrics.get("biometric_verify_rejected_total") == 7


def test_bsec15_metrics_reset_clears_counters():
    biometric_metrics.increment("biometric_verify_attempt_total", outcome="rejected")
    biometric_metrics.reset()
    assert biometric_metrics.get("biometric_verify_attempt_total", outcome="rejected") == 0


def test_bsec16_unknown_label_key_sanitized_to_unknown():
    biometric_metrics.reset()
    # "user_id" is not a whitelisted label key — must be sanitized
    biometric_metrics.increment("biometric_verify_attempt_total", user_id="42")
    # The counter is stored with key="unknown"
    val = biometric_metrics.get("biometric_verify_attempt_total", user_id="unknown")
    assert val == 1, "Unknown label key must be sanitized to 'unknown'"


def test_bsec17_unknown_label_value_sanitized_to_unknown():
    biometric_metrics.reset()
    # "outcome" is a known key but "hacked_value" is not in OUTCOME_LABELS
    biometric_metrics.increment("biometric_verify_attempt_total", outcome="hacked_value")
    val = biometric_metrics.get("biometric_verify_attempt_total", outcome="unknown")
    assert val == 1, "Unknown label value must be sanitized to 'unknown'"


def test_bsec18_separate_label_combinations_stored_independently():
    biometric_metrics.reset()
    biometric_metrics.increment("biometric_verify_attempt_total", outcome="verified")
    biometric_metrics.increment("biometric_verify_attempt_total", outcome="rejected")
    assert biometric_metrics.get("biometric_verify_attempt_total", outcome="verified") == 1
    assert biometric_metrics.get("biometric_verify_attempt_total", outcome="rejected") == 1


# ── BSEC-19..23  Audit event constants ───────────────────────────────────────

def test_bsec19_evt_rate_limited_in_all_event_types():
    assert EVT_RATE_LIMITED in ALL_EVENT_TYPES


def test_bsec20_evt_verify_abuse_detected_in_all_event_types():
    assert EVT_VERIFY_ABUSE_DETECTED in ALL_EVENT_TYPES


def test_bsec21_evt_disclosure_stale_attempt_in_all_event_types():
    assert EVT_DISCLOSURE_STALE_ATTEMPT in ALL_EVENT_TYPES


def test_bsec22_evt_admin_override_self_attempt_in_all_event_types():
    assert EVT_ADMIN_OVERRIDE_SELF_ATTEMPT in ALL_EVENT_TYPES


def test_bsec23_evt_admin_history_accessed_in_all_event_types():
    assert EVT_ADMIN_HISTORY_ACCESSED in ALL_EVENT_TYPES


# ── BSEC-24  Stale disclosure audit ──────────────────────────────────────────

def test_bsec24_assert_disclosure_current_writes_stale_attempt_audit(
    db, student_user, monkeypatch
):
    monkeypatch.setattr("app.config.settings.BIOMETRIC_DISCLOSURE_ENABLED", True)
    from app.services.biometric.disclosure_service import accept_disclosure
    accept_disclosure(db=db, user=student_user, disclosure_version="v1.0")
    db.flush()

    # Simulate a disclosure version bump so the accepted one is now stale
    monkeypatch.setattr(
        "app.services.biometric.disclosure_service.settings"
        ".CURRENT_BIOMETRIC_DISCLOSURE_VERSION",
        "v2.0",
    )
    monkeypatch.setattr("app.config.settings.CURRENT_BIOMETRIC_DISCLOSURE_VERSION", "v2.0")

    from app.services.biometric.disclosure_service import assert_disclosure_current
    with pytest.raises(HTTPException) as exc_info:
        assert_disclosure_current(db=db, user_id=student_user.id)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "biometric_disclosure_update_required"

    log = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student_user.id,
        BiometricVerificationLog.event_type == EVT_DISCLOSURE_STALE_ATTEMPT,
    ).first()
    assert log is not None, "EVT_DISCLOSURE_STALE_ATTEMPT must be written to audit log"
    assert "v1.0" in (log.error_message or "")
    assert "v2.0" in (log.error_message or "")


# ── BSEC-25..26  Verify endpoint ─────────────────────────────────────────────

def test_bsec25_verify_endpoint_propagates_429_from_rate_limit(db, student_user, biometric_feature_enabled, monkeypatch):
    from app.api.api_v1.endpoints.users.biometric_verify import verify_biometric
    from app.schemas.biometric import BiometricVerifyRequest

    # enforce_rate_limit is imported locally in the function body, so patch at source
    with patch(
        "app.services.biometric.rate_limiter.enforce_rate_limit",
        side_effect=HTTPException(status_code=429, detail="rate_limited"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(verify_biometric(
                payload=BiometricVerifyRequest(photo_filename="test.jpg"),
                db=db,
                current_user=student_user,
            ))
    assert exc_info.value.status_code == 429


def test_bsec26_verify_endpoint_calls_record_verify_outcome(
    db, student_user, biometric_feature_enabled, encryption_test_key, allow_test_key, monkeypatch
):
    from app.api.api_v1.endpoints.users.biometric_verify import verify_biometric
    from app.schemas.biometric import BiometricVerifyRequest
    from app.services.biometric.consent_service import grant_consent
    from app.services.biometric.disclosure_service import accept_disclosure
    from app.services.biometric.embedding_service import FakeEmbeddingProvider, store_embedding

    monkeypatch.setattr("app.config.settings.BIOMETRIC_DISCLOSURE_ENABLED", True)
    accept_disclosure(db=db, user=student_user, disclosure_version="v1.0")
    db.flush()
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    emb = FakeEmbeddingProvider().generate(b"verify_photo.jpg")
    row = store_embedding(db=db, user_id=student_user.id, embedding=emb, model_version="fake_v1")
    row.is_active = True
    db.flush()

    with patch(
        "app.services.biometric.rate_limiter.record_verify_outcome"
    ) as mock_rvo:
        _run(verify_biometric(
            payload=BiometricVerifyRequest(photo_filename="verify_photo.jpg"),
            db=db,
            current_user=student_user,
        ))
    mock_rvo.assert_called_once()
    call_kwargs = mock_rvo.call_args.kwargs
    assert call_kwargs["user_id"] == student_user.id
    assert call_kwargs["outcome"] in ("verified", "manual_review_required", "rejected")


# ── BSEC-27  Admin history audit ─────────────────────────────────────────────

def test_bsec27_admin_history_writes_evt_admin_history_accessed(db, biometric_feature_enabled):
    from app.api.api_v1.endpoints.admin_biometric_review import admin_get_user_history

    admin = _make_admin(db)
    target = _make_student(db)

    _run(admin_get_user_history(
        user_id=target.id,
        db=db,
        current_admin=admin,
    ))

    log = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == target.id,
        BiometricVerificationLog.event_type == EVT_ADMIN_HISTORY_ACCESSED,
        BiometricVerificationLog.actor_user_id == admin.id,
    ).first()
    assert log is not None, "EVT_ADMIN_HISTORY_ACCESSED must be written by admin_get_user_history"


# ── BSEC-28..29  Admin self-override ─────────────────────────────────────────

def test_bsec28_admin_self_override_writes_evt_admin_override_self_attempt(
    db, biometric_feature_enabled
):
    from app.api.api_v1.endpoints.admin_biometric_review import admin_override_biometric
    from app.schemas.biometric import AdminBiometricOverrideRequest

    admin = _make_admin(db)
    from app.services.biometric.disclosure_service import accept_disclosure
    from app.services.biometric.consent_service import grant_consent
    accept_disclosure(db=db, user=admin, disclosure_version="v1.0")
    grant_consent(db=db, user=admin, consent_version="v1.0")
    admin.face_match_status = "manual_review_required"
    admin.manual_review_required = True
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        _run(admin_override_biometric(
            user_id=admin.id,
            payload=AdminBiometricOverrideRequest(decision="approved"),
            request=_mock_request(),
            db=db,
            current_admin=admin,
        ))
    assert exc_info.value.status_code == 403

    log = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == admin.id,
        BiometricVerificationLog.event_type == EVT_ADMIN_OVERRIDE_SELF_ATTEMPT,
    ).first()
    assert log is not None, "EVT_ADMIN_OVERRIDE_SELF_ATTEMPT must be written on self-override"
    assert log.actor_user_id == admin.id


def test_bsec29_admin_self_override_returns_403(db, biometric_feature_enabled):
    from app.api.api_v1.endpoints.admin_biometric_review import admin_override_biometric
    from app.schemas.biometric import AdminBiometricOverrideRequest

    admin = _make_admin(db)

    with pytest.raises(HTTPException) as exc_info:
        _run(admin_override_biometric(
            user_id=admin.id,
            payload=AdminBiometricOverrideRequest(decision="approved"),
            request=_mock_request(),
            db=db,
            current_admin=admin,
        ))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "self_override_forbidden"


# ── BSEC-30  M_ADMIN_OVERRIDE metric ─────────────────────────────────────────

def test_bsec30_admin_override_increments_metric(db, biometric_feature_enabled):
    from app.api.api_v1.endpoints.admin_biometric_review import admin_override_biometric
    from app.schemas.biometric import AdminBiometricOverrideRequest

    admin = _make_admin(db)
    target = _make_student(db)

    from app.services.biometric.disclosure_service import accept_disclosure
    from app.services.biometric.consent_service import grant_consent
    accept_disclosure(db=db, user=target, disclosure_version="v1.0")
    grant_consent(db=db, user=target, consent_version="v1.0")
    target.face_match_status = "manual_review_required"
    target.manual_review_required = True
    db.flush()

    biometric_metrics.reset()

    _run(admin_override_biometric(
        user_id=target.id,
        payload=AdminBiometricOverrideRequest(decision="approved"),
        request=_mock_request(),
        db=db,
        current_admin=admin,
    ))

    assert biometric_metrics.get(M_ADMIN_OVERRIDE, decision="approved") == 1


# ── BSEC-31..35  Coverage: edge cases + production paths ─────────────────────

def test_bsec31_hash_ip_none_returns_unknown():
    assert _hash_ip(None) == "unknown"


def test_bsec32_hash_ip_empty_returns_unknown():
    assert _hash_ip("") == "unknown"


def test_bsec33_is_fail_open_default_false(monkeypatch):
    monkeypatch.delenv("BIOMETRIC_RATE_LIMIT_FAIL_OPEN", raising=False)
    assert _is_fail_open() is False


def test_bsec34_is_fail_open_true_when_env_set(monkeypatch):
    monkeypatch.setenv("BIOMETRIC_RATE_LIMIT_FAIL_OPEN", "true")
    assert _is_fail_open() is True


def test_bsec35_enforce_rate_limit_503_in_production_fail_closed(monkeypatch):
    """Production + no Redis + BIOMETRIC_RATE_LIMIT_FAIL_OPEN=false → HTTP 503."""
    import app.services.biometric.rate_limiter as rl
    monkeypatch.setattr(rl, "_redis_available", False)
    monkeypatch.setattr(rl, "_redis_client", None)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("BIOMETRIC_RATE_LIMIT_FAIL_OPEN", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        enforce_rate_limit(endpoint_group=VERIFY, user_id=99999)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "biometric_rate_limiter_unavailable"


def test_bsec36_enforce_rate_limit_bypass_in_production_fail_open(monkeypatch):
    """Production + no Redis + BIOMETRIC_RATE_LIMIT_FAIL_OPEN=true → allowed (CRITICAL log)."""
    import app.services.biometric.rate_limiter as rl
    monkeypatch.setattr(rl, "_redis_available", False)
    monkeypatch.setattr(rl, "_redis_client", None)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("BIOMETRIC_RATE_LIMIT_FAIL_OPEN", "true")

    # Should NOT raise — fail-open returns without checking keys
    enforce_rate_limit(endpoint_group=VERIFY, user_id=99998)


def test_bsec37_check_rate_limit_production_safety_net(monkeypatch):
    """check_rate_limit production safety net: no Redis in production → True (secondary guard)."""
    import app.services.biometric.rate_limiter as rl
    monkeypatch.setattr(rl, "_redis_available", False)
    monkeypatch.setattr(rl, "_redis_client", None)
    monkeypatch.setenv("ENV", "production")

    key = _unique_key(":bsec37")
    result = check_rate_limit(key, VERIFY)
    assert result is True, "check_rate_limit production safety net must return True (fail-open)"
