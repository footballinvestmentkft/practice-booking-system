"""SS-HOLD: Tuning/holdout split tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.smart_snap_poc1.config import HOLDOUT_RATIO, SPLIT_SEED


def _make_frames(n: int, category: str = "clear_ball", video_id: str = "vid1") -> list[dict]:
    return [
        {
            "frame_id": f"{video_id}_{i:04d}",
            "video_id": video_id,
            "type": "A",
            "is_no_ball": False,
            "category": category,
            "split": None,
        }
        for i in range(n)
    ]


def _apply_split(frames: list[dict], holdout_ratio: float = HOLDOUT_RATIO, seed: int = SPLIT_SEED):
    """Mirror of 05_split_dataset logic for testing."""
    import random
    rng = random.Random(seed)
    by_cat: dict[str, list[dict]] = {}
    for f in frames:
        cat = f.get("category") or "unassigned"
        by_cat.setdefault(cat, []).append(f)
    for cat_frames in by_cat.values():
        shuffled = list(cat_frames)
        rng.shuffle(shuffled)
        n_hold = max(1, round(len(shuffled) * holdout_ratio))
        for i, f in enumerate(shuffled):
            f["split"] = "holdout" if i < n_hold else "tuning"
    return frames


class TestSplitRatio:
    def test_SS_HOLD_01_holdout_ratio_approx(self):
        """SS-HOLD-01: Holdout fraction is approximately HOLDOUT_RATIO (±10%)."""
        frames = _make_frames(50)
        _apply_split(frames)
        n_hold = sum(1 for f in frames if f["split"] == "holdout")
        ratio = n_hold / len(frames)
        assert abs(ratio - HOLDOUT_RATIO) < 0.10

    def test_SS_HOLD_02_every_frame_assigned(self):
        """SS-HOLD-02: Every frame has a non-None split after assignment."""
        frames = _make_frames(20)
        _apply_split(frames)
        assert all(f["split"] is not None for f in frames)

    def test_SS_HOLD_03_only_tuning_holdout_values(self):
        """SS-HOLD-03: Split values are only 'tuning' or 'holdout'."""
        frames = _make_frames(30)
        _apply_split(frames)
        splits = {f["split"] for f in frames}
        assert splits <= {"tuning", "holdout"}

    def test_SS_HOLD_04_no_overlap_between_sets(self):
        """SS-HOLD-04: No frame_id appears in both tuning and holdout."""
        frames = _make_frames(40)
        _apply_split(frames)
        tuning = {f["frame_id"] for f in frames if f["split"] == "tuning"}
        holdout = {f["frame_id"] for f in frames if f["split"] == "holdout"}
        assert not (tuning & holdout)

    def test_SS_HOLD_05_deterministic_same_seed(self):
        """SS-HOLD-05: Same seed produces same split."""
        frames_a = _make_frames(30)
        frames_b = _make_frames(30)
        _apply_split(frames_a, seed=42)
        _apply_split(frames_b, seed=42)
        for fa, fb in zip(frames_a, frames_b):
            assert fa["split"] == fb["split"]

    def test_SS_HOLD_06_different_seed_different_split(self):
        """SS-HOLD-06: Different seeds produce different splits (probabilistically)."""
        frames_a = _make_frames(30)
        frames_b = _make_frames(30)
        _apply_split(frames_a, seed=42)
        _apply_split(frames_b, seed=99)
        splits_a = [f["split"] for f in frames_a]
        splits_b = [f["split"] for f in frames_b]
        assert splits_a != splits_b


class TestStratification:
    def test_SS_HOLD_07_each_category_has_holdout(self):
        """SS-HOLD-07: Each category has at least 1 holdout frame."""
        frames = (
            _make_frames(10, "clear_ball", "v1")
            + _make_frames(10, "edge_of_frame", "v2")
            + _make_frames(10, "no_ball", "v3")
        )
        _apply_split(frames)
        by_cat: dict[str, set] = {}
        for f in frames:
            by_cat.setdefault(f["category"], set()).add(f["split"])
        for cat, splits in by_cat.items():
            assert "holdout" in splits, f"Category '{cat}' has no holdout frames"

    def test_SS_HOLD_08_each_category_has_tuning(self):
        """SS-HOLD-08: Each category has at least 1 tuning frame."""
        frames = (
            _make_frames(5, "clear_ball", "v1")
            + _make_frames(5, "edge_of_frame", "v2")
        )
        _apply_split(frames)
        by_cat: dict[str, set] = {}
        for f in frames:
            by_cat.setdefault(f["category"], set()).add(f["split"])
        for cat, splits in by_cat.items():
            assert "tuning" in splits, f"Category '{cat}' has no tuning frames"

    def test_SS_HOLD_09_min_one_holdout_per_category(self):
        """SS-HOLD-09: Even a 2-frame category has at least 1 holdout."""
        frames = _make_frames(2, "rare_category", "v1")
        _apply_split(frames)
        n_holdout = sum(1 for f in frames if f["split"] == "holdout")
        assert n_holdout >= 1

    def test_SS_HOLD_10_holdout_ratio_constant(self):
        """SS-HOLD-10: HOLDOUT_RATIO is 0.30 (30%)."""
        assert HOLDOUT_RATIO == pytest.approx(0.30)
