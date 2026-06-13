"""add juggling contact events

Revision ID: 2026_06_13_1000
Revises: 2026_06_11_1100
Create Date: 2026-06-13

New table: juggling_contact_events
  Per-event annotation record for juggling contact annotation.
  Stores player-generated or model-predicted contact events with full
  consent snapshot, review lifecycle, and training eligibility tracking.

Training eligibility dual-gate policy:
  Training use requires BOTH conditions to be true simultaneously:
    1. consent_snapshot->>'training_consent' == 'true'  (historical state at creation)
    2. JugglingConsent.training_consent == true          (current user consent)
  The snapshot is IMMUTABLE audit trail. Revocation is NOT reflected in the
  snapshot — it is enforced at export/query time by joining the live consent.
  See app/services/juggling/contact_service.py is_training_eligible().

New columns on juggling_videos:
  annotation_status      — tracks the video's annotation lifecycle
  annotation_finished_at — when POST /contacts/finish was called
  total_juggling_count   — computed count of non-excluded events after finish

FK policy:
  video_id FK ON DELETE CASCADE — contact events deleted when video is deleted
  created_by_user_id FK ON DELETE RESTRICT — prevents orphan events;
    safe because users.id CASCADE → juggling_videos CASCADE → contact events CASCADE,
    so the RESTRICT is never triggered in practice during GDPR user deletion.
  corrected_from_event_id FK self ON DELETE SET NULL — preserves the correction
    audit trail even if the original event is later deleted.

Invariants:
  No endpoint, service, or router is registered in this migration.
  No production behavior changes in this migration.
  downgrade() fully reverts all changes.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision      = "2026_06_13_1000"
down_revision = "2026_06_11_1100"
branch_labels = None
depends_on    = None

# ── Check constraint names ────────────────────────────────────────────────────

_CK_ANNOTATION_SOURCE      = "ck_juggling_contact_annotation_source"
_CK_ANNOTATION_CONFIDENCE  = "ck_juggling_contact_annotation_confidence"
_CK_ANNOTATION_REVIEW_STATUS = "ck_juggling_contact_annotation_review_status"
_CK_TAXONOMY_REVIEW_STATUS = "ck_juggling_contact_taxonomy_review_status"
_CK_TIMESTAMP_NONNEG       = "ck_juggling_contact_timestamp_ms_nonneg"
_CK_VERSION_POSITIVE       = "ck_juggling_contact_version_positive"
_CK_MODEL_CONFIDENCE_RANGE = "ck_juggling_contact_model_confidence_range"
_CK_VIDEO_ANNOTATION_STATUS = "ck_juggling_videos_annotation_status"

_VALID_ANNOTATION_SOURCES = (
    "manual_user", "model_prediction", "user_corrected"
)
_VALID_ANNOTATION_CONFIDENCES = ("certain", "probable", "uncertain")
_VALID_ANNOTATION_REVIEW_STATUSES = (
    "pending", "confirmed", "corrected", "rejected"
)
_VALID_TAXONOMY_REVIEW_STATUSES = (
    "not_applicable", "pending_taxonomy_review", "reclassified",
    "promotion_candidate", "promoted", "approved_unclassified"
)
_VALID_VIDEO_ANNOTATION_STATUSES = (
    "metadata_ready", "in_progress", "human_review_pending",
    "annotated", "reviewed", "rejected"
)


def upgrade() -> None:
    # ── juggling_contact_events ───────────────────────────────────────────────
    op.create_table(
        "juggling_contact_events",

        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("device_event_id", postgresql.UUID(as_uuid=True), nullable=False),

        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
        sa.Column("contact_type", sa.String(40), nullable=False),
        sa.Column("side", sa.String(20), nullable=True),
        sa.Column("annotation_confidence", sa.String(20), nullable=False),

        sa.Column("annotation_review_status", sa.String(20), nullable=False,
                  server_default="pending"),
        sa.Column("taxonomy_review_status", sa.String(40), nullable=False,
                  server_default="not_applicable"),
        sa.Column("annotation_source", sa.String(30), nullable=False),

        sa.Column("excluded_from_training", sa.Boolean(), nullable=False,
                  server_default="true"),
        sa.Column("excluded_from_count", sa.Boolean(), nullable=False,
                  server_default="false"),

        sa.Column("model_confidence", sa.Float(), nullable=True),
        sa.Column("user_confirmed", sa.Boolean(), nullable=True),
        sa.Column("corrected_from_event_id", postgresql.UUID(as_uuid=True), nullable=True),

        sa.Column("custom_label", sa.String(40), nullable=True),
        sa.Column("custom_description", sa.String(200), nullable=True),
        sa.Column("taxonomy_version", sa.String(10), nullable=False,
                  server_default="v1"),
        sa.Column("consent_snapshot", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("ball_height_approx_px", sa.Integer(), nullable=True),

        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),

        # ── Primary key ───────────────────────────────────────────────────────
        sa.PrimaryKeyConstraint("id"),

        # ── Foreign keys ──────────────────────────────────────────────────────
        sa.ForeignKeyConstraint(
            ["video_id"], ["juggling_videos.id"],
            ondelete="CASCADE",
            name="fk_juggling_contact_events_video_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            ondelete="RESTRICT",
            name="fk_juggling_contact_events_created_by_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["corrected_from_event_id"], ["juggling_contact_events.id"],
            ondelete="SET NULL",
            name="fk_juggling_contact_events_corrected_from",
        ),

        # ── Unique constraint ─────────────────────────────────────────────────
        sa.UniqueConstraint(
            "video_id", "device_event_id",
            name="uq_juggling_contact_device_event",
        ),

        # ── Check constraints ─────────────────────────────────────────────────
        sa.CheckConstraint(
            sa.column("annotation_source").in_(list(_VALID_ANNOTATION_SOURCES)),
            name=_CK_ANNOTATION_SOURCE,
        ),
        sa.CheckConstraint(
            sa.column("annotation_confidence").in_(list(_VALID_ANNOTATION_CONFIDENCES)),
            name=_CK_ANNOTATION_CONFIDENCE,
        ),
        sa.CheckConstraint(
            sa.column("annotation_review_status").in_(list(_VALID_ANNOTATION_REVIEW_STATUSES)),
            name=_CK_ANNOTATION_REVIEW_STATUS,
        ),
        sa.CheckConstraint(
            sa.column("taxonomy_review_status").in_(list(_VALID_TAXONOMY_REVIEW_STATUSES)),
            name=_CK_TAXONOMY_REVIEW_STATUS,
        ),
        sa.CheckConstraint(
            "timestamp_ms >= 0",
            name=_CK_TIMESTAMP_NONNEG,
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=_CK_VERSION_POSITIVE,
        ),
        sa.CheckConstraint(
            "model_confidence IS NULL OR "
            "(model_confidence >= 0.0 AND model_confidence <= 1.0)",
            name=_CK_MODEL_CONFIDENCE_RANGE,
        ),
    )

    # ── Indexes on juggling_contact_events ────────────────────────────────────
    op.create_index(
        "ix_juggling_contact_events_video_id",
        "juggling_contact_events", ["video_id"],
    )
    op.create_index(
        "ix_juggling_contact_events_created_by_user_id",
        "juggling_contact_events", ["created_by_user_id"],
    )
    op.create_index(
        "ix_juggling_contact_events_annotation_review_status",
        "juggling_contact_events", ["annotation_review_status"],
    )
    op.create_index(
        "ix_juggling_contact_events_deleted_at",
        "juggling_contact_events", ["deleted_at"],
    )
    op.create_index(
        "ix_juggling_contact_events_created_at",
        "juggling_contact_events", ["created_at"],
    )
    # Composite index for training export query
    op.create_index(
        "ix_juggling_contact_events_training_filter",
        "juggling_contact_events",
        ["excluded_from_training", "video_id"],
    )

    # ── New columns on juggling_videos ────────────────────────────────────────
    op.add_column(
        "juggling_videos",
        sa.Column(
            "annotation_status",
            sa.String(30),
            nullable=True,
            comment=(
                "Annotation lifecycle: "
                "metadata_ready | in_progress | human_review_pending | "
                "annotated | reviewed | rejected. "
                "NULL = not yet started (same as metadata_ready for legacy rows)."
            ),
        ),
    )
    op.add_column(
        "juggling_videos",
        sa.Column(
            "annotation_finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when POST /contacts/finish was successfully called.",
        ),
    )
    op.add_column(
        "juggling_videos",
        sa.Column(
            "total_juggling_count",
            sa.Integer(),
            nullable=True,
            comment=(
                "Computed count of non-excluded contact events after finish. "
                "NULL until annotation is finished."
            ),
        ),
    )

    op.create_check_constraint(
        _CK_VIDEO_ANNOTATION_STATUS,
        "juggling_videos",
        sa.column("annotation_status").in_(list(_VALID_VIDEO_ANNOTATION_STATUSES)),
    )

    op.create_index(
        "ix_juggling_videos_annotation_status",
        "juggling_videos",
        ["annotation_status"],
    )


def downgrade() -> None:
    # ── Remove annotation tracking from juggling_videos ───────────────────────
    op.drop_index("ix_juggling_videos_annotation_status",
                  table_name="juggling_videos")
    op.drop_constraint(_CK_VIDEO_ANNOTATION_STATUS, "juggling_videos",
                       type_="check")
    op.drop_column("juggling_videos", "total_juggling_count")
    op.drop_column("juggling_videos", "annotation_finished_at")
    op.drop_column("juggling_videos", "annotation_status")

    # ── Drop juggling_contact_events (indexes first, then table) ──────────────
    op.drop_index("ix_juggling_contact_events_training_filter",
                  table_name="juggling_contact_events")
    op.drop_index("ix_juggling_contact_events_created_at",
                  table_name="juggling_contact_events")
    op.drop_index("ix_juggling_contact_events_deleted_at",
                  table_name="juggling_contact_events")
    op.drop_index("ix_juggling_contact_events_annotation_review_status",
                  table_name="juggling_contact_events")
    op.drop_index("ix_juggling_contact_events_created_by_user_id",
                  table_name="juggling_contact_events")
    op.drop_index("ix_juggling_contact_events_video_id",
                  table_name="juggling_contact_events")

    op.drop_table("juggling_contact_events")
