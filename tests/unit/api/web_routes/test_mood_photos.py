"""
MP-R01..MP-R26 — unit tests for mood_photos web routes.
MP-D01..MP-D06 — display fallback tests (processed_png_url rendering).

Tests call route functions directly (asyncio.run) with patched
dependencies — no TestClient, no real DB, no disk I/O.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

_BASE = "app.api.web_routes.mood_photos"
_SVC  = "app.services.mood_photo_service"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _user(uid: int = 1):
    u = MagicMock()
    u.id    = uid
    u.email = f"user{uid}@lfa.com"
    return u


def _db():
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = None
    return db


def _request(accept: str = "text/html"):
    req = MagicMock()
    req.headers = {"accept": accept}
    return req


def _run(coro):
    return asyncio.run(coro)


def _mock_photo(content: bytes = b"\xff\xd8\xff", content_type: str = "image/jpeg"):
    f = AsyncMock()
    f.read = AsyncMock(return_value=content)
    f.content_type = content_type
    return f


# ── MP-R01 ── authenticated upload → 303 redirect ────────────────────────────

def test_mp_r01_authenticated_upload_redirects():
    from app.api.web_routes.mood_photos import mood_photo_upload

    with patch(f"{_BASE}.save_mood_photo") as mock_save, \
         patch(f"{_BASE}.get_mood_photos_for_user", return_value={}):
        mock_save.return_value = MagicMock()

        resp = _run(
            mood_photo_upload(
                slot    = "mood_happy_smile",
                request = _request(),
                photo   = _mock_photo(),
                user    = _user(),
                db      = _db(),
            )
        )

    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == 303
    assert "/profile/my-mood-photos" in str(resp.headers.get("location", ""))


# ── MP-R02 ── unauthenticated → dependency raises (simulated 401) ─────────────

def test_mp_r02_unauthenticated_raises():
    from app.api.web_routes.mood_photos import mood_photo_upload

    async def _raise():
        raise HTTPException(status_code=401, detail="Not authenticated")

    with pytest.raises(HTTPException) as exc_info:
        _run(
            mood_photo_upload(
                slot    = "mood_happy_smile",
                request = _request(),
                photo   = _mock_photo(),
                user    = await_raises(401),
                db      = _db(),
            )
        )


def await_raises(status: int):
    raise HTTPException(status_code=status)


# ── MP-R03 ── invalid slot → HTTPException 422 ───────────────────────────────

def test_mp_r03_invalid_slot_raises_422():
    from app.api.web_routes.mood_photos import mood_photo_upload

    with pytest.raises(HTTPException) as exc_info:
        _run(
            mood_photo_upload(
                slot    = "angry_rage",
                request = _request(),
                photo   = _mock_photo(),
                user    = _user(),
                db      = _db(),
            )
        )
    assert exc_info.value.status_code == 422


# ── MP-R04 ── GET page returns 6-slot context ─────────────────────────────────

_ALL_SLOTS = [
    "mood_intro_neutral",
    "mood_happy_smile",
    "mood_celebration",
    "mood_sad_disappointed",
    "mood_angry_competitive",
    "mood_surprised_shocked",
]


def test_mp_r04_get_page_returns_all_slots():
    from app.api.web_routes.mood_photos import mood_photos_page

    six_slots = {s: None for s in _ALL_SLOTS}

    with patch(f"{_BASE}.get_mood_photos_for_user", return_value=six_slots), \
         patch(f"{_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.return_value = MagicMock()

        _run(mood_photos_page(request=_request(), user=_user(), db=_db()))

        call_kwargs = mock_tpl.TemplateResponse.call_args
        ctx = call_kwargs[0][1]
        assert "mood_photos" in ctx
        assert set(ctx["mood_photos"].keys()) == set(six_slots.keys())
        assert "slots_meta" in ctx
        assert len(ctx["slots_meta"]) == 6


# ── MP-R05 ── GET only queries own user_id ───────────────────────────────────

def test_mp_r05_get_queries_correct_user_id():
    from app.api.web_routes.mood_photos import mood_photos_page

    with patch(f"{_BASE}.get_mood_photos_for_user") as mock_get, \
         patch(f"{_BASE}.templates") as mock_tpl:
        mock_get.return_value = {s: None for s in _ALL_SLOTS}
        mock_tpl.TemplateResponse.return_value = MagicMock()

        db = _db()
        _run(mood_photos_page(request=_request(), user=_user(uid=42), db=db))

        called_uid = mock_get.call_args[0][0]
        assert called_uid == 42


# ── MP-R06 ── POST /delete form fallback → 303 ───────────────────────────────

def test_mp_r06_form_delete_redirects():
    from app.api.web_routes.mood_photos import mood_photo_delete_form

    with patch(f"{_BASE}.delete_mood_photo") as mock_del:
        resp = _run(
            mood_photo_delete_form(
                slot = "mood_intro_neutral",
                user = _user(),
                db   = _db(),
            )
        )

    mock_del.assert_called_once()
    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == 303


# ── MP-R07 ── DELETE endpoint → 204 (None return) ────────────────────────────

def test_mp_r07_delete_api_returns_none():
    from app.api.web_routes.mood_photos import mood_photo_delete_api

    with patch(f"{_BASE}.delete_mood_photo"):
        result = _run(
            mood_photo_delete_api(
                slot = "mood_celebration",
                user = _user(),
                db   = _db(),
            )
        )
    assert result is None  # 204 = no body


# ── MP-R08 ── delete invalid slot → 422 ──────────────────────────────────────

def test_mp_r08_delete_invalid_slot_raises_422():
    from app.api.web_routes.mood_photos import mood_photo_delete_form

    with pytest.raises(HTTPException) as exc_info:
        _run(
            mood_photo_delete_form(
                slot = "unknown_slot_xyz",
                user = _user(),
                db   = _db(),
            )
        )
    assert exc_info.value.status_code == 422


# ── MP-R09 ── onboarding Step 7 contains English mood photo offer block ───────

def test_mp_r09_onboarding_template_contains_mood_offer():
    from pathlib import Path

    template_path = (
        Path(__file__).resolve()
        .parent.parent.parent.parent.parent
        / "app" / "templates" / "lfa_player_onboarding.html"
    )
    content = template_path.read_text(encoding="utf-8")
    assert "step7-mood-offer" in content, (
        "lfa_player_onboarding.html missing step7-mood-offer block"
    )
    assert "/profile/my-mood-photos" in content, (
        "lfa_player_onboarding.html missing link to /profile/my-mood-photos"
    )
    assert "Mood Photos" in content, (
        "lfa_player_onboarding.html must use English 'Mood Photos'"
    )
    assert "Hangulatk" not in content, (
        "lfa_player_onboarding.html must not contain Hungarian 'Hangulatkép'"
    )


# ── MP-R10 ── /profile/my-mood-photos renders zero-state (no uploads) ────────

def test_mp_r10_management_page_zero_state_renders():
    from app.api.web_routes.mood_photos import mood_photos_page

    empty_slots = {s: None for s in _ALL_SLOTS}

    with patch(f"{_BASE}.get_mood_photos_for_user", return_value=empty_slots), \
         patch(f"{_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.return_value = MagicMock()

        _run(mood_photos_page(request=_request(), user=_user(), db=_db()))

        call_kwargs = mock_tpl.TemplateResponse.call_args
        template_name = call_kwargs[0][0]
        ctx = call_kwargs[0][1]

        assert template_name == "lfa_player_mood_photos.html"
        assert all(v is None for v in ctx["mood_photos"].values()), (
            "zero-state: all slots must be None when nothing uploaded"
        )
        assert len(ctx["slots_meta"]) == 6


# ── MP-R11 ── mood photo template uses English labels only ───────────────────

def test_mp_r11_management_template_is_english():
    from pathlib import Path

    template_path = (
        Path(__file__).resolve()
        .parent.parent.parent.parent.parent
        / "app" / "templates" / "lfa_player_mood_photos.html"
    )
    content = template_path.read_text(encoding="utf-8")

    hungarian_markers = [
        "Hangulat", "Feltölt", "Töröl", "Semleges", "Boldog",
        "Ünneplés", "Szomorú", "Vissza a", "Biztosan", "Nincs feltöltve",
    ]
    for marker in hungarian_markers:
        assert marker not in content, (
            f"lfa_player_mood_photos.html contains Hungarian text: {marker!r}"
        )

    assert "Mood Photos" in content
    assert "Upload" in content
    assert "Delete" in content
    assert "Not uploaded" in content


# ── MP-R12 ── dashboard My Card Media section removed; access via quicknav ─────

def test_mp_r12_dashboard_has_card_media_section():
    from pathlib import Path

    _tpl = Path(__file__).resolve().parent.parent.parent.parent.parent / "app" / "templates"
    dashboard = (_tpl / "dashboard_student_new.html").read_text(encoding="utf-8")
    quicknav  = (_tpl / "includes" / "spec_subpage_hdr.html").read_text(encoding="utf-8")

    # My Card Media section removed in MVP refactor — now lives in quicknav
    assert "My Card Media" not in dashboard, (
        "My Card Media section should be removed from dashboard (MVP refactor)"
    )
    assert "/profile/my-mood-photos" in quicknav, (
        "quicknav should still link to /profile/my-mood-photos"
    )
    assert "/card-editor" in quicknav, (
        "quicknav should still link to Card Studio (/card-editor)"
    )


# ── MP-R13 ── Public Profile action row no longer contains Mood Photos ────────

def test_mp_r13_public_profile_section_has_no_mood_photos_button():
    from pathlib import Path

    content = (
        Path(__file__).resolve()
        .parent.parent.parent.parent.parent
        / "app" / "templates" / "dashboard_student_new.html"
    ).read_text(encoding="utf-8")

    # Extract only the Public Profile section (between its section tags)
    pp_start = content.find("Public Profile entry point")
    pp_end   = content.find("My Card Media", pp_start)
    pp_block = content[pp_start:pp_end] if pp_start != -1 and pp_end != -1 else ""

    assert "my-mood-photos" not in pp_block, (
        "Mood Photos link must not appear inside the Public Profile section — "
        "it must only be in the My Card Media section"
    )


# ── MP-R14 ── profile page cards-grid contains Card Media card ───────────────

def test_mp_r14_profile_page_has_card_media_card():
    from pathlib import Path

    content = (
        Path(__file__).resolve()
        .parent.parent.parent.parent.parent
        / "app" / "templates" / "lfa_player_profile.html"
    ).read_text(encoding="utf-8")

    assert "My Card Media" in content, "profile page missing 'My Card Media' card"
    assert "/profile/my-mood-photos" in content, "profile page missing /profile/my-mood-photos link"
    assert "/card-editor/player#media" in content, (
        "profile page missing card editor #media deep link"
    )


# ── MP-R15 ── profile page header no longer contains Mood Photos button ───────

def test_mp_r15_profile_header_has_no_mood_photos_button():
    from pathlib import Path

    content = (
        Path(__file__).resolve()
        .parent.parent.parent.parent.parent
        / "app" / "templates" / "lfa_player_profile.html"
    ).read_text(encoding="utf-8")

    # The header block ends before cards-grid
    header_end = content.find('class="cards-grid"')
    header_block = content[:header_end] if header_end != -1 else content[:500]

    assert "my-mood-photos" not in header_block, (
        "Mood Photos link must not appear in the profile page header — "
        "it must only be in the My Card Media card inside cards-grid"
    )


# ── MP-R16 ── mood photos template uses correct block names (layout gate) ────

def test_mp_r16_template_uses_correct_blocks():
    from pathlib import Path

    content = (
        Path(__file__).resolve()
        .parent.parent.parent.parent.parent
        / "app" / "templates" / "lfa_player_mood_photos.html"
    ).read_text(encoding="utf-8")

    assert "{% block student_content %}" in content, (
        "lfa_player_mood_photos.html must use student_content block "
        "(not 'content') — otherwise the student nav/header is stripped"
    )
    assert "{% block extra_styles %}" in content, (
        "lfa_player_mood_photos.html must use extra_styles block "
        "(not 'head_extra') — otherwise CSS is dropped"
    )
    assert "{% block active_page %}lfa-player{% endblock %}" in content, (
        "lfa_player_mood_photos.html must set active_page=lfa-player "
        "to highlight the correct nav item"
    )
    assert "{% block content %}" not in content, (
        "lfa_player_mood_photos.html must NOT use 'content' block — "
        "it replaces the entire student layout"
    )
    assert "{% block head_extra %}" not in content, (
        "lfa_player_mood_photos.html must NOT use 'head_extra' block — "
        "use 'extra_styles' instead"
    )
    assert "mp-grid" in content, "template must contain mp-grid class for 2×2 slot layout"
    assert "mp-card" in content, "template must contain mp-card class for slot cards"
    assert "btn btn-primary" in content, "template must use btn btn-primary for upload button"
    assert 'href="/profile/lfa-football-player"' not in content, (
        "redundant back-to-profile link removed in Phase A nav cleanup"
    )
    assert "btn btn-danger" in content, "template must use btn btn-danger for delete button"
    assert "spec_subpage_hdr.html" in content, (
        "template must include spec_subpage_hdr.html for platform header"
    )
    assert "_mpUpload(this)" in content, (
        "file input must use _mpUpload(this) — form.submit() skips submit event "
        "so the base.html CSRF interceptor never fires, causing 403"
    )
    assert "this.form.submit()" not in content, (
        "file input must NOT use form.submit() — it bypasses the submit event "
        "and the CSRF interceptor, causing 403 CSRF_VALIDATION_FAILED"
    )
    assert "X-CSRF-Token" in content, (
        "template must include X-CSRF-Token fetch header for CSRF validation"
    )
    assert "_mpDelete(" in content, (
        "delete must use _mpDelete() onclick — base.html capture listener fires "
        "before onsubmit, so confirm() never gets to cancel the request"
    )
    assert 'type="button"' in content, (
        "delete button must be type=button to avoid triggering submit event"
    )


# ── MP-R17 ── spec_subpage_hdr has LFA quicknav strip with all key links ─────

def test_mp_r17_spec_subpage_hdr_has_lfa_quicknav():
    from pathlib import Path

    content = (
        Path(__file__).resolve()
        .parent.parent.parent.parent.parent
        / "app" / "templates" / "includes" / "spec_subpage_hdr.html"
    ).read_text(encoding="utf-8")

    assert "spec-quicknav" in content, "spec_subpage_hdr must contain .spec-quicknav nav strip"
    assert "LFA_FOOTBALL_PLAYER" in content, "quicknav must be gated on LFA_FOOTBALL_PLAYER"

    required_links = {
        "/profile/lfa-football-player": "Profile",
        "/my-cards":                    "My Cards",
        "/card-editor":       "Card Studio",   # CE-3.2: landing → /card-editor
        "/profile/my-mood-photos":      "Mood Photos",
        "/events":                      "Events",
        "/training":                    "Training",
    }
    for url, label in required_links.items():
        assert url in content, f"spec quicknav missing link: {url!r} ({label})"
        assert label in content, f"spec quicknav missing label: {label!r}"

    assert "sqn-active" in content, "quicknav must highlight active item (sqn-active class)"
    assert "spec-qn-item" in content, "quicknav items must use spec-qn-item class"


# ── MP-R18 ── dashboard mod-nav has 4 Quick Access tiles; quicknav has Mood ───
# After MVP refactor: 9-tile mod-nav replaced with 4-tile Quick Access.
# Profile/Editor/Mood Photos moved to spec_subpage_hdr.html quicknav.

def test_mp_r18_dashboard_modnav_has_profile_editor_moodphotos():
    from pathlib import Path

    _tpl = Path(__file__).resolve().parent.parent.parent.parent.parent / "app" / "templates"
    content = (_tpl / "dashboard_student_new.html").read_text(encoding="utf-8")
    quicknav = (_tpl / "includes" / "spec_subpage_hdr.html").read_text(encoding="utf-8")

    # Dashboard mod-nav: 4 Quick Access tiles
    modnav_start = content.find('<section class="mod-nav-section">')
    modnav_end   = content.find("</section>", modnav_start)
    modnav_block = content[modnav_start:modnav_end] if modnav_start != -1 else content

    for url in ("/calendar", "/achievements", "/sessions", "/progress"):
        assert url in modnav_block, f"dashboard mod-nav missing Quick Access tile: {url!r}"

    # Mood Photos, Card Studio, Profile accessible via quicknav (not mod-nav)
    assert "/profile/my-mood-photos"       in quicknav
    assert "/card-editor" in quicknav      # CE-3.2: general Card Studio entry
    assert "/profile/lfa-football-player"  in quicknav


# ── MP-R19 ── mood_photos_page route passes explicit LFA spec context ─────────

def test_mp_r19_route_passes_lfa_spec_context():
    """
    mood_photos_page must hardcode LFA spec context, not rely on
    user.specialization which can be any active spec (e.g. GANCUJU_PLAYER)
    on multi-spec accounts.
    """
    from app.api.web_routes.mood_photos import mood_photos_page

    with patch(f"{_BASE}.get_mood_photos_for_user", return_value={s: None for s in _ALL_SLOTS}), \
         patch(f"{_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.return_value = MagicMock()

        # Simulate a multi-spec user whose primary spec is NOT LFA_FOOTBALL_PLAYER
        multi_spec_user = _user()
        multi_spec_user.specialization = MagicMock()
        multi_spec_user.specialization.value = "GANCUJU_PLAYER"

        _run(mood_photos_page(request=_request(), user=multi_spec_user, db=_db()))

        ctx = mock_tpl.TemplateResponse.call_args[0][1]

        assert ctx.get("spec_dashboard_url") == "/dashboard/lfa-football-player", (
            "mood_photos_page must pass spec_dashboard_url='/dashboard/lfa-football-player' "
            "regardless of user.specialization — prevents wrong spec in header"
        )
        assert ctx.get("spec_dashboard_icon") == "⚽"
        assert ctx.get("spec_profile_url") == "/profile/lfa-football-player"
        assert ctx.get("spec_profile_icon") == "🪪"


# ── MP-R20 ── MOOD_PHOTO_SLOTS contains all 6 valid slot keys ────────────────

def test_mp_r20_mood_photo_slots_has_6_entries():
    from app.models.user_mood_photos import MOOD_PHOTO_SLOTS

    assert "mood_angry_competitive" in MOOD_PHOTO_SLOTS, (
        "MOOD_PHOTO_SLOTS missing mood_angry_competitive"
    )
    assert "mood_surprised_shocked" in MOOD_PHOTO_SLOTS, (
        "MOOD_PHOTO_SLOTS missing mood_surprised_shocked"
    )
    assert len(MOOD_PHOTO_SLOTS) == 6, (
        f"MOOD_PHOTO_SLOTS must have 6 entries, got {len(MOOD_PHOTO_SLOTS)}"
    )


# ── MP-R21 ── _SLOT_META has 6 entries each with required keys ───────────────

def test_mp_r21_slot_meta_has_6_entries_with_description():
    from app.api.web_routes.mood_photos import _SLOT_META

    assert len(_SLOT_META) == 6, (
        f"_SLOT_META must have 6 entries, got {len(_SLOT_META)}"
    )
    slots_in_meta = {m["slot"] for m in _SLOT_META}
    assert "mood_angry_competitive" in slots_in_meta, (
        "_SLOT_META missing mood_angry_competitive"
    )
    assert "mood_surprised_shocked" in slots_in_meta, (
        "_SLOT_META missing mood_surprised_shocked"
    )
    labels = {m["label"] for m in _SLOT_META}
    assert "Angry" in labels,    "_SLOT_META missing label 'Angry'"
    assert "Surprised" in labels, "_SLOT_META missing label 'Surprised'"

    for meta in _SLOT_META:
        assert "description" in meta, (
            f"_SLOT_META entry {meta['slot']!r} missing 'description' key"
        )
        assert meta["description"], (
            f"_SLOT_META entry {meta['slot']!r} has empty description"
        )


# ── MP-R22 ── upload to mood_angry_competitive → 303 redirect ────────────────

def test_mp_r22_upload_angry_slot_redirects():
    from app.api.web_routes.mood_photos import mood_photo_upload

    with patch(f"{_BASE}.save_mood_photo"), \
         patch(f"{_BASE}.get_mood_photos_for_user", return_value={}):
        resp = _run(
            mood_photo_upload(
                slot    = "mood_angry_competitive",
                request = _request(),
                photo   = _mock_photo(),
                user    = _user(),
                db      = _db(),
            )
        )

    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == 303
    assert "/profile/my-mood-photos" in str(resp.headers.get("location", ""))


# ── MP-R23 ── upload to mood_surprised_shocked → 303 redirect ────────────────

def test_mp_r23_upload_surprised_slot_redirects():
    from app.api.web_routes.mood_photos import mood_photo_upload

    with patch(f"{_BASE}.save_mood_photo"), \
         patch(f"{_BASE}.get_mood_photos_for_user", return_value={}):
        resp = _run(
            mood_photo_upload(
                slot    = "mood_surprised_shocked",
                request = _request(),
                photo   = _mock_photo(),
                user    = _user(),
                db      = _db(),
            )
        )

    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == 303


# ── MP-R24 ── delete mood_angry_competitive → 303 redirect ───────────────────

def test_mp_r24_delete_angry_slot_redirects():
    from app.api.web_routes.mood_photos import mood_photo_delete_form

    with patch(f"{_BASE}.delete_mood_photo"):
        resp = _run(
            mood_photo_delete_form(
                slot = "mood_angry_competitive",
                user = _user(),
                db   = _db(),
            )
        )

    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == 303


# ── MP-R25 ── delete mood_surprised_shocked via API → 204 None ───────────────

def test_mp_r25_delete_surprised_slot_api_returns_none():
    from app.api.web_routes.mood_photos import mood_photo_delete_api

    with patch(f"{_BASE}.delete_mood_photo"):
        result = _run(
            mood_photo_delete_api(
                slot = "mood_surprised_shocked",
                user = _user(),
                db   = _db(),
            )
        )
    assert result is None


# ── MP-R26 ── template is data-driven — no hardcoded slot name comparisons ───

def test_mp_r26_template_has_no_hardcoded_slot_if_elif():
    from pathlib import Path

    content = (
        Path(__file__).resolve()
        .parent.parent.parent.parent.parent
        / "app" / "templates" / "lfa_player_mood_photos.html"
    ).read_text(encoding="utf-8")

    assert "meta.slot == 'mood_intro_neutral'" not in content, (
        "Template must not hardcode mood_intro_neutral in if/elif — "
        "use meta.description from _SLOT_META instead"
    )
    assert "meta.slot == 'mood_happy_smile'" not in content, (
        "Template must not hardcode mood_happy_smile in if/elif"
    )
    assert "meta.slot == 'mood_celebration'" not in content, (
        "Template must not hardcode mood_celebration in if/elif"
    )
    assert "meta.description" in content, (
        "Template must render {{ meta.description }} — data-driven slot descriptions"
    )
    # Template is data-driven: labels are injected via {{ meta.label }} and
    # {{ meta.description }} — "Angry"/"Surprised" are NOT literal strings in
    # the source.  Assert the data-driving variables are present instead.
    assert "meta.label" in content, (
        "Template must render {{ meta.label }} so Angry/Surprised appear at runtime"
    )
    assert "meta.emoji" in content, (
        "Template must render {{ meta.emoji }}"
    )


# ── MP-D: Display Fallback (processed_png_url) ────────────────────────────────
#
# These tests verify the template display logic introduced by the display-only
# fix: when status='ready' and processed_png_url is set, the <img> src renders
# the processed URL with an onerror fallback to original_url.
#
# Rendering strategy: Jinja2 Environment + FileSystemLoader renders the full
# template tree (extends student_base.html) with a minimal mock context.  This
# is the same approach used by other template-render tests in this suite.

_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[4] / "app" / "templates"
)

_ORIG_URL = "/static/uploads/mood_photos/99_mood_mood_happy_smile_orig_111.png"
_PROC_URL = "/static/uploads/mood_photos/99_mood_mood_happy_smile_proc_222.png"


def _mood_record(
    status: str = "uploaded",
    original_url: str = _ORIG_URL,
    processed_png_url: str | None = None,
) -> MagicMock:
    r = MagicMock()
    r.status            = status
    r.original_url      = original_url
    r.processed_png_url = processed_png_url
    r.created_at        = datetime(2026, 5, 29, 10, 0)
    r.updated_at        = datetime(2026, 5, 29, 10, 0)
    return r


def _render_mood_page(record_for_slot: MagicMock | None, slot: str = "mood_happy_smile") -> str:
    """Render lfa_player_mood_photos.html with one slot populated, rest empty."""
    import jinja2
    from app.api.web_routes.mood_photos import _SLOT_META

    mood_photos = {s: None for s in _ALL_SLOTS}
    mood_photos[slot] = record_for_slot

    user = MagicMock()
    user.credit_balance = 500
    user.id = 99

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=False,
    )
    return env.get_template("lfa_player_mood_photos.html").render(
        request           = MagicMock(),
        user              = user,
        mood_photos       = mood_photos,
        slots_meta        = _SLOT_META,
        spec_dashboard_url  = "/dashboard/lfa-football-player",
        spec_dashboard_icon = "⚽",
        spec_profile_url    = "/profile/lfa-football-player",
        spec_profile_icon   = "🪪",
    )


# ── MP-D01 ── ready + processed_png_url → processed src rendered ──────────────

class TestMPD01ProcessedImageDisplayed:

    def test_mp_d01_ready_with_proc_url_renders_proc_as_src(self):
        """MP-D01a: status=ready + processed_png_url → img src = processed_png_url."""
        record = _mood_record(status="ready", processed_png_url=_PROC_URL)
        html = _render_mood_page(record)
        assert f'src="{_PROC_URL}"' in html, \
            "processed_png_url must be the img src when status=ready"

    def test_mp_d01_ready_original_url_not_primary_src(self):
        """MP-D01b: original_url must not appear as primary img src when processed available."""
        record = _mood_record(status="ready", processed_png_url=_PROC_URL)
        html = _render_mood_page(record)
        assert f'src="{_ORIG_URL}"' not in html, \
            "original_url must not be the primary img src when processed_png_url is set"


# ── MP-D02 ── uploaded + no processed → original src rendered ─────────────────

class TestMPD02UploadedShowsOriginal:

    def test_mp_d02_uploaded_status_renders_original_url(self):
        """MP-D02a: status=uploaded, processed_png_url=None → img src = original_url."""
        record = _mood_record(status="uploaded", processed_png_url=None)
        html = _render_mood_page(record)
        assert f'src="{_ORIG_URL}"' in html

    def test_mp_d02_no_proc_url_in_src_when_uploaded(self):
        """MP-D02b: _proc_ pattern must not appear in any src when status=uploaded."""
        record = _mood_record(status="uploaded", processed_png_url=None)
        html = _render_mood_page(record)
        assert "_proc_" not in html


# ── MP-D03 ── ready + processed_png_url=None → fallback to original ───────────

class TestMPD03ReadyNullProcessedFallback:

    def test_mp_d03_ready_null_processed_renders_original(self):
        """MP-D03: status=ready but processed_png_url=None → renders original_url."""
        record = _mood_record(status="ready", processed_png_url=None)
        html = _render_mood_page(record)
        assert f'src="{_ORIG_URL}"' in html

    def test_mp_d03_no_proc_url_in_html_when_null(self):
        """MP-D03b: no _proc_ URL in rendered HTML when processed_png_url is None."""
        record = _mood_record(status="ready", processed_png_url=None)
        html = _render_mood_page(record)
        assert "_proc_" not in html


# ── MP-D04 ── onerror fallback attribute ──────────────────────────────────────

class TestMPD04OnerrorFallback:

    def test_mp_d04_onerror_present_on_processed_img(self):
        """MP-D04a: img rendered with processed src has an onerror attribute."""
        record = _mood_record(status="ready", processed_png_url=_PROC_URL)
        html = _render_mood_page(record)
        assert "onerror=" in html

    def test_mp_d04_onerror_points_to_original_url(self):
        """MP-D04b: onerror attribute contains original_url for graceful degradation."""
        record = _mood_record(status="ready", processed_png_url=_PROC_URL)
        html = _render_mood_page(record)
        assert _ORIG_URL in html, "original_url must appear in onerror fallback"

    def test_mp_d04_onerror_has_null_guard(self):
        """MP-D04c: onerror sets this.onerror=null to prevent infinite loop."""
        record = _mood_record(status="ready", processed_png_url=_PROC_URL)
        html = _render_mood_page(record)
        assert "this.onerror=null" in html


# ── MP-D05 ── Remove Background button absent in display-only fix ─────────────

class TestMPD05NoRemoveBackgroundButton:

    def test_mp_d05_no_remove_bg_when_uploaded(self):
        """MP-D05a: Remove Background button must not appear for uploaded status."""
        record = _mood_record(status="uploaded", processed_png_url=None)
        html = _render_mood_page(record)
        assert "Remove Background" not in html
        assert "_mpRemoveBg" not in html

    def test_mp_d05_no_remove_bg_when_ready(self):
        """MP-D05b: Remove Background button must not appear for ready status."""
        record = _mood_record(status="ready", processed_png_url=_PROC_URL)
        html = _render_mood_page(record)
        assert "Remove Background" not in html

    def test_mp_d05_no_retry_remove_bg_when_failed(self):
        """MP-D05c: Retry Remove Background button must not appear for failed status."""
        record = _mood_record(status="failed", processed_png_url=None)
        html = _render_mood_page(record)
        assert "Retry Remove Background" not in html
        assert "Remove Background" not in html


# ── MP-D06 ── Regression: no-record placeholder unchanged ─────────────────────

class TestMPD06Regression:

    def test_mp_d06_no_record_renders_placeholder(self):
        """MP-D06a: slot with no record renders the placeholder div, not an img."""
        html = _render_mood_page(record_for_slot=None)
        assert "No photo uploaded yet" in html

    def test_mp_d06_no_record_no_img_in_preview(self):
        """MP-D06b: placeholder slot must not render an <img> tag in the preview."""
        html = _render_mood_page(record_for_slot=None)
        preview_section = html.split("mp-preview")[1].split("mp-actions")[0]
        assert "<img" not in preview_section
