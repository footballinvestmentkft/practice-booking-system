from datetime import date
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.core.auth import create_access_token
from app.models.assessment import AssessmentAttempt, AssessmentIssuedQuestion
from app.models.quiz import (
    ContentStatus,
    QuestionType,
    Quiz,
    QuizAnswerOption,
    QuizCategory,
    QuizDifficulty,
    QuizQuestion,
    QuizAttempt,
)
from app.models.track import EducationProgressEvent
from app.models.user import User, UserRole
from app.models.user_progress import Specialization
from app.services.assessment_integrity_service import (
    AssessmentIntegrityError,
    AssessmentIntegrityService,
)
from app.services.education_content_service import EducationContentService
from app.services.player_identity_service import issue_football_player_entitlement
from app.services.quiz_service import QuizService
from app.schemas.quiz import QuizAttemptSubmit, QuizUserAnswerCreate


def _program(db, program_id="LFA_FOOTBALL_PLAYER"):
    row = db.get(Specialization, program_id)
    if row is None:
        row = Specialization(id=program_id, is_active=True)
        db.add(row)
        db.flush()
    return row


def _player(db, *, name="PC4 Player"):
    player = User(
        name=name,
        email=f"pc4-{uuid4().hex}@example.com",
        password_hash="test-hash",
        role=UserRole.STUDENT,
        is_active=True,
        onboarding_completed=True,
        date_of_birth=date(2000, 1, 1),
        credit_balance=0,
    )
    db.add(player)
    db.flush()
    issue_football_player_entitlement(
        db,
        user=player,
        payment_verified=True,
        on_date=date(2026, 9, 10),
    )
    return player


def _quiz(db, *, key, questions=10, status=ContentStatus.PUBLISHED.value):
    quiz = Quiz(
        title=f"PC4 {key}",
        description="PC4 fixture",
        category=QuizCategory.LESSON,
        difficulty=QuizDifficulty.EASY,
        language="en",
        content_status=status,
        is_active=status == ContentStatus.PUBLISHED.value,
        content_key=key,
        revision=3,
        source_checksum=(key.encode().hex() + "0" * 64)[:64],
        source_schema_version="1.0",
        xp_reward=0,
        passing_score=70,
    )
    db.add(quiz)
    db.flush()
    for index in range(questions):
        question = QuizQuestion(
            quiz_id=quiz.id,
            question_text=f"Question {index + 1}?",
            question_type=QuestionType.MULTIPLE_CHOICE,
            points=1,
            order_index=index + 1,
            explanation=f"Explanation {index + 1}",
        )
        db.add(question)
        db.flush()
        db.add_all(
            [
                QuizAnswerOption(
                    question_id=question.id,
                    option_text="Correct",
                    is_correct=True,
                    order_index=1,
                ),
                QuizAnswerOption(
                    question_id=question.id,
                    option_text="Wrong",
                    is_correct=False,
                    order_index=2,
                ),
            ]
        )
    db.flush()
    return quiz


def _assessment(db, *, mode="STANDARD", release_status="PUBLISHED", questions=10):
    _program(db)
    education = EducationContentService(db)
    suffix = uuid4().hex[:8]
    track = education.create_track(
        program_id="LFA_FOOTBALL_PLAYER",
        stable_key=f"pc4-track-{suffix}",
        code=f"PC4-{suffix}",
        name="PC4 Player",
    )
    release = education.create_release(track_id=track.id, version=1)
    module = education.add_module(
        release_id=release.id,
        stable_key="module",
        name="Module",
        order=1,
    )
    lesson = education.add_lesson(
        module_id=module.id,
        stable_key="lesson",
        title="Lesson",
        order=1,
    )
    quiz = _quiz(db, key=f"pc4-{suffix}", questions=questions)
    placement = education.attach_assessment(
        lesson_id=lesson.id,
        stable_key="assessment",
        delivery_mode=mode,
        purpose="FORMATIVE",
        order=1,
    )
    education.add_assessment_variant(placement.id, quiz.id, locale="en")
    if release_status == "PUBLISHED":
        education.publish_release(release.id)
    db.flush()
    return track, release, lesson, placement, quiz


