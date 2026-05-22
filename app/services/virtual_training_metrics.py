"""
Virtual Training Metrics — Phase 2.2 (Performance-based Skill Delta)

Three-layer pipeline replacing the old XP-based compute_skill_deltas():

  VTSignalExtractor  → normalised signals from aggregate payload fields
  VTSkillScorer      → per-skill score 0–1 from signals
  VTDeltaComputer    → additive skill deltas from scores

Design constraints:
  - Works from aggregate DB fields alone; raw_metrics enriches but is optional
  - Malformed or missing raw_metrics → falls back to aggregate-only path
  - XP computation stays in VirtualTrainingService (fully decoupled)
  - Immutability: past attempt rows keep their old skill_deltas unchanged

Calibration note:
  At perfect performance (score=1.0, attempt_index=1) the total delta across
  all skills equals base_xp / _DEFAULT_XP_PER_POINT — identical to the old
  formula maximum. Poor performance now yields proportionally lower deltas.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_DEFAULT_XP_PER_POINT: int = 10   # mirrors segment_reward_service constant


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

        # Enrich with per-phase from raw_metrics if structurally valid
        per_phase = None
        raw = data.get("raw_metrics")
        if isinstance(raw, dict) and raw.get("v") == 1:
            per_phase = raw.get("per_phase")

        return VTSignals(
            hit_rate=hit_rate,
            wrong_rate=wrong_rate,
            miss_rate=miss_rate,
            speed_score=speed_score,
            completion_rate=completion,
            avg_reaction_ms=float(avg_ms) if avg_ms is not None else None,
            per_phase=per_phase,
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
          decisions = clamp(hit_rate − 1.5 × wrong_rate, 0, 1)
        """
        score = signals.hit_rate - 1.5 * signals.wrong_rate
        return max(0.0, min(1.0, score))

    @staticmethod
    def score_concentration(signals: VTSignals) -> float:
        """
        Sustained-attention proxy.
        Misses penalised 2× because ignoring a stimulus is a full attention lapse.
          concentration = clamp(1 − 2 × miss_rate, 0, 1)
        """
        score = 1.0 - 2.0 * signals.miss_rate
        return max(0.0, min(1.0, score))

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
    def score_all(signals: VTSignals, skill_targets: dict[str, float]) -> dict[str, float]:
        """
        Score every skill present in skill_targets.
        Known keys dispatch to dedicated scorers.
        Unknown keys receive the mean of the four known scores (future-proofing).
        """
        _scorers = {
            "reactions":     VTSkillScorer.score_reactions,
            "decisions":     VTSkillScorer.score_decisions,
            "concentration": VTSkillScorer.score_concentration,
            "anticipation":  VTSkillScorer.score_anticipation,
        }
        known = {k: fn(signals) for k, fn in _scorers.items()}
        fallback = sum(known.values()) / len(known) if known else 0.5

        return {
            skill: (_scorers[skill](signals) if skill in _scorers else fallback)
            for skill in skill_targets
        }


# ── Layer 3: Delta computation ────────────────────────────────────────────────

class VTDeltaComputer:
    """Convert per-skill scores to additive skill deltas."""

    @staticmethod
    def compute(
        scores:        dict[str, float],
        skill_targets: dict[str, float],
        base_xp:       int,
        multiplier:    float,
    ) -> dict[str, float]:
        """
        Compute per-skill additive deltas from performance scores.

        Formula:
            delta(skill) = score(skill)
                           × (weight(skill) / Σ weights)
                           × (base_xp / _DEFAULT_XP_PER_POINT)
                           × multiplier

        Calibration: at score=1.0, multiplier=1.0, sum_weights=1.0 the total
        delta equals base_xp / _DEFAULT_XP_PER_POINT — same ceiling as the
        old XP-only formula, now scaled by actual performance.
        """
        if not scores or multiplier <= 0:
            return {}

        sum_weights = sum(skill_targets.get(s, 0.0) for s in scores)
        if sum_weights <= 0:
            return {}

        base_max = base_xp / _DEFAULT_XP_PER_POINT

        result: dict[str, float] = {}
        for skill, score in scores.items():
            weight = skill_targets.get(skill, 0.0)
            delta  = round(score * (weight / sum_weights) * base_max * multiplier, 4)
            if delta > 0:
                result[skill] = delta

        return result


# ── Entry point used by VirtualTrainingService ────────────────────────────────

def compute_vt_skill_deltas(
    data:       dict,
    game,              # VirtualTrainingGame ORM object
    multiplier: float,
) -> dict[str, float]:
    """
    Full three-layer pipeline: extract → score → compute.

    Called from VirtualTrainingService.record_attempt() instead of the old
    XP-routed compute_skill_deltas() from segment_reward_service.
    Returns {} on any guard condition (no targets, zero multiplier, etc.).
    """
    skill_targets = game.skill_targets or {}
    if not skill_targets or multiplier <= 0:
        return {}

    cfg          = game.config or {}
    phase_config = cfg.get("phases") or [] if isinstance(cfg, dict) else []

    signals = VTSignalExtractor.extract(data, phase_config)
    scores  = VTSkillScorer.score_all(signals, skill_targets)
    return VTDeltaComputer.compute(scores, skill_targets, game.base_xp, multiplier)
