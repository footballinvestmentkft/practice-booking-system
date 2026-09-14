"""
Ball Annotation Reward Service — AN-3B2E PR-3A.

Upfront reward: awarded immediately on feedback submission.
Posterior reward: awarded after consensus task sets approval_state = "approved".

Race condition protection:
  pg_advisory_xact_lock(user_id) serialises all cap-check + award sequences for
  a given user within a single DB transaction. The lock is released on db.commit().
  This mirrors the pattern used in ball_training_service._get_or_create_assignment().

Idempotency:
  Upfront XP:     idempotency_key = f"ball_annotation_xp_{assignment_id}"
  Posterior XP:   idempotency_key = f"ball_annotation_accuracy_{feedback_id}"
  Posterior CR:   idempotency_key = f"ball_annotation_credit_{feedback_id}"

  XPTransaction has a partial unique index on idempotency_key (non-NULL rows).
  CreditTransaction has a full unique index on idempotency_key.
  Both use savepoint-guarded INSERTs so that concurrent duplicate calls are safe.

Daily cap:
  All BALL_ANNOTATION_* XP types count toward the shared daily XP cap.
  Partial reward: if 95/100 XP used, a 10 XP reward gives 5 XP (not 0, not 10).
  Credit cap is independent of the XP cap.
"""
from __future__ import annotations

import hashlib
import uuid as _uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.credit_transaction import CreditTransaction, TransactionType
from app.models.juggling import JugglingBallFeedback, UserAnnotationReliability
from app.models.user import User
from app.models.xp_transaction import XPTransaction
from app.services.credit_service import CreditService
from app.services.xp_write_service import XPWriteService

_ANNOTATION_XP_TYPES = frozenset({
    "BALL_ANNOTATION_XP",
    "BALL_ANNOTATION_XP_CORRECTED",
    "BALL_ANNOTATION_ACCURACY_BONUS",
})


def _advisory_lock_user_reward(db: Session, user_id: int) -> None:
    raw = f"ball_annotation_reward_{user_id}".encode()
    lock_key = int(hashlib.sha256(raw).hexdigest()[:8], 16) % (2**31 - 1)
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})


