"""
Biometric metrics — PR-8.

Thread-safe counter registry for biometric events.
Designed to be prometheus_client-compatible: labels are low-cardinality enum
values only. No PII, no user_id, no actor_user_id, no IP, no face_match_score
as labels.

Usage:
    from app.services.biometric.metrics import biometric_metrics
    biometric_metrics.increment("biometric_verify_attempt_total", outcome="verified")

Prometheus integration:
    If prometheus_client is available (production), metrics can be exported
    via /metrics endpoint. Otherwise, counters remain in-memory (dev/test).

Privacy rules (enforced by whitelist):
  - outcome:        verified / manual_review_required / rejected / error
  - decision:       approved / rejected
  - status:         accepted / duplicate / failed / completed
  - endpoint_group: whitelisted enum (see ENDPOINT_GROUP_LABELS)
  - error_type:     whitelisted enum (see ERROR_TYPE_LABELS)
  NO user_id, actor_user_id, IP, face_match_score, email, name labels.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

# ── Whitelisted label value enums ─────────────────────────────────────────────

OUTCOME_LABELS = frozenset({"verified", "manual_review_required", "rejected", "error"})
DECISION_LABELS = frozenset({"approved", "rejected"})
STATUS_LABELS = frozenset({"accepted", "duplicate", "failed", "completed"})
ENDPOINT_GROUP_LABELS = frozenset({
    "disclosure_post", "disclosure_delete", "disclosure_get",
    "liveness_submit", "verify",
    "admin_queue", "admin_history", "admin_override",
})
ERROR_TYPE_LABELS = frozenset({
    "rate_limited", "consent_missing", "disclosure_missing", "disclosure_stale",
    "embedding_missing", "decrypt_failed", "provider_error", "unknown",
})

_LABEL_WHITELISTS: dict[str, frozenset[str]] = {
    "outcome":        OUTCOME_LABELS,
    "decision":       DECISION_LABELS,
    "status":         STATUS_LABELS,
    "endpoint_group": ENDPOINT_GROUP_LABELS,
    "error_type":     ERROR_TYPE_LABELS,
}


# ── Counter registry ──────────────────────────────────────────────────────────

class _BiometricMetrics:
    """
    Thread-safe counter registry.

    Keys are (metric_name, frozenset_of_label_tuples).
    Labels are validated against whitelists — unknown values become "unknown"
    to prevent high-cardinality / PII injection.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple, int] = defaultdict(int)

    def _safe_label(self, k: str, v: str) -> str:
        whitelist = _LABEL_WHITELISTS.get(k)
        if whitelist is None:
            return "unknown"   # unknown label key → drop
        return v if v in whitelist else "unknown"

    def _key(self, metric: str, labels: dict[str, str]) -> tuple:
        safe = {k: self._safe_label(k, str(v)) for k, v in labels.items()}
        return (metric, frozenset(safe.items()))

    def increment(self, metric: str, **labels: Any) -> None:
        """Increment counter by 1. Labels are sanitised against whitelists."""
        key = self._key(metric, {k: str(v) for k, v in labels.items()})
        with self._lock:
            self._counters[key] += 1

    def get(self, metric: str, **labels: Any) -> int:
        """Return current counter value (test utility)."""
        key = self._key(metric, {k: str(v) for k, v in labels.items()})
        with self._lock:
            return self._counters[key]

    def reset(self) -> None:
        """Reset all counters (test utility only)."""
        with self._lock:
            self._counters.clear()


# ── Singleton ─────────────────────────────────────────────────────────────────

biometric_metrics = _BiometricMetrics()

# ── Metric name constants ─────────────────────────────────────────────────────

M_VERIFY_ATTEMPT         = "biometric_verify_attempt_total"
M_VERIFY_SUCCESS         = "biometric_verify_success_total"
M_VERIFY_MANUAL_REVIEW   = "biometric_verify_manual_review_total"
M_VERIFY_REJECTED        = "biometric_verify_rejected_total"
M_LIVENESS_SUBMIT        = "biometric_liveness_submit_total"
M_DISCLOSURE_ACCEPT      = "biometric_disclosure_accept_total"
M_DISCLOSURE_REVOKE      = "biometric_disclosure_revoke_total"
M_ADMIN_OVERRIDE         = "biometric_admin_override_total"
M_ADMIN_OVERRIDE_SELF    = "biometric_admin_override_selfblock_total"
M_RATE_LIMITED           = "biometric_rate_limited_total"
M_CONSENT_REVOKED        = "biometric_consent_revoked_total"
M_EMBEDDING_DELETE       = "biometric_embedding_delete_total"
M_ERROR                  = "biometric_error_total"
M_FLAG_ENABLED           = "biometric_feature_flag_enabled_total"