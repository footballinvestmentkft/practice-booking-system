"""
Virtual Training Metrics — Phase 2.2 (Performance-based Skill Delta)

Three-layer pipeline replacing the old XP-based compute_skill_deltas():

  VTSignalExtractor  → normalised signals from aggregate payload fields
  VTSkillScorer      → per-skill score 0–1 (or negative) from signals
  VTDeltaComputer    → additive skill deltas from scores (positive or negative)

Design constraints:
  - Works from aggregate DB fields alone; raw_metrics enriches but is optional
  - Malformed or missing raw_metrics → falls back to aggregate-only path
  - XP computation stays in VirtualTrainingService (fully decoupled)
  - Immutability: past attempt rows keep their old skill_deltas unchanged

Scoring model (Phase 2.4 — bidirectional deltas):
  Scorers return a value in (-∞, 1.0].  The lower clamp was intentionally
  removed so that very weak performance can produce a negative delta.

  Delta model uses a neutral zone:
    score ≥ NEUTRAL_THRESHOLD (0.45)  → positive delta (performance × multiplier)
    0 ≤ score < NEUTRAL_THRESHOLD     → small negative delta (below-threshold × NEG_SCALE × multiplier)
    score < 0                         → negative delta (raw score × NEG_SCALE × multiplier)

  NEG_SCALE = 0.5 keeps the negative direction half as intense as positive.
  A per-skill per-user per-day cap of −0.5 limits cumulative daily loss.

  reactions and anticipation scorers are always ≥ 0 (component structure),
  so they never produce negative deltas in practice.

  Invalid attempts and attempts 4+ always produce zero skill deltas.

  Phase 2.4 — Protocol difficulty multiplier:
  The effective delta multiplier is compound:
    effective = xp_multiplier × protocol_difficulty_multiplier
  protocol_difficulty_multiplier is self-declared (1.00–1.25).
  XP and score_normalized are NOT affected — only skill deltas.

Calibration note:
  At perfect performance (score=1.0, attempt_index=1) the total delta across
  all skills equals base_xp / _DEFAULT_XP_PER_POINT — identical to the old
  formula maximum. Poor performance now yields negative deltas per skill.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_DEFAULT_XP_PER_POINT: int = 10   # mirrors segment_reward_service constant

# ── Bidirectional delta calibration constants ─────────────────────────────────
# score below this threshold → negative delta instead of positive
_NEUTRAL_THRESHOLD: float = 0.45
# negative delta intensity relative to an equivalent positive (50%)
_NEG_SCALE:         float = 0.50
# maximum cumulative negative delta per skill per user per UTC calendar day
_DAILY_NEG_CAP:     float = -0.50


# ── Signal dataclass ──────────────────────────────────────────────────────────

@dataclass
class VTSignals:
    """Normalised performance signals for one attempt."""
    hit_rate:        float          # correct_count / stimuli_count
    wrong_rate:      float          # wrong_click_count / stimuli_count
    miss_rate:       float          # error_count / stimuli_count
    speed_score:     float          # 0–1; 1 = near-instant, 0 = at/above window limit
    completion_rate: float          # stimuli_count / expected_total
    avg_reaction_ms: Optional[float] = None
    per_phase:       Optional[list]  = None   # raw_metrics.per_phase if available
    late_click_rate:               float = 0.0   # late clicks / stimuli (v2+)
    late_go_rate:                  float = 0.0   # late GO responses / stimuli (v2+ GNG only)
    late_nogo_rate:                float = 0.0   # late NO-GO false alarms / stimuli (v2+ GNG only)
    protocol_difficulty_multiplier: float = 1.0  # self-declared; 1.00=free, max 1.25 (v3+)
    difficulty_multiplier:          float = 1.0  # TT difficulty level multiplier (v3+); max 2.50


# ── Layer 1: Signal extraction ────────────────────────────────────────────────

class VTSignalExtractor:
    """Extract VTSignals from a raw submit-payload dict."""

    @staticmethod
    def extract(data: dict, phase_config: list[dict]) -> VTSignals:
        """
        Build VTSignals from aggregate payload fields.

        Graceful defaults for missing or None values:
          - Missing counts              → 0
          - Missing/zero stimuli_count  → inferred from phase_config sum (or 36)
          - Missing avg_reaction_ms     → speed_score = 0.5 (neutral)
        """
        expected_total = (
            sum(p.get("stimuli", 0) for p in phase_config) if phase_config else 0
        ) or 36

        stimuli = int(data.get("stimuli_count") or expected_total)
        correct = int(data.get("correct_count")     or 0)
        wrong   = int(data.get("wrong_click_count") or 0)
        misses  = int(data.get("error_count")       or 0)

        safe = max(stimuli, 1)
        hit_rate    = max(0.0, min(1.0, correct / safe))
        wrong_rate  = max(0.0, min(1.0, wrong   / safe))
        miss_rate   = max(0.0, min(1.0, misses  / safe))
        completion  = max(0.0, min(1.0, stimuli / expected_total))

        avg_ms = data.get("avg_reaction_ms")
        if avg_ms is not None:
            phase_avg_window = (
                sum(p.get("window_ms", 3000) for p in phase_config) / len(phase_config)
                if phase_config else 3067.0
            )
            speed_score = max(0.0, min(1.0, 1.0 - float(avg_ms) / phase_avg_window))
        else:
            speed_score = 0.5  # neutral when RT not recorded (non-RT game path)

        # Enrich with per-phase and late_summary from raw_metrics if structurally valid
        per_phase = None
        late_click_rate = 0.0
        late_go_rate    = 0.0
        late_nogo_rate  = 0.0
        raw = data.get("raw_metrics")
        if isinstance(raw, dict) and raw.get("v", 1) >= 1:
            per_phase = raw.get("per_phase")
        if isinstance(raw, dict) and raw.get("v", 1) >= 2:
            ls = raw.get("late_summary") or {}
            late_click_rate = max(0.0, min(1.0, int(ls.get("late_click_count") or 0) / safe))
            late_go_rate    = max(0.0, min(1.0, int(ls.get("late_go_count")    or 0) / safe))
            late_nogo_rate  = max(0.0, min(1.0, int(ls.get("late_no_go_count") or 0) / safe))

        protocol_difficulty_multiplier = 1.0
        difficulty_multiplier          = 1.0
        if isinstance(raw, dict) and raw.get("v", 1) >= 3:
            hp = raw.get("hand_profile") or {}
            try:
                pdm = float(hp.get("protocol_difficulty_multiplier", 1.0))
                protocol_difficulty_multiplier = max(1.0, min(1.25, pdm))
            except (TypeError, ValueError):
                protocol_difficulty_multiplier = 1.0
            try:
                dm = float(raw.get("difficulty_multiplier", 1.0))
                difficulty_multiplier = max(1.0, min(2.50, dm))
            except (TypeError, ValueError):
                difficulty_multiplier = 1.0

        return VTSignals(
            hit_rate=hit_rate,
            wrong_rate=wrong_rate,
            miss_rate=miss_rate,
            speed_score=speed_score,
            completion_rate=completion,
            avg_reaction_ms=float(avg_ms) if avg_ms is not None else None,
            per_phase=per_phase,
            late_click_rate=late_click_rate,
            late_go_rate=late_go_rate,
            late_nogo_rate=late_nogo_rate,
            protocol_difficulty_multiplier=protocol_difficulty_multiplier,
            difficulty_multiplier=difficulty_multiplier,
        )


# ── Layer 2: Per-skill scoring ────────────────────────────────────────────────

class VTSkillScorer:
    """Compute a 0–1 performance score for each skill from VTSignals."""

    @staticmethod
    def score_reactions(signals: VTSignals) -> float:
        """
        Reaction speed + accuracy blend.
          speed_score = 1 − avg_rt / phase_window_avg  (already in VTSignals)
          reactions   = 0.65 × speed_score + 0.35 × hit_rate
        """
        score = 0.65 * signals.speed_score + 0.35 * signals.hit_rate
        return max(0.0, min(1.0, score))

    @staticmethod
    def score_decisions(signals: VTSignals) -> float:
        """
        Decision accuracy under Stroop interference.
        Wrong clicks penalised 1.5× (active error) vs misses (omission).
          decisions = min(1.0, hit_rate − 1.5 × wrong_rate)
        Range: (−∞, 1.0] — negative when false alarms dominate hits.
        """
        return min(1.0, signals.hit_rate - 1.5 * signals.wrong_rate)

    @staticmethod
    def score_concentration(signals: VTSignals) -> float:
        """
        Sustained-attention proxy.
        Late clicks (post-window taps) are partial attention lapses: penalised at 0.8×
        instead of 2× for a full miss.  Excludes late NO-GO (composure domain, not attention).
          late_for_conc = max(0, late_click_rate − late_nogo_rate)
          pure_miss     = max(0, miss_rate − late_for_conc)
          concentration = min(1.0, 1 − 2×pure_miss − 0.8×late_for_conc)
        Range: (−∞, 1.0].  v1 payloads: late_* = 0.0 → identical to old formula.
        """
        late_for_conc = max(0.0, signals.late_click_rate - signals.late_nogo_rate)
        pure_miss     = max(0.0, signals.miss_rate - late_for_conc)
        return min(1.0, 1.0 - 2.0 * pure_miss - 0.8 * late_for_conc)

    @staticmethod
    def score_anticipation(signals: VTSignals) -> float:
        """
        Proactive engagement.

        Phase 2.2 (Option A): completion × accuracy proxy.
        Phase 2.3 upgrade (auto-activates when per_phase[2] available):
          weight phase-3 accuracy 60% — harder phase, better signal.
        """
        if signals.per_phase and len(signals.per_phase) >= 3:
            p3 = signals.per_phase[2]
            p3_stim = p3.get("stimuli", 0)
            if p3_stim > 0:
                p3_acc = max(0.0, min(1.0, p3.get("correct", 0) / p3_stim))
                score = 0.4 * signals.completion_rate * signals.hit_rate + 0.6 * p3_acc
                return max(0.0, min(1.0, score))

        # Option A fallback
        return max(0.0, min(1.0, signals.completion_rate * signals.hit_rate))

    @staticmethod
    def score_composure(signals: VTSignals) -> float:
        """
        Impulse control — inverse of false alarm rate.

        In Go/No-Go: wrong_click_count = false alarms (clicks on NO-GO stimuli).
        Commission errors (acting when you should not) are the primary failure
        mode of a Go/No-Go task, so penalty weight mirrors score_decisions.

          composure = min(1.0, 1.0 − 1.5 × wrong_rate − 0.3 × late_nogo_rate)

        wrong_rate = wrong_click_count / stimuli_count
        late_nogo_rate: late clicks after a NO-GO window (impulse-control failure, 0.3× penalty).
        At zero false alarms and zero late NO-GO: composure = 1.0 (perfect).
        Range: (−∞, 1.0].  v1 payloads: late_nogo_rate = 0.0 → identical to old formula.
        """
        return min(1.0, 1.0 - 1.5 * signals.wrong_rate - 0.3 * signals.late_nogo_rate)

    @staticmethod
    def score_tactical_awareness(signals: VTSignals) -> float:
        """
        Visuospatial working memory span (Memory Sequence primary scorer).

        Measures how much of the shown sequence the player correctly recalled,
        with emphasis on completing longer, harder sequences (Phase 3).

        Aggregate path (always available):
          tactical_awareness = 0.65 × hit_rate + 0.35 × completion_rate

          hit_rate        = correct_positions / total_expected_positions
          completion_rate = positions_attempted / total_expected_positions

        Per-phase upgrade (auto-activates when per_phase[2] present):
          Phase 3 (sequence_length=7) carries more signal about span capacity.
            score = 0.4 × completion_rate × hit_rate  +  0.6 × phase_3_accuracy

          Reads 'correct_positions'/'total_positions' (Memory Sequence format);
          falls back to 'correct'/'stimuli' for CR/NCC legacy compatibility.

        Range: [0, 1] — always non-negative. Wrong positions already reduce
        hit_rate; no additional wrong-rate penalty here.
        """
        if signals.per_phase and len(signals.per_phase) >= 3:
            p3 = signals.per_phase[2]
            p3_total = p3.get("total_positions", 0) or p3.get("stimuli", 0)
            if p3_total > 0:
                p3_correct = p3.get("correct_positions", 0) or p3.get("correct", 0)
                p3_acc = max(0.0, min(1.0, p3_correct / p3_total))
                score = 0.4 * signals.completion_rate * signals.hit_rate + 0.6 * p3_acc
                return max(0.0, min(1.0, score))
        return max(0.0, min(1.0, 0.65 * signals.hit_rate + 0.35 * signals.completion_rate))

    @staticmethod
    def score_all(signals: VTSignals, skill_targets: dict[str, float]) -> dict[str, float]:
        """
        Score every skill present in skill_targets.
        Known keys dispatch to dedicated scorers.
        Unknown keys receive the mean of the known scores (future-proofing).
        """
        _scorers = {
            "reactions":          VTSkillScorer.score_reactions,
            "decisions":          VTSkillScorer.score_decisions,
            "concentration":      VTSkillScorer.score_concentration,
            "anticipation":       VTSkillScorer.score_anticipation,
            "composure":          VTSkillScorer.score_composure,
            "tactical_awareness": VTSkillScorer.score_tactical_awareness,
        }
        known = {k: fn(signals) for k, fn in _scorers.items()}
        fallback = sum(known.values()) / len(known) if known else 0.5

        return {
            skill: (_scorers[skill](signals) if skill in _scorers else fallback)
            for skill in skill_targets
        }


# ── Layer 3: Delta computation ────────────────────────────────────────────────

class VTDeltaComputer:
    """Convert per-skill scores to additive skill deltas (positive or negative)."""

    @staticmethod
    def compute(
        scores:             dict[str, float],
        skill_targets:      dict[str, float],
        base_xp:            int,
        multiplier:         float,
        existing_neg_today: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """
        Compute per-skill additive deltas from performance scores.

        Positive delta formula (score ≥ NEUTRAL_THRESHOLD):
            delta = score × (weight / Σweights) × base_max × multiplier

        Negative delta formula (score < NEUTRAL_THRESHOLD):
            score ≥ 0: delta = (score − NEUTRAL_THRESHOLD) × NEG_SCALE × unit × multiplier
            score < 0: delta = score × NEG_SCALE × unit × multiplier
        where unit = (weight / Σweights) × base_max

        The multiplier applies to both directions (attempt-index fairness).
        A per-skill daily cap of _DAILY_NEG_CAP (−0.5) limits cumulative loss.

        existing_neg_today: {skill: sum_of_neg_deltas_already_stored_today}
          — passed by VirtualTrainingService.record_attempt() before writing.
          — omit (None) in pure-formula unit tests.

        Calibration: at score=1.0, multiplier=1.0, sum_weights=1.0 the total
        delta equals base_xp / _DEFAULT_XP_PER_POINT — same ceiling as before.
        """
        if not scores or multiplier <= 0:
            return {}

        sum_weights = sum(skill_targets.get(s, 0.0) for s in scores)
        if sum_weights <= 0:
            return {}

        base_max        = base_xp / _DEFAULT_XP_PER_POINT
        existing_neg    = existing_neg_today or {}

        result: dict[str, float] = {}
        for skill, score in scores.items():
            weight = skill_targets.get(skill, 0.0)
            unit   = (weight / sum_weights) * base_max  # max per-skill at score=1.0

            if score >= _NEUTRAL_THRESHOLD:
                delta = round(score * unit * multiplier, 4)
            elif score >= 0.0:
                # below neutral, still non-negative raw score → small negative
                delta = round((score - _NEUTRAL_THRESHOLD) * _NEG_SCALE * unit * multiplier, 4)
            else:
                # raw score below zero (e.g. decisions = hit_rate − 1.5×wrong_rate < 0)
                delta = round(score * _NEG_SCALE * unit * multiplier, 4)

            # Apply daily negative cap (write-time enforcement)
            if delta < 0:
                current_neg = existing_neg.get(skill, 0.0)
                if current_neg <= _DAILY_NEG_CAP:
                    delta = 0.0  # cap already reached today
                else:
                    delta = max(delta, _DAILY_NEG_CAP - current_neg)

            if delta != 0:
                result[skill] = delta

        return result


# ── Entry point used by VirtualTrainingService ────────────────────────────────

def compute_vt_skill_deltas(
    data:               dict,
    game,                           # VirtualTrainingGame ORM object
    multiplier:         float,
    existing_neg_today: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Full three-layer pipeline: extract → score → compute.

    Called from VirtualTrainingService.record_attempt() instead of the old
    XP-routed compute_skill_deltas() from segment_reward_service.
    Returns {} on any guard condition (no targets, zero multiplier, etc.).

    existing_neg_today: today's already-stored negative deltas per skill for
    this user, queried by record_attempt() before calling this function.
    Pass None (default) in unit tests that test the formula in isolation.
    """
    skill_targets = game.skill_targets or {}
    if not skill_targets or multiplier <= 0:
        return {}

    cfg          = game.config or {}
    phase_config = cfg.get("phases") or [] if isinstance(cfg, dict) else []

    signals = VTSignalExtractor.extract(data, phase_config)
    scores  = VTSkillScorer.score_all(signals, skill_targets)
    return VTDeltaComputer.compute(
        scores, skill_targets, game.base_xp, multiplier, existing_neg_today
    )
