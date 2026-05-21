"""Adaptive Learning Analytics Service.

All functions are pure DB-read; they handle the zero-row state gracefully —
every aggregate returns sensible defaults (0, 0.0, empty list) when no
ALAnswerLog rows exist yet.

Exposed surface:
  get_global_stats(db)                     → GlobalStats
  get_position_heatmap(db, quiz_id)        → list[PositionBucket]
  get_top_distractors(db, quiz_id, limit)  → list[DistractorStat]
  get_session_category_stats(db)           → list[SessionCategoryStat]
  get_per_quiz_question_stats(db, quiz_id) → list[QuestionStat]
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import Float, Integer, func
from sqlalchemy.orm import Session

from ..models.quiz import (
    AdaptiveLearningSession,
    ALAnswerLog,
    QuizAnswerOption,
    QuizQuestion,
    QuestionMetadata,
)


# ── Return types ──────────────────────────────────────────────────────────────

@dataclass
class GlobalStats:
    total_answers:   int   = 0
    correct_count:   int   = 0
    timeout_count:   int   = 0
    success_rate:    float = 0.0
    timeout_rate:    float = 0.0
    avg_time_seconds: float = 0.0
    total_sessions:  int   = 0


@dataclass
class PositionBucket:
    position:      int         # 0=A 1=B 2=C 3=D
    label:         str         # "A".."D"
    total_count:   int   = 0
    correct_count: int   = 0
    pct:           float = 0.0  # fraction of all answers landing here


@dataclass
class DistractorStat:
    option_id:           int
    option_text:         str
    question_id:         int
    question_text_short: str
    chosen_count:        int


@dataclass
class SessionCategoryStat:
    category:      str
    language:      str
    session_count: int   = 0
    avg_score:     float = 0.0
    total_xp:      int   = 0


@dataclass
class QuestionStat:
    question_id:          int
    question_text_short:  str
    total_attempts:       int   = 0
    correct_count:        int   = 0
    timeout_count:        int   = 0
    success_rate:         float = 0.0
    avg_time_seconds:     float = 0.0
    estimated_difficulty: float = 0.5


# ── Internal helper ───────────────────────────────────────────────────────────

def _pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


# ── Public API ────────────────────────────────────────────────────────────────

def get_global_stats(db: Session) -> GlobalStats:
    """Aggregate across all ALAnswerLog rows."""
    row = db.query(
        func.count(ALAnswerLog.id).label("total"),
        func.sum(func.cast(ALAnswerLog.is_correct, Integer)).label("correct"),
        func.sum(func.cast(ALAnswerLog.timed_out,  Integer)).label("timeouts"),
        func.avg(ALAnswerLog.time_spent_seconds).label("avg_time"),
    ).first()

    total    = int(row.total    or 0)
    correct  = int(row.correct  or 0)
    timeouts = int(row.timeouts or 0)
    avg_time = float(row.avg_time or 0.0)

    sessions = int(db.query(func.count(AdaptiveLearningSession.id)).scalar() or 0)

    return GlobalStats(
        total_answers    = total,
        correct_count    = correct,
        timeout_count    = timeouts,
        success_rate     = _pct(correct, total),
        timeout_rate     = _pct(timeouts, total),
        avg_time_seconds = round(avg_time, 2),
        total_sessions   = sessions,
    )


def get_position_heatmap(
    db: Session,
    quiz_id: int | None = None,
) -> list[PositionBucket]:
    """Answer count per correct_option_position (0..3).

    Always returns 4 buckets; quiz_id restricts to one quiz's questions.
    """
    q = (
        db.query(
            ALAnswerLog.correct_option_position.label("pos"),
            func.count(ALAnswerLog.id).label("cnt"),
            func.sum(func.cast(ALAnswerLog.is_correct, Integer)).label("corr"),
        )
        .filter(ALAnswerLog.correct_option_position.isnot(None))
    )
    if quiz_id is not None:
        q = q.join(QuizQuestion, QuizQuestion.id == ALAnswerLog.question_id).filter(
            QuizQuestion.quiz_id == quiz_id
        )
    rows = q.group_by(ALAnswerLog.correct_option_position).all()

    counts: dict[int, tuple[int, int]] = {
        r.pos: (int(r.cnt), int(r.corr or 0)) for r in rows
    }
    total = sum(c for c, _ in counts.values())

    return [
        PositionBucket(
            position      = pos,
            label         = chr(ord("A") + pos),
            total_count   = counts.get(pos, (0, 0))[0],
            correct_count = counts.get(pos, (0, 0))[1],
            pct           = _pct(counts.get(pos, (0, 0))[0], total),
        )
        for pos in range(4)
    ]


def get_top_distractors(
    db: Session,
    quiz_id: int | None = None,
    limit: int = 10,
) -> list[DistractorStat]:
    """Options most frequently chosen when the answer was wrong."""
    q = (
        db.query(
            ALAnswerLog.selected_option_id.label("opt_id"),
            QuizAnswerOption.option_text.label("opt_text"),
            ALAnswerLog.question_id.label("qid"),
            QuizQuestion.question_text.label("q_text"),
            func.count(ALAnswerLog.id).label("cnt"),
        )
        .join(QuizAnswerOption, QuizAnswerOption.id == ALAnswerLog.selected_option_id)
        .join(QuizQuestion,     QuizQuestion.id     == ALAnswerLog.question_id)
        .filter(ALAnswerLog.is_correct == sa.false())
        .filter(ALAnswerLog.selected_option_id.isnot(None))
    )
    if quiz_id is not None:
        q = q.filter(QuizQuestion.quiz_id == quiz_id)

    rows = (
        q.group_by(
            ALAnswerLog.selected_option_id,
            QuizAnswerOption.option_text,
            ALAnswerLog.question_id,
            QuizQuestion.question_text,
        )
        .order_by(func.count(ALAnswerLog.id).desc())
        .limit(limit)
        .all()
    )

    return [
        DistractorStat(
            option_id           = r.opt_id,
            option_text         = r.opt_text or "",
            question_id         = r.qid,
            question_text_short = (r.q_text or "")[:80],
            chosen_count        = int(r.cnt),
        )
        for r in rows
    ]


def get_session_category_stats(db: Session) -> list[SessionCategoryStat]:
    """Per-category / per-language aggregate from AdaptiveLearningSession."""
    rows = (
        db.query(
            AdaptiveLearningSession.category.label("cat"),
            AdaptiveLearningSession.language.label("lang"),
            func.count(AdaptiveLearningSession.id).label("sess"),
            func.avg(
                func.cast(AdaptiveLearningSession.questions_correct, Float)
                / func.nullif(
                    func.cast(AdaptiveLearningSession.questions_presented, Float), 0
                )
            ).label("avg_score"),
            func.sum(AdaptiveLearningSession.xp_earned).label("total_xp"),
        )
        .group_by(AdaptiveLearningSession.category, AdaptiveLearningSession.language)
        .order_by(AdaptiveLearningSession.category, AdaptiveLearningSession.language)
        .all()
    )

    return [
        SessionCategoryStat(
            category      = r.cat.value if hasattr(r.cat, "value") else str(r.cat),
            language      = r.lang or "en",
            session_count = int(r.sess),
            avg_score     = round(float(r.avg_score or 0.0), 4),
            total_xp      = int(r.total_xp or 0),
        )
        for r in rows
    ]


def get_per_quiz_question_stats(
    db: Session,
    quiz_id: int,
) -> list[QuestionStat]:
    """Per-question breakdown for one quiz; zero-attempt questions included."""
    questions = (
        db.query(QuizQuestion)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.order_index.asc())
        .all()
    )
    if not questions:
        return []

    q_ids = [q.id for q in questions]

    agg_rows = (
        db.query(
            ALAnswerLog.question_id.label("qid"),
            func.count(ALAnswerLog.id).label("total"),
            func.sum(func.cast(ALAnswerLog.is_correct, Integer)).label("correct"),
            func.sum(func.cast(ALAnswerLog.timed_out,  Integer)).label("timeouts"),
            func.avg(ALAnswerLog.time_spent_seconds).label("avg_time"),
        )
        .filter(ALAnswerLog.question_id.in_(q_ids))
        .group_by(ALAnswerLog.question_id)
        .all()
    )
    agg: dict[int, tuple[int, int, int, float]] = {
        r.qid: (int(r.total), int(r.correct or 0), int(r.timeouts or 0), float(r.avg_time or 0.0))
        for r in agg_rows
    }

    meta_rows = (
        db.query(QuestionMetadata.question_id, QuestionMetadata.estimated_difficulty)
        .filter(QuestionMetadata.question_id.in_(q_ids))
        .all()
    )
    meta: dict[int, float] = {r.question_id: float(r.estimated_difficulty or 0.5) for r in meta_rows}

    result = []
    for q in questions:
        total, correct, timeouts, avg_time = agg.get(q.id, (0, 0, 0, 0.0))
        result.append(QuestionStat(
            question_id          = q.id,
            question_text_short  = (q.question_text or "")[:80],
            total_attempts       = total,
            correct_count        = correct,
            timeout_count        = timeouts,
            success_rate         = _pct(correct, total),
            avg_time_seconds     = round(avg_time, 2),
            estimated_difficulty = meta.get(q.id, 0.5),
        ))
    return result
