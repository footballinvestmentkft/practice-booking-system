"""
Minimal 2D Kalman filter for ball trajectory smoothing.

State:     [x, y, vx, vy]  (position + velocity)
Measure:   [x, y]           (detector output, normalized [0,1])
No external dependencies beyond numpy (already present via onnxruntime).
"""
from __future__ import annotations

import numpy as np


class KalmanBallTracker:
    """
    Simple constant-velocity Kalman filter for 2D ball tracking.

    After max_miss consecutive frames without detection the tracker
    enters 'lost' state and predict_only() returns None.
    Re-initialized on next detection (or manual seed).
    """

    def __init__(self, max_miss: int = 5, dt: float = 0.1):
        self._max_miss = max_miss
        self._dt = dt
        self._initialized = False
        self._miss_count = 0

        self._F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1,  0],
            [0, 0, 0,  1],
        ], dtype=np.float64)

        self._H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0],
        ], dtype=np.float64)

        self._Q = np.eye(4, dtype=np.float64) * 0.001
        self._Q[2, 2] = 0.01
        self._Q[3, 3] = 0.01

        self._R = np.eye(2, dtype=np.float64) * 0.005

        self._x = np.zeros(4, dtype=np.float64)
        self._P = np.eye(4, dtype=np.float64)

    @property
    def is_lost(self) -> bool:
        return not self._initialized or self._miss_count > self._max_miss

    @property
    def miss_count(self) -> int:
        return self._miss_count

    def update(self, cx: float, cy: float) -> tuple[float, float]:
        """Feed a detection. Returns smoothed (x, y)."""
        z = np.array([cx, cy], dtype=np.float64)

        if not self._initialized or self.is_lost:
            self._x = np.array([cx, cy, 0.0, 0.0], dtype=np.float64)
            self._P = np.eye(4, dtype=np.float64)
            self._initialized = True
            self._miss_count = 0
            return (cx, cy)

        x_pred = self._F @ self._x
        P_pred = self._F @ self._P @ self._F.T + self._Q

        y_res = z - self._H @ x_pred
        S = self._H @ P_pred @ self._H.T + self._R
        K = P_pred @ self._H.T @ np.linalg.inv(S)
        self._x = x_pred + K @ y_res
        self._P = (np.eye(4) - K @ self._H) @ P_pred

        self._miss_count = 0
        return (float(self._x[0]), float(self._x[1]))

    def predict_only(self) -> tuple[float, float] | None:
        """Extrapolate one step without measurement. Returns None if lost."""
        if not self._initialized:
            return None

        self._miss_count += 1
        if self._miss_count > self._max_miss:
            return None

        self._x = self._F @ self._x
        self._P = self._F @ self._P @ self._F.T + self._Q
        return (float(self._x[0]), float(self._x[1]))

    def mark_miss(self) -> None:
        """Increment miss counter without predicting."""
        self._miss_count += 1

    def seed(self, cx: float, cy: float) -> None:
        """Manual re-initialize (after user tap)."""
        self._x = np.array([cx, cy, 0.0, 0.0], dtype=np.float64)
        self._P = np.eye(4, dtype=np.float64)
        self._initialized = True
        self._miss_count = 0
