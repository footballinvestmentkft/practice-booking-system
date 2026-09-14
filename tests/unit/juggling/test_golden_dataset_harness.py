import json
from pathlib import Path

import pytest

from app.services.juggling.golden_dataset import GoldenDatasetError, evaluate, validate_manifest


def _sample():
    return {
        "schema_version": "PC6A_GOLDEN_V1",
        "samples": [{
            "sample_id": "opaque-sample-1",
            "video_checksum_sha256": "a" * 64,
            "privacy_review_status": "APPROVED",
            "ground_truth": {
                "touch_count": 2,
                "sequence_status": "VALID",
                "sequence_start_ms": 100,
                "sequence_end_ms": 300,
                "termination_reason": "VIDEO_ENDED",
                "contact_events": [
                    {"timestamp_ms": 100, "contact_type": "right_instep", "side": "right"},
                    {"timestamp_ms": 300, "contact_type": "left_knee", "side": "left"},
                ],
            },
        }],
    }


def test_empty_checked_in_manifest_is_valid_and_contains_no_media():
    path = Path("datasets/juggling/golden_manifest_v1.json")
    manifest = json.loads(path.read_text())
    validate_manifest(manifest)
    assert manifest["samples"] == []
    media_extensions = {".mp4", ".mov", ".m4v", ".avi", ".hevc"}
    assert not [
        path for path in Path("datasets/juggling").rglob("*")
        if path.is_file() and path.suffix.lower() in media_extensions
    ]


def test_harness_compares_count_sequence_body_part_and_side():
    result = evaluate(_sample(), {
        "opaque-sample-1": {
            "touch_count": 2,
            "sequence_status": "VALID",
            "contact_events": [
                {"timestamp_ms": 110, "contact_type": "right_instep", "side": "right"},
                {"timestamp_ms": 320, "contact_type": "left_knee", "side": "left"},
            ],
        },
    })
    assert result.samples_compared == 1
    assert result.exact_count_matches == 1
    assert result.mean_absolute_count_error == 0
    assert result.sequence_status_matches == 1
    assert result.contact_type_matches == result.side_matches == result.comparable_contacts == 2


def test_unapproved_or_inconsistent_sample_fails_closed():
    manifest = _sample()
    manifest["samples"][0]["privacy_review_status"] = "PENDING"
    with pytest.raises(GoldenDatasetError, match="GOLDEN_PRIVACY_APPROVAL_REQUIRED"):
        validate_manifest(manifest)
