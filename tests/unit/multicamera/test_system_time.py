"""
MC1-BE-1 — System Time Endpoint tests.

ST-01  Response has all 4 required fields
ST-02  server_time_utc is ISO 8601 with millisecond precision and Z suffix
ST-03  server_epoch_ms matches parsed server_time_utc within 10ms
ST-04  Cache-Control header contains no-store
ST-05  X-Server-Time-Ms header matches body server_epoch_ms
ST-06  No auth required (200 without token)
ST-07  precision field == "milliseconds"
ST-08  source field == "backend_app_clock"
ST-09  Route count == 933 (932 + 1 new)
ST-10  /api/v1/system/time present in OpenAPI schema
ST-11  Two sequential calls return non-negative epoch_ms values
"""
import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestSystemTimeResponse:

    def test_st_01_response_has_all_fields(self, client):
        """ST-01: Response contains server_time_utc, server_epoch_ms, precision, source."""
        r = client.get("/api/v1/system/time")
        assert r.status_code == 200
        data = r.json()
        assert "server_time_utc" in data
        assert "server_epoch_ms" in data
        assert "precision" in data
        assert "source" in data

    def test_st_02_iso_format_with_millis(self, client):
        """ST-02: server_time_utc matches YYYY-MM-DDTHH:MM:SS.mmmZ."""
        r = client.get("/api/v1/system/time")
        ts = r.json()["server_time_utc"]
        pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
        assert re.match(pattern, ts), f"Timestamp {ts!r} does not match ISO 8601 millis"

    def test_st_03_epoch_ms_matches_iso(self, client):
        """ST-03: server_epoch_ms and server_time_utc represent the same instant (±10ms)."""
        r = client.get("/api/v1/system/time")
        data = r.json()
        epoch_ms = data["server_epoch_ms"]
        parsed = datetime.strptime(data["server_time_utc"], "%Y-%m-%dT%H:%M:%S.%fZ")
        parsed = parsed.replace(tzinfo=timezone.utc)
        parsed_ms = int(parsed.timestamp() * 1000)
        assert abs(epoch_ms - parsed_ms) <= 10, (
            f"epoch_ms={epoch_ms} vs parsed={parsed_ms}, diff={abs(epoch_ms - parsed_ms)}ms"
        )

    def test_st_04_cache_control_no_store(self, client):
        """ST-04: Cache-Control header contains no-store."""
        r = client.get("/api/v1/system/time")
        cc = r.headers.get("cache-control", "")
        assert "no-store" in cc, f"Cache-Control={cc!r}, expected no-store"

    def test_st_05_header_matches_body(self, client):
        """ST-05: X-Server-Time-Ms header equals body server_epoch_ms."""
        r = client.get("/api/v1/system/time")
        header_ms = r.headers.get("x-server-time-ms")
        assert header_ms is not None, "X-Server-Time-Ms header missing"
        body_ms = r.json()["server_epoch_ms"]
        assert int(header_ms) == body_ms

    def test_st_06_no_auth_required(self, client):
        """ST-06: Endpoint returns 200 without Authorization header."""
        r = client.get("/api/v1/system/time")
        assert r.status_code == 200

    def test_st_07_precision_field(self, client):
        """ST-07: precision == 'milliseconds'."""
        r = client.get("/api/v1/system/time")
        assert r.json()["precision"] == "milliseconds"

    def test_st_08_source_field(self, client):
        """ST-08: source == 'backend_app_clock'."""
        r = client.get("/api/v1/system/time")
        assert r.json()["source"] == "backend_app_clock"

    def test_st_09_route_count(self, client):
        """ST-09: OpenAPI route count == 933 (932 baseline + 1 new)."""
        schema = client.app.openapi()
        paths = len(schema.get("paths", {}))
        assert paths == 933, f"Expected 933 routes, got {paths}"

    def test_st_10_openapi_presence(self, client):
        """ST-10: /api/v1/system/time in OpenAPI schema."""
        schema = client.app.openapi()
        assert "/api/v1/system/time" in schema["paths"]

    def test_st_11_sequential_calls_positive(self, client):
        """ST-11: Two sequential calls return positive epoch_ms values."""
        r1 = client.get("/api/v1/system/time")
        r2 = client.get("/api/v1/system/time")
        ms1 = r1.json()["server_epoch_ms"]
        ms2 = r2.json()["server_epoch_ms"]
        assert ms1 > 0
        assert ms2 > 0
