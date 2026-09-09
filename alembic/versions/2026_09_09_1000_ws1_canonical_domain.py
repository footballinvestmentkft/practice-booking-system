"""WS1 canonical program, consent and football category domain.

Revision ID: 2026_09_09_1000
Revises: 2026_06_24_1000
"""
from alembic import op
import sqlalchemy as sa


revision = "2026_09_09_1000"
down_revision = "2026_06_24_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_licenses",
        sa.Column("canonical_program_id", sa.String(length=40), nullable=True),
    )
    op.create_index(
        "ix_user_licenses_canonical_program_id",
        "user_licenses",
        ["canonical_program_id"],
    )
    op.create_index(
        "uq_user_license_user_canonical_program",
        "user_licenses",
        ["user_id", "canonical_program_id"],
        unique=True,
        postgresql_where=sa.text("canonical_program_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_user_licenses_canonical_program_id",
        "user_licenses",
        "canonical_program_id IS NULL OR canonical_program_id IN "
        "('LFA_FOOTBALL_PLAYER','LFA_COACH','GANCUJU_PLAYER','INTERNSHIP')",
    )
    # Existing non-zero balances remain untouched. New wallet values and
    # later balance mutations are rejected at the database boundary.
    op.execute(
        """
        CREATE FUNCTION ws1_reject_legacy_license_wallet_write()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' AND COALESCE(NEW.credit_balance, 0) <> 0 THEN
                RAISE EXCEPTION 'legacy license wallet is read-only';
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.credit_balance IS DISTINCT FROM OLD.credit_balance THEN
                RAISE EXCEPTION 'legacy license wallet is read-only';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_user_licenses_legacy_wallet_read_only
        BEFORE INSERT OR UPDATE ON user_licenses
        FOR EACH ROW EXECUTE FUNCTION ws1_reject_legacy_license_wallet_write()
        """
    )

    op.create_table(
        "user_guardian_consents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("guardian_name", sa.String(length=200), nullable=False),
        sa.Column("guardian_relationship", sa.String(length=100), nullable=True),
        sa.Column("evidence_reference", sa.String(length=500), nullable=True),
        sa.Column("granted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.Integer(), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_user_guardian_consents_user_id", "user_guardian_consents", ["user_id"])
    op.create_index(
        "uq_user_guardian_consents_one_active",
        "user_guardian_consents",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "football_season_category_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_user_id", sa.Integer(), nullable=False),
        sa.Column("user_license_id", sa.Integer(), nullable=False),
        sa.Column("season_start", sa.Date(), nullable=False),
        sa.Column("season_end", sa.Date(), nullable=False),
        sa.Column("season_base_category", sa.String(length=20), nullable=False),
        sa.Column("effective_category", sa.String(length=20), nullable=False),
        sa.Column("base_participation_retained", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_license_id"], ["user_licenses.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("player_user_id", "season_start", name="uq_football_assignment_player_season"),
        sa.UniqueConstraint("user_license_id", "season_start", name="uq_football_assignment_license_season"),
        sa.CheckConstraint("season_end > season_start", name="ck_football_assignment_season_dates"),
        sa.CheckConstraint(
            "season_base_category IN ('PRE','YOUTH','AMATEUR')",
            name="ck_football_assignment_base_category",
        ),
        sa.CheckConstraint(
            "effective_category IN ('PRE','YOUTH','AMATEUR','PRO')",
            name="ck_football_assignment_effective_category",
        ),
        sa.CheckConstraint("version >= 1", name="ck_football_assignment_version"),
    )
    op.create_index(
        "ix_football_season_category_assignments_player_user_id",
        "football_season_category_assignments",
        ["player_user_id"],
    )
    op.create_index(
        "ix_football_season_category_assignments_user_license_id",
        "football_season_category_assignments",
        ["user_license_id"],
    )
    op.create_index(
        "ix_football_season_category_assignments_season_start",
        "football_season_category_assignments",
        ["season_start"],
    )

    op.add_column(
        "semester_enrollments",
        sa.Column("football_category_assignment_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_semester_enrollments_football_category_assignment",
        "semester_enrollments",
        "football_season_category_assignments",
        ["football_category_assignment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_semester_enrollments_football_category_assignment_id",
        "semester_enrollments",
        ["football_category_assignment_id"],
    )

    op.create_table(
        "football_category_movement_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("player_user_id", sa.Integer(), nullable=False),
        sa.Column("season_start", sa.Date(), nullable=False),
        sa.Column("season_end", sa.Date(), nullable=False),
        sa.Column("season_base_category", sa.String(length=20), nullable=False),
        sa.Column("previous_effective_category", sa.String(length=20), nullable=False),
        sa.Column("target_effective_category", sa.String(length=20), nullable=False),
        sa.Column("base_participation_retained", sa.Boolean(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("actor_role", sa.String(length=30), nullable=False),
        sa.Column("authorization_basis", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("instructor_assignment_id", sa.Integer(), nullable=True),
        sa.Column("source_enrollment_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("assignment_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["football_season_category_assignments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["player_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["instructor_assignment_id"], ["instructor_assignments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_enrollment_id"], ["semester_enrollments.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_football_movement_idempotency_key"),
        sa.CheckConstraint(
            "season_base_category IN ('PRE','YOUTH','AMATEUR')",
            name="ck_football_movement_base_category",
        ),
        sa.CheckConstraint(
            "previous_effective_category IN ('PRE','YOUTH','AMATEUR','PRO')",
            name="ck_football_movement_previous_category",
        ),
        sa.CheckConstraint(
            "target_effective_category IN ('PRE','YOUTH','AMATEUR','PRO')",
            name="ck_football_movement_target_category",
        ),
    )
    op.create_index(
        "ix_football_category_movement_events_assignment_id",
        "football_category_movement_events",
        ["assignment_id"],
    )
    op.create_index(
        "ix_football_category_movement_events_player_user_id",
        "football_category_movement_events",
        ["player_user_id"],
    )
    op.create_index(
        "ix_football_category_movement_events_actor_user_id",
        "football_category_movement_events",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_football_category_movement_events_idempotency_key",
        "football_category_movement_events",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_football_category_movement_events_created_at",
        "football_category_movement_events",
        ["created_at"],
    )

    op.add_column(
        "credit_transactions",
        sa.Column("context_user_license_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_credit_transactions_context_user_license",
        "credit_transactions",
        "user_licenses",
        ["context_user_license_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_credit_transactions_context_user_license_id",
        "credit_transactions",
        ["context_user_license_id"],
    )
    # NOT VALID preserves historical license-owned rows without a backfill,
    # while PostgreSQL enforces global User ownership for every new row.
    op.execute(
        "ALTER TABLE credit_transactions "
        "ADD CONSTRAINT ck_credit_transactions_global_owner_new "
        "CHECK (user_id IS NOT NULL AND user_license_id IS NULL) NOT VALID"
    )
    # Historical license-owned ledger rows remain valid evidence, but a license
    # carrying such rows can no longer cascade-delete financial history.
    op.drop_constraint(
        "credit_transactions_user_license_id_fkey",
        "credit_transactions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "credit_transactions_user_license_id_fkey",
        "credit_transactions",
        "user_licenses",
        ["user_license_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_user_licenses_legacy_wallet_read_only ON user_licenses"
    )
    op.execute("DROP FUNCTION IF EXISTS ws1_reject_legacy_license_wallet_write()")
    op.drop_constraint(
        "ck_credit_transactions_global_owner_new", "credit_transactions", type_="check"
    )
    op.drop_constraint(
        "credit_transactions_user_license_id_fkey",
        "credit_transactions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "credit_transactions_user_license_id_fkey",
        "credit_transactions",
        "user_licenses",
        ["user_license_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("ix_credit_transactions_context_user_license_id", table_name="credit_transactions")
    op.drop_constraint(
        "fk_credit_transactions_context_user_license", "credit_transactions", type_="foreignkey"
    )
    op.drop_column("credit_transactions", "context_user_license_id")

    op.drop_table("football_category_movement_events")
    op.drop_index(
        "ix_semester_enrollments_football_category_assignment_id",
        table_name="semester_enrollments",
    )
    op.drop_constraint(
        "fk_semester_enrollments_football_category_assignment",
        "semester_enrollments",
        type_="foreignkey",
    )
    op.drop_column("semester_enrollments", "football_category_assignment_id")
    op.drop_table("football_season_category_assignments")
    op.drop_table("user_guardian_consents")

    op.drop_constraint("ck_user_licenses_canonical_program_id", "user_licenses", type_="check")
    op.drop_index("uq_user_license_user_canonical_program", table_name="user_licenses")
    op.drop_index("ix_user_licenses_canonical_program_id", table_name="user_licenses")
    op.drop_column("user_licenses", "canonical_program_id")
