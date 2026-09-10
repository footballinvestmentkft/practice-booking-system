"""
scripts/patch_demo_seed_profiles.py
=====================================
Demo seed profile completion for seed.player.1-9@promo-seed.example.com.

Usage:
    python scripts/patch_demo_seed_profiles.py              # dry-run (no DB writes)
    python scripts/patch_demo_seed_profiles.py --apply      # write to DB
    python scripts/patch_demo_seed_profiles.py --apply --force  # overwrite existing values

Default (no flags): audit + dry-run only.

What it patches (per-field idempotent, --force to overwrite):
    users: nationality, gender, nickname, position, specialization
    user_licenses: right_foot_score, left_foot_score,
                   motivation_scores (key-by-key merge)

What it NEVER touches:
    football_skills, xp_balance, credit_balance, xp_transactions,
    credit_transactions, semester_enrollments, tournament_participations,
    reward / tournament tables.
"""

import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# Allow running from project root without package install
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Seed profiles — fully explicit, no derived values
# preferred_foot is consistent with right/left scores:
#   abs(right - left) <= 10 → "both"
#   right > left            → "right"
#   left  > right           → "left"
# ---------------------------------------------------------------------------

SEED_PROFILES: List[Dict[str, Any]] = [
    {
        "email":          "seed.player.1@promo-seed.example.com",
        "position":       "STRIKER",
        "nationality":    "HU",
        "gender":         "Male",
        "nickname":       "Seed1",
        "height_cm":      180,
        "weight_kg":      76,
        "right_foot_score": 75.0,
        "left_foot_score":  35.0,
        "preferred_foot": "right",   # |75-35|=40 > 10, right > left
    },
    {
        "email":          "seed.player.2@promo-seed.example.com",
        "position":       "STRIKER",
        "nationality":    "DE",
        "gender":         "Male",
        "nickname":       "Seed2",
        "height_cm":      175,
        "weight_kg":      72,
        "right_foot_score": 30.0,
        "left_foot_score":  78.0,
        "preferred_foot": "left",    # |30-78|=48 > 10, left > right
    },
    {
        "email":          "seed.player.3@promo-seed.example.com",
        "position":       "STRIKER",
        "nationality":    "AT",
        "gender":         "Male",
        "nickname":       "Seed3",
        "height_cm":      177,
        "weight_kg":      74,
        "right_foot_score": 65.0,
        "left_foot_score":  60.0,
        "preferred_foot": "both",    # |65-60|=5 <= 10
    },
    {
        "email":          "seed.player.4@promo-seed.example.com",
        "position":       "MIDFIELDER",
        "nationality":    "HR",
        "gender":         "Female",
        "nickname":       "Seed4",
        "height_cm":      172,
        "weight_kg":      68,
        "right_foot_score": 72.0,
        "left_foot_score":  38.0,
        "preferred_foot": "right",   # |72-38|=34 > 10, right > left
    },
    {
        "email":          "seed.player.5@promo-seed.example.com",
        "position":       "MIDFIELDER",
        "nationality":    "SK",
        "gender":         "Male",
        "nickname":       "Seed5",
        "height_cm":      174,
        "weight_kg":      71,
        "right_foot_score": 62.0,
        "left_foot_score":  58.0,
        "preferred_foot": "both",    # |62-58|=4 <= 10
    },
    {
        "email":          "seed.player.6@promo-seed.example.com",
        "position":       "MIDFIELDER",
        "nationality":    "RO",
        "gender":         "Male",
        "nickname":       "Seed6",
        "height_cm":      170,
        "weight_kg":      67,
        "right_foot_score": 80.0,
        "left_foot_score":  28.0,
        "preferred_foot": "right",   # |80-28|=52 > 10, right > left
    },
    {
        "email":          "seed.player.7@promo-seed.example.com",
        "position":       "DEFENDER",
        "nationality":    "CZ",
        "gender":         "Female",
        "nickname":       "Seed7",
        "height_cm":      182,
        "weight_kg":      82,
        "right_foot_score": 70.0,
        "left_foot_score":  42.0,
        "preferred_foot": "right",   # |70-42|=28 > 10, right > left
    },
    {
        "email":          "seed.player.8@promo-seed.example.com",
        "position":       "DEFENDER",
        "nationality":    "PL",
        "gender":         "Male",
        "nickname":       "Seed8",
        "height_cm":      185,
        "weight_kg":      85,
        "right_foot_score": 68.0,
        "left_foot_score":  30.0,
        "preferred_foot": "right",   # |68-30|=38 > 10, right > left
    },
    {
        "email":          "seed.player.9@promo-seed.example.com",
        "position":       "GOALKEEPER",
        "nationality":    "SI",
        "gender":         "Male",
        "nickname":       "Seed9",
        "height_cm":      190,
        "weight_kg":      86,
        "right_foot_score": 60.0,
        "left_foot_score":  55.0,
        "preferred_foot": "both",    # |60-55|=5 <= 10
    },
]

