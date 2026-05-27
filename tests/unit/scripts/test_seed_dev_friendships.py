"""Unit tests for scripts/seed_dev_friendships.py

SDF-01  _DEV_PAIRS is non-empty
SDF-02  _DEV_PAIRS contains no duplicate pairs
SDF-03  All _DEV_PAIRS emails follow lfa-adult-*.@lfa.com pattern
SDF-04  DB: seed creates ACCEPTED friendships for existing user pairs
SDF-05  DB: seed is idempotent — second call produces same count
SDF-06  DB: seed skips gracefully when a user is not found
SDF-07  DB: seed upgrades PENDING → ACCEPTED
SDF-08  DB: seed replaces DECLINED with ACCEPTED
SDF-09  summary dict keys: created, upgraded, skipped
"""
from __future__ import annotations

from unittest.mock import MagicMock


# ── Data-definition tests (no DB) ────────────────────────────────────────────

class TestDevPairsDefinition:

    def setup_method(self):
        from scripts.seed_dev_friendships import _DEV_PAIRS
        self._pairs = _DEV_PAIRS

    def test_sdf01_pairs_not_empty(self):
        """SDF-01: _DEV_PAIRS is not empty."""
        assert len(self._pairs) > 0

    def test_sdf02_no_duplicate_pairs(self):
        """SDF-02: No duplicate (a, b) pairs."""
        normalised = {tuple(sorted(pair)) for pair in self._pairs}
        assert len(normalised) == len(self._pairs)

    def test_sdf03_all_emails_bootstrap_pattern(self):
        """SDF-03: All emails follow lfa-adult-*@lfa.com (bootstrap-guaranteed)."""
        for email_a, email_b in self._pairs:
            assert email_a.startswith("lfa-adult-") and email_a.endswith("@lfa.com"), \
                f"Email {email_a!r} is not a bootstrap adult player"
            assert email_b.startswith("lfa-adult-") and email_b.endswith("@lfa.com"), \
                f"Email {email_b!r} is not a bootstrap adult player"


# ── DB logic tests ────────────────────────────────────────────────────────────
# Use test-specific emails to avoid clashing with bootstrap data already in the real DB.

_TEST_EMAIL_A = "test-frd-alpha@unit-test.lfa"
_TEST_EMAIL_B = "test-frd-beta@unit-test.lfa"
_TEST_PAIRS   = [(_TEST_EMAIL_A, _TEST_EMAIL_B)]


def _wrap_no_close(test_db):
    """Wrap test_db so .close() is a no-op (preserves SAVEPOINT isolation)."""
    wrapper = MagicMock(wraps=test_db)
    wrapper.close = MagicMock()
    return wrapper


def _create_user(test_db, email: str):
    from app.models.user import User, UserRole
    from app.core.security import get_password_hash
    u = User(
        name=email,
        email=email,
        password_hash=get_password_hash("Test#123"),
        role=UserRole.STUDENT,
        is_active=True,
        onboarding_completed=True,
    )
    test_db.add(u)
    test_db.flush()
    return u


def _run_seed(test_db, pairs=None):
    patch_pairs = pairs if pairs is not None else _TEST_PAIRS
    patch = __import__("unittest.mock", fromlist=["patch"]).patch
    with patch("scripts.seed_dev_friendships.SessionLocal", return_value=_wrap_no_close(test_db)), \
         patch("scripts.seed_dev_friendships._DEV_PAIRS", patch_pairs):
        from scripts.seed_dev_friendships import seed_dev_friendships
        return seed_dev_friendships()


class TestSeedDevFriendshipsDB:

    def test_sdf04_creates_accepted_friendships(self, test_db):
        """SDF-04: Seed creates ACCEPTED friendships for known user pairs."""
        from app.models.friendship import Friendship, FriendshipStatus

        _create_user(test_db, _TEST_EMAIL_A)
        _create_user(test_db, _TEST_EMAIL_B)

        result = _run_seed(test_db)

        accepted = test_db.query(Friendship).filter(
            Friendship.status == FriendshipStatus.ACCEPTED
        ).all()
        assert len(accepted) >= 1
        assert result["created"] >= 1

    def test_sdf05_idempotent_second_call(self, test_db):
        """SDF-05: Second call produces same friendship count — no duplicates."""
        from app.models.friendship import Friendship

        _create_user(test_db, _TEST_EMAIL_A)
        _create_user(test_db, _TEST_EMAIL_B)

        _run_seed(test_db)
        count_after_first = test_db.query(Friendship).count()

        result2 = _run_seed(test_db)
        count_after_second = test_db.query(Friendship).count()

        assert count_after_first == count_after_second
        assert result2["created"] == 0
        assert result2["skipped"] >= 1

    def test_sdf06_skips_missing_user(self, test_db):
        """SDF-06: Missing user → pair skipped without error (users not in DB)."""
        # _TEST_PAIRS users are NOT created → all pairs skipped
        result = _run_seed(test_db)

        assert result["created"] == 0
        assert result["skipped"] >= 1

    def test_sdf07_upgrades_pending_to_accepted(self, test_db):
        """SDF-07: Existing PENDING friendship → upgraded to ACCEPTED."""
        from app.models.friendship import Friendship, FriendshipStatus

        user_a = _create_user(test_db, _TEST_EMAIL_A)
        user_b = _create_user(test_db, _TEST_EMAIL_B)

        test_db.add(Friendship(
            requester_id=user_a.id,
            addressee_id=user_b.id,
            status=FriendshipStatus.PENDING,
        ))
        test_db.flush()

        result = _run_seed(test_db)

        assert result["upgraded"] >= 1
        row = test_db.query(Friendship).filter_by(
            requester_id=user_a.id, addressee_id=user_b.id
        ).first()
        assert row.status == FriendshipStatus.ACCEPTED

    def test_sdf08_replaces_declined_with_accepted(self, test_db):
        """SDF-08: Existing DECLINED friendship → replaced with ACCEPTED."""
        from app.models.friendship import Friendship, FriendshipStatus

        user_a = _create_user(test_db, _TEST_EMAIL_A)
        user_b = _create_user(test_db, _TEST_EMAIL_B)

        test_db.add(Friendship(
            requester_id=user_a.id,
            addressee_id=user_b.id,
            status=FriendshipStatus.DECLINED,
        ))
        test_db.flush()

        result = _run_seed(test_db)

        assert result["upgraded"] >= 1
        row = test_db.query(Friendship).filter_by(
            requester_id=user_a.id, addressee_id=user_b.id
        ).first()
        assert row.status == FriendshipStatus.ACCEPTED

    def test_sdf09_summary_keys_present(self, test_db):
        """SDF-09: Return dict contains created, upgraded, skipped."""
        result = _run_seed(test_db)
        assert "created" in result
        assert "upgraded" in result
        assert "skipped" in result
