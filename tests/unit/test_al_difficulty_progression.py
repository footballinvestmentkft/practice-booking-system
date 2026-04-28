"""
CI validation — Adaptive Learning difficulty progression gates.

Tests three invariants introduced with the difficulty-hierarchy UX feature:

1. Topic dicts must carry a `difficulty` key with a valid EASY/MEDIUM/HARD value.
2. `_DIFF_SORT` produces a strictly non-decreasing difficulty order within every
   category (EASY=0, MEDIUM=1, HARD=2) — same logic as the live route.
3. No (module, topic, difficulty) triple is duplicated within a single category —
   a duplicate would show the same card twice in the picker.

All tests are pure logic / data-structure tests — no DB, no HTTP server needed.
Fast enough for every push; extensible for future AL content invariants.
"""

import pytest

# ── The sort map used in the live route (adaptive_learning.py:144) ────────────
_DIFF_SORT = {"EASY": 0, "MEDIUM": 1, "HARD": 2}
_VALID_DIFFICULTIES = set(_DIFF_SORT.keys())


def _apply_diff_sort(topics: list[dict]) -> list[dict]:
    """Mirror of the live route sort — mutates a copy, not the original."""
    return sorted(topics, key=lambda t: _DIFF_SORT.get(t["difficulty"], 9))


# ── Fixtures — representative topic lists ──────────────────────────────────────

def _make_topic(module: str, topic: str, difficulty: str, quiz_id: int = 1) -> dict:
    return {
        "module": module,
        "topic": topic,
        "quiz_id": quiz_id,
        "question_count": 10,
        "difficulty": difficulty,
    }


@pytest.fixture
def mixed_order_topics() -> list[dict]:
    """Topics arriving in DB insertion order (not sorted) — simulates raw query results."""
    return [
        _make_topic("Module A", "Topic A — Advanced",      "HARD",   quiz_id=3),
        _make_topic("Module A", "Topic A — Intermediate",  "MEDIUM", quiz_id=2),
        _make_topic("Module A", "Topic A — Easy",          "EASY",   quiz_id=1),
        _make_topic("Module B", "Topic B — Hard",          "HARD",   quiz_id=6),
        _make_topic("Module B", "Topic B — Easy",          "EASY",   quiz_id=4),
        _make_topic("Module B", "Topic B — Medium",        "MEDIUM", quiz_id=5),
    ]


@pytest.fixture
def single_difficulty_topics() -> list[dict]:
    """All topics share the same difficulty — sort must be stable (no change)."""
    return [
        _make_topic("Module X", "Topic X1", "EASY", quiz_id=10),
        _make_topic("Module X", "Topic X2", "EASY", quiz_id=11),
        _make_topic("Module X", "Topic X3", "EASY", quiz_id=12),
    ]


@pytest.fixture
def multi_category_available_topics() -> dict[str, list[dict]]:
    """Simulates the `available_topics` dict that the route sends to the template."""
    return {
        "LESSON": [
            _make_topic("Edzéselmélet alapjai", "Edzéselmélet — Haladó",  "HARD",   quiz_id=68),
            _make_topic("Edzéselmélet alapjai", "Edzéselmélet — Alapok",  "EASY",   quiz_id=66),
            _make_topic("Edzéselmélet alapjai", "Edzéselmélet — Középszint", "MEDIUM", quiz_id=67),
            _make_topic("Motoros képességek",   "Motoros képességek — Haladó", "HARD", quiz_id=57),
            _make_topic("Motoros képességek",   "Motoros képességek — Alapok", "EASY", quiz_id=55),
        ],
        "GENERAL": [
            _make_topic("Általános tudás", "Általános — HARD",   "HARD",   quiz_id=90),
            _make_topic("Általános tudás", "Általános — EASY",   "EASY",   quiz_id=88),
            _make_topic("Általános tudás", "Általános — MEDIUM", "MEDIUM", quiz_id=89),
        ],
    }


# ── 1. Difficulty field presence & valid values ────────────────────────────────

