"""Unit tests for the deep-link command builder and API helpers (MC1-AUTO-2).

These tests verify that _open_url_cmd() produces the correct
'xcrun devicectl device process openURL' syntax for devicectl ≥ 629 (Xcode 15+).
They do NOT invoke xcrun — command construction only.

The transition_session tests mock http_request to verify the PATCH body
matches the backend's TransitionRequest schema (target_status, not target).
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, call

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mc1_regression.lib import _open_url_cmd, transition_session


UDID = "6C301A7E-DC8C-5EE6-BF21-11569118A65B"
URL = "lfa-mc1://automate?action=begin-cycle"


def test_uses_process_openurl_subcommand():
    cmd = _open_url_cmd(UDID, URL)
    assert "process" in cmd
    assert "openURL" in cmd
    # Must be consecutive: [..., "process", "openURL", ...]
    idx = cmd.index("process")
    assert cmd[idx + 1] == "openURL"


def test_does_not_use_send_url():
    cmd = _open_url_cmd(UDID, URL)
    assert "send" not in cmd, "old 'device send url' syntax must not be used"


def test_url_positional_before_device_flag():
    cmd = _open_url_cmd(UDID, URL)
    url_idx = cmd.index(URL)
    device_flag_idx = cmd.index("--device")
    assert url_idx < device_flag_idx, "URL must come before --device flag"


def test_udid_immediately_after_device_flag():
    cmd = _open_url_cmd(UDID, URL)
    device_flag_idx = cmd.index("--device")
    assert cmd[device_flag_idx + 1] == UDID


def test_starts_with_xcrun_devicectl():
    cmd = _open_url_cmd(UDID, URL)
    assert cmd[0] == "xcrun"
    assert cmd[1] == "devicectl"


def test_url_preserved_verbatim():
    special_url = "lfa-mc1://automate?action=join&session_uuid=abc-123&role=instructor"
    cmd = _open_url_cmd(UDID, special_url)
    assert special_url in cmd


def test_different_udid():
    udid2 = "339B8F67-79A2-5099-A110-ABAF9E9902F5"
    cmd = _open_url_cmd(udid2, URL)
    device_flag_idx = cmd.index("--device")
    assert cmd[device_flag_idx + 1] == udid2


# ── transition_session request body ──────────────────────────────────────────

_FAKE_SESSION = {"session_uuid": "test-uuid", "status": "lobby", "revision": 5}
_API = "https://example.com"
_TOKEN = "tok"
_UUID = "test-uuid"


@patch("mc1_regression.lib.http_request")
def test_transition_body_uses_target_status(mock_http):
    mock_http.return_value = {"status": "devices_ready", "revision": 6}
    with patch("mc1_regression.lib.get_session", return_value=_FAKE_SESSION):
        transition_session(_API, _TOKEN, _UUID, "devices_ready")
    _, kwargs = mock_http.call_args
    body = kwargs.get("body") or mock_http.call_args[0][3] if len(mock_http.call_args[0]) > 3 else None
    # Also check positional form
    args = mock_http.call_args
    if body is None:
        body = args.kwargs.get("body")
    assert "target_status" in body, f"body must use 'target_status', got keys: {list(body.keys())}"
    assert "target" not in body or "target_status" in body
    assert body["target_status"] == "devices_ready"
    assert body["revision"] == 5


@patch("mc1_regression.lib.http_request")
def test_transition_409_retry_also_uses_target_status(mock_http):
    from mc1_regression.lib import ValidationError
    mock_http.side_effect = [
        ValidationError("HTTP 409 PATCH ..."),
        {"status": "devices_ready", "revision": 8},
    ]
    fresh_session = {**_FAKE_SESSION, "revision": 7}
    with patch("mc1_regression.lib.get_session", side_effect=[_FAKE_SESSION, fresh_session]):
        transition_session(_API, _TOKEN, _UUID, "devices_ready")
    assert mock_http.call_count == 2
    retry_body = mock_http.call_args_list[1].kwargs.get("body") or mock_http.call_args_list[1][1].get("body")
    assert "target_status" in retry_body, f"retry body must use 'target_status', got keys: {list(retry_body.keys())}"
    assert retry_body["target_status"] == "devices_ready"
    assert retry_body["revision"] == 7
