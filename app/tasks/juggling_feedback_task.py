"""
Ball feedback consensus task — AN-3B2B2 D1.

compute_frame_consensus(video_id, frame_ms):
  Aggregates non-spam feedback rows for (video_id, frame_ms), derives ground
  truth, updates approval_state on rows, and refreshes user reliability scores.

  Idempotent: re-running for the same (video_id, frame_ms) always re-derives
  the result from current DB state.

Thresholds (conservative MVP):
  AUTO_APPROVE_MIN_VOTES      = 3
  AUTO_APPROVE_AGREEMENT_RATE = 0.80   (80% unweighted vote agreement)
  TRAINING_ELIGIBLE_AGREEMENT = 0.75   (floor for training_eligible=True)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.juggling import (
    JugglingBallFeedback,
    JugglingConsent,
    JugglingFrameGroundTruth,
    JugglingVideo,
    UserAnnotationReliability,
)

logger = logging.getLogger(__name__)

AUTO_APPROVE_MIN_VOTES      = 3
AUTO_APPROVE_AGREEMENT_RATE = 0.80
TRAINING_ELIGIBLE_AGREEMENT = 0.75


def _update_reliability(
    db: Session, user_id: int, correct: bool, is_gold: bool
) -> None:
    rel = db.get(UserAnnotationReliability, user_id)
    if rel is None:
        rel = UserAnnotationReliability(user_id=user_id)
        db.add(rel)
        db.flush()
    weight = 2 if is_gold else 1
    rel.total_feedbacks   += weight
    rel.correct_feedbacks += weight if correct else 0
    if is_gold:
        rel.gold_attempts += 1
        if correct:
            rel.gold_correct += 1
    if rel.total_feedbacks > 0:
        raw = rel.correct_feedbacks / rel.total_feedbacks
        rel.ball_annotation_reliability = max(0.1, min(1.0, raw))
    rel.last_updated = datetime.now(timezone.utc)


def run_compute_frame_consensus(
    db: Session, video_id: str, frame_ms: int
) -> None:
    """Core consensus logic — callable directly in tests without Celery.

    Live consent check (AN-3B2F PR-1A): if the video owner has revoked
    training_consent, the GT row is not created or updated for this frame.
    This prevents revoked-consent data from entering the training pipeline
    even if feedback rows were already submitted before revocation.
    """
    video = db.execute(
        select(JugglingVideo).where(JugglingVideo.id == video_id)
    ).scalar_one_or_none()
    if video is None or video.status == "gdpr_deleted":
        logger.info(
            "Consensus skipped: video %s not found or gdpr_deleted", video_id
        )
        return

    consent = db.execute(
        select(JugglingConsent).where(
            JugglingConsent.user_id == video.user_id,
            JugglingConsent.training_consent.is_(True),
        )
    ).scalar_one_or_none()
    if consent is None:
        logger.info(
            "Consensus skipped: training_consent revoked for video %s (owner %s)",
            video_id, video.user_id,
        )
        return

    rows = db.execute(
        select(JugglingBallFeedback).where(
            JugglingBallFeedback.video_id == video_id,
            JugglingBallFeedback.frame_ms == frame_ms,
            JugglingBallFeedback.approval_state != "spam",
        )
    ).scalars().all()

    if not rows:
        return

    vote_count       = len(rows)
    yes_count        = sum(1 for r in rows if r.decision in ("confirm", "corrected"))
    no_ball_count    = sum(1 for r in rows if r.decision == "no_ball")
    no_count         = sum(1 for r in rows if r.decision == "reject")
    correction_count = sum(1 for r in rows if r.decision == "corrected")

    max_votes      = max(yes_count, no_ball_count, no_count)
    agreement_rate = max_votes / vote_count

    avg_reliability = sum(r.user_reliability_at_submit or 0.5 for r in rows) / vote_count
    confidence_score = round(agreement_rate * avg_reliability, 4)

    # Determine consensus outcome
    if vote_count < AUTO_APPROVE_MIN_VOTES or agreement_rate < AUTO_APPROVE_AGREEMENT_RATE:
        gt_decision       = "uncertain"
        training_eligible = False
        # Mark rows needs_review only once enough votes exist but no agreement
        new_state = "needs_review" if vote_count >= AUTO_APPROVE_MIN_VOTES else None
    else:
        if yes_count == max_votes:
            gt_decision = "ball_present"
        elif no_ball_count == max_votes:
            gt_decision = "no_ball"
        else:
            gt_decision = "uncertain"
        training_eligible = (
            gt_decision != "uncertain" and agreement_rate >= TRAINING_ELIGIBLE_AGREEMENT
        )
        new_state = "approved"

    # Reliability-weighted centroid for corrected positions
    gt_x: Optional[float] = None
    gt_y: Optional[float] = None
    corrected = [
        r for r in rows if r.decision == "corrected" and r.corrected_x is not None
    ]
    if corrected:
        total_w = sum(r.user_reliability_at_submit or 0.5 for r in corrected)
        if total_w > 0:
            gt_x = sum(
                (r.user_reliability_at_submit or 0.5) * r.corrected_x for r in corrected
            ) / total_w
            gt_y = sum(
                (r.user_reliability_at_submit or 0.5) * r.corrected_y for r in corrected
            ) / total_w

    # Upsert juggling_frame_ground_truth
    gt_row = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == video_id,
            JugglingFrameGroundTruth.frame_ms == frame_ms,
        )
    ).scalar_one_or_none()
    if gt_row is None:
        gt_row = JugglingFrameGroundTruth(video_id=video_id, frame_ms=frame_ms)
        db.add(gt_row)

    gt_row.gt_decision       = gt_decision
    gt_row.gt_x              = gt_x
    gt_row.gt_y              = gt_y
    gt_row.vote_count        = vote_count
    gt_row.yes_votes         = yes_count
    gt_row.no_votes          = no_count
    gt_row.no_ball_votes     = no_ball_count
    gt_row.correction_count  = correction_count
    gt_row.agreement_rate    = round(agreement_rate, 4)
    gt_row.confidence_score  = confidence_score
    gt_row.training_eligible = training_eligible
    gt_row.updated_at        = datetime.now(timezone.utc)

    # Update approval_state + weighted_vote_contribution on feedback rows
    total_weight = sum(r.user_reliability_at_submit or 0.5 for r in rows)
    for r in rows:
        if new_state is not None and r.approval_state not in ("approved", "rejected"):
            r.approval_state = new_state
        if new_state == "approved" and total_weight > 0:
            r.weighted_vote_contribution = round(
                (r.user_reliability_at_submit or 0.5) / total_weight, 4
            )

    # Update user reliability only when consensus reached
    if new_state == "approved" and gt_decision != "uncertain":
        for r in rows:
            matches = (
                r.decision in ("confirm", "corrected") and gt_decision == "ball_present"
            ) or (r.decision == "no_ball" and gt_decision == "no_ball")
            _update_reliability(db, r.user_id, matches, r.is_gold_standard)

    db.commit()

    # Award posterior XP + credit for approved feedbacks (AN-3B2E).
    # Called after commit so that approval_state is durable before reward is issued.
    # award_annotation_accuracy_bonus() manages its own transaction with advisory lock.
    if new_state == "approved":
        from app.services.juggling.ball_annotation_reward_service import (
            award_annotation_accuracy_bonus,
        )
        for r in rows:
            if r.approval_state == "approved":
                try:
                    award_annotation_accuracy_bonus(
                        db=db,
                        feedback_id=r.id,
                        user_id=r.user_id,
                        decision=r.decision,
                        is_gold_standard=r.is_gold_standard,
                        reliability_at_submit=r.user_reliability_at_submit or 0.5,
                    )
                except Exception:
                    logger.exception(
                        "award_annotation_accuracy_bonus failed: feedback_id=%s", r.id
                    )


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="ball_feedback",
    name="app.tasks.juggling_feedback_task.compute_frame_consensus",
)
def compute_frame_consensus(self, video_id: str, frame_ms: int) -> None:
    db = SessionLocal()
    try:
        run_compute_frame_consensus(db, video_id, frame_ms)
    except Exception as exc:
        logger.exception(
            "compute_frame_consensus failed: video=%s frame_ms=%s", video_id, frame_ms
        )
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()
