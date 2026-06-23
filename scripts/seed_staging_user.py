"""Idempotent staging test user seed.

Usage:
  DATABASE_URL=postgresql://... STAGING_USER_PASSWORD=... python scripts/seed_staging_user.py

Creates a single staging test user if it does not already exist.
The password is read from STAGING_USER_PASSWORD — never hardcoded.
"""

import os
import sys

import bcrypt
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")
PASSWORD = os.environ.get("STAGING_USER_PASSWORD")

if not DATABASE_URL or not PASSWORD:
    print("ERROR: Set DATABASE_URL and STAGING_USER_PASSWORD environment variables.")
    sys.exit(1)

EMAIL = "staging-smoke@lfa-test.local"
NAME = "Staging Smoke User"

engine = create_engine(DATABASE_URL)
hashed = bcrypt.hashpw(PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

with engine.begin() as conn:
    existing = conn.execute(text("SELECT id FROM users WHERE email = :email"), {"email": EMAIL}).fetchone()
    if existing:
        conn.execute(
            text("UPDATE users SET password_hash = :h WHERE email = :email"),
            {"h": hashed, "email": EMAIL},
        )
        print(f"Updated password for existing staging user: {EMAIL}")
    else:
        conn.execute(
            text(
                "INSERT INTO users (name, email, password_hash, role, is_active) "
                "VALUES (:name, :email, :h, 'INSTRUCTOR', true)"
            ),
            {"name": NAME, "email": EMAIL, "h": hashed},
        )
        print(f"Created staging user: {EMAIL}")
