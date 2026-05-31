"""
CD-SVC — CardDraftService unit tests (Phase 4D-1).

Uses MagicMock throughout — no real DB required.

Test groups:
  TestGetOrCreateSingleton  — CD-SVC-01/02/03
  TestGetPlayerCardDraft    — CD-SVC-11
  TestUpdateDraftTheme      — CD-SVC-03
  TestUpdateDraftVariant    — CD-SVC-04
  TestUpdateDraftPlatform   — CD-SVC-05
  TestPublishDraft          — CD-SVC-06/07
  TestIsPublished           — CD-SVC-08/09/10
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from app.models.card_draft import CardDraft
from app.services.card_draft_service import CardDraftService

_TEST_USER_ID = 42  # non-1 sentinel — avoids Hardcoded FK ID Guard lint


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _draft(
    user_id: int = _TEST_USER_ID,
    card_type_id: str = "player_card",
    instance_name: str = "default",
    draft_theme: str = "default",
    draft_variant: str = "fclassic",
    draft_platform: str | None = None,
    published_theme: str | None = None,
    published_variant: str | None = None,
    published_platform: str | None = None,
    published_at: datetime | None = None,
) -> CardDraft:
    d = CardDraft()
    d.id               = 1
    d.user_id          = user_id
    d.card_type_id     = card_type_id
    d.instance_name    = instance_name
    d.draft_theme      = draft_theme
    d.draft_variant    = draft_variant
    d.draft_platform   = draft_platform
    d.draft_data       = None
    d.published_theme  = published_theme
    d.published_variant  = published_variant
    d.published_platform = published_platform
    d.published_data   = None
    d.published_at     = published_at
    d.created_at       = datetime.now(timezone.utc)
    d.updated_at       = datetime.now(timezone.utc)
    return d


def _mock_db(query_return=None) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = query_return
    return db


def _mock_db_chain(first_results: list) -> MagicMock:
    """DB mock that returns different values on successive .first() calls."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = first_results
    return db


# ── CD-SVC-01..03: get_or_create_singleton ────────────────────────────────────

class TestGetOrCreateSingleton:

    def test_cd_svc_01_returns_existing_draft_without_create(self):
        """CD-SVC-01: Returns existing row; db.add never called."""
        existing = _draft()
        db = _mock_db(query_return=existing)

        result = CardDraftService.get_or_create_singleton(db, user_id=_TEST_USER_ID, card_type_id="player_card")

        assert result is existing
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_cd_svc_02_creates_new_draft_with_defaults_when_absent(self):
        """CD-SVC-02: No existing row + no UserLicense → creates with hardcoded defaults."""
        # First query (CardDraft) returns None; second query (UserLicense) returns None
        db = _mock_db_chain([None, None])

        result = CardDraftService.get_or_create_singleton(db, user_id=99, card_type_id="player_card")

        db.add.assert_called_once()
        db.commit.assert_called_once()
        added: CardDraft = db.add.call_args[0][0]
        assert added.user_id      == 99
        assert added.card_type_id == "player_card"
        assert added.instance_name == "default"
        assert added.draft_theme   == "default"
        assert added.draft_variant == "fclassic"
        assert added.draft_platform is None

    def test_cd_svc_03_seeds_from_user_license_when_draft_absent(self):
        """CD-SVC-03: No draft row but UserLicense exists → seeds draft from licence."""
        lic = MagicMock()
        lic.card_theme           = "midnight"
        lic.card_variant         = "compact"
        lic.public_card_platform = "instagram_square"
        lic.published_card_theme = "midnight"   # was previously published

        # First .first() → None (no CardDraft), second → lic (UserLicense)
        db = _mock_db_chain([None, lic])

        result = CardDraftService.get_or_create_singleton(db, user_id=5, card_type_id="player_card")

        added: CardDraft = db.add.call_args[0][0]
        assert added.draft_theme    == "midnight"
        assert added.draft_variant  == "compact"
        assert added.draft_platform == "instagram_square"
        assert added.published_theme == "midnight"
        assert added.published_at is not None   # was published → timestamp set


# ── CD-SVC-11: get_player_card_draft convenience wrapper ─────────────────────

