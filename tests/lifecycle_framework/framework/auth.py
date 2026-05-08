"""Authentication context and login helper."""
from __future__ import annotations

from dataclasses import dataclass, field

import requests

from ._http import require_ok


@dataclass
class AuthContext:
    """Holds tokens and IDs for all principals in a scenario run."""

    admin_token: str
    instructor_token: str
    instructor_id: int
    # email -> token
    player_tokens: dict[str, str] = field(default_factory=dict)
    # email -> id
    player_ids: dict[str, int] = field(default_factory=dict)


def login(base_url: str, email: str, password: str) -> str:
    """POST /api/v1/auth/login and return the access token."""
    resp = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    data = require_ok(resp, f"login:{email}")
    token = data.get("access_token") or data.get("token")
    if not token:
        raise ValueError(f"No token in login response for {email}: {data}")
    return token
