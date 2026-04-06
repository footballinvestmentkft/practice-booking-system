"""
cleanup_legacy_users.py — Remove artifact users left by legacy seed scripts and
accumulated smoke test runs.

Deletes users (cascade) matching these patterns:

  Pattern                           Origin
  ──────────────────────────────────────────────────────────────────────────────
  boot-*@lfa.com                    Old bootstrap_clean.py format (pre-2026-03)
  ir-player-*@lfa-seed.com          scripts/seed_ir_team_examples.py
  sdemo-*@skill-delta.local         scripts/seed_skill_delta_demo.py
  smoke.*@example.com               Smoke test module-scoped fixtures (fixed email)
  smoke-idem-*@test.example.com     Idempotency smoke tests
  smoke-*@test.example.com          Smoke test generated users
  smoke.student*@example.com        Smoke test student variants
  smoke.admin*@example.com          Smoke test admin variants
  smoke.instructor*@example.com     Smoke test instructor variants
  *@lfa-regr.test                   Regression test leaked users
  *@lfa-seed.com                    All seed-domain users
  junior.intern@lfa.com             Manual test user (known artifact)

Safe (NOT deleted):
  *@lfa.com                         Bootstrap real users (admin@, instructor@,
                                    lfa-{age}-*@lfa.com, lfa-instr-*@lfa.com)
  demo-*@lfa.com                    Comprehensive demo seed players
  OPS-SMOKE-*                       These are semesters, not users

Usage:
    # Dry-run (default) — shows what would be deleted
    PYTHONPATH=. python scripts/cleanup_legacy_users.py

    # Execute deletion
    PYTHONPATH=. python scripts/cleanup_legacy_users.py --confirm

    # Skip confirmation prompt
    PYTHONPATH=. python scripts/cleanup_legacy_users.py --confirm --yes
"""

import argparse
import os
import sys
from collections import defaultdict

# ── Bootstrap ─────────────────────────────────────────────────────────────────

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lfa_intern_system")
os.environ.setdefault("SECRET_KEY", "cleanup-script-not-for-production")
os.environ.setdefault("TESTING", "true")

# ── Target email patterns ──────────────────────────────────────────────────────

_PATTERNS = [
    # Legacy bootstrap format
    "boot-%@lfa.com",
    # IR team seed script
    "ir-player-%@lfa-seed.com",
    # Skill delta demo seed
    "sdemo-%@skill-delta.local",
    # All @lfa-seed.com (catch-all for IR seed + any future seed.com variants)
    "%@lfa-seed.com",
    # Smoke test fixed-email users
    "smoke.admin@example.com",
    "smoke.student@example.com",
    "smoke.instructor@example.com",
    # Smoke test generated users (UUIDs appended)
    "smoke.student2.%@example.com",
    "smoke.student3.%@example.com",
    "smoke.student4.%@example.com",
    # Function-scope smoke test users (new format after SAVEPOINT migration)
    "smoke.admin.%@example.com",
    "smoke.student.%@example.com",
    "smoke.instructor.%@example.com",
    # Idempotency smoke test users
    "smoke-idem-%@test.example.com",
    "smoke-%@test.example.com",
    # Regression test leaked users
    "%@lfa-regr.test",
    # Known manual artifact
    "junior.intern@lfa.com",
]

# Explicit safelist — users matching these are NEVER deleted even if a pattern
# above would match (e.g. wildcards that could be too broad)
_SAFELIST_EXACT = {
    "admin@lfa.com",
    "instructor@lfa.com",
}

_SAFELIST_PREFIXES = (
    "lfa-u",      # lfa-u15-*, lfa-u18-*, lfa-adult-*
    "lfa-instr-", # lfa-instr-1@lfa.com … lfa-instr-4@lfa.com
    "demo-",      # demo-u12-*, demo-u15-* etc. (comprehensive demo players)
)


def _is_safe(email: str) -> bool:
    if email in _SAFELIST_EXACT:
        return True
    local = email.split("@")[0]
    return any(local.startswith(p) for p in _SAFELIST_PREFIXES)


def _build_query(db):
    """Return a SQLAlchemy query that matches all target users."""
    from sqlalchemy import or_
    from app.models.user import User

    conditions = []
    for pattern in _PATTERNS:
        conditions.append(User.email.like(pattern))

    return db.query(User).filter(or_(*conditions))


def _group_by_origin(users) -> dict:
    groups = defaultdict(list)
    for u in users:
        email = u.email
        if "lfa-seed.com" in email or "ir-player-" in email:
            groups["IR / seed scripts"].append(email)
        elif "skill-delta.local" in email:
            groups["Skill delta demo"].append(email)
        elif email.startswith("boot-") and email.endswith("@lfa.com"):
            groups["Legacy bootstrap"].append(email)
        elif "smoke" in email.split("@")[0]:
            groups["Smoke tests"].append(email)
        elif "@lfa-regr.test" in email:
            groups["Regression tests"].append(email)
        elif email == "junior.intern@lfa.com":
            groups["Manual artifact"].append(email)
        else:
            groups["Other"].append(email)
    return groups


def run(confirm: bool, skip_prompt: bool) -> int:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        query = _build_query(db)
        candidates = query.all()

        # Apply safelist
        to_delete = [u for u in candidates if not _is_safe(u.email)]
        safe_skipped = [u for u in candidates if _is_safe(u.email)]

        if safe_skipped:
            print(f"\n⚠️  Safelist protected {len(safe_skipped)} user(s) — skipped:")
            for u in safe_skipped:
                print(f"   • {u.email}")

        if not to_delete:
            print("\n✅ No legacy artifact users found — nothing to delete.")
            return 0

        groups = _group_by_origin(to_delete)
        print(f"\n{'DRY-RUN: ' if not confirm else ''}Found {len(to_delete)} legacy artifact user(s):\n")
        for group, emails in sorted(groups.items()):
            print(f"  [{group}] — {len(emails)} user(s)")
            for email in sorted(emails)[:5]:
                print(f"    • {email}")
            if len(emails) > 5:
                print(f"    … and {len(emails) - 5} more")

        if not confirm:
            print(
                "\n  Run with --confirm to execute deletion.\n"
                "  Run with --confirm --yes to skip the prompt.\n"
            )
            return 0

        if not skip_prompt:
            answer = input(f"\n⚠️  Delete {len(to_delete)} users (and all cascade data)? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                return 0

        # Delete using raw SQL with DELETE CASCADE to avoid ORM SET-NULL issues
        # (some FK relationships are NOT NULL, so ORM cascade would fail)
        from sqlalchemy import text
        user_ids = [u.id for u in to_delete]
        db.execute(
            text("DELETE FROM users WHERE id = ANY(:ids)"),
            {"ids": user_ids},
        )
        db.commit()
        print(f"\n✅ Deleted {len(user_ids)} legacy artifact user(s) (PostgreSQL CASCADE applied).")
        return 0

    except Exception as exc:
        db.rollback()
        print(f"\n❌ Error during cleanup: {exc}", file=sys.stderr)
        import traceback; traceback.print_exc()
        return 1
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Remove legacy artifact users from the development database."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Execute deletion (default: dry-run only)",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip interactive confirmation prompt (requires --confirm)",
    )
    args = parser.parse_args()

    sys.exit(run(confirm=args.confirm, skip_prompt=args.yes))


if __name__ == "__main__":
    main()
