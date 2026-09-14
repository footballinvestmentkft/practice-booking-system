"""PC6A deterministic policy for one human-reviewed juggling attempt.

This policy deliberately contains no CV timing heuristic. Review decisions and
explicit boundaries are inputs; the server validates their consistency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID


_FINAL_REVIEW_STATUSES = frozenset({"confirmed", "corrected", "rejected"})
_RESOLVED_TAXONOMY_STATUSES = frozenset({"not_applicable", "reclassified", "promoted"})
_TERMINATION_REASONS = frozenset({
    "BALL_DROPPED", "VIDEO_ENDED", "PLAYER_STOPPED", "INVALID_SEQUENCE",
})


class SequencePolicyError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReviewedContact:
    event_id: UUID
    timestamp_ms: int
    contact_type: str
    side: str | None
    review_status: str
    taxonomy_review_status: str
    included: bool


@dataclass(frozen=True)
class SequenceResult:
    is_valid: bool
    touch_count: int
    score: int
    sequence_start_ms: int | None
    sequence_end_ms: int | None
    termination_reason: str


def validate_human_sequence(
    *,
    contacts: Iterable[ReviewedContact],
    status: str,
    sequence_start_ms: int | None,
    sequence_end_ms: int | None,
    termination_reason: str,
) -> SequenceResult:
    rows = list(contacts)
    if status not in {"VALID", "INVALID"}:
        raise SequencePolicyError("INVALID_VERIFICATION_STATUS")
    if termination_reason not in _TERMINATION_REASONS:
        raise SequencePolicyError("INVALID_TERMINATION_REASON")

    for row in rows:
        if row.review_status not in _FINAL_REVIEW_STATUSES:
            raise SequencePolicyError("UNRESOLVED_CONTACT_EVENT")
        if row.review_status != "rejected" and row.taxonomy_review_status not in _RESOLVED_TAXONOMY_STATUSES:
            raise SequencePolicyError("UNRESOLVED_CONTACT_EVENT")
        if row.included and row.review_status not in {"confirmed", "corrected"}:
            raise SequencePolicyError("INCLUDED_CONTACT_NOT_CONFIRMED")

    included = sorted((row for row in rows if row.included), key=lambda row: row.timestamp_ms)
    timestamps = [row.timestamp_ms for row in included]
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise SequencePolicyError("CONTACT_TIMESTAMPS_NOT_STRICTLY_INCREASING")

    if status == "VALID" and not included:
        raise SequencePolicyError("VALID_SEQUENCE_REQUIRES_CONTACT")
    if not included:
        if sequence_start_ms is not None or sequence_end_ms is not None:
            raise SequencePolicyError("EMPTY_SEQUENCE_REQUIRES_NULL_BOUNDARIES")
        return SequenceResult(
            is_valid=False,
            touch_count=0,
            score=0,
            sequence_start_ms=None,
            sequence_end_ms=None,
            termination_reason=termination_reason,
        )

    if sequence_start_ms != timestamps[0] or sequence_end_ms != timestamps[-1]:
        raise SequencePolicyError("SEQUENCE_BOUNDARY_MISMATCH")
    return SequenceResult(
        is_valid=status == "VALID",
        touch_count=len(included),
        score=len(included),
        sequence_start_ms=sequence_start_ms,
        sequence_end_ms=sequence_end_ms,
        termination_reason=termination_reason,
    )
