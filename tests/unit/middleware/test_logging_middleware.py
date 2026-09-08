import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.logging import LoggingMiddleware, logger


def test_sensitive_request_headers_are_redacted_from_debug_logs(caplog):
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)

    @app.get("/logging-probe")
    async def logging_probe():
        return {"ok": True}

    sensitive_headers = {
        "Authorization": "Bearer real.jwt-like.token",
        "Cookie": "access_token=Bearer cookie.jwt-like.token; csrf_token=cookie-csrf-secret",
        "Set-Cookie": "access_token=response-cookie-secret; HttpOnly",
        "Proxy-Authorization": "Basic proxy-credential-secret",
        "X-CSRF-Token": "csrf-header-secret",
        "X-CSRFToken": "alternate-csrf-header-secret",
        "Idempotency-Key": "renewal-replay-secret",
    }

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        response = TestClient(app).get(
            "/logging-probe",
            headers={**sensitive_headers, "X-Diagnostic": "safe-to-log"},
        )

    assert response.status_code == 200
    log_output = "\n".join(record.getMessage() for record in caplog.records)
    for raw_value in sensitive_headers.values():
        assert raw_value not in log_output
    for secret_fragment in (
        "real.jwt-like.token",
        "cookie.jwt-like.token",
        "cookie-csrf-secret",
        "response-cookie-secret",
        "proxy-credential-secret",
        "csrf-header-secret",
        "alternate-csrf-header-secret",
        "renewal-replay-secret",
    ):
        assert secret_fragment not in log_output

    request_log = next(
        json.loads(record.getMessage())
        for record in caplog.records
        if json.loads(record.getMessage()).get("event_type") == "request_start"
    )
    assert request_log["headers"]["authorization"] == "[REDACTED]"
    assert request_log["headers"]["cookie"] == "[REDACTED]"
    assert request_log["headers"]["set-cookie"] == "[REDACTED]"
    assert request_log["headers"]["proxy-authorization"] == "[REDACTED]"
    assert request_log["headers"]["x-csrf-token"] == "[REDACTED]"
    assert request_log["headers"]["x-csrftoken"] == "[REDACTED]"
    assert request_log["headers"]["idempotency-key"] == "[REDACTED]"
    assert request_log["headers"]["x-diagnostic"] == "safe-to-log"