EMAIL_PATTERN = "seed.player.%@promo-seed.example.com"


# ---------------------------------------------------------------------------
# Core patch logic (pure functions — no DB call, testable in isolation)
# ---------------------------------------------------------------------------

def patch_user_fields(
    user: Any,
    profile: Dict[str, Any],
    force: bool,
) -> Dict[str, Tuple[Any, Any]]:
    """
    Apply profile values to a user object in-place.
    Returns a dict of {field_name: (old_value, new_value)} for changed fields.
    Fields already set are skipped unless force=True.
    NEVER touches: xp_balance, credit_balance, football_skills, enrollments.
    """
    changes: Dict[str, Tuple[Any, Any]] = {}

    for field in ("nationality", "gender", "nickname", "position"):
        current = getattr(user, field, None)
        if current is None or force:
            new_val = profile[field]
            if current != new_val:
                setattr(user, field, new_val)
                changes[f"users.{field}"] = (current, new_val)

    # specialization is an Enum — compare by value
    current_spec = getattr(user, "specialization", None)
    spec_val = "LFA_FOOTBALL_PLAYER"
    current_spec_str = current_spec.value if hasattr(current_spec, "value") else current_spec
    if current_spec_str is None or force:
        # Import here to avoid circular import at module level during tests
        from app.models.specialization import SpecializationType
        user.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
        changes["users.specialization"] = (current_spec_str, spec_val)

    return changes


def patch_license_fields(
    license: Any,
    profile: Dict[str, Any],
    force: bool,
) -> Dict[str, Tuple[Any, Any]]:
    """
    Apply foot scores and motivation_scores keys to a license object in-place.
    Returns a dict of {field_name: (old_value, new_value)} for changed fields.
    NEVER touches: football_skills.
    """
    changes: Dict[str, Tuple[Any, Any]] = {}

    # right_foot_score
    if license.right_foot_score is None or force:
        new_val = profile["right_foot_score"]
        if license.right_foot_score != new_val:
            changes["ul.right_foot_score"] = (license.right_foot_score, new_val)
            license.right_foot_score = new_val

    # left_foot_score
    if license.left_foot_score is None or force:
        new_val = profile["left_foot_score"]
        if license.left_foot_score != new_val:
            changes["ul.left_foot_score"] = (license.left_foot_score, new_val)
            license.left_foot_score = new_val

    # motivation_scores — key-by-key merge
    existing_ms = dict(license.motivation_scores or {})
    ms_keys = {
        "position":       profile["position"],
        "height_cm":      profile["height_cm"],
        "weight_kg":      profile["weight_kg"],
        "preferred_foot": profile["preferred_foot"],
    }
    ms_changed = False
    for key, val in ms_keys.items():
        if key not in existing_ms or force:
            old = existing_ms.get(key, "MISSING")
            if old != val:
                changes[f"ul.motivation_scores.{key}"] = (old, val)
                existing_ms[key] = val
                ms_changed = True

    if ms_changed:
        license.motivation_scores = existing_ms

    return changes


