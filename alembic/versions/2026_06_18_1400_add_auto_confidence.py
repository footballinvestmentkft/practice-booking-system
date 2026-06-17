"""Add auto_confidence to juggling_ball_detections (AN-3B2C-1 follow-up).

Preserves the original model confidence at auto-detection time so that the
ball_detection_audit.py script can compute a precise high-confidence false
positive rate (auto_confidence >= 0.80, annotator later marked no_ball_detected).

No backfill — existing rows keep auto_confidence = NULL.
The audit script shows N/A for the high-confidence FP metric when all values
are NULL, which is the correct behaviour for pre-migration data.

Revision ID: 2026_06_18_1400
Revises: 2026_06_18_1300
"""
from alembic import op
import sqlalchemy as sa

revision      = "2026_06_18_1400"
down_revision = "2026_06_18_1300"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column(
        "juggling_ball_detections",
        sa.Column(
            "auto_confidence",
            sa.Float(),
            nullable=True,
            comment=(
                "Original model confidence frozen at auto detection time. "
                "NULL for manual-first events and pre-migration rows. "
                "Never overwritten by manual override."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("juggling_ball_detections", "auto_confidence")
