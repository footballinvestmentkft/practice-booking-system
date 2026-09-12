"""Adaptive Learning import service.

Two-phase flow (mirrors card_theme_admin_service):
  1. validate_files()  → ImportReport (no DB writes except skip-check reads)
  2. apply_import()    → ALImportSummary (DB writes + ALImportLog row)

All validation and seeding logic lives here; seed_adaptive_learning_questions.py
is a thin CLI wrapper that imports from this module.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from ..models.quiz import (
    Quiz, QuizCategory, QuizDifficulty, QuizQuestion,
    QuizAnswerOption, QuestionMetadata, QuestionType, OptionType, ContentStatus,
)

# ── Constants (also exported for seeder compatibility) ────────────────────────

VALID_SCHEMA_VERSIONS: set[str] = {"1.0", "2.0"}

SPECIALIZATION_CATEGORY_ALLOWLIST: dict[str, set[str]] = {
    "LFA_FOOTBALL_PLAYER": {
        "GENERAL", "LESSON", "SPORTS_PHYSIOLOGY", "NUTRITION",
        "MARKETING", "ECONOMICS", "INFORMATICS",
    },
    "LFA_FOOTBALL_COACH": {
        "GENERAL", "LESSON", "SPORTS_PHYSIOLOGY", "NUTRITION",
    },
}

_V2_MIN_CORRECT_VARIANTS = 2
_V2_MIN_DISTRACTORS = 6
_MAX_FILES_PER_IMPORT = 20
_MAX_FILE_BYTES = 256 * 1024   # 256 KB per file


# ── Exceptions ────────────────────────────────────────────────────────────────

class ValidationError(Exception):
    pass


# ── Report dataclasses ────────────────────────────────────────────────────────

@dataclass
class VariantStats:
    min_variants: int
    max_variants: int
    avg_variants: float


@dataclass
class DistractorStats:
    min_distractors: int
    max_distractors: int
    avg_distractors: float


@dataclass
class FileValidationResult:
    filename:         str
    status:           Literal["ok", "skip", "error"]
    schema_version:   str | None = None
    quiz_title:       str | None = None
    category:         str | None = None
    difficulty:       str | None = None
    language:         str | None = None
    question_count:   int = 0
    variant_stats:    VariantStats | None = None
    distractor_stats: DistractorStats | None = None
    error_message:    str | None = None
    # Serialised quiz data — passed round-trip to apply step (ok items only)
    _data_json:       str | None = None


@dataclass
class ImportReport:
    files:               list[FileValidationResult] = field(default_factory=list)
    total_ok:            int = 0
    total_skip:          int = 0
    total_error:         int = 0
    quizzes_to_create:   int = 0
    questions_to_create: int = 0
    # Serialised payload to embed in hidden form field (ok items only)
    apply_payload_json:  str = ""


@dataclass
class ALImportSummary:
    quizzes_created:    int = 0
    questions_created:  int = 0
    options_fixed:      int = 0
    options_variant:    int = 0
    options_distractor: int = 0
    skipped:            int = 0
    errors:             list[str] = field(default_factory=list)
    log_id:             int | None = None


# ── Validation (pure — no DB access) ─────────────────────────────────────────

def _validate_file(data: dict[str, Any], spec: str) -> None:
    """Raise ValidationError if the file fails schema validation."""
    sv = data.get("schema_version")
    if sv not in VALID_SCHEMA_VERSIONS:
        raise ValidationError(f"Unknown schema_version: {sv!r}")

    if spec not in data.get("specializations", []):
        raise ValidationError(f"Specialization {spec!r} not in specializations field")

    category_str = data.get("category", "")
    if category_str not in SPECIALIZATION_CATEGORY_ALLOWLIST.get(spec, set()):
        raise ValidationError(
            f"Category {category_str!r} not in allowlist for {spec}"
        )
    try:
        QuizCategory[category_str]
    except KeyError:
        raise ValidationError(f"Unknown QuizCategory value: {category_str!r}")

    difficulty_str = data.get("difficulty", "")
    try:
        QuizDifficulty[difficulty_str]
    except KeyError:
        raise ValidationError(f"Unknown QuizDifficulty value: {difficulty_str!r}")

    if not data.get("quiz_title", "").strip():
        raise ValidationError("quiz_title must be non-empty")

    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) == 0:
        raise ValidationError("questions must be a non-empty list")

    validator = _validate_question_v2 if sv == "2.0" else _validate_question
    for i, q in enumerate(questions):
        validator(q, i)


def _validate_question(q: dict[str, Any], idx: int) -> None:
    prefix = f"questions[{idx}]"

    if not q.get("text", "").strip():
        raise ValidationError(f"{prefix}.text must be non-empty")

    qtype_str = q.get("type", "")
    try:
        QuestionType[qtype_str]
    except KeyError:
        raise ValidationError(f"{prefix}.type unknown: {qtype_str!r}")

    if not q.get("explanation", "").strip():
        raise ValidationError(f"{prefix}.explanation must be non-empty")

    options = q.get("options")
    if not isinstance(options, list):
        raise ValidationError(f"{prefix}.options must be a list")

    if qtype_str == "TRUE_FALSE":
        if len(options) != 2:
            raise ValidationError(
                f"{prefix} ({qtype_str}) must have exactly 2 options (got {len(options)})"
            )
    elif len(options) < 4:
        raise ValidationError(
            f"{prefix} ({qtype_str}) must have at least 4 options (got {len(options)})"
        )

    correct_count = sum(1 for o in options if o.get("is_correct"))
    if correct_count != 1:
        raise ValidationError(
            f"{prefix} must have exactly 1 correct option (got {correct_count})"
        )

    for j, o in enumerate(options):
        if not o.get("text", "").strip():
            raise ValidationError(f"{prefix}.options[{j}].text must be non-empty")

    _validate_metadata(q, prefix)


def _validate_question_v2(q: dict[str, Any], idx: int) -> None:
    prefix = f"questions[{idx}]"

    if not q.get("text", "").strip():
        raise ValidationError(f"{prefix}.text must be non-empty")

    qtype_str = q.get("type", "")
    try:
        QuestionType[qtype_str]
    except KeyError:
        raise ValidationError(f"{prefix}.type unknown: {qtype_str!r}")

    if qtype_str == "TRUE_FALSE":
        raise ValidationError(
            f"{prefix}: TRUE_FALSE questions are not supported in v2.0 schema "
            "(use v1.0 options[] format for true/false questions)"
        )

    if not q.get("explanation", "").strip():
        raise ValidationError(f"{prefix}.explanation must be non-empty")

    variants = q.get("correct_variants")
    if not isinstance(variants, list):
        raise ValidationError(f"{prefix}.correct_variants must be a list")
    if len(variants) < _V2_MIN_CORRECT_VARIANTS:
        raise ValidationError(
            f"{prefix}.correct_variants must have at least {_V2_MIN_CORRECT_VARIANTS} "
            f"entries (got {len(variants)})"
        )
    for j, v in enumerate(variants):
        if not isinstance(v, str) or not v.strip():
            raise ValidationError(f"{prefix}.correct_variants[{j}] must be a non-empty string")

    distractors = q.get("distractor_pool")
    if not isinstance(distractors, list):
        raise ValidationError(f"{prefix}.distractor_pool must be a list")
    if len(distractors) < _V2_MIN_DISTRACTORS:
        raise ValidationError(
            f"{prefix}.distractor_pool must have at least {_V2_MIN_DISTRACTORS} "
            f"entries (got {len(distractors)})"
        )
    for j, d in enumerate(distractors):
        if not isinstance(d, str) or not d.strip():
            raise ValidationError(f"{prefix}.distractor_pool[{j}] must be a non-empty string")

    _validate_metadata(q, prefix)


def _validate_metadata(q: dict[str, Any], prefix: str) -> None:
    meta = q.get("metadata")
    if not isinstance(meta, dict):
        raise ValidationError(f"{prefix}.metadata must be a dict")
    for key in ("estimated_difficulty", "cognitive_load", "average_time_seconds"):
        if key not in meta:
            raise ValidationError(f"{prefix}.metadata.{key} is required")
    diff = meta["estimated_difficulty"]
    if not (0.0 <= diff <= 1.0):
        raise ValidationError(
            f"{prefix}.metadata.estimated_difficulty must be 0.0–1.0 (got {diff})"
        )


# ── Option seeding ────────────────────────────────────────────────────────────

def _seed_options_v1(db, question_id: int, q_data: dict[str, Any]) -> dict[str, int]:
    options_list = list(q_data["options"])
    random.shuffle(options_list)
    for opt_idx, opt in enumerate(options_list):
        db.add(QuizAnswerOption(
            question_id=question_id,
            option_text=opt["text"].strip(),
            is_correct=bool(opt["is_correct"]),
            order_index=opt_idx,
            option_type=OptionType.FIXED,
        ))
    return {"fixed": len(options_list), "variant": 0, "distractor": 0}


def _seed_options_v2(db, question_id: int, q_data: dict[str, Any]) -> dict[str, int]:
    opt_idx = 0
    n_variants = 0
    for variant_text in q_data["correct_variants"]:
        db.add(QuizAnswerOption(
            question_id=question_id,
            option_text=variant_text.strip(),
            is_correct=True,
            order_index=opt_idx,
            option_type=OptionType.CORRECT_VARIANT,
        ))
        opt_idx += 1
        n_variants += 1
    n_distractors = 0
    for distractor_text in q_data["distractor_pool"]:
        db.add(QuizAnswerOption(
            question_id=question_id,
            option_text=distractor_text.strip(),
            is_correct=False,
            order_index=opt_idx,
            option_type=OptionType.DISTRACTOR,
        ))
        opt_idx += 1
        n_distractors += 1
    return {"fixed": 0, "variant": n_variants, "distractor": n_distractors}


# ── Quiz seeding ──────────────────────────────────────────────────────────────

def _seed_quiz(db, data: dict[str, Any], is_active: bool = True) -> dict[str, int]:
    """Create a single quiz with all questions and options.

    Does NOT check for existing quizzes — caller is responsible.
    Commits the transaction at the end.
    Returns counts: {quizzes, questions, fixed, variant, distractor}.
    """
    category = QuizCategory[data["category"]]
    difficulty = QuizDifficulty[data["difficulty"]]
    schema_version = data.get("schema_version", "1.0")

    quiz = Quiz(
        title=data["quiz_title"].strip(),
        description=f"{data.get('topic', '')} — {data.get('module', '')}",
        category=category,
        difficulty=difficulty,
        language=data.get("language", "en"),
        time_limit_minutes=20,
        xp_reward=50,
        passing_score=70.0,
        is_active=is_active,
        content_status=(
            ContentStatus.PUBLISHED.value if is_active else ContentStatus.DRAFT.value
        ),
    )
    db.add(quiz)
    db.flush()

    totals = {"questions": 0, "fixed": 0, "variant": 0, "distractor": 0}

    for order_idx, q_data in enumerate(data["questions"]):
        question = QuizQuestion(
            quiz_id=quiz.id,
            question_text=q_data["text"].strip(),
            question_type=QuestionType[q_data["type"]],
            points=q_data.get("points", 1),
            order_index=order_idx,
            explanation=q_data["explanation"].strip(),
        )
        db.add(question)
        db.flush()

        if schema_version == "2.0":
            counts = _seed_options_v2(db, question.id, q_data)
        else:
            counts = _seed_options_v1(db, question.id, q_data)

        totals["fixed"]      += counts["fixed"]
        totals["variant"]    += counts["variant"]
        totals["distractor"] += counts["distractor"]
        totals["questions"]  += 1

        meta = q_data["metadata"]
        tags = meta.get("concept_tags", [])
        db.add(QuestionMetadata(
            question_id=question.id,
            estimated_difficulty=meta["estimated_difficulty"],
            cognitive_load=meta["cognitive_load"],
            concept_tags=json.dumps(tags) if isinstance(tags, list) else tags,
            average_time_seconds=meta["average_time_seconds"],
            global_success_rate=None,
        ))

    db.commit()
    return {"quizzes": 1, **totals}


# ── Stats helpers ─────────────────────────────────────────────────────────────

def _compute_variant_stats(questions: list[dict]) -> VariantStats | None:
    counts = [len(q.get("correct_variants", [])) for q in questions]
    if not counts:
        return None
    return VariantStats(
        min_variants=min(counts),
        max_variants=max(counts),
        avg_variants=round(sum(counts) / len(counts), 1),
    )


def _compute_distractor_stats(questions: list[dict]) -> DistractorStats | None:
    counts = [len(q.get("distractor_pool", [])) for q in questions]
    if not counts:
        return None
    return DistractorStats(
        min_distractors=min(counts),
        max_distractors=max(counts),
        avg_distractors=round(sum(counts) / len(counts), 1),
    )


# ── Service API ───────────────────────────────────────────────────────────────

def validate_files(
    raw_files: list[tuple[str, bytes]],
    spec: str,
    db,
) -> ImportReport:
    """Validate a list of (filename, bytes) tuples.

    Performs JSON parse + schema validation + DB skip-check.
    No writes to the database.
    Returns ImportReport with per-file results and a serialised apply payload.
    """
    if len(raw_files) > _MAX_FILES_PER_IMPORT:
        raise ValueError(
            f"Too many files: {len(raw_files)} > {_MAX_FILES_PER_IMPORT} max"
        )

    report = ImportReport()
    apply_items: list[dict] = []

    for filename, content in raw_files:
        result = FileValidationResult(filename=filename, status="error")

        if len(content) > _MAX_FILE_BYTES:
            result.error_message = f"File too large ({len(content)} bytes, max {_MAX_FILE_BYTES})"
            report.files.append(result)
            report.total_error += 1
            continue

        # JSON parse
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            result.error_message = f"JSON parse error: {exc}"
            report.files.append(result)
            report.total_error += 1
            continue

        # Spec gate
        if spec not in data.get("specializations", []):
            result.status = "error"
            result.error_message = f"spec {spec!r} not in specializations field"
            report.files.append(result)
            report.total_error += 1
            continue

        # Schema validation
        try:
            _validate_file(data, spec)
        except ValidationError as exc:
            result.error_message = str(exc)
            report.files.append(result)
            report.total_error += 1
            continue

        # Populate basic metadata
        result.schema_version = data.get("schema_version", "1.0")
        result.quiz_title     = data.get("quiz_title", "").strip()
        result.category       = data.get("category")
        result.difficulty     = data.get("difficulty")
        result.language       = data.get("language", "en")
        result.question_count = len(data.get("questions", []))

        if result.schema_version == "2.0":
            result.variant_stats    = _compute_variant_stats(data["questions"])
            result.distractor_stats = _compute_distractor_stats(data["questions"])

        # DB skip check
        existing = db.query(Quiz).filter(
            Quiz.title == result.quiz_title
        ).first()
        if existing:
            result.status = "skip"
            report.files.append(result)
            report.total_skip += 1
            continue

        result.status = "ok"
        result._data_json = json.dumps(data)
        report.files.append(result)
        report.total_ok += 1
        report.quizzes_to_create   += 1
        report.questions_to_create += result.question_count

        apply_items.append({
            "filename": filename,
            "spec":     spec,
            "data":     data,
        })

    report.apply_payload_json = json.dumps(apply_items)
    return report


def apply_import(
    apply_payload_json: str,
    spec: str,
    db,
    operator_user_id: int | None,
    import_as_draft: bool = False,
) -> ALImportSummary:
    """Apply a validated import payload.

    apply_payload_json: the JSON string from the hidden form field produced
    by validate_files().  Items are re-validated before writing to prevent
    hidden-field tampering.

    Creates an ALImportLog row regardless of partial failures.
    """
    # Lazy import to avoid circular: al_import_log imports Base from database
    from ..models.al_import_log import ALImportLog, ImportStatus

    try:
        items: list[dict] = json.loads(apply_payload_json)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("apply_payload_json is not valid JSON")

    if not isinstance(items, list):
        raise ValueError("apply_payload_json must be a JSON array")

    summary = ALImportSummary()
    details: list[dict] = []
    is_active = not import_as_draft

    for item in items:
        data = item.get("data", {})
        filename = item.get("filename", "unknown")
        quiz_title = data.get("quiz_title", "").strip()

        # Re-validate to prevent tampering
        try:
            _validate_file(data, spec)
        except ValidationError as exc:
            msg = f"{filename}: validation error after round-trip — {exc}"
            summary.errors.append(msg)
            details.append({"filename": filename, "status": "error", "error": str(exc)})
            continue

        # Idempotency check
        if db.query(Quiz).filter(Quiz.title == quiz_title).first():
            summary.skipped += 1
            details.append({"filename": filename, "status": "skip", "quiz_title": quiz_title})
            continue

        try:
            counts = _seed_quiz(db, data, is_active=is_active)
            summary.quizzes_created    += counts["quizzes"]
            summary.questions_created  += counts["questions"]
            summary.options_fixed      += counts["fixed"]
            summary.options_variant    += counts["variant"]
            summary.options_distractor += counts["distractor"]
            details.append({
                "filename":  filename,
                "status":    "created",
                "quiz_title": quiz_title,
                "questions": counts["questions"],
            })
        except Exception as exc:
            db.rollback()
            msg = f"{filename}: seed failed — {exc}"
            summary.errors.append(msg)
            details.append({"filename": filename, "status": "error", "error": str(exc)})

    # Write import log
    if summary.errors and (summary.quizzes_created > 0 or summary.skipped > 0):
        log_status = ImportStatus.PARTIAL
    elif summary.errors:
        log_status = ImportStatus.ERROR
    else:
        log_status = ImportStatus.SUCCESS

    log = ALImportLog(
        operator_id=operator_user_id,
        spec=spec,
        status=log_status,
        completed_at=datetime.now(timezone.utc),
        files_submitted=len(items),
        files_ok=summary.quizzes_created,
        files_skipped=summary.skipped,
        files_error=len(summary.errors),
        quizzes_created=summary.quizzes_created,
        questions_created=summary.questions_created,
        options_fixed=summary.options_fixed,
        options_variant=summary.options_variant,
        options_distractor=summary.options_distractor,
        details_json=json.dumps(details),
    )
    db.add(log)
    db.commit()
    summary.log_id = log.id
    return summary
