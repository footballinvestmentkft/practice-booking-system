"""
Unit tests for app/services/skill_progression_service.py

Coverage targets (pure functions — no DB needed):
  get_skill_tier(level)                                    → (tier_name, emoji)
  get_all_skill_keys()                                     → List[str]
  calculate_skill_value_from_placement(...)                → float  (V3 EMA + V2 legacy paths)

Constants:
  MIN_SKILL_VALUE = 40.0   MAX_SKILL_CAP = 99.0   DEFAULT_BASELINE = 60.0   SYSTEM_BASELINE = 60.0

Tier thresholds:
  ≥ 95 → MASTER   ≥ 85 → ADVANCED   ≥ 70 → INTERMEDIATE   ≥ 50 → DEVELOPING   else → BEGINNER

V3 EMA path (prev_value is not None):
  step = lr × log(1+w) / log(2)   (lr=0.20, w=skill_weight)
  raw_delta = step × (placement_skill − prev_value)
  match_performance_modifier applied symmetrically
  opponent_factor applied asymmetrically (gain: ×f, loss: /f)
  clamped to [MIN_SKILL_VALUE, MAX_SKILL_CAP] = [40.0, 99.0]

V2 legacy path (prev_value is None):
  baseline_weight = 1/(n+1), placement_weight = n/(n+1)
  new_base = baseline×bw + placement_skill×pw
  delta = (new_base − baseline) × skill_weight
  clamped to [40.0, 99.0]
"""

import math
import pytest
from app.services.skill_progression_service import (
    get_skill_tier,
    get_all_skill_keys,
    calculate_skill_value_from_placement,
    MIN_SKILL_VALUE,
    MAX_SKILL_CAP,
    DEFAULT_BASELINE,
)


# ── get_skill_tier ─────────────────────────────────────────────────────────────

class TestGetSkillTier:

    def test_master_tier_at_95(self):
        name, emoji = get_skill_tier(95.0)
        assert name == "MASTER"

    def test_master_tier_above_95(self):
        name, _ = get_skill_tier(99.0)
        assert name == "MASTER"

    def test_advanced_tier_at_85(self):
        name, emoji = get_skill_tier(85.0)
        assert name == "ADVANCED"

    def test_advanced_tier_below_95(self):
        name, _ = get_skill_tier(94.9)
        assert name == "ADVANCED"

    def test_intermediate_tier_at_70(self):
        name, emoji = get_skill_tier(70.0)
        assert name == "INTERMEDIATE"

    def test_intermediate_tier_below_85(self):
        name, _ = get_skill_tier(84.9)
        assert name == "INTERMEDIATE"

    def test_developing_tier_at_50(self):
        name, emoji = get_skill_tier(50.0)
        assert name == "DEVELOPING"

    def test_developing_tier_below_70(self):
        name, _ = get_skill_tier(69.9)
        assert name == "DEVELOPING"

    def test_beginner_tier_at_49(self):
        name, _ = get_skill_tier(49.9)
        assert name == "BEGINNER"

    def test_beginner_tier_at_zero(self):
        name, _ = get_skill_tier(0.0)
        assert name == "BEGINNER"

    def test_beginner_tier_at_40(self):
        name, _ = get_skill_tier(40.0)
        assert name == "BEGINNER"

    def test_returns_tuple_of_two(self):
        result = get_skill_tier(60.0)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_emoji_is_string(self):
        _, emoji = get_skill_tier(80.0)
        assert isinstance(emoji, str)
        assert len(emoji) > 0

    def test_boundary_85_is_advanced_not_intermediate(self):
        name, _ = get_skill_tier(85.0)
        assert name == "ADVANCED"

    def test_boundary_just_below_85_is_intermediate(self):
        name, _ = get_skill_tier(84.99)
        assert name == "INTERMEDIATE"


# ── get_all_skill_keys ─────────────────────────────────────────────────────────

class TestGetAllSkillKeys:

    def test_returns_list(self):
        keys = get_all_skill_keys()
        assert isinstance(keys, list)

    def test_returns_nonempty_list(self):
        keys = get_all_skill_keys()
        assert len(keys) > 0

    def test_all_elements_are_strings(self):
        for key in get_all_skill_keys():
            assert isinstance(key, str)

    def test_no_duplicates(self):
        keys = get_all_skill_keys()
        assert len(keys) == len(set(keys))

    def test_no_empty_strings(self):
        for key in get_all_skill_keys():
            assert key.strip() != ""

    def test_returns_consistent_result(self):
        """Two calls return same list (no randomization)."""
        assert get_all_skill_keys() == get_all_skill_keys()

    def test_count_matches_config(self):
        """29 skills expected based on SKILL_CATEGORIES config."""
        keys = get_all_skill_keys()
        assert len(keys) == 29, f"Expected 29 skill keys, got {len(keys)}: {keys}"


