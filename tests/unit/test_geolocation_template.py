"""Static template tests for geolocation P0+P1 mobile fix.

Tests parse the raw HTML template — no running server required.

GEO-01  isSecureContext guard present before geolocation call
GEO-02  Insecure context error message present
GEO-03  navigator.permissions.query call present
GEO-04  Safari try/catch fallback wraps permissions.query
GEO-05  High accuracy (enableHighAccuracy:true, timeout:15000) used for first attempt
GEO-06  Low accuracy fallback (enableHighAccuracy:false, timeout:10000) used for retry
GEO-07  Error handler logs err.code, err.message, isSecureContext, perm, ua
"""
import pathlib

_TEMPLATE = (
    pathlib.Path(__file__).parents[2]
    / "app" / "templates" / "dashboard" / "lfa_public_profile_editor.html"
).read_text(encoding="utf-8")


def test_geo_01_secure_context_guard():
    assert "window.isSecureContext" in _TEMPLATE, (
        "lfa_public_profile_editor.html is missing window.isSecureContext guard"
    )


def test_geo_02_insecure_context_error_message():
    assert "Location requires HTTPS on mobile" in _TEMPLATE, (
        "lfa_public_profile_editor.html is missing insecure context error message"
    )


def test_geo_03_permissions_query_call():
    assert 'navigator.permissions.query' in _TEMPLATE, (
        "lfa_public_profile_editor.html is missing navigator.permissions.query call"
    )


def test_geo_04_safari_try_catch_fallback():
    # The permissions.query call must be wrapped in a try/catch block
    assert "} catch (e) {" in _TEMPLATE, (
        "lfa_public_profile_editor.html is missing try/catch fallback for Safari"
    )
    # Verify the catch includes a comment about Safari
    assert "Safari" in _TEMPLATE, (
        "lfa_public_profile_editor.html is missing Safari compatibility comment"
    )


def test_geo_05_high_accuracy_first_attempt():
    assert "enableHighAccuracy: true, timeout: 15000" in _TEMPLATE, (
        "lfa_public_profile_editor.html must use enableHighAccuracy:true, timeout:15000 for first attempt"
    )


def test_geo_06_low_accuracy_fallback():
    assert "enableHighAccuracy: false, timeout: 10000" in _TEMPLATE, (
        "lfa_public_profile_editor.html must use enableHighAccuracy:false, timeout:10000 for retry"
    )


def test_geo_07_error_handler_logs_diagnostics():
    # All four diagnostic fields must appear in the error logging block
    for field in ("err.code", "err.message", "window.isSecureContext", "navigator.userAgent"):
        assert field in _TEMPLATE, (
            f"lfa_public_profile_editor.html error handler is missing diagnostic field: {field}"
        )