def _correct_option(db, issued):
    option_id = issued.correct_option_id
    return db.get(QuizAnswerOption, option_id)


def test_one_of_ten_and_omissions_are_scored_against_the_issued_denominator(test_db):
    _, release, lesson, placement, quiz = _assessment(test_db, questions=10)
    player = _player(test_db)
    service = AssessmentIntegrityService(test_db)

    attempt = service.start_attempt(user=player, assessment_id=placement.id, locale="en")
    assert len(attempt.issued_questions) == 10
    first = attempt.issued_questions[0]
    service.answer_question(
        user=player,
        attempt_id=attempt.id,
        issued_question_id=first.id,
        selected_option_id=_correct_option(test_db, first).id,
        idempotency_key="answer-1",
    )
    completed = service.complete_attempt(
        user=player,
        attempt_id=attempt.id,
        idempotency_key="complete-1",
    )

    assert completed.score_percent == 10.0
    assert completed.total_questions == 10
    assert completed.answered_questions == 10
    assert completed.correct_answers == 1
    assert sum(row.omitted for row in completed.issued_questions) == 9
    assert completed.track_release_id == release.id
    assert completed.lesson_id == lesson.id
    assert completed.quiz_id == quiz.id


def test_answer_replay_and_duplicate_answer_are_idempotent(test_db):
    _, _, _, placement, _ = _assessment(test_db, questions=1)
    player = _player(test_db)
    service = AssessmentIntegrityService(test_db)
    attempt = service.start_attempt(user=player, assessment_id=placement.id, locale="en")
    issued = attempt.issued_questions[0]
    correct_id = _correct_option(test_db, issued).id

    first = service.answer_question(
        user=player,
        attempt_id=attempt.id,
        issued_question_id=issued.id,
        selected_option_id=correct_id,
        idempotency_key="same-answer",
    )
    replay = service.answer_question(
        user=player,
        attempt_id=attempt.id,
        issued_question_id=issued.id,
        selected_option_id=correct_id,
        idempotency_key="same-answer",
    )
    duplicate = service.answer_question(
        user=player,
        attempt_id=attempt.id,
        issued_question_id=issued.id,
        selected_option_id=correct_id,
        idempotency_key="different-key",
    )

    assert first.id == replay.id == duplicate.id
    assert test_db.query(AssessmentIssuedQuestion).filter_by(attempt_id=attempt.id).count() == 1
    assert attempt.correct_answers == 1


def test_foreign_attempt_unissued_question_and_wrong_option_fail_closed(test_db):
    _, _, _, placement, _ = _assessment(test_db, questions=2)
    owner = _player(test_db, name="Owner")
    foreign = _player(test_db, name="Foreign")
    service = AssessmentIntegrityService(test_db)
    attempt = service.start_attempt(user=owner, assessment_id=placement.id, locale="en")
    issued, other = attempt.issued_questions

    with pytest.raises(AssessmentIntegrityError, match="ATTEMPT_NOT_FOUND"):
        service.answer_question(
            user=foreign,
            attempt_id=attempt.id,
            issued_question_id=issued.id,
            selected_option_id=issued.correct_option_id,
            idempotency_key="foreign",
        )

    alien_question = AssessmentIssuedQuestion(
        attempt_id=attempt.id,
        source_question_id=999999999,
        sequence=99,
        question_snapshot={"text": "forged"},
        option_snapshot=[],
        correct_option_id=None,
        points=1,
    )
    with pytest.raises(AssessmentIntegrityError, match="ISSUED_QUESTION_NOT_FOUND"):
        service.answer_question(
            user=owner,
            attempt_id=attempt.id,
            issued_question_id=uuid4(),
            selected_option_id=issued.correct_option_id,
            idempotency_key="unissued",
        )

    with pytest.raises(AssessmentIntegrityError, match="OPTION_NOT_ISSUED"):
        service.answer_question(
            user=owner,
            attempt_id=attempt.id,
            issued_question_id=issued.id,
            selected_option_id=other.correct_option_id,
            idempotency_key="wrong-relation",
        )