class TestDifficultyFieldSchema:
    """Every topic dict must carry a valid `difficulty` key."""

    def test_difficulty_key_present(self, mixed_order_topics):
        for t in mixed_order_topics:
            assert "difficulty" in t, f"topic {t['topic']!r} missing 'difficulty' key"

    def test_difficulty_values_are_valid(self, mixed_order_topics):
        for t in mixed_order_topics:
            assert t["difficulty"] in _VALID_DIFFICULTIES, (
                f"topic {t['topic']!r} has invalid difficulty {t['difficulty']!r}; "
                f"expected one of {_VALID_DIFFICULTIES}"
            )

    def test_all_three_difficulties_representable(self):
        topics = [
            _make_topic("M", "Easy topic",   "EASY",   quiz_id=1),
            _make_topic("M", "Medium topic", "MEDIUM", quiz_id=2),
            _make_topic("M", "Hard topic",   "HARD",   quiz_id=3),
        ]
        found = {t["difficulty"] for t in topics}
        assert found == _VALID_DIFFICULTIES

    def test_multi_category_all_valid(self, multi_category_available_topics):
        for cat, topics in multi_category_available_topics.items():
            for t in topics:
                assert t.get("difficulty") in _VALID_DIFFICULTIES, (
                    f"[{cat}] topic {t['topic']!r} invalid difficulty {t.get('difficulty')!r}"
                )


# ── 2. Deterministic ordering: EASY → MEDIUM → HARD ──────────────────────────

class TestDifficultyOrdering:
    """After `_DIFF_SORT` is applied, topics must be non-decreasing by difficulty."""

    def _assert_non_decreasing(self, topics: list[dict], label: str = "") -> None:
        for i in range(1, len(topics)):
            prev = _DIFF_SORT[topics[i - 1]["difficulty"]]
            curr = _DIFF_SORT[topics[i]["difficulty"]]
            assert prev <= curr, (
                f"{label}ordering violation at index {i}: "
                f"{topics[i-1]['difficulty']} ({prev}) > {topics[i]['difficulty']} ({curr})"
            )

    def test_mixed_order_sorted_correctly(self, mixed_order_topics):
        sorted_topics = _apply_diff_sort(mixed_order_topics)
        self._assert_non_decreasing(sorted_topics, "mixed_order: ")

    def test_sort_is_idempotent(self, mixed_order_topics):
        once = _apply_diff_sort(mixed_order_topics)
        twice = _apply_diff_sort(once)
        assert once == twice, "Sorting twice must produce the same result"

    def test_already_sorted_unchanged(self, single_difficulty_topics):
        result = _apply_diff_sort(single_difficulty_topics)
        assert result == single_difficulty_topics

    def test_hard_first_gets_reordered(self):
        topics = [
            _make_topic("M", "hard",   "HARD",   quiz_id=3),
            _make_topic("M", "medium", "MEDIUM", quiz_id=2),
            _make_topic("M", "easy",   "EASY",   quiz_id=1),
        ]
        result = _apply_diff_sort(topics)
        difficulties = [t["difficulty"] for t in result]
        assert difficulties == ["EASY", "MEDIUM", "HARD"]

    def test_multi_category_each_sorted(self, multi_category_available_topics):
        """Each category in available_topics must be sorted independently."""
        for cat, topics in multi_category_available_topics.items():
            sorted_topics = _apply_diff_sort(topics)
            self._assert_non_decreasing(sorted_topics, f"[{cat}] ")

    def test_sort_preserves_all_topics(self, mixed_order_topics):
        sorted_topics = _apply_diff_sort(mixed_order_topics)
        assert len(sorted_topics) == len(mixed_order_topics)
        assert {t["quiz_id"] for t in sorted_topics} == {t["quiz_id"] for t in mixed_order_topics}


# ── 3. No duplicate topics within a category ─────────────────────────────────

