"""
Card Unlock+Apply Gate
======================
Validates that unlock_theme / unlock_variant atomically:
  1. Adds the ID to the unlocked list
  2. Sets the active field (card_theme / card_variant) in the SAME commit
  3. Does NOT double-charge for already-unlocked items
  4. Raises InsufficientCreditsError (via CreditService.deduct) when balance < cost

No DB required — all tests use MagicMock.
"""
import json
import pytest
from unittest.mock import MagicMock, patch, call

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(uid=1, credits=2000):
    u = MagicMock()
    u.id = uid
    u.credit_balance = credits
    return u


def _make_license(card_theme="default", card_variant="fifa",
                  unlocked_themes=None, unlocked_variants=None):
    ul = MagicMock()
    ul.card_theme = card_theme
    ul.card_variant = card_variant
    ul.unlocked_card_themes = list(unlocked_themes or [])
    ul.unlocked_card_variants = list(unlocked_variants or [])
    return ul


def _mock_db():
    db = MagicMock()
    db.begin_nested.return_value.__enter__ = MagicMock(return_value=None)
    db.begin_nested.return_value.__exit__ = MagicMock(return_value=False)
    return db


# ── Theme: unlock + auto-apply ─────────────────────────────────────────────────

class TestUnlockThemeAutoApply:

    def test_unlock_sets_active_theme(self):
        """unlock_theme must set card_theme = theme_id in the same DB commit."""
        db = _mock_db()
        user = _make_user(credits=2000)
        ul = _make_license()

        with patch("app.services.card_theme_service.CreditService") as MockCS:
            MockCS.return_value.deduct.return_value = MagicMock()
            from app.services.card_theme_service import unlock_theme
            unlock_theme(db, user, ul, "gold")

        assert ul.card_theme == "gold", (
            f"card_theme must be 'gold' after unlock, got {ul.card_theme!r}. "
            "unlock_theme must auto-apply the just-unlocked theme."
        )

    def test_unlock_adds_to_unlocked_list(self):
        """unlock_theme must add theme_id to unlocked_card_themes."""
        db = _mock_db()
        user = _make_user()
        ul = _make_license()

        with patch("app.services.card_theme_service.CreditService") as MockCS:
            MockCS.return_value.deduct.return_value = MagicMock()
            from app.services.card_theme_service import unlock_theme
            unlock_theme(db, user, ul, "emerald")

        assert "emerald" in ul.unlocked_card_themes

    def test_unlock_and_apply_in_single_commit(self):
        """Both the unlock list update and the active apply must land in ONE commit."""
        db = _mock_db()
        user = _make_user()
        ul = _make_license()

        captured_state = {}

        def check_state_at_commit():
            captured_state["theme"] = ul.card_theme
            captured_state["unlocked"] = list(ul.unlocked_card_themes)

        db.commit.side_effect = check_state_at_commit

        with patch("app.services.card_theme_service.CreditService") as MockCS:
            MockCS.return_value.deduct.return_value = MagicMock()
            from app.services.card_theme_service import unlock_theme
            unlock_theme(db, user, ul, "crimson")

        assert captured_state["theme"] == "crimson", (
            "card_theme must be 'crimson' at commit time — split-brain detected."
        )
        assert "crimson" in captured_state["unlocked"]
        assert db.commit.call_count == 1, "Must commit exactly once for unlock+apply."

    def test_free_theme_not_charged(self):
        """Free themes (e.g. midnight) skip CreditService entirely."""
        db = _mock_db()
        user = _make_user()
        ul = _make_license()

        with patch("app.services.card_theme_service.CreditService") as MockCS:
            from app.services.card_theme_service import unlock_theme
            unlock_theme(db, user, ul, "midnight")
            MockCS.return_value.deduct.assert_not_called()

    def test_already_unlocked_is_idempotent(self):
        """Unlocking an already-unlocked premium theme must not charge or re-commit."""
        db = _mock_db()
        user = _make_user()
        ul = _make_license(unlocked_themes=["gold"])

        with patch("app.services.card_theme_service.CreditService") as MockCS:
            from app.services.card_theme_service import unlock_theme
            unlock_theme(db, user, ul, "gold")
            MockCS.return_value.deduct.assert_not_called()

        db.commit.assert_not_called()

    def test_unknown_theme_raises(self):
        db = _mock_db()
        from app.services.card_theme_service import unlock_theme
        with pytest.raises(ValueError, match="Unknown theme"):
            unlock_theme(db, _make_user(), _make_license(), "nonexistent")


