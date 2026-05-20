#!/usr/bin/env python3
"""
Shuffle AL option order — in-place order_index randomisation
=============================================================

Randomises quiz_answer_options.order_index for all questions that belong to
quizzes with titles matching 'AL — %', eliminating the source-file positional
bias (correct answer was always at order_index 0 or 1; position 3 / "D" was
never correct across all 375 questions).

What changes
------------
  quiz_answer_options.order_index   — reassigned per-question via random()

What is NEVER touched
---------------------
  quiz_answer_options.id            — no ID change
  quiz_answer_options.question_id   — FK intact
  quiz_answer_options.is_correct    — correctness flag unchanged
  quiz_answer_options.option_text   — content unchanged
  user_question_performance         — not touched (question_id FKs intact)
  adaptive_learning_answer_log      — not touched (option-ID FKs intact;
                                      presented_option_ids stores IDs not
                                      order_index values)
  Any other table                   — not touched

FK safety
---------
  This script performs only UPDATE statements.  No DELETE or INSERT occurs.
  All NO ACTION and CASCADE foreign-key constraints remain satisfied because
  no referenced rows change their primary keys.

Usage
-----
  # Dry run (default) — shows before/after distribution, writes nothing:
  python scripts/shuffle_al_option_order.py

  # Apply:
  python scripts/shuffle_al_option_order.py --apply

Idempotency
-----------
  Safe to run multiple times.  Each run produces a new random ordering.
  The bias correction is achieved after the first --apply run; subsequent
  runs simply re-randomise (harmless).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.database import SessionLocal

# ── SQL ───────────────────────────────────────────────────────────────────────

# Distribution of correct-answer positions (order_index) for AL questions
_SQL_DISTRIBUTION = text("""
SELECT
    qao.order_index        AS position,
    COUNT(*)               AS correct_count
FROM quiz_answer_options qao
JOIN quiz_questions qq ON qao.question_id = qq.id
JOIN quizzes        q  ON qq.quiz_id = q.id
WHERE q.title LIKE 'AL -- %'
  AND qao.is_correct = TRUE
GROUP BY qao.order_index
ORDER BY qao.order_index;
""".replace("AL -- %", "AL — %"))  # em-dash safe in source

# Count of affected rows (sanity check before and after)
_SQL_ROW_COUNTS = text("""
SELECT
    COUNT(DISTINCT q.id)   AS quiz_count,
    COUNT(DISTINCT qq.id)  AS question_count,
    COUNT(qao.id)          AS option_count
FROM quiz_answer_options qao
JOIN quiz_questions qq ON qao.question_id = qq.id
JOIN quizzes        q  ON qq.quiz_id = q.id
WHERE q.title LIKE :pattern;
""")

# Per-question correctness check: every question must have exactly 1 correct option
_SQL_CORRECTNESS_INTEGRITY = text("""
SELECT
    COUNT(*) AS questions_with_wrong_correct_count
FROM (
    SELECT qao.question_id, SUM(CASE WHEN qao.is_correct THEN 1 ELSE 0 END) AS correct_cnt
    FROM quiz_answer_options qao
    JOIN quiz_questions qq ON qao.question_id = qq.id
    JOIN quizzes        q  ON qq.quiz_id = q.id
    WHERE q.title LIKE :pattern
    GROUP BY qao.question_id
    HAVING SUM(CASE WHEN qao.is_correct THEN 1 ELSE 0 END) != 1
) violations;
""")

# order_index range check: all values must be in 0–(n_options-1) per question
_SQL_ORDERINDEX_RANGE = text("""
SELECT
    COUNT(*) AS out_of_range_options
FROM quiz_answer_options qao
JOIN quiz_questions qq ON qao.question_id = qq.id
JOIN quizzes        q  ON qq.quiz_id = q.id
WHERE q.title LIKE :pattern
  AND qao.order_index NOT BETWEEN 0 AND 9;
""")

# Duplicate order_index within a question check
_SQL_DUPLICATE_ORDER = text("""
SELECT
    COUNT(*) AS duplicate_order_index_rows
