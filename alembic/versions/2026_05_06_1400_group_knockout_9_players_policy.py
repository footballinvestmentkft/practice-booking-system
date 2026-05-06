"""group_knockout: add 9_players qualification policy to group_configuration

Revision ID: 2026_05_06_1400
Revises: 2026_05_05_1000
Create Date: 2026-05-06 14:00:00.000000

Surgical jsonb_set — only adds the group_configuration.9_players key.
All existing entries (8/12/16/24/32/48/64p) are untouched by this operation.

Background: the GroupKnockoutGenerator now reads qualification_policy from
group_configuration[N_players] (per-size) instead of the top-level config.
9 players → 3×3 groups → 3 winners + 1 best runner-up → 4 KO qualifiers,
no play-in rounds, SF1: A winner vs best runner-up, SF2: B winner vs C winner.
"""
from alembic import op
from sqlalchemy import text

revision = '2026_05_06_1400'
down_revision = '2026_05_05_1000'
branch_labels = None
depends_on = None

_NEW_9P_JSON = (
    '{"groups": 3, "players_per_group": 3, "qualifiers": 1,'
    ' "qualification_policy": "winners_plus_best_runner_up",'
    ' "best_runner_up_count": 1}'
)


def upgrade() -> None:
    # config column is JSON (not JSONB) — cast to jsonb for jsonb_set, then back to json.
    # jsonb_set with create_missing=true inserts the new key without touching siblings.
    # The IS NULL guard makes this idempotent — safe to run twice.
    op.execute(text(f"""
        UPDATE tournament_types
        SET config = jsonb_set(
            config::jsonb,
            '{{group_configuration,9_players}}',
            '{_NEW_9P_JSON}'::jsonb,
            true
        )::json
        WHERE code = 'group_knockout'
          AND (config -> 'group_configuration' -> '9_players') IS NULL
    """))


def downgrade() -> None:
    # #- path operator removes exactly the 9_players key; all other keys survive.
    op.execute(text("""
        UPDATE tournament_types
        SET config = (config::jsonb #- '{group_configuration,9_players}')::json
        WHERE code = 'group_knockout'
    """))