# ── Variant: unlock + auto-apply ───────────────────────────────────────────────

class TestUnlockVariantAutoApply:

    def test_unlock_sets_active_variant(self):
        """unlock_variant must set card_variant = variant_id in the same DB commit."""
        db = _mock_db()
        user = _make_user(credits=2000)
        ul = _make_license()

        with patch("app.services.card_variant_service.CreditService") as MockCS:
            MockCS.return_value.deduct.return_value = MagicMock()
            from app.services.card_variant_service import unlock_variant
            unlock_variant(db, user, ul, "compact")

        assert ul.card_variant == "compact", (
            f"card_variant must be 'compact' after unlock, got {ul.card_variant!r}. "
            "unlock_variant must auto-apply the just-unlocked variant."
        )

    def test_unlock_adds_to_unlocked_list(self):
        db = _mock_db()
        user = _make_user()
        ul = _make_license()

        with patch("app.services.card_variant_service.CreditService") as MockCS:
            MockCS.return_value.deduct.return_value = MagicMock()
            from app.services.card_variant_service import unlock_variant
            unlock_variant(db, user, ul, "showcase")

        assert "showcase" in ul.unlocked_card_variants

    def test_unlock_and_apply_in_single_commit(self):
        """Both the unlock list update and the active apply must land in ONE commit."""
        db = _mock_db()
        user = _make_user()
        ul = _make_license()

        captured = {}

        def check_at_commit():
            captured["variant"] = ul.card_variant
            captured["unlocked"] = list(ul.unlocked_card_variants)

        db.commit.side_effect = check_at_commit

        with patch("app.services.card_variant_service.CreditService") as MockCS:
            MockCS.return_value.deduct.return_value = MagicMock()
            from app.services.card_variant_service import unlock_variant
            unlock_variant(db, user, ul, "compact")

        assert captured["variant"] == "compact", "Split-brain: variant not active at commit."
        assert "compact" in captured["unlocked"]
        assert db.commit.call_count == 1

    def test_free_variant_not_charged(self):
        db = _mock_db()
        user = _make_user()
        ul = _make_license()

        with patch("app.services.card_variant_service.CreditService") as MockCS:
            from app.services.card_variant_service import unlock_variant
            unlock_variant(db, user, ul, "fifa")
            MockCS.return_value.deduct.assert_not_called()

    def test_already_unlocked_is_idempotent(self):
        db = _mock_db()
        user = _make_user()
        ul = _make_license(unlocked_variants=["compact"])

        with patch("app.services.card_variant_service.CreditService") as MockCS:
            from app.services.card_variant_service import unlock_variant
            unlock_variant(db, user, ul, "compact")
            MockCS.return_value.deduct.assert_not_called()

        db.commit.assert_not_called()

    def test_unknown_variant_raises(self):
        db = _mock_db()
        from app.services.card_variant_service import unlock_variant
        with pytest.raises(ValueError, match="Unknown variant"):
            unlock_variant(db, _make_user(), _make_license(), "nonexistent")


# ── apply_theme / apply_variant: standalone ────────────────────────────────────

class TestApplyStandalone:

    def test_apply_theme_when_unlocked(self):
        db = _mock_db()
        ul = _make_license(unlocked_themes=["emerald"])
        from app.services.card_theme_service import apply_theme
        apply_theme(db, ul, "emerald")
        assert ul.card_theme == "emerald"
        db.commit.assert_called_once()

    def test_apply_theme_free(self):
        db = _mock_db()
        ul = _make_license()
        from app.services.card_theme_service import apply_theme
        apply_theme(db, ul, "arctic")
        assert ul.card_theme == "arctic"

    def test_apply_theme_locked_raises(self):
        db = _mock_db()
        ul = _make_license()
        from app.services.card_theme_service import apply_theme
        with pytest.raises(ValueError, match="locked"):
            apply_theme(db, ul, "gold")

    def test_apply_variant_when_unlocked(self):
        db = _mock_db()
        ul = _make_license(unlocked_variants=["showcase"])
        from app.services.card_variant_service import apply_variant
        apply_variant(db, ul, "showcase")
        assert ul.card_variant == "showcase"
        db.commit.assert_called_once()

    def test_apply_variant_locked_raises(self):
        db = _mock_db()
        ul = _make_license()
        from app.services.card_variant_service import apply_variant
        with pytest.raises(ValueError, match="locked"):
            apply_variant(db, ul, "compact")
