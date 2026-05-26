"""Expand mood photo slots from 4 to 6.

Adds mood_angry_competitive and mood_surprised_shocked to the
ck_mood_photo_slot_valid CHECK constraint on user_mood_photos.

PostgreSQL does not support ALTER CHECK in-place; the constraint must be
dropped and recreated.  The table data is not touched — existing rows
(4 old slots) satisfy the wider constraint.

Revision ID: 2026_05_26_1200
Revises:     2026_05_26_1100
Create Date: 2026-05-26 12:00:00
"""
from alembic import op

revision      = "2026_05_26_1200"
down_revision = "2026_05_26_1100"
branch_labels = None
depends_on    = None

_TABLE      = "user_mood_photos"
_CONSTRAINT = "ck_mood_photo_slot_valid"

_SLOTS_V2 = (
    "mood_intro_neutral",
    "mood_happy_smile",
    "mood_celebration",
    "mood_sad_disappointed",
    "mood_angry_competitive",
    "mood_surprised_shocked",
)

_SLOTS_V1 = (
    "mood_intro_neutral",
    "mood_happy_smile",
    "mood_celebration",
    "mood_sad_disappointed",
)


def _slots_sql(slots: tuple[str, ...]) -> str:
    return ", ".join(f"'{s}'" for s in slots)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        f"slot IN ({_slots_sql(_SLOTS_V2)})",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        f"slot IN ({_slots_sql(_SLOTS_V1)})",
    )