FROM (
    SELECT qao.question_id, qao.order_index, COUNT(*) AS cnt
    FROM quiz_answer_options qao
    JOIN quiz_questions qq ON qao.question_id = qq.id
    JOIN quizzes        q  ON qq.quiz_id = q.id
    WHERE q.title LIKE :pattern
    GROUP BY qao.question_id, qao.order_index
    HAVING COUNT(*) > 1
) dups;
""")

# The actual shuffle UPDATE
_SQL_SHUFFLE = text("""
UPDATE quiz_answer_options AS qao
SET order_index = new_order.new_idx
FROM (
    SELECT
        qao2.id,
        (ROW_NUMBER() OVER (
            PARTITION BY qao2.question_id
            ORDER BY random()
        ) - 1)::smallint AS new_idx
    FROM quiz_answer_options qao2
    JOIN quiz_questions qq ON qao2.question_id = qq.id
    JOIN quizzes        q  ON qq.quiz_id = q.id
    WHERE q.title LIKE :pattern
) AS new_order
WHERE qao.id = new_order.id;
""")

# ── Helpers ───────────────────────────────────────────────────────────────────

_PATTERN = "AL — %"
_SEP = "─" * 60


def _print_distribution(label: str, rows: list) -> None:
    print(f"\n  {label}")
    if not rows:
        print("    (no rows returned)")
        return
    total = sum(r.correct_count for r in rows)
    for r in rows:
        bar = "█" * int(r.correct_count / max(total, 1) * 30)
        print(f"    position {r.position} (order_index={r.position}): "
              f"{r.correct_count:>4} correct  {bar}")
    positions_seen = {r.position for r in rows}
    missing = {0, 1, 2, 3} - positions_seen
    if missing:
        print(f"    ⚠  positions with zero correct answers: {sorted(missing)}")
    else:
        print("    ✓  all 4 positions (0–3) have at least 1 correct answer")


def _run_integrity_checks(conn, label: str) -> bool:
    ok = True

    row = conn.execute(_SQL_CORRECTNESS_INTEGRITY, {"pattern": _PATTERN}).fetchone()
    if row.questions_with_wrong_correct_count != 0:
        print(f"  ✗ {label}: {row.questions_with_wrong_correct_count} questions "
              f"do not have exactly 1 correct answer")
        ok = False
    else:
        print(f"  ✓ {label}: every question has exactly 1 correct answer")

    row = conn.execute(_SQL_ORDERINDEX_RANGE, {"pattern": _PATTERN}).fetchone()
    if row.out_of_range_options != 0:
        print(f"  ✗ {label}: {row.out_of_range_options} options have "
              f"order_index outside 0–9")
        ok = False
    else:
        print(f"  ✓ {label}: all order_index values are in valid range")

    row = conn.execute(_SQL_DUPLICATE_ORDER, {"pattern": _PATTERN}).fetchone()
    if row.duplicate_order_index_rows != 0:
        print(f"  ✗ {label}: {row.duplicate_order_index_rows} duplicate "
              f"order_index values within a question")
        ok = False
    else:
        print(f"  ✓ {label}: no duplicate order_index within any question")

    return ok


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shuffle quiz_answer_options.order_index for AL questions in-place."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply the UPDATE. Without this flag the script is a dry run.",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"\n{_SEP}")
    print(f"  AL option order shuffle — {mode}")
    print(f"  Scope: quiz_answer_options WHERE quiz.title LIKE 'AL — %'")
    print(f"  Mutates: order_index only — IDs, is_correct, option_text unchanged")
    print(_SEP)

    db = SessionLocal()
    try:
        # ── Snapshot row counts ────────────────────────────────────────────
        counts = db.execute(_SQL_ROW_COUNTS, {"pattern": _PATTERN}).fetchone()
        print(f"\nScope snapshot:")
        print(f"  quizzes   : {counts.quiz_count}")
        print(f"  questions : {counts.question_count}")
        print(f"  options   : {counts.option_count}")

        # ── Before distribution ────────────────────────────────────────────
        before_rows = db.execute(_SQL_DISTRIBUTION).fetchall()
        _print_distribution("BEFORE — correct answer position distribution", before_rows)

        # ── Before integrity ──────────────────────────────────────────────
        print(f"\nIntegrity checks BEFORE:")
        before_ok = _run_integrity_checks(db, "before")
        if not before_ok:
            print("\n  ✗ Pre-flight integrity check failed. Aborting.")
            sys.exit(1)

        if not args.apply:
            print(f"\n{_SEP}")
            print("  DRY RUN — no changes written.")
            print("  Run with --apply to execute the UPDATE.")
            print(_SEP)
            return

        # ── Shuffle UPDATE (inside transaction) ───────────────────────────
        print(f"\nExecuting shuffle UPDATE …")
        result = db.execute(_SQL_SHUFFLE, {"pattern": _PATTERN})
        rows_updated = result.rowcount
        print(f"  {rows_updated} rows updated (order_index reassigned)")

        # ── After distribution ─────────────────────────────────────────────
        after_rows = db.execute(_SQL_DISTRIBUTION).fetchall()
        _print_distribution("AFTER  — correct answer position distribution", after_rows)

        # ── After integrity ────────────────────────────────────────────────
        print(f"\nIntegrity checks AFTER:")
        after_ok = _run_integrity_checks(db, "after")

        # ── Row-count stability ────────────────────────────────────────────
        counts_after = db.execute(_SQL_ROW_COUNTS, {"pattern": _PATTERN}).fetchone()
        row_counts_stable = (
            counts_after.quiz_count     == counts.quiz_count and
            counts_after.question_count == counts.question_count and
            counts_after.option_count   == counts.option_count
        )
        if row_counts_stable:
            print("  ✓ Row counts unchanged (no rows inserted or deleted)")
        else:
            print("  ✗ Row count mismatch — unexpected rows inserted/deleted")
            after_ok = False

        if not after_ok:
            print("\n  ✗ Post-UPDATE integrity check failed. Rolling back.")
            db.rollback()
            sys.exit(1)

        # ── All clear — commit ─────────────────────────────────────────────
        db.commit()
        print(f"\n{_SEP}")
        print("  ✓ Shuffle committed successfully.")
        print(f"  {rows_updated} option rows updated (order_index only).")
        print("  IDs, is_correct, option_text — unchanged.")
        print("  user_question_performance    — not touched.")
        print("  adaptive_learning_answer_log — not touched.")
        print(_SEP)

    except Exception as exc:
        db.rollback()
        print(f"\n  ✗ Error: {exc}")
        print("  Transaction rolled back — no changes persisted.")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
