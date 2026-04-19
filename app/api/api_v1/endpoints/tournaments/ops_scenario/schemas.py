"""OPS Scenario Pydantic schemas."""
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field

_OPS_CONFIRM_THRESHOLD = 128  # player_count >= this requires confirmed=True


class OpsScenarioRequest(BaseModel):
    """Request to trigger an ops scenario (admin-only)."""
    scenario: Literal["large_field_monitor", "smoke_test", "scale_test"] = Field(
        ...,
        description="Scenario to run: 'large_field_monitor', 'smoke_test', or 'scale_test'."
    )
    player_count: int = Field(
        default=1024,
        ge=0,
        le=1024,
        description="Number of players to seed + enroll (0–1024). Use 0 for testing enrollment workflows.",
    )
    max_players: Optional[int] = Field(
        None,
        description="Maximum players allowed in tournament. Defaults to player_count if not specified.",
    )
    tournament_type_code: Optional[str] = Field(
        "knockout",
        description="Tournament type code: 'knockout', 'league', or 'group_knockout'. Only used for HEAD_TO_HEAD format.",
    )
    tournament_format: Literal["HEAD_TO_HEAD", "INDIVIDUAL_RANKING"] = Field(
        "HEAD_TO_HEAD",
        description="Tournament format: HEAD_TO_HEAD (1v1 matches) or INDIVIDUAL_RANKING (all compete, ranked by result).",
    )
    scoring_type: Optional[str] = Field(
        None,
        description="Scoring type for INDIVIDUAL_RANKING: TIME_BASED, SCORE_BASED, DISTANCE_BASED, PLACEMENT. Ignored for HEAD_TO_HEAD.",
    )
    ranking_direction: Optional[str] = Field(
        None,
        description="Ranking direction for INDIVIDUAL_RANKING: ASC (lowest wins), DESC (highest wins). Ignored for HEAD_TO_HEAD.",
    )
    tournament_name: Optional[str] = Field(
        None,
        description="Tournament name. Auto-generated as 'Ops-<scenario>-<timestamp>' if omitted.",
    )
    age_group: Optional[str] = Field(
        "PRO",
        description="Age group for tournament: 'PRE', 'YOUTH', 'AMATEUR', 'PRO'. Default: 'PRO'.",
    )
    enrollment_cost: Optional[int] = Field(
        0,
        description="Tournament enrollment cost in credits. Default: 0 (free).",
    )
    initial_tournament_status: Optional[str] = Field(
        "IN_PROGRESS",
        description=(
            "Initial tournament status. Default: 'IN_PROGRESS' (ready for enrollment). "
            "Use 'SEEKING_INSTRUCTOR' for testing instructor assignment workflows."
        ),
    )
    dry_run: bool = Field(
        False,
        description="If True, validate inputs and return without creating any DB records.",
    )
    confirmed: bool = Field(
        False,
        description=(
            "Safety gate for large-scale operations. "
            f"Must be True when player_count >= {_OPS_CONFIRM_THRESHOLD}."
        ),
    )
    simulation_mode: Literal["manual", "auto_immediate", "accelerated"] = Field(
        "accelerated",
        description=(
            "Controls result auto-simulation: "
            "'manual' — sessions created, no auto-simulation (observe live); "
            "'auto_immediate' — results simulated but lifecycle not completed; "
            "'accelerated' — full lifecycle completed synchronously (default)."
        ),
    )
    game_preset_id: Optional[int] = Field(
        None,
        description=(
            "Game preset ID (e.g., GānFootvolley=1). When provided, skills and game config "
            "are auto-synced from the preset. Overrides the default hardcoded skill list."
        ),
    )
    reward_config: Optional[Dict] = Field(
        None,
        description=(
            "Reward config override in the format: "
            "{'first_place': {'xp': N, 'credits': N}, 'second_place': {...}, "
            "'third_place': {...}, 'participation': {'xp': N, 'credits': 0}}. "
            "If omitted, the OPS default policy is used."
        ),
    )
    number_of_rounds: Optional[int] = Field(
        None,
        ge=1,
        le=20,
        description=(
            "Number of rounds for INDIVIDUAL_RANKING tournaments (1–20). "
            "Defaults to 1 if omitted."
        ),
    )
    player_ids: Optional[List[int]] = Field(
        None,
        description=(
            "Explicit list of user IDs to enroll. When provided, overrides player_count "
            "and skips the @lfa-seed.hu pool lookup — any active users can be selected. "
            "player_count is ignored when player_ids is set."
        ),
    )
    campus_ids: List[int] = Field(
        ...,
        min_length=1,
        description=(
            "Explicit campus IDs for session distribution (required, min 1). "
            "Sessions are assigned round-robin across the provided campus IDs. "
            "Auto-discovery is disabled — campuses must be specified explicitly."
        ),
    )
    auto_generate_sessions: bool = Field(
        True,
        description=(
            "Controls session generation behavior. "
            "True: Auto-generate sessions (default). "
            "False: Skip session generation (manual mode for instructor assignment tests)."
        ),
    )


class OpsScenarioResponse(BaseModel):
    """Response from an ops scenario trigger."""
    triggered: bool
    scenario: str
    tournament_id: Optional[int] = None
    tournament_name: Optional[str] = None
    task_id: Optional[str] = None
    enrolled_count: Optional[int] = None
    session_count: Optional[int] = None
    dry_run: bool
    audit_log_id: Optional[int] = None
    message: str
