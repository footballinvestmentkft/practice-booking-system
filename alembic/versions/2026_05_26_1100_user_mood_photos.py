"""Add user_mood_photos table (hangulatkép feature).

Revision ID: 2026_05_26_1100
Revises:     2026_05_25_1400
Create Date: 2026-05-26 11:00:00
"""
import sqlalchemy as sa
from alembic import op

revision    = "2026_05_26_1100"
down_revision = "2026_05_25_1400"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        "user_mood_photos",
        sa.Column("id",      sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "license_id",
            sa.Integer,
            sa.ForeignKey("user_licenses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("slot",              sa.String(30),  nullable=False),
        sa.Column("original_url",      sa.String(512), nullable=False),
        sa.Column("processed_png_url", sa.String(512), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "slot", name="uq_mood_photo_user_slot"),
        sa.CheckConstraint(
            "slot IN ('mood_intro_neutral','mood_happy_smile',"
            "'mood_celebration','mood_sad_disappointed')",
            name="ck_mood_photo_slot_valid",
        ),
    )
    op.create_index(
        "ix_user_mood_photos_user_id",
        "user_mood_photos",
        ["user_id"],
    )
    op.create_index(
        "ix_user_mood_photos_license_id",
        "user_mood_photos",
        ["license_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_mood_photos_license_id", table_name="user_mood_photos")
    op.drop_index("ix_user_mood_photos_user_id",    table_name="user_mood_photos")
    op.drop_table("user_mood_photos")