def test_adaptive_question_must_be_server_issued_and_snapshot_is_authoritative(test_db):
    _, _, _, placement, quiz = _assessment(test_db, mode="ADAPTIVE", questions=2)
    player = _player(test_db)
    service = AssessmentIntegrityService(test_db)
    attempt = service.start_attempt(user=player, assessment_id=placement.id, locale="en")
    assert attempt.issued_questions == []

    issued = service.issue_next_question(user=player, attempt_id=attempt.id)
    assert issued is not None
    original_snapshot = list(issued.option_snapshot)
    unissued_source = next(q for q in quiz.questions if q.id != issued.source_question_id)

    with pytest.raises(AssessmentIntegrityError, match="QUESTION_NOT_ISSUED"):
        service.answer_source_question(
            user=player,
            attempt_id=attempt.id,
            question_id=unissued_source.id,
            selected_option_id=unissued_source.answer_options[0].id,
            idempotency_key="unissued-source",
        )

    source_correct = test_db.get(QuizAnswerOption, issued.correct_option_id)
    source_correct.is_correct = False
    source_correct.option_text = "Mutated after issue"
    result = service.answer_question(
        user=player,
        attempt_id=attempt.id,
        issued_question_id=issued.id,
        selected_option_id=issued.correct_option_id,
        idempotency_key="snapshot-answer",
    )
    assert result.is_correct is True
    assert result.option_snapshot == original_snapshot


def test_completed_attempt_is_immutable_and_completion_replay_has_one_outcome(test_db):
    _, _, _, placement, _ = _assessment(test_db, questions=1)
    player = _player(test_db)
    service = AssessmentIntegrityService(test_db)
    attempt = service.start_attempt(user=player, assessment_id=placement.id, locale="en")
    issued = attempt.issued_questions[0]
    service.answer_question(
        user=player,
        attempt_id=attempt.id,
        issued_question_id=issued.id,
        selected_option_id=issued.correct_option_id,
        idempotency_key="answer",
    )
    first = service.complete_attempt(user=player, attempt_id=attempt.id, idempotency_key="complete")
    replay = service.complete_attempt(user=player, attempt_id=attempt.id, idempotency_key="complete")

    assert first.id == replay.id
    assert test_db.query(EducationProgressEvent).filter_by(assessment_attempt_id=attempt.id).count() == 1
    with pytest.raises(AssessmentIntegrityError, match="ATTEMPT_COMPLETED"):
        service.answer_question(
            user=player,
            attempt_id=attempt.id,
            issued_question_id=issued.id,
            selected_option_id=issued.correct_option_id,
            idempotency_key="late-answer",
        )


def test_draft_release_and_non_player_program_are_denied(test_db):
    _, _, _, draft_assessment, _ = _assessment(test_db, release_status="DRAFT", questions=1)
    player = _player(test_db)
    service = AssessmentIntegrityService(test_db)
    with pytest.raises(AssessmentIntegrityError, match="ASSESSMENT_NOT_PUBLISHED"):
        service.start_attempt(user=player, assessment_id=draft_assessment.id, locale="en")

    _, release, _, placement, _ = _assessment(test_db, questions=1)
    _program(test_db, "LFA_COACH")
    release.track.specialization_id = "LFA_COACH"
    test_db.flush()
    with pytest.raises(AssessmentIntegrityError, match="PLAYER_SPECIALIZATION_REQUIRED"):
        service.start_attempt(user=player, assessment_id=placement.id, locale="en")


