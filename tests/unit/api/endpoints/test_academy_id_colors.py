"""
Academy ID Color System — Phase 1 + Phase 2 unit tests.

Phase 1 (AIC-01..AIC-10) — free colours only
─────────────────────────────────────────────
AIC-01  GET /me/academy-id/colors — no licence → 404
AIC-02  GET /me/academy-id/colors — 6 colours: 3 free owned + 3 premium not owned
AIC-03  GET /me/academy-id/colors — active_color_id reflects stored value
AIC-04  POST /me/academy-id/colors/select — unknown color_id → 400
AIC-05  POST /me/academy-id/colors/select — valid ivory → active_color_id updated
AIC-06  POST /me/academy-id/colors/select — no licence → 404
AIC-07  GET /me/academy-id — includes active_color_id (licence present)
AIC-08  GET /me/academy-id — active_color_id=None when no licence
AIC-09  academy_id_color_service is isolated from Player/Welcome card systems
AIC-10  get_active_color_id falls back to 'official' for unknown/NULL value

Phase 2 (AIC-11..AIC-20) — premium colour ownership + unlock
─────────────────────────────────────────────────────────────
AIC-11  GET /me/academy-id/colors — premium colour is_owned=True after ownership row exists
AIC-12  POST /me/academy-id/colors/select — premium not owned → 403 color_not_owned
AIC-13  POST /me/academy-id/colors/select — premium owned → updates active_color_id
AIC-14  POST /me/academy-id/colors/unlock — free colour → 400 color_is_free
AIC-15  POST /me/academy-id/colors/unlock — unknown color_id → 400 color_unknown
AIC-16  POST /me/academy-id/colors/unlock — premium sufficient credits → ok + ownership
AIC-17  POST /me/academy-id/colors/unlock — premium insufficient credits → 402
AIC-18  POST /me/academy-id/colors/unlock — already owned → ok + already_owned=True, no deduction
AIC-19  POST /me/academy-id/colors/unlock — race condition (IntegrityError) → ok + already_owned
AIC-20  Player/Welcome/Challenge card colour systems unaffected (isolation maintained)
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.api_v1.endpoints.users.profile import (
    get_academy_id_colors,
    select_academy_id_color,
    unlock_academy_id_color,
)
from app.services.academy_id_color_service import (
    ACADEMY_ID_COLORS,
    UnlockColorResult,
    get_active_color_id,
    get_all_colors,
    is_valid_color,
    set_active_color,
)
from app.services.credit_service import InsufficientCreditsError

_BASE = "app.api.api_v1.endpoints.users.profile"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(uid: int = 1, credit_balance: int = 1000):
    u = MagicMock()
    u.id             = uid
    u.credit_balance = credit_balance
    u.lfa_academy_id = "LFA-2026-00001"
    u.public_token   = "test-token-uuid"
    return u


def _license(color: str = "official", spec: str = "LFA_FOOTBALL_PLAYER"):
    lic = MagicMock()
    lic.specialization_type = spec
    lic.academy_id_color    = color
    return lic


def _db_with_license(lic, owned_color_ids: list[str] | None = None):
    rows = [(cid,) for cid in (owned_color_ids or [])]
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = lic
    db.query.return_value.filter.return_value.all.return_value   = rows
    return db


def _db_no_license():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value   = []
    return db


class _SelectPayload:
    def __init__(self, color_id: str):
        self.color_id = color_id


class _UnlockPayload:
    def __init__(self, color_id: str):
        self.color_id = color_id


# ── AIC-01 ────────────────────────────────────────────────────────────────────

def test_aic_01_colors_no_licence_raises_404():
    with pytest.raises(HTTPException) as exc:
        get_academy_id_colors(db=_db_no_license(), current_user=_user())
    assert exc.value.status_code == 404


# ── AIC-02 ────────────────────────────────────────────────────────────────────

def test_aic_02_colors_returns_six_colors_three_free_three_premium():
    """GET /me/academy-id/colors → 6 colours: 3 free owned + 3 premium not owned."""
    db  = _db_with_license(_license())
    res = get_academy_id_colors(db=db, current_user=_user())

    assert res["active_color_id"] == "official"
    colors = res["colors"]
    assert len(colors) == 6

    free_colors    = [c for c in colors if not c["is_premium"]]
    premium_colors = [c for c in colors if c["is_premium"]]

    assert {c["id"] for c in free_colors}    == {"official", "ivory", "charcoal"}
    assert {c["id"] for c in premium_colors} == {"navy", "burgundy", "forest"}

    for c in free_colors:
        assert c["is_owned"] is True
        assert c["credit_cost"] == 0
    for c in premium_colors:
        assert c["is_owned"]    is False
        assert c["credit_cost"] == 300


# ── AIC-03 ────────────────────────────────────────────────────────────────────

def test_aic_03_colors_reflects_stored_active_color():
    db  = _db_with_license(_license(color="charcoal"))
    res = get_academy_id_colors(db=db, current_user=_user())
    assert res["active_color_id"] == "charcoal"


# ── AIC-04 ────────────────────────────────────────────────────────────────────

def test_aic_04_select_unknown_color_raises_400():
    with pytest.raises(HTTPException) as exc:
        select_academy_id_color(
            payload=_SelectPayload("totally_unknown_color"),
            db=_db_with_license(_license()),
            current_user=_user(),
        )
    assert exc.value.status_code == 400
    assert "totally_unknown_color" in exc.value.detail


# ── AIC-05 ────────────────────────────────────────────────────────────────────

def test_aic_05_select_ivory_updates_color():
    lic = _license(color="official")
    db  = _db_with_license(lic)

    res = select_academy_id_color(
        payload=_SelectPayload("ivory"),
        db=db,
        current_user=_user(),
    )

    assert res["ok"] is True
    assert res["active_color_id"] == "ivory"
    assert lic.academy_id_color == "ivory"
    db.commit.assert_called_once()


# ── AIC-06 ────────────────────────────────────────────────────────────────────

def test_aic_06_select_no_licence_raises_404():
    with pytest.raises(HTTPException) as exc:
        select_academy_id_color(
            payload=_SelectPayload("charcoal"),
            db=_db_no_license(),
            current_user=_user(),
        )
    assert exc.value.status_code == 404


# ── AIC-07 ────────────────────────────────────────────────────────────────────

def test_aic_07_get_academy_id_includes_active_color():
    from app.api.api_v1.endpoints.users.profile import get_academy_id

    user = _user()
    lic  = _license(color="ivory")
    db   = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = lic

    with patch(f"{_BASE}.assign_lfa_academy_id"), \
         patch(f"{_BASE}.ensure_public_token"):
        res = get_academy_id(db=db, current_user=user)

    assert res["active_color_id"] == "ivory"


# ── AIC-08 ────────────────────────────────────────────────────────────────────

def test_aic_08_get_academy_id_active_color_none_without_licence():
    from app.api.api_v1.endpoints.users.profile import get_academy_id

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch(f"{_BASE}.assign_lfa_academy_id"), \
         patch(f"{_BASE}.ensure_public_token"):
        res = get_academy_id(db=db, current_user=_user())

    assert res["active_color_id"] is None


# ── AIC-09 ────────────────────────────────────────────────────────────────────

def test_aic_09_color_service_is_isolated():
    import ast, pathlib

    src  = pathlib.Path("app/services/academy_id_color_service.py").read_text()
    tree = ast.parse(src)
    forbidden = {"card_color_service", "card_theme_service", "shop_catalog_service"}
    imported  = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else ([node.module] if node.module else []))
            for name in names:
                for f in forbidden:
                    if f in (name or ""):
                        imported.add(f)
    assert not imported, f"academy_id_color_service must not import: {imported}"


# ── AIC-10 ────────────────────────────────────────────────────────────────────

def test_aic_10_get_active_color_id_fallback():
    assert get_active_color_id(_license(color=None))              == "official"
    assert get_active_color_id(_license(color="totally_unknown")) == "official"
    assert get_active_color_id(_license(color=""))                == "official"
    assert get_active_color_id(_license(color="ivory"))           == "ivory"


# ── AIC-11 ────────────────────────────────────────────────────────────────────

def test_aic_11_premium_color_is_owned_when_ownership_row_exists():
    db  = _db_with_license(_license(color="navy"), owned_color_ids=["navy"])
    res = get_academy_id_colors(db=db, current_user=_user())
    colors = {c["id"]: c for c in res["colors"]}
    assert colors["navy"]["is_owned"]     is True
    assert colors["burgundy"]["is_owned"] is False
    assert colors["official"]["is_owned"] is True


# ── AIC-12 ────────────────────────────────────────────────────────────────────

def test_aic_12_select_premium_not_owned_raises_403():
    db = _db_with_license(_license(), owned_color_ids=[])
    with pytest.raises(HTTPException) as exc:
        select_academy_id_color(
            payload=_SelectPayload("navy"),
            db=db,
            current_user=_user(),
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"]       == "color_not_owned"
    assert exc.value.detail["color_id"]   == "navy"
    assert exc.value.detail["credit_cost"] == 300


# ── AIC-13 ────────────────────────────────────────────────────────────────────

def test_aic_13_select_premium_owned_updates_color():
    lic = _license(color="official")
    db  = _db_with_license(lic, owned_color_ids=["navy"])
    res = select_academy_id_color(
        payload=_SelectPayload("navy"),
        db=db,
        current_user=_user(),
    )
    assert res["ok"] is True
    assert res["active_color_id"] == "navy"
    assert lic.academy_id_color   == "navy"
    db.commit.assert_called_once()


# ── AIC-14 ────────────────────────────────────────────────────────────────────

def test_aic_14_unlock_free_color_raises_400():
    with pytest.raises(HTTPException) as exc:
        unlock_academy_id_color(
            payload=_UnlockPayload("ivory"),
            db=_db_with_license(_license()),
            current_user=_user(),
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "color_is_free"


# ── AIC-15 ────────────────────────────────────────────────────────────────────

def test_aic_15_unlock_unknown_color_raises_400():
    with pytest.raises(HTTPException) as exc:
        unlock_academy_id_color(
            payload=_UnlockPayload("nonexistent"),
            db=_db_with_license(_license()),
            current_user=_user(),
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "color_unknown"


# ── AIC-16 ────────────────────────────────────────────────────────────────────

def test_aic_16_unlock_premium_sufficient_credits_returns_ok():
    mock_result = UnlockColorResult(ok=True, already_owned=False, credits_charged=300,
                                     credit_balance=700, color_id="navy")
    with patch(f"{_BASE}._unlock_academy_id_color", return_value=mock_result):
        res = unlock_academy_id_color(
            payload=_UnlockPayload("navy"),
            db=_db_with_license(_license()),
            current_user=_user(credit_balance=1000),
        )
    assert res["ok"]              is True
    assert res["already_owned"]   is False
    assert res["credits_charged"] == 300
    assert res["balance_after"]   == 700


# ── AIC-17 ────────────────────────────────────────────────────────────────────

def test_aic_17_unlock_insufficient_credits_raises_402():
    with patch(f"{_BASE}._unlock_academy_id_color",
               side_effect=InsufficientCreditsError(required=300, available=100)):
        with pytest.raises(HTTPException) as exc:
            unlock_academy_id_color(
                payload=_UnlockPayload("navy"),
                db=_db_with_license(_license()),
                current_user=_user(credit_balance=100),
            )
    assert exc.value.status_code          == 402
    assert exc.value.detail["code"]       == "insufficient_credits"
    assert exc.value.detail["required"]   == 300
    assert exc.value.detail["available"]  == 100


# ── AIC-18 ────────────────────────────────────────────────────────────────────

def test_aic_18_unlock_already_owned_returns_ok_no_deduction():
    mock_result = UnlockColorResult(ok=True, already_owned=True, credits_charged=0,
                                     credit_balance=1000, color_id="navy")
    with patch(f"{_BASE}._unlock_academy_id_color", return_value=mock_result):
        res = unlock_academy_id_color(
            payload=_UnlockPayload("navy"),
            db=_db_with_license(_license(), owned_color_ids=["navy"]),
            current_user=_user(credit_balance=1000),
        )
    assert res["ok"]              is True
    assert res["already_owned"]   is True
    assert res["credits_charged"] == 0


# ── AIC-19 ────────────────────────────────────────────────────────────────────

def test_aic_19_unlock_race_condition_returns_already_owned():
    db   = _db_with_license(_license())
    user = _user(credit_balance=1000)
    with patch(f"{_BASE}._unlock_academy_id_color",
               side_effect=IntegrityError("uq_cco_user_type_color", {}, Exception())):
        res = unlock_academy_id_color(
            payload=_UnlockPayload("navy"),
            db=db,
            current_user=user,
        )
    assert res["ok"]            is True
    assert res["already_owned"] is True
    db.rollback.assert_called_once()


# ── AIC-20 ────────────────────────────────────────────────────────────────────

def test_aic_20_player_welcome_challenge_card_systems_unaffected():
    import ast, pathlib

    for path_str in ["app/services/card_color_service.py", "app/services/card_theme_service.py"]:
        p = pathlib.Path(path_str)
        if not p.exists():
            continue
        src  = p.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                         else ([node.module] if node.module else []))
                for name in names:
                    assert "academy_id_color" not in (name or ""), \
                        f"{path_str} must not import academy_id_color_service"

    from app.services.card_color_service import FAMILY_COLORS
    player_ids = set(FAMILY_COLORS.get("player_card", {}).keys())
    assert player_ids.isdisjoint({"navy", "burgundy", "forest"}), \
        f"Academy ID premium colours leaked into player_card: {player_ids & {'navy','burgundy','forest'}}"
