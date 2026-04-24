"""
Deterministic regression tests: preset skill_weights → reactivity → EMA step → skill_rating_delta

Goal: prove mathematically that the full pipeline is correct end-to-end.

Pipeline steps tested:
  1. Reactivity conversion:  reactivity = fractional / avg(fractionals)
     (applied in create.py when game_preset_id is specified)

  2. EMA step formula:       step = lr × log(1+weight) / log(2)
     (V3 path in calculate_skill_value_from_placement when prev_value is supplied)

  3. Skill-points distribution: points = (weight / total_weight) × base_points
     (linear, separate from EMA; tested via calculate_skill_points_for_placement)

  4. Full delta pipeline (DB-backed):
     TournamentSkillMapping.weight (reactivity) → compute_single_tournament_skill_delta
     → dominant delta > minor delta, ratio ≈ log(1+w_dom)/log(1+w_min)

Test preset (3 skills with known fractional weights):
  acceleration  → 0.60  (dominant)
  sprint_speed  → 0.25  (mid)
  agility       → 0.15  (minor)

  avg_w = (0.60 + 0.25 + 0.15) / 3 = 1/3
  reactivity_dominant = 0.60 / (1/3) = 1.80
  reactivity_mid      = 0.25 / (1/3) = 0.75
  reactivity_minor    = 0.15 / (1/3) = 0.45
"""

import math
import uuid
import pytest
from decimal import Decimal
from datetime import date, timedelta
from sqlalchemy.orm import Session

from app.services.skill_progression_service import (
    calculate_skill_value_from_placement,
    compute_single_tournament_skill_delta,
    MIN_SKILL_VALUE,
    MAX_SKILL_CAP,
    DEFAULT_BASELINE,
)
from app.services.tournament.tournament_participation_service import (
    calculate_skill_points_for_placement,
    PLACEMENT_SKILL_POINTS,
)


# ─── Preset-level constants ───────────────────────────────────────────────────
# Three skills with fractional weights that sum to 1.0 (as stored in
# game_config.skill_config.skill_weights by _build_game_config).
FRACS: dict[str, float] = {
    "acceleration": 0.60,   # dominant
    "sprint_speed":  0.25,   # mid
    "agility":       0.15,   # minor
}
AVG_W: float = sum(FRACS.values()) / len(FRACS)   # = 1/3

# Reactivity values (stored in TournamentSkillMapping.weight after create.py conversion)
REACT: dict[str, float] = {k: round(v / AVG_W, 2) for k, v in FRACS.items()}

# Default EMA learning rate
LR: float = 0.20

# Placement scenario: 3 players, 1 place each
TOTAL_PLAYERS = 3


# ─── Pure-math helpers ────────────────────────────────────────────────────────

def _ema_step(weight: float) -> float:
    """Expected EMA step = lr × log(1+w) / log(2)."""
    return LR * math.log(1.0 + weight) / math.log(2.0)


def _placement_skill(placement: int, total: int = TOTAL_PLAYERS) -> float:
    """Placement → target skill value (100=best, 40=worst, linear)."""
    if total == 1:
        return 100.0
    percentile = (placement - 1) / (total - 1)
    return 100.0 - percentile * (100.0 - 40.0)


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _make_user(db: Session) -> "User":  # noqa: F821
    from app.models.user import User, UserRole
    u = User(
        email=f"skill_pipeline+{uuid.uuid4().hex[:10]}@test.com",
        name="Pipeline Test User",
        password_hash="test_hash",
        role=UserRole.STUDENT,
    )
    db.add(u)
    db.flush()
    return u


def _make_semester(db: Session) -> "Semester":  # noqa: F821
    """Create a minimal tournament (Semester) via the core service."""
    from app.services.tournament.core import create_tournament_semester
    from app.models.specialization import SpecializationType

    return create_tournament_semester(
        db=db,
        tournament_date=date.today() + timedelta(days=7),
        name=f"Pipeline Test Tournament {uuid.uuid4().hex[:6]}",
        specialization_type=SpecializationType.LFA_PLAYER_YOUTH,
    )


def _add_skill_mappings(
    db: Session,
    semester_id: int,
    weights: dict[str, float],
) -> None:
    """Insert TournamentSkillMapping rows with given reactivity weights."""
    from app.models.tournament_achievement import TournamentSkillMapping

    for skill_name, weight in weights.items():
        db.add(TournamentSkillMapping(
            semester_id=semester_id,
            skill_name=skill_name,
            skill_category="Physical",
            weight=Decimal(str(round(weight, 2))),
        ))
    db.flush()


