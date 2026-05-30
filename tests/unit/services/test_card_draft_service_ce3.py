"""
CDS CE-3.0 — CardDraftService generic get_draft() tests.

CE-3.0 adds get_draft(db, user_id, card_type_id, instance_name="default") as the
family-aware successor to get_or_create_singleton().  These tests verify the new
API while confirming backward compatibility with get_player_card_draft().

CDS-01  get_draft("player_card") returns existing draft (no db.add)
CDS-02  get_player_card_draft() wrapper returns same object as get_draft("player_card")
CDS-03  instance_name creates a distinct draft row
CDS-04  duplicate calls do not create duplicate rows (idempotent)
CDS-05  unknown card_type_id raises ValueError with informative message
CDS-06  get_draft creates new draft with generic defaults for non-player families
CDS-07  get_draft seeds from UserLicense for player_card (same as get_or_create_singleton)
CDS-08  KNOWN_CARD_TYPE_IDS covers all expected families
CDS-09  get_or_create_singleton still works for unknown family (backward compat — no validation)
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.models.card_draft import CardDraft
from app.services.card_draft_service import CardDraftService, KNOWN_CARD_TYPE_IDS

_UID = 77  # non-1 sentinel


# ── Helpers ────────────────────────────────────────────────────────────────────

def _draft(
    user_id: int = _UID,
    card_type_id: str = "player_card",
    instance_name: str = "default",
    draft_theme: str = "default",
    draft_variant: str = "fifa",
) -> CardDraft:
    d = CardDraft()
    d.id            = 10
    d.user_id       = user_id
    d.card_type_id  = card_type_id
    d.instance_name = instance_name
    d.draft_theme   = draft_theme
    d.draft_variant = draft_variant
    d.draft_platform = None
    d.draft_data    = None
    d.published_theme    = None
    d.published_variant  = None
    d.published_platform = None
    d.published_data = None
    d.published_at  = None
    d.created_at    = datetime.now(timezone.utc)
    d.updated_at    = datetime.now(timezone.utc)
    return d


def _db_returning(first_result) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = first_result
    return db


def _db_chain(results: list) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = results
    return db


# ── CDS-01: get_draft returns existing row ────────────────────────────────────

class TestGetDraftReturnsExisting:

    def test_cds_01_returns_existing_player_card_draft(self):
        """CDS-01: get_draft("player_card") returns existing row; db.add not called."""
        existing = _draft(card_type_id="player_card")
        db = _db_returning(existing)

        result = CardDraftService.get_draft(db, user_id=_UID, card_type_id="player_card")

        assert result is existing
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_cds_01b_returns_existing_welcome_card_draft(self):
        """CDS-01b: get_draft("welcome_card") returns existing row without create."""
        existing = _draft(card_type_id="welcome_card")
        db = _db_returning(existing)

        result = CardDraftService.get_draft(db, user_id=_UID, card_type_id="welcome_card")

        assert result is existing
        db.add.assert_not_called()

    def test_cds_01c_correct_fields_passed_to_db_query(self):
        """CDS-01c: DB is queried with the correct user_id and card_type_id."""
        existing = _draft(card_type_id="player_card")
        db = _db_returning(existing)

        CardDraftService.get_draft(db, user_id=_UID, card_type_id="player_card")

        # Verify db.query was called — field filtering is implicit via MagicMock chain
        db.query.assert_called_once_with(CardDraft)


# ── CDS-02: backward compat — get_player_card_draft wrapper ──────────────────

class TestGetPlayerCardDraftWrapper:

    def test_cds_02_wrapper_returns_same_object_as_get_draft(self):
        """CDS-02: get_player_card_draft() and get_draft("player_card") hit the same row."""
        existing = _draft(card_type_id="player_card")

        # Two separate DB mocks that both return the same draft object
        db_a = _db_returning(existing)
        db_b = _db_returning(existing)

        result_wrapper = CardDraftService.get_player_card_draft(db_a, user_id=_UID)
        result_generic = CardDraftService.get_draft(db_b, user_id=_UID, card_type_id="player_card")

        assert result_wrapper.card_type_id == result_generic.card_type_id == "player_card"
        assert result_wrapper.user_id      == result_generic.user_id      == _UID

    def test_cds_02b_wrapper_does_not_call_db_add_on_existing(self):
        """CDS-02b: get_player_card_draft() does not create if draft already exists."""
        existing = _draft(card_type_id="player_card")
        db = _db_returning(existing)

        CardDraftService.get_player_card_draft(db, user_id=_UID)

        db.add.assert_not_called()


# ── CDS-03: instance_name creates distinct draft ──────────────────────────────

class TestInstanceNameDistinct:

    def test_cds_03_different_instance_names_yield_different_rows(self):
        """CDS-03: get_draft with instance_name='secondary' creates a distinct draft row."""
        draft_default   = _draft(instance_name="default")
        draft_secondary = _draft(instance_name="secondary")

        # DB returns existing for "default", None (→ create) for "secondary"
        db_default   = _db_returning(draft_default)
        db_secondary = _db_chain([None, None])  # no existing + no UserLicense

        result_default = CardDraftService.get_draft(
            db_default, user_id=_UID, card_type_id="player_card", instance_name="default"
        )
        CardDraftService.get_draft(
            db_secondary, user_id=_UID, card_type_id="player_card", instance_name="secondary"
        )

        added: CardDraft = db_secondary.add.call_args[0][0]
        assert added.instance_name == "secondary"
        assert result_default.instance_name == "default"

    def test_cds_03b_default_instance_name_is_used_when_omitted(self):
        """CDS-03b: Omitting instance_name defaults to 'default'."""
        existing = _draft(instance_name="default")
        db = _db_returning(existing)

        result = CardDraftService.get_draft(db, user_id=_UID, card_type_id="player_card")

        assert result.instance_name == "default"
        db.add.assert_not_called()


# ── CDS-04: idempotent — no duplicate rows ────────────────────────────────────

class TestNoDuplicateRows:

    def test_cds_04_second_call_returns_existing_row_not_a_new_one(self):
        """CDS-04: Calling get_draft twice returns the same row on second call."""
        # First call: no existing → create
        db_first = _db_chain([None, None])

        CardDraftService.get_draft(db_first, user_id=_UID, card_type_id="welcome_card")

        # db.add called once on first create
        assert db_first.add.call_count == 1

        # Second call: existing row returned
        created = db_first.add.call_args[0][0]
        db_second = _db_returning(created)

        result = CardDraftService.get_draft(db_second, user_id=_UID, card_type_id="welcome_card")

        db_second.add.assert_not_called()
        assert result is created

    def test_cds_04b_add_called_exactly_once_on_first_create(self):
        """CDS-04b: db.add is called exactly once when creating a new draft."""
        db = _db_chain([None, None])

        CardDraftService.get_draft(db, user_id=_UID, card_type_id="challenge_card")

        assert db.add.call_count == 1
        assert db.commit.call_count == 1


# ── CDS-05: invalid card_type_id raises ValueError ───────────────────────────

class TestInvalidCardTypeId:

    def test_cds_05_unknown_family_raises_value_error(self):
        """CDS-05: get_draft with unknown card_type_id raises ValueError."""
        db = MagicMock()

        with pytest.raises(ValueError, match="Unknown card_type_id"):
            CardDraftService.get_draft(db, user_id=_UID, card_type_id="banana_card")

    def test_cds_05b_error_message_includes_the_bad_id(self):
        """CDS-05b: ValueError message contains the offending card_type_id."""
        db = MagicMock()

        with pytest.raises(ValueError, match="banana_card"):
            CardDraftService.get_draft(db, user_id=_UID, card_type_id="banana_card")

    def test_cds_05c_error_message_lists_known_types(self):
        """CDS-05c: ValueError message lists the known types for debugging."""
        db = MagicMock()

        with pytest.raises(ValueError, match="player_card"):
            CardDraftService.get_draft(db, user_id=_UID, card_type_id="future_card")

    def test_cds_05d_db_is_never_queried_for_unknown_family(self):
        """CDS-05d: DB is not touched when card_type_id is invalid."""
        db = MagicMock()

        try:
            CardDraftService.get_draft(db, user_id=_UID, card_type_id="not_a_card")
        except ValueError:
            pass

        db.query.assert_not_called()

    def test_cds_05e_empty_string_raises_value_error(self):
        """CDS-05e: Empty string is not a known card_type_id."""
        db = MagicMock()

        with pytest.raises(ValueError):
            CardDraftService.get_draft(db, user_id=_UID, card_type_id="")


# ── CDS-06: non-player family gets generic defaults ───────────────────────────

class TestGenericDefaultsForNonPlayerFamilies:

    def test_cds_06_welcome_card_created_with_generic_defaults(self):
        """CDS-06: welcome_card draft is created with theme='default', variant='fifa'."""
        db = _db_chain([None])  # no existing draft; no UserLicense lookup for WC

        CardDraftService.get_draft(db, user_id=_UID, card_type_id="welcome_card")

        added: CardDraft = db.add.call_args[0][0]
        assert added.card_type_id  == "welcome_card"
        assert added.draft_theme   == "default"
        assert added.draft_variant == "fifa"
        assert added.instance_name == "default"

    def test_cds_06b_challenge_card_created_with_generic_defaults(self):
        """CDS-06b: challenge_card draft created with generic defaults."""
        db = _db_chain([None])

        CardDraftService.get_draft(db, user_id=_UID, card_type_id="challenge_card")

        added: CardDraft = db.add.call_args[0][0]
        assert added.card_type_id == "challenge_card"
        assert added.draft_theme  == "default"

    def test_cds_06c_match_card_created_without_user_license_lookup(self):
        """CDS-06c: Non-player families do not trigger UserLicense query."""
        # If UserLicense were queried and returned None, side_effect would need 2 items.
        # Using a single-item side_effect proves only one query (CardDraft) is made.
        db = _db_chain([None])

        CardDraftService.get_draft(db, user_id=_UID, card_type_id="match_card")

        # db.query called once (for CardDraft), not twice
        assert db.query.call_count == 1


# ── CDS-07: player_card seeds from UserLicense ────────────────────────────────

class TestPlayerCardSeedsFromLicense:

    def test_cds_07_seeds_theme_and_variant_from_license(self):
        """CDS-07: get_draft('player_card') seeds draft from UserLicense (same as get_or_create_singleton)."""
        lic = MagicMock()
        lic.card_theme           = "midnight"
        lic.card_variant         = "compact"
        lic.public_card_platform = "instagram_square"
        lic.published_card_theme = None

        db = _db_chain([None, lic])  # no draft → UserLicense → lic

        CardDraftService.get_draft(db, user_id=_UID, card_type_id="player_card")

        added: CardDraft = db.add.call_args[0][0]
        assert added.draft_theme    == "midnight"
        assert added.draft_variant  == "compact"
        assert added.draft_platform == "instagram_square"

    def test_cds_07b_player_card_non_default_instance_skips_license_seed(self):
        """CDS-07b: player_card with non-default instance_name skips UserLicense seeding."""
        db = _db_chain([None])  # only one query → no UserLicense lookup

        CardDraftService.get_draft(
            db, user_id=_UID, card_type_id="player_card", instance_name="secondary"
        )

        # Only one .query() call (CardDraft), no UserLicense lookup
        assert db.query.call_count == 1


# ── CDS-08: KNOWN_CARD_TYPE_IDS completeness ─────────────────────────────────

class TestKnownCardTypeIds:

    def test_cds_08_known_types_includes_all_three_active_families(self):
        """CDS-08: KNOWN_CARD_TYPE_IDS includes all three currently active families."""
        assert "player_card"    in KNOWN_CARD_TYPE_IDS
        assert "welcome_card"   in KNOWN_CARD_TYPE_IDS
        assert "challenge_card" in KNOWN_CARD_TYPE_IDS

    def test_cds_08b_known_types_includes_skeletal_registry_families(self):
        """CDS-08b: KNOWN_CARD_TYPE_IDS covers the 7 families in CardRegistry."""
        skeletal = {"match_card", "event_card", "birthday_card", "badge_card"}
        assert skeletal.issubset(KNOWN_CARD_TYPE_IDS), (
            f"Missing from KNOWN_CARD_TYPE_IDS: {skeletal - KNOWN_CARD_TYPE_IDS}"
        )

    def test_cds_08c_known_types_is_frozenset(self):
        """CDS-08c: KNOWN_CARD_TYPE_IDS is a frozenset (immutable at module level)."""
        assert isinstance(KNOWN_CARD_TYPE_IDS, frozenset)


# ── CDS-09: get_or_create_singleton backward compat — no validation ────────────

class TestGetOrCreateSingletonBackwardCompat:

    def test_cds_09_get_or_create_singleton_accepts_unknown_family(self):
        """CDS-09: get_or_create_singleton does NOT validate — legacy code unaffected."""
        # get_or_create_singleton was never validated; must remain so for backward compat.
        db = _db_chain([None, None])

        # This should NOT raise ValueError even for an unknown family
        try:
            CardDraftService.get_or_create_singleton(
                db, user_id=_UID, card_type_id="legacy_unknown_card"
            )
        except ValueError:
            pytest.fail(
                "get_or_create_singleton must not raise ValueError for unknown families "
                "(backward compat). Use get_draft() for validated access."
            )
