"""
Unit tests for app/api/api_v1/endpoints/admin_players.py

Coverage targets (helper functions — no HTTP, no real DB):
  - _check_rate_limit(): soft in-process rate guard (HTTPException 429)
  - _resolve_existing(): duplicate email detection (HTTPException 409)
  - PlayerCreateEntry: Pydantic schema validation (email, password, name)
  - BatchCreatePlayersRequest: min/max list length

These helpers hold non-trivial logic that the smoke test alone cannot exercise
(rate window pruning, per-admin isolation, skip_existing=False path, etc.).
"""

import time
import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException

# Import module-level rate state so we can reset it between tests
from app.api.api_v1.endpoints.admin_players import (
    _check_rate_limit,
    _resolve_existing,
    _rate_calls,
    MAX_CALLS_PER_WINDOW,
    PlayerCreateEntry,
    BatchCreatePlayersRequest,
)


# ── Fixture: reset rate state between tests ───────────────────────────────────

@pytest.fixture(autouse=True)
def reset_rate_state():
    """Clear in-process rate limit dict before each test."""
    _rate_calls.clear()
    yield
    _rate_calls.clear()


# ── _check_rate_limit ─────────────────────────────────────────────────────────

class TestCheckRateLimit:

    def test_first_call_allowed(self):
        """First call for a user is always allowed."""
        _check_rate_limit(user_id=42, incoming_count=10)  # should not raise

    def test_calls_up_to_max_allowed(self):
        """Up to MAX_CALLS_PER_WINDOW calls are allowed."""
        for _ in range(MAX_CALLS_PER_WINDOW):
            _check_rate_limit(user_id=42, incoming_count=1)

    def test_exceeding_max_raises_429(self):
        """MAX_CALLS_PER_WINDOW + 1 raises 429."""
        for _ in range(MAX_CALLS_PER_WINDOW):
            _check_rate_limit(user_id=42, incoming_count=1)
        with pytest.raises(HTTPException) as exc:
            _check_rate_limit(user_id=42, incoming_count=1)
        assert exc.value.status_code == 429

    def test_429_detail_mentions_rate_limit(self):
        for _ in range(MAX_CALLS_PER_WINDOW):
            _check_rate_limit(user_id=42, incoming_count=1)
        with pytest.raises(HTTPException) as exc:
            _check_rate_limit(user_id=42, incoming_count=1)
        assert "Rate limit exceeded" in exc.value.detail

    def test_429_includes_retry_after_header(self):
        for _ in range(MAX_CALLS_PER_WINDOW):
            _check_rate_limit(user_id=42, incoming_count=1)
        with pytest.raises(HTTPException) as exc:
            _check_rate_limit(user_id=42, incoming_count=1)
        assert "Retry-After" in exc.value.headers

    def test_rate_limit_per_user_isolated(self):
        """Different user_ids have independent rate windows."""
        for _ in range(MAX_CALLS_PER_WINDOW):
            _check_rate_limit(user_id=42, incoming_count=1)
        # user 99 is unaffected
        _check_rate_limit(user_id=99, incoming_count=1)  # should not raise

    def test_old_calls_pruned_from_window(self):
        """Calls outside the rolling window are pruned and don't count."""
        import app.api.api_v1.endpoints.admin_players as mod
        # Inject stale timestamps directly into _rate_calls
        stale_ts = time.monotonic() - (mod.RATE_WINDOW_SECONDS + 5)
        _rate_calls[42] = [(stale_ts, 1)] * MAX_CALLS_PER_WINDOW
        # All entries are outside window → fresh call should be allowed
        _check_rate_limit(user_id=42, incoming_count=1)  # should not raise


# ── _resolve_existing ─────────────────────────────────────────────────────────

