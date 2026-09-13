from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.core.auth import create_access_token
from app.models.credit_transaction import CreditTransaction
from app.models.gamification import UserAchievement
from app.models.juggling import JugglingConsent, JugglingContactEvent, JugglingVerification, JugglingVideo
from app.models.player_progression import PlayerProgressionOutcome, PlayerSkillChange
from app.models.user import User, UserRole
from app.models.xp_transaction import XPTransaction
from app.services.juggling.verification_service import (
    ContactReviewDecision,
    HumanVerificationCommand,
    JugglingVerificationError,
    JugglingVerificationPolicy,
    JugglingVerificationService,
)
from app.services.player_identity_service import issue_football_player_entitlement


def _player(db, *, name="PC6A Player", credit=11):
    from app.models.user_progress import Specialization
    if db.get(Specialization, "LFA_FOOTBALL_PLAYER") is None:
        db.add(Specialization(id="LFA_FOOTBALL_PLAYER", is_active=True))
        db.flush()
    player = User(
        name=name, email=f"pc6a-{uuid4().hex}@example.com", password_hash="test-hash",
        role=UserRole.STUDENT, is_active=True, onboarding_completed=True,
        date_of_birth=date(2000, 1, 1), credit_balance=credit, xp_balance=0,
    )
    db.add(player)
    db.flush()
    issue_football_player_entitlement(
        db, user=player, payment_verified=True, on_date=date(2026, 9, 10),
    )
    db.add(JugglingConsent(
        user_id=player.id, service_consent=True, admin_review_consent=True,
        training_consent=False,
    ))
    db.flush()
    return player


def _admin(db):
    row = User(
        name="PC6A Admin", email=f"pc6a-admin-{uuid4().hex}@example.com",
        password_hash="test-hash", role=UserRole.ADMIN, is_active=True,
        date_of_birth=date(1990, 1, 1),
    )
    db.add(row)
    db.flush()
    return row


def _video(db, player):
    row = JugglingVideo(
        user_id=player.id, source_type="uploaded_video", upload_source="gallery",
        training_video_type="juggling", status="analyzed",
        annotation_status="human_review_pending",
        checksum_sha256="a" * 64, checksum_processed="b" * 64,
        storage_path=f"/tmp/{uuid4()}.mp4",
    )
    db.add(row)
    db.flush()
    return row


def _event(db, video, player, timestamp_ms, contact_type, side):
    row = JugglingContactEvent(
        video_id=video.id, created_by_user_id=player.id, device_event_id=uuid4(),
        timestamp_ms=timestamp_ms, contact_type=contact_type, side=side,
        annotation_confidence="certain", annotation_source="manual_user",
        annotation_review_status="pending", taxonomy_review_status="not_applicable",
        excluded_from_training=True, excluded_from_count=False,
        taxonomy_version="v1", version=1,
    )
    db.add(row)
    db.flush()
    return row


def _command(service, video, player, events, *, status="VALID"):
    revision = service.get_candidate(video_id=video.id).annotation_revision_hash
    decisions = tuple(ContactReviewDecision(
        event_id=e.id, decision="CONFIRM", include_in_sequence=True,
    ) for e in events)
    return HumanVerificationCommand(
        player_user_id=player.id,
        expected_annotation_revision_hash=revision,
        event_decisions=decisions,
        status=status,
        sequence_start_ms=events[0].timestamp_ms if events else None,
        sequence_end_ms=events[-1].timestamp_ms if events else None,
        termination_reason="VIDEO_ENDED" if events else "INVALID_SEQUENCE",
        confidence=0.95,
    )


