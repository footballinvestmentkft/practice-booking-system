from datetime import date
from uuid import uuid4

from app.dependencies import get_current_user
from app.main import app
from app.models.user import User, UserRole
from app.services.player_identity_service import issue_football_player_entitlement
from tests.integration.test_pc3_education_core_postgres import _draft_tree


def _player(db, *, entitled=True):
    user = User(
        name="PC3 API Player",
        email=f"pc3-api-{uuid4().hex}@example.com",
        password_hash="test-hash",
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=date(2000, 1, 1),
        credit_balance=0,
    )
    db.add(user)
    db.flush()
    if entitled:
        issue_football_player_entitlement(
            db, user=user, payment_verified=True, on_date=date(2026, 9, 10)
        )
    return user


def test_api_and_legacy_web_contract_share_canonical_published_service(client, test_db):
    service, track, release, _, _, _ = _draft_tree(test_db)
    service.publish_release(release.id)
    player = _player(test_db)
    app.dependency_overrides[get_current_user] = lambda: player

    catalog = client.get("/api/v1/education/programs/LFA_FOOTBALL_PLAYER/tracks")
    assert catalog.status_code == 200
    assert catalog.json()[0]["release_id"] == str(release.id)

    curriculum = client.get(f"/api/v1/education/tracks/{track.id}/curriculum?locale=hu")
    assert curriculum.status_code == 200
    assert curriculum.json()["release_id"] == str(release.id)
    assert curriculum.json()["modules"][0]["name"] == "Alapok"

    legacy = client.get("/api/v1/curriculum/track/LFA_FOOTBALL_PLAYER")
    assert legacy.status_code == 200
    assert legacy.json()["id"] == str(track.id)
    assert legacy.json()["release_id"] == str(release.id)


def test_api_fails_closed_for_draft_and_noncanonical_player(client, test_db):
    _, track, _, _, _, _ = _draft_tree(test_db)
    ineligible = _player(test_db, entitled=False)
    app.dependency_overrides[get_current_user] = lambda: ineligible

    denied = client.get("/api/v1/education/programs/LFA_FOOTBALL_PLAYER/tracks")
    assert denied.status_code == 403
    entitled = _player(test_db, entitled=True)
    app.dependency_overrides[get_current_user] = lambda: entitled
    hidden = client.get(f"/api/v1/education/tracks/{track.id}/curriculum")
    assert hidden.status_code == 404


def test_openapi_exposes_canonical_education_contract_and_no_legacy_mutations(client):
    schema = client.get("/api/v1/openapi.json").json()
    paths = schema["paths"]
    assert "/api/v1/education/programs/{program_id}/tracks" in paths
    assert "/api/v1/education/tracks/{track_id}/curriculum" in paths
    assert "post" not in paths.get("/api/v1/curriculum/modules/{module_id}/complete", {})