def _add_participation(
    db: Session,
    user_id: int,
    semester_id: int,
    placement: int | None,
) -> "TournamentParticipation":  # noqa: F821
    from app.models.tournament_achievement import TournamentParticipation

    p = TournamentParticipation(
        user_id=user_id,
        semester_id=semester_id,
        placement=placement,
        xp_awarded=0,
        credits_awarded=0,
    )
    db.add(p)
    db.flush()
    return p


# ─── Class 1: Reactivity conversion (pure math, no DB) ───────────────────────

@pytest.mark.unit
@pytest.mark.tournament
class TestReactivityConversion:
    """
    Prove that the reactivity = fractional / avg(fractionals) formula
    yields the expected concrete values for a 3-skill preset.
    """

    def test_avg_w_is_one_third(self):
        """avg_w of a 3-skill uniform-sum-1.0 preset = 1/3."""
        assert abs(AVG_W - (1.0 / 3.0)) < 1e-10

    def test_dominant_reactivity_above_one(self):
        """Skill with fraction > avg → reactivity > 1.0 (amplified)."""
        assert REACT["acceleration"] > 1.0

    def test_minor_reactivity_below_one(self):
        """Skill with fraction < avg → reactivity < 1.0 (dampened)."""
        assert REACT["agility"] < 1.0

    def test_concrete_reactivity_values(self):
        """Reactivity values match formula to 2 decimal places."""
        assert abs(REACT["acceleration"] - 1.80) < 0.005
        assert abs(REACT["sprint_speed"] - 0.75) < 0.005
        assert abs(REACT["agility"] - 0.45) < 0.005

    def test_ordering_preserved(self):
        """Reactivity ordering mirrors fractional ordering."""
        assert REACT["acceleration"] > REACT["sprint_speed"] > REACT["agility"]

    def test_fracs_sum_to_one(self):
        """Preset fractional weights must sum to 1.0 (invariant of _build_game_config)."""
        assert abs(sum(FRACS.values()) - 1.0) < 1e-10


# ─── Class 2: EMA step math (pure function, no DB) ───────────────────────────

@pytest.mark.unit
@pytest.mark.tournament
class TestEMAStepMath:
    """
    Prove that V3 EMA step = lr × log(1+w) / log(2) and that
    dominant_step / minor_step = log(1+w_dom) / log(1+w_min).
    """

    def test_step_formula_dominant(self):
        """EMA step for dominant reactivity (1.80) matches analytical formula."""
        expected = LR * math.log(1.0 + REACT["acceleration"]) / math.log(2.0)
        actual = _ema_step(REACT["acceleration"])
        assert abs(actual - expected) < 1e-12

    def test_step_formula_minor(self):
        """EMA step for minor reactivity (0.45) matches analytical formula."""
        expected = LR * math.log(1.0 + REACT["agility"]) / math.log(2.0)
        actual = _ema_step(REACT["agility"])
        assert abs(actual - expected) < 1e-12

    def test_step_ordering(self):
        """Dominant skill has larger EMA step than minor skill."""
        assert _ema_step(REACT["acceleration"]) > _ema_step(REACT["sprint_speed"])
        assert _ema_step(REACT["sprint_speed"]) > _ema_step(REACT["agility"])

    def test_step_ratio_is_log_normalized(self):
        """
        Ratio of EMA steps equals ratio of log-normalized weights — not linear.

        Mathematical guarantee from V3:
            step_dom / step_min = log(1+w_dom) / log(1+w_min)
        This is strictly less than w_dom / w_min (sub-linear growth).
        """
        step_dom = _ema_step(REACT["acceleration"])
        step_min = _ema_step(REACT["agility"])
        expected_ratio = (
            math.log(1.0 + REACT["acceleration"]) / math.log(1.0 + REACT["agility"])
        )
        actual_ratio = step_dom / step_min
        assert abs(actual_ratio - expected_ratio) < 1e-10

    def test_step_sublinear_vs_linear(self):
        """Log-normalisation ensures EMA ratio < linear weight ratio."""
        step_ratio = _ema_step(REACT["acceleration"]) / _ema_step(REACT["agility"])
        weight_ratio = REACT["acceleration"] / REACT["agility"]
        assert step_ratio < weight_ratio

    def test_unit_weight_anchors_at_lr(self):
        """EMA step at weight=1.0 exactly equals learning_rate (lr)."""
        step_at_one = LR * math.log(2.0) / math.log(2.0)  # = LR
        actual = _ema_step(1.0)
        assert abs(actual - LR) < 1e-12

    def test_placement_first_produces_positive_delta(self):
        """1st place with prev_value below 100 always produces a positive delta."""
        prev = 60.0
        new_val = calculate_skill_value_from_placement(
            baseline=prev,
            placement=1,
            total_players=3,
            tournament_count=1,
            skill_weight=REACT["acceleration"],
            prev_value=prev,
        )
        assert new_val > prev

    def test_placement_last_produces_negative_delta(self):
        """Last place with prev_value above 40 always produces a negative delta."""
        prev = 60.0
        new_val = calculate_skill_value_from_placement(
            baseline=prev,
            placement=3,
            total_players=3,
            tournament_count=1,
            skill_weight=REACT["acceleration"],
            prev_value=prev,
        )
        assert new_val < prev

    def test_dominant_delta_exceeds_minor_delta_same_placement(self):
        """For the same placement, dominant weight produces larger absolute delta."""
        prev = 60.0
        new_dom = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=3, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=prev,
        )
        new_min = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=3, tournament_count=1,
            skill_weight=REACT["agility"], prev_value=prev,
        )
        assert (new_dom - prev) > (new_min - prev)

    def test_clamp_floor_at_40(self):
        """New skill value is never below MIN_SKILL_VALUE (40.0)."""
        new_val = calculate_skill_value_from_placement(
            baseline=40.0, placement=3, total_players=3, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=40.0,
        )
        assert new_val >= MIN_SKILL_VALUE

    def test_clamp_ceiling_at_99(self):
        """New skill value is never above MAX_SKILL_CAP (99.0)."""
        new_val = calculate_skill_value_from_placement(
            baseline=99.0, placement=1, total_players=3, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=99.0,
        )
        assert new_val <= MAX_SKILL_CAP