def test_valid_human_review_creates_immutable_result_and_exact_pc5_provenance(test_db):
    player, reviewer = _player(test_db), _admin(test_db)
    video = _video(test_db, player)
    events = [
        _event(test_db, video, player, 100, "right_instep", "right"),
        _event(test_db, video, player, 450, "left_knee", "left"),
    ]
    service = JugglingVerificationService(test_db, policy=JugglingVerificationPolicy(xp_reward=25))
    record = service.verify_human(video_id=video.id, reviewer=reviewer, command=_command(service, video, player, events))
    test_db.commit()

    assert record.status == "VALID"
    assert record.verification_method == "HUMAN"
    assert record.touch_count == record.score == 2
    assert record.side_breakdown == {"left": 1, "right": 1}
    assert record.body_part_breakdown == {"instep": 1, "knee": 1}
    assert record.video_checksum_sha256 == "b" * 64
    assert len(record.provenance_hash) == 64
    assert record.progression_outcome_id is not None
    outcome = test_db.get(PlayerProgressionOutcome, record.progression_outcome_id)
    assert outcome.source_type == "JUGGLING_VERIFICATION"
    assert outcome.source_id == str(record.id)
    assert outcome.provenance["verification_id"] == str(record.id)
    assert outcome.provenance["contact_event_revision_hash"] == record.contact_event_revision_hash
    assert test_db.query(XPTransaction).filter_by(user_id=player.id).count() == 1
    assert test_db.query(PlayerSkillChange).filter_by(player_user_id=player.id).count() == 0
    assert test_db.query(UserAchievement).filter_by(user_id=player.id).count() == 0
    assert test_db.query(CreditTransaction).filter_by(user_id=player.id).count() == 0

    record.touch_count = 99
    with pytest.raises(DBAPIError):
        test_db.commit()
    test_db.rollback()


def test_zero_touch_attempt_is_invalid_and_never_positive_progression(test_db):
    player, reviewer = _player(test_db), _admin(test_db)
    video = _video(test_db, player)
    service = JugglingVerificationService(
        test_db, policy=JugglingVerificationPolicy(xp_reward=25, credit_reward=3),
    )
    command = _command(service, video, player, [], status="INVALID")
    record = service.verify_human(video_id=video.id, reviewer=reviewer, command=command)
    test_db.flush()
    outcome = test_db.get(PlayerProgressionOutcome, record.progression_outcome_id)
    assert record.score == record.touch_count == 0
    assert outcome.xp_delta == 0
    assert test_db.query(XPTransaction).filter_by(user_id=player.id).count() == 0
    assert test_db.query(PlayerSkillChange).filter_by(player_user_id=player.id).count() == 0
    assert test_db.query(UserAchievement).filter_by(user_id=player.id).count() == 0
    assert test_db.query(CreditTransaction).filter_by(user_id=player.id).count() == 0


def test_verified_result_cannot_be_deleted(test_db):
    player, reviewer = _player(test_db), _admin(test_db)
    video = _video(test_db, player)
    event = _event(test_db, video, player, 100, "right_instep", "right")
    service = JugglingVerificationService(test_db)
    record = service.verify_human(
        video_id=video.id,
        reviewer=reviewer,
        command=_command(service, video, player, [event]),
    )
    test_db.commit()

    test_db.delete(record)
    with pytest.raises(DBAPIError):
        test_db.commit()
    test_db.rollback()


def test_human_correction_reuses_contact_event_and_records_reviewer_audit(test_db):
    player, reviewer = _player(test_db), _admin(test_db)
    video = _video(test_db, player)
    event = _event(test_db, video, player, 200, "custom_other", "unknown")
    service = JugglingVerificationService(test_db)
    revision = service.get_candidate(video_id=video.id).annotation_revision_hash
    command = HumanVerificationCommand(
        player_user_id=player.id,
        expected_annotation_revision_hash=revision,
        event_decisions=(ContactReviewDecision(
            event_id=event.id,
            decision="CORRECT",
            include_in_sequence=True,
            corrected_contact_type="left_hip",
            note="Reviewer resolved the body-part taxonomy.",
        ),),
        status="VALID",
        sequence_start_ms=200,
        sequence_end_ms=200,
        termination_reason="VIDEO_ENDED",
        confidence=0.9,
    )
    record = service.verify_human(video_id=video.id, reviewer=reviewer, command=command)
    test_db.flush()

    assert record.touch_count == 1
    assert record.side_breakdown == {"left": 1}
    assert record.body_part_breakdown == {"hip": 1}
    assert event.contact_type == "left_hip"
    assert event.side == "left"
    assert event.annotation_source == "user_corrected"
    assert event.annotation_review_status == "corrected"
    assert event.reviewed_by_user_id == reviewer.id
    assert event.reviewed_at is not None
    assert event.review_note == "Reviewer resolved the body-part taxonomy."