class TestNoDuplicateTopics:
    """(module, topic, difficulty) must be unique within a category after sort."""

    def _find_duplicates(self, topics: list[dict]) -> list[tuple]:
        seen: set[tuple] = set()
        dupes = []
        for t in topics:
            key = (t["module"], t["topic"], t["difficulty"])
            if key in seen:
                dupes.append(key)
            seen.add(key)
        return dupes

    def test_no_duplicates_in_clean_data(self, mixed_order_topics):
        dupes = self._find_duplicates(mixed_order_topics)
        assert dupes == [], f"Unexpected duplicates: {dupes}"

    def test_duplicate_detected_correctly(self):
        """Sanity-check: the duplicate-finder must catch real duplicates."""
        topics = [
            _make_topic("M", "Topic", "EASY", quiz_id=1),
            _make_topic("M", "Topic", "EASY", quiz_id=1),  # intentional dup
        ]
        dupes = self._find_duplicates(topics)
        assert len(dupes) == 1

    def test_same_topic_different_difficulties_not_duplicates(self):
        """Same module+topic with different difficulties are distinct cards."""
        topics = [
            _make_topic("M", "Topic", "EASY",   quiz_id=1),
            _make_topic("M", "Topic", "MEDIUM", quiz_id=2),
            _make_topic("M", "Topic", "HARD",   quiz_id=3),
        ]
        dupes = self._find_duplicates(topics)
        assert dupes == []

    def test_multi_category_no_duplicates(self, multi_category_available_topics):
        for cat, topics in multi_category_available_topics.items():
            dupes = self._find_duplicates(topics)
            assert dupes == [], f"[{cat}] duplicates found: {dupes}"

    def test_quiz_id_uniqueness_within_category(self, mixed_order_topics):
        """Each quiz_id must appear at most once per category."""
        ids = [t["quiz_id"] for t in mixed_order_topics]
        assert len(ids) == len(set(ids)), f"Duplicate quiz_ids: {ids}"


# ── 4. Template rendering: difficulty data reaches the template ────────────────

class TestDifficultyInTemplate:
    """Rendered HTML must expose difficulty data that JS needs for grouping."""

    def _render(self, available_topics: dict | None = None) -> str:
        import os
        from jinja2 import Environment, FileSystemLoader
        from app.models.quiz import QuizCategory

        templates_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "app", "templates")
        )

        class _Role:
            value = "STUDENT"

        class _Spec:
            value = "LFA_FOOTBALL_PLAYER"

        class _URL:
            path = "/adaptive-learning/session"

        class _Request:
            url = _URL()

        class _User:
            credit_balance = 500
            name = "CI User"
            role = _Role()
            specialization = _Spec()
            onboarding_completed = True

        env = Environment(loader=FileSystemLoader(templates_dir), autoescape=False)
        t = env.get_template("adaptive_learning_session.html")
        return t.render(
            request=_Request(),
            user=_User(),
            spec_dashboard_url="/dashboard/lfa-football-player",
            spec_dashboard_icon="⚽",
            available_categories=[QuizCategory.LESSON],
            session_language="en",
            available_topics=available_topics or {},
            easy_completed_modules=[],
        )

    def test_easy_completed_modules_var_in_output(self):
        html = self._render()
        assert "easyCompletedModules" in html

    def test_diff_sort_constants_in_output(self):
        html = self._render()
        assert "_DIFF_ORDER" in html or "EASY" in html, (
            "Difficulty order constants must appear in rendered JS"
        )

    def test_als_diff_section_css_in_output(self):
        html = self._render()
        assert "als-diff-section-header" in html, (
            "Section header CSS class must be present in rendered template"
        )

    def test_diff_labels_hu_and_en_in_output(self):
        html = self._render()
        assert "Alapszint" in html or "Easy" in html, (
            "At least one difficulty label must appear in rendered JS"
        )

    def test_available_topics_json_injected(self):
        topics = {
            "LESSON": [_make_topic("Module A", "Topic Easy", "EASY", quiz_id=1)]
        }
        html = self._render(available_topics=topics)
        assert "availableTopics" in html
        assert "EASY" in html