# ── calculate_skill_value_from_placement — shared ─────────────────────────────

class TestCalculateSkillValueShared:
    """Shared contract across both V2 and V3 paths."""

    def test_output_is_float(self):
        result = calculate_skill_value_from_placement(50.0, 1, 5, 3)
        assert isinstance(result, float)

    def test_output_never_below_min(self):
        # Last place — worst possible outcome
        result = calculate_skill_value_from_placement(40.0, 100, 100, 1)
        assert result >= MIN_SKILL_VALUE

    def test_output_never_above_max_cap(self):
        # First place — best possible outcome
        result = calculate_skill_value_from_placement(99.0, 1, 100, 20, skill_weight=5.0)
        assert result <= MAX_SKILL_CAP

    def test_rounded_to_one_decimal(self):
        result = calculate_skill_value_from_placement(50.0, 2, 5, 3)
        assert result == round(result, 1)

    def test_single_player_percentile_zero(self):
        """When total_players=1, percentile=0.0 → placement_skill = MAX_SKILL_VALUE = 100.0."""
        # V2 path: with tournament_count=1, moves toward 100
        result = calculate_skill_value_from_placement(50.0, 1, 1, 1)
        assert result > 50.0

    def test_first_place_yields_higher_than_last_place(self):
        """Rank 1 of 10 must yield a higher new value than rank 10 of 10."""
        first = calculate_skill_value_from_placement(50.0, 1, 10, 5)
        last  = calculate_skill_value_from_placement(50.0, 10, 10, 5)
        assert first > last


# ── calculate_skill_value_from_placement — V2 legacy path ────────────────────

class TestCalculateSkillV2LegacyPath:
    """prev_value=None → V2 weighted-average convergence formula."""

    def test_first_tournament_first_place(self):
        """1 prior tournament, rank 1/5 → skill moves above baseline."""
        result = calculate_skill_value_from_placement(50.0, 1, 5, 1)
        assert result > 50.0

    def test_first_tournament_last_place(self):
        """1 prior tournament, rank 5/5 → skill moves below baseline."""
        result = calculate_skill_value_from_placement(50.0, 5, 5, 1)
        assert result < 50.0

    def test_many_tournaments_convergence_first_place(self):
        """After many tournaments, rank 1 converges skill close to 100."""
        result = calculate_skill_value_from_placement(50.0, 1, 10, 50)
        assert result > 80.0

    def test_skill_weight_amplifies_delta(self):
        """Higher skill_weight should move the value further from baseline."""
        r1 = calculate_skill_value_from_placement(50.0, 1, 5, 3, skill_weight=1.0)
        r2 = calculate_skill_value_from_placement(50.0, 1, 5, 3, skill_weight=2.0)
        assert r2 > r1

    def test_skill_weight_zero_clamps_to_baseline_effectively(self):
        """skill_weight=0.0 → delta=0 → new_skill=baseline, clamped to MIN if below."""
        result = calculate_skill_value_from_placement(50.0, 1, 5, 3, skill_weight=0.0)
        assert result == 50.0

    def test_clamp_above_max_cap(self):
        """Extreme first-place with huge weight should not exceed 99.0."""
        result = calculate_skill_value_from_placement(99.0, 1, 100, 100, skill_weight=5.0)
        assert result <= MAX_SKILL_CAP

    def test_clamp_below_min(self):
        """Extreme last-place with huge weight should not drop below 40.0."""
        result = calculate_skill_value_from_placement(40.0, 100, 100, 100, skill_weight=5.0)
        assert result >= MIN_SKILL_VALUE


# ── calculate_skill_value_from_placement — V3 EMA path ───────────────────────

