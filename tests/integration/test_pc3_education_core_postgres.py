from datetime import date, datetime, timezone
from pathlib import Path
import json
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.quiz import ContentStatus, Quiz, QuizAttempt, QuizCategory, QuizDifficulty
from app.models.user import User, UserRole
from app.models.user_progress import Specialization
from app.services.education_content_service import (
    EducationContentError,
    EducationContentService,
    import_player_sample_draft,
)
from app.models.track import Track
from app.services.player_identity_service import issue_football_player_entitlement
from app.services.track_service import TrackService


def _program(db, program_id="LFA_FOOTBALL_PLAYER"):
    row = db.get(Specialization, program_id)
    if row is None:
        row = Specialization(id=program_id, is_active=True)
        db.add(row)
        db.flush()
    return row


def _service(db):
    _program(db)
    return EducationContentService(db)


def _quiz(db, *, key, locale="en", status=ContentStatus.PUBLISHED.value):
    quiz = Quiz(
        title=f"PC3 {key} {locale}",
        description="PC3 fixture",
        category=QuizCategory.LESSON,
        difficulty=QuizDifficulty.EASY,
        language=locale,
        content_status=status,
        is_active=status == ContentStatus.PUBLISHED.value,
        content_key=key,
        revision=1,
        source_checksum=uuid4().hex,
        source_schema_version="1.0",
    )
    db.add(quiz)
    db.flush()
    return quiz


def _draft_tree(db, *, suffix=None):
    suffix = suffix or uuid4().hex[:8]
    service = _service(db)
    track = service.create_track(
        program_id="LFA_FOOTBALL_PLAYER",
        stable_key=f"player-track-{suffix}",
        code=f"PC3-{suffix}",
        name="Player Core",
        default_locale="en",
    )
    release = service.create_release(track_id=track.id, version=1)
    module = service.add_module(
        release_id=release.id,
        stable_key="fundamentals",
        name="Fundamentals",
        order=1,
        translations={"hu": {"name": "Alapok"}},
    )
    lesson = service.add_lesson(
        module_id=module.id,
        stable_key="first-touch",
        title="First Touch",
        order=1,
        topic_key="technical.first_touch",
        translations={"hu": {"title": "Első érintés"}},
    )
    component = service.add_component(
        lesson_id=lesson.id,
        stable_key="intro-text",
        component_type="text",
        name="Introduction",
        order=1,
        payload={"body": "Base English sample"},
        translations={"hu": {"name": "Bevezetés", "payload": {"body": "Magyar minta"}}},
    )
    return service, track, release, module, lesson, component


def test_content_nodes_and_new_locale_are_data_operations_with_unique_order(test_db):
    service, _, release, module, lesson, component = _draft_tree(test_db)

    assert release.status == "DRAFT"
    assert module.translations["hu"]["name"] == "Alapok"
    assert lesson.translations["hu"]["title"] == "Első érintés"
    assert component.translations["hu"]["payload"]["body"] == "Magyar minta"

    service.add_translation(module, "pt-br", {"name": "Fundamentos"})
    assert module.translations["pt-BR"]["name"] == "Fundamentos"

    with pytest.raises(IntegrityError):
        service.add_lesson(
            module_id=module.id,
            stable_key="duplicate-order",
            title="Duplicate",
            order=1,
        )
    test_db.rollback()


def test_draft_is_hidden_and_no_assessment_lesson_can_be_published(test_db):
    service, track, release, _, lesson, _ = _draft_tree(test_db)

    assert service.list_published_tracks("LFA_FOOTBALL_PLAYER") == []
    with pytest.raises(EducationContentError, match="PUBLISHED_RELEASE_NOT_FOUND"):
        service.get_published_curriculum(track.id, locale="en")

    service.publish_release(release.id)
    test_db.flush()

    catalog = service.list_published_tracks("LFA_FOOTBALL_PLAYER")
    assert [row["id"] for row in catalog] == [str(track.id)]
    curriculum = service.get_published_curriculum(track.id, locale="hu")
    assert curriculum["release_id"] == str(release.id)
    assert curriculum["modules"][0]["name"] == "Alapok"
    assert curriculum["modules"][0]["lessons"][0]["id"] == str(lesson.id)
    assert curriculum["modules"][0]["lessons"][0]["assessments"] == []

    with pytest.raises(EducationContentError, match="RELEASE_IMMUTABLE"):
        service.add_lesson(
            module_id=curriculum["modules"][0]["id"],
            stable_key="late-edit",
            title="Late edit",
            order=2,
        )