def get_daily_annotation_stats(db: Session, user_id: int) -> tuple[int, int, int]:
    """Return (daily_task_count, daily_xp_earned, daily_credits_earned) for today UTC."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    daily_xp: int = db.execute(
        select(func.coalesce(func.sum(XPTransaction.amount), 0)).where(
            XPTransaction.user_id == user_id,
            XPTransaction.transaction_type.in_(_ANNOTATION_XP_TYPES),
            XPTransaction.created_at >= today_start,
        )
    ).scalar() or 0

    daily_credits: int = db.execute(
        select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
            CreditTransaction.user_id == user_id,
            CreditTransaction.transaction_type == TransactionType.BALL_ANNOTATION_REWARD.value,
            CreditTransaction.created_at >= today_start,
        )
    ).scalar() or 0

    daily_count: int = db.execute(
        select(func.count(JugglingBallFeedback.id)).where(
            JugglingBallFeedback.user_id == user_id,
            JugglingBallFeedback.approval_state != "spam",
            JugglingBallFeedback.created_at >= today_start,
        )
    ).scalar() or 0

    return int(daily_count), int(daily_xp), int(daily_credits)


def _is_known_spammer(db: Session, user_id: int) -> bool:
    rel = db.get(UserAnnotationReliability, user_id)
    if rel is None:
        return False
    return rel.spam_flags_count >= settings.BALL_ANNOTATION_SPAM_FLAG_BLOCK_THRESHOLD


def _check_rapid_submit(db: Session, user_id: int, now: datetime) -> bool:
    window_start = now - timedelta(seconds=settings.BALL_ANNOTATION_RAPID_SUBMIT_WINDOW_S)
    count: int = db.execute(
        select(func.count(JugglingBallFeedback.id)).where(
            JugglingBallFeedback.user_id == user_id,
            JugglingBallFeedback.created_at >= window_start,
        )
    ).scalar() or 0
    return count > settings.BALL_ANNOTATION_RAPID_SUBMIT_THRESHOLD


def _award_xp_inline(
    db: Session,
    user_id: int,
    xp_amount: int,
    transaction_type: str,
    idempotency_key: str,
    description: str,
) -> int:
    """Delegate annotation XP to the canonical XP ledger/balance authority."""
    _, _, created = XPWriteService(db).award(
        user_id=user_id,
        amount=xp_amount,
        transaction_type=transaction_type,
        description=description,
        idempotency_key=idempotency_key,
    )
    return xp_amount if created else 0


def _award_credit_inline(
    db: Session,
    user_id: int,
    idempotency_key: str,
    description: str,
) -> int:
    """Delegate annotation credit to the canonical global credit authority."""
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("USER_NOT_FOUND")
    _, created = CreditService(db).credit_with_status(
        user=user,
        amount=1,
        transaction_type=TransactionType.BALL_ANNOTATION_REWARD.value,
        description=description,
        idempotency_key=idempotency_key,
    )
    return 1 if created else 0


def compute_upfront_reward(
    decision: str,
    daily_count: int,
    daily_xp: int,
) -> int:
    """Pure function. Returns xp_to_award (≥0). Credit is always 0 upfront."""
    if daily_count >= settings.BALL_ANNOTATION_MAX_TASKS_PER_DAY:
        return 0

    if decision == "corrected":
        base_xp = settings.BALL_ANNOTATION_XP_CORRECTED
    elif decision in ("confirm", "no_ball"):
        base_xp = settings.BALL_ANNOTATION_XP_BASE
    else:
        return 0

    remaining = max(0, settings.BALL_ANNOTATION_MAX_XP_PER_DAY - daily_xp)
    return min(base_xp, remaining)


def award_annotation_upfront(
    db: Session,
    user_id: int,
    assignment_id: _uuid_mod.UUID,
    decision: str,
    reliability: float,
    feedback_id: Optional[_uuid_mod.UUID] = None,
) -> tuple[int, int]:
    """Award upfront XP for a completed annotation task.

    Acquires a user-scoped advisory lock so that cap-check and XP write are atomic.
    Returns (xp_awarded, 0) — credit is always 0 upfront.

    Also flags rapid-submit pattern on the feedback row (soft flag, no XP block).
    """
    now = datetime.now(timezone.utc)

    _advisory_lock_user_reward(db, user_id)

    if _is_known_spammer(db, user_id):
        db.commit()
        return 0, 0

    daily_count, daily_xp, _ = get_daily_annotation_stats(db, user_id)

    xp_to_award = compute_upfront_reward(decision, daily_count, daily_xp)

    # Rapid-submit flag (soft — does not block XP, flags for admin review).
    if feedback_id is not None and _check_rapid_submit(db, user_id, now):
        fb = db.get(JugglingBallFeedback, feedback_id)
        if fb is not None and isinstance(fb.spam_flags, list):
            if "rapid_submit" not in fb.spam_flags:
                fb.spam_flags = fb.spam_flags + ["rapid_submit"]

    if xp_to_award <= 0:
        db.commit()
        return 0, 0

    tx_type = (
        "BALL_ANNOTATION_XP_CORRECTED"
        if decision == "corrected"
        else "BALL_ANNOTATION_XP"
    )
    idempotency_key = f"ball_annotation_xp_{assignment_id}"
    awarded = _award_xp_inline(
        db, user_id, xp_to_award, tx_type, idempotency_key,
        f"Ball annotation upfront: {decision}"
    )

    db.commit()
    return awarded, 0


def award_annotation_accuracy_bonus(
    db: Session,
    feedback_id: _uuid_mod.UUID,
    user_id: int,
    decision: str,
    is_gold_standard: bool,
    reliability_at_submit: float,
) -> tuple[int, int]:
    """Award posterior XP + credit after consensus sets approval_state = "approved".

    Acquires a user-scoped advisory lock so that cap-check, XP write, and credit
    write are all atomic. Returns (xp_awarded, credit_awarded).

    Safe to call multiple times for the same feedback_id — idempotency keys prevent
    duplicate awards even if the consensus task re-runs.
    """
    _advisory_lock_user_reward(db, user_id)

    _, daily_xp, daily_credits = get_daily_annotation_stats(db, user_id)

    # XP: base accuracy bonus + optional gold bonus
    total_bonus_xp = settings.BALL_ANNOTATION_XP_ACCURACY_BONUS
    if is_gold_standard:
        total_bonus_xp += settings.BALL_ANNOTATION_XP_GOLD_BONUS

    remaining = max(0, settings.BALL_ANNOTATION_MAX_XP_PER_DAY - daily_xp)
    xp_to_award = min(total_bonus_xp, remaining)

    xp_awarded = 0
    if xp_to_award > 0:
        xp_awarded = _award_xp_inline(
            db, user_id, xp_to_award,
            "BALL_ANNOTATION_ACCURACY_BONUS",
            f"ball_annotation_accuracy_{feedback_id}",
            "Ball annotation accuracy bonus",
        )

    # Credit: only for approved corrected decisions above reliability threshold.
    credit_awarded = 0
    _rel = reliability_at_submit if reliability_at_submit is not None else 0.5
    if (
        decision == "corrected"
        and _rel >= settings.BALL_ANNOTATION_MIN_RELIABILITY_FOR_CREDIT
        and daily_credits < settings.BALL_ANNOTATION_MAX_CORRECTED_CREDIT_PER_DAY
    ):
        credit_awarded = _award_credit_inline(
            db, user_id,
            f"ball_annotation_credit_{feedback_id}",
            "Ball annotation corrected — consensus approved",
        )

    db.commit()
    return xp_awarded, credit_awarded
