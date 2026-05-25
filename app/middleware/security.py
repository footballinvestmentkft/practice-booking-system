import time
from typing import Dict, Callable, Optional
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..middleware.logging import SecurityLogger, get_current_request_id


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Production-grade rate limiting middleware with multiple strategies.
    
    Features:
    - Per-IP rate limiting
    - Per-user rate limiting (if authenticated)
    - Different limits for different endpoints
    - Sliding window algorithm
    - Automatic suspicious activity detection
    """
    
    def __init__(
        self, 
        app,
        calls: int = 100,  # Default requests per window
        window_seconds: int = 60,  # Default time window
        per_user_calls: int = 200,  # Higher limit for authenticated users
        cleanup_interval: int = 300  # Clean old entries every 5 minutes
    ):
        super().__init__(app)
        self.calls = calls
        self.window_seconds = window_seconds
        self.per_user_calls = per_user_calls
        self.cleanup_interval = cleanup_interval
        # Storage for rate limiting data
        self.ip_requests: Dict[str, deque] = defaultdict(deque)
        self.user_requests: Dict[int, deque] = defaultdict(deque)
        self.blocked_ips: Dict[str, datetime] = {}
        self.last_cleanup = time.time()
        # Endpoint-specific limits - import settings
        from ..config import settings
        self.endpoint_limits = {
            "/api/v1/auth/login": (settings.LOGIN_RATE_LIMIT_CALLS, settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS),
            "/api/v1/users/": (10, 60),     # 10 user creations per minute
            "/api/v1/bookings/": (20, 60),  # 20 bookings per minute
        }
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Periodic cleanup
        await self._cleanup_old_entries()
        
        client_ip = self._get_client_ip(request)
        user_id = await self._get_user_id(request)
        endpoint = request.url.path
        
        # Check if IP is temporarily blocked
        if await self._is_ip_blocked(client_ip):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "rate_limit_exceeded",
                    "message": "IP temporarily blocked due to excessive requests",
                    "retry_after": 300
                }
            )
        
        # Get rate limit for this endpoint
        limit, window = self._get_endpoint_limit(endpoint)
        
        # Check rate limits
        if not await self._check_rate_limit(client_ip, user_id, limit, window, endpoint):
            # Log security event
            SecurityLogger.log_suspicious_activity(
                request_id=get_current_request_id(),
                client_ip=client_ip,
                activity="rate_limit_exceeded",
                details={
                    "endpoint": endpoint,
                    "user_id": user_id,
                    "limit": limit,
                    "window": window
                }
            )
            
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"Rate limit exceeded: {limit} requests per {window} seconds",
                    "retry_after": window
                },
                headers={"Retry-After": str(window)}
            )
        
        # Add rate limiting headers to response
        response = await call_next(request)
        
        # Add rate limit headers
        remaining = await self._get_remaining_requests(client_ip, user_id, limit, window)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        response.headers["X-RateLimit-Window"] = str(window)
        
        return response
    
    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP address."""
        # Check for forwarded headers
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip
        
        return getattr(request.client, "host", "unknown")
    
    async def _get_user_id(self, request: Request) -> Optional[int]:
        """Extract user ID from request if authenticated."""
        try:
            # This is a simplified version - in real implementation,
            # you'd decode the JWT token from Authorization header
            auth_header = request.headers.get("authorization")
            if auth_header and auth_header.startswith("Bearer "):
                # TODO: Implement JWT token decoding to extract user_id
                # For now, return None (treat as unauthenticated)
                return None
        except Exception:
            pass
        return None
    
    def _get_endpoint_limit(self, endpoint: str) -> tuple[int, int]:
        """Get rate limit for specific endpoint."""
        for pattern, (limit, window) in self.endpoint_limits.items():
            if endpoint.startswith(pattern):
                return limit, window
        return self.calls, self.window_seconds
    
    async def _check_rate_limit(
        self, 
        client_ip: str, 
        user_id: Optional[int], 
        limit: int, 
        window: int,
        endpoint: str
    ) -> bool:
        """Check if request should be allowed based on rate limits."""
        now = time.time()
        window_start = now - window
        
        # Check IP-based rate limit
        ip_requests = self.ip_requests[client_ip]
        
        # Remove old requests outside window
        while ip_requests and ip_requests[0] < window_start:
            ip_requests.popleft()
        
        # Check if IP limit exceeded
        if len(ip_requests) >= limit:
            # If too many requests, block IP for a period
            if len(ip_requests) >= limit * 2:  # Aggressive blocking threshold
                self.blocked_ips[client_ip] = datetime.now(timezone.utc) + timedelta(minutes=5)
            return False
        
        # If user is authenticated, check user-based limit (higher)
        if user_id:
            user_requests = self.user_requests[user_id]
            
            # Remove old requests outside window
            while user_requests and user_requests[0] < window_start:
                user_requests.popleft()
            
            # Check if user limit exceeded
            if len(user_requests) >= self.per_user_calls:
                return False
            
            # Record request for user
            user_requests.append(now)
        
        # Record request for IP
        ip_requests.append(now)
        return True
    
    async def _get_remaining_requests(
        self,
        client_ip: str,
        user_id: Optional[int],
        limit: int,
        window: int
    ) -> int:
        """Get remaining requests for rate limit headers."""
        now = time.time()
        window_start = now - window
        
        ip_requests = self.ip_requests[client_ip]
        current_requests = sum(1 for req_time in ip_requests if req_time >= window_start)
        
        return max(0, limit - current_requests)
    
    async def _is_ip_blocked(self, client_ip: str) -> bool:
        """Check if IP is currently blocked."""
        if client_ip in self.blocked_ips:
            block_until = self.blocked_ips[client_ip]
            if datetime.now(timezone.utc) < block_until:
                return True
            else:
                # Block expired, remove it
                del self.blocked_ips[client_ip]
        return False
    
    async def _cleanup_old_entries(self):
        """Periodically clean up old entries to prevent memory leaks."""
        now = time.time()
        if now - self.last_cleanup < self.cleanup_interval:
            return
        
        # Clean up IP requests older than max window
        max_window = max(self.window_seconds, 3600)  # At least 1 hour
        cutoff_time = now - max_window
        
        for ip in list(self.ip_requests.keys()):
            requests = self.ip_requests[ip]
            while requests and requests[0] < cutoff_time:
                requests.popleft()
            if not requests:
                del self.ip_requests[ip]
        
        # Clean up user requests
        for user_id in list(self.user_requests.keys()):
            requests = self.user_requests[user_id]
            while requests and requests[0] < cutoff_time:
                requests.popleft()
            if not requests:
                del self.user_requests[user_id]
        
        # Clean up expired blocked IPs
        current_time = datetime.now(timezone.utc)
        expired_blocks = [
            ip for ip, block_time in self.blocked_ips.items()
            if current_time >= block_time
        ]
        for ip in expired_blocks:
            del self.blocked_ips[ip]
        
        self.last_cleanup = now


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Security headers middleware for production deployment.
    
    Adds essential security headers to all responses.
    """
    
    def __init__(self, app, csp_policy: Optional[str] = None):
        super().__init__(app)
        self.csp_policy = csp_policy or (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https://i.ytimg.com https:; "
            "frame-src 'self' https://www.youtube-nocookie.com https://www.youtube.com; "
            "connect-src 'self' http://localhost:8000 http://192.168.1.129:8000; "
            "font-src 'self'"
        )
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Add security headers
        security_headers = {
            # Prevent MIME type sniffing
            "X-Content-Type-Options": "nosniff",
            
            # Enable XSS protection
            "X-XSS-Protection": "1; mode=block",
            
            # Allow same-origin iframe embedding (player card preview in dashboard)
            "X-Frame-Options": "SAMEORIGIN",
            
            # Content Security Policy
            "Content-Security-Policy": self.csp_policy,
            
            # Strict Transport Security (HTTPS only)
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            
            # Referrer Policy
            "Referrer-Policy": "strict-origin-when-cross-origin",
            
            # Permissions Policy
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
            
            # Remove server information
            "Server": "Practice-Booking-API"
        }
        
        for header, value in security_headers.items():
            response.headers[header] = value
        
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware to limit request body size for security.
    """
    
    def __init__(self, app, max_size_mb: int = 10):
        super().__init__(app)
        self.max_size_bytes = max_size_mb * 1024 * 1024  # Convert MB to bytes
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Check Content-Length header
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > self.max_size_bytes:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={
                            "error": "request_too_large",
                            "message": f"Request body too large. Maximum size: {self.max_size_bytes // (1024*1024)}MB"
                        }
                    )
            except ValueError:
                pass
        
        return await call_next(request)