def test_player_specialization_isolation_fails_closed(test_db):
    service, track, release, _, _, _ = _draft_tree(test_db)
    service.publish_release(release.id)
    _program(test_db, "LFA_COACH")

    with pytest.raises(EducationContentError, match="UNSUPPORTED_EDUCATION_PROGRAM"):
        service.create_track(
            program_id="LFA_COACH",
            stable_key="coach-track",
            code=f"COACH-{uuid4().hex[:8]}",
            name="Coach",
        )
    with pytest.raises(EducationContentError, match="UNSUPPORTED_EDUCATION_PROGRAM"):
        service.list_published_tracks("LFA_COACH")

    assert service.get_published_curriculum(track.id, locale="en")["program_id"] == "LFA_FOOTBALL_PLAYER"


def test_legacy_track_service_cannot_expose_or_enroll_canonical_content(test_db):
    service, track, release, _, _, _ = _draft_tree(test_db)
    service.publish_release(release.id)
    player = User(
        name="PC3 Legacy Boundary Player",
        email=f"pc3-legacy-{uuid4().hex}@example.com",
        password_hash="test-hash",
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=date(2000, 1, 1),
        credit_balance=0,
    )
    test_db.add(player)
    test_db.flush()

    legacy = TrackService(test_db)
    assert track.id not in {row.id for row in legacy.get_available_tracks(str(player.id))}
    eligibility = legacy.check_enrollment_eligibility(
        str(player.id), str(track.id), str(uuid4())
    )
    assert eligibility == {
        "eligible": False,
        "reason": "Canonical education tracks cannot use legacy enrollment",
    }


def test_assessment_is_bound_to_exact_lesson_release_and_old_outcome_survives_update(test_db):
    service, track, release_v1, _, lesson, _ = _draft_tree(test_db)
    quiz_v1 = _quiz(test_db, key="first-touch-check")
    placement = service.attach_assessment(
        lesson_id=lesson.id,
        stable_key="knowledge-check",
        delivery_mode="ADAPTIVE",
        purpose="FORMATIVE",
        order=1,
    )
    service.add_assessment_variant(placement.id, quiz_v1.id, locale="en")
    service.publish_release(release_v1.id)

    player = User(
        name="PC3 Player",
        email=f"pc3-{uuid4().hex}@example.com",
        password_hash="test-hash",
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=date(2000, 1, 1),
        credit_balance=0,
    )
    test_db.add(player)
    test_db.flush()
    issue_football_player_entitlement(
        test_db,
        user=player,
        payment_verified=True,
        on_date=date(2026, 9, 10),
    )
    attempt = QuizAttempt(
        user_id=player.id,
        quiz_id=quiz_v1.id,
        completed_at=datetime.now(timezone.utc),
        total_questions=1,
        correct_answers=1,
        score=100.0,
        passed=True,
    )
    test_db.add(attempt)
    test_db.flush()

    outcome = service.record_assessment_outcome(
        user=player,
        assessment_id=placement.id,
        quiz_attempt_id=attempt.id,
        idempotency_key=f"pc3-outcome-{uuid4().hex}",
        on_date=date(2026, 9, 10),
    )
    original_hash = release_v1.content_hash

    release_v2 = service.create_release(track_id=track.id, version=2)
    module_v2 = service.add_module(
        release_id=release_v2.id,
        stable_key="updated",
        name="Updated content",
        order=1,
    )
    lesson_v2 = service.add_lesson(
        module_id=module_v2.id,
        stable_key="new-lesson",
        title="New Lesson",
        order=1,
    )
    service.add_component(
        lesson_id=lesson_v2.id,
        stable_key="new-text",
        component_type="text",
        name="New",
        order=1,
        payload={"body": "v2"},
    )
    service.publish_release(release_v2.id)
    test_db.flush()

    persisted = service.get_outcome(outcome.id, user_id=player.id)
    assert persisted.track_release_id == release_v1.id
    assert persisted.lesson_assessment_id == placement.id
    assert persisted.quiz_id == quiz_v1.id
    assert persisted.quiz_revision == 1
    assert persisted.provenance["release_content_hash"] == original_hash
    assert persisted.provenance["quiz_source_checksum"] == quiz_v1.source_checksum
    assert track.current_release_id == release_v2.id


