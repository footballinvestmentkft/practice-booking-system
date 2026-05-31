"""Backend publish/export guard tests — PG-01..PG-06.

PG-01  publish-card 403 when draft.draft_variant not owned
PG-02  publish-card 200 when draft.draft_variant is owned
PG-03  publish_draft() NOT called when variant unowned
PG-04  publish-card 404 when no LFA license
PG-05  FClassic Player credit_cost > 0 in DESIGNS fallback dict
PG-06  shop _state() returns 'not_available' for credit_cost=0 designs
"""
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

_DASH_BASE = "app.api.web_routes.dashboard"
_CDS_PATH  = f"{_DASH_BASE}._CardDraftService"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(uid: int = 42) -> MagicMock:
    u = MagicMock(); u.id = uid; u.credit_balance = 0
    return u


def _draft(variant: str = "fclassic") -> MagicMock:
    d = MagicMock()
    d.draft_variant      = variant
    d.published_theme    = "default"
    d.published_variant  = variant
    d.published_platform = None
    return d


def _invoke_publish(user: MagicMock, draft: MagicMock, is_owned: bool):
    """Call student_publish_card, return (response_mock, publish_draft_call_count)."""
    from app.api.web_routes.dashboard import student_publish_card

    db = MagicMock()
    # license check
    db.query.return_value.filter.return_value.first.return_value = MagicMock()

    publish_draft_calls = []

    def _fake_publish(db_, d_):
        publish_draft_calls.append(d_)
        d_.published_variant = d_.draft_variant

    with patch(_CDS_PATH) as MockCDS, \
         patch("app.services.card_design_service.is_design_accessible", return_value=is_owned):
        MockCDS.get_player_card_draft.return_value = draft
        MockCDS.publish_draft.side_effect = _fake_publish
        resp = asyncio.run(student_publish_card(db=db, user=user))

    return resp, len(publish_draft_calls)


# ── PG-01..PG-04: publish-card endpoint ──────────────────────────────────────

class TestPublishGuard:

    def test_pg01_publish_403_unowned_variant(self):
        """PG-01: publish-card returns 403 when draft variant is not owned."""
        resp, _ = _invoke_publish(_user(), _draft("showcase"), is_owned=False)
        assert resp.status_code == 403

    def test_pg02_publish_200_owned_variant(self):
        """PG-02: publish-card returns 200 when draft variant is owned."""
        resp, _ = _invoke_publish(_user(), _draft("fclassic"), is_owned=True)
        assert resp.status_code == 200

    def test_pg03_publish_draft_not_called_when_unowned(self):
        """PG-03: publish_draft() is never called when the variant is not owned."""
        _, call_count = _invoke_publish(_user(), _draft("showcase"), is_owned=False)
        assert call_count == 0, "publish_draft must not be called for unowned variant"

    def test_pg04_publish_404_no_license(self):
        """PG-04: publish-card returns 404 when user has no LFA license."""
        from app.api.web_routes.dashboard import student_publish_card

        db   = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None  # no license

        with patch(_CDS_PATH):
            resp = asyncio.run(student_publish_card(db=db, user=_user()))
        assert resp.status_code == 404


# ── PG-05..PG-06: pricing / 0 CR guard ───────────────────────────────────────

class TestPricingGuard:

    def test_pg05_fifa_classic_not_free(self):
        """PG-05: FClassic Player credit_cost > 0 in DESIGNS fallback dict."""
        from app.services.card_design_service import DESIGNS
        fclassic = DESIGNS.get("fclassic")
        assert fclassic is not None, "DESIGNS must contain 'fclassic' (legacy alias key)"
        assert fclassic.credit_cost > 0, (
            f"FClassic Player must not be free (credit_cost={fclassic.credit_cost}); "
            "no 0-CR purchasable designs allowed"
        )
        assert fclassic.is_premium is True, "FClassic Player must be is_premium=True"

    def test_pg06_zero_credit_cost_yields_not_available(self):
        """PG-06: shop route assigns 'not_available' to credit_cost=0 unowned designs."""
        import asyncio
        from unittest.mock import MagicMock, patch

        # Build a fake design with credit_cost=0 (simulates old DB row for FClassic Player)
        zero_design = MagicMock()
        zero_design.id          = "hypothetical_zero"
        zero_design.label       = "Zero"
        zero_design.description = "Should never be purchasable"
        zero_design.credit_cost = 0
        zero_design.is_premium  = False

        user      = MagicMock(); user.id = 1; user.credit_balance = 9999
        db        = MagicMock()
        request   = MagicMock(); request.query_params.get.return_value = None

        captured = {}

        def _fake_tmpl(tmpl, ctx, **kw):
            captured["context"] = ctx
            return MagicMock(status_code=200)

        _BASE = "app.api.web_routes.shop"
        with patch(f"{_BASE}.templates") as mock_tpl, \
             patch(f"{_BASE}.get_all_designs", return_value=[zero_design]), \
             patch(f"{_BASE}.is_design_accessible", return_value=False):
            mock_tpl.TemplateResponse.side_effect = _fake_tmpl
            from app.api.web_routes.shop import shop_player_card
            asyncio.run(shop_player_card(request=request, db=db, user=user))

        rows  = captured["context"]["design_rows"]
        row   = next(r for r in rows if r["id"] == "hypothetical_zero")
        assert row["state"] == "not_available", (
            f"0-CR unowned design must yield 'not_available', got {row['state']!r}"
        )
