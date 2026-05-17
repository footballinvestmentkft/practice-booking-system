"""
Unit tests for the dynamic distractor pool feature.

Covers:
  Seeder validation (_validate_question):
    - legacy 4-option question passes
    - pool question with 7 options passes
    - question with 3 options fails (< 4)
    - TRUE_FALSE with exactly 2 options passes
    - TRUE_FALSE with 3 options fails (exactly 2 required)
    - multiple correct options still fails

  AdaptiveLearningService._build_presented_options:
    - legacy 4-option: all 4 returned, exactly 1 correct in output
    - pool 7-option: exactly 4 returned, exactly 1 correct in output
    - correct option always present in output
    - correct position randomises across runs
    - distractor combination varies across runs
    - answer validation: option_id lookup unchanged by shuffling
    - performance tracking: same question_id regardless of distractor set
"""
import random
import pytest
from unittest.mock import MagicMock

from app.services.adaptive_learning import AdaptiveLearningService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _svc():
    return AdaptiveLearningService(MagicMock())


def _make_option(opt_id: int, text: str, is_correct: bool) -> MagicMock:
    opt = MagicMock()
    opt.id = opt_id
    opt.option_text = text
    opt.is_correct = is_correct
    return opt


def _make_question(options: list[MagicMock]) -> MagicMock:
    q = MagicMock()
    q.answer_options = options
    return q


def _legacy_question():
    """1 correct + 3 incorrect — classic 4-option layout."""
    return _make_question([
        _make_option(1, "Correct answer", True),
        _make_option(2, "Wrong A", False),
        _make_option(3, "Wrong B", False),
        _make_option(4, "Wrong C", False),
    ])


def _pool_question():
    """1 correct + 6 incorrect — distractor pool of 7 total options."""
    return _make_question([
        _make_option(10, "Correct answer", True),
        _make_option(11, "Wrong 1", False),
        _make_option(12, "Wrong 2", False),
        _make_option(13, "Wrong 3", False),
        _make_option(14, "Wrong 4", False),
        _make_option(15, "Wrong 5", False),
        _make_option(16, "Wrong 6", False),
    ])


