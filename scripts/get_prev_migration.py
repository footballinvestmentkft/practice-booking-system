"""
Resolve the 'previous' Alembic revision for use in CI downgrade steps.

Usage:
    PREV=$(python3 scripts/get_prev_migration.py)
    alembic downgrade "$PREV"

Background:
    `alembic downgrade -1` fails with "Ambiguous walk" when the current head
    is a merge migration (down_revision is a tuple, not a single string).
    This script derives the correct parent unambiguously:
      - Regular migration → returns its single down_revision
      - Merge migration   → returns the first parent in the tuple
      - At base           → returns "base"
"""
from __future__ import annotations

import sys

from alembic.config import Config
from alembic.script import ScriptDirectory

cfg = Config("alembic.ini")
script = ScriptDirectory.from_config(cfg)

heads = script.get_heads()
if not heads:
    print("base")
    sys.exit(0)

rev = script.get_revision(heads[0])
down = rev.down_revision

if isinstance(down, (list, tuple)):
    print(down[0])
elif down:
    print(down)
else:
    print("base")
