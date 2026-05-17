"""Add card_themes table with seed data for the 6 launch themes.

Revision ID: 2026_05_17_1000
Revises:     2026_05_16_1001
Create Date: 2026-05-17 10:00:00.000000

Introduces the card_themes table as a DB-backed registry of colour themes.
The THEMES dict in card_theme_service.py remains as a warm-start fallback.

Seed strategy: ON CONFLICT DO NOTHING — idempotent re-runs safe.
No imports from app code — all values inlined so the migration is self-contained.
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

revision      = '2026_05_17_1000'
down_revision = '2026_05_16_1001'
branch_labels = None
depends_on    = None

_NOW = datetime(2026, 5, 17, 10, 0, 0, tzinfo=timezone.utc)

# Seed rows — mirrors THEMES dict in card_theme_service.py exactly.
# (id, label, is_premium, credit_cost, sort_order,
#  panel_bg, body_bg, tab_bg, accent, page_bg, dot_color,
#  is_light_body_bg, text_faint, val_neutral, skill_up, skill_dn)
_SEED = [
    (
        "default", "Slate", False, 0, 0,
        "linear-gradient(155deg, #1a2744 0%, #2a3a5c 60%, #1e3a4a 100%)",
        "#1a202c", "#2d3748", "#667eea", "#0f1923", "#667eea",
        False,
        "rgba(255,255,255,0.35)", "rgba(255,255,255,0.85)", "#48bb78", "#fc8181",
    ),
    (
        "midnight", "Midnight", False, 0, 1,
        "linear-gradient(155deg, #0d0d0d 0%, #1a1a2e 60%, #16213e 100%)",
        "#0f0f0f", "#1a1a1a", "#00d4ff", "#050505", "#00d4ff",
        False,
        "rgba(255,255,255,0.35)", "rgba(255,255,255,0.85)", "#48bb78", "#fc8181",
    ),
    (
        "arctic", "Arctic", False, 0, 2,
        "linear-gradient(155deg, #1a2744 0%, #2a3a5c 60%, #1e3a4a 100%)",
        "#f7fafc", "#edf2f7", "#4299e1", "#e2e8f0", "#4299e1",
        True,
        "rgba(0,0,0,0.30)", "rgba(0,0,0,0.70)", "#276749", "#c53030",
    ),
    (
        "gold", "Gold", True, 500, 3,
        "linear-gradient(155deg, #3d2200 0%, #5c3500 60%, #3d2200 100%)",
        "#1e1500", "#2d1f00", "#f6ad3c", "#120d00", "#f6ad3c",
        False,
        "rgba(255,255,255,0.48)", "rgba(255,255,255,0.68)", "#48bb78", "#fc8181",
    ),
    (
        "emerald", "Emerald", True, 500, 4,
        "linear-gradient(155deg, #0a2d0a 0%, #144d1e 60%, #0a2d14 100%)",
        "#0d1f0d", "#142b14", "#4cde82", "#060f06", "#4cde82",
        False,
        "rgba(255,255,255,0.48)", "rgba(255,255,255,0.68)", "#48bb78", "#fc8181",
    ),
    (
        "crimson", "Crimson", True, 500, 5,
        "linear-gradient(155deg, #3d0a0a 0%, #5c1414 60%, #3d0a14 100%)",
        "#1e0d0d", "#2d1010", "#ff6b6b", "#120404", "#ff6b6b",
        False,
        "rgba(255,255,255,0.38)", "rgba(255,255,255,0.65)", "#68d391", "#ffb3b3",
    ),
]


def upgrade() -> None:
    op.create_table(
        'card_themes',

        sa.Column('id',    sa.String(50), primary_key=True),
        sa.Column('label', sa.String(80), nullable=False),

        sa.Column('is_premium',  sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('credit_cost', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active',   sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('sort_order',  sa.Integer(), nullable=False, server_default='0'),

        sa.Column('panel_bg',  sa.Text(),        nullable=False),
        sa.Column('body_bg',   sa.String(100),   nullable=False),
        sa.Column('tab_bg',    sa.String(100),   nullable=False),
        sa.Column('accent',    sa.String(100),   nullable=False),
        sa.Column('page_bg',   sa.String(100),   nullable=False),
        sa.Column('dot_color', sa.String(100),   nullable=False),

        sa.Column('is_light_body_bg', sa.Boolean(), nullable=False,
                  server_default='false'),
        sa.Column('text_faint',  sa.String(100), nullable=False,
                  server_default='rgba(255,255,255,0.35)'),
        sa.Column('val_neutral', sa.String(100), nullable=False,
                  server_default='rgba(255,255,255,0.85)'),
        sa.Column('skill_up',    sa.String(100), nullable=False,
                  server_default='#48bb78'),
        sa.Column('skill_dn',    sa.String(100), nullable=False,
                  server_default='#fc8181'),

        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )

    # Seed — idempotent via ON CONFLICT DO NOTHING
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            INSERT INTO card_themes (
                id, label, is_premium, credit_cost, sort_order,
                panel_bg, body_bg, tab_bg, accent, page_bg, dot_color,
                is_light_body_bg, text_faint, val_neutral, skill_up, skill_dn,
                created_at, updated_at
            ) VALUES (
                :id, :label, :is_premium, :credit_cost, :sort_order,
                :panel_bg, :body_bg, :tab_bg, :accent, :page_bg, :dot_color,
                :is_light_body_bg, :text_faint, :val_neutral, :skill_up, :skill_dn,
                :created_at, :updated_at
            )
            ON CONFLICT (id) DO NOTHING
        """),
        [
            {
                "id": row[0], "label": row[1],
                "is_premium": row[2], "credit_cost": row[3], "sort_order": row[4],
                "panel_bg": row[5], "body_bg": row[6], "tab_bg": row[7],
                "accent": row[8], "page_bg": row[9], "dot_color": row[10],
                "is_light_body_bg": row[11],
                "text_faint": row[12], "val_neutral": row[13],
                "skill_up": row[14], "skill_dn": row[15],
                "created_at": _NOW, "updated_at": _NOW,
            }
            for row in _SEED
        ],
    )


def downgrade() -> None:
    op.drop_table('card_themes')
