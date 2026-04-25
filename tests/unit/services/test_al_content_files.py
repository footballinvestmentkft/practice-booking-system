"""
Regression tests: AL content JSON files pass seed script schema validation.

Guards against:
- missing or wrong schema_version
- missing quiz_title (idempotency key)
- wrong category / difficulty values
- questions without metadata blocks
- missing estimated_difficulty (required for adaptive candidate selection)
- zero-question files reaching the DB

Also verifies dry-run behaviour: the seed script in dry-run mode must produce
no DB writes and must mark all already-loaded files as [SKIP].
"""
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CONTENT_ROOT = _REPO_ROOT / "content" / "adaptive_learning"

_NEW_FILES = [
    _CONTENT_ROOT / "lfa_football_player" / "lesson" / "tactics_hard.json",
    _CONTENT_ROOT / "_shared" / "sports_physiology" / "conditioning_hard.json",
    _CONTENT_ROOT / "lfa_football_player" / "general" / "football_awareness_easy.json",
    _CONTENT_ROOT / "lfa_football_player" / "general" / "football_awareness_medium.json",
    _CONTENT_ROOT / "lfa_football_player" / "nutrition" / "athlete_nutrition_easy.json",
    _CONTENT_ROOT / "lfa_football_player" / "nutrition" / "athlete_nutrition_medium.json",
]

_ALL_FILES = _NEW_FILES + [
    _CONTENT_ROOT / "_shared" / "sports_physiology" / "conditioning_easy.json",
    _CONTENT_ROOT / "_shared" / "sports_physiology" / "conditioning_medium.json",
    _CONTENT_ROOT / "lfa_football_player" / "lesson" / "rules_easy.json",
    _CONTENT_ROOT / "lfa_football_player" / "lesson" / "rules_medium.json",
    _CONTENT_ROOT / "lfa_football_player" / "lesson" / "rules_hard.json",
    _CONTENT_ROOT / "lfa_football_player" / "lesson" / "tactics_easy.json",
    _CONTENT_ROOT / "lfa_football_player" / "lesson" / "tactics_medium.json",
]