def test_wrong_quiz_cannot_be_recorded_for_assessment(test_db):
    service, _, release, _, lesson, _ = _draft_tree(test_db)
    expected_quiz = _quiz(test_db, key="expected")
    wrong_quiz = _quiz(test_db, key="wrong")
    placement = service.attach_assessment(
        lesson_id=lesson.id,
        stable_key="check",
        delivery_mode="STANDARD",
        purpose="SUMMATIVE",
        order=1,
    )
    service.add_assessment_variant(placement.id, expected_quiz.id, locale="en")
    service.publish_release(release.id)

    user = User(
        name="Wrong Quiz Player",
        email=f"pc3-wrong-{uuid4().hex}@example.com",
        password_hash="test-hash",
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=date(2000, 1, 1),
        credit_balance=0,
    )
    test_db.add(user)
    test_db.flush()
    issue_football_player_entitlement(test_db, user=user, payment_verified=True, on_date=date(2026, 9, 10))
    attempt = QuizAttempt(
        user_id=user.id,
        quiz_id=wrong_quiz.id,
        completed_at=datetime.now(timezone.utc),
        total_questions=1,
        correct_answers=1,
        score=100,
        passed=True,
    )
    test_db.add(attempt)
    test_db.flush()

    with pytest.raises(EducationContentError, match="ASSESSMENT_QUIZ_MISMATCH"):
        service.record_assessment_outcome(
            user=user,
            assessment_id=placement.id,
            quiz_attempt_id=attempt.id,
            idempotency_key=f"pc3-wrong-{uuid4().hex}",
            on_date=date(2026, 9, 10),
        )


def test_sample_corpus_import_is_complete_transactional_and_draft(test_db):
    _program(test_db)
    result = import_player_sample_draft(
        test_db,
        manifest_path=Path("content/education/lfa_football_player/pc3_sample_manifest.json"),
    )

    assert result.files == 31
    assert result.questions == 375
    assert result.modules == 7
    assert result.lessons == 21
    assert result.assessments == 23
    assert result.release.status == "DRAFT"
    assert result.track.current_release_id is None
    assert all(quiz.content_status == ContentStatus.DRAFT.value for quiz in result.quizzes)
    assert all(quiz.is_active is False for quiz in result.quizzes)
    assert EducationContentService(test_db).list_published_tracks("LFA_FOOTBALL_PLAYER") == []


def test_sample_import_rolls_back_the_whole_hierarchy_on_source_failure(test_db, tmp_path):
    _program(test_db)
    source = Path("content/education/lfa_football_player/pc3_sample_manifest.json")
    manifest = json.loads(source.read_text(encoding="utf-8"))
    invalid = tmp_path / "invalid-corpus.json"
    invalid.write_text("{invalid", encoding="utf-8")
    manifest["modules"][0]["lessons"][0]["assessments"][0]["variants"][0]["source_path"] = str(invalid)
    broken_manifest = tmp_path / "manifest.json"
    broken_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    before = test_db.query(Track).filter(Track.code == "PC3-PLAYER-SAMPLE").count()

    with pytest.raises(json.JSONDecodeError):
        import_player_sample_draft(test_db, manifest_path=broken_manifest)

    after = test_db.query(Track).filter(Track.code == "PC3-PLAYER-SAMPLE").count()
    assert after == before
