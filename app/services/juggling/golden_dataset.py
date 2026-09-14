"""Privacy-safe local golden-data validation and deterministic evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class GoldenDatasetError(ValueError):
    pass


@dataclass(frozen=True)
class GoldenEvaluation:
    samples_compared: int
    exact_count_matches: int
    mean_absolute_count_error: float
    sequence_status_matches: int
    contact_type_matches: int
    side_matches: int
    comparable_contacts: int


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != "PC6A_GOLDEN_V1":
        raise GoldenDatasetError("UNSUPPORTED_GOLDEN_SCHEMA")
    samples = manifest.get("samples")
    if not isinstance(samples, list):
        raise GoldenDatasetError("GOLDEN_SAMPLES_REQUIRED")
    seen: set[str] = set()
    for sample in samples:
        sample_id = sample.get("sample_id")
        checksum = sample.get("video_checksum_sha256")
        truth = sample.get("ground_truth") or {}
        contacts = truth.get("contact_events")
        if not sample_id or sample_id in seen:
            raise GoldenDatasetError("GOLDEN_SAMPLE_ID_INVALID")
        seen.add(sample_id)
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise GoldenDatasetError("GOLDEN_VIDEO_CHECKSUM_INVALID")
        if sample.get("privacy_review_status") != "APPROVED":
            raise GoldenDatasetError("GOLDEN_PRIVACY_APPROVAL_REQUIRED")
        if not isinstance(contacts, list):
            raise GoldenDatasetError("GOLDEN_CONTACT_EVENTS_REQUIRED")
        timestamps = [row.get("timestamp_ms") for row in contacts]
        if any(not isinstance(value, int) or value < 0 for value in timestamps):
            raise GoldenDatasetError("GOLDEN_CONTACT_TIMESTAMP_INVALID")
        if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
            raise GoldenDatasetError("GOLDEN_CONTACT_ORDER_INVALID")
        if truth.get("touch_count") != len(contacts):
            raise GoldenDatasetError("GOLDEN_TOUCH_COUNT_MISMATCH")
        if truth.get("sequence_status") not in {"VALID", "INVALID"}:
            raise GoldenDatasetError("GOLDEN_SEQUENCE_STATUS_INVALID")
        for contact in contacts:
            if not contact.get("contact_type") or contact.get("side") not in {
                "left", "right", "center", "unknown", None,
            }:
                raise GoldenDatasetError("GOLDEN_CONTACT_TAXONOMY_INVALID")


def evaluate(manifest: dict[str, Any], candidates: dict[str, dict[str, Any]]) -> GoldenEvaluation:
    validate_manifest(manifest)
    count_errors: list[int] = []
    exact = sequence = contact_types = sides = comparable = 0
    for sample in manifest["samples"]:
        prediction = candidates.get(sample["sample_id"])
        if prediction is None:
            continue
        truth = sample["ground_truth"]
        truth_count = int(truth["touch_count"])
        predicted_count = int(prediction.get("touch_count", 0))
        error = abs(truth_count - predicted_count)
        count_errors.append(error)
        exact += int(error == 0)
        sequence += int(prediction.get("sequence_status") == truth["sequence_status"])
        for expected, actual in zip(truth["contact_events"], prediction.get("contact_events", [])):
            comparable += 1
            contact_types += int(actual.get("contact_type") == expected["contact_type"])
            sides += int(actual.get("side") == expected.get("side"))
    return GoldenEvaluation(
        samples_compared=len(count_errors),
        exact_count_matches=exact,
        mean_absolute_count_error=(sum(count_errors) / len(count_errors)) if count_errors else 0.0,
        sequence_status_matches=sequence,
        contact_type_matches=contact_types,
        side_matches=sides,
        comparable_contacts=comparable,
    )
