"""Adaptive Learning Content Quality Service.

All functions are pure DB-read.  They surface structural quality signals that
help admins decide whether a quiz is ready to publish.

Exposed surface:
  get_quiz_quality_summary(db, quiz_id)  → QuizQualitySummary
  get_global_quality_report(db)          → list[QuizQualitySummary]
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..models.quiz import (
    ContentStatus,
    OptionType,
    Quiz,
    QuizAnswerOption,
    QuizQuestion,
    QuestionMetadata,
)

# Thresholds (tunable; intentionally not env-driven — admin concern, not ops concern)
_MIN_QUESTIONS          = 3
_MIN_OPTIONS_FIXED      = 4   # legacy FIXED questions need exactly 4
_V2_MIN_VARIANTS        = 2
_V2_MIN_DISTRACTORS     = 6
_EXPLANATION_FLOOR      = 0.5  # fraction of questions that must have explanations
_DIFFICULTY_SPREAD_MIN  = 0.1  # std-dev proxy: max−min must exceed this for diversity
_SHORT_QUESTION_CHARS   = 10   # question_text shorter than this is suspicious


@dataclass
class QuestionQualityFlag:
    question_id:   int
    question_text: str
    flags:         list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.flags


@dataclass
class QuizQualitySummary:
    quiz_id:             int
    quiz_title:          str
    content_status:      str
    total_questions:     int   = 0
    flagged_questions:   int   = 0
    has_min_questions:   bool  = False
    explanation_ratio:   float = 0.0
    difficulty_spread:   float = 0.0
    quality_score:       float = 0.0   # 0.0–1.0 composite
    flags:               list[str]                = field(default_factory=list)
    question_flags:      list[QuestionQualityFlag] = field(default_factory=list)

    @property
    def ready_to_publish(self) -> bool:
        return not self.flags and self.flagged_questions == 0


def _options_for_questions(
    db: Session, q_ids: list[int]
) -> dict[int, list[QuizAnswerOption]]:
    if not q_ids:
        return {}
    rows = (
        db.query(QuizAnswerOption)
        .filter(QuizAnswerOption.question_id.in_(q_ids))
        .all()
    )
    result: dict[int, list[QuizAnswerOption]] = {qid: [] for qid in q_ids}
    for opt in rows:
        result[opt.question_id].append(opt)
    return result


def _difficulty_spread(difficulties: list[float]) -> float:
    if len(difficulties) < 2:
        return 0.0
    return round(max(difficulties) - min(difficulties), 4)


def get_quiz_quality_summary(db: Session, quiz_id: int) -> QuizQualitySummary | None:
    """Return quality summary for one quiz, or None if quiz not found."""
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        return None

    questions = (
        db.query(QuizQuestion)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.order_index.asc())
        .all()
    )
    q_ids = [q.id for q in questions]

    opts_map   = _options_for_questions(db, q_ids)
    meta_rows  = (
        db.query(QuestionMetadata)
        .filter(QuestionMetadata.question_id.in_(q_ids))
        .all()
    ) if q_ids else []
    meta_map: dict[int, QuestionMetadata] = {m.question_id: m for m in meta_rows}

    summary = QuizQualitySummary(
        quiz_id        = quiz.id,
        quiz_title     = quiz.title or "",
        content_status = quiz.content_status,
        total_questions = len(questions),
    )

    # Quiz-level flags
    if len(questions) < _MIN_QUESTIONS:
        summary.flags.append(
            f"Too few questions: {len(questions)} < {_MIN_QUESTIONS} required"
        )
    else:
        summary.has_min_questions = True

    with_explanation = sum(1 for q in questions if q.explanation and q.explanation.strip())
    summary.explanation_ratio = round(
        with_explanation / len(questions), 4
    ) if questions else 0.0
    if questions and summary.explanation_ratio < _EXPLANATION_FLOOR:
        summary.flags.append(
            f"Low explanation coverage: {summary.explanation_ratio:.0%} "
            f"(need ≥{_EXPLANATION_FLOOR:.0%})"
        )

    difficulties = [
        float(meta_map[q.id].estimated_difficulty)
        for q in questions
        if q.id in meta_map and meta_map[q.id].estimated_difficulty is not None
    ]
    summary.difficulty_spread = _difficulty_spread(difficulties)
    if len(difficulties) >= 3 and summary.difficulty_spread < _DIFFICULTY_SPREAD_MIN:
        summary.flags.append(
            f"Low difficulty diversity: spread={summary.difficulty_spread:.2f} "
            f"(min {_DIFFICULTY_SPREAD_MIN})"
        )

    # Per-question flags
    for q in questions:
        qf = QuestionQualityFlag(
            question_id   = q.id,
            question_text = (q.question_text or "")[:80],
        )
        opts = opts_map.get(q.id, [])

        if not q.question_text or len(q.question_text.strip()) < _SHORT_QUESTION_CHARS:
            qf.flags.append("Question text too short or missing")

        variants    = [o for o in opts if o.option_type == OptionType.CORRECT_VARIANT]
        distractors = [o for o in opts if o.option_type == OptionType.DISTRACTOR]
        fixed       = [o for o in opts if o.option_type == OptionType.FIXED]
        is_pool     = bool(variants or distractors)

        if is_pool:
            if len(variants) < _V2_MIN_VARIANTS:
                qf.flags.append(
                    f"Pool: only {len(variants)} variant(s), need ≥{_V2_MIN_VARIANTS}"
                )
            if len(distractors) < _V2_MIN_DISTRACTORS:
                qf.flags.append(
                    f"Pool: only {len(distractors)} distractor(s), need ≥{_V2_MIN_DISTRACTORS}"
                )
        else:
            if len(fixed) < _MIN_OPTIONS_FIXED:
                qf.flags.append(
                    f"Fixed: only {len(fixed)} option(s), need {_MIN_OPTIONS_FIXED}"
                )
            correct_count = sum(1 for o in fixed if o.is_correct)
            if correct_count != 1:
                qf.flags.append(
                    f"Fixed: {correct_count} correct option(s) — must be exactly 1"
                )

        if not qf.ok:
            summary.question_flags.append(qf)

    summary.flagged_questions = len(summary.question_flags)

    # Composite quality score (0.0–1.0)
    subscores: list[float] = []
    subscores.append(1.0 if summary.has_min_questions else 0.0)
    subscores.append(min(summary.explanation_ratio / _EXPLANATION_FLOOR, 1.0))
    question_ok_ratio = (
        (len(questions) - summary.flagged_questions) / len(questions)
        if questions else 1.0
    )
    subscores.append(question_ok_ratio)
    summary.quality_score = round(sum(subscores) / len(subscores), 4)

    return summary


def get_global_quality_report(db: Session) -> list[QuizQualitySummary]:
    """Return quality summaries for all non-archived quizzes, ordered by quality_score asc."""
    quizzes = (
        db.query(Quiz)
        .filter(Quiz.content_status != ContentStatus.ARCHIVED.value)
        .order_by(Quiz.id.asc())
        .all()
    )
    results = []
    for quiz in quizzes:
        summary = get_quiz_quality_summary(db, quiz.id)
        if summary:
            results.append(summary)
    results.sort(key=lambda s: s.quality_score)
    return results
