#!/usr/bin/env python3
"""Unit tests for drift_probe_report.py — fixture-based, no device access needed."""

import json
import math
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from drift_probe_report import (
    parse_iso,
    percentile_95,
    load_drift_records,
    validate_record,
    check_duplicate_records,
    compute_drift_ms,
)


def _make_record(
    session_uuid="s1",
    cycle_index=1,
    device_id=57,
    device_type="ipad",
    did_start_recording_at="2026-06-24T07:54:52.917Z",
    server_offset_estimate_ms=0.0,
    server_offset_ms=293.069,
    callback_delay_ms=293.069,
    clock_quality="synchronized",
    success=True,
    failure_reason=None,
):
    return {
        "session_uuid": session_uuid,
        "cycle_index": cycle_index,
        "device_id": device_id,
        "device_type": device_type,
        "scheduled_start_at": "2026-06-24T07:54:52.624Z",
        "local_fire_at": "2026-06-24T07:54:52.624Z",
        "did_start_recording_at": did_start_recording_at,
        "server_offset_estimate_ms": server_offset_estimate_ms,
        "server_offset_ms": server_offset_ms,
        "callback_delay_ms": callback_delay_ms,
        "capture_orientation": "portrait",
        "clock_quality": clock_quality,
        "success": success,
        "failure_reason": failure_reason,
    }


class TestParseISO(unittest.TestCase):
    def test_fractional_seconds(self):
        dt = parse_iso("2026-06-24T07:54:52.917Z")
        self.assertEqual(dt.year, 2026)
        self.assertAlmostEqual(dt.microsecond, 917000, delta=1000)

    def test_no_fractional(self):
        dt = parse_iso("2026-06-24T07:54:52+00:00")
        self.assertEqual(dt.second, 52)

    def test_z_suffix(self):
        dt = parse_iso("2026-06-24T07:54:52.123456Z")
        self.assertAlmostEqual(dt.microsecond, 123456, delta=1)


class TestPercentile95(unittest.TestCase):
    def test_10_values(self):
        # N=10: P95 index = ceil(0.95*10)-1 = 9 → value 100
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        self.assertAlmostEqual(percentile_95(values), 100.0)

    def test_single_value(self):
        # N=1: P95 index = ceil(0.95)-1 = 0
        self.assertAlmostEqual(percentile_95([42.0]), 42.0)

    def test_5_values(self):
        # N=5: P95 index = ceil(4.75)-1 = 4 → last element
        self.assertAlmostEqual(percentile_95([1, 2, 3, 4, 5]), 5.0)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            percentile_95([])

    def test_deterministic_small_sample(self):
        # N=3: P95 index = ceil(2.85)-1 = 2 → value 30
        self.assertAlmostEqual(percentile_95([10, 20, 30]), 30.0)


class TestLoadRecords(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_valid_json(self):
        p = os.path.join(self.tmpdir, "valid.json")
        with open(p, "w") as f:
            json.dump([_make_record()], f)
        recs = load_drift_records(p, "test")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["device_type"], "ipad")

    def test_corrupt_json_exits(self):
        p = os.path.join(self.tmpdir, "bad.json")
        with open(p, "w") as f:
            f.write("not json {{{")
        with self.assertRaises(SystemExit):
            load_drift_records(p, "test")

    def test_missing_file_exits(self):
        p = os.path.join(self.tmpdir, "nonexistent.json")
        with self.assertRaises(SystemExit):
            load_drift_records(p, "test")

    def test_single_record_dict_wrapped(self):
        p = os.path.join(self.tmpdir, "single.json")
        with open(p, "w") as f:
            json.dump(_make_record(), f)
        recs = load_drift_records(p, "test")
        self.assertEqual(len(recs), 1)


class TestValidateRecord(unittest.TestCase):
    def test_valid_record_passes(self):
        rec = _make_record(session_uuid="s1")
        validate_record(rec, "s1", "test")

    def test_session_mismatch_exits(self):
        rec = _make_record(session_uuid="wrong")
        with self.assertRaises(SystemExit):
            validate_record(rec, "s1", "test")

    def test_missing_did_start_exits(self):
        rec = _make_record()
        rec["did_start_recording_at"] = None
        with self.assertRaises(SystemExit):
            validate_record(rec, "s1", "test")

    def test_success_false_exits(self):
        rec = _make_record(success=False, failure_reason="Timeout")
        with self.assertRaises(SystemExit):
            validate_record(rec, "s1", "test")


class TestCheckDuplicateRecords(unittest.TestCase):
    def test_no_duplicates_passes(self):
        recs = [_make_record(cycle_index=1), _make_record(cycle_index=2)]
        check_duplicate_records(recs, "test")

    def test_duplicate_cycle_exits(self):
        recs = [_make_record(cycle_index=1), _make_record(cycle_index=1)]
        with self.assertRaises(SystemExit):
            check_duplicate_records(recs, "test")


class TestComputeDriftMs(unittest.TestCase):
    def test_known_22ms(self):
        ipad = _make_record(did_start_recording_at="2026-06-24T07:54:52.917Z")
        iphone = _make_record(did_start_recording_at="2026-06-24T07:54:52.939Z")
        drift = compute_drift_ms(ipad, iphone)
        self.assertAlmostEqual(drift, 22.0, places=0)

    def test_identical_timestamps(self):
        rec = _make_record(did_start_recording_at="2026-06-24T07:54:52.917Z")
        drift = compute_drift_ms(rec, rec)
        self.assertAlmostEqual(drift, 0.0, places=1)

    def test_large_drift(self):
        a = _make_record(did_start_recording_at="2026-06-24T07:54:52.000Z")
        b = _make_record(did_start_recording_at="2026-06-24T07:54:53.500Z")
        drift = compute_drift_ms(a, b)
        self.assertAlmostEqual(drift, 1500.0, places=0)

    def test_reversed_order_same_result(self):
        a = _make_record(did_start_recording_at="2026-06-24T07:54:52.917Z")
        b = _make_record(did_start_recording_at="2026-06-24T07:54:52.939Z")
        self.assertAlmostEqual(compute_drift_ms(a, b), compute_drift_ms(b, a), places=3)


if __name__ == "__main__":
    unittest.main()
