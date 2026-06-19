"""
CE-2 tests — Card Editor owned-only filtering + no purchase affordance.

CE2-01  card_variants context contains only owned variants (unlocked=True)
CE2-02  locked (not-owned) variant never appears in card_variants context
CE2-03  card_themes context contains only free + owned premium themes
CE2-04  locked premium theme never appears in card_themes context
CE2-05  response body does NOT contain "Unlock for"
CE2-06  response body does NOT contain "Get Card" or "Get Player Card"
CE2-07  response body does NOT contain credit-price locked string
CE2-08  response body does NOT contain unlockVariant JS function
CE2-09  response body does NOT contain unlockTheme JS function
CE2-10  stale draft_variant (unowned) → render-time fallback to first owned variant
CE2-11  zero owned variants → card_variants is empty list in context
CE2-12  empty state block contains Browse Player Card designs shop link
CE2-13  /dashboard/unlock-variant endpoint still registered (backward compat)
CE2-14  /dashboard/unlock-theme endpoint still registered (backward compat)
CE2-15  publish/export CDO guard unchanged — active_variant_owned=False disables buttons
CE2-16  Welcome editor unowned → 303 redirect unchanged
CE2-17  route count unchanged — no new routes added
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_DASH_BASE  = "app.api.web_routes.dashboard"
_IS_DA_PATH = "app.services.card_design_service.is_design_accessible"

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    return MagicMock()


def _student(uid: int = 42):
    u = MagicMock()
    u.id = uid
    u.credit_balance = 100
    return u


def _license(uid: int = 42):
    lic = MagicMock()
    lic.user_id = uid
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.onboarding_completed = True
    lic.football_skills = {"passing": 60}
    lic.unlocked_card_variants = []
    lic.unlocked_card_themes   = []
    return lic


def _draft(variant: str = "fclassic", theme: str = "default") -> MagicMock:
    d = MagicMock()
    d.draft_theme    = theme
    d.draft_variant  = variant
    d.draft_platform = None
    d.draft_data     = {}
    d.published_theme    = theme
    d.published_variant  = variant
    d.published_platform = None
    d.published_data     = {}
    return d


def _variant(vid: str, label: str = "", is_premium: bool = True, credit_cost: int = 300) -> MagicMock:
    v = MagicMock()
    v.id          = vid
    v.label       = label or vid
    v.description = f"{vid} description"
    v.is_premium  = is_premium
    v.credit_cost = credit_cost
    v.available   = True
    return v


def _theme_def(tid: str, is_premium: bool = False, credit_cost: int = 0, dot_color: str = "#667eea") -> MagicMock:
    t = MagicMock()
    t.id          = tid
    t.label       = tid
    t.dot_color   = dot_color
    t.is_premium  = is_premium
    t.credit_cost = credit_cost
    return t


def _invoke_editor(
    draft: MagicMock,
    owned_variant_ids: list[str],
    owned_color_ids: set[str] | None = None,
    all_variants: list[MagicMock] | None = None,
    all_themes: list[MagicMock] | None = None,
) -> dict:
    """Invoke lfa_player_card_editor and return the captured template context."""
    if owned_color_ids is None:
        owned_color_ids = set()

    from app.api.web_routes.dashboard import lfa_player_card_editor

    user    = _student()
    license = _license(uid=user.id)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [license, None]

    if all_variants is None:
        all_variants = [_variant("fclassic"), _variant("compact"), _variant("showcase")]
    if all_themes is None:
        all_themes = [
            _theme_def("default", is_premium=False),
            _theme_def("midnight", is_premium=False),
            _theme_def("gold", is_premium=True, credit_cost=500),
        ]

    captured: dict = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    def _is_da_side_effect(db_, user_id, card_type_id, design_id):
        return design_id in owned_variant_ids

    with patch(f"{_DASH_BASE}._CardDraftService") as MockCDS, \
         patch(f"{_DASH_BASE}.templates") as mock_tpl, \
         patch(f"{_DASH_BASE}.SemesterEnrollment"), \
         patch(_IS_DA_PATH, side_effect=_is_da_side_effect), \
         patch("app.services.card_variant_service.get_all_variants", return_value=all_variants), \
         patch("app.services.card_color_service.get_colors_for_family", return_value=all_themes), \
         patch("app.services.card_color_service.get_owned_color_ids", return_value=owned_color_ids), \
         patch("app.services.card_platform_service.build_platform_list", return_value=[]), \
         patch("app.services.card_constants.ANIMATED_EXPORT_CAPABLE", []), \
         patch("app.services.card_constants.CANVAS_SIZES", {}), \
         patch("app.services.card_constants.CARD_EDITOR_PLATFORM_IDS", []), \
         patch("app.services.highlight_video_service.build_youtube_embed_url", return_value=None):
        MockCDS.get_player_card_draft.return_value = draft
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl
        try:
            _run(lfa_player_card_editor(_req(), db=db, user=user))
        except Exception:
            pass

    return captured.get("context", {})



def _html_from_template() -> str:
    """Effective editor source: main template + all Jinja2 includes expanded."""
    _inc = TEMPLATES_DIR / "includes" / "player_editor"
    return "\n".join([
        (TEMPLATES_DIR / "dashboard_card_editor.html").read_text(encoding="utf-8"),
        (_inc / "styles.html").read_text(encoding="utf-8"),                # REF-P1
        (_inc / "preview_panel.html").read_text(encoding="utf-8"),         # REF-P3
        (_inc / "design_panel.html").read_text(encoding="utf-8"),          # REF-P4
        (_inc / "platform_panel.html").read_text(encoding="utf-8"),        # REF-P4
        (_inc / "photo_panel.html").read_text(encoding="utf-8"),           # REF-P5a
        (_inc / "highlight_video_panel.html").read_text(encoding="utf-8"), # REF-P5b
        (_inc / "scripts.html").read_text(encoding="utf-8"),               # REF-P2
    ])


# ── CE2-01 — card_variants context contains only owned variants ───────────────

class TestCE201OwnedOnlyVariants:
    def test_owned_variants_in_context(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=["fclassic"],
        )
        assert ctx, "context not captured"
        variants = ctx.get("card_variants", [])
        assert all(v["unlocked"] for v in variants), \
            "card_variants must contain only unlocked (owned) items"

    def test_all_returned_variants_are_owned(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=["fclassic"],
        )
        ids = [v["id"] for v in ctx.get("card_variants", [])]
        assert "fclassic" in ids
        assert "compact" not in ids
        assert "showcase" not in ids


# ── CE2-02 — locked variant not in card_variants context ─────────────────────

class TestCE202LockedVariantAbsent:
    def test_unowned_variant_not_in_context(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=["fclassic"],
        )
        unowned_ids = [v["id"] for v in ctx.get("card_variants", []) if not v["unlocked"]]
        assert unowned_ids == [], \
            f"Unowned variants leaked into context: {unowned_ids}"

    def test_zero_owned_gives_empty_list(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=[],
        )
        assert ctx.get("card_variants") == []


# ── CE2-03 — card_themes context contains only free + owned premium themes ────

class TestCE203OwnedThemes:
    def test_free_themes_always_in_context(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=["fclassic"],
            owned_color_ids=set(),
        )
        theme_ids = [t["id"] for t in ctx.get("card_themes", [])]
        assert "default"  in theme_ids, "free theme 'default' must always appear"
        assert "midnight" in theme_ids, "free theme 'midnight' must always appear"

    def test_unowned_premium_theme_absent(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=["fclassic"],
            owned_color_ids=set(),
        )
        theme_ids = [t["id"] for t in ctx.get("card_themes", [])]
        assert "gold" not in theme_ids, "locked premium theme 'gold' must not appear"

    def test_owned_premium_theme_present(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=["fclassic"],
            owned_color_ids={"gold"},
        )
        theme_ids = [t["id"] for t in ctx.get("card_themes", [])]
        assert "gold" in theme_ids, "owned premium theme 'gold' must appear"


# ── CE2-04 — locked premium theme not in card_themes context ─────────────────

class TestCE204LockedThemeAbsent:
    def test_unlocked_only_in_themes(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=["fclassic"],
            owned_color_ids=set(),
        )
        assert all(t["unlocked"] for t in ctx.get("card_themes", [])), \
            "card_themes must contain only unlocked themes"


# ── CE2-05..CE2-09 — template source has no purchase affordance ───────────────

class TestCE205to209NoPurchaseInTemplate:
    def _src(self) -> str:
        return _html_from_template()

    def test_ce2_05_no_unlock_for_text(self):
        assert "Unlock for" not in self._src()

    def test_ce2_06_no_get_card_cta(self):
        src = self._src()
        assert "Get Card" not in src
        assert "Get Player Card" not in src

    def test_ce2_07_no_credit_price_locked_text(self):
        src = self._src()
        assert "var-cost" not in src
        assert "ce-design-tile-lock" not in src
        assert "credit_cost" not in src

    def test_ce2_08_no_unlock_variant_js(self):
        assert "unlockVariant" not in self._src()

    def test_ce2_09_no_unlock_theme_js(self):
        assert "unlockTheme" not in self._src()


# ── CE2-10 — stale draft → render-time fallback to first owned ────────────────

class TestCE210DraftFallback:
    def test_stale_draft_variant_replaced_by_first_owned(self):
        """draft_variant='showcase' but only 'fclassic' owned → active_card_variant='fclassic'."""
        ctx = _invoke_editor(
            draft=_draft("showcase"),   # stale — not owned
            owned_variant_ids=["fclassic"],
        )
        assert ctx.get("active_card_variant") == "fclassic", \
            "render-time fallback must set active_card_variant to first owned design"

    def test_stale_draft_fallback_sets_owned_true(self):
        ctx = _invoke_editor(
            draft=_draft("showcase"),
            owned_variant_ids=["fclassic"],
        )
        assert ctx.get("active_variant_owned") is True

    def test_owned_draft_variant_unchanged(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=["fclassic"],
        )
        assert ctx.get("active_card_variant") == "fclassic"


# ── CE2-11 — zero owned → empty list ─────────────────────────────────────────

class TestCE211ZeroOwned:
    def test_zero_owned_card_variants_empty(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=[],
        )
        assert ctx.get("card_variants") == []

    def test_zero_owned_active_variant_owned_false(self):
        ctx = _invoke_editor(
            draft=_draft("fclassic"),
            owned_variant_ids=[],
        )
        assert ctx.get("active_variant_owned") is False


# ── CE2-12 — empty state block in template ───────────────────────────────────

class TestCE212EmptyState:
    def _src(self) -> str:
        return _html_from_template()

    def test_empty_state_div_present(self):
        assert "ce-empty-state" in self._src()

    def test_empty_state_links_to_shop(self):
        """SHOP-2: empty state links to unified shop (player type filter)."""
        assert 'href="/shop?type=player_card"' in self._src()

    def test_empty_state_uses_neutral_language(self):
        src = self._src()
        assert "Browse Player Card designs" in src

    def test_empty_state_no_buy_or_unlock_language(self):
        # The empty state must not say "Buy", "Unlock", or "Get"
        import re
        # Find the ce-empty-state block
        m = re.search(r'ce-empty-state.*?</div>', src := self._src(), re.DOTALL)
        if m:
            block = m.group(0)
            assert "Buy" not in block
            assert "Unlock" not in block


# ── CE2-13 / CE2-14 — backend endpoints still registered ─────────────────────

class TestCE213_14EndpointsRegistered:
    def _route_paths(self) -> list[str]:
        from app.main import app
        return [r.path for r in app.routes]

    def test_ce2_13_unlock_variant_endpoint_exists(self):
        assert "/dashboard/unlock-variant" in self._route_paths(), \
            "/dashboard/unlock-variant must remain registered for backward compat"

    def test_ce2_14_unlock_theme_endpoint_exists(self):
        assert "/dashboard/unlock-theme" in self._route_paths(), \
            "/dashboard/unlock-theme must remain registered for backward compat"


# ── CE2-15 — publish/export CDO guard unchanged ───────────────────────────────

class TestCE215PublishExportGuard:
    def _src(self) -> str:
        return _html_from_template()

    def test_publish_button_disabled_when_not_owned(self):
        src = self._src()
        assert '{% if not active_variant_owned %}disabled{% endif %}' in src or \
               'not active_variant_owned' in src, \
               "publish button must remain guarded by active_variant_owned"

    def test_export_button_disabled_when_not_owned(self):
        src = self._src()
        assert 'not active_variant_owned' in src

    def test_active_variant_owned_js_var_present(self):
        src = self._src()
        assert "_activeVariantOwned" in src


# ── CE2-16 — Welcome editor unowned → 303 unchanged ──────────────────────────

class TestCE216WelcomeEditorUnchanged:
    def test_welcome_editor_redirect_unowned(self):
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import welcome_card_editor

        user = MagicMock()
        user.id = 42
        from app.models.user import UserRole
        user.role = UserRole.STUDENT

        license = MagicMock()
        license.onboarding_completed = True

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = license

        with patch("app.api.web_routes.card_editor.is_design_accessible", return_value=False):
            result = _run(welcome_card_editor(
                format_id="instagram_portrait",
                request=MagicMock(),
                db=db,
                user=user,
            ))

        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303


# ── CE2-17 — route count unchanged ────────────────────────────────────────────

class TestCE217RouteCount:
    def test_openapi_snapshot_route_count_unchanged(self):
        """Route count history:
        901 → 903: AN-3B2B ball detection (user ball-detection + admin trigger)
        903 → 905: AN-3B2D-1 ball trajectory (GET /ball-trajectory + POST /manual-seed)
        905 → 907: AN-3B2D-B0 ball feedback (POST /ball-feedback + GET /ball-feedback/queue)
        907 → 910: AN-3B2B2 admin feedback review (GET review-queue + PATCH review + GET training-export)
        910 → 912: AN-3B2F PR-1A ball training hub (GET /ball-training/queue + POST /ball-training/feedback)
        """
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 912, (
            f"Expected 912 routes (910 prior + 2 ball training hub), got {len(paths)}."
        )