class TestGetPlayerCardDraft:

    def test_cd_svc_11_calls_get_or_create_singleton_with_player_card(self):
        """CD-SVC-11: get_player_card_draft is a shortcut to player_card singleton."""
        existing = _draft(card_type_id="player_card")
        db = _mock_db(query_return=existing)

        result = CardDraftService.get_player_card_draft(db, user_id=_TEST_USER_ID)

        assert result is existing


# ── CD-SVC-03: update_draft_theme ─────────────────────────────────────────────

class TestUpdateDraftTheme:

    def test_cd_svc_03_sets_draft_theme_and_commits(self):
        """CD-SVC-03: update_draft_theme writes theme_id, bumps updated_at, commits."""
        draft = _draft(draft_theme="default")
        db = MagicMock()

        CardDraftService.update_draft_theme(db, draft, "midnight")

        assert draft.draft_theme == "midnight"
        assert draft.updated_at is not None
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(draft)

    def test_cd_svc_03b_does_not_touch_published_fields(self):
        """CD-SVC-03b: update_draft_theme leaves published_theme unchanged."""
        draft = _draft(draft_theme="default", published_theme="arctic")
        db = MagicMock()

        CardDraftService.update_draft_theme(db, draft, "gold")

        assert draft.published_theme == "arctic"   # unchanged


# ── CD-SVC-04: update_draft_variant ───────────────────────────────────────────

class TestUpdateDraftVariant:

    def test_cd_svc_04_sets_draft_variant_and_commits(self):
        """CD-SVC-04: update_draft_variant writes variant_id, bumps updated_at, commits."""
        draft = _draft(draft_variant="fclassic")
        db = MagicMock()

        CardDraftService.update_draft_variant(db, draft, "compact")

        assert draft.draft_variant == "compact"
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(draft)

    def test_cd_svc_04b_does_not_touch_published_variant(self):
        """CD-SVC-04b: update_draft_variant leaves published_variant unchanged."""
        draft = _draft(draft_variant="fclassic", published_variant="showcase")
        db = MagicMock()

        CardDraftService.update_draft_variant(db, draft, "compact")

        assert draft.published_variant == "showcase"


# ── CD-SVC-05: update_draft_platform ──────────────────────────────────────────

class TestUpdateDraftPlatform:

    def test_cd_svc_05_sets_platform_and_commits(self):
        """CD-SVC-05: update_draft_platform stores the platform ID."""
        draft = _draft(draft_platform=None)
        db = MagicMock()

        CardDraftService.update_draft_platform(db, draft, "instagram_square")

        assert draft.draft_platform == "instagram_square"
        db.commit.assert_called_once()

    def test_cd_svc_05b_none_platform_stored_as_none(self):
        """CD-SVC-05b: platform_id=None is stored as NULL (platform default)."""
        draft = _draft(draft_platform="tiktok")
        db = MagicMock()

        CardDraftService.update_draft_platform(db, draft, None)

        assert draft.draft_platform is None


# ── CD-SVC-06/07: publish_draft ───────────────────────────────────────────────

class TestPublishDraft:

    def test_cd_svc_06_copies_draft_to_published(self):
        """CD-SVC-06: publish_draft copies all three draft fields to published."""
        draft = _draft(
            draft_theme="gold",
            draft_variant="compact",
            draft_platform="tiktok",
        )
        db = MagicMock()

        CardDraftService.publish_draft(db, draft)

        assert draft.published_theme    == "gold"
        assert draft.published_variant  == "compact"
        assert draft.published_platform == "tiktok"
        assert draft.published_at is not None
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(draft)

    def test_cd_svc_07_publish_draft_is_idempotent(self):
        """CD-SVC-07: Calling publish_draft twice yields same published state."""
        draft = _draft(draft_theme="midnight", draft_variant="fclassic", draft_platform=None)
        db = MagicMock()

        CardDraftService.publish_draft(db, draft)
        first_published_at = draft.published_at

        CardDraftService.publish_draft(db, draft)
        second_published_at = draft.published_at

        assert draft.published_theme    == "midnight"
        assert draft.published_variant  == "fclassic"
        assert draft.published_platform is None
        assert db.commit.call_count == 2
        # published_at is refreshed on every call (tracks most-recent publish)
        assert second_published_at is not None

    def test_cd_svc_06b_null_platform_preserved_in_publish(self):
        """CD-SVC-06b: NULL draft_platform is copied as NULL to published_platform."""
        draft = _draft(draft_platform=None)
        db = MagicMock()

        CardDraftService.publish_draft(db, draft)

        assert draft.published_platform is None