def run(
    db: Any,
    apply: bool,
    force: bool,
    print_fn=print,
) -> Dict[str, Any]:
    """
    Main patch logic. Returns a summary dict (for testing / assertions).
    db: SQLAlchemy Session
    apply: if False → dry-run (no commits)
    force: if True → overwrite existing values
    """
    from app.models.user import User
    from app.models.license import UserLicense

    # Snapshot XP/credit before any changes
    seed_users = (
        db.query(User)
        .filter(User.email.like(EMAIL_PATTERN))
        .order_by(User.email)
        .all()
    )

    if not seed_users:
        print_fn("[ERROR] No seed players found matching pattern: " + EMAIL_PATTERN)
        return {"found": 0, "patched": 0, "errors": []}

    before_xp      = {u.id: u.xp_balance     for u in seed_users}
    before_credits = {u.id: u.credit_balance  for u in seed_users}

    # Build email → profile lookup
    profile_map = {p["email"]: p for p in SEED_PROFILES}

    results = []
    errors  = []

    for user in seed_users:
        profile = profile_map.get(user.email)
        if not profile:
            print_fn(f"[WARN] No profile defined for {user.email} — skipping")
            continue

        license = (
            db.query(UserLicense)
            .filter(
                UserLicense.user_id == user.id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            )
            .first()
        )
        if not license:
            msg = f"[ERROR] No LFA_FOOTBALL_PLAYER license for {user.email}"
            print_fn(msg)
            errors.append(msg)
            continue

        user_changes    = patch_user_fields(user, profile, force)
        license_changes = patch_license_fields(license, profile, force)
        all_changes     = {**user_changes, **license_changes}

        prefix = "[DRY-RUN]" if not apply else "[APPLY]"
        if all_changes:
            print_fn(f"{prefix} {user.email}")
            for field, (old, new) in all_changes.items():
                print_fn(f"  {field}: {old!r} → {new!r}")
        else:
            print_fn(f"[SKIP] {user.email} — all fields already set (use --force to overwrite)")

        if apply and all_changes:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(license, "motivation_scores")

        results.append({"email": user.email, "changes": all_changes})

    if apply:
        db.commit()
        print_fn("\n[COMMIT] Changes written to DB.")
        _run_assertions(db, before_xp, before_credits, print_fn)
    else:
        db.rollback()
        print_fn("\n[DRY-RUN] No changes written. Run with --apply to apply.")

    return {
        "found":   len(seed_users),
        "patched": sum(1 for r in results if r["changes"]),
        "results": results,
        "errors":  errors,
    }


def _run_assertions(
    db: Any,
    before_xp: Dict[int, Any],
    before_credits: Dict[int, Any],
    print_fn=print,
) -> None:
    """Post-apply assertions. Raises AssertionError if any check fails."""
    from app.models.user import User
    from app.models.license import UserLicense

    seed_users = (
        db.query(User)
        .filter(User.email.like(EMAIL_PATTERN))
        .order_by(User.email)
        .all()
    )

    failures = []

    for u in seed_users:
        ul = (
            db.query(UserLicense)
            .filter(
                UserLicense.user_id == u.id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            )
            .first()
        )
        ms = ul.motivation_scores or {} if ul else {}
        spec_str = u.specialization.value if hasattr(u.specialization, "value") else u.specialization

        checks = [
            (u.nationality      is not None,               f"{u.email}: nationality NULL"),
            (u.gender           is not None,               f"{u.email}: gender NULL"),
            (u.nickname         is not None,               f"{u.email}: nickname NULL"),
            (spec_str == "LFA_FOOTBALL_PLAYER",            f"{u.email}: specialization wrong ({spec_str!r})"),
            (u.position         is not None,               f"{u.email}: position NULL"),
            (ms.get("position") is not None,               f"{u.email}: motivation_scores.position NULL"),
            (ms.get("height_cm") is not None,              f"{u.email}: motivation_scores.height_cm NULL"),
            (ms.get("weight_kg") is not None,              f"{u.email}: motivation_scores.weight_kg NULL"),
            (ms.get("preferred_foot") is not None,         f"{u.email}: motivation_scores.preferred_foot NULL"),
            (ul is not None and ul.right_foot_score is not None, f"{u.email}: right_foot_score NULL"),
            (ul is not None and ul.left_foot_score  is not None, f"{u.email}: left_foot_score NULL"),
            (ul is not None and len(ul.football_skills or {}) == 44,
             f"{u.email}: football_skills tampered (keys={len(ul.football_skills or {})})"),
            (u.xp_balance == before_xp[u.id],
             f"{u.email}: xp_balance changed {before_xp[u.id]} → {u.xp_balance}"),
            (u.credit_balance == before_credits[u.id],
             f"{u.email}: credit_balance changed {before_credits[u.id]} → {u.credit_balance}"),
        ]

        for ok, msg in checks:
            if not ok:
                failures.append(msg)

    if failures:
        print_fn("\n[ASSERTION FAILURES]")
        for f in failures:
            print_fn(f"  FAIL: {f}")
        raise AssertionError(f"{len(failures)} post-run assertion(s) failed — see output above")

    print_fn(f"\n✅ All {len(seed_users) * 14} assertions PASSED ({len(seed_users)}/9 seed players).")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch demo seed player profiles. Default: dry-run."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write changes to DB (default: dry-run only)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite fields that already have values (default: skip existing)",
    )
    args = parser.parse_args()

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/lfa_intern_system",
    )

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine  = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    db      = Session()

    try:
        summary = run(db, apply=args.apply, force=args.force)
    finally:
        db.close()

    print(f"\nSummary: found={summary['found']}, patched={summary['patched']}, errors={len(summary['errors'])}")
    if summary["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