def test_exact_release_revision_and_issued_snapshot_provenance(test_db):
    _, release, lesson, placement, quiz = _assessment(test_db, questions=1)
    player = _player(test_db)
    service = AssessmentIntegrityService(test_db)
    attempt = service.start_attempt(user=player, assessment_id=placement.id, locale="en")
    issued = attempt.issued_questions[0]
    service.answer_question(
        user=player,
        attempt_id=attempt.id,
        issued_question_id=issued.id,
        selected_option_id=issued.correct_option_id,
        idempotency_key="provenance-answer",
    )
    completed = service.complete_attempt(
        user=player,
        attempt_id=attempt.id,
        idempotency_key="provenance-complete",
    )
    event = test_db.query(EducationProgressEvent).filter_by(assessment_attempt_id=attempt.id).one()

    assert completed.track_release_id == release.id
    assert completed.lesson_id == lesson.id
    assert completed.quiz_revision == 3
    assert completed.content_provenance["release_content_hash"] == release.content_hash
    assert completed.content_provenance["quiz_source_checksum"] == quiz.source_checksum
    assert event.track_release_id == release.id
    assert event.quiz_revision == 3
    assert event.provenance["issued_question_snapshot_hash"]


def test_answer_failure_rolls_back_score_and_history(test_db, monkeypatch):
    _, _, _, placement, _ = _assessment(test_db, questions=1)
    player = _player(test_db)
    service = AssessmentIntegrityService(test_db)
    attempt = service.start_attempt(user=player, assessment_id=placement.id, locale="en")
    issued = attempt.issued_questions[0]
    attempt_id = attempt.id
    test_db.commit()

    def fail_flush():
        raise RuntimeError("forced rollback")

    monkeypatch.setattr(test_db, "flush", fail_flush)
    with pytest.raises(RuntimeError, match="forced rollback"):
        service.answer_question(
            user=player,
            attempt_id=attempt.id,
            issued_question_id=issued.id,
            selected_option_id=issued.correct_option_id,
            idempotency_key="rollback-answer",
        )
    test_db.rollback()
    persisted = test_db.get(AssessmentAttempt, attempt_id)
    assert persisted.correct_answers == 0
    assert persisted.answered_questions == 0


def test_concurrent_completion_creates_exactly_one_outcome_and_xp_transaction():
    setup = sessionmaker(bind=engine)()
    try:
        _, _, _, placement, quiz = _assessment(setup, questions=1)
        quiz.xp_reward = 25
        player = _player(setup, name="Concurrent Player")
        service = AssessmentIntegrityService(setup)
        attempt = service.start_attempt(user=player, assessment_id=placement.id, locale="en")
        issued = attempt.issued_questions[0]
        service.answer_question(
            user=player, attempt_id=attempt.id, issued_question_id=issued.id,
            selected_option_id=issued.correct_option_id,
            idempotency_key="concurrent-answer",
        )
        player_id, attempt_id = player.id, attempt.id
        setup.commit()
    finally:
        setup.close()

    barrier = Barrier(2)

    def complete():
        db = sessionmaker(bind=engine)()
        try:
            actor = db.get(User, player_id)
            barrier.wait(timeout=5)
            row = AssessmentIntegrityService(db).complete_attempt(
                user=actor, attempt_id=attempt_id,
                idempotency_key="concurrent-complete",
            )
            db.commit()
            return row.id
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: complete(), range(2)))

    verify = sessionmaker(bind=engine)()
    try:
        from app.models.xp_transaction import XPTransaction
        assert results == [attempt_id, attempt_id]
        assert verify.query(EducationProgressEvent).filter_by(
            assessment_attempt_id=attempt_id
        ).count() == 1
        assert verify.query(XPTransaction).filter_by(
            idempotency_key=f"assessment:{attempt_id}:xp"
        ).count() == 1
        assert verify.get(User, player_id).xp_balance == 25
    finally:
        verify.close()


def test_concurrent_answer_is_counted_once():
    setup = sessionmaker(bind=engine)()
    try:
        _, _, _, placement, _ = _assessment(setup, questions=1)
        player = _player(setup, name="Concurrent Answer Player")
        attempt = AssessmentIntegrityService(setup).start_attempt(
            user=player, assessment_id=placement.id, locale="en"
        )
        issued = attempt.issued_questions[0]
        player_id, attempt_id, issued_id = player.id, attempt.id, issued.id
        option_id = issued.correct_option_id
        setup.commit()
    finally:
        setup.close()

    barrier = Barrier(2)

    def answer():
        db = sessionmaker(bind=engine)()
        try:
            actor = db.get(User, player_id)
            barrier.wait(timeout=5)
            row = AssessmentIntegrityService(db).answer_question(
                user=actor, attempt_id=attempt_id, issued_question_id=issued_id,
                selected_option_id=option_id, idempotency_key="same-concurrent-answer",
            )
            db.commit()
            return row.id
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: answer(), range(2))) == [issued_id, issued_id]

    verify = sessionmaker(bind=engine)()
    try:
        persisted = verify.get(AssessmentAttempt, attempt_id)
        assert persisted.answered_questions == 1
        assert persisted.correct_answers == 1
        assert persisted.earned_points == 1
    finally:
        verify.close()


