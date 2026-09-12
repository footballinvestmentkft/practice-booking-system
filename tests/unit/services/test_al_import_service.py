"""Unit + integration tests for app.services.al_import_service.

ALS-01  validate_files — single valid v1.0 file returns ImportReport(total_ok=1)
ALS-02  validate_files — file with unknown spec returns total_error=1
ALS-03  validate_files — file with bad JSON returns total_error=1
ALS-04  validate_files — file too large returns total_error=1
ALS-05  validate_files — quiz already in DB returns total_skip=1
ALS-06  validate_files — mixed batch (ok/skip/error) totals correct
ALS-07  validate_files — v2.0 file with correct_variants/distractor_pool ok
ALS-08  validate_files — apply_payload_json is valid JSON with one item per ok file
ALS-09  apply_import — creates quiz + questions + options for a v1.0 item
ALS-10  apply_import — idempotency: re-applying same payload skips existing quiz
ALS-11  apply_import — re-validates payload; tampered quiz_title still blocked
ALS-12  apply_import — writes ALImportLog row with correct counts
"""
import json
import uuid
import pytest

from app.services.al_import_service import (
    validate_files,
    apply_import,
    ValidationError,
)
from app.models.quiz import Quiz, QuizQuestion, QuizAnswerOption, OptionType


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _v1_file(
    title="Test Quiz ALS",
    spec="LFA_FOOTBALL_PLAYER",
    n_questions=2,
) -> bytes:
    questions = []
    for i in range(n_questions):
        questions.append({
            "text": f"Question {i}?",
            "type": "MULTIPLE_CHOICE",
            "explanation": f"Explanation {i}.",
            "options": [
                {"text": "Correct", "is_correct": True},
                {"text": "W1", "is_correct": False},
                {"text": "W2", "is_correct": False},
                {"text": "W3", "is_correct": False},
            ],
            "metadata": {
                "estimated_difficulty": 0.5,
                "cognitive_load": 0.5,
                "average_time_seconds": 30.0,
                "concept_tags": [],
            },
        })
    data = {
        "schema_version": "1.0",
        "specializations": [spec],
        "quiz_title": title,
        "category": "GENERAL",
        "difficulty": "MEDIUM",
        "language": "en",
        "topic": "Test",
        "module": "Module A",
        "questions": questions,
    }
    return json.dumps(data).encode()


def _v2_file(
    title="Test Quiz ALS V2",
    spec="LFA_FOOTBALL_PLAYER",
) -> bytes:
    data = {
        "schema_version": "2.0",
        "specializations": [spec],
        "quiz_title": title,
        "category": "LESSON",
        "difficulty": "HARD",
        "language": "hu",
        "topic": "Advanced",
        "module": "Module B",
        "questions": [
            {
                "text": "V2 question?",
                "type": "MULTIPLE_CHOICE",
                "explanation": "V2 explanation.",
                "correct_variants": ["Correct A", "Correct B"],
                "distractor_pool": [f"Wrong {i}" for i in range(6)],
                "metadata": {
                    "estimated_difficulty": 0.7,
                    "cognitive_load": 0.6,
                    "average_time_seconds": 40.0,
                    "concept_tags": ["tag1"],
                },
            }
        ],
    }
    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# ALS-01 to ALS-08 — validate_files (pure / DB skip-check via postgres_db)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidateFilesUnit:
    """ALS-01 to ALS-04 — no DB writes, db mock sufficient for skip-check."""

    @pytest.fixture
    def mock_db(self, mocker):
        db = mocker.MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        return db

    def test_als01_valid_v1_file(self, mock_db):
        """ALS-01: single valid v1.0 file → total_ok=1, one item in payload."""
        report = validate_files([("quiz.json", _v1_file())], "LFA_FOOTBALL_PLAYER", mock_db)
        assert report.total_ok == 1
        assert report.total_error == 0
        assert report.total_skip == 0
        assert report.quizzes_to_create == 1
        assert report.questions_to_create == 2

    def test_als02_wrong_spec(self, mock_db):
        """ALS-02: file listing only LFA_FOOTBALL_COACH → error for PLAYER spec."""
        content = _v1_file(spec="LFA_FOOTBALL_COACH")
        report = validate_files([("q.json", content)], "LFA_FOOTBALL_PLAYER", mock_db)
        assert report.total_error == 1
        assert report.total_ok == 0

    def test_als03_bad_json(self, mock_db):
        """ALS-03: malformed JSON bytes → total_error=1."""
        report = validate_files([("bad.json", b"{not json}")], "LFA_FOOTBALL_PLAYER", mock_db)
        assert report.total_error == 1
        assert report.files[0].error_message is not None

    def test_als04_file_too_large(self, mock_db):
        """ALS-04: payload exceeding 256 KB → total_error=1 without parse attempt."""
        huge = b"x" * (256 * 1024 + 1)
        report = validate_files([("big.json", huge)], "LFA_FOOTBALL_PLAYER", mock_db)
        assert report.total_error == 1
        assert "too large" in report.files[0].error_message.lower()


