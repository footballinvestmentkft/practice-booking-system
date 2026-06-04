"""Virtual Training Card — v1 spec.

Single-game and reward variants.

Access model (dual-gated):
  1. Ownership  — CDO row required per format (same as Challenge Card).
                  Purchase via /shop?type=virtual_training_card.
  2. Performance — user must have completed enough standalone attempts
                  (check_single_game_eligibility / check_reward_eligibility).

No family shim: owning player_card/fclassic does NOT grant VTC access.
"""
from app.services.card_system._types import (
    CardContentContract,
    CardTypeSpec,
    ContentField,
    ContentFieldKind,
)

_R = ContentFieldKind.REQUIRED
_O = ContentFieldKind.OPTIONAL
_C = ContentFieldKind.COMPUTED

VIRTUAL_TRAINING_CARD_CONTRACT = CardContentContract(
    card_type_id="virtual_training_card",
    version=1,
    fields=(
        ContentField("player_name",       _R, "str",   "Full display name"),
        ContentField("game_name",         _R, "str",   "Virtual training game name"),
        ContentField("game_code",         _R, "str",   "Game identifier code"),
        ContentField("attempt_date",      _R, "str",   "Date of attempts (ISO 8601, YYYY-MM-DD)"),
        ContentField("completed_count",   _R, "int",   "Valid standalone attempts completed"),
        ContentField("max_attempts",      _R, "int",   "Max daily attempts for the game"),
        ContentField("score_normalized",  _O, "float", "Best normalized score 0–100"),
        ContentField("tier",              _O, "int",   "Reward tier (3 / 5 / 10); None for single-game"),
        ContentField("completed_games",   _C, "int",   "Total games completed today (reward context)"),
    ),
)

VIRTUAL_TRAINING_CARD_SPEC = CardTypeSpec(
    card_type_id="virtual_training_card",
    label="Virtual Training Card",
    description="Virtual Training game result card — single-game and reward variants.",
    content_contract=VIRTUAL_TRAINING_CARD_CONTRACT,
    supported_variant_ids=("virtual_training",),
    supported_platform_ids=("vt_landscape", "vt_portrait", "vt_reward_landscape", "vt_reward_portrait"),
    theme_compatible=False,
    has_published_state=False,
    is_editable=False,
)
