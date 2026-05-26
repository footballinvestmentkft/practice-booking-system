"""Challenge Card v1 specification — content contract + CardTypeSpec."""
from app.services.card_system._types import (
    CardContentContract,
    CardTypeSpec,
    ContentField,
    ContentFieldKind,
)

_R = ContentFieldKind.REQUIRED
_O = ContentFieldKind.OPTIONAL
_C = ContentFieldKind.COMPUTED

CHALLENGE_CARD_CONTRACT = CardContentContract(
    card_type_id="challenge_card",
    version=1,
    fields=(
        ContentField("challenger_name",    _R, "str",        "Challenger display name"),
        ContentField("challenged_name",    _R, "str",        "Challenged display name"),
        ContentField("game_name",          _R, "str",        "Game display name"),
        ContentField("challenge_mode",     _R, "str",        "'live' or 'async'"),
        ContentField("outcome_reason",     _R, "str",        "Outcome category string"),
        ContentField("challenger_score",   _O, "float|None", "Challenger score_normalized (0-100)"),
        ContentField("challenged_score",   _O, "float|None", "Challenged score_normalized (0-100)"),
        ContentField("winner_name",        _O, "str|None",   "Winner display name; None for draw/no-contest"),
        ContentField("is_draw",            _O, "bool",       "True when challenge ended in draw"),
        ContentField("my_score",           _C, "float|None", "Viewer's own score"),
        ContentField("opp_score",          _C, "float|None", "Opponent's score from viewer perspective"),
        ContentField("my_skill_scores",    _C, "dict",       "Viewer's skill deltas (empty if no attempt)"),
        ContentField("is_viewer_winner",   _C, "bool",       "True when viewing user is the winner"),
        ContentField("cta_label",          _C, "str",        "CTA button text"),
        ContentField("challenge_id",       _C, "int",        "Challenge DB id for CTA link"),
        ContentField("completed_at",       _O, "datetime|None", "Completion timestamp"),
    ),
)

CHALLENGE_CARD_SPEC = CardTypeSpec(
    card_type_id="challenge_card",
    label="Challenge Card",
    description="Virtual Challenge result card for social sharing (16:9 post + 9:16 story).",
    content_contract=CHALLENGE_CARD_CONTRACT,
    supported_variant_ids=("challenge",),
    supported_platform_ids=("challenge_post_16_9", "challenge_story_9_16"),
    theme_compatible=True,
    has_published_state=False,
    is_editable=True,
)