@pytest.mark.integration
class TestValidateFilesIntegration:
    """ALS-05 to ALS-08 — require real postgres_db for skip-check."""

    def test_als05_existing_quiz_is_skipped(self, postgres_db):
        """ALS-05: quiz with same title already in DB → total_skip=1."""
        from app.models.quiz import Quiz, QuizCategory, QuizDifficulty

        existing = Quiz(
            title="Test Quiz ALS",
            category=QuizCategory.GENERAL,
            difficulty=QuizDifficulty.MEDIUM,
            time_limit_minutes=20,
            xp_reward=50,
            passing_score=70.0,
            language="en",
        )
        postgres_db.add(existing)
        postgres_db.commit()

        report = validate_files([("q.json", _v1_file())], "LFA_FOOTBALL_PLAYER", postgres_db)
        assert report.total_skip == 1
        assert report.total_ok == 0

    def test_als06_mixed_batch(self, postgres_db):
        """ALS-06: one ok + one skip + one error → correct totals."""
        from app.models.quiz import Quiz, QuizCategory, QuizDifficulty

        existing = Quiz(
            title="Existing Quiz",
            category=QuizCategory.GENERAL,
            difficulty=QuizDifficulty.MEDIUM,
            time_limit_minutes=20,
            xp_reward=50,
            passing_score=70.0,
            language="en",
        )
        postgres_db.add(existing)
        postgres_db.commit()

        files = [
            ("new.json",    _v1_file(title="Brand New Quiz")),
            ("skip.json",   _v1_file(title="Existing Quiz")),
            ("bad.json",    b"not json"),
        ]
        report = validate_files(files, "LFA_FOOTBALL_PLAYER", postgres_db)
        assert report.total_ok == 1
        assert report.total_skip == 1
        assert report.total_error == 1

    def test_als07_v2_file_accepted(self, postgres_db):
        """ALS-07: valid v2.0 file → total_ok=1, variant/distractor stats populated."""
        report = validate_files([("v2.json", _v2_file())], "LFA_FOOTBALL_PLAYER", postgres_db)
        assert report.total_ok == 1
        fvr = report.files[0]
        assert fvr.variant_stats is not None
        assert fvr.distractor_stats is not None
        assert fvr.variant_stats.min_variants == 2

    def test_als08_apply_payload_json_structure(self, postgres_db):
        """ALS-08: apply_payload_json is a JSON list with filename/spec/data per ok file."""
        report = validate_files([("q.json", _v1_file())], "LFA_FOOTBALL_PLAYER", postgres_db)
        payload = json.loads(report.apply_payload_json)
        assert isinstance(payload, list)
        assert len(payload) == 1
        item = payload[0]
        assert item["filename"] == "q.json"
        assert item["spec"] == "LFA_FOOTBALL_PLAYER"
        assert "quiz_title" in item["data"]