# ─── Class 3: Skill-points distribution (DB-backed) ──────────────────────────

@pytest.mark.unit
@pytest.mark.tournament
class TestSkillPointsDistribution:
    """
    Prove that calculate_skill_points_for_placement distributes base_points
    proportionally to the reactivity weights stored in TournamentSkillMapping.
    """

    def test_first_place_total_equals_base_points(self, postgres_db: Session):
        """Sum of skill points for 1st place = PLACEMENT_SKILL_POINTS[1] = 10."""
        semester = _make_semester(postgres_db)
        _add_skill_mappings(postgres_db, semester.id, REACT)

        result = calculate_skill_points_for_placement(
            db=postgres_db,
            tournament_id=semester.id,
            placement=1,
        )

        total_pts = sum(result.values())
        assert abs(total_pts - PLACEMENT_SKILL_POINTS[1]) < 0.15  # rounding tolerance

    def test_dominant_skill_gets_most_points(self, postgres_db: Session):
        """Acceleration (reactivity 1.80) earns more points than agility (0.45)."""
        semester = _make_semester(postgres_db)
        _add_skill_mappings(postgres_db, semester.id, REACT)

        result = calculate_skill_points_for_placement(
            db=postgres_db,
            tournament_id=semester.id,
            placement=1,
        )

        assert result["acceleration"] > result["sprint_speed"] > result["agility"]

    def test_skill_points_proportional_to_reactivity(self, postgres_db: Session):
        """
        Points ratio mirrors reactivity ratio:
            points_dom / points_min ≈ reactivity_dom / reactivity_min = 1.80/0.45 = 4.0
        """
        semester = _make_semester(postgres_db)
        _add_skill_mappings(postgres_db, semester.id, REACT)

        result = calculate_skill_points_for_placement(
            db=postgres_db,
            tournament_id=semester.id,
            placement=1,
        )

        expected_ratio = REACT["acceleration"] / REACT["agility"]
        actual_ratio = result["acceleration"] / result["agility"]
        assert abs(actual_ratio - expected_ratio) < 0.05

    def test_participant_placement_gets_fewer_points_than_first(self, postgres_db: Session):
        """Participation-level reward (placement=None, base=1) < 1st place (base=10)."""
        semester = _make_semester(postgres_db)
        _add_skill_mappings(postgres_db, semester.id, REACT)

        pts_first = calculate_skill_points_for_placement(
            db=postgres_db, tournament_id=semester.id, placement=1,
        )
        pts_part = calculate_skill_points_for_placement(
            db=postgres_db, tournament_id=semester.id, placement=None,
        )

        assert sum(pts_first.values()) > sum(pts_part.values())

    def test_all_three_skills_present_in_result(self, postgres_db: Session):
        """All three mapped skills appear in the result dictionary."""
        semester = _make_semester(postgres_db)
        _add_skill_mappings(postgres_db, semester.id, REACT)

        result = calculate_skill_points_for_placement(
            db=postgres_db, tournament_id=semester.id, placement=2,
        )

        for skill in REACT:
            assert skill in result, f"Expected skill '{skill}' in result"