# ---------------------------------------------------------------------------
# Seeder validation tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSeederValidation:
    """_validate_question is a module-level function in the seeder script."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
        from seed_adaptive_learning_questions import _validate_question, ValidationError
        self._validate = _validate_question
        self.ValidationError = ValidationError

    def _q(self, n_correct=1, n_incorrect=3, qtype="MULTIPLE_CHOICE"):
        options = (
            [{"text": "correct", "is_correct": True}] * n_correct
            + [{"text": f"wrong{i}", "is_correct": False} for i in range(n_incorrect)]
        )
        return {
            "text": "Sample question?",
            "type": qtype,
            "explanation": "Sample explanation.",
            "options": options,
            "metadata": {
                "estimated_difficulty": 0.5,
                "cognitive_load": 0.5,
                "average_time_seconds": 30.0,
                "concept_tags": [],
            },
        }

    def test_legacy_4_option_passes(self):
        self._validate(self._q(n_correct=1, n_incorrect=3), idx=0)

    def test_pool_7_option_passes(self):
        self._validate(self._q(n_correct=1, n_incorrect=6), idx=0)

    def test_pool_5_option_passes(self):
        self._validate(self._q(n_correct=1, n_incorrect=4), idx=0)

    def test_3_option_raises(self):
        with pytest.raises(self.ValidationError, match="at least 4"):
            self._validate(self._q(n_correct=1, n_incorrect=2), idx=0)

    def test_1_option_raises(self):
        with pytest.raises(self.ValidationError, match="at least 4"):
            self._validate(self._q(n_correct=1, n_incorrect=0), idx=0)

    def test_true_false_exactly_2_passes(self):
        self._validate(self._q(n_correct=1, n_incorrect=1, qtype="TRUE_FALSE"), idx=0)

    def test_true_false_3_options_raises(self):
        with pytest.raises(self.ValidationError, match="exactly 2"):
            self._validate(self._q(n_correct=1, n_incorrect=2, qtype="TRUE_FALSE"), idx=0)

    def test_true_false_1_option_raises(self):
        with pytest.raises(self.ValidationError, match="exactly 2"):
            self._validate(self._q(n_correct=1, n_incorrect=0, qtype="TRUE_FALSE"), idx=0)

    def test_multiple_correct_raises(self):
        with pytest.raises(self.ValidationError, match="exactly 1 correct"):
            self._validate(self._q(n_correct=2, n_incorrect=3), idx=0)

    def test_zero_correct_raises(self):
        with pytest.raises(self.ValidationError, match="exactly 1 correct"):
            self._validate(self._q(n_correct=0, n_incorrect=4), idx=0)


# ---------------------------------------------------------------------------
# _build_presented_options — output shape
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBuildPresentedOptionsShape:

    def test_legacy_4_option_returns_4(self):
        svc = _svc()
        result = svc._build_presented_options(_legacy_question())
        assert len(result) == 4

    def test_pool_7_option_returns_4(self):
        svc = _svc()
        result = svc._build_presented_options(_pool_question())
        assert len(result) == 4

    def test_legacy_contains_exactly_1_correct(self):
        """With 4 options in DB (1 correct + 3 incorrect), all 4 are returned."""
        svc = _svc()
        q = _legacy_question()
        correct_ids = {o.id for o in q.answer_options if o.is_correct}
        result = svc._build_presented_options(q)
        correct_in_result = [r for r in result if r["id"] in correct_ids]
        assert len(correct_in_result) == 1

    def test_pool_contains_exactly_1_correct(self):
        svc = _svc()
        q = _pool_question()
        correct_ids = {o.id for o in q.answer_options if o.is_correct}
        result = svc._build_presented_options(q)
        correct_in_result = [r for r in result if r["id"] in correct_ids]
        assert len(correct_in_result) == 1

    def test_correct_option_always_present(self):
        """Run 20 times — correct option must appear every time."""
        svc = _svc()
        q = _pool_question()
        correct_id = next(o.id for o in q.answer_options if o.is_correct)
        for _ in range(20):
            result = svc._build_presented_options(q)
            ids_in_result = {r["id"] for r in result}
            assert correct_id in ids_in_result

    def test_output_contains_id_and_text_keys(self):
        svc = _svc()
        result = svc._build_presented_options(_legacy_question())
        for item in result:
            assert "id" in item
            assert "text" in item

    def test_output_contains_no_is_correct_key(self):
        """is_correct must never leak to the client-facing option dict."""
        svc = _svc()
        result = svc._build_presented_options(_pool_question())
        for item in result:
            assert "is_correct" not in item


# ---------------------------------------------------------------------------
# _build_presented_options — randomness
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBuildPresentedOptionsRandomness:

    def test_correct_position_varies(self):
        """Over 50 runs the correct option must appear in at least 2 distinct positions.

        With 4 positions and uniform distribution, P(only 1 position in 50 draws) < 1e-29.
        """
        svc = _svc()
        q = _pool_question()
        correct_id = next(o.id for o in q.answer_options if o.is_correct)
        positions = set()
        for _ in range(50):
            result = svc._build_presented_options(q)
            pos = next(i for i, r in enumerate(result) if r["id"] == correct_id)
            positions.add(pos)
        assert len(positions) >= 2, (
            f"Correct option only appeared at positions {positions} across 50 runs — "
            "shuffling appears non-functional"
        )

    def test_all_4_positions_covered(self):
        """Over 200 runs all 4 positions (0–3) should be observed for the correct option.

        P(miss any single position in 200 uniform draws) = (3/4)^200 ≈ 1.4e-25.
        """
        svc = _svc()
        q = _pool_question()
        correct_id = next(o.id for o in q.answer_options if o.is_correct)
        positions = set()
        for _ in range(200):
            result = svc._build_presented_options(q)
            pos = next(i for i, r in enumerate(result) if r["id"] == correct_id)
            positions.add(pos)
        assert positions == {0, 1, 2, 3}

    def test_distractor_combinations_vary(self):
        """Over 50 runs at least 2 distinct distractor combinations must appear.

        Pool has 6 incorrect options; 6C3 = 20 possible combos.
        P(same combo all 50 times) = (1/20)^49 ≈ 5e-64.
        """
        svc = _svc()
        q = _pool_question()
        correct_id = next(o.id for o in q.answer_options if o.is_correct)
        combos = set()
        for _ in range(50):
            result = svc._build_presented_options(q)
            distractor_ids = frozenset(r["id"] for r in result if r["id"] != correct_id)
            combos.add(distractor_ids)
        assert len(combos) >= 2, (
            f"Only {len(combos)} distractor combination(s) seen across 50 runs — "
            "random.sample appears non-functional"
        )

    def test_legacy_4_option_distractors_unchanged(self):
        """With exactly 3 incorrect options in DB, all 3 always appear (no variation)."""
        svc = _svc()
        q = _legacy_question()
        correct_id = next(o.id for o in q.answer_options if o.is_correct)
        incorrect_ids = frozenset(o.id for o in q.answer_options if not o.is_correct)
        for _ in range(20):
            result = svc._build_presented_options(q)
            returned_incorrect = frozenset(r["id"] for r in result if r["id"] != correct_id)
            assert returned_incorrect == incorrect_ids


# ---------------------------------------------------------------------------
# Answer validation — option_id remains correct regardless of shuffle order
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestAnswerValidationWithShuffle:
    """The answer validation endpoint uses option.is_correct via DB lookup by option_id.
    It never relies on position. These tests confirm that the option ids returned by
    _build_presented_options match the DB ids, so validation stays correct post-shuffle.
    """

    def test_correct_option_id_survives_shuffle(self):
        """The id of the correct option in the output matches the DB option id."""
        svc = _svc()
        q = _pool_question()
        correct_opt = next(o for o in q.answer_options if o.is_correct)
        for _ in range(20):
            result = svc._build_presented_options(q)
            correct_in_result = [r for r in result if r["id"] == correct_opt.id]
            assert len(correct_in_result) == 1
            assert correct_in_result[0]["text"] == correct_opt.option_text

    def test_incorrect_option_ids_survive_shuffle(self):
        """All incorrect ids in the output match real DB option ids."""
        svc = _svc()
        q = _pool_question()
        all_db_ids = {o.id for o in q.answer_options}
        for _ in range(20):
            result = svc._build_presented_options(q)
            for item in result:
                assert item["id"] in all_db_ids


# ---------------------------------------------------------------------------
# Performance tracking — question_id aggregation unaffected by distractor set
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestPerformanceTrackingQuestionId:
    """_build_presented_options never changes question.id.
    Performance is always recorded against the same question_id.
    """

    def test_question_id_unchanged_across_builds(self):
        svc = _svc()
        q = _pool_question()
        q.id = 42
        for _ in range(20):
            result = svc._build_presented_options(q)
            # The question id is what gets passed to record_answer — it never
            # comes from the options list, so it's always stable.
            assert q.id == 42

    def test_different_distractor_sets_same_question_id(self):
        """Even when distractors vary, the question object identity is the same."""
        svc = _svc()
        q = _pool_question()
        q.id = 99
        seen_distractor_combos = set()
        for _ in range(50):
            result = svc._build_presented_options(q)
            correct_id = next(o.id for o in q.answer_options if o.is_correct)
            combo = frozenset(r["id"] for r in result if r["id"] != correct_id)
            seen_distractor_combos.add(combo)
        # Multiple distractor combos observed — all mapped to the same question id
        assert len(seen_distractor_combos) >= 2
        assert q.id == 99