# ---------------------------------------------------------------------------
# ALS-09 to ALS-12 — apply_import
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestApplyImport:
    """ALS-09 to ALS-12 — apply_import writes to real DB."""

    def _make_payload(self, title="ALS Apply Quiz") -> str:
        return json.dumps([{
            "filename": "quiz.json",
            "spec": "LFA_FOOTBALL_PLAYER",
            "data": json.loads(_v1_file(title=title).decode()),
        }])

    def test_als09_creates_quiz_questions_options(self, postgres_db):
        """ALS-09: apply_import creates Quiz + QuizQuestions + QuizAnswerOptions."""
        summary = apply_import(
            apply_payload_json=self._make_payload(),
            spec="LFA_FOOTBALL_PLAYER",
            db=postgres_db,
            operator_user_id=None,
        )
        assert summary.quizzes_created == 1
        assert summary.questions_created == 2
        assert summary.options_fixed > 0

        quiz = postgres_db.query(Quiz).filter(Quiz.title == "ALS Apply Quiz").first()
        assert quiz is not None
        questions = postgres_db.query(QuizQuestion).filter_by(quiz_id=quiz.id).all()
        assert len(questions) == 2
        options = postgres_db.query(QuizAnswerOption).filter_by(question_id=questions[0].id).all()
        assert len(options) == 4

    def test_als10_idempotency_existing_quiz_skipped(self, postgres_db):
        """ALS-10: running the same payload twice skips on second pass."""
        payload = self._make_payload("ALS Idempotent Quiz")
        apply_import(payload, "LFA_FOOTBALL_PLAYER", postgres_db, None)

        summary2 = apply_import(payload, "LFA_FOOTBALL_PLAYER", postgres_db, None)
        assert summary2.quizzes_created == 0
        assert summary2.skipped == 1

    def test_als11_tampered_payload_blocked(self, postgres_db):
        """ALS-11: payload with invalid category after round-trip raises error result."""
        payload = json.dumps([{
            "filename": "tampered.json",
            "spec": "LFA_FOOTBALL_PLAYER",
            "data": {
                "schema_version": "1.0",
                "specializations": ["LFA_FOOTBALL_PLAYER"],
                "quiz_title": "Tampered Quiz",
                "category": "INVALID_CATEGORY",
                "difficulty": "MEDIUM",
                "language": "en",
                "questions": [
                    {
                        "text": "Q?",
                        "type": "MULTIPLE_CHOICE",
                        "explanation": "E.",
                        "options": [
                            {"text": "C", "is_correct": True},
                            {"text": "W1", "is_correct": False},
                            {"text": "W2", "is_correct": False},
                            {"text": "W3", "is_correct": False},
                        ],
                        "metadata": {
                            "estimated_difficulty": 0.5,
                            "cognitive_load": 0.5,
                            "average_time_seconds": 30.0,
                        },
                    }
                ],
            },
        }])
        summary = apply_import(payload, "LFA_FOOTBALL_PLAYER", postgres_db, None)
        assert summary.quizzes_created == 0
        assert len(summary.errors) == 1

    def test_als12_writes_al_import_log(self, postgres_db):
        """ALS-12: apply_import creates an ALImportLog row with correct counts."""
        from app.models.al_import_log import ALImportLog, ImportStatus

        summary = apply_import(
            apply_payload_json=self._make_payload("ALS Log Quiz"),
            spec="LFA_FOOTBALL_PLAYER",
            db=postgres_db,
            operator_user_id=None,
        )
        assert summary.log_id is not None

        log = postgres_db.query(ALImportLog).filter_by(id=summary.log_id).first()
        assert log is not None
        assert log.quizzes_created == 1
        assert log.questions_created == 2
        assert log.status == ImportStatus.SUCCESS
        assert log.spec == "LFA_FOOTBALL_PLAYER"

    def test_pc3_draft_import_is_not_runtime_visible(self, postgres_db):
        title = f"PC3 Draft Import {uuid.uuid4().hex}"
        summary = apply_import(
            self._make_payload(title), "LFA_FOOTBALL_PLAYER", postgres_db, None,
            import_as_draft=True,
        )
        assert summary.quizzes_created == 1
        quiz = postgres_db.query(Quiz).filter(Quiz.title == title).one()
        assert quiz.content_status == "DRAFT"
        assert quiz.is_active is False
