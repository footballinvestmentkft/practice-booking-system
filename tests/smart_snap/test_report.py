"""SS-REP: Report generation smoke tests (v2)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.smart_snap_poc1.config import TARGET_CATEGORIES


def _make_results(
    frames_with_gt: int = 3,
    no_ball_frames: int = 2,
    n_tuning: int = 2,
    n_holdout: int = 1,
) -> dict:
    per_frame = [
        {
            "frame_id": "test_0001000ms",
            "type": "A", "video_id": "test1234",
            "category": "clear_ball", "category_source": "provisional_auto",
            "split": "holdout", "has_gt": True, "is_no_ball": False,
            "gt_provenance": "provisional_simulation",
            "gt_agreement_px": 8.5, "gt_review_required": False,
            "img_w": 640, "img_h": 480,
            "methods": {
                "M1_synthetic_raw_tap": {"method": "M1_synthetic_raw_tap",
                                         "data_source": "SYNTHETIC", "mean": 55.0, "n": 100},
                "M2_human_raw_tap": {"method": "M2_human_raw_tap", "data_source": "SIMULATED",
                                     "x": 0.52, "y": 0.48, "pixel_error": 48.0},
                "M2_human_loupe_tap": {"method": "M2_human_loupe_tap", "data_source": "SIMULATED",
                                       "x": 0.51, "y": 0.49, "pixel_error": 10.0},
                "M3_stored_ssd": {"method": "M3_stored_ssd", "found": True,
                                  "pixel_error": 14.0, "latency_ms": 0.1,
                                  "confidence": 0.7},
                "M4_contour": {"method": "M4_contour", "found": True,
                               "pixel_error": 22.0, "latency_ms": 0.3,
                               "confidence": 0.6},
            },
        },
        {
            "frame_id": "test_nb_0002000ms",
            "type": "C", "video_id": "test1234",
            "category": "no_ball", "category_source": "db_lost_state",
            "split": "holdout", "has_gt": False, "is_no_ball": True,
            "gt_provenance": "validated_no_ball_db_lost_state",
            "gt_agreement_px": None, "gt_review_required": False,
            "img_w": 640, "img_h": 480,
            "methods": {
                "M3_stored_ssd": {"method": "M3_stored_ssd", "found": False,
                                  "false_positive": False, "latency_ms": 0.1},
                "M4_contour": {"method": "M4_contour", "found": False,
                               "false_positive": False, "latency_ms": 0.2},
            },
        },
    ]
    return {
        "generated_at": "2026-06-20T10:00:00+00:00",
        "poc": "smart_snap_poc1",
        "schema_version": "2.0",
        "annotation_data_warning": "PROVISIONAL SIMULATION",
        "summary": {
            "total_frames": len(per_frame),
            "frames_with_gt": frames_with_gt,
            "no_ball_frames": no_ball_frames,
            "tuning_frames": n_tuning,
            "holdout_frames": n_holdout,
            "unsplit_frames": 0,
            "algorithms": ["M3_stored_ssd", "M4_contour"],
            "category_distribution": {"clear_ball": 1, "no_ball": 1},
        },
        "per_frame": per_frame,
        "aggregated": {
            "M1_synthetic_raw_tap": {
                "all": {
                    "overall": {"mean": 55.0, "median": 55.0, "p90": 60.0, "p95": 65.0, "n": 1},
                    "by_category": {"clear_ball": {"mean": 55.0, "median": 55.0, "p90": 60.0, "p95": 65.0, "n": 1}},
                    "by_video": {}, "latency": {"p50_ms": None, "p95_ms": None, "n": 0},
                    "wrong_snap_rate_vs_m1_synthetic": None,
                    "wrong_snap_rate_vs_m2_raw": None,
                    "wrong_snap_rate_vs_m2_loupe": None,
                    "false_positive_rate": None, "false_refusal_rate": None,
                    "confidence_distribution": None, "n_no_ball_frames_tested": 0,
                },
                "holdout": {
                    "overall": {"mean": 55.0, "median": 55.0, "p90": 60.0, "p95": 65.0, "n": 1},
                    "by_category": {}, "by_video": {},
                    "latency": {"p50_ms": None, "p95_ms": None, "n": 0},
                    "wrong_snap_rate_vs_m1_synthetic": None, "wrong_snap_rate_vs_m2_raw": None,
                    "wrong_snap_rate_vs_m2_loupe": None,
                    "false_positive_rate": None, "false_refusal_rate": None,
                    "confidence_distribution": None, "n_no_ball_frames_tested": 0,
                },
                "tuning": None, "unsplit": None,
            },
            "M3_stored_ssd": {
                "all": {
                    "overall": {"mean": 14.0, "median": 14.0, "p90": 14.0, "p95": 14.0, "n": 1},
                    "by_category": {"clear_ball": {"mean": 14.0, "median": 14.0, "p90": 14.0, "p95": 14.0, "n": 1}},
                    "by_video": {"test1234": {"mean": 14.0, "median": 14.0, "p90": 14.0, "p95": 14.0, "n": 1}},
                    "latency": {"p50_ms": 0.1, "p95_ms": 0.1, "n": 2},
                    "wrong_snap_rate_vs_m1_synthetic": 0.0, "wrong_snap_rate_vs_m2_raw": 0.0,
                    "wrong_snap_rate_vs_m2_loupe": None, "false_positive_rate": 0.0,
                    "false_refusal_rate": 0.0, "confidence_distribution": {"mean": 0.7, "n": 1},
                    "n_no_ball_frames_tested": 1,
                },
                "holdout": {
                    "overall": {"mean": 14.0, "median": 14.0, "p90": 14.0, "p95": 14.0, "n": 1},
                    "by_category": {"clear_ball": {"mean": 14.0, "median": 14.0, "p90": 14.0, "p95": 14.0, "n": 1}},
                    "by_video": {}, "latency": {"p50_ms": 0.1, "p95_ms": 0.1, "n": 2},
                    "wrong_snap_rate_vs_m1_synthetic": 0.0, "wrong_snap_rate_vs_m2_raw": 0.0,
                    "wrong_snap_rate_vs_m2_loupe": None, "false_positive_rate": 0.0,
                    "false_refusal_rate": 0.0, "confidence_distribution": None,
                    "n_no_ball_frames_tested": 1,
                },
                "tuning": None, "unsplit": None,
            },
        },
    }


class TestReportGenerationV2:
    def test_SS_REP_01_report_is_string(self):
        """SS-REP-01: build_report returns a non-empty string."""
        from scripts.smart_snap_poc1.report_builder import build_report
        report = build_report(_make_results(), None, None)
        assert isinstance(report, str) and len(report) > 100

    def test_SS_REP_02_report_contains_verdict(self):
        """SS-REP-02: Report contains one of the three verdict strings."""
        from scripts.smart_snap_poc1.report_builder import build_report
        report = build_report(_make_results(), None, None)
        verdicts = ["PROCEED TO POC-2", "NEED MORE DATA", "REJECT APPROACH",
                    "REJECT CURRENT SNAP METHODS"]
        assert any(v in report for v in verdicts)

    def test_SS_REP_03_report_has_measured_section(self):
        """SS-REP-03: Report contains TÉNYLEGESEN MÉRT section."""
        from scripts.smart_snap_poc1.report_builder import build_report
        report = build_report(_make_results(), None, None)
        assert "TÉNYLEGESEN MÉRT" in report or "Measured" in report

    def test_SS_REP_04_ios_latency_na(self):
        """SS-REP-04: Report states iOS latency as N/A or POC-2."""
        from scripts.smart_snap_poc1.report_builder import build_report
        report = build_report(_make_results(), None, None)
        assert "N/A" in report or "POC-2" in report

    def test_SS_REP_05_no_hardcoded_merge_permission(self):
        """SS-REP-05: Report does not claim merge is approved."""
        from scripts.smart_snap_poc1.report_builder import build_report
        report = build_report(_make_results(), None, None)
        assert "merged to main" not in report.lower()
        assert "merge approved" not in report.lower()

    def test_SS_REP_06_report_contains_acceptance_gate_table(self):
        """SS-REP-06: Report includes Acceptance Gate Evaluation section."""
        from scripts.smart_snap_poc1.report_builder import build_report
        report = build_report(_make_results(), None, None)
        assert "Acceptance Gate" in report or "acceptance gate" in report.lower()

    def test_SS_REP_07_need_more_data_when_insufficient_gt(self):
        """SS-REP-07: Verdict is NEED MORE DATA when frames_with_gt < threshold."""
        from scripts.smart_snap_poc1.report_builder import build_report
        results = _make_results(frames_with_gt=0)
        report = build_report(results, None, None)
        assert "NEED MORE DATA" in report

    def test_SS_REP_08_provisional_simulation_warning(self):
        """SS-REP-08: Report warns about provisional simulation annotation data."""
        from scripts.smart_snap_poc1.report_builder import build_report
        report = build_report(_make_results(), None, None)
        assert "PROVISIONAL" in report or "provisional" in report.lower()

    def test_SS_REP_09_data_sources_separated(self):
        """SS-REP-09: Report separates BECSLÉSEK from TÉNYLEGESEN MÉRT."""
        from scripts.smart_snap_poc1.report_builder import build_report
        report = build_report(_make_results(), None, None)
        assert "BECSLÉSEK" in report or "Estimation" in report or "simulation" in report.lower()

    def test_SS_REP_10_holdout_set_label_in_verdict(self):
        """SS-REP-10: Report verdict section labels the evaluation set."""
        from scripts.smart_snap_poc1.report_builder import build_report
        report = build_report(_make_results(n_holdout=1), None, None)
        assert "HOLDOUT" in report or "holdout" in report.lower()
