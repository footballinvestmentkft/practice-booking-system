"""Card Format Registry tests — CFR-01..CFR-16.

CFR-01  WELCOME_CARD_FORMATS has 7 entries
CFR-02  All WC format credit_costs > 0
CFR-03  WC format design_ids are unique
CFR-04  CHALLENGE_CARD_FORMATS has 2 entries
CFR-05  All CC format credit_costs > 0
CFR-06  CC format design_ids are unique
CFR-07  _NON_PLAYER_CARD_PRICES keys include all WC format design_ids
CFR-08  _NON_PLAYER_CARD_PRICES keys include all CC format design_ids
CFR-09  Legacy sentinel keys map to price=0
CFR-10  _resolve_price works for a WC format design_id
CFR-11  _resolve_price works for a CC format design_id
CFR-12  NonPlayerCardFormatDefinition is a frozen dataclass
CFR-13  WC format sort_orders are unique
CFR-14  CC format sort_orders are unique
CFR-15  purchase_design("welcome_card", "default") raises FreeDesignError
CFR-16  purchase_design("challenge_card", "challenge") raises FreeDesignError
"""
import pytest
from unittest.mock import MagicMock


def test_cfr01_welcome_card_formats_has_7_entries():
    from app.services.card_design_service import WELCOME_CARD_FORMATS
    assert len(WELCOME_CARD_FORMATS) == 7


def test_cfr02_wc_format_prices_never_zero():
    from app.services.card_design_service import WELCOME_CARD_FORMATS
    for fmt in WELCOME_CARD_FORMATS:
        assert fmt.credit_cost > 0, f"WC format {fmt.design_id} must have credit_cost > 0"


def test_cfr03_wc_format_design_ids_unique():
    from app.services.card_design_service import WELCOME_CARD_FORMATS
    ids = [f.design_id for f in WELCOME_CARD_FORMATS]
    assert len(ids) == len(set(ids)), "WC format design_ids must be unique"


def test_cfr04_challenge_card_formats_has_2_entries():
    from app.services.card_design_service import CHALLENGE_CARD_FORMATS
    assert len(CHALLENGE_CARD_FORMATS) == 2


def test_cfr05_cc_format_prices_never_zero():
    from app.services.card_design_service import CHALLENGE_CARD_FORMATS
    for fmt in CHALLENGE_CARD_FORMATS:
        assert fmt.credit_cost > 0, f"CC format {fmt.design_id} must have credit_cost > 0"


def test_cfr06_cc_format_design_ids_unique():
    from app.services.card_design_service import CHALLENGE_CARD_FORMATS
    ids = [f.design_id for f in CHALLENGE_CARD_FORMATS]
    assert len(ids) == len(set(ids)), "CC format design_ids must be unique"


def test_cfr07_non_player_prices_covers_all_wc_formats():
    from app.services.card_design_service import WELCOME_CARD_FORMATS, _NON_PLAYER_CARD_PRICES
    for fmt in WELCOME_CARD_FORMATS:
        key = ("welcome_card", fmt.design_id)
        assert key in _NON_PLAYER_CARD_PRICES, f"Missing price entry for {key}"


def test_cfr08_non_player_prices_covers_all_cc_formats():
    from app.services.card_design_service import CHALLENGE_CARD_FORMATS, _NON_PLAYER_CARD_PRICES
    for fmt in CHALLENGE_CARD_FORMATS:
        key = ("challenge_card", fmt.design_id)
        assert key in _NON_PLAYER_CARD_PRICES, f"Missing price entry for {key}"


def test_cfr09_legacy_sentinel_keys_are_zero():
    from app.services.card_design_service import _NON_PLAYER_CARD_PRICES
    assert _NON_PLAYER_CARD_PRICES[("welcome_card", "default")] == 0
    assert _NON_PLAYER_CARD_PRICES[("challenge_card", "challenge")] == 0


def test_cfr10_resolve_price_wc_format():
    from app.services.card_design_service import _resolve_price, WELCOME_CARD_FORMATS
    fmt = WELCOME_CARD_FORMATS[0]
    price = _resolve_price("welcome_card", fmt.design_id)
    assert price == fmt.credit_cost
    assert price > 0


def test_cfr11_resolve_price_cc_format():
    from app.services.card_design_service import _resolve_price, CHALLENGE_CARD_FORMATS
    fmt = CHALLENGE_CARD_FORMATS[0]
    price = _resolve_price("challenge_card", fmt.design_id)
    assert price == fmt.credit_cost
    assert price > 0


def test_cfr12_non_player_format_definition_is_frozen():
    from app.services.card_design_service import NonPlayerCardFormatDefinition
    fmt = NonPlayerCardFormatDefinition(
        design_id="test", label="Test", style_tag="TEST",
        dims="1x1", credit_cost=10, preview_platform="test",
    )
    with pytest.raises(Exception):
        fmt.credit_cost = 99  # type: ignore[misc]


def test_cfr13_wc_format_sort_orders_unique():
    from app.services.card_design_service import WELCOME_CARD_FORMATS
    orders = [f.sort_order for f in WELCOME_CARD_FORMATS]
    assert len(orders) == len(set(orders)), "WC format sort_orders must be unique"


def test_cfr14_cc_format_sort_orders_unique():
    from app.services.card_design_service import CHALLENGE_CARD_FORMATS
    orders = [f.sort_order for f in CHALLENGE_CARD_FORMATS]
    assert len(orders) == len(set(orders)), "CC format sort_orders must be unique"


def test_cfr15_purchase_sentinel_wc_default_raises_free_design_error():
    """CFR-15: purchase_design('welcome_card','default') → FreeDesignError (sentinel key)."""
    from app.services.card_design_service import FreeDesignError, purchase_design

    db   = MagicMock()
    user = MagicMock()
    user.id = 1

    with pytest.raises(FreeDesignError):
        purchase_design(db, user, "welcome_card", "default")


def test_cfr16_purchase_sentinel_cc_challenge_raises_free_design_error():
    """CFR-16: purchase_design('challenge_card','challenge') → FreeDesignError (sentinel key)."""
    from app.services.card_design_service import FreeDesignError, purchase_design

    db   = MagicMock()
    user = MagicMock()
    user.id = 1

    with pytest.raises(FreeDesignError):
        purchase_design(db, user, "challenge_card", "challenge")