def test_replay_returns_same_verification_and_xp_and_credit_are_exactly_once(test_db):
    player, reviewer = _player(test_db), _admin(test_db)
    video = _video(test_db, player)
    event = _event(test_db, video, player, 100, "right_instep", "right")
    service = JugglingVerificationService(
        test_db, policy=JugglingVerificationPolicy(xp_reward=10, credit_reward=2),
    )
    command = _command(service, video, player, [event])
    first = service.verify_human(video_id=video.id, reviewer=reviewer, command=command)
    second = service.verify_human(video_id=video.id, reviewer=reviewer, command=command)
    test_db.flush()
    assert first.id == second.id
    assert test_db.query(JugglingVerification).filter_by(video_id=video.id).count() == 1
    assert test_db.query(PlayerProgressionOutcome).filter_by(
        source_type="JUGGLING_VERIFICATION", player_user_id=player.id,
    ).count() == 1
    assert test_db.query(XPTransaction).filter_by(user_id=player.id).count() == 1
    assert test_db.query(CreditTransaction).filter_by(user_id=player.id).count() == 1
    test_db.refresh(player)
    assert player.xp_balance == 10
    assert player.credit_balance == 13


def test_modified_annotation_revision_and_foreign_player_fail_closed(test_db):
    player, foreign, reviewer = _player(test_db), _player(test_db, name="Foreign"), _admin(test_db)
    video = _video(test_db, player)
    event = _event(test_db, video, player, 100, "right_instep", "right")
    service = JugglingVerificationService(test_db)
    command = _command(service, video, player, [event])
    event.timestamp_ms = 101
    event.version += 1
    test_db.flush()
    with pytest.raises(JugglingVerificationError, match="ANNOTATION_REVISION_MISMATCH"):
        service.verify_human(video_id=video.id, reviewer=reviewer, command=command)

    foreign_command = HumanVerificationCommand(**{
        **command.__dict__, "player_user_id": foreign.id,
    })
    with pytest.raises(JugglingVerificationError, match="PLAYER_VIDEO_MISMATCH"):
        service.verify_human(video_id=video.id, reviewer=reviewer, command=foreign_command)


def test_verification_rollback_removes_record_outcome_xp_and_credit(test_db, monkeypatch):
    player, reviewer = _player(test_db), _admin(test_db)
    player_id = player.id
    video = _video(test_db, player)
    event = _event(test_db, video, player, 100, "right_instep", "right")
    service = JugglingVerificationService(
        test_db, policy=JugglingVerificationPolicy(xp_reward=10, credit_reward=1),
    )
    command = _command(service, video, player, [event])
    # Establish the attempt as pre-existing input.  The rollback under test must
    # remove only the verification transaction and its downstream writes.
    test_db.commit()

    def fail_credit(*args, **kwargs):
        raise RuntimeError("forced credit failure")

    monkeypatch.setattr("app.services.juggling.verification_service.CreditService.credit", fail_credit)
    with pytest.raises(RuntimeError, match="forced credit failure"):
        service.verify_human(video_id=video.id, reviewer=reviewer, command=command)
        test_db.commit()
    test_db.rollback()
    assert test_db.query(JugglingVerification).filter_by(player_user_id=player_id).count() == 0
    assert test_db.query(PlayerProgressionOutcome).filter_by(player_user_id=player_id).count() == 0
    assert test_db.query(XPTransaction).filter_by(user_id=player_id).count() == 0
    assert test_db.query(CreditTransaction).filter_by(user_id=player_id).count() == 0
    assert test_db.get(User, player_id).xp_balance == 0
    assert test_db.get(User, player_id).credit_balance == 11


