"""PC5 canonical Player progression regression and PostgreSQL concurrency tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.core.auth import create_access_token
from app.models.track import EducationProgressEvent
from app.models.football_skill_assessment import FootballSkillAssessment
from app.services.assessment_integrity_service import AssessmentIntegrityService
from app.services.football_skill_service import FootballSkillService
from tests.integration.test_pc4_assessment_integrity_postgres import _assessment
from app.models.gamification import UserAchievement, UserStats
from app.models.player_progression import PlayerProgressionOutcome, PlayerSkillChange, PlayerSkillState
from app.models.user import User, UserRole
from app.models.user_progress import Specialization
from app.models.user_progress import SpecializationProgress
from app.models.xp_transaction import XPTransaction
from app.services.player_identity_service import issue_football_player_entitlement
from app.services.player_progression_service import PlayerProgressionError, PlayerProgressionService
from app.services.progress_license_coupling import ProgressLicenseCoupler
from app.services.progress_license_sync_service import ProgressLicenseSyncService


def _program(db):
    row = db.get(Specialization, "LFA_FOOTBALL_PLAYER")
    if row is None:
        row = Specialization(id="LFA_FOOTBALL_PLAYER", is_active=True)
        db.add(row)
        db.flush()
    return row


def _player(db, *, name="PC5 Player", credit=37):
    _program(db)
    row = User(name=name, email=f"pc5-{uuid4().hex}@example.com", password_hash="test-hash",
               role=UserRole.STUDENT, is_active=True, onboarding_completed=True,
               date_of_birth=date(2000, 1, 1), credit_balance=credit, xp_balance=0)
    db.add(row)
    db.flush()
    license_row = issue_football_player_entitlement(
        db, user=row, payment_verified=True, on_date=date(2026, 9, 10),
    ).license
    return row, license_row


def _grant():
    return [{"key": "FIRST_PLAYER_OUTCOME", "badge_type": "first_level_up",
             "title": "First Player Outcome", "description": "Applied the first canonical Player outcome.",
             "icon": "star"}]


def test_same_outcome_is_exactly_once_for_skill_xp_achievement_and_credit_boundary(test_db):
    player, _ = _player(test_db)
    service = PlayerProgressionService(test_db)
    kwargs = dict(player=player, source_type="VERIFIED_TRAINING_OUTCOME", source_id="training-1",
                  idempotency_key="pc5-training-1", actor=player, xp_delta=25,
                  skill_updates={"passing": 72.0}, achievements=_grant(),
                  provenance={"provider": "test", "revision": "v1"}, context={"session_id": 101})
    first = service.apply_outcome(**kwargs)
    second = service.apply_outcome(**kwargs)
    test_db.flush()
    assert first.id == second.id
    assert test_db.query(PlayerProgressionOutcome).filter_by(player_user_id=player.id).count() == 1
    assert test_db.query(PlayerSkillChange).filter_by(outcome_id=first.id).count() == 1
    assert test_db.query(XPTransaction).filter_by(idempotency_key=f"progression:{first.id}:xp").count() == 1
    assert test_db.query(UserAchievement).filter_by(user_id=player.id, canonical_key="FIRST_PLAYER_OUTCOME").count() == 1
    test_db.refresh(player)
    assert player.xp_balance == 25
    assert player.credit_balance == 37
    assert test_db.query(UserStats).filter_by(user_id=player.id).one().total_xp == 25


def test_canonical_xp_preserves_existing_player_level_boundaries(test_db):
    player, _ = _player(test_db, name="PC5 XP Boundary Player")
    service = PlayerProgressionService(test_db)
    service.apply_outcome(
        player=player, source_type="VERIFIED_TRAINING_OUTCOME", source_id="xp-boundary-1000",
        idempotency_key="pc5-xp-boundary-1000", actor=player, xp_delta=1000,
    )
    assert test_db.query(UserStats).filter_by(user_id=player.id).one().level == 1
    service.apply_outcome(
        player=player, source_type="VERIFIED_TRAINING_OUTCOME", source_id="xp-boundary-2000",
        idempotency_key="pc5-xp-boundary-2000", actor=player, xp_delta=1000,
    )
    assert test_db.query(UserStats).filter_by(user_id=player.id).one().level == 2


def test_legacy_progress_sync_and_coupling_are_fail_closed_for_canonical_player(test_db):
    player, license_row = _player(test_db, name="PC5 Legacy Sync Guard Player")
    progress = SpecializationProgress(
        student_id=player.id,
        specialization_id="LFA_FOOTBALL_PLAYER",
        current_level=7,
        total_xp=7000,
        completed_sessions=10,
        completed_projects=0,
    )
    test_db.add(progress)
    test_db.flush()
    original_level = license_row.current_level

    sync_service = ProgressLicenseSyncService(test_db)
    assert sync_service.find_desync_issues("LFA_FOOTBALL_PLAYER") == []
    assert sync_service.sync_progress_to_license(
        player.id, "LFA_FOOTBALL_PLAYER"
    )["action"] == "denied"
    assert sync_service.sync_license_to_progress(
        player.id, "LFA_FOOTBALL_PLAYER"
    )["action"] == "denied"
    assert ProgressLicenseCoupler(test_db).update_level_atomic(
        player.id, "LFA_FOOTBALL_PLAYER", new_level=8
    )["error_type"] == "canonical_player_sync_denied"

    test_db.refresh(license_row)
    test_db.refresh(progress)
    assert license_row.current_level == original_level
    assert progress.current_level == 7


def test_outcome_replay_payload_conflict_fails_closed(test_db):
    player, _ = _player(test_db)
    service = PlayerProgressionService(test_db)
    service.apply_outcome(player=player, source_type="VERIFIED_TRAINING_OUTCOME", source_id="training-2",
                          idempotency_key="pc5-training-2", actor=player, xp_delta=10,
                          skill_updates={"passing": 60.0}, provenance={"revision": "v1"})
    with pytest.raises(PlayerProgressionError, match="OUTCOME_REPLAY_MISMATCH"):
        service.apply_outcome(player=player, source_type="VERIFIED_TRAINING_OUTCOME", source_id="training-2",
                              idempotency_key="pc5-training-2", actor=player, xp_delta=11,
                              skill_updates={"passing": 61.0}, provenance={"revision": "v2"})


def test_invalid_or_foreign_player_outcome_is_denied(test_db):
    player, _ = _player(test_db)
    foreign, _ = _player(test_db, name="Foreign PC5 Player")
    with pytest.raises(PlayerProgressionError, match="SOURCE_PLAYER_MISMATCH"):
        PlayerProgressionService(test_db).apply_outcome(
            player=player, source_player_user_id=foreign.id, source_type="VERIFIED_TRAINING_OUTCOME",
            source_id="foreign-1", idempotency_key="foreign-1", actor=player)


def test_progression_rollback_is_atomic(test_db):
    player, _ = _player(test_db)
    player_id = player.id
    test_db.commit()
    with pytest.raises(RuntimeError, match="forced rollback"):
        PlayerProgressionService(test_db).apply_outcome(
            player=player, source_type="VERIFIED_TRAINING_OUTCOME", source_id="rollback-1",
            idempotency_key="rollback-1", actor=player, xp_delta=40,
            skill_updates={"passing": 90.0}, achievements=_grant())
        raise RuntimeError("forced rollback")
    test_db.rollback()
    assert test_db.query(PlayerProgressionOutcome).filter_by(player_user_id=player_id).count() == 0
    assert test_db.query(PlayerSkillState).filter_by(player_user_id=player_id).count() == 0
    assert test_db.query(XPTransaction).filter_by(user_id=player_id).count() == 0
    assert test_db.query(UserAchievement).filter_by(user_id=player_id).count() == 0
    assert test_db.get(User, player_id).xp_balance == 0
    assert test_db.get(User, player_id).credit_balance == 37


def test_concurrent_outcome_processing_is_exactly_once():
    setup = sessionmaker(bind=engine)()
    try:
        player, _ = _player(setup, name="Concurrent PC5 Player")
        player_id = player.id
        source_key = f"concurrent-{player_id}"
        setup.commit()
    finally:
        setup.close()
    barrier = Barrier(2)
    def apply():
        db = sessionmaker(bind=engine)()
        try:
            actor = db.get(User, player_id)
            barrier.wait(timeout=5)
            outcome = PlayerProgressionService(db).apply_outcome(
                player=actor, source_type="VERIFIED_TRAINING_OUTCOME", source_id=source_key,
                idempotency_key=source_key, actor=actor, xp_delta=30,
                skill_updates={"passing": 75.0}, achievements=_grant())
            db.commit()
            return outcome.id
        finally:
            db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: apply(), range(2)))
    assert result[0] == result[1]
    verify = sessionmaker(bind=engine)()
    try:
        assert verify.query(PlayerProgressionOutcome).filter_by(player_user_id=player_id).count() == 1
        assert verify.query(XPTransaction).filter_by(user_id=player_id).count() == 1
        assert verify.query(UserAchievement).filter_by(user_id=player_id).count() == 1
        assert verify.get(User, player_id).xp_balance == 30
        assert verify.get(User, player_id).credit_balance == 37
    finally:
        verify.close()


def test_pc4_assessment_outcome_has_exact_release_provenance_and_progression_link(test_db):
    _, release, lesson, placement, quiz = _assessment(test_db, questions=1)
    quiz.xp_reward = 25
    player, _ = _player(test_db, name="Assessment Chain Player")
    service = AssessmentIntegrityService(test_db)
    attempt = service.start_attempt(user=player, assessment_id=placement.id, locale="en")
    issued = attempt.issued_questions[0]
    service.answer_question(
        user=player, attempt_id=attempt.id, issued_question_id=issued.id,
        selected_option_id=issued.correct_option_id, idempotency_key="pc5-chain-answer",
    )
    completed = service.complete_attempt(
        user=player, attempt_id=attempt.id, idempotency_key="pc5-chain-complete",
    )
    outcome = test_db.query(PlayerProgressionOutcome).filter_by(
        source_type="EDUCATION_ASSESSMENT", source_id=str(attempt.id),
    ).one()
    event = test_db.query(EducationProgressEvent).filter_by(assessment_attempt_id=attempt.id).one()
    assert event.progression_outcome_id == outcome.id
    assert outcome.provenance["track_release_id"] == str(release.id)
    assert outcome.provenance["lesson_id"] == str(lesson.id)
    assert outcome.provenance["lesson_assessment_id"] == str(placement.id)
    assert outcome.provenance["quiz_revision"] == completed.quiz_revision
    assert outcome.provenance["issued_question_snapshot_hash"]
    assert completed.xp_awarded == 25


def test_instructor_skill_assessment_writes_canonical_state_and_history(test_db):
    player, license_row = _player(test_db, name="Skill Assessment Player")
    instructor = User(
        name="PC5 Instructor", email=f"pc5-instructor-{uuid4().hex}@example.com",
        password_hash="test-hash", role=UserRole.INSTRUCTOR, is_active=True,
        date_of_birth=date(1990, 1, 1),
    )
    test_db.add(instructor)
    test_db.flush()
    assessment, created = FootballSkillService(test_db).create_assessment(
        user_license_id=license_row.id, skill_name="passing", points_earned=8,
        points_total=10, assessed_by=instructor.id, notes="Canonical provenance",
    )
    assert created is True
    assert assessment.progression_outcome_id is not None
    state = test_db.query(PlayerSkillState).filter_by(player_user_id=player.id, skill_key="passing").one()
    change = test_db.query(PlayerSkillChange).filter_by(outcome_id=assessment.progression_outcome_id).one()
    outcome = test_db.get(PlayerProgressionOutcome, assessment.progression_outcome_id)
    assert state.current_value == 80.0
    assert change.new_value == 80.0
    assert change.previous_value == 0.0
    assert outcome.actor_user_id == instructor.id
    assert outcome.provenance["football_skill_assessment_id"] == assessment.id
    assert license_row.football_skills["passing"] == 80.0

    before = (
        test_db.query(PlayerProgressionOutcome).count(),
        test_db.query(PlayerSkillChange).count(),
        dict(license_row.football_skills),
    )
    assert FootballSkillService(test_db).recalculate_skill_average(
        user_license_id=license_row.id, skill_name="passing"
    ) == 80.0
    assert (
        test_db.query(PlayerProgressionOutcome).count(),
        test_db.query(PlayerSkillChange).count(),
        dict(license_row.football_skills),
    ) == before


def test_progression_read_api_is_pure_and_returns_canonical_projection(client, test_db):
    player, _ = _player(test_db, name="Read Only Player")
    outcome = PlayerProgressionService(test_db).apply_outcome(
        player=player, source_type="VERIFIED_TRAINING_OUTCOME", source_id="read-1",
        idempotency_key="read-1", actor=player, xp_delta=15,
        skill_updates={"passing": 65.0}, achievements=_grant(),
        provenance={"revision": "v1"},
    )
    test_db.commit()
    before = {
        "outcomes": test_db.query(PlayerProgressionOutcome).count(),
        "skills": test_db.query(PlayerSkillState).count(),
        "changes": test_db.query(PlayerSkillChange).count(),
        "xp": test_db.query(XPTransaction).count(),
        "achievements": test_db.query(UserAchievement).count(),
    }
    token = create_access_token({"sub": player.email})
    headers = {"Authorization": f"Bearer {token}"}
    state_response = client.get("/api/v1/progression/player-state", headers=headers)
    history_response = client.get("/api/v1/progression/player-history", headers=headers)
    assert state_response.status_code == 200, state_response.text
    assert history_response.status_code == 200, history_response.text
    assert state_response.json()["xp"] == 15
    assert state_response.json()["skills"][0]["last_outcome_id"] == str(outcome.id)
    assert history_response.json()[0]["id"] == str(outcome.id)
    after = {
        "outcomes": test_db.query(PlayerProgressionOutcome).count(),
        "skills": test_db.query(PlayerSkillState).count(),
        "changes": test_db.query(PlayerSkillChange).count(),
        "xp": test_db.query(XPTransaction).count(),
        "achievements": test_db.query(UserAchievement).count(),
    }
    assert after == before


def test_legacy_gamification_get_is_pure_and_player_refresh_is_denied(client, test_db):
    player, _ = _player(test_db, name="Pure Gamification Player")
    PlayerProgressionService(test_db).apply_outcome(
        player=player, source_type="VERIFIED_TRAINING_OUTCOME", source_id="gamification-read-1",
        idempotency_key="gamification-read-1", actor=player, xp_delta=20,
        achievements=_grant(), provenance={"revision": "v1"},
    )
    admin = User(
        name="PC5 Admin", email=f"pc5-admin-{uuid4().hex}@example.com",
        password_hash="test-hash", role=UserRole.ADMIN, is_active=True,
        date_of_birth=date(1990, 1, 1),
    )
    test_db.add(admin)
    test_db.commit()
    before = {
        "outcomes": test_db.query(PlayerProgressionOutcome).count(),
        "xp": test_db.query(XPTransaction).count(),
        "achievements": test_db.query(UserAchievement).count(),
        "stats_updated_at": test_db.query(UserStats).filter_by(user_id=player.id).one().updated_at,
    }
    player_token = create_access_token({"sub": player.email})
    response = client.get(
        "/api/v1/gamification/me", headers={"Authorization": f"Bearer {player_token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["stats"]["total_xp"] == 20
    after = {
        "outcomes": test_db.query(PlayerProgressionOutcome).count(),
        "xp": test_db.query(XPTransaction).count(),
        "achievements": test_db.query(UserAchievement).count(),
        "stats_updated_at": test_db.query(UserStats).filter_by(user_id=player.id).one().updated_at,
    }
    assert after == before

    admin_token = create_access_token({"sub": admin.email})
    denied = client.post(
        f"/api/v1/gamification/refresh/{player.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["message"] == (
        "Canonical Player progression is outcome-driven; refresh is not permitted"
    )


def test_legacy_skill_update_api_delegates_to_canonical_outcome(client, test_db):
    player, license_row = _player(test_db, name="Legacy API Player")
    instructor = User(
        name="Legacy API Instructor", email=f"pc5-api-instructor-{uuid4().hex}@example.com",
        password_hash="test-hash", role=UserRole.INSTRUCTOR, is_active=True,
        date_of_birth=date(1990, 1, 1),
    )
    test_db.add(instructor)
    test_db.commit()
    token = create_access_token({"sub": instructor.email})
    payload = {"heading": 61, "shooting": 62, "crossing": 63, "passing": 64,
               "dribbling": 65, "ball_control": 66}
    response = client.put(
        f"/api/v1/licenses/{license_row.id}/football-skills", json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    outcome_id = response.json()["progression_outcome_id"]
    assert test_db.get(PlayerProgressionOutcome, outcome_id) is not None
    assert test_db.query(PlayerSkillState).filter_by(player_user_id=player.id).count() == 6
    # Identical request is an idempotent replay, not a second history event.
    replay = client.put(
        f"/api/v1/licenses/{license_row.id}/football-skills", json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert replay.status_code == 200
    assert replay.json()["progression_outcome_id"] == outcome_id
    assert test_db.query(PlayerProgressionOutcome).filter_by(
        player_user_id=player.id, source_type="LEGACY_INSTRUCTOR_SKILL_UPDATE"
    ).count() == 1


def test_non_entitled_player_outcome_is_denied(test_db):
    row = User(
        name="No Entitlement", email=f"pc5-no-entitlement-{uuid4().hex}@example.com",
        password_hash="test-hash", role=UserRole.STUDENT, is_active=True,
        date_of_birth=date(2000, 1, 1), credit_balance=11, xp_balance=0,
    )
    test_db.add(row)
    test_db.flush()
    with pytest.raises(PlayerProgressionError):
        PlayerProgressionService(test_db).apply_outcome(
            player=row, source_type="VERIFIED_TRAINING_OUTCOME", source_id="denied-1",
            idempotency_key="denied-1", actor=row, xp_delta=20,
        )
    assert row.xp_balance == 0
    assert row.credit_balance == 11
