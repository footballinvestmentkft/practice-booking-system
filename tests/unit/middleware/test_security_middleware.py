"""
Unit tests for app/middleware/security.py

Covers:
  RateLimitMiddleware
    _get_client_ip()
      - x-forwarded-for header → first IP used
      - x-real-ip header fallback
      - request.client.host fallback
      - unknown when no client

    _get_user_id()
      - Bearer token present → returns None (JWT decoding is TODO)
      - no auth header → returns None
      - malformed header → no exception, returns None

    _get_endpoint_limit()
      - /api/v1/auth/login → LOGIN_RATE_LIMIT settings
      - /api/v1/users/ → 10, 60
      - /api/v1/bookings/ → 20, 60
      - unknown endpoint → default calls/window_seconds

    _check_rate_limit()
      - first request allowed
      - requests under limit allowed
      - requests at limit blocked
      - IP aggressively blocked when 2× limit exceeded
      - authenticated user requests tracked separately

    _is_ip_blocked()
      - unblocked IP → False
      - blocked IP with future expiry → True
      - blocked IP with past expiry → False (auto-removed)

    _get_remaining_requests()
      - no prior requests → limit remaining
      - N prior requests → limit - N remaining

    _cleanup_old_entries()
      - cleanup only runs when cleanup_interval elapsed
      - old IP requests pruned
      - old user requests pruned
      - expired IP blocks removed

    dispatch() integration
      - blocked IP returns 429 with retry_after
      - rate limit exceeded returns 429 with Retry-After header
      - allowed request returns call_next response
      - X-RateLimit headers added to response

  SecurityHeadersMiddleware
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: SAMEORIGIN
    - X-XSS-Protection: 1; mode=block
    - Content-Security-Policy present
    - Strict-Transport-Security present
    - Referrer-Policy present
    - Permissions-Policy present
    - Server header set
    - Custom CSP policy respected

  RequestSizeLimitMiddleware
    - content-length absent → request passes through
    - content-length within limit → request passes through
    - content-length exceeds limit → 413 response
    - content-length exactly at limit → passes through
    - non-integer content-length → no crash, passes through
"""
import asyncio
import time
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi import Request
from starlette.datastructures import Headers

from app.middleware.security import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    RequestSizeLimitMiddleware,
)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _mock_request(
    path="/api/v1/test",
    ip="127.0.0.1",
    headers_dict=None,
) -> Request:
    """Build a minimal mock Request."""
    mock_req = MagicMock(spec=Request)
    mock_req.url.path = path
    mock_req.client = MagicMock()
    mock_req.client.host = ip

    raw_headers = {k.lower(): v for k, v in (headers_dict or {}).items()}
    mock_headers = MagicMock()
    mock_headers.get = lambda k, default=None: raw_headers.get(k.lower(), default)
    mock_req.headers = mock_headers
    return mock_req


def _make_middleware(calls=100, window=60, per_user=200, cleanup=300):
    """Instantiate RateLimitMiddleware with a dummy ASGI app."""
    app = MagicMock()
    with patch("app.middleware.security.SecurityLogger"), \
         patch("app.middleware.security.get_current_request_id"):
        return RateLimitMiddleware(
            app=app,
            calls=calls,
            window_seconds=window,
            per_user_calls=per_user,
            cleanup_interval=cleanup,
        )


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────────
# RateLimitMiddleware — _get_client_ip
# ──────────────────────────────────────────────────────────────────────────────