def test_concurrent_verification_is_exactly_once():
    setup = sessionmaker(bind=engine)()
    try:
        player, reviewer = _player(setup), _admin(setup)
        video = _video(setup, player)
        event = _event(setup, video, player, 100, "right_instep", "right")
        service = JugglingVerificationService(setup)
        revision = service.get_candidate(video_id=video.id).annotation_revision_hash
        ids = player.id, reviewer.id, video.id, event.id
        setup.commit()
    finally:
        setup.close()

    barrier = Barrier(2)
    def run():
        db = sessionmaker(bind=engine)()
        try:
            player_id, reviewer_id, video_id, event_id = ids
            command = HumanVerificationCommand(
                player_user_id=player_id, expected_annotation_revision_hash=revision,
                event_decisions=(ContactReviewDecision(
                    event_id=event_id, decision="CONFIRM", include_in_sequence=True,
                ),),
                status="VALID", sequence_start_ms=100, sequence_end_ms=100,
                termination_reason="VIDEO_ENDED", confidence=1.0,
            )
            barrier.wait(timeout=5)
            record = JugglingVerificationService(
                db, policy=JugglingVerificationPolicy(xp_reward=7, credit_reward=2),
            ).verify_human(video_id=video_id, reviewer=db.get(User, reviewer_id), command=command)
            db.commit()
            return record.id
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results[0] == results[1]
    verify = sessionmaker(bind=engine)()
    try:
        assert verify.query(JugglingVerification).filter_by(video_id=ids[2]).count() == 1
        assert verify.query(PlayerProgressionOutcome).filter_by(source_type="JUGGLING_VERIFICATION").filter_by(player_user_id=ids[0]).count() == 1
        assert verify.query(XPTransaction).filter_by(user_id=ids[0]).count() == 1
        assert verify.query(CreditTransaction).filter_by(user_id=ids[0]).count() == 1
        player = verify.get(User, ids[0])
        assert player.xp_balance == 7
        assert player.credit_balance == 13
    finally:
        verify.close()


def test_juggling_services_do_not_write_progression_or_credit_balances_directly():
    service_root = Path("app/services/juggling")
    forbidden = ("UPDATE users SET xp_balance", "UPDATE users SET credit_balance")
    offenders = {
        str(path): token
        for path in service_root.glob("*.py")
        for token in forbidden
        if token in path.read_text()
    }
    assert offenders == {}


def test_admin_and_player_api_share_canonical_verification_contract(
    test_db, client, monkeypatch,
):
    monkeypatch.setattr("app.config.settings.JUGGLING_POC_ENABLED", True)
    player, foreign, reviewer = _player(test_db), _player(test_db, name="Foreign API"), _admin(test_db)
    video = _video(test_db, player)
    event = _event(test_db, video, player, 123, "right_instep", "right")
    test_db.commit()

    admin_token = create_access_token(
        data={"sub": reviewer.email}, expires_delta=timedelta(hours=1),
    )
    player_token = create_access_token(
        data={"sub": player.email}, expires_delta=timedelta(hours=1),
    )
    foreign_token = create_access_token(
        data={"sub": foreign.email}, expires_delta=timedelta(hours=1),
    )
    headers = {"Authorization": f"Bearer {admin_token}"}

    candidate_response = client.get(
        f"/api/v1/admin/juggling/videos/{video.id}/verification-candidate",
        headers=headers,
    )
    assert candidate_response.status_code == 200, candidate_response.text
    candidate = candidate_response.json()
    assert candidate["player_user_id"] == player.id
    assert candidate["contact_events"][0]["event_id"] == str(event.id)

    verify_response = client.post(
        f"/api/v1/admin/juggling/videos/{video.id}/verify",
        headers=headers,
        json={
            "player_user_id": player.id,
            "expected_annotation_revision_hash": candidate["annotation_revision_hash"],
            "event_decisions": [{
                "event_id": str(event.id),
                "decision": "CONFIRM",
                "include_in_sequence": True,
            }],
            "status": "VALID",
            "sequence_start_ms": 123,
            "sequence_end_ms": 123,
            "termination_reason": "VIDEO_ENDED",
            "confidence": 1.0,
        },
    )
    assert verify_response.status_code == 200, verify_response.text
    verified = verify_response.json()
    assert verified["touch_count"] == verified["score"] == 1
    assert "contact_event_snapshot" not in verified
    assert "provenance" not in verified

    own_response = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/verification",
        headers={"Authorization": f"Bearer {player_token}"},
    )
    assert own_response.status_code == 200
    assert own_response.json() == verified

    foreign_response = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/verification",
        headers={"Authorization": f"Bearer {foreign_token}"},
    )
    assert foreign_response.status_code == 404
