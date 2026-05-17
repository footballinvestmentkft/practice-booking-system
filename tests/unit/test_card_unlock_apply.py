"""
Card Unlock+Apply Gate
======================
Validates that unlock_theme / unlock_variant atomically:
  1. Adds the ID to the unlocked list
  2. Stages the draft theme/variant via CardDraftService in the SAME commit
  3. Does NOT double-charge for already-unlocked items
  4. Raises InsufficientCreditsError (via CreditService.deduct) when balance < cost

Phase 4D-2: active theme/variant are now stored in card_drafts, not UserLicense.
The tests mock CardDraftService and verify it is called with the correct arguments.

No DB required — all tests use MagicMock.
"""
import pytest
from unittest.mock import MagicMock, patch

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(uid=1, credits=2000):
    u = MagicMock()
    u.id = uid
    u.credit_balance = credits
    return u


def _make_license(unlocked_themes=None, unlocked_variants=None, user_id=1):
    ul = MagicMock()
    ul.user_id = user_id
    ul.unlocked_card_themes = list(unlocked_themes or [])
    ul.unlocked_card_variants = list(unlocked_variants or [])
    return ul


def _mock_db():
    db = MagicMock()
    db.begin_nested.return_value.__enter__ = MagicMock(return_value=None)
    db.begin_nested.return_value.__exit__ = MagicMock(return_value=False)
    return db


_CDS_THEME   = "app.services.card_theme_service.CardDraftService"
_CDS_VARIANT = "app.services.card_variant_service.CardDraftService"


# ── Theme: unlock + auto-apply ─────────────────────────────────────────────────

class TestUnlockThemeAutoApply:

    def test_unlock_sets_active_theme(self):
        """unlock_theme must call CardDraftService.update_draft_theme with the new ID."""
        db = _mock_db()
        user = _make_user(credits=2000)
        ul = _make_license()
        mock_draft = MagicMock()

        with patch("app.services.card_theme_service.CreditService") as MockCS, \
             patch(_CDS_THEME) as MockCDS:
            MockCS.return_value.deduct.return_value = MagicMock()
            MockCDS.get_player_card_draft.return_value = mock_draft
            from app.services.card_theme_service import unlock_theme
            unlock_theme(db, user, ul, "gold")

        MockCDS.update_draft_theme.assert_called_once_with(db, mock_draft, "gold", commit=False)

    def test_unlock_adds_to_unlocked_list(self):
        """unlock_theme must add theme_id to unlocked_card_themes."""
        db = _mock_db()
        user = _make_user()
        ul = _make_license()

        with patch("app.services.card_theme_service.CreditService") as MockCS, \
             patch(_CDS_THEME) as MockCDS:
            MockCS.return_value.deduct.return_value = MagicMock()
            MockCDS.get_player_card_draft.return_value = MagicMock()
            from app.services.card_theme_service import unlock_theme
            unlock_theme(db, user, ul, "emerald")

        assert "emerald" in ul.unlocked_card_themes

    def test_unlock_and_apply_in_single_commit(self):
        """Unlock list + draft theme must land in ONE outer db.commit()."""
        db = _mock_db()
        user = _make_user()
        ul = _make_license()
        mock_draft = MagicMock()

        captured = {}

        def check_state_at_commit():
            # At commit time: unlock list must already be updated
            captured["unlocked"] = list(ul.unlocked_card_themes)
            # update_draft_theme(commit=False) must have been called
            captured["draft_theme_staged"] = MockCDS.update_draft_theme.called

        with patch("app.services.card_theme_service.CreditService") as MockCS, \
             patch(_CDS_THEME) as MockCDS:
            MockCS.return_value.deduct.return_value = MagicMock()
            MockCDS.get_player_card_draft.return_value = mock_draft
            db.commit.side_effect = check_state_at_commit
            from app.services.card_theme_service import unlock_theme
            unlock_theme(db, user, ul, "crimson")

        assert "crimson" in captured["unlocked"]
        assert captured["draft_theme_staged"] is True, (
            "update_draft_theme must be called before the outer db.commit()."
        )
        assert db.commit.call_count == 1, "Must commit exactly once for unlock+apply."

    def test_free_theme_not_charged(self):
        """Free themes (e.g. midnight) skip CreditService entirely."""
        db = _mock_db()
        user = _make_user()
        ul = _make_license()

        with patch("app.services.card_theme_service.CreditService") as MockCS, \
             patch(_CDS_THEME) as MockCDS:
            MockCDS.get_player_card_draft.return_value = MagicMock()
            from app.services.card_theme_service import unlock_theme
            unlock_theme(db, user, ul, "midnight")
            MockCS.return_value.deduct.assert_not_called()

    def test_already_unlocked_is_idempotent(self):
        """Unlocking an already-unlocked premium theme must not charge or re-commit."""
        db = _mock_db()
        user = _make_user()
        ul = _make_license(unlocked_themes=["gold"])

        with patch("app.services.card_theme_service.CreditService") as MockCS, \
             patch(_CDS_THEME):
            from app.services.card_theme_service import unlock_theme
            unlock_theme(db, user, ul, "gold")
            MockCS.return_value.deduct.assert_not_called()

        db.commit.assert_not_called()

    def test_unknown_theme_raises(self):
        db = _mock_db()
        from app.services.card_theme_service import unlock_theme
        with pytest.raises(ValueError, match="Unknown or inactive theme"):
            unlock_theme(db, _make_user(), _make_license(), "nonexistent")


