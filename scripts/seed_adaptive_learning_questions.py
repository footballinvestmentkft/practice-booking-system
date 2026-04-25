#!/usr/bin/env python3
"""Seed Adaptive Learning questions from JSON content files.

Usage:
  python scripts/seed_adaptive_learning_questions.py [--spec SPEC] [--dry-run]

Arguments:
  --spec   Specialization filter, e.g. LFA_FOOTBALL_PLAYER  (default: LFA_FOOTBALL_PLAYER)
  --dry-run  Validate and report without writing to the database

Content is loaded from content/adaptive_learning/ following two-gate filtering:
  Gate 1: folder scan — only files under _shared/ and <spec_slug>/ directories are considered
  Gate 2: field validation — file must list the requested specialization in its "specializations" field

Idempotency: Quiz rows are matched by quiz_title. Existing quizzes are skipped in full.
"""

import argparse
import json
import sys
import os
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models.quiz import (
    Quiz,
    QuizCategory,
    QuizDifficulty,
    QuizQuestion,
    QuizAnswerOption,
    QuestionMetadata,
    QuestionType,
)

# ── Constants ─────────────────────────────────────────────────────────────────

CONTENT_ROOT = Path(__file__).resolve().parent.parent / "content" / "adaptive_learning"

# Maps specialization token (as used in JSON) → folder slug under content/adaptive_learning/
SPEC_FOLDER_MAP: dict[str, str] = {
    "LFA_FOOTBALL_PLAYER": "lfa_football_player",
    "LFA_FOOTBALL_COACH": "lfa_football_coach",
}

# Valid categories per specialization (gate 2 category check)
SPECIALIZATION_CATEGORY_ALLOWLIST: dict[str, set[str]] = {
    "LFA_FOOTBALL_PLAYER": {
        "GENERAL", "LESSON", "SPORTS_PHYSIOLOGY", "NUTRITION",
        "MARKETING", "ECONOMICS", "INFORMATICS",
    },
    "LFA_FOOTBALL_COACH": {
        "GENERAL", "LESSON", "SPORTS_PHYSIOLOGY", "NUTRITION",
    },
}

VALID_SCHEMA_VERSIONS = {"1.0"}


# ── Validation ────────────────────────────────────────────────────────────────

class ValidationError(Exception):
    pass


def _validate_file(data: dict[str, Any], path: Path, spec: str) -> None:
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

    for i, q in enumerate(questions):
        _validate_question(q, i)


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

    expected_option_count = 2 if qtype_str == "TRUE_FALSE" else 4
    if len(options) != expected_option_count:
        raise ValidationError(
            f"{prefix} ({qtype_str}) must have exactly {expected_option_count} options "
            f"(got {len(options)})"
        )

    correct_count = sum(1 for o in options if o.get("is_correct"))
    if correct_count != 1:
        raise ValidationError(
            f"{prefix} must have exactly 1 correct option (got {correct_count})"
        )

    for j, o in enumerate(options):
        if not o.get("text", "").strip():
            raise ValidationError(f"{prefix}.options[{j}].text must be non-empty")

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


# ── File discovery ─────────────────────────────────────────────────────────────

def _discover_files(spec: str) -> list[Path]:
    """Gate 1: collect JSON files from _shared/ and the spec-specific folder."""
    spec_folder = SPEC_FOLDER_MAP.get(spec)
    if not spec_folder:
        print(f"[ERROR] No folder mapping for spec {spec!r}. Known: {list(SPEC_FOLDER_MAP)}")
        sys.exit(1)

    candidate_dirs = [
        CONTENT_ROOT / "_shared",
        CONTENT_ROOT / spec_folder,
    ]

    files: list[Path] = []
    for d in candidate_dirs:
        if d.exists():
            files.extend(sorted(d.rglob("*.json")))

    return files


# ── Seeding ───────────────────────────────────────────────────────────────────