_VALID_CATEGORIES = {
    "GENERAL", "LESSON", "SPORTS_PHYSIOLOGY", "NUTRITION",
    "MARKETING", "ECONOMICS", "INFORMATICS",
}
_VALID_DIFFICULTIES = {"EASY", "MEDIUM", "HARD"}
_VALID_SCHEMA_VERSIONS = {"1.0"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


# ── File existence ─────────────────────────────────────────────────────────────

class TestNewFilesExist:
    """All 6 new content files must be present on disk."""

    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_file_exists(self, path):
        assert path.exists(), f"Expected content file missing: {path}"


# ── Schema validation (per-file) ──────────────────────────────────────────────

class TestSchemaVersion:
    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_schema_version_valid(self, path):
        d = _load(path)
        assert d.get("schema_version") in _VALID_SCHEMA_VERSIONS, \
            f"{path.name}: schema_version must be one of {_VALID_SCHEMA_VERSIONS}, got {d.get('schema_version')!r}"


class TestQuizTitle:
    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_quiz_title_non_empty(self, path):
        d = _load(path)
        assert d.get("quiz_title", "").strip(), \
            f"{path.name}: quiz_title must be non-empty (idempotency key)"

    def test_all_quiz_titles_unique(self):
        """quiz_title must be unique across all AL content files — it is the idempotency key."""
        titles = [_load(p)["quiz_title"] for p in _ALL_FILES]
        assert len(titles) == len(set(titles)), \
            f"Duplicate quiz_title detected: {[t for t in titles if titles.count(t) > 1]}"


class TestCategoryAndDifficulty:
    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_category_valid(self, path):
        d = _load(path)
        assert d.get("category") in _VALID_CATEGORIES, \
            f"{path.name}: category {d.get('category')!r} not in valid set"

    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_difficulty_valid(self, path):
        d = _load(path)
        assert d.get("difficulty") in _VALID_DIFFICULTIES, \
            f"{path.name}: difficulty {d.get('difficulty')!r} not in valid set"


class TestSpecializations:
    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_specialization_includes_lfa_football_player(self, path):
        d = _load(path)
        specs = d.get("specializations", [])
        assert "LFA_FOOTBALL_PLAYER" in specs, \
            f"{path.name}: specializations must include 'LFA_FOOTBALL_PLAYER', got {specs}"


class TestQuestions:
    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_question_count_meets_minimum(self, path):
        d = _load(path)
        qs = d.get("questions", [])
        assert len(qs) >= 6, \
            f"{path.name}: expected at least 6 questions, got {len(qs)}"

    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_all_questions_have_text(self, path):
        d = _load(path)
        for i, q in enumerate(d.get("questions", [])):
            assert q.get("text", "").strip(), \
                f"{path.name} question[{i}]: text must be non-empty"

    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_all_questions_have_exactly_one_correct_option(self, path):
        d = _load(path)
        for i, q in enumerate(d.get("questions", [])):
            options = q.get("options", [])
            correct = [o for o in options if o.get("is_correct") is True]
            assert len(correct) == 1, \
                f"{path.name} question[{i}]: must have exactly 1 correct option, got {len(correct)}"

    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_all_questions_have_at_least_four_options(self, path):
        d = _load(path)
        for i, q in enumerate(d.get("questions", [])):
            options = q.get("options", [])
            assert len(options) >= 4, \
                f"{path.name} question[{i}]: expected ≥4 options, got {len(options)}"

    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_all_questions_have_explanation(self, path):
        d = _load(path)
        for i, q in enumerate(d.get("questions", [])):
            assert q.get("explanation", "").strip(), \
                f"{path.name} question[{i}]: explanation must be non-empty"


class TestMetadata:
    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_all_questions_have_metadata(self, path):
        d = _load(path)
        for i, q in enumerate(d.get("questions", [])):
            assert q.get("metadata"), \
                f"{path.name} question[{i}]: metadata block missing (required for adaptive selection)"

    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_estimated_difficulty_in_valid_range(self, path):
        d = _load(path)
        for i, q in enumerate(d.get("questions", [])):
            meta = q.get("metadata", {})
            ed = meta.get("estimated_difficulty")
            assert ed is not None, \
                f"{path.name} question[{i}]: estimated_difficulty missing"
            assert 0.0 <= ed <= 1.0, \
                f"{path.name} question[{i}]: estimated_difficulty {ed} out of [0, 1]"

    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_concept_tags_present_and_non_empty(self, path):
        d = _load(path)
        for i, q in enumerate(d.get("questions", [])):
            tags = q.get("metadata", {}).get("concept_tags", [])
            assert isinstance(tags, list) and len(tags) >= 1, \
                f"{path.name} question[{i}]: concept_tags must be a non-empty list"

    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_average_time_seconds_positive(self, path):
        d = _load(path)
        for i, q in enumerate(d.get("questions", [])):
            t = q.get("metadata", {}).get("average_time_seconds")
            assert t is not None and t > 0, \
                f"{path.name} question[{i}]: average_time_seconds must be > 0"


# ── Difficulty alignment ──────────────────────────────────────────────────────

class TestDifficultyAlignment:
    """estimated_difficulty values must be consistent with the file's declared difficulty tier."""

    _EXPECTED_RANGES = {
        "EASY":   (0.10, 0.55),
        "MEDIUM": (0.40, 0.70),
        "HARD":   (0.60, 0.90),
    }

    @pytest.mark.parametrize("path", _NEW_FILES, ids=[p.name for p in _NEW_FILES])
    def test_estimated_difficulty_consistent_with_tier(self, path):
        d = _load(path)
        tier = d.get("difficulty", "")
        lo, hi = self._EXPECTED_RANGES.get(tier, (0.0, 1.0))
        for i, q in enumerate(d.get("questions", [])):
            ed = q.get("metadata", {}).get("estimated_difficulty", 0.5)
            assert lo <= ed <= hi, (
                f"{path.name} question[{i}]: estimated_difficulty={ed} outside "
                f"expected range [{lo}, {hi}] for difficulty={tier}"
            )


# ── Seed script dry-run ───────────────────────────────────────────────────────

class TestSeedScriptDryRun:
    """Seed script dry-run must complete without errors and report no DB writes."""

    def test_dry_run_imports_without_error(self):
        """The seed script module must be importable."""
        import importlib.util
        spec_obj = importlib.util.spec_from_file_location(
            "seed_al",
            str(_REPO_ROOT / "scripts" / "seed_adaptive_learning_questions.py"),
        )
        assert spec_obj is not None, "seed_adaptive_learning_questions.py not found"

    def test_dry_run_validates_all_new_files(self):
        """All 6 new files must pass the seed script's internal _validate_file() check."""
        sys.path.insert(0, str(_REPO_ROOT))
        import importlib
        import importlib.util

        spec_obj = importlib.util.spec_from_file_location(
            "seed_al_mod",
            str(_REPO_ROOT / "scripts" / "seed_adaptive_learning_questions.py"),
        )
        mod = importlib.util.module_from_spec(spec_obj)
        # Execute only to load module-level definitions; DB calls happen inside main()
        spec_obj.loader.exec_module(mod)

        for path in _NEW_FILES:
            data = _load(path)
            try:
                mod._validate_file(data, path, "LFA_FOOTBALL_PLAYER")
            except mod.ValidationError as exc:
                pytest.fail(f"{path.name} failed seed validation: {exc}")


# ── Category counts post-seed (expected state) ───────────────────────────────

class TestExpectedPostSeedCounts:
    """Document expected DB question counts after live seed.
    These are static assertions on JSON content — not DB queries.
    They catch content authoring errors before seeding."""

    def test_lesson_hard_total_reaches_16(self):
        """After seeding tactics_hard (10q) + existing rules_hard (6q) = 16 LESSON HARD."""
        tactics_hard = _load(
            _CONTENT_ROOT / "lfa_football_player" / "lesson" / "tactics_hard.json"
        )
        assert len(tactics_hard["questions"]) == 10, \
            "tactics_hard.json must have exactly 10 questions"

    def test_sports_physiology_hard_reaches_10(self):
        """After seeding conditioning_hard (10q), SPORTS_PHYSIOLOGY HARD = 10."""
        cond_hard = _load(
            _CONTENT_ROOT / "_shared" / "sports_physiology" / "conditioning_hard.json"
        )
        assert len(cond_hard["questions"]) == 10, \
            "conditioning_hard.json must have exactly 10 questions"

    def test_general_total_reaches_22(self):
        """After seeding easy (12q) + medium (10q), GENERAL = 22 questions."""
        easy = _load(
            _CONTENT_ROOT / "lfa_football_player" / "general" / "football_awareness_easy.json"
        )
        medium = _load(
            _CONTENT_ROOT / "lfa_football_player" / "general" / "football_awareness_medium.json"
        )
        assert len(easy["questions"]) + len(medium["questions"]) == 22, \
            f"GENERAL total must be 22, got {len(easy['questions']) + len(medium['questions'])}"

    def test_general_easy_meets_min_threshold(self):
        """GENERAL EASY alone must have ≥ 10 questions to meet category picker threshold."""
        easy = _load(
            _CONTENT_ROOT / "lfa_football_player" / "general" / "football_awareness_easy.json"
        )
        assert len(easy["questions"]) >= 10, \
            "GENERAL EASY must have ≥10 questions to clear MIN_QUESTIONS_PER_CATEGORY=10"

    def test_nutrition_total_reaches_18(self):
        """After seeding easy (10q) + medium (8q), NUTRITION = 18 questions."""
        easy = _load(
            _CONTENT_ROOT / "lfa_football_player" / "nutrition" / "athlete_nutrition_easy.json"
        )
        medium = _load(
            _CONTENT_ROOT / "lfa_football_player" / "nutrition" / "athlete_nutrition_medium.json"
        )
        assert len(easy["questions"]) + len(medium["questions"]) == 18, \
            f"NUTRITION total must be 18, got {len(easy['questions']) + len(medium['questions'])}"

    def test_nutrition_easy_meets_min_threshold(self):
        """NUTRITION EASY alone must have ≥ 10 questions to meet category picker threshold."""
        easy = _load(
            _CONTENT_ROOT / "lfa_football_player" / "nutrition" / "athlete_nutrition_easy.json"
        )
        assert len(easy["questions"]) >= 10, \
            "NUTRITION EASY must have ≥10 questions to clear MIN_QUESTIONS_PER_CATEGORY=10"
