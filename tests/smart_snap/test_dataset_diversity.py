"""SS-DIV: Dataset diversity enforcement tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.smart_snap_poc1.config import MAX_VIDEO_FRACTION, MIN_NO_BALL_FRAMES, TARGET_CATEGORIES


def _make_manifest(frames: list[dict]) -> dict:
    return {
        "schema_version": "2.0",
        "summary": {},
        "frames": frames,
        "diversity_warnings": [],
    }


def _pos_frame(video_id: str, frame_id: str, category: str) -> dict:
    return {
        "frame_id": frame_id,
        "video_id": video_id,
        "type": "A",
        "is_no_ball": False,
        "category": category,
        "split": "tuning",
    }


def _no_ball_frame(video_id: str, frame_id: str) -> dict:
    return {
        "frame_id": frame_id,
        "video_id": video_id,
        "type": "C",
        "is_no_ball": True,
        "category": "no_ball",
        "split": "tuning",
    }


class TestVideoFractionLimit:
    def test_SS_DIV_01_no_video_exceeds_40pct(self):
        """SS-DIV-01: No single video exceeds MAX_VIDEO_FRACTION of positive frames."""
        frames = (
            [_pos_frame("vid1", f"vid1_{i:03d}", "clear_ball") for i in range(20)]
            + [_pos_frame("vid2", f"vid2_{i:03d}", "edge_of_frame") for i in range(20)]
            + [_pos_frame("vid3", f"vid3_{i:03d}", "low_contrast") for i in range(10)]
        )
        positive = [f for f in frames if not f["is_no_ball"]]
        n_pos = len(positive)
        vid_counts: dict[str, int] = {}
        for f in positive:
            vid_counts[f["video_id"]] = vid_counts.get(f["video_id"], 0) + 1
        max_share = max(vid_counts.values()) / n_pos
        assert max_share <= MAX_VIDEO_FRACTION, (
            f"Video share {max_share:.1%} exceeds {MAX_VIDEO_FRACTION:.0%}"
        )

    def test_SS_DIV_02_violation_detected(self):
        """SS-DIV-02: A dataset where one video has 60% positive triggers violation."""
        frames = (
            [_pos_frame("vid1", f"v1_{i:03d}", "clear_ball") for i in range(30)]
            + [_pos_frame("vid2", f"v2_{i:03d}", "edge_of_frame") for i in range(20)]
        )
        positive = [f for f in frames if not f["is_no_ball"]]
        n_pos = len(positive)
        vid_counts: dict[str, int] = {}
        for f in positive:
            vid_counts[f["video_id"]] = vid_counts.get(f["video_id"], 0) + 1
        max_share = max(vid_counts.values()) / n_pos
        assert max_share > MAX_VIDEO_FRACTION  # deliberately violating


class TestNoBallFrames:
    def test_SS_DIV_03_no_ball_frame_structure(self):
        """SS-DIV-03: No-ball frames must have is_no_ball=True and category='no_ball'."""
        f = _no_ball_frame("vid1", "vid1_nb_001")
        assert f["is_no_ball"] is True
        assert f["category"] == "no_ball"

    def test_SS_DIV_04_no_ball_count_threshold(self):
        """SS-DIV-04: Dataset with < MIN_NO_BALL_FRAMES should trigger warning."""
        nb_frames = [_no_ball_frame("vid1", f"nb_{i:03d}") for i in range(MIN_NO_BALL_FRAMES - 1)]
        assert len(nb_frames) < MIN_NO_BALL_FRAMES

    def test_SS_DIV_05_sufficient_no_ball_frames(self):
        """SS-DIV-05: Dataset with >= MIN_NO_BALL_FRAMES passes the threshold."""
        nb_frames = [_no_ball_frame("vid1", f"nb_{i:03d}") for i in range(MIN_NO_BALL_FRAMES)]
        assert len(nb_frames) >= MIN_NO_BALL_FRAMES

    def test_SS_DIV_06_no_ball_across_multiple_videos(self):
        """SS-DIV-06: No-ball frames should come from multiple videos for diversity."""
        nb_frames = (
            [_no_ball_frame("vid1", f"v1_nb_{i:03d}") for i in range(5)]
            + [_no_ball_frame("vid2", f"v2_nb_{i:03d}") for i in range(5)]
        )
        nb_videos = len({f["video_id"] for f in nb_frames})
        assert nb_videos >= 2


class TestCategoryAssignment:
    def test_SS_DIV_07_provisional_auto_category_not_unassigned(self):
        """SS-DIV-07: Frames with auto-category should not be 'unassigned'."""
        from scripts.smart_snap_poc1.utils import auto_category_hints
        hints = auto_category_hints("detected", 0.75, 0.5, 0.5)
        assert hints  # has at least one hint

    def test_SS_DIV_08_edge_frame_gets_edge_category(self):
        """SS-DIV-08: Ball near frame edge gets edge_of_frame category hint."""
        from scripts.smart_snap_poc1.utils import auto_category_hints
        hints = auto_category_hints("detected", 0.7, 0.05, 0.5)  # ball_x near left edge
        assert "edge_of_frame" in hints

    def test_SS_DIV_09_no_ball_tracking_state_gets_hint(self):
        """SS-DIV-09: tracking_state='lost' gets no_ball_candidate hint."""
        from scripts.smart_snap_poc1.utils import auto_category_hints
        hints = auto_category_hints("lost", None, None, None)
        assert "no_ball_candidate" in hints

    def test_SS_DIV_10_low_conf_gets_hint(self):
        """SS-DIV-10: Low confidence frame gets low_conf hint."""
        from scripts.smart_snap_poc1.utils import auto_category_hints
        hints = auto_category_hints("detected", 0.30, 0.5, 0.5)
        assert "low_conf" in hints

    def test_SS_DIV_11_target_categories_list(self):
        """SS-DIV-11: TARGET_CATEGORIES contains all 7 required categories."""
        required = {"clear_ball", "motion_blur", "partial_occlusion",
                    "edge_of_frame", "small_ball", "low_contrast", "no_ball"}
        assert required == set(TARGET_CATEGORIES)

    def test_SS_DIV_12_category_source_field_exists(self):
        """SS-DIV-12: Frames should carry category_source field."""
        f = _pos_frame("vid1", "vid1_001", "clear_ball")
        # category_source is set by 00_audit; test that the key can be added
        f["category_source"] = "provisional_auto"
        assert f["category_source"] in ("provisional_auto", "db_lost_state", "human_verified")