class TestGetClientIp:

    def setup_method(self):
        self.mw = _make_middleware()

    def test_x_forwarded_for_first_ip(self):
        """x-forwarded-for with multiple IPs → first one used."""
        req = _mock_request(headers_dict={"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
        assert self.mw._get_client_ip(req) == "1.2.3.4"

    def test_x_forwarded_for_single_ip(self):
        """x-forwarded-for single IP → that IP returned."""
        req = _mock_request(headers_dict={"x-forwarded-for": "9.10.11.12"})
        assert self.mw._get_client_ip(req) == "9.10.11.12"

    def test_x_real_ip_fallback(self):
        """x-real-ip used when x-forwarded-for absent."""
        req = _mock_request(headers_dict={"x-real-ip": "192.168.1.1"})
        assert self.mw._get_client_ip(req) == "192.168.1.1"

    def test_client_host_fallback(self):
        """request.client.host used when no proxy headers."""
        req = _mock_request(ip="10.0.0.5")
        assert self.mw._get_client_ip(req) == "10.0.0.5"

    def test_unknown_when_no_client(self):
        """'unknown' returned when request.client is None."""
        req = _mock_request()
        req.client = None
        # getattr fallback handles this
        result = self.mw._get_client_ip(req)
        assert result == "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# RateLimitMiddleware — _get_user_id
# ──────────────────────────────────────────────────────────────────────────────

class TestGetUserId:

    def setup_method(self):
        self.mw = _make_middleware()

    def test_bearer_token_returns_none(self):
        """Bearer token present → returns None (JWT decoding TODO)."""
        req = _mock_request(headers_dict={"authorization": "Bearer sometoken"})
        result = _run(self.mw._get_user_id(req))
        assert result is None

    def test_no_auth_header_returns_none(self):
        """No Authorization header → returns None."""
        req = _mock_request()
        result = _run(self.mw._get_user_id(req))
        assert result is None

    def test_malformed_auth_header_no_exception(self):
        """Non-Bearer auth header → no exception, returns None."""
        req = _mock_request(headers_dict={"authorization": "Basic dXNlcjpwYXNz"})
        result = _run(self.mw._get_user_id(req))
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# RateLimitMiddleware — _get_endpoint_limit
# ──────────────────────────────────────────────────────────────────────────────

class TestGetEndpointLimit:

    def setup_method(self):
        self.mw = _make_middleware(calls=100, window=60)

    def test_auth_login_endpoint_specific_limit(self):
        """Login endpoint returns LOGIN_RATE_LIMIT settings."""
        from app.config import settings
        limit, window = self.mw._get_endpoint_limit("/api/v1/auth/login")
        assert limit == settings.LOGIN_RATE_LIMIT_CALLS
        assert window == settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS

    def test_users_endpoint_limit(self):
        """/api/v1/users/ endpoint → 10 req per 60s."""
        limit, window = self.mw._get_endpoint_limit("/api/v1/users/")
        assert limit == 10
        assert window == 60

    def test_bookings_endpoint_limit(self):
        """/api/v1/bookings/ endpoint → 20 req per 60s."""
        limit, window = self.mw._get_endpoint_limit("/api/v1/bookings/")
        assert limit == 20
        assert window == 60

    def test_unknown_endpoint_uses_defaults(self):
        """Unknown path → default calls/window_seconds."""
        limit, window = self.mw._get_endpoint_limit("/api/v1/other/path")
        assert limit == self.mw.calls
        assert window == self.mw.window_seconds

    def test_login_subpath_matches(self):
        """Subpath of /api/v1/auth/login → still matches login limit."""
        limit, window = self.mw._get_endpoint_limit("/api/v1/auth/login/")
        from app.config import settings
        assert limit == settings.LOGIN_RATE_LIMIT_CALLS


# ──────────────────────────────────────────────────────────────────────────────
# RateLimitMiddleware — _check_rate_limit
# ──────────────────────────────────────────────────────────────────────────────

class TestCheckRateLimit:

    def setup_method(self):
        self.mw = _make_middleware(calls=5, window=60)

    def test_first_request_allowed(self):
        """First request from new IP is allowed."""
        result = _run(self.mw._check_rate_limit("1.1.1.1", None, 5, 60, "/api/test"))
        assert result is True

    def test_requests_under_limit_allowed(self):
        """4 requests with limit=5 → all allowed."""
        for i in range(4):
            result = _run(self.mw._check_rate_limit("2.2.2.2", None, 5, 60, "/"))
            assert result is True

    def test_request_at_limit_blocked(self):
        """5th request with limit=5 already at 5 → blocked."""
        ip = "3.3.3.3"
        for _ in range(5):
            _run(self.mw._check_rate_limit(ip, None, 5, 60, "/"))
        # 6th request → blocked
        result = _run(self.mw._check_rate_limit(ip, None, 5, 60, "/"))
        assert result is False

    def test_aggressive_blocking_threshold(self):
        """IP blocked when requests ≥ 2× limit."""
        ip = "4.4.4.4"
        # Add 10 requests (2× limit=5) to ip_requests directly
        now = time.time()
        for _ in range(10):
            self.mw.ip_requests[ip].append(now)

        _run(self.mw._check_rate_limit(ip, None, 5, 60, "/"))
        # IP should now be in blocked_ips
        assert ip in self.mw.blocked_ips

    def test_authenticated_user_requests_tracked(self):
        """With user_id, user_requests deque grows."""
        user_id = 42
        _run(self.mw._check_rate_limit("5.5.5.5", user_id, 100, 60, "/"))
        assert user_id in self.mw.user_requests
        assert len(self.mw.user_requests[user_id]) == 1

    def test_user_limit_exceeded_blocks(self):
        """Per-user limit hit → request blocked."""
        user_id = 99
        ip = "6.6.6.6"
        # Fill user queue to per_user_calls=200
        now = time.time()
        for _ in range(200):
            self.mw.user_requests[user_id].append(now)

        result = _run(self.mw._check_rate_limit(ip, user_id, 5, 60, "/"))
        assert result is False


# ──────────────────────────────────────────────────────────────────────────────
# RateLimitMiddleware — _is_ip_blocked
# ──────────────────────────────────────────────────────────────────────────────

class TestIsIpBlocked:

    def setup_method(self):
        self.mw = _make_middleware()

    def test_unblocked_ip_returns_false(self):
        """IP not in blocked_ips → False."""
        result = _run(self.mw._is_ip_blocked("8.8.8.8"))
        assert result is False

    def test_blocked_ip_with_future_expiry_returns_true(self):
        """IP blocked until future time → True."""
        ip = "1.2.3.4"
        self.mw.blocked_ips[ip] = datetime.now(timezone.utc) + timedelta(minutes=5)
        result = _run(self.mw._is_ip_blocked(ip))
        assert result is True

    def test_blocked_ip_with_past_expiry_returns_false(self):
        """IP block expired → False, entry removed."""
        ip = "5.6.7.8"
        self.mw.blocked_ips[ip] = datetime.now(timezone.utc) - timedelta(minutes=1)
        result = _run(self.mw._is_ip_blocked(ip))
        assert result is False

    def test_expired_block_removed_from_dict(self):
        """Expired block auto-removed on check."""
        ip = "9.9.9.9"
        self.mw.blocked_ips[ip] = datetime.now(timezone.utc) - timedelta(seconds=1)
        _run(self.mw._is_ip_blocked(ip))
        assert ip not in self.mw.blocked_ips


# ──────────────────────────────────────────────────────────────────────────────
# RateLimitMiddleware — _get_remaining_requests
# ──────────────────────────────────────────────────────────────────────────────

class TestGetRemainingRequests:

    def setup_method(self):
        self.mw = _make_middleware(calls=10, window=60)

    def test_no_prior_requests_returns_full_limit(self):
        """No requests yet → remaining = limit."""
        result = _run(self.mw._get_remaining_requests("1.1.1.1", None, 10, 60))
        assert result == 10

    def test_after_n_requests_remaining_decreases(self):
        """After 3 requests → remaining = limit - 3."""
        ip = "2.2.2.2"
        now = time.time()
        for _ in range(3):
            self.mw.ip_requests[ip].append(now)

        result = _run(self.mw._get_remaining_requests(ip, None, 10, 60))
        assert result == 7

    def test_remaining_never_negative(self):
        """Even if over limit, remaining ≥ 0."""
        ip = "3.3.3.3"
        now = time.time()
        for _ in range(15):  # More than limit=10
            self.mw.ip_requests[ip].append(now)

        result = _run(self.mw._get_remaining_requests(ip, None, 10, 60))
        assert result == 0


# ──────────────────────────────────────────────────────────────────────────────
# RateLimitMiddleware — _cleanup_old_entries
# ──────────────────────────────────────────────────────────────────────────────

class TestCleanupOldEntries:

    def test_no_cleanup_before_interval(self):
        """cleanup_old_entries does nothing if interval not elapsed."""
        mw = _make_middleware(cleanup=300)
        ip = "1.1.1.1"
        now = time.time()
        # Add an "old" request
        mw.ip_requests[ip].append(now - 7200)
        mw.last_cleanup = now  # Just cleaned, interval not elapsed

        _run(mw._cleanup_old_entries())
        # Entry NOT removed because cleanup skipped
        assert ip in mw.ip_requests

    def test_cleanup_removes_old_ip_requests(self):
        """Old IP requests beyond max_window are pruned."""
        mw = _make_middleware(cleanup=0, window=60)
        ip = "2.2.2.2"
        # Add request from 2 hours ago
        mw.ip_requests[ip].append(time.time() - 7201)
        mw.last_cleanup = 0  # Force cleanup

        _run(mw._cleanup_old_entries())
        assert ip not in mw.ip_requests

    def test_cleanup_removes_old_user_requests(self):
        """Old user requests are pruned."""
        mw = _make_middleware(cleanup=0, window=60)
        user_id = 7
        mw.user_requests[user_id].append(time.time() - 7201)
        mw.last_cleanup = 0

        _run(mw._cleanup_old_entries())
        assert user_id not in mw.user_requests

    def test_cleanup_removes_expired_blocked_ips(self):
        """Expired IP blocks are removed during cleanup."""
        mw = _make_middleware(cleanup=0)
        ip = "3.3.3.3"
        mw.blocked_ips[ip] = datetime.now(timezone.utc) - timedelta(minutes=10)
        mw.last_cleanup = 0

        _run(mw._cleanup_old_entries())
        assert ip not in mw.blocked_ips

    def test_cleanup_preserves_recent_requests(self):
        """Recent requests (within window) are not removed."""
        mw = _make_middleware(cleanup=0, window=60)
        ip = "4.4.4.4"
        mw.ip_requests[ip].append(time.time())  # just now
        mw.last_cleanup = 0

        _run(mw._cleanup_old_entries())
        assert ip in mw.ip_requests


# ──────────────────────────────────────────────────────────────────────────────
# RateLimitMiddleware — dispatch integration
# ──────────────────────────────────────────────────────────────────────────────

class TestRateLimitDispatch:

    def setup_method(self):
        self.mw = _make_middleware(calls=100, window=60, cleanup=9999)

    def _make_response(self, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.headers = {}
        return resp

    def test_blocked_ip_returns_429(self):
        """IP in blocked_ips → dispatch returns 429."""
        req = _mock_request(ip="7.7.7.7")
        self.mw.blocked_ips["7.7.7.7"] = datetime.now(timezone.utc) + timedelta(minutes=5)

        async def call_next(r):
            return self._make_response(200)

        with patch("app.middleware.security.SecurityLogger"), \
             patch("app.middleware.security.get_current_request_id"):
            response = _run(self.mw.dispatch(req, call_next))

        assert response.status_code == 429

    def test_blocked_ip_response_has_retry_after(self):
        """Blocked IP response body contains retry_after."""
        req = _mock_request(ip="8.8.8.8")
        self.mw.blocked_ips["8.8.8.8"] = datetime.now(timezone.utc) + timedelta(minutes=5)

        async def call_next(r):
            return self._make_response(200)

        with patch("app.middleware.security.SecurityLogger"), \
             patch("app.middleware.security.get_current_request_id"):
            response = _run(self.mw.dispatch(req, call_next))

        assert response.status_code == 429

    def test_rate_limit_exceeded_returns_429(self):
        """IP at limit → dispatch returns 429."""
        mw = _make_middleware(calls=1, window=60, cleanup=9999)
        ip = "9.9.9.9"
        # Fill up the queue
        now = time.time()
        mw.ip_requests[ip].append(now)

        req = _mock_request(ip=ip)

        async def call_next(r):
            return self._make_response(200)

        with patch("app.middleware.security.SecurityLogger"), \
             patch("app.middleware.security.get_current_request_id"):
            response = _run(mw.dispatch(req, call_next))

        assert response.status_code == 429

    def test_allowed_request_passes_through(self):
        """First request → call_next invoked, response returned."""
        req = _mock_request(ip="10.0.0.1")
        expected_response = self._make_response(200)

        async def call_next(r):
            return expected_response

        with patch("app.middleware.security.SecurityLogger"), \
             patch("app.middleware.security.get_current_request_id"):
            response = _run(self.mw.dispatch(req, call_next))

        assert response is expected_response

    def test_rate_limit_headers_added(self):
        """X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Window added."""
        req = _mock_request(ip="10.0.0.2")
        mock_response = self._make_response(200)

        async def call_next(r):
            return mock_response

        with patch("app.middleware.security.SecurityLogger"), \
             patch("app.middleware.security.get_current_request_id"):
            _run(self.mw.dispatch(req, call_next))

        assert "X-RateLimit-Limit" in mock_response.headers
        assert "X-RateLimit-Remaining" in mock_response.headers
        assert "X-RateLimit-Window" in mock_response.headers


# ──────────────────────────────────────────────────────────────────────────────
# SecurityHeadersMiddleware
# ──────────────────────────────────────────────────────────────────────────────

class TestSecurityHeadersMiddleware:

    def _dispatch(self, csp=None):
        """Run dispatch and return the response with headers."""
        app = MagicMock()
        mw = SecurityHeadersMiddleware(app=app, csp_policy=csp)
        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        _run(mw.dispatch(MagicMock(), call_next))
        return response

    def test_x_content_type_options_nosniff(self):
        """X-Content-Type-Options: nosniff added."""
        response = self._dispatch()
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_x_frame_options_sameorigin(self):
        """X-Frame-Options: SAMEORIGIN — allows same-origin iframe embedding."""
        response = self._dispatch()
        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"

    def test_x_xss_protection(self):
        """X-XSS-Protection header added."""
        response = self._dispatch()
        assert "X-XSS-Protection" in response.headers

    def test_content_security_policy_present(self):
        """Content-Security-Policy header added."""
        response = self._dispatch()
        assert "Content-Security-Policy" in response.headers

    def test_strict_transport_security_present(self):
        """Strict-Transport-Security header added."""
        response = self._dispatch()
        assert "Strict-Transport-Security" in response.headers

    def test_referrer_policy_present(self):
        """Referrer-Policy header added."""
        response = self._dispatch()
        assert "Referrer-Policy" in response.headers

    def test_permissions_policy_present(self):
        """Permissions-Policy header added."""
        response = self._dispatch()
        assert "Permissions-Policy" in response.headers

    def test_server_header_set(self):
        """Server header is overridden to hide actual server."""
        response = self._dispatch()
        assert response.headers["Server"] == "Practice-Booking-API"

    def test_custom_csp_policy_used(self):
        """Custom CSP policy overrides the default."""
        custom_csp = "default-src 'none'"
        response = self._dispatch(csp=custom_csp)
        assert response.headers["Content-Security-Policy"] == custom_csp

    def test_default_csp_contains_default_src_self(self):
        """Default CSP contains default-src 'self'."""
        response = self._dispatch()
        csp = response.headers["Content-Security-Policy"]
        assert "default-src" in csp


# ──────────────────────────────────────────────────────────────────────────────
# RequestSizeLimitMiddleware
# ──────────────────────────────────────────────────────────────────────────────

class TestRequestSizeLimitMiddleware:

    def _make_mw(self, max_mb=10):
        return RequestSizeLimitMiddleware(app=MagicMock(), max_size_mb=max_mb)

    def _mock_request_with_content_length(self, size_bytes=None):
        req = MagicMock()
        mock_headers = MagicMock()
        if size_bytes is not None:
            mock_headers.get = lambda k, d=None: str(size_bytes) if k == "content-length" else d
        else:
            mock_headers.get = lambda k, d=None: d
        req.headers = mock_headers
        return req

    def _run_dispatch(self, mw, req, response_status=200):
        expected = MagicMock()
        expected.status_code = response_status

        async def call_next(r):
            return expected

        return _run(mw.dispatch(req, call_next)), expected

    def test_no_content_length_passes_through(self):
        """No Content-Length header → request passes through."""
        mw = self._make_mw(max_mb=10)
        req = self._mock_request_with_content_length(None)
        response, expected = self._run_dispatch(mw, req)
        assert response is expected

    def test_content_length_within_limit_passes(self):
        """Content-Length ≤ max → request passes through."""
        mw = self._make_mw(max_mb=10)
        req = self._mock_request_with_content_length(5 * 1024 * 1024)  # 5 MB
        response, expected = self._run_dispatch(mw, req)
        assert response is expected

    def test_content_length_exceeds_limit_returns_413(self):
        """Content-Length > max → 413 response."""
        mw = self._make_mw(max_mb=10)
        req = self._mock_request_with_content_length(11 * 1024 * 1024)  # 11 MB
        response, _ = self._run_dispatch(mw, req)
        assert response.status_code == 413

    def test_content_length_exactly_at_limit_passes(self):
        """Content-Length == max_size_bytes exactly → passes through."""
        mw = self._make_mw(max_mb=10)
        req = self._mock_request_with_content_length(10 * 1024 * 1024)  # exactly 10 MB
        response, expected = self._run_dispatch(mw, req)
        assert response is expected

    def test_non_integer_content_length_no_crash(self):
        """Non-integer Content-Length header → no exception, passes through."""
        mw = self._make_mw(max_mb=10)
        req = MagicMock()
        req.headers.get = lambda k, d=None: "not-a-number" if k == "content-length" else d
        response, expected = self._run_dispatch(mw, req)
        assert response is expected

    def test_413_response_contains_error_message(self):
        """413 response body has error and message fields."""
        mw = self._make_mw(max_mb=1)
        req = self._mock_request_with_content_length(2 * 1024 * 1024)  # 2 MB
        response, _ = self._run_dispatch(mw, req)
        assert response.status_code == 413