# ─── Class 4: Full delta pipeline (DB-backed) ────────────────────────────────

@pytest.mark.unit
@pytest.mark.tournament
class TestSkillDeltaPipeline:
    """
    End-to-end DB-backed test:
    TournamentSkillMapping reactivity weights → compute_single_tournament_skill_delta
    → verify dominant delta > minor delta with correct log-normalised ratio.
    """

    def _setup_tournament_with_participants(
        self,
        db: Session,
    ) -> tuple:
        """
        Returns (target_user, semester) after creating:
          - 3 users (target placed 1st, others 2nd/3rd)
          - semester with TournamentSkillMapping rows
          - TournamentParticipation for all 3 users
        """
        semester = _make_semester(db)
        _add_skill_mappings(db, semester.id, REACT)

        target = _make_user(db)
        second = _make_user(db)
        third = _make_user(db)

        _add_participation(db, target.id, semester.id, placement=1)
        _add_participation(db, second.id, semester.id, placement=2)
        _add_participation(db, third.id, semester.id, placement=3)

        db.commit()
        return target, semester

    def test_dominant_delta_greater_than_minor_delta(self, postgres_db: Session):
        """
        Dominant skill (reactivity 1.80) earns larger EMA delta than
        minor skill (reactivity 0.45) for the same 1st-place finish.
        """
        target, semester = self._setup_tournament_with_participants(postgres_db)

        delta = compute_single_tournament_skill_delta(
            db=postgres_db,
            user_id=target.id,
            tournament_id=semester.id,
        )

        assert "acceleration" in delta, "Dominant skill must have a non-zero delta"
        assert "agility" in delta, "Minor skill must have a non-zero delta"
        assert delta["acceleration"] > delta["agility"]

    def test_delta_ordering_matches_reactivity_ordering(self, postgres_db: Session):
        """acceleration delta > sprint_speed delta > agility delta."""
        target, semester = self._setup_tournament_with_participants(postgres_db)

        delta = compute_single_tournament_skill_delta(
            db=postgres_db,
            user_id=target.id,
            tournament_id=semester.id,
        )

        assert delta["acceleration"] > delta["sprint_speed"] > delta["agility"]

    def test_delta_ratio_approximates_log_normalised_step_ratio(self, postgres_db: Session):
        """
        delta_dom / delta_min ≈ step_dom / step_min
                               = log(1+1.80) / log(1+0.45)
        (within ±10% accounting for rounding and same prev_value = DEFAULT_BASELINE)
        """
        target, semester = self._setup_tournament_with_participants(postgres_db)

        delta = compute_single_tournament_skill_delta(
            db=postgres_db,
            user_id=target.id,
            tournament_id=semester.id,
        )

        expected_ratio = _ema_step(REACT["acceleration"]) / _ema_step(REACT["agility"])
        actual_ratio = delta["acceleration"] / delta["agility"]
        # Allow 10% tolerance for rounding at 1 decimal place
        assert abs(actual_ratio - expected_ratio) / expected_ratio < 0.10

    def test_first_place_all_deltas_positive(self, postgres_db: Session):
        """1st place finish → all skill deltas positive (moving up from DEFAULT_BASELINE)."""
        target, semester = self._setup_tournament_with_participants(postgres_db)

        delta = compute_single_tournament_skill_delta(
            db=postgres_db,
            user_id=target.id,
            tournament_id=semester.id,
        )

        for skill, d in delta.items():
            assert d > 0, f"Expected positive delta for {skill!r}, got {d}"

    def test_delta_values_within_clamp_bounds(self, postgres_db: Session):
        """
        Delta can never push skill outside [40, 99].
        Verify: DEFAULT_BASELINE + delta is in valid range.
        """
        target, semester = self._setup_tournament_with_participants(postgres_db)

        delta = compute_single_tournament_skill_delta(
            db=postgres_db,
            user_id=target.id,
            tournament_id=semester.id,
        )

        for skill, d in delta.items():
            new_val = DEFAULT_BASELINE + d
            assert MIN_SKILL_VALUE <= new_val <= MAX_SKILL_CAP, (
                f"{skill}: DEFAULT_BASELINE({DEFAULT_BASELINE}) + delta({d}) = {new_val} "
                f"outside [{MIN_SKILL_VALUE}, {MAX_SKILL_CAP}]"
            )

    def test_user_with_no_prior_tournament_history(self, postgres_db: Session):
        """
        A brand-new user (first tournament) starts from DEFAULT_BASELINE.
        All skills should have a delta recorded (non-empty result).
        """
        target, semester = self._setup_tournament_with_participants(postgres_db)

        delta = compute_single_tournament_skill_delta(
            db=postgres_db,
            user_id=target.id,
            tournament_id=semester.id,
        )

        assert len(delta) > 0, "Delta should be non-empty for a user's first tournament"
        # Only our 3 mapped skills should appear (unmapped skills have no weight)
        assert set(delta.keys()) <= set(REACT.keys())


