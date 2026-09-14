"""Privacy-safe local golden-data validation and deterministic evaluation."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from sqlalchemy.orm import Session

from app.models.juggling import JugglingConsent, JugglingVerification, JugglingVideo


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
    touch_count_exact_accuracy: float
    contact_precision: float
    contact_recall: float
    mean_timestamp_error_ms: float
    left_right_accuracy: float
    body_part_accuracy: float
    sequence_validity_accuracy: float


def build_reviewed_sample(*, verification: Any, video: Any, consent: Any) -> dict[str, Any]:
    """Project an immutable reviewed result into a privacy-safe dataset row."""
    if not (consent and consent.service_consent and consent.training_consent and consent.admin_review_consent):
        raise GoldenDatasetError("GOLDEN_DATASET_CONSENT_REQUIRED")
    metadata = video.server_detected_metadata or {}
    if not verification.provenance_hash or not verification.video_checksum_sha256:
        raise GoldenDatasetError("GOLDEN_PROVENANCE_REQUIRED")
    # Bind the pseudonym to the immutable sample provenance so repository data
    # cannot be used to enumerate low integer user IDs.
    safe_ref = hashlib.sha256(
        f"{verification.provenance_hash}:{verification.player_user_id}".encode("utf-8")
    ).hexdigest()
    contacts = [
        {
            "timestamp_ms": int(row["timestamp_ms"]),
            "contact_type": row["contact_type"],
            "side": row.get("side"),
        }
        for row in verification.contact_event_snapshot
        if not row.get("excluded_from_count") and row.get("deleted_at") is None
    ]
    return {
        "sample_id": str(verification.id),
        "player_safe_reference": safe_ref,
        "video_checksum_sha256": verification.video_checksum_sha256,
        "duration_seconds": metadata.get("duration_seconds"),
        "capture_metadata": _privacy_safe_capture_metadata(
            video.client_reported_metadata or {}
        ),
        "annotation_revision": verification.contact_event_revision_hash,
        "review_status": "VERIFIED",
        "privacy_review_status": "APPROVED",
        "ground_truth": {
            "touch_count": verification.touch_count,
            "sequence_status": verification.status,
            "sequence_start_ms": verification.sequence_start_ms,
            "sequence_end_ms": verification.sequence_end_ms,
            "termination_reason": verification.termination_reason,
            "contact_events": contacts,
        },
    }


def _privacy_safe_capture_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "device_model", "camera_position", "orientation_degrees",
        "os_version", "app_version", "capture_version",
    }
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in allowed or not isinstance(value, (str, int, float, bool)):
            continue
        if isinstance(value, str) and (
            len(value) > 128 or "/" in value or "\\" in value
        ):
            continue
        safe[key] = value
    return safe


def build_reviewed_manifest(db: Session) -> dict[str, Any]:
    """Build the consent-filtered manifest from canonical immutable records."""
    rows = (
        db.query(JugglingVerification, JugglingVideo, JugglingConsent)
        .join(JugglingVideo, JugglingVideo.id == JugglingVerification.video_id)
        .join(JugglingConsent, JugglingConsent.user_id == JugglingVideo.user_id)
        .filter(
            JugglingVerification.verification_method == "HUMAN",
            JugglingConsent.service_consent.is_(True),
            JugglingConsent.training_consent.is_(True),
            JugglingConsent.admin_review_consent.is_(True),
        )
        .order_by(JugglingVerification.verified_at, JugglingVerification.id)
        .all()
    )
    manifest = {
        "schema_version": "PC6B_GOLDEN_V1",
        "samples": [
            build_reviewed_sample(
                verification=verification, video=video, consent=consent,
            )
            for verification, video, consent in rows
        ],
    }
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") not in {"PC6A_GOLDEN_V1", "PC6B_GOLDEN_V1"}:
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
        if manifest["schema_version"] == "PC6B_GOLDEN_V1":
            if not isinstance(sample.get("player_safe_reference"), str) or len(sample["player_safe_reference"]) != 64:
                raise GoldenDatasetError("GOLDEN_PLAYER_REFERENCE_INVALID")
            if not isinstance(sample.get("annotation_revision"), str) or len(sample["annotation_revision"]) != 64:
                raise GoldenDatasetError("GOLDEN_ANNOTATION_REVISION_INVALID")
            if sample.get("review_status") != "VERIFIED":
                raise GoldenDatasetError("GOLDEN_REVIEW_REQUIRED")
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


def evaluate(
    manifest: dict[str, Any], candidates: dict[str, dict[str, Any]], *, timestamp_tolerance_ms: int = 100,
) -> GoldenEvaluation:
    validate_manifest(manifest)
    count_errors: list[int] = []
    exact = sequence = contact_types = sides = comparable = 0
    matched = predicted_total = truth_total = 0
    timestamp_errors: list[int] = []
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
        predicted_contacts = prediction.get("contact_events", [])
        predicted_total += len(predicted_contacts)
        truth_total += len(truth["contact_events"])
        unmatched = set(range(len(predicted_contacts)))
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for expected in truth["contact_events"]:
            eligible = [(abs(int(predicted_contacts[i].get("timestamp_ms", -10**9)) - expected["timestamp_ms"]), i) for i in unmatched]
            if not eligible:
                continue
            error_ms, index = min(eligible)
            if error_ms <= timestamp_tolerance_ms:
                unmatched.remove(index)
                matched += 1
                timestamp_errors.append(error_ms)
                pairs.append((expected, predicted_contacts[index]))
        for expected, actual in pairs:
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
        touch_count_exact_accuracy=(exact / len(count_errors)) if count_errors else 0.0,
        contact_precision=(matched / predicted_total) if predicted_total else 0.0,
        contact_recall=(matched / truth_total) if truth_total else 0.0,
        mean_timestamp_error_ms=(sum(timestamp_errors) / len(timestamp_errors)) if timestamp_errors else 0.0,
        left_right_accuracy=(sides / comparable) if comparable else 0.0,
        body_part_accuracy=(contact_types / comparable) if comparable else 0.0,
        sequence_validity_accuracy=(sequence / len(count_errors)) if count_errors else 0.0,
    )
