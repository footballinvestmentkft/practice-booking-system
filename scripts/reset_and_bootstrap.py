"""
Destructive DB Reset + Bootstrap
=================================
WARNING: Deletes ALL data. Use only on dev/test databases.

Steps:
  1. Drop all tables (DROP SCHEMA public CASCADE)
  2. Re-apply all migrations (alembic upgrade head)
  3. Seed reference data + bootstrap club (bootstrap_clean.py)
  4. Seed demo players + 88 tournaments (seed_comprehensive_demo.py)  [optional]

Result: clean, reproducible DB with:
  - 52 students (36 bootstrap + 16 demo), 0 legacy artifacts
  - 57 invitation codes (36 + 16 used, 5 stock unused)
  - 88+ tournaments across all formats and lifecycle states

Usage:
    DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lfa_intern_system" \\
        PYTHONPATH=. python scripts/reset_and_bootstrap.py

    # Skip confirmation prompt:
    PYTHONPATH=. python scripts/reset_and_bootstrap.py --yes

    # Bootstrap only (skip demo seed):
    PYTHONPATH=. python scripts/reset_and_bootstrap.py --yes --no-demo
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
    parser.add_argument("--no-demo", action="store_true", help="Skip seed_comprehensive_demo.py (bootstrap only)")
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
        answer = input("\nType 'yes' or 'y' to continue: ").strip().lower()
        if answer not in ("yes", "y"):
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

    if not args.no_demo:
        print("\n--- Step 4: Comprehensive demo seed ---")
        env = os.environ.copy()
        env["PYTHONPATH"] = "."
        result = subprocess.run(
            [sys.executable, "scripts/seed_comprehensive_demo.py"],
            env=env,
        )
        if result.returncode != 0:
            print("❌ FAILED: seed_comprehensive_demo.py")
            sys.exit(1)
        print("✅ seed_comprehensive_demo.py")

    print("\n" + "=" * 60)
    print("  ✅  Reset complete. DB is in a clean bootstrapped state.")
    if not args.no_demo:
        print("  ✅  Demo seed applied (88+ events, 16 demo players).")
    print("=" * 60)


if __name__ == "__main__":
    main()