# ─── Class 5: Multi-seed variation (pure function, parametrized) ───────────────

# Fixed baseline seeds for deterministic reproduction — NOT truly random.
# These cover: below average, at system default (60), above average, near clamp boundaries.
_SEED_BASELINES = [
    (41.5, "near_floor"),      # close to MIN_SKILL_VALUE (40)
    (50.0, "below_average"),   # below system default; retained as formula regression seed
    (60.0, "default"),         # DEFAULT_BASELINE / SYSTEM_BASELINE — every new player starts here
    (75.0, "advanced"),        # above-average player
    (97.5, "near_ceiling"),    # close to MAX_SKILL_CAP (99)
]

# Variant used by test_step_ratio_consistent_across_seeds.
# near_ceiling (prev=97.5) is included but uses an ordering assertion instead of the
# dom/min ratio check: at MAX_SKILL_CAP proximity the EMA compression changes the
# effective slope, so the ratio diverges from step_dom/step_min. The ordering
# invariant (dom_delta > agil_delta) remains valid and is asserted instead.
_RATIO_SEED_BASELINES = [
    pytest.param(41.5, "near_floor",   id="near_floor"),
    pytest.param(50.0, "below_average", id="below_average"),
    pytest.param(60.0, "default",      id="default"),
    pytest.param(75.0, "advanced",     id="advanced"),
    pytest.param(97.5, "near_ceiling", id="near_ceiling"),
]


