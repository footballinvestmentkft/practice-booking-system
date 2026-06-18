"""
KalmanBallTracker unit tests — BT-06..BT-12.

Pure-logic tests: no DB, no Celery, no ONNX.
"""
from __future__ import annotations

import pytest

from app.services.juggling.kalman_ball_tracker import KalmanBallTracker


class TestKalmanBallTracker:

    # BT-06: first detection returns raw coordinates (no smoothing yet)
    def test_bt06_first_detection_returns_raw(self):
        t = KalmanBallTracker()
        x, y = t.update(0.5, 0.7)
        assert x == 0.5
        assert y == 0.7
        assert not t.is_lost
        assert t.miss_count == 0

    # BT-07: consecutive detections converge to actual position
    def test_bt07_consecutive_detections_converge(self):
        t = KalmanBallTracker()
        t.update(0.5, 0.5)
        for _ in range(10):
            x, y = t.update(0.6, 0.6)
        assert abs(x - 0.6) < 0.05
        assert abs(y - 0.6) < 0.05

    # BT-08: 1-3 miss → predict_only returns extrapolated position
    def test_bt08_few_misses_predict_returns_position(self):
        t = KalmanBallTracker(max_miss=5)
        t.update(0.5, 0.5)
        t.update(0.55, 0.55)

        for i in range(3):
            result = t.predict_only()
            assert result is not None, f"predict_only returned None on miss #{i+1}"
            px, py = result
            assert 0.0 <= px <= 1.5
            assert 0.0 <= py <= 1.5

        assert not t.is_lost

    # BT-09: 6+ miss → predict_only returns None (lost)
    def test_bt09_many_misses_returns_none(self):
        t = KalmanBallTracker(max_miss=5)
        t.update(0.5, 0.5)

        for _ in range(5):
            result = t.predict_only()
            assert result is not None

        result = t.predict_only()
        assert result is None
        assert t.is_lost

    # BT-10: detection after lost → re-init tracker
    def test_bt10_detection_after_lost_reinit(self):
        t = KalmanBallTracker(max_miss=2)
        t.update(0.5, 0.5)

        for _ in range(3):
            t.predict_only()
        assert t.is_lost

        x, y = t.update(0.8, 0.8)
        assert x == 0.8
        assert y == 0.8
        assert not t.is_lost
        assert t.miss_count == 0

    # BT-11: seed() re-initializes from manual position
    def test_bt11_seed_reinit(self):
        t = KalmanBallTracker(max_miss=2)
        t.update(0.5, 0.5)
        for _ in range(3):
            t.predict_only()
        assert t.is_lost

        t.seed(0.3, 0.9)
        assert not t.is_lost
        assert t.miss_count == 0
        x, y = t.update(0.31, 0.91)
        assert abs(x - 0.31) < 0.05
        assert abs(y - 0.91) < 0.05

    # BT-12: is_lost reflects miss_count vs threshold
    def test_bt12_is_lost_property(self):
        t = KalmanBallTracker(max_miss=3)
        assert t.is_lost  # not initialized

        t.update(0.5, 0.5)
        assert not t.is_lost

        t.mark_miss()
        assert not t.is_lost
        assert t.miss_count == 1

        t.mark_miss()
        t.mark_miss()
        assert not t.is_lost  # miss_count=3 == max_miss, not > max_miss

        t.mark_miss()
        assert t.is_lost  # miss_count=4 > max_miss=3

    def test_bt06b_uninitialized_predict_returns_none(self):
        t = KalmanBallTracker()
        assert t.predict_only() is None
        assert t.is_lost