class TestResolveExisting:

    def _db_with_rows(self, rows):
        """Create a mock DB that returns `rows` from the email query chain."""
        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = rows
        db = MagicMock()
        db.query.return_value = q
        return db

    def test_no_existing_emails_returns_empty_dict(self):
        db = self._db_with_rows([])
        result = _resolve_existing(db, ["new@example.com"], skip_existing=True)
        assert result == {}

    def test_existing_email_returned_in_dict(self):
        row = MagicMock()
        row.email = "existing@example.com"
        row.id = 99
        db = self._db_with_rows([row])
        result = _resolve_existing(db, ["existing@example.com"], skip_existing=True)
        assert result == {"existing@example.com": 99}

    def test_skip_existing_false_raises_409_on_duplicate(self):
        row = MagicMock()
        row.email = "dup@example.com"
        row.id = 7
        db = self._db_with_rows([row])
        with pytest.raises(HTTPException) as exc:
            _resolve_existing(db, ["dup@example.com"], skip_existing=False)
        assert exc.value.status_code == 409

    def test_skip_existing_true_no_raise_on_duplicate(self):
        row = MagicMock()
        row.email = "dup@example.com"
        row.id = 7
        db = self._db_with_rows([row])
        result = _resolve_existing(db, ["dup@example.com"], skip_existing=True)
        assert "dup@example.com" in result

    def test_409_detail_includes_duplicate_email(self):
        row = MagicMock()
        row.email = "dup@example.com"
        row.id = 7
        db = self._db_with_rows([row])
        with pytest.raises(HTTPException) as exc:
            _resolve_existing(db, ["dup@example.com"], skip_existing=False)
        assert "dup@example.com" in exc.value.detail

    def test_multiple_existing_rows(self):
        rows = []
        for i, email in enumerate(["a@example.com", "b@example.com"]):
            r = MagicMock()
            r.email = email
            r.id = i + 1
            rows.append(r)
        db = self._db_with_rows(rows)
        result = _resolve_existing(db, ["a@example.com", "b@example.com"], skip_existing=True)
        assert len(result) == 2
        assert result["a@example.com"] == 1
        assert result["b@example.com"] == 2


# ── PlayerCreateEntry schema validation ───────────────────────────────────────

class TestPlayerCreateEntrySchema:

    def test_valid_entry(self):
        entry = PlayerCreateEntry(
            email="test@example.com",
            password="secure123",
            name="Jane Doe",
            date_of_birth="2000-06-15",
        )
        assert entry.email == "test@example.com"

    def test_email_lowercased_and_stripped(self):
        entry = PlayerCreateEntry(
            email="  TEST@EXAMPLE.COM  ",
            password="secure123",
            name="Jane Doe",
            date_of_birth="2000-06-15",
        )
        assert entry.email == "test@example.com"

    def test_invalid_email_no_at(self):
        with pytest.raises(Exception):  # Pydantic ValidationError
            PlayerCreateEntry(email="notanemail", password="secure123", name="Jane Doe", date_of_birth="2000-06-15")

    def test_invalid_email_no_dot_after_at(self):
        with pytest.raises(Exception):
            PlayerCreateEntry(email="test@nodot", password="secure123", name="Jane Doe", date_of_birth="2000-06-15")

    def test_password_min_length_enforced(self):
        with pytest.raises(Exception):
            PlayerCreateEntry(email="test@example.com", password="abc", name="Jane Doe", date_of_birth="2000-06-15")

    def test_name_required(self):
        with pytest.raises(Exception):
            PlayerCreateEntry(email="test@example.com", password="secure123", name="", date_of_birth="2000-06-15")

    def test_dob_is_required(self):
        with pytest.raises(Exception):
            PlayerCreateEntry(
                email="test@example.com",
                password="secure123",
                name="Jane Doe",
            )

    def test_custom_dob(self):
        entry = PlayerCreateEntry(
            email="test@example.com",
            password="secure123",
            name="Jane Doe",
            date_of_birth="1995-03-15",
        )
        assert entry.date_of_birth == "1995-03-15"


# ── BatchCreatePlayersRequest schema validation ───────────────────────────────

class TestBatchCreatePlayersRequestSchema:

    def _entry(self, n: int = 0) -> dict:
        return {
            "email": f"player{n}@example.com",
            "password": "secure123",
            "name": f"Player {n}",
            "date_of_birth": "2000-06-15",
        }

    def test_single_player_valid(self):
        req = BatchCreatePlayersRequest(players=[self._entry(0)])
        assert len(req.players) == 1

    def test_empty_list_raises(self):
        with pytest.raises(Exception):
            BatchCreatePlayersRequest(players=[])

    def test_default_specialization(self):
        req = BatchCreatePlayersRequest(players=[self._entry(0)])
        assert req.specialization == "LFA_FOOTBALL_PLAYER"

    def test_custom_specialization(self):
        req = BatchCreatePlayersRequest(
            players=[self._entry(0)],
            specialization="GANCUJU",
        )
        assert req.specialization == "GANCUJU"

    def test_skip_existing_default_true(self):
        req = BatchCreatePlayersRequest(players=[self._entry(0)])
        assert req.skip_existing is True

    def test_skip_existing_false(self):
        req = BatchCreatePlayersRequest(
            players=[self._entry(0)],
            skip_existing=False,
        )
        assert req.skip_existing is False