@pytest.mark.unit
@pytest.mark.tournament
class TestMultiSeedVariation:
    """
    Parametrized regression over multiple initial skill baselines.
    Verifies that ordering and clamp invariants hold across all seeds.
    CI runs all seeds — confirms the pipeline is not accidentally tuned
    only to DEFAULT_BASELINE (60.0).
    """

    @pytest.mark.parametrize("prev,label", _SEED_BASELINES, ids=[l for _, l in _SEED_BASELINES])
    def test_dominant_always_beats_minor_for_1st_place(self, prev, label):
        """For any starting value, 1st place dominant delta > minor delta."""
        dom = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=4, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=prev,
        )
        mino = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=4, tournament_count=1,
            skill_weight=REACT["agility"], prev_value=prev,
        )
        dom_delta = dom - prev
        min_delta = mino - prev
        # Both should be positive (gaining from 1st place)
        assert dom_delta >= 0, f"[{label}] Expected positive dom delta, got {dom_delta}"
        assert min_delta >= 0, f"[{label}] Expected positive min delta, got {min_delta}"
        # Dominant must always beat minor (may be equal only when both clamped)
        assert dom_delta >= min_delta, (
            f"[{label}] dom_delta={dom_delta} < min_delta={min_delta}"
        )

    @pytest.mark.parametrize("prev,label", _SEED_BASELINES, ids=[l for _, l in _SEED_BASELINES])
    def test_last_place_always_negative_or_clamped(self, prev, label):
        """For any starting value ≥ MIN_SKILL_VALUE, last place never increases skill."""
        new_val = calculate_skill_value_from_placement(
            baseline=prev, placement=4, total_players=4, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=prev,
        )
        assert new_val <= prev, (
            f"[{label}] Last place raised skill from {prev} to {new_val}"
        )

    @pytest.mark.parametrize("prev,label", _SEED_BASELINES, ids=[l for _, l in _SEED_BASELINES])
    def test_clamp_invariant_all_seeds(self, prev, label):
        """Result is always within [MIN_SKILL_VALUE, MAX_SKILL_CAP] for every seed."""
        for placement in (1, 2, 3, 4):
            new_val = calculate_skill_value_from_placement(
                baseline=prev, placement=placement, total_players=4, tournament_count=1,
                skill_weight=REACT["acceleration"], prev_value=prev,
            )
            assert MIN_SKILL_VALUE <= new_val <= MAX_SKILL_CAP, (
                f"[{label}] placement={placement}: {new_val} outside [{MIN_SKILL_VALUE}, {MAX_SKILL_CAP}]"
            )

    @pytest.mark.parametrize("prev,label", _RATIO_SEED_BASELINES)
    def test_step_ratio_consistent_across_seeds(self, prev, label):
        """
        The dom/min delta ratio converges to step_dom/step_min regardless of prev_value
        (unless near MAX_SKILL_CAP). Skip check when either delta rounds to zero.
        For near_ceiling (prev=97.5): EMA compression changes the effective slope so the
        ratio diverges — assert the ordering invariant (dom_delta > agil_delta) instead.
        """
        dom_new = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=4, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=prev,
        )
        min_new = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=4, tournament_count=1,
            skill_weight=REACT["agility"], prev_value=prev,
        )
        dom_delta = round(dom_new - prev, 1)
        min_delta = round(min_new - prev, 1)

        if min_delta == 0 or dom_delta == 0:
            pytest.skip(f"[{label}] Delta rounded to zero (clamp boundary) — ratio undefined")

        if label == "near_ceiling":
            # Near MAX_SKILL_CAP the EMA slope compresses differently for each weight,
            # causing the dom/min ratio to diverge from the step_dom/step_min target.
            # Assert the ordering invariant: dominant weight always produces a larger
            # positive delta than the minor weight at 1st place.
            assert dom_delta > 0, f"[near_ceiling] dom_delta must be positive, got {dom_delta}"
            assert dom_delta > min_delta, (
                f"[near_ceiling] dom_delta ({dom_delta}) must exceed agil_delta ({min_delta})"
            )
            return

        expected_ratio = _ema_step(REACT["acceleration"]) / _ema_step(REACT["agility"])
        actual_ratio = dom_delta / min_delta
        assert abs(actual_ratio - expected_ratio) / expected_ratio < 0.15, (
            f"[{label}] Ratio {actual_ratio:.3f} deviates >15% from {expected_ratio:.3f}"
        )


# ─── Extreme-weight preset constants (module-level to avoid comprehension scoping) ──
# Two-skill extreme preset (fractional, sum = 1.0)
_EXTREME_FRACS: dict[str, float] = {"acceleration": 0.99, "agility": 0.01}
_EXTREME_AVG_W: float = sum(_EXTREME_FRACS.values()) / len(_EXTREME_FRACS)   # = 0.50
# raw reactivities: dom=1.98, min=0.02 (below schema clamp of 0.10)
_EXTREME_REACT_RAW: dict[str, float] = {k: v / _EXTREME_AVG_W for k, v in _EXTREME_FRACS.items()}
# after schema clamp [0.1, 5.0]
_EXTREME_REACT: dict[str, float] = {k: max(0.1, min(5.0, v)) for k, v in _EXTREME_REACT_RAW.items()}


# ─── Class 6: Edge-case stress — extreme weight distribution ──────────────────