def test_completion_failure_rolls_back_attempt_outcome_and_xp(test_db, monkeypatch):
    _, _, _, placement, quiz = _assessment(test_db, questions=1)
    quiz.xp_reward = 25
    player = _player(test_db, name="Completion Rollback Player")
    service = AssessmentIntegrityService(test_db)
    attempt = service.start_attempt(user=player, assessment_id=placement.id, locale="en")
    issued = attempt.issued_questions[0]
    service.answer_question(
        user=player, attempt_id=attempt.id, issued_question_id=issued.id,
        selected_option_id=issued.correct_option_id, idempotency_key="rollback-answer",
    )
    attempt_id, player_id = attempt.id, player.id
    test_db.commit()

    def fail_flush():
        raise RuntimeError("forced completion rollback")

    monkeypatch.setattr(test_db, "flush", fail_flush)
    with pytest.raises(RuntimeError, match="forced completion rollback"):
        service.complete_attempt(
            user=player, attempt_id=attempt_id, idempotency_key="rollback-complete"
        )
    test_db.rollback()
    persisted = test_db.get(AssessmentAttempt, attempt_id)
    assert persisted.status == "IN_PROGRESS"
    assert persisted.completed_at is None
    assert test_db.query(EducationProgressEvent).filter_by(
        assessment_attempt_id=attempt_id
    ).count() == 0
    assert test_db.get(User, player_id).xp_balance == 0