def _seed_file(db, data: dict[str, Any], path: Path, dry_run: bool) -> dict[str, int]:
    """Seed one quiz file. Returns {"skipped": 1} or {"quizzes": 1, "questions": N}."""
    quiz_title = data["quiz_title"].strip()

    existing = db.query(Quiz).filter(Quiz.title == quiz_title).first()
    if existing:
        return {"skipped": 1, "questions": 0}

    if dry_run:
        return {"dry_run_would_create": 1, "questions": len(data["questions"])}

    category = QuizCategory[data["category"]]
    difficulty = QuizDifficulty[data["difficulty"]]

    quiz = Quiz(
        title=quiz_title,
        description=f"{data.get('topic', '')} — {data.get('module', '')}",
        category=category,
        difficulty=difficulty,
        language=data.get("language", "en"),
        module=data.get("module") or None,
        topic=data.get("topic") or None,
        time_limit_minutes=20,
        xp_reward=50,
        passing_score=70.0,
        is_active=True,
    )
    db.add(quiz)
    db.flush()  # get quiz.id

    questions = data["questions"]
    for order_idx, q_data in enumerate(questions):
        question = QuizQuestion(
            quiz_id=quiz.id,
            question_text=q_data["text"].strip(),
            question_type=QuestionType[q_data["type"]],
            points=q_data.get("points", 1),
            order_index=order_idx,
            explanation=q_data["explanation"].strip(),
        )
        db.add(question)
        db.flush()  # get question.id

        for opt_idx, opt in enumerate(q_data["options"]):
            db.add(QuizAnswerOption(
                question_id=question.id,
                option_text=opt["text"].strip(),
                is_correct=bool(opt["is_correct"]),
                order_index=opt_idx,
            ))

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
    return {"quizzes": 1, "questions": len(questions)}


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Adaptive Learning questions from JSON")
    parser.add_argument(
        "--spec",
        default="LFA_FOOTBALL_PLAYER",
        help="Specialization filter (default: LFA_FOOTBALL_PLAYER)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report without writing to the database",
    )
    parser.add_argument(
        "--lang",
        default=None,
        help="Language filter, e.g. 'hu' or 'en'. If omitted, all languages are included.",
    )
    args = parser.parse_args()
    spec = args.spec.upper()
    dry_run: bool = args.dry_run
    lang_filter: str | None = args.lang.lower() if args.lang else None

    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"\n{'='*60}")
    print(f"  Adaptive Learning Question Seeder  [{mode}]")
    print(f"  Spec: {spec}")
    if lang_filter:
        print(f"  Language filter: {lang_filter!r}")
    print(f"{'='*60}\n")

    files = _discover_files(spec)
    if not files:
        print("[WARN] No JSON files found. Check content/ folder structure.")
        return

    print(f"Discovered {len(files)} candidate file(s):\n")

    # Gate 2 + validate all files before touching the DB
    valid_files: list[tuple[Path, dict]] = []
    error_count = 0
    for path in files:
        try:
            with open(path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"  [SKIP] {path.relative_to(CONTENT_ROOT)} — JSON parse error: {e}")
            error_count += 1
            continue

        # Gate 2: specializations field
        if spec not in data.get("specializations", []):
            print(f"  [SKIP] {path.relative_to(CONTENT_ROOT)} — spec {spec!r} not in specializations")
            continue

        # Gate 3: language filter (optional)
        if lang_filter is not None:
            file_lang = data.get("language", "").lower()
            if file_lang != lang_filter:
                print(f"  [SKIP] {path.relative_to(CONTENT_ROOT)} — language {file_lang!r} != {lang_filter!r}")
                continue

        try:
            _validate_file(data, path, spec)
        except ValidationError as e:
            print(f"  [ERROR] {path.relative_to(CONTENT_ROOT)} — {e}")
            error_count += 1
            continue

        print(f"  [OK]   {path.relative_to(CONTENT_ROOT)}")
        print(f"         quiz_title={data['quiz_title']!r}  category={data['category']}  "
              f"difficulty={data['difficulty']}  questions={len(data['questions'])}")
        valid_files.append((path, data))

    if error_count:
        print(f"\n[ABORT] {error_count} file(s) had errors. Fix them before seeding.")
        sys.exit(1)

    print(f"\n{len(valid_files)} file(s) passed validation.\n")

    if not valid_files:
        print("Nothing to seed.")
        return

    # Seed
    db = SessionLocal()
    try:
        totals = {"quizzes_created": 0, "questions_created": 0, "skipped": 0}

        for path, data in valid_files:
            result = _seed_file(db, data, path, dry_run)

            rel = path.relative_to(CONTENT_ROOT)
            if result.get("skipped"):
                print(f"  [SKIP]   {rel} — quiz already exists ({data['quiz_title']!r})")
                totals["skipped"] += 1
            elif result.get("dry_run_would_create"):
                print(f"  [DRY]    {rel} — would create quiz + {result['questions']} questions")
                totals["quizzes_created"] += 1
                totals["questions_created"] += result["questions"]
            else:
                print(f"  [CREATE] {rel} — created quiz + {result['questions']} questions")
                totals["quizzes_created"] += 1
                totals["questions_created"] += result["questions"]

        print(f"\n{'='*60}")
        action = "Would create" if dry_run else "Created"
        print(f"  {action}:  {totals['quizzes_created']} quiz(zes), {totals['questions_created']} question(s)")
        print(f"  Skipped: {totals['skipped']} quiz(zes) (already exist)")
        print(f"{'='*60}\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
