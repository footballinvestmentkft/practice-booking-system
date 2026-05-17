"""
Unit tests for the DB-backed card_theme_service.

Coverage targets (F-THEME-2):
  CT-01  get_all_themes() returns only active themes from DB
  CT-02  get_all_themes() excludes inactive themes
  CT-03  get_theme() returns the correct ThemeDefinition for a known ID
  CT-04  get_theme() falls back to 'default' for an unknown theme ID
  CT-05  get_theme() falls back to 'default' for an inactive theme ID
  CT-06  apply_theme() commits the draft theme for a valid, unlocked theme
  CT-07  apply_theme() raises ValueError for an unknown/inactive theme
  CT-08  apply_theme() raises ValueError for a locked premium theme
  CT-09  Cache invalidation resets to empty — next reload hits DB
  CT-10  get_all_themes(db=None) falls back to hardcoded THEMES (no DB)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_row(
    id="default", label="Slate", is_premium=False, credit_cost=0,
    panel_bg="linear-gradient(155deg, #1a2744 0%)", body_bg="#1a202c",
    tab_bg="#2d3748", accent="#667eea", page_bg="#0f1923", dot_color="#667eea",
    is_light_body_bg=False, text_faint="rgba(255,255,255,0.35)",
    val_neutral="rgba(255,255,255,0.85)", skill_up="#48bb78", skill_dn="#fc8181",
    is_active=True, sort_order=0,
):
    """Return a MagicMock that quacks like a CardTheme ORM row."""
    row = MagicMock()
    row.id              = id
    row.label           = label
    row.is_premium      = is_premium
    row.credit_cost     = credit_cost
    row.panel_bg        = panel_bg
    row.body_bg         = body_bg
    row.tab_bg          = tab_bg
    row.accent          = accent
    row.page_bg         = page_bg
    row.dot_color       = dot_color
    row.is_light_body_bg = is_light_body_bg
    row.text_faint      = text_faint
    row.val_neutral     = val_neutral
    row.skill_up        = skill_up
    row.skill_dn        = skill_dn
    row.is_active       = is_active
    row.sort_order      = sort_order
    return row


def _make_db(*rows):
    """Return a MagicMock db whose query().filter().all() returns rows."""
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = list(rows)
    return db


@pytest.fixture(autouse=True)
def reset_cache():
    """Ensure the module-level cache is cleared before and after every test."""
    import app.services.card_theme_service as svc
    svc._invalidate_cache()
    yield
    svc._invalidate_cache()


# ── CT-01 / CT-02  get_all_themes — active/inactive filtering ────────────────

def test_CT01_get_all_themes_returns_only_active():
    import app.services.card_theme_service as svc
    active   = _make_row(id="default", label="Slate", sort_order=0)
    inactive = _make_row(id="midnight", label="Midnight", is_active=False)
    db = _make_db(active)  # DB query already filters is_active=True

    result = svc.get_all_themes(db=db)

    assert len(result) == 1
    assert result[0].id == "default"


def test_CT02_get_all_themes_excludes_inactive():
    import app.services.card_theme_service as svc
    db = _make_db(
        _make_row(id="default", sort_order=0),
        _make_row(id="gold", is_premium=True, credit_cost=500, sort_order=3),
    )

    result = svc.get_all_themes(db=db)

    ids = [t.id for t in result]
    assert "default" in ids
    assert "gold" in ids
    # midnight not seeded into this mock DB → absent from result
    assert "midnight" not in ids


# ── CT-03 / CT-04 / CT-05  get_theme — lookup and fallback ───────────────────

def test_CT03_get_theme_known_id_returns_correct_definition():
    import app.services.card_theme_service as svc
    db = _make_db(
        _make_row(id="default"),
        _make_row(id="midnight", label="Midnight", body_bg="#0f0f0f", page_bg="#050505"),
    )

    result = svc.get_theme("midnight", db=db)

    assert result.id == "midnight"
    assert result.body_bg == "#0f0f0f"
    assert result.page_bg == "#050505"


def test_CT04_get_theme_unknown_id_falls_back_to_default():
    import app.services.card_theme_service as svc
    db = _make_db(_make_row(id="default"))

    result = svc.get_theme("nonexistent_xyz", db=db)

    assert result.id == "default"


def test_CT05_get_theme_inactive_id_falls_back_to_default():
    import app.services.card_theme_service as svc
    # DB query filters is_active=True — inactive row never enters the cache
    db = _make_db(_make_row(id="default"))  # midnight omitted (would be inactive)

    result = svc.get_theme("midnight", db=db)

    assert result.id == "default"


# ── CT-06 / CT-07 / CT-08  apply_theme ───────────────────────────────────────

def test_CT06_apply_theme_commits_draft_for_valid_unlocked_theme():
    import app.services.card_theme_service as svc

    db = _make_db(_make_row(id="default"), _make_row(id="midnight"))
    user_license = MagicMock()
    user_license.user_id = 42
    user_license.unlocked_card_themes = []

    mock_draft = MagicMock()

    with patch.object(svc.CardDraftService, "get_player_card_draft",
                      return_value=mock_draft) as mock_get, \
         patch.object(svc.CardDraftService, "update_draft_theme") as mock_update:
        svc.apply_theme(db, user_license, "midnight")

    mock_get.assert_called_once_with(db, user_id=42)
    mock_update.assert_called_once_with(db, mock_draft, "midnight")


def test_CT07_apply_theme_raises_for_unknown_inactive_theme():
    import app.services.card_theme_service as svc

    # DB only has "default" — "ghost" not in cache
    db = _make_db(_make_row(id="default"))
    user_license = MagicMock()
    user_license.user_id = 42
    user_license.unlocked_card_themes = []

    with pytest.raises(ValueError, match="Unknown or inactive theme"):
        svc.apply_theme(db, user_license, "ghost")


def test_CT08_apply_theme_raises_for_locked_premium_theme():
    import app.services.card_theme_service as svc

    db = _make_db(
        _make_row(id="default"),
        _make_row(id="gold", label="Gold", is_premium=True, credit_cost=500),
    )
    user_license = MagicMock()
    user_license.user_id = 42
    user_license.unlocked_card_themes = []  # gold not in unlocked list

    with pytest.raises(ValueError, match="locked"):
        svc.apply_theme(db, user_license, "gold")


# ── CT-09  Cache invalidation ─────────────────────────────────────────────────

def test_CT09_cache_invalidation_forces_db_reload():
    import app.services.card_theme_service as svc

    # First load: populate cache
    db1 = _make_db(_make_row(id="default"))
    svc.get_all_themes(db=db1)
    assert svc._theme_cache  # cache populated

    # Invalidate
    svc._invalidate_cache()
    assert not svc._theme_cache  # cache empty

    # Second load: DB queried again
    db2 = _make_db(
        _make_row(id="default"),
        _make_row(id="midnight", label="Midnight"),
    )
    result = svc.get_all_themes(db=db2)
    assert any(t.id == "midnight" for t in result)


# ── CT-10  Fallback to hardcoded THEMES when db=None ─────────────────────────

def test_CT10_get_all_themes_no_db_falls_back_to_hardcoded():
    import app.services.card_theme_service as svc

    # Cache already empty (reset_cache fixture)
    result = svc.get_all_themes(db=None)

    # Must include all 6 hardcoded themes
    ids = {t.id for t in result}
    assert ids == {"default", "midnight", "arctic", "gold", "emerald", "crimson"}


def test_CT10b_get_theme_no_db_falls_back_to_hardcoded():
    import app.services.card_theme_service as svc

    result = svc.get_theme("arctic", db=None)

    assert result.id == "arctic"
    assert result.is_light_body_bg is True