def test_canonical_api_contract_never_exposes_answer_key_and_uses_same_outcome(client, test_db):
    _, _, _, placement, _ = _assessment(test_db, questions=2)
    player = _player(test_db, name="API Player")
    test_db.commit()
    token = create_access_token({"sub": player.email})
    headers = {"Authorization": f"Bearer {token}"}

    started = client.post(
        f"/api/v1/education/assessments/{placement.id}/attempts",
        json={"locale": "en"}, headers=headers,
    )
    assert started.status_code == 200, started.text
    payload = started.json()
    assert payload["track_release_id"]
    assert len(payload["questions"]) == 2
    assert all("is_correct" not in option for option in payload["questions"][0]["options"])
    assert "correct_option_id" not in payload["questions"][0]

    question = payload["questions"][0]
    answered = client.post(
        f"/api/v1/education/assessment-attempts/{payload['id']}/answers",
        json={
            "issued_question_id": question["id"],
            "selected_option_id": question["options"][0]["id"],
            "idempotency_key": "api-answer-1",
        }, headers=headers,
    )
    assert answered.status_code == 200, answered.text
    completed = client.post(
        f"/api/v1/education/assessment-attempts/{payload['id']}/complete",
        json={"idempotency_key": "api-complete-1"}, headers=headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["score_percent"] == 50.0
    assert completed.json()["quiz_revision"] == 3
    assert completed.json()["xp_awarded"] == 0


def test_web_adaptive_contract_uses_server_issued_snapshot(client, test_db):
    _, _, _, placement, _ = _assessment(test_db, mode="ADAPTIVE", questions=1)
    player = _player(test_db, name="Web Adaptive Player")
    test_db.commit()
    token = create_access_token({"sub": player.email})
    client.cookies.set("access_token", f"Bearer {token}", domain="testserver.local")
    client.cookies.set("csrf_token", "pc4-web-quiz-csrf", domain="testserver.local")
    client.cookies.set("csrf_token", "pc4-csrf", domain="testserver.local")
    csrf_headers = {"X-CSRF-Token": "pc4-csrf"}

    started = client.post(
        "/adaptive-learning/session/start",
        params={
            "category": "LESSON", "assessment_id": str(placement.id),
            "time_limit": 180, "language": "en",
        },
        headers=csrf_headers,
    )
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    issued = client.get(f"/adaptive-learning/session/{session_id}/next-question")
    assert issued.status_code == 200, issued.text
    question = issued.json()
    assert question["issued_question_id"]
    assert all("is_correct" not in option for option in question["options"])
    csrf_headers = {"X-CSRF-Token": client.cookies.get("csrf_token", domain="testserver.local")}

    answered = client.post(
        f"/adaptive-learning/session/{session_id}/answer",
        json={
            "question_id": question["id"],
            "issued_question_id": question["issued_question_id"],
            "selected_option_id": question["options"][0]["id"],
            "presented_option_ids": [999999999],
            "idempotency_key": "web-adaptive-answer",
            "time_spent_seconds": 1,
        }, headers=csrf_headers,
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["correct"] is True
    csrf_headers = {"X-CSRF-Token": client.cookies.get("csrf_token", domain="testserver.local")}
    completed = client.post(
        f"/adaptive-learning/session/{session_id}/complete", headers=csrf_headers
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["accuracy_pct"] == 100.0


def test_web_quiz_renders_and_submits_the_server_issued_snapshot(client, test_db):
    _, _, _, placement, quiz = _assessment(test_db, questions=1)
    player = _player(test_db, name="Web Quiz Snapshot Player")
    original_text = quiz.questions[0].question_text
    test_db.commit()
    token = create_access_token({"sub": player.email})
    client.cookies.set("access_token", f"Bearer {token}", domain="testserver.local")

    started = client.get(f"/quizzes/{quiz.id}/take")
    assert started.status_code == 200, started.text
    attempt = test_db.query(AssessmentAttempt).filter_by(user_id=player.id).one()
    issued = attempt.issued_questions[0]
    legacy_attempt = test_db.query(QuizAttempt).filter_by(
        assessment_attempt_id=attempt.id
    ).one()

    quiz.questions[0].question_text = "MUTATED AFTER ISSUE"
    test_db.commit()
    resumed = client.get(f"/quizzes/{quiz.id}/take")
    assert resumed.status_code == 200, resumed.text
    assert original_text in resumed.text
    assert "MUTATED AFTER ISSUE" not in resumed.text

    submitted = client.post(
        f"/quizzes/{quiz.id}/submit",
        data={
            "session_id": "",
            "attempt_id": str(legacy_attempt.id),
            "time_spent": "1",
            f"issued_{issued.id}": str(issued.correct_option_id),
        },
        headers={"X-CSRF-Token": client.cookies.get("csrf_token", domain="testserver.local")},
    )
    assert submitted.status_code == 200, submitted.text
    test_db.refresh(attempt)
    assert attempt.status == "COMPLETED"
    assert attempt.score_percent == 100.0


def test_legacy_quiz_entrypoint_delegates_mapped_player_content_to_canonical_service(test_db):
    _, _, _, _, quiz = _assessment(test_db, questions=10)
    player = _player(test_db, name="Legacy Bridge Player")
    bridge = QuizService(test_db)

    attempt = bridge.start_quiz_attempt(player.id, quiz.id)
    assert attempt.assessment_attempt_id is not None
    issued = attempt.assessment_attempt.issued_questions[0]
    submission = QuizAttemptSubmit(
        attempt_id=attempt.id,
        answers=[QuizUserAnswerCreate(
            question_id=issued.source_question_id,
            selected_option_id=issued.correct_option_id,
        )],
    )
    completed = bridge.submit_quiz_attempt(player.id, submission)
    replay = bridge.submit_quiz_attempt(player.id, submission)

    assert completed.id == replay.id
    assert completed.score == 10.0
    assert completed.total_questions == 10
    assert completed.correct_answers == 1
    assert test_db.query(EducationProgressEvent).filter_by(
        assessment_attempt_id=attempt.assessment_attempt_id
    ).count() == 1
