from uuid import uuid4

import pytest

from app.services.juggling.sequence_policy import (
    ReviewedContact,
    SequencePolicyError,
    validate_human_sequence,
)


def _contact(timestamp_ms: int, *, included: bool = True) -> ReviewedContact:
    return ReviewedContact(
        event_id=uuid4(),
        timestamp_ms=timestamp_ms,
        contact_type="right_instep",
        side="right",
        review_status="confirmed",
        taxonomy_review_status="not_applicable",
        included=included,
    )


def test_valid_human_reviewed_sequence_uses_explicit_boundaries():
    contacts = [_contact(100), _contact(410), _contact(760)]
    result = validate_human_sequence(
        contacts=contacts,
        status="VALID",
        sequence_start_ms=100,
        sequence_end_ms=760,
        termination_reason="BALL_DROPPED",
    )
    assert result.touch_count == 3
    assert result.score == 3
    assert result.is_valid is True


def test_zero_touch_attempt_is_auditable_but_not_valid():
    result = validate_human_sequence(
        contacts=[],
        status="INVALID",
        sequence_start_ms=None,
        sequence_end_ms=None,
        termination_reason="INVALID_SEQUENCE",
    )
    assert result.touch_count == 0
    assert result.score == 0
    assert result.is_valid is False


def test_zero_touch_cannot_be_a_valid_sequence():
    with pytest.raises(SequencePolicyError, match="VALID_SEQUENCE_REQUIRES_CONTACT"):
        validate_human_sequence(
            contacts=[], status="VALID", sequence_start_ms=None,
            sequence_end_ms=None, termination_reason="VIDEO_ENDED",
        )


def test_pending_or_ambiguous_contact_fails_closed():
    pending = _contact(100)
    pending = ReviewedContact(**{**pending.__dict__, "review_status": "pending"})
    with pytest.raises(SequencePolicyError, match="UNRESOLVED_CONTACT_EVENT"):
        validate_human_sequence(
            contacts=[pending], status="VALID", sequence_start_ms=100,
            sequence_end_ms=100, termination_reason="VIDEO_ENDED",
        )


def test_exact_duplicate_timestamp_is_rejected_without_a_time_heuristic():
    contacts = [_contact(100), _contact(100)]
    with pytest.raises(SequencePolicyError, match="CONTACT_TIMESTAMPS_NOT_STRICTLY_INCREASING"):
        validate_human_sequence(
            contacts=contacts, status="VALID", sequence_start_ms=100,
            sequence_end_ms=100, termination_reason="VIDEO_ENDED",
        )


def test_boundaries_must_match_first_and_last_included_contact():
    contacts = [_contact(100), _contact(200)]
    with pytest.raises(SequencePolicyError, match="SEQUENCE_BOUNDARY_MISMATCH"):
        validate_human_sequence(
            contacts=contacts, status="VALID", sequence_start_ms=90,
            sequence_end_ms=200, termination_reason="PLAYER_STOPPED",
        )