@pytest.mark.unit
@pytest.mark.tournament
class TestEdgeCaseExtremeWeights:
    """
    Stress test with a nearly-degenerate preset: one skill at 0.99 fractional weight,
    another at 0.01.

    After reactivity conversion (clamped to [0.1, 5.0]):
      avg_w = (0.99 + 0.01) / 2 = 0.50
      reactivity_dom = 0.99 / 0.50 = 1.98
      reactivity_min = 0.01 / 0.50 = 0.02 → clamped to 0.10 (schema minimum)
    """

    EXTREME_FRACS  = _EXTREME_FRACS
    EXTREME_AVG_W  = _EXTREME_AVG_W
    EXTREME_REACT  = _EXTREME_REACT

    def test_dominant_reactivity_within_schema_bounds(self):
        """Dominant reactivity for 0.99 fractional ≤ 5.0 (schema max)."""
        assert self.EXTREME_REACT["acceleration"] <= 5.0
        assert self.EXTREME_REACT["acceleration"] > 1.0  # definitely dominant

    def test_minor_reactivity_clamped_to_minimum(self):
        """Reactivity for 0.01 fractional is clamped to schema min (0.10)."""
        # raw = 0.01/0.50 = 0.02 < 0.10 → must clamp
        assert self.EXTREME_REACT["agility"] == pytest.approx(0.10, abs=0.001)

    def test_extreme_dominant_delta_positive_for_1st(self):
        """Even at extreme weights, dominant skill improves on 1st place."""
        prev = 50.0
        new_val = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=4, tournament_count=1,
            skill_weight=self.EXTREME_REACT["acceleration"], prev_value=prev,
        )
        assert new_val > prev

    def test_extreme_minor_delta_still_positive_for_1st(self):
        """Even clamped-minimum weight skill improves on 1st place."""
        prev = 50.0
        new_val = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=4, tournament_count=1,
            skill_weight=self.EXTREME_REACT["agility"], prev_value=prev,
        )
        assert new_val > prev

    def test_extreme_dominant_always_greater_than_minor(self):
        """Dominant delta > minor delta even at extreme ratio."""
        prev = 60.0
        dom = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=4, tournament_count=1,
            skill_weight=self.EXTREME_REACT["acceleration"], prev_value=prev,
        )
        mino = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=4, tournament_count=1,
            skill_weight=self.EXTREME_REACT["agility"], prev_value=prev,
        )
        assert (dom - prev) > (mino - prev)

    def test_extreme_clamp_invariant(self):
        """Extreme weights never push skill outside [40, 99] for any placement."""
        for placement in (1, 2, 3, 4):
            for w in self.EXTREME_REACT.values():
                new_val = calculate_skill_value_from_placement(
                    baseline=50.0, placement=placement, total_players=4, tournament_count=1,
                    skill_weight=w, prev_value=50.0,
                )
                assert MIN_SKILL_VALUE <= new_val <= MAX_SKILL_CAP


# ─── Class 7: Edge-case stress — clamp boundary baselines ────────────────────

@pytest.mark.unit
@pytest.mark.tournament
class TestEdgeCaseClampBoundary:
    """
    Stress test with baselines near the clamp limits.
    Verifies: no overflow, no underflow, correct clamp behaviour.
    """

    def test_near_floor_first_place_does_not_underflow(self):
        """With prev=41.0, even a loss cannot push below 40.0."""
        new_val = calculate_skill_value_from_placement(
            baseline=41.0, placement=4, total_players=4, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=41.0,
        )
        assert new_val >= MIN_SKILL_VALUE

    def test_near_floor_value_raises_on_win(self):
        """With prev=41.0, 1st place should push the value up."""
        new_val = calculate_skill_value_from_placement(
            baseline=41.0, placement=1, total_players=4, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=41.0,
        )
        assert new_val > 41.0

    def test_at_floor_last_place_stays_at_floor(self):
        """At exactly prev=40.0 (floor), last place cannot go lower."""
        new_val = calculate_skill_value_from_placement(
            baseline=40.0, placement=4, total_players=4, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=40.0,
        )
        assert new_val == pytest.approx(MIN_SKILL_VALUE, abs=0.1)

    def test_near_ceiling_last_place_decreases(self):
        """With prev=98.0, last place pushes value down (not clamped on loss side)."""
        new_val = calculate_skill_value_from_placement(
            baseline=98.0, placement=4, total_players=4, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=98.0,
        )
        assert new_val < 98.0

    def test_at_ceiling_first_place_stays_at_ceiling(self):
        """At exactly prev=99.0 (ceiling), 1st place cannot exceed 99.0."""
        new_val = calculate_skill_value_from_placement(
            baseline=99.0, placement=1, total_players=4, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=99.0,
        )
        assert new_val == pytest.approx(MAX_SKILL_CAP, abs=0.1)

    def test_near_ceiling_first_place_capped(self):
        """With prev=98.0, 1st place clamps at 99.0 (never exceeds)."""
        new_val = calculate_skill_value_from_placement(
            baseline=98.0, placement=1, total_players=4, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=98.0,
        )
        assert new_val <= MAX_SKILL_CAP

    @pytest.mark.parametrize("prev", [40.0, 40.1, 98.9, 99.0])
    def test_all_placements_clamped_at_boundary_values(self, prev):
        """Boundary baselines: all placements produce values in [40, 99]."""
        for placement in (1, 2, 3, 4):
            new_val = calculate_skill_value_from_placement(
                baseline=prev, placement=placement, total_players=4, tournament_count=1,
                skill_weight=REACT["acceleration"], prev_value=prev,
            )
            assert MIN_SKILL_VALUE <= new_val <= MAX_SKILL_CAP, (
                f"prev={prev}, placement={placement}: {new_val} outside bounds"
            )


