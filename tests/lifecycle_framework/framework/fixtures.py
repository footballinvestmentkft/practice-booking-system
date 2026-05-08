"""Bootstrap fixture resolution — instructor, campus, seed players.

Requires PYTHONPATH=. (app imports must resolve).
Player resolution uses direct DB query — the users list API raises Pydantic
validation errors on .test-TLD emails created by other seed scripts.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

from ._http import PreflightError, require_ok


@dataclass
class InstructorFixture:
    id: int
    email: str


@dataclass
class CampusFixture:
    id: int
    name: str


@dataclass
class PlayerFixture:
    id: int
    email: str


def resolve_instructor(
    base_url: str,
    admin_token: str,
    email: str,
) -> InstructorFixture:
    """Find instructor by email via the admin users endpoint."""
    resp = requests.get(
        f"{base_url}/api/v1/users/?role=instructor&size=100",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    data = require_ok(resp, "resolve_instructor")
    for u in data.get("users", []):
        if u.get("email") == email:
            return InstructorFixture(id=u["id"], email=email)
    raise PreflightError(
        "resolve_instructor",
        f"{email} not found — run: PYTHONPATH=. python scripts/bootstrap_clean.py",
    )


def resolve_campus(
    base_url: str,
    admin_token: str,
) -> CampusFixture:
    """Return the first active campus from the admin campuses endpoint."""
    resp = requests.get(
        f"{base_url}/api/v1/admin/campuses",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    data = require_ok(resp, "resolve_campus")
    items: list[dict] = data if isinstance(data, list) else data.get("campuses", [])
    active = [c for c in items if c.get("is_active", True)]
    if not active:
        raise PreflightError(
            "resolve_campus",
            "No active campus — run: PYTHONPATH=. python scripts/bootstrap_clean.py",
        )
    c = active[0]
    return CampusFixture(id=c["id"], name=c.get("name", ""))


def resolve_players_db(
    email_pattern: str = "lfa-adult-%@lfa.com",
    count: int = 4,
) -> list[PlayerFixture]:
    """Resolve seed players via direct DB query.

    Returns PlayerFixture list (id + email). Token acquisition is the caller's
    responsibility — call login(base_url, player.email, password) per player.

    Direct DB is required because the users list API rejects .test-TLD emails
    created by other seed scripts (Pydantic EmailStr validation).
    """
    from app.database import SessionLocal
    from app.models.user import User as UserModel, UserRole

    db = SessionLocal()
    try:
        rows = (
            db.query(UserModel)
            .filter(
                UserModel.role == UserRole.STUDENT,
                UserModel.email.like(email_pattern),
                UserModel.is_active == True,  # noqa: E712
            )
            .limit(count)
            .all()
        )
    finally:
        db.close()

    if len(rows) < count:
        raise PreflightError(
            "resolve_players_db",
            f"Need {count} seed players matching '{email_pattern}', found {len(rows)} — "
            "run: PYTHONPATH=. python scripts/bootstrap_clean.py",
        )

    return [PlayerFixture(id=u.id, email=u.email) for u in rows]
