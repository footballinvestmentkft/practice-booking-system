"""Expand mood photo slots from 6 to 9 (Phase-B).

Adds mood_focused_ready, mood_confident, mood_proud to the
ck_mood_photo_slot_valid CHECK constraint on user_mood_photos.

PostgreSQL does not support ALTER CHECK in-place; the constraint must be
dropped and recreated.  Existing rows (6 old slots) satisfy the wider
constraint — no data migration required.

Revision ID: 2026_06_03_1000
Revises:     2026_06_02_1000
Create Date: 2026-06-03 10:00:00
"""
from alembic import op

revision      = "2026_06_03_1000"
down_revision = "2026_06_02_1000"
branch_labels = None
depends_on    = None

_TABLE      = "user_mood_photos"
_CONSTRAINT = "ck_mood_photo_slot_valid"

_SLOTS_V3 = (
    "mood_intro_neutral",
    "mood_happy_smile",
    "mood_celebration",
    "mood_sad_disappointed",
    "mood_angry_competitive",
    "mood_surprised_shocked",
    "mood_focused_ready",
    "mood_confident",
    "mood_proud",
)

_SLOTS_V2 = (
    "mood_intro_neutral",
    "mood_happy_smile",
    "mood_celebration",
    "mood_sad_disappointed",
    "mood_angry_competitive",
    "mood_surprised_shocked",
)


def _slots_sql(slots: tuple[str, ...]) -> str:
    return ", ".join(f"'{s}'" for s in slots)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        f"slot IN ({_slots_sql(_SLOTS_V3)})",
    )


def downgrade() -> None:
    # Downgrade removes Phase-B slots from the constraint.
    # Rows with new slots must be deleted manually before downgrading.
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        f"slot IN ({_slots_sql(_SLOTS_V2)})",
    )