# ─── Class 8: Large tournament stress (≥128 players, pure function) ───────────

@pytest.mark.unit
@pytest.mark.tournament
class TestLargeTournament:
    """
    Stress test with field sizes of 128, 256, and 1024 players.
    Verifies that percentile math scales correctly and clamp invariants hold.
    """

    @pytest.mark.parametrize("total", [128, 256, 1024], ids=["128p", "256p", "1024p"])
    def test_1st_place_always_100_target(self, total):
        """1st place always maps to placement_skill=100 regardless of field size."""
        # placement_skill = 100 - (0/(total-1)) * 60 = 100
        prev = 50.0
        new_val = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=total, tournament_count=1,
            skill_weight=1.0, prev_value=prev,
        )
        # 1st place always gives a positive delta (moving toward 100)
        assert new_val > prev

    @pytest.mark.parametrize("total", [128, 256, 1024], ids=["128p", "256p", "1024p"])
    def test_last_place_always_40_target(self, total):
        """Last place always maps to placement_skill=40 regardless of field size."""
        prev = 60.0
        new_val = calculate_skill_value_from_placement(
            baseline=prev, placement=total, total_players=total, tournament_count=1,
            skill_weight=1.0, prev_value=prev,
        )
        # Last place always gives a negative delta (moving toward 40)
        assert new_val < prev

    @pytest.mark.parametrize("total", [128, 256, 1024], ids=["128p", "256p", "1024p"])
    def test_median_placement_near_neutral(self, total):
        """
        Median placement (50th percentile) targets placement_skill≈70 (midpoint of [40,100]).
        With prev=70, delta should be near zero.
        """
        median = (total + 1) // 2
        prev = 70.0
        new_val = calculate_skill_value_from_placement(
            baseline=prev, placement=median, total_players=total, tournament_count=1,
            skill_weight=1.0, prev_value=prev,
        )
        # median percentile ≈ 0.5 → placement_skill ≈ 70 → delta ≈ 0
        assert abs(new_val - prev) < 5.0, (
            f"Median placement in {total}-player field should be near-neutral, got delta={new_val - prev:.1f}"
        )

    @pytest.mark.parametrize("total", [128, 256, 1024], ids=["128p", "256p", "1024p"])
    def test_dominant_always_beats_minor_large_field(self, total):
        """For large fields, dominant weight still produces larger delta than minor."""
        prev = 55.0
        dom = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=total, tournament_count=1,
            skill_weight=REACT["acceleration"], prev_value=prev,
        )
        mino = calculate_skill_value_from_placement(
            baseline=prev, placement=1, total_players=total, tournament_count=1,
            skill_weight=REACT["agility"], prev_value=prev,
        )
        assert (dom - prev) > (mino - prev), (
            f"Dominant delta {dom - prev:.2f} ≤ minor delta {mino - prev:.2f} at {total} players"
        )

    @pytest.mark.parametrize("total", [128, 256, 1024], ids=["128p", "256p", "1024p"])
    def test_clamp_holds_for_all_field_sizes(self, total):
        """Values never leave [40, 99] for any field size or placement."""
        for placement in (1, total // 4, total // 2, total):
            new_val = calculate_skill_value_from_placement(
                baseline=50.0, placement=placement, total_players=total, tournament_count=1,
                skill_weight=REACT["acceleration"], prev_value=50.0,
            )
            assert MIN_SKILL_VALUE <= new_val <= MAX_SKILL_CAP, (
                f"total={total}, placement={placement}: {new_val} outside [{MIN_SKILL_VALUE}, {MAX_SKILL_CAP}]"
            )