# ── Variant: unlock + auto-apply ───────────────────────────────────────────────

import app.services.card_variant_service as _cvsvc


class TestUnlockVariantAutoApply:

    def test_unlock_sets_active_variant(self):
        """unlock_variant must call CardDraftService.update_draft_variant with the new ID."""
        db = _mock_db()
        user = _make_user(credits=2000)
        ul = _make_license()
        mock_draft = MagicMock()

        with patch("app.services.card_variant_service.CreditService") as MockCS, \
             patch(_CDS_VARIANT) as MockCDS:
            MockCS.return_value.deduct.return_value = MagicMock()
            MockCDS.get_player_card_draft.return_value = mock_draft
            _cvsvc.unlock_variant(db, user, ul, "compact")

        MockCDS.update_draft_variant.assert_called_once_with(db, mock_draft, "compact", commit=False)

    def test_unlock_adds_to_unlocked_list(self):
        db = _mock_db()
        user = _make_user()
        ul = _make_license()

        with patch("app.services.card_variant_service.CreditService") as MockCS, \
             patch(_CDS_VARIANT) as MockCDS:
            MockCS.return_value.deduct.return_value = MagicMock()
            MockCDS.get_player_card_draft.return_value = MagicMock()
            _cvsvc.unlock_variant(db, user, ul, "showcase")

        assert "showcase" in ul.unlocked_card_variants

    def test_unlock_and_apply_in_single_commit(self):
        """Unlock list + draft variant must land in ONE outer db.commit()."""
        db = _mock_db()
        user = _make_user()
        ul = _make_license()
        mock_draft = MagicMock()

        captured = {}

        def check_at_commit():
            captured["unlocked"] = list(ul.unlocked_card_variants)
            captured["draft_variant_staged"] = MockCDS.update_draft_variant.called

        with patch("app.services.card_variant_service.CreditService") as MockCS, \
             patch(_CDS_VARIANT) as MockCDS:
            MockCS.return_value.deduct.return_value = MagicMock()
            MockCDS.get_player_card_draft.return_value = mock_draft
            db.commit.side_effect = check_at_commit
            _cvsvc.unlock_variant(db, user, ul, "compact")

        assert "compact" in captured["unlocked"]
        assert captured["draft_variant_staged"] is True
        assert db.commit.call_count == 1

    def test_free_variant_not_charged(self):
        db = _mock_db()
        user = _make_user()
        ul = _make_license()

        with patch("app.services.card_variant_service.CreditService") as MockCS, \
             patch(_CDS_VARIANT) as MockCDS:
            MockCDS.get_player_card_draft.return_value = MagicMock()
            _cvsvc.unlock_variant(db, user, ul, "fifa")
            MockCS.return_value.deduct.assert_not_called()

    def test_already_unlocked_is_idempotent(self):
        db = _mock_db()
        user = _make_user()
        ul = _make_license(unlocked_variants=["compact"])

        with patch("app.services.card_variant_service.CreditService") as MockCS, \
             patch(_CDS_VARIANT):
            _cvsvc.unlock_variant(db, user, ul, "compact")
            MockCS.return_value.deduct.assert_not_called()

        db.commit.assert_not_called()

    def test_unknown_variant_raises(self):
        db = _mock_db()
        with pytest.raises(ValueError, match="Unknown variant"):
            _cvsvc.unlock_variant(db, _make_user(), _make_license(), "nonexistent")


# ── apply_theme / apply_variant: standalone ────────────────────────────────────

class TestApplyStandalone:

    def test_apply_theme_when_unlocked(self):
        """apply_theme calls CardDraftService.update_draft_theme (not UserLicense)."""
        db = _mock_db()
        ul = _make_license(unlocked_themes=["emerald"])
        mock_draft = MagicMock()

        with patch(_CDS_THEME) as MockCDS:
            MockCDS.get_player_card_draft.return_value = mock_draft
            from app.services.card_theme_service import apply_theme
            apply_theme(db, ul, "emerald")

        MockCDS.update_draft_theme.assert_called_once_with(db, mock_draft, "emerald")

    def test_apply_theme_free(self):
        """Free themes bypass the unlock check and apply via CardDraftService."""
        db = _mock_db()
        ul = _make_license()
        mock_draft = MagicMock()

        with patch(_CDS_THEME) as MockCDS:
            MockCDS.get_player_card_draft.return_value = mock_draft
            from app.services.card_theme_service import apply_theme
            apply_theme(db, ul, "arctic")

        MockCDS.update_draft_theme.assert_called_once_with(db, mock_draft, "arctic")

    def test_apply_theme_locked_raises(self):
        db = _mock_db()
        ul = _make_license()
        from app.services.card_theme_service import apply_theme
        with pytest.raises(ValueError, match="locked"):
            apply_theme(db, ul, "gold")

    def test_apply_variant_when_unlocked(self):
        """apply_variant calls CardDraftService.update_draft_variant (not UserLicense)."""
        db = _mock_db()
        ul = _make_license(unlocked_variants=["showcase"])
        mock_draft = MagicMock()

        with patch(_CDS_VARIANT) as MockCDS:
            MockCDS.get_player_card_draft.return_value = mock_draft
            _cvsvc.apply_variant(db, ul, "showcase")

        MockCDS.update_draft_variant.assert_called_once_with(db, mock_draft, "showcase")

    def test_apply_variant_locked_raises(self):
        db = _mock_db()
        ul = _make_license()
        with pytest.raises(ValueError, match="locked"):
            _cvsvc.apply_variant(db, ul, "compact")