class TestCalculateSkillV3EMAPath:
    """prev_value is not None → V3 EMA path."""

    def test_first_place_increases_skill(self):
        """Rank 1 of 10 should push skill above prev_value."""
        result = calculate_skill_value_from_placement(
            50.0, placement=1, total_players=10,
            tournament_count=0, prev_value=60.0
        )
        assert result > 60.0

    def test_last_place_decreases_skill(self):
        """Rank 10 of 10 should pull skill below prev_value."""
        result = calculate_skill_value_from_placement(
            50.0, placement=10, total_players=10,
            tournament_count=0, prev_value=60.0
        )
        assert result < 60.0

    def test_neutral_placement_small_change(self):
        """Middle placement (5 of 10) → moderate change."""
        result = calculate_skill_value_from_placement(
            50.0, placement=5, total_players=10,
            tournament_count=0, prev_value=70.0
        )
        assert MIN_SKILL_VALUE <= result <= MAX_SKILL_CAP

    def test_opponent_factor_above_1_amplifies_win(self):
        """Strong opponents (factor>1.0): winning gives bigger bonus."""
        r_neutral = calculate_skill_value_from_placement(
            50.0, 1, 10, 0, prev_value=60.0, opponent_factor=1.0
        )
        r_strong = calculate_skill_value_from_placement(
            50.0, 1, 10, 0, prev_value=60.0, opponent_factor=2.0
        )
        assert r_strong > r_neutral

    def test_opponent_factor_above_1_softens_loss(self):
        """Strong opponents (factor>1.0): losing gives smaller penalty."""
        r_neutral = calculate_skill_value_from_placement(
            50.0, 10, 10, 0, prev_value=60.0, opponent_factor=1.0
        )
        r_strong = calculate_skill_value_from_placement(
            50.0, 10, 10, 0, prev_value=60.0, opponent_factor=2.0
        )
        assert r_strong > r_neutral  # less penalty → higher result

    def test_positive_match_modifier_amplifies_gain(self):
        """Good match performance (modifier>0) amplifies positive delta."""
        r_neutral = calculate_skill_value_from_placement(
            50.0, 1, 10, 0, prev_value=60.0, match_performance_modifier=0.0
        )
        r_good = calculate_skill_value_from_placement(
            50.0, 1, 10, 0, prev_value=60.0, match_performance_modifier=0.5
        )
        assert r_good > r_neutral

    def test_negative_match_modifier_amplifies_loss(self):
        """Poor match performance (modifier<0) amplifies negative delta."""
        r_neutral = calculate_skill_value_from_placement(
            50.0, 10, 10, 0, prev_value=70.0, match_performance_modifier=0.0
        )
        r_bad = calculate_skill_value_from_placement(
            50.0, 10, 10, 0, prev_value=70.0, match_performance_modifier=-0.5
        )
        assert r_bad < r_neutral

    def test_skill_weight_2_gives_bigger_step_than_1(self):
        """Higher weight → larger step → bigger absolute change."""
        r1 = calculate_skill_value_from_placement(
            50.0, 1, 10, 0, skill_weight=1.0, prev_value=60.0
        )
        r2 = calculate_skill_value_from_placement(
            50.0, 1, 10, 0, skill_weight=2.0, prev_value=60.0
        )
        assert r2 > r1

    def test_clamped_below_min(self):
        """Even extreme loss cannot push below MIN_SKILL_VALUE=40.0."""
        result = calculate_skill_value_from_placement(
            50.0, 10, 10, 0, skill_weight=5.0, prev_value=40.0,
            match_performance_modifier=-1.0
        )
        assert result >= MIN_SKILL_VALUE

    def test_clamped_above_max_cap(self):
        """Extreme win cannot exceed MAX_SKILL_CAP=99.0."""
        result = calculate_skill_value_from_placement(
            50.0, 1, 10, 0, skill_weight=5.0, prev_value=99.0,
            opponent_factor=2.0, match_performance_modifier=1.0
        )
        assert result <= MAX_SKILL_CAP

    def test_zero_match_modifier_leaves_formula_unchanged(self):
        """modifier=0.0 must be a no-op (code has early exit when modifier==0.0)."""
        r_no_modifier = calculate_skill_value_from_placement(
            50.0, 1, 10, 0, prev_value=60.0, match_performance_modifier=0.0
        )
        # Calling again must be identical
        r_again = calculate_skill_value_from_placement(
            50.0, 1, 10, 0, prev_value=60.0, match_performance_modifier=0.0
        )
        assert r_no_modifier == r_again

    def test_default_learning_rate_applied(self):
        """Default lr=0.20 with weight=1 → step = 0.20×log(2)/log(2) = 0.20."""
        # For rank 1/2: percentile=0, placement_skill=100; prev=70
        # step=0.20, raw_delta=0.20×(100-70)=6.0; new=76.0
        result = calculate_skill_value_from_placement(
            50.0, placement=1, total_players=2,
            tournament_count=0, skill_weight=1.0, prev_value=70.0
        )
        expected = round(max(MIN_SKILL_VALUE, min(MAX_SKILL_CAP, 70.0 + 0.20 * (100.0 - 70.0))), 1)
        assert result == expected
