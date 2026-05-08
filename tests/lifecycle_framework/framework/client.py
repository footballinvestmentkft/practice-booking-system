"""Thin HTTP client factory."""
from __future__ import annotations

import requests


def make_client(base_url: str, token: str) -> requests.Session:
    """Return a requests.Session pre-configured with bearer auth."""
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}"})
    s.base_url = base_url  # type: ignore[attr-defined]
    return s


def get(session: requests.Session, path: str, **kwargs) -> requests.Response:
    return session.get(f"{session.base_url}{path}", **kwargs)  # type: ignore[attr-defined]


def post(session: requests.Session, path: str, **kwargs) -> requests.Response:
    return session.post(f"{session.base_url}{path}", **kwargs)  # type: ignore[attr-defined]


def patch(session: requests.Session, path: str, **kwargs) -> requests.Response:
    return session.patch(f"{session.base_url}{path}", **kwargs)  # type: ignore[attr-defined]
