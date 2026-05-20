"""Adaptive Learning Question & Quiz Editor Service.

Handles:
- Quiz content_status state machine (DRAFT / PUBLISHED / ARCHIVED)
- is_active synchronisation (backward-compat)
- Per-question text + metadata editing
- Per-option text editing with FIXED/pool-mode protection
- Pool-size floor enforcement (v2.0: min 2 variants, min 6 distractors)
- is_correct mutation guard for PUBLISHED quizzes
- New question / new option creation (with type rules)
- Question deletion (DRAFT-only, min-1 guard)

TECH DEBT NOTE: Edits to PUBLISHED-status quiz content (question text,
explanation, metadata) take immediate effect without a separate versioning
layer. Until an admin audit-log table is wired, callers should record
changes at the route level (operator + timestamp). A full version-history
table (quiz_question_versions) is the clean long-term solution.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..models.quiz import (
    ContentStatus,
    OptionType,
    Quiz,
    QuizAnswerOption,
    QuizDifficulty,
    QuizQuestion,
    QuestionMetadata,
    QuestionType,
)


# ── Exceptions ────────────────────────────────────────────────────────────────

class EditorError(Exception):
    """Business-rule violation in the editor."""


class ProtectedFieldError(EditorError):
    """Attempted mutation of a field that must not change in this context."""


class ValidationError(EditorError):
    """Value failed schema or pool-size validation."""


class InvalidTransitionError(EditorError):
    """Attempted content_status transition that is not allowed."""


# ── Payload dataclasses ───────────────────────────────────────────────────────

@dataclass
class QuestionEditPayload:
    question_text: str
    explanation: str
    estimated_difficulty: float   # 0.0–1.0
    cognitive_load: float         # 0.0–1.0
    average_time_seconds: float   # > 0
    concept_tags: list[str] = field(default_factory=list)


@dataclass
class OptionEditPayload:
    option_text: str
    # is_correct is intentionally absent — use swap_correct_option() instead


@dataclass
class OptionCreatePayload:
    option_text: str
    is_correct: bool
    option_type: OptionType


@dataclass
class QuestionCreatePayload:
    question_text: str
    explanation: str
    question_type: QuestionType
    estimated_difficulty: float
    cognitive_load: float
    average_time_seconds: float
    concept_tags: list[str] = field(default_factory=list)
    # v1.0 FIXED options  (used when option_type not given)
    fixed_options: list[OptionCreatePayload] = field(default_factory=list)
    # v2.0 pool options
    correct_variants: list[str] = field(default_factory=list)
    distractor_pool: list[str] = field(default_factory=list)


# ── Internal constants ────────────────────────────────────────────────────────

_V2_MIN_VARIANTS    = 2
_V2_MIN_DISTRACTORS = 6
_V1_MIN_OPTIONS     = 4


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sync_is_active(quiz: Quiz) -> None:
    """Keep is_active in sync with content_status."""
    quiz.is_active = (quiz.content_status == ContentStatus.PUBLISHED.value)


def _pool_mode(options: list[QuizAnswerOption]) -> bool:
    """True if question uses pool mode (CORRECT_VARIANT + enough distractors)."""
    variants    = [o for o in options if o.option_type == OptionType.CORRECT_VARIANT]
    distractors = [o for o in options if o.option_type == OptionType.DISTRACTOR]
    return bool(variants) and len(distractors) >= 3


def _require_quiz(db, quiz_id: int) -> Quiz:
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise EditorError(f"Quiz {quiz_id} not found")
    return quiz


def _require_question(db, question_id: int, quiz_id: int | None = None) -> QuizQuestion:
    q = db.query(QuizQuestion).filter(QuizQuestion.id == question_id).first()
    if not q:
        raise EditorError(f"Question {question_id} not found")
    if quiz_id is not None and q.quiz_id != quiz_id:
        raise EditorError(f"Question {question_id} does not belong to quiz {quiz_id}")
    return q


def _require_option(db, option_id: int, question_id: int | None = None) -> QuizAnswerOption:
    o = db.query(QuizAnswerOption).filter(QuizAnswerOption.id == option_id).first()
    if not o:
        raise EditorError(f"Option {option_id} not found")
    if question_id is not None and o.question_id != question_id:
        raise EditorError(f"Option {option_id} does not belong to question {question_id}")
    return o


def _require_metadata(db, question_id: int) -> QuestionMetadata | None:
    return db.query(QuestionMetadata).filter(
        QuestionMetadata.question_id == question_id
    ).first()


def _validate_float_field(name: str, value: float, lo: float = 0.0, hi: float = 1.0) -> None:
    if not isinstance(value, (int, float)) or not (lo <= float(value) <= hi):
        raise ValidationError(
            f"{name} must be between {lo} and {hi} (got {value!r})"
        )


def _validate_positive(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or float(value) <= 0:
        raise ValidationError(f"{name} must be > 0 (got {value!r})")


# ── State machine ─────────────────────────────────────────────────────────────

def publish_quiz(db, quiz_id: int) -> Quiz:
    """DRAFT → PUBLISHED.  ARCHIVED → PUBLISHED is blocked."""
    quiz = _require_quiz(db, quiz_id)
    if quiz.content_status == ContentStatus.ARCHIVED.value:
        raise InvalidTransitionError(
            "Archived quizzes cannot be re-published. Create a new quiz instead."
        )
    if quiz.content_status == ContentStatus.PUBLISHED.value:
        return quiz  # idempotent
    quiz.content_status = ContentStatus.PUBLISHED.value
    _sync_is_active(quiz)
    db.commit()
    return quiz


def draft_quiz(db, quiz_id: int) -> Quiz:
    """PUBLISHED → DRAFT (takes content offline immediately)."""
    quiz = _require_quiz(db, quiz_id)
    if quiz.content_status == ContentStatus.ARCHIVED.value:
        raise InvalidTransitionError("Archived quizzes cannot be moved to draft.")
    if quiz.content_status == ContentStatus.DRAFT.value:
        return quiz  # idempotent
    quiz.content_status = ContentStatus.DRAFT.value
    _sync_is_active(quiz)
    db.commit()
    return quiz


def archive_quiz(db, quiz_id: int) -> Quiz:
    """Any → ARCHIVED (irreversible soft-delete)."""
    quiz = _require_quiz(db, quiz_id)
    if quiz.content_status == ContentStatus.ARCHIVED.value:
        return quiz  # idempotent
    quiz.content_status = ContentStatus.ARCHIVED.value
    _sync_is_active(quiz)
    db.commit()
    return quiz


# ── Question editing ──────────────────────────────────────────────────────────

def update_question(
    db,
    question_id: int,
    payload: QuestionEditPayload,
    *,
    quiz_id: int | None = None,
) -> QuizQuestion:
    """Update question text, explanation, and metadata.

    Permitted regardless of quiz content_status — see TECH DEBT NOTE above.
    Returns the updated QuizQuestion.
    """
    _validate_float_field("estimated_difficulty", payload.estimated_difficulty)
    _validate_float_field("cognitive_load", payload.cognitive_load)
    _validate_positive("average_time_seconds", payload.average_time_seconds)
    if not payload.question_text.strip():
        raise ValidationError("question_text must be non-empty")
    if not payload.explanation.strip():
        raise ValidationError("explanation must be non-empty")

    question = _require_question(db, question_id, quiz_id)
    question.question_text = payload.question_text.strip()
    question.explanation   = payload.explanation.strip()

    meta = _require_metadata(db, question_id)
    if meta:
        meta.estimated_difficulty = float(payload.estimated_difficulty)
        meta.cognitive_load       = float(payload.cognitive_load)
        meta.average_time_seconds = float(payload.average_time_seconds)
        meta.concept_tags = (
            json.dumps(payload.concept_tags)
            if isinstance(payload.concept_tags, list)
            else payload.concept_tags
        )
    db.commit()
    return question


# ── Option editing ────────────────────────────────────────────────────────────

def update_option(
    db,
    option_id: int,
    payload: OptionEditPayload,
    *,
    question_id: int | None = None,
) -> QuizAnswerOption:
    """Update option text only.

    option_type is never modified here.
    is_correct is never modified here (use swap_correct_option for v1.0 FIXED).
    """
    if not payload.option_text.strip():
        raise ValidationError("option_text must be non-empty")
    option = _require_option(db, option_id, question_id)
    option.option_text = payload.option_text.strip()
    db.commit()
    return option


def swap_correct_option(
    db,
    question_id: int,
    new_correct_option_id: int,
) -> QuizQuestion:
    """Change which FIXED option is marked correct (v1.0 questions only).

    Blocked for PUBLISHED quizzes — put quiz in DRAFT first.
    Blocked for pool-mode questions (CORRECT_VARIANT / DISTRACTOR architecture).
    """
    question = _require_question(db, question_id)
    quiz     = _require_quiz(db, question.quiz_id)

    if quiz.content_status == ContentStatus.PUBLISHED.value:
        raise ProtectedFieldError(
            "Cannot change the correct answer of a PUBLISHED quiz. "
            "Put the quiz in DRAFT first."
        )

    options = (
        db.query(QuizAnswerOption)
        .filter(QuizAnswerOption.question_id == question_id)
        .all()
    )
    if _pool_mode(options):
        raise ProtectedFieldError(
            "Pool-mode questions use CORRECT_VARIANT options — "
            "use add/delete variant operations instead of swapping is_correct."
        )

    target = next((o for o in options if o.id == new_correct_option_id), None)
    if target is None:
        raise EditorError(f"Option {new_correct_option_id} not found in question {question_id}")

    for o in options:
        o.is_correct = (o.id == new_correct_option_id)
    db.commit()
    return question


def add_option(
    db,
    question_id: int,
    payload: OptionCreatePayload,
) -> QuizAnswerOption:
    """Add a new option to a question.

    Rules:
    - FIXED question: only FIXED type allowed; new is_correct not allowed if
      one already exists (use swap_correct_option).
    - Pool-mode question: only CORRECT_VARIANT or DISTRACTOR type allowed.
    - CORRECT_VARIANT is_correct must be True; DISTRACTOR must be False.
    """
    if not payload.option_text.strip():
        raise ValidationError("option_text must be non-empty")

    question = _require_question(db, question_id)
    options  = (
        db.query(QuizAnswerOption)
        .filter(QuizAnswerOption.question_id == question_id)
        .all()
    )
    fixed_options = [o for o in options if o.option_type == OptionType.FIXED]
    is_pool = _pool_mode(options) or any(
        o.option_type in (OptionType.CORRECT_VARIANT, OptionType.DISTRACTOR)
        for o in options
    )

    if is_pool:
        if payload.option_type == OptionType.FIXED:
            raise ProtectedFieldError(
                "Cannot add a FIXED option to a pool-mode question. "
                "Use CORRECT_VARIANT or DISTRACTOR."
            )
        if payload.option_type == OptionType.CORRECT_VARIANT and not payload.is_correct:
            raise ValidationError("CORRECT_VARIANT option must have is_correct=True")
        if payload.option_type == OptionType.DISTRACTOR and payload.is_correct:
            raise ValidationError("DISTRACTOR option must have is_correct=False")
    else:
        # FIXED question
        if payload.option_type != OptionType.FIXED:
            raise ProtectedFieldError(
                "Cannot add a CORRECT_VARIANT or DISTRACTOR option to a "
                "FIXED question. The option type cannot be changed."
            )
        if payload.is_correct:
            existing_correct = [o for o in fixed_options if o.is_correct]
            if existing_correct:
                raise ValidationError(
                    "A correct option already exists. Use swap_correct_option() "
                    "to change which option is correct."
                )

    next_idx = max((o.order_index for o in options), default=-1) + 1
    new_opt = QuizAnswerOption(
        question_id  = question_id,
        option_text  = payload.option_text.strip(),
        is_correct   = payload.is_correct,
        order_index  = next_idx,
        option_type  = payload.option_type,
    )
    db.add(new_opt)
    db.commit()
    db.refresh(new_opt)
    return new_opt


def delete_option(db, option_id: int) -> None:
    """Delete an option, enforcing pool-size and correct-answer guards.

    Guards:
    - PUBLISHED quiz: deleting the is_correct option is blocked.
    - v1.0 FIXED: total option count must stay ≥ 4 after deletion.
    - v2.0 pool:
        CORRECT_VARIANT remaining ≥ 2 after deletion.
        DISTRACTOR      remaining ≥ 6 after deletion.
    """
    option   = _require_option(db, option_id)
    question = _require_question(db, option.question_id)
    quiz     = _require_quiz(db, question.quiz_id)

    options = (
        db.query(QuizAnswerOption)
        .filter(QuizAnswerOption.question_id == option.question_id)
        .all()
    )

    # Guard: PUBLISHED + is_correct deletion
    if quiz.content_status == ContentStatus.PUBLISHED.value and option.is_correct:
        raise ProtectedFieldError(
            "Cannot delete the correct answer option of a PUBLISHED quiz. "
            "Put the quiz in DRAFT first."
        )

    is_pool = _pool_mode(options)
    if is_pool:
        variants    = [o for o in options if o.option_type == OptionType.CORRECT_VARIANT]
        distractors = [o for o in options if o.option_type == OptionType.DISTRACTOR]

        if option.option_type == OptionType.CORRECT_VARIANT:
            remaining = len(variants) - 1
            if remaining < _V2_MIN_VARIANTS:
                raise ValidationError(
                    f"Minimum {_V2_MIN_VARIANTS} correct variants required "
                    f"(would have {remaining} after deletion)."
                )
        elif option.option_type == OptionType.DISTRACTOR:
            remaining = len(distractors) - 1
            if remaining < _V2_MIN_DISTRACTORS:
                raise ValidationError(
                    f"Minimum {_V2_MIN_DISTRACTORS} distractors required "
                    f"(would have {remaining} after deletion)."
                )
    else:
        # FIXED mode
        fixed = [o for o in options if o.option_type == OptionType.FIXED]
        if len(fixed) - 1 < _V1_MIN_OPTIONS:
            raise ValidationError(
                f"Minimum {_V1_MIN_OPTIONS} options required "
                f"(would have {len(fixed) - 1} after deletion)."
            )

    db.delete(option)
    db.commit()


# ── New question creation ─────────────────────────────────────────────────────

def add_question(db, quiz_id: int, payload: QuestionCreatePayload) -> QuizQuestion:
    """Add a question to a quiz.

    Blocked for PUBLISHED and ARCHIVED quizzes — draft the quiz first.
    Validates option counts up front.
    """
    quiz = _require_quiz(db, quiz_id)
    if quiz.content_status != ContentStatus.DRAFT.value:
        raise InvalidTransitionError(
            f"Questions can only be added to DRAFT quizzes "
            f"(current status: {quiz.content_status}). "
            "Put the quiz in DRAFT first."
        )

    _validate_float_field("estimated_difficulty", payload.estimated_difficulty)
    _validate_float_field("cognitive_load", payload.cognitive_load)
    _validate_positive("average_time_seconds", payload.average_time_seconds)
    if not payload.question_text.strip():
        raise ValidationError("question_text must be non-empty")
    if not payload.explanation.strip():
        raise ValidationError("explanation must be non-empty")

    using_pool = bool(payload.correct_variants or payload.distractor_pool)

    if using_pool:
        if len(payload.correct_variants) < _V2_MIN_VARIANTS:
            raise ValidationError(
                f"Pool-mode question requires at least {_V2_MIN_VARIANTS} correct variants."
            )
        if len(payload.distractor_pool) < _V2_MIN_DISTRACTORS:
            raise ValidationError(
                f"Pool-mode question requires at least {_V2_MIN_DISTRACTORS} distractors."
            )
    else:
        if len(payload.fixed_options) < _V1_MIN_OPTIONS:
            raise ValidationError(
                f"FIXED question requires at least {_V1_MIN_OPTIONS} options."
            )
        correct_count = sum(1 for o in payload.fixed_options if o.is_correct)
        if correct_count != 1:
            raise ValidationError(
                f"FIXED question must have exactly 1 correct option (got {correct_count})."
            )

    # Determine next order_index
    existing = (
        db.query(QuizQuestion)
        .filter(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.order_index.desc())
        .first()
    )
    next_order = (existing.order_index + 1) if existing else 0

    question = QuizQuestion(
        quiz_id       = quiz_id,
        question_text = payload.question_text.strip(),
        question_type = payload.question_type,
        points        = 1,
        order_index   = next_order,
        explanation   = payload.explanation.strip(),
    )
    db.add(question)
    db.flush()

    if using_pool:
        for idx, text in enumerate(payload.correct_variants):
            db.add(QuizAnswerOption(
                question_id = question.id,
                option_text = text.strip(),
                is_correct  = True,
                order_index = idx,
                option_type = OptionType.CORRECT_VARIANT,
            ))
        offset = len(payload.correct_variants)
        for idx, text in enumerate(payload.distractor_pool):
            db.add(QuizAnswerOption(
                question_id = question.id,
                option_text = text.strip(),
                is_correct  = False,
                order_index = offset + idx,
                option_type = OptionType.DISTRACTOR,
            ))
    else:
        for idx, opt in enumerate(payload.fixed_options):
            db.add(QuizAnswerOption(
                question_id = question.id,
                option_text = opt.option_text.strip(),
                is_correct  = opt.is_correct,
                order_index = idx,
                option_type = OptionType.FIXED,
            ))

    db.add(QuestionMetadata(
        question_id           = question.id,
        estimated_difficulty  = float(payload.estimated_difficulty),
        cognitive_load        = float(payload.cognitive_load),
        average_time_seconds  = float(payload.average_time_seconds),
        concept_tags          = json.dumps(payload.concept_tags),
        global_success_rate   = None,
    ))

    db.commit()
    db.refresh(question)
    return question


# ── Question deletion ─────────────────────────────────────────────────────────

def delete_question(db, question_id: int, *, quiz_id: int | None = None) -> None:
    """Delete a question (cascade removes options + metadata via ORM).

    Blocked unless quiz is DRAFT.
    Blocked if this is the last question in the quiz.
    """
    question = _require_question(db, question_id, quiz_id)
    quiz     = _require_quiz(db, question.quiz_id)

    if quiz.content_status != ContentStatus.DRAFT.value:
        raise InvalidTransitionError(
            f"Questions can only be deleted from DRAFT quizzes "
            f"(current status: {quiz.content_status})."
        )

    total = (
        db.query(QuizQuestion)
        .filter(QuizQuestion.quiz_id == quiz.id)
        .count()
    )
    if total <= 1:
        raise ValidationError("Cannot delete the last question in a quiz.")

    db.delete(question)
    db.commit()


# ── Read helpers (used by admin routes) ──────────────────────────────────────

def get_question_with_options(db, question_id: int) -> dict[str, Any]:
    """Return question + options + metadata as a plain dict for templates."""
    question = db.query(QuizQuestion).filter(QuizQuestion.id == question_id).first()
    if not question:
        return {}

    options = (
        db.query(QuizAnswerOption)
        .filter(QuizAnswerOption.question_id == question_id)
        .order_by(QuizAnswerOption.order_index.asc())
        .all()
    )
    meta = _require_metadata(db, question_id)

    variants    = [o for o in options if o.option_type == OptionType.CORRECT_VARIANT]
    distractors = [o for o in options if o.option_type == OptionType.DISTRACTOR]
    fixed_opts  = [o for o in options if o.option_type == OptionType.FIXED]
    is_pool_q   = _pool_mode(options)

    concept_tags: list[str] = []
    if meta and meta.concept_tags:
        try:
            concept_tags = json.loads(meta.concept_tags)
        except (json.JSONDecodeError, TypeError):
            concept_tags = [meta.concept_tags] if meta.concept_tags else []

    return {
        "question":     question,
        "options":      options,
        "variants":     variants,
        "distractors":  distractors,
        "fixed_opts":   fixed_opts,
        "is_pool":      is_pool_q,
        "metadata":     meta,
        "concept_tags": concept_tags,
        "OptionType":   OptionType,
    }