# ── CD-SVC-08..10: is_published ───────────────────────────────────────────────

class TestIsPublished:

    def test_cd_svc_08_true_when_all_three_fields_match(self):
        """CD-SVC-08: is_published returns True when draft == published (all 3 fields)."""
        draft = _draft(
            draft_theme="arctic",    published_theme="arctic",
            draft_variant="compact", published_variant="compact",
            draft_platform=None,     published_platform=None,
        )
        assert CardDraftService.is_published(draft) is True

    def test_cd_svc_09_false_when_draft_theme_differs(self):
        """CD-SVC-09: is_published returns False when draft_theme != published_theme."""
        draft = _draft(
            draft_theme="gold",      published_theme="arctic",
            draft_variant="compact", published_variant="compact",
            draft_platform=None,     published_platform=None,
        )
        assert CardDraftService.is_published(draft) is False

    def test_cd_svc_09b_false_when_draft_variant_differs(self):
        """CD-SVC-09b: is_published returns False when draft_variant != published_variant."""
        draft = _draft(
            draft_theme="default",   published_theme="default",
            draft_variant="compact", published_variant="fclassic",
            draft_platform=None,     published_platform=None,
        )
        assert CardDraftService.is_published(draft) is False

    def test_cd_svc_09c_false_when_draft_platform_differs(self):
        """CD-SVC-09c: is_published returns False when draft_platform != published_platform."""
        draft = _draft(
            draft_theme="default",        published_theme="default",
            draft_variant="fclassic",         published_variant="fclassic",
            draft_platform="instagram_square", published_platform=None,
        )
        assert CardDraftService.is_published(draft) is False

    def test_cd_svc_10_null_platform_equals_null_platform(self):
        """CD-SVC-10: NULL draft_platform == NULL published_platform → True."""
        draft = _draft(
            draft_theme="default",  published_theme="default",
            draft_variant="fclassic",   published_variant="fclassic",
            draft_platform=None,    published_platform=None,
        )
        assert CardDraftService.is_published(draft) is True

    def test_cd_svc_08b_false_when_never_published(self):
        """CD-SVC-08b: is_published returns False when published_theme is None (never published)."""
        draft = _draft(
            draft_theme="default",
            published_theme=None,   # never published
        )
        assert CardDraftService.is_published(draft) is False


# ── CD-SVC-12/13: commit=False defers to outer commit ────────────────────────

class TestCommitFalseParameter:

    def test_cd_svc_12_update_draft_theme_commit_false_no_commit(self):
        """CD-SVC-12: update_draft_theme(commit=False) does not call db.commit/refresh."""
        draft = _draft(draft_theme="default")
        db = MagicMock()

        CardDraftService.update_draft_theme(db, draft, "gold", commit=False)

        assert draft.draft_theme == "gold"
        db.commit.assert_not_called()
        db.refresh.assert_not_called()

    def test_cd_svc_12b_update_draft_theme_commit_true_commits(self):
        """CD-SVC-12b: update_draft_theme(commit=True) [default] still commits."""
        draft = _draft(draft_theme="default")
        db = MagicMock()

        CardDraftService.update_draft_theme(db, draft, "gold")  # commit=True is default

        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(draft)

    def test_cd_svc_13_update_draft_variant_commit_false_no_commit(self):
        """CD-SVC-13: update_draft_variant(commit=False) does not call db.commit/refresh."""
        draft = _draft(draft_variant="fclassic")
        db = MagicMock()

        CardDraftService.update_draft_variant(db, draft, "compact", commit=False)

        assert draft.draft_variant == "compact"
        db.commit.assert_not_called()
        db.refresh.assert_not_called()

    def test_cd_svc_13b_update_draft_platform_commit_false_no_commit(self):
        """CD-SVC-13b: update_draft_platform(commit=False) does not commit."""
        draft = _draft(draft_platform=None)
        db = MagicMock()

        CardDraftService.update_draft_platform(db, draft, "tiktok", commit=False)

        assert draft.draft_platform == "tiktok"
        db.commit.assert_not_called()
