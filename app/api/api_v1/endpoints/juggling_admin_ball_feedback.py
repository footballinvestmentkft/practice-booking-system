"""
Admin ball feedback review + export endpoints — AN-3B2B2 D3/D4.

GET  /admin/juggling/ball-feedback/review-queue
  Returns feedback rows in needs_review or spam state for manual admin review.

PATCH /admin/juggling/ball-feedback/{feedback_id}/review
  Approve, reject, or escalate a single feedback row.
  approve          → approval_state=approved, reviewed_at stamped
  reject           → approval_state=rejected, reviewed_at stamped
  escalate_to_review → clears spam_flags, sets approval_state=needs_review

GET /admin/juggling/ball-feedback/training-export
  Returns training-eligible, not-yet-exported ground truth frames.
  Stamps exported_at + dataset_version on returned rows (idempotent with ?version=).

All endpoints require admin role. No BALL_FEEDBACK_ENABLED gate on admin routes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin_user
from app.models.juggling import JugglingBallFeedback, JugglingFrameGroundTruth
from app.models.user import User
from app.schemas.juggling import (
    BallFeedbackAdminItem,
    BallFeedbackAdminQueueResponse,
    BallFeedbackReviewAction,
    TrainingExportFrame,
    TrainingExportResponse,
)

router = APIRouter()

_TAG = ["admin", "juggling"]


@router.get(
    "/ball-feedback/review-queue",
    response_model=BallFeedbackAdminQueueResponse,
    summary="List feedback rows awaiting admin review",
    tags=_TAG,
)
def get_review_queue(
    video_id: str | None = Query(None, description="Filter by video UUID"),
    state: str = Query("needs_review", description="needs_review | spam"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin_user),
) -> BallFeedbackAdminQueueResponse:
    if state not in ("needs_review", "spam"):
        raise HTTPException(
            status_code=422, detail="state must be 'needs_review' or 'spam'"
        )
    q = select(JugglingBallFeedback).where(
        JugglingBallFeedback.approval_state == state
    )
    if video_id is not None:
        q = q.where(JugglingBallFeedback.video_id == video_id)
    q = q.order_by(JugglingBallFeedback.created_at.asc())

    all_rows = db.execute(q).scalars().all()
    total = len(all_rows)
    page  = all_rows[offset : offset + limit]

    return BallFeedbackAdminQueueResponse(
        items=[BallFeedbackAdminItem.model_validate(r) for r in page],
        total=total,
    )


@router.patch(
    "/ball-feedback/{feedback_id}/review",
    response_model=BallFeedbackAdminItem,
    summary="Approve, reject, or escalate a feedback row",
    tags=_TAG,
)
def patch_feedback_review(
    feedback_id: uuid.UUID,
    body: BallFeedbackReviewAction,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
) -> BallFeedbackAdminItem:
    row = db.get(JugglingBallFeedback, feedback_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Feedback record not found.")

    if body.action == "approve":
        if row.approval_state == "approved":
            raise HTTPException(
                status_code=409, detail="Feedback is already approved."
            )
        row.approval_state       = "approved"
        row.reviewed_at          = datetime.now(timezone.utc)
        row.reviewed_by_user_id  = admin.id

    elif body.action == "reject":
        row.approval_state       = "rejected"
        row.reviewed_at          = datetime.now(timezone.utc)
        row.reviewed_by_user_id  = admin.id

    elif body.action == "escalate_to_review":
        row.approval_state = "needs_review"
        row.spam_flags     = []
        row.reviewed_at    = datetime.now(timezone.utc)
        row.reviewed_by_user_id = admin.id

    db.commit()
    db.refresh(row)
    return BallFeedbackAdminItem.model_validate(row)


@router.get(
    "/ball-feedback/training-export",
    response_model=TrainingExportResponse,
    summary="Export training-eligible ground truth frames",
    tags=_TAG,
)
def get_training_export(
    version: str | None = Query(
        None,
        description="Re-export a specific dataset version (idempotent).",
    ),
    limit: int = Query(1000, ge=1, le=10000),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin_user),
) -> TrainingExportResponse:
    now = datetime.now(timezone.utc)

    if version is not None:
        # Idempotent re-export: return already-stamped rows for this version
        rows = db.execute(
            select(JugglingFrameGroundTruth).where(
                JugglingFrameGroundTruth.dataset_version == version,
                JugglingFrameGroundTruth.exported_at.is_not(None),
            ).limit(limit)
        ).scalars().all()
        if not rows:
            raise HTTPException(
                status_code=404,
                detail=f"No export found for version '{version}'.",
            )
        export_version = version
        export_time    = rows[0].exported_at

    else:
        # Fresh export: only not-yet-exported eligible rows
        rows = db.execute(
            select(JugglingFrameGroundTruth).where(
                JugglingFrameGroundTruth.training_eligible.is_(True),
                JugglingFrameGroundTruth.exported_at.is_(None),
            ).limit(limit)
        ).scalars().all()

        export_version = now.strftime("v1_%Y-%m-%d_%H%M")
        export_time    = now

        # Stamp all returned rows
        for row in rows:
            row.exported_at     = export_time
            row.dataset_version = export_version
        if rows:
            db.commit()

    return TrainingExportResponse(
        version=export_version,
        exported_at=export_time,
        frame_count=len(rows),
        frames=[TrainingExportFrame.model_validate(r) for r in rows],
    )
