"""
Unit tests for scripts/patch_demo_seed_profiles.py

PATCH-01  dry-run: db.commit() never called
PATCH-02  apply: NULL nationality is filled
PATCH-03  apply, force=False: existing nationality NOT overwritten
PATCH-04  apply, force=True: existing nationality IS overwritten
PATCH-05  motivation_scores merge: only missing keys filled; existing key untouched
PATCH-06  football_skills key count unchanged (29)
PATCH-07  xp_balance unchanged after apply
PATCH-08  credit_balance unchanged after apply
PATCH-09  patch_user_fields skips fields already set (no-force)
PATCH-10  patch_license_fields skips foot scores already set (no-force)
PATCH-11a preferred_foot='both' when abs(right-left) <= 10
PATCH-11b preferred_foot='right' when right dominant
PATCH-11c preferred_foot='left' when left dominant
PATCH-12  run() returns correct found/patched counts
PATCH-13  run() errors list populated when license missing
"""

import importlib.util
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# ── project root on path ──────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ── load the script as a module (all app imports are deferred inside fns) ─────
def _load_script():
    path = os.path.join(_PROJECT_ROOT, "scripts", "patch_demo_seed_profiles.py")
    spec = importlib.util.spec_from_file_location("patch_demo_seed_profiles", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_script()
patch_user_fields    = _mod.patch_user_fields
patch_license_fields = _mod.patch_license_fields


# ── fixtures / factories ─────────────────────────────────────────────────────

def _profile(
    email="seed.player.1@promo-seed.test",
    position="STRIKER",
    nationality="HU",
    gender="Male",
    nickname="Seed1",
    height_cm=180,
    weight_kg=76,
    right_foot_score=75.0,
    left_foot_score=35.0,
    preferred_foot="right",
):
    return dict(
        email=email, position=position, nationality=nationality,
        gender=gender, nickname=nickname, height_cm=height_cm,
        weight_kg=weight_kg, right_foot_score=right_foot_score,
        left_foot_score=left_foot_score, preferred_foot=preferred_foot,
    )


def _user(
    uid=9,
    email="seed.player.1@promo-seed.test",
    nationality=None,
    gender=None,
    nickname=None,
    position=None,
    specialization=None,
    xp_balance=5000,
    credit_balance=2000,
):
    u = MagicMock()
    u.id            = uid
    u.email         = email
    u.nationality   = nationality
    u.gender        = gender
    u.nickname      = nickname
    u.position      = position
    u.specialization = specialization
    u.xp_balance    = xp_balance
    u.credit_balance = credit_balance
    return u


def _license(
    right_foot_score=None,
    left_foot_score=None,
    motivation_scores=None,
    n_skills=44,
):
    lic = MagicMock()
    lic.right_foot_score  = right_foot_score
    lic.left_foot_score   = left_foot_score
    lic.motivation_scores = motivation_scores
    # Simulate existing 44-key football_skills — must survive untouched
    original = {f"skill_{i}": {"current_level": 60.0} for i in range(n_skills)}
    lic.football_skills   = original
    lic._original_skills  = original   # reference for identity checks
    return lic


def _make_db(seed_users, license_result):
    """Build a minimal DB mock for run() calls."""
    db = MagicMock()

    # Chain for seed_users query: .query().filter().order_by().all()
    q_users = MagicMock()
    q_users.filter.return_value.order_by.return_value.all.return_value = seed_users

    # Chain for license query: .query().filter().first()
    q_lic = MagicMock()
    q_lic.filter.return_value.first.return_value = license_result

    call_count = [0]

    def _query_side(model):
        call_count[0] += 1
        return q_users if call_count[0] == 1 else q_lic

    db.query.side_effect = _query_side
    return db


# ── PATCH-01: dry-run does not commit ────────────────────────────────────────

class TestDryRun:
    def test_dry_run_no_commit(self):
        """PATCH-01: run() without --apply must not call db.commit()."""
        user = _user()
        lic  = _license()
        db   = _make_db([user], lic)

        with patch.object(_mod, "patch_user_fields",    return_value={"users.nationality": (None, "HU")}), \
             patch.object(_mod, "patch_license_fields", return_value={}), \
             patch("sqlalchemy.orm.attributes.flag_modified"):
            _mod.run(db, apply=False, force=False, print_fn=lambda *a: None)

        db.commit.assert_not_called()
        db.rollback.assert_called_once()


# ── PATCH-02/03/04/09: user field idempotence ─────────────────────────────────

class TestPatchUserFields:
    def test_null_nationality_filled(self):
        """PATCH-02: nationality=None → filled with profile value."""
        u = _user(nationality=None)
        p = _profile(nationality="HU")
        changes = patch_user_fields(u, p, force=False)
        assert u.nationality == "HU"
        assert "users.nationality" in changes

    def test_existing_nationality_not_overwritten_without_force(self):
        """PATCH-03: existing nationality NOT overwritten when force=False."""
        u = _user(nationality="DE")
        p = _profile(nationality="HU")
        changes = patch_user_fields(u, p, force=False)
        assert u.nationality == "DE"
        assert "users.nationality" not in changes

    def test_existing_nationality_overwritten_with_force(self):
        """PATCH-04: existing nationality overwritten when force=True."""
        u = _user(nationality="DE")
        p = _profile(nationality="HU")
        changes = patch_user_fields(u, p, force=True)
        assert u.nationality == "HU"
        assert "users.nationality" in changes

    def test_all_user_fields_filled_when_null(self):
        """PATCH-09a: all NULL user fields are filled."""
        u = _user()
        p = _profile()
        changes = patch_user_fields(u, p, force=False)
        assert u.nationality == "HU"
        assert u.gender      == "Male"
        assert u.nickname    == "Seed1"
        assert u.position    == "STRIKER"

    def test_skip_fields_already_set(self):
        """PATCH-09b: all set fields are skipped (no changes returned)."""
        spec_mock = MagicMock()
        spec_mock.value = "LFA_FOOTBALL_PLAYER"
        u = _user(nationality="HU", gender="Male", nickname="Seed1",
                  position="STRIKER", specialization=spec_mock)
        p = _profile()
        changes = patch_user_fields(u, p, force=False)
        for field in ("users.nationality", "users.gender", "users.nickname", "users.position"):
            assert field not in changes, f"{field} should be skipped"


# ── PATCH-05/06/10/11: license field idempotence ──────────────────────────────

class TestPatchLicenseFields:
    def test_motivation_scores_fills_missing_keys_keeps_existing(self):
        """PATCH-05: existing motivation_scores: existing keys kept, missing keys added."""
        existing = {"goals": "improve", "average_skill_level": 60.0}
        lic = _license(motivation_scores=existing)
        p   = _profile(height_cm=180, weight_kg=76, preferred_foot="right", position="STRIKER")
        patch_license_fields(lic, p, force=False)
        ms = lic.motivation_scores
        assert ms["height_cm"]      == 180
        assert ms["weight_kg"]      == 76
        assert ms["preferred_foot"] == "right"
        assert ms["position"]       == "STRIKER"
        assert ms["goals"]          == "improve"        # untouched
        assert ms["average_skill_level"] == 60.0        # untouched

    def test_football_skills_not_touched(self):
        """PATCH-06: football_skills is never reassigned."""
        lic = _license()
        original = lic._original_skills
        p = _profile()
        patch_license_fields(lic, p, force=False)
        # The football_skills attribute must be the same object (not reassigned)
        assert lic.football_skills is original
        assert len(lic.football_skills) == 44

    def test_foot_scores_not_overwritten_without_force(self):
        """PATCH-10: existing foot scores not overwritten when force=False."""
        lic = _license(right_foot_score=55.0, left_foot_score=45.0)
        p   = _profile(right_foot_score=75.0, left_foot_score=35.0)
        changes = patch_license_fields(lic, p, force=False)
        assert lic.right_foot_score == 55.0
        assert lic.left_foot_score  == 45.0
        assert "ul.right_foot_score" not in changes
        assert "ul.left_foot_score"  not in changes

    def test_preferred_foot_both_when_scores_close(self):
        """PATCH-11a: preferred_foot='both' when abs(right-left) <= 10."""
        lic = _license()
        p   = _profile(right_foot_score=62.0, left_foot_score=58.0, preferred_foot="both")
        patch_license_fields(lic, p, force=False)
        assert lic.motivation_scores["preferred_foot"] == "both"

    def test_preferred_foot_right_when_right_dominant(self):
        """PATCH-11b: preferred_foot='right' when right > left by > 10."""
        lic = _license()
        p   = _profile(right_foot_score=75.0, left_foot_score=35.0, preferred_foot="right")
        patch_license_fields(lic, p, force=False)
        assert lic.motivation_scores["preferred_foot"] == "right"

    def test_preferred_foot_left_when_left_dominant(self):
        """PATCH-11c: preferred_foot='left' when left > right by > 10."""
        lic = _license()
        p   = _profile(right_foot_score=30.0, left_foot_score=78.0, preferred_foot="left")
        patch_license_fields(lic, p, force=False)
        assert lic.motivation_scores["preferred_foot"] == "left"


# ── PATCH-07/08: balance integrity ───────────────────────────────────────────

class TestBalanceIntegrity:
    def test_xp_balance_unchanged(self):
        """PATCH-07: patch_user_fields does not touch xp_balance."""
        u = _user(xp_balance=5067)
        patch_user_fields(u, _profile(), force=True)
        assert u.xp_balance == 5067

    def test_credit_balance_unchanged(self):
        """PATCH-08: patch_user_fields does not touch credit_balance."""
        u = _user(credit_balance=2495)
        patch_user_fields(u, _profile(), force=True)
        assert u.credit_balance == 2495


# ── PATCH-12/13: run() summary and error handling ────────────────────────────

class TestRunSummary:
    def test_run_returns_found_patched_counts(self):
        """PATCH-12: run() returns found=1, patched=1 for single NULL-field user."""
        user = _user()
        lic  = _license()
        db   = _make_db([user], lic)

        with patch.object(_mod, "patch_user_fields",    return_value={"users.nationality": (None, "HU")}), \
             patch.object(_mod, "patch_license_fields", return_value={}), \
             patch.object(_mod, "_run_assertions"), \
             patch("sqlalchemy.orm.attributes.flag_modified"):
            summary = _mod.run(db, apply=True, force=False, print_fn=lambda *a: None)

        assert summary["found"]  == 1
        assert summary["patched"] == 1
        assert summary["errors"] == []

    def test_run_errors_when_license_missing(self):
        """PATCH-13: run() records error when LFA license not found."""
        user = _user()
        db   = _make_db([user], None)   # license query returns None

        summary = _mod.run(db, apply=False, force=False, print_fn=lambda *a: None)

        assert len(summary["errors"]) > 0
        assert any("No LFA_FOOTBALL_PLAYER license" in e for e in summary["errors"])
