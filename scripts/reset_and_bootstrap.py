"""
Destructive DB Reset + Bootstrap
=================================
WARNING: Deletes ALL data. Use only on dev/test databases.

Steps:
  1. Drop all tables via alembic downgrade base
  2. Re-apply all migrations (alembic upgrade head)
  3. Seed reference data + bootstrap club via bootstrap_clean.py logic

Usage:
    DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lfa_intern_system" \\
        PYTHONPATH=. python scripts/reset_and_bootstrap.py

    # Skip confirmation prompt (for CI / scripts):
    PYTHONPATH=. python scripts/reset_and_bootstrap.py --yes
"""
import os
import sys
import subprocess
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/lfa_intern_system",
)

DATABASE_URL = os.environ["DATABASE_URL"]


def _run(cmd: list[str], desc: str) -> None:
    print(f"\n▶ {desc}")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"❌ FAILED: {desc}")
        sys.exit(1)
    print(f"✅ {desc}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    args = parser.parse_args()

    db_name = DATABASE_URL.split("/")[-1]

    print("=" * 60)
    print("  ⚠️  DESTRUCTIVE DB RESET")
    print("=" * 60)
    print(f"  Database : {db_name}")
    print(f"  URL      : {DATABASE_URL[:DATABASE_URL.rfind('/') + 1]}***")
    print()
    print("  This will DELETE ALL DATA in the database.")
    print("  Only run on dev / test databases.")
    print("=" * 60)

    if not args.yes:
        answer = input("\nType 'yes' to continue: ").strip().lower()
        if answer != "yes":
            print("Aborted.")
            sys.exit(0)

    print("\n--- Step 1: Drop all tables (DROP SCHEMA CASCADE) ---")
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP SCHEMA public CASCADE;")
    cur.execute("CREATE SCHEMA public;")
    cur.execute("GRANT ALL ON SCHEMA public TO PUBLIC;")
    cur.close()
    conn.close()
    print("✅ Schema dropped and recreated")

    print("\n--- Step 2: Re-apply all migrations ---")
    _run(
        ["alembic", "upgrade", "head"],
        "alembic upgrade head (recreate schema)",
    )

    print("\n--- Step 3: Bootstrap reference data ---")
    # Import and run bootstrap logic inline (avoids subprocess env issues)
    from scripts.bootstrap_clean import run as bootstrap_run  # noqa: E402
    bootstrap_run()

    print("\n--- Step 4: Post-reset VT reference data validation ---")
    from app.database import SessionLocal as _SL       # noqa: E402
    from app.models.virtual_training import VirtualTrainingGame as _VTG  # noqa: E402
    _vdb = _SL()
    try:
        _vt_total  = _vdb.query(_VTG).count()
        _vt_compat = _vdb.query(_VTG).filter(
            _VTG.code.in_(["memory_sequence", "target_tracking"]),
            _VTG.is_active == True,  # noqa: E712
        ).count()
    finally:
        _vdb.close()

    if _vt_total == 0:
        print("❌ FATAL: virtual_training_games is EMPTY after bootstrap.")
        print("   bootstrap_clean.py Step 8 did not complete correctly.")
        print("   Fix: PYTHONPATH=. python scripts/seed_virtual_training_games.py")
        sys.exit(1)

    print(f"✅ VirtualTrainingGame: {_vt_total} rows ({_vt_compat}/2 challenge-compatible active)")

    print("\n" + "=" * 60)
    print("  ✅  Reset complete. DB is in a clean bootstrapped state.")
    print("=" * 60)


if __name__ == "__main__":
    main()
