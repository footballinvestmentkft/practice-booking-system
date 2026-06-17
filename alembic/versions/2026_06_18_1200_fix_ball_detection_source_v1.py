"""Fix ball detection_source CHECK constraint: v2 → v1.

Licence audit confirmed SSD MobileNet v1 (Apache-2.0) as the approved model.

Revision ID: 2026_06_18_1200
Revises: 2026_06_18_1100
"""
from alembic import op

revision      = "2026_06_18_1200"
down_revision = "2026_06_18_1100"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_juggling_ball_detections_source",
        "juggling_ball_detections",
        type_="check",
    )
    op.create_check_constraint(
        "ck_juggling_ball_detections_source",
        "juggling_ball_detections",
        "detection_source IN ('mobilenet_ssd_v1', 'manual')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_juggling_ball_detections_source",
        "juggling_ball_detections",
        type_="check",
    )
    op.create_check_constraint(
        "ck_juggling_ball_detections_source",
        "juggling_ball_detections",
        "detection_source IN ('mobilenet_ssd_v2', 'manual')",
    )
