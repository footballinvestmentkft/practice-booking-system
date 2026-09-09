import logging
import time
import json
import os
from logging.handlers import RotatingFileHandler
from typing import Callable
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

# Canonical ContextVar — defined in request_context so domain services can
# read it without importing from middleware (avoids circular imports).
from app.core.request_context import request_id_var

# Log directory and rotation settings — all overridable via environment variables
# (LOG_DIR, LOG_MAX_BYTES, LOG_BACKUP_COUNT).  Defaults match app/config.py Settings.
_log_dir = os.getenv('LOG_DIR', 'logs')
_log_max_bytes = int(os.getenv('LOG_MAX_BYTES', str(10 * 1024 * 1024)))
_log_backup_count = int(os.getenv('LOG_BACKUP_COUNT', '5'))

# Create logs directory if it doesn't exist
os.makedirs(_log_dir, exist_ok=True)

# Configure structured logging
handlers = [logging.StreamHandler()]

# Only add file handler if not in test environment.
# RotatingFileHandler caps each log file at LOG_MAX_BYTES (default 10 MB) and
# keeps LOG_BACKUP_COUNT rotated backups (default 5) — ~50 MB max disk usage.
if not os.getenv('TESTING', '').lower() == 'true':
    handlers.append(
        RotatingFileHandler(
            os.path.join(_log_dir, 'app.log'),
            maxBytes=_log_max_bytes,
            backupCount=_log_backup_count,
            encoding='utf-8',
        )
    )

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=handlers
)

logger = logging.getLogger(__name__)

_REDACTED_HEADER_VALUE = "[REDACTED]"
_SENSITIVE_REQUEST_HEADERS = frozenset({
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-csrf-token",
    "x-csrftoken",
    "idempotency-key",
})


def _redact_sensitive_headers(headers) -> dict[str, str]:
    """Return request headers with credential and replay-token values removed."""
    return {
        name: (
            _REDACTED_HEADER_VALUE
            if name.lower() in _SENSITIVE_REQUEST_HEADERS
            else value
        )
        for name, value in headers.items()
    }


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Structured logging middleware for production monitoring.
    
    Features:
    - Request/Response logging with performance metrics
    - Unique request ID tracking
    - Error logging with stack traces
    - Security event logging
    - JSON structured logs for easy parsing
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate unique request ID
        request_id = str(uuid4())
        request_id_var.set(request_id)
        
        # Extract request details
        start_time = time.time()
        client_ip = self._get_client_ip(request)
        user_agent = request.headers.get("user-agent", "")
        method = request.method
        url = str(request.url)
        
        # Log incoming request
        request_log = {
            "event_type": "request_start",
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "url": url,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "headers": (
                _redact_sensitive_headers(request.headers)
                if logger.isEnabledFor(logging.DEBUG)
                else {}
            )
        }
        
        logger.info(json.dumps(request_log))
        
        # Process request and capture response
        try:
            response = await call_next(request)
            
            # Calculate processing time
            process_time = time.time() - start_time
            
            # Log response details
            response_log = {
                "event_type": "request_complete",
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": method,
                "url": url,
                "status_code": response.status_code,
                "process_time_ms": round(process_time * 1000, 2),
                "client_ip": client_ip,
                "response_size": self._get_response_size(response)
            }
            
            # Log level based on status code
            if response.status_code >= 500:
                logger.error(json.dumps(response_log))
            elif response.status_code >= 400:
                logger.warning(json.dumps(response_log))
            else:
                logger.info(json.dumps(response_log))
            
            # Add performance warning for slow requests
            if process_time > 2.0:  # 2 second threshold
                performance_log = {
                    "event_type": "performance_warning",
                    "request_id": request_id,
                    "message": "Slow request detected",
                    "process_time_ms": round(process_time * 1000, 2),
                    "threshold_ms": 2000,
                    "url": url,
                    "method": method
                }
                logger.warning(json.dumps(performance_log))
            
            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            
            return response
            
        except Exception as e:
            # Log unhandled exceptions
            process_time = time.time() - start_time
            error_log = {
                "event_type": "request_error",
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": method,
                "url": url,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "process_time_ms": round(process_time * 1000, 2),
                "client_ip": client_ip
            }
            logger.error(json.dumps(error_log), exc_info=True)
            raise
    
    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP address considering proxies."""
        # Check for forwarded headers (for reverse proxy setups)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip
        
        # Fallback to direct client IP
        return getattr(request.client, "host", "unknown")
    
    def _get_response_size(self, response: Response) -> int:
        """Estimate response size in bytes."""
        try:
            if isinstance(response, StreamingResponse):
                return 0  # Cannot determine streaming response size
            
            content_length = response.headers.get("content-length")
            if content_length:
                return int(content_length)
            
            # For small responses, estimate from body
            if hasattr(response, 'body') and response.body:
                return len(response.body)
                
        except Exception:
            pass
        
        return 0


class SecurityLogger:
    """Security-focused logging utilities."""
    
    @staticmethod
    def log_auth_attempt(request_id: str, email: str, success: bool, client_ip: str):
        """Log authentication attempts."""
        auth_log = {
            "event_type": "auth_attempt",
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "email": email,
            "success": success,
            "client_ip": client_ip
        }
        
        if success:
            logger.info(json.dumps(auth_log))
        else:
            logger.warning(json.dumps(auth_log))
    
    @staticmethod
    def log_permission_denied(request_id: str, user_id: int, resource: str, action: str):
        """Log permission denied events."""
        security_log = {
            "event_type": "permission_denied",
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "resource": resource,
            "action": action
        }
        logger.warning(json.dumps(security_log))
    
    @staticmethod
    def log_suspicious_activity(request_id: str, client_ip: str, activity: str, details: dict):
        """Log suspicious activities."""
        suspicious_log = {
            "event_type": "suspicious_activity",
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "client_ip": client_ip,
            "activity": activity,
            "details": details
        }
        logger.error(json.dumps(suspicious_log))


def get_current_request_id() -> str:
    """Get current request ID from context."""
    return request_id_var.get()


def log_business_event(event_name: str, user_id: int = None, data: dict = None):
    """Log business events for analytics."""
    business_log = {
        "event_type": "business_event",
        "request_id": get_current_request_id(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_name": event_name,
        "user_id": user_id,
        "data": data or {}
    }
    logger.info(json.dumps(business_log))
