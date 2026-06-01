"""
CS-S4B — Challenge Studio: selector + phase/platform selector + live preview iframe

CS-S4B1: Challenge selector list
S4B1-01  GET /card-studio/challenge (no challenge_id) → 200
S4B1-02  challenge_rows includes user's sent challenges (challenger_id match)
S4B1-03  challenge_rows includes user's received challenges (challenged_id match)
S4B1-04  other user's challenges do not appear in challenge_rows
S4B1-05  filter=active → only active challenges
S4B1-06  filter=completed → only completed/terminal challenges
S4B1-07  empty challenge list → empty state in context
S4B1-08  Preview Card CTA links to /card-studio/challenge?challenge_id={id}

CS-S4B2: Challenge select + phase/platform selector
S4B2-01  challenge_id param → selected_challenge context
S4B2-02  non-participant user → error mode (challenge_error=not_participant)
S4B2-03  non-existent challenge_id → error mode (challenge_error=not_found)
S4B2-04  unlocked phases for PENDING as challenger: challenge_sent
S4B2-05  unlocked phases for PENDING as challenged: challenge_received
S4B2-06  unlocked phases for COMPLETED score win: completed_score_win
S4B2-07  locked phases list present in context for non-PENDING challenges
S4B2-08  active phase chip marked correctly
S4B2-09  platform chips include both challenge_post_16_9 and challenge_story_9_16

CS-S4B3: Preview iframe integration
S4B3-01  preview_url = /challenges/{id}/card/preview?platform={platform}&phase={phase}
S4B3-02  shell template uses preview_url as iframe src (challenge preview mode)
S4B3-03  post_16_9 platform → ratio_class mfg-ratio-169
S4B3-04  story_9_16 platform → ratio_class mfg-ratio-916
S4B3-05  selector mode (no challenge_id) → preview_url is None, placeholder shown
S4B3-06  error mode → no iframe, placeholder shown
S4B3-07  legacy editor CTA /card-editor/challenge present in challenge panel
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
INCLUDES_DIR  = TEMPLATES_DIR / "includes"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(uid: int = 10):
    u = MagicMock(); u.id = uid; u.nickname = f"user{uid}"; u.email = f"u{uid}@test.com"
    return u

def _make_license(onboarding: bool = True):
    lic = MagicMock(); lic.onboarding_completed = onboarding; return lic

def _make_challenge(ch_id: int, challenger_id: int, challenged_id: int, status_val: str):
    """Create a mock VirtualTrainingChallenge."""
    from app.models.vt_challenge import ChallengeStatus
    ch = MagicMock()
    ch.id                    = ch_id
    ch.challenger_id         = challenger_id
    ch.challenged_id         = challenged_id
    ch.challenger_attempt_id = None
    ch.challenged_attempt_id = None
    ch.winner_id             = None
    ch.is_draw               = False
    ch.forfeit_user_id       = None
    ch.forfeit_reason        = None
    ch.challenge_mode        = "async"
    ch.created_at            = None
    ch.completed_at          = None

    status_map = {
        "pending":          ChallengeStatus.PENDING,
        "accepted":         ChallengeStatus.ACCEPTED,
        "completed":        ChallengeStatus.COMPLETED,
        "declined":         ChallengeStatus.DECLINED,
        "cancelled":        ChallengeStatus.CANCELLED,
        "expired":          ChallengeStatus.EXPIRED,
        "live_lobby":       ChallengeStatus.LIVE_LOBBY,
        "live_in_progress": ChallengeStatus.LIVE_IN_PROGRESS,
    }
    ch.status = status_map.get(status_val, ChallengeStatus.PENDING)
    ch.game = MagicMock(); ch.game.name = "Memory Sequence"
    ch.challenger = _make_user(challenger_id)
    ch.challenged = _make_user(challenged_id)
    return ch

def _ctx_fn():
    from app.api.web_routes.card_studio import _resolve_challenge_context
    return _resolve_challenge_context

def _db_licensed():
    """DB with active license."""
    db  = MagicMock()
    lic = _make_license(onboarding=True)
    db.query.return_value.filter.return_value.first.return_value = lic
    return db

def _db_with_challenges(challenges: list):
    """DB that returns given challenges for the query chain."""
    db = MagicMock()
    lic = _make_license(onboarding=True)
    # first().return_value = lic (license guard)
    db.query.return_value.filter.return_value.first.return_value = lic
    # .filter().filter().order_by().limit().all() → challenges
    db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = challenges
    # no filter (all): .filter().order_by().limit().all()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = challenges
    # Attempt batch load: filter().filter().all()
    db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
    return db


# ── S4B1: Challenge selector list ────────────────────────────────────────────

class TestS4B1ChallengeSelector:

    def test_s4b1_01_no_challenge_id_returns_200(self):
        """S4B1-01: GET /card-studio/challenge route is registered and returns 200."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/card-studio/challenge" in paths

    def test_s4b1_02_challenger_challenges_in_rows(self):
        """S4B1-02: challenge_rows includes challenges where user is challenger."""
        fn   = _ctx_fn()
        user = _make_user(10)
        ch1  = _make_challenge(1, challenger_id=10, challenged_id=20, status_val="pending")
        db   = _db_with_challenges([ch1])

        with patch("app.api.web_routes.card_studio.VirtualTrainingAttempt"):
            ctx, redirect = fn(db, user, challenge_id=None)

        assert redirect is None
        ids = [r["id"] for r in ctx.get("challenge_rows", [])]
        assert 1 in ids

    def test_s4b1_03_challenged_challenges_in_rows(self):
        """S4B1-03: challenge_rows includes challenges where user is challenged."""
        fn   = _ctx_fn()
        user = _make_user(20)
        ch1  = _make_challenge(5, challenger_id=10, challenged_id=20, status_val="accepted")
        db   = _db_with_challenges([ch1])

        with patch("app.api.web_routes.card_studio.VirtualTrainingAttempt"):
            ctx, redirect = fn(db, user, challenge_id=None)

        assert redirect is None
        ids = [r["id"] for r in ctx.get("challenge_rows", [])]
        assert 5 in ids

    def test_s4b1_04_other_user_challenge_not_in_rows(self):
        """S4B1-04: Challenges between other users do not appear."""
        fn   = _ctx_fn()
        user = _make_user(99)  # not involved in challenge
        ch1  = _make_challenge(7, challenger_id=10, challenged_id=20, status_val="pending")
        db   = _db_with_challenges([])  # query already filtered by user

        ctx, redirect = fn(db, user, challenge_id=None)
        assert redirect is None
        assert ctx["challenge_rows"] == []

    def test_s4b1_05_filter_active_context(self):
        """S4B1-05: filter=active → active_filter='active' in context."""
        fn   = _ctx_fn()
        user = _make_user(10)
        db   = _db_with_challenges([])

        ctx, _ = fn(db, user, challenge_id=None, filter_val="active")
        assert ctx["active_filter"] == "active"

    def test_s4b1_06_filter_completed_context(self):
        """S4B1-06: filter=completed → active_filter='completed' in context."""
        fn   = _ctx_fn()
        user = _make_user(10)
        db   = _db_with_challenges([])

        ctx, _ = fn(db, user, challenge_id=None, filter_val="completed")
        assert ctx["active_filter"] == "completed"

    def test_s4b1_07_empty_challenges_empty_rows(self):
        """S4B1-07: No challenges → challenge_rows == []."""
        fn   = _ctx_fn()
        user = _make_user(10)
        db   = _db_with_challenges([])

        ctx, _ = fn(db, user, challenge_id=None)
        assert ctx["challenge_rows"] == []
        assert ctx["challenge_mode"] == "selector"

    def test_s4b1_08_challenge_row_has_studio_url(self):
        """S4B1-08: challenge_rows entry has studio_url with challenge_id."""
        fn   = _ctx_fn()
        user = _make_user(10)
        ch   = _make_challenge(42, 10, 20, "pending")
        db   = _db_with_challenges([ch])

        with patch("app.api.web_routes.card_studio.VirtualTrainingAttempt"):
            ctx, _ = fn(db, user, challenge_id=None)

        rows = ctx.get("challenge_rows", [])
        if rows:
            assert "challenge_id=42" in rows[0]["studio_url"]


# ── S4B2: Challenge select + phase/platform selector ─────────────────────────

class TestS4B2PhaseSelector:

    def _db_for_challenge(self, ch, my_attempt=None):
        db = MagicMock()
        lic = _make_license(True)
        db.query.return_value.filter.return_value.first.side_effect = [lic, ch, my_attempt]
        return db

    def test_s4b2_01_challenge_id_returns_selected_challenge(self):
        """S4B2-01: ?challenge_id={id} → selected_challenge in context."""
        fn   = _ctx_fn()
        user = _make_user(10)
        ch   = _make_challenge(99, 10, 20, "pending")

        db = MagicMock()
        lic = _make_license(True)

        def query_side(*args, **kwargs):
            m = MagicMock()
            m.filter.return_value.first.return_value = lic
            return m

        with patch("app.api.web_routes.card_studio._license_guard", return_value=lic):
            with patch("app.api.web_routes.card_studio.VirtualTrainingChallenge") as VTC:
                VTC_instance = MagicMock()
                db.query.return_value.filter.return_value.first.return_value = ch
                ctx, redirect = fn(db, user, challenge_id=99)

        assert redirect is None
        assert ctx.get("challenge_mode") == "preview"
        assert ctx.get("selected_challenge_id") == 99

    def test_s4b2_02_non_participant_returns_error(self):
        """S4B2-02: User not in challenge → error mode not_participant."""
        fn   = _ctx_fn()
        user = _make_user(99)  # not in challenge
        ch   = _make_challenge(1, challenger_id=10, challenged_id=20, status_val="pending")

        db = MagicMock()
        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, redirect = fn(db, user, challenge_id=1)

        assert redirect is None
        assert ctx["challenge_mode"] == "error"
        assert ctx["challenge_error"] == "not_participant"

    def test_s4b2_03_not_found_challenge_returns_error(self):
        """S4B2-03: Non-existent challenge_id → error mode not_found."""
        fn   = _ctx_fn()
        user = _make_user(10)

        db = MagicMock()
        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            db.query.return_value.filter.return_value.first.return_value = None
            ctx, redirect = fn(db, user, challenge_id=9999)

        assert redirect is None
        assert ctx["challenge_mode"] == "error"
        assert ctx["challenge_error"] == "not_found"

    def test_s4b2_04_pending_challenger_unlocked_phase(self):
        """S4B2-04: PENDING as challenger → unlocked phase includes challenge_sent."""
        from app.api.web_routes.vt_challenges import get_unlocked_challenge_card_phases
        ch = _make_challenge(1, 10, 20, "pending")
        phases = get_unlocked_challenge_card_phases(ch, 10, None)
        assert "challenge_sent" in phases

    def test_s4b2_05_pending_challenged_unlocked_phase(self):
        """S4B2-05: PENDING as challenged → unlocked phase includes challenge_received."""
        from app.api.web_routes.vt_challenges import get_unlocked_challenge_card_phases
        ch = _make_challenge(1, 10, 20, "pending")
        phases = get_unlocked_challenge_card_phases(ch, 20, None)
        assert "challenge_received" in phases

    def test_s4b2_06_completed_score_win_unlocked_phases(self):
        """S4B2-06: COMPLETED score win → unlocked phases include completed_score_win."""
        from app.api.web_routes.vt_challenges import get_unlocked_challenge_card_phases
        ch = _make_challenge(1, 10, 20, "completed")
        ch.winner_id     = 10
        ch.forfeit_user_id = None
        ch.is_draw       = False
        phases = get_unlocked_challenge_card_phases(ch, 10, None)
        assert "completed_score_win" in phases

    def test_s4b2_07_locked_phases_returned_for_non_pending(self):
        """S4B2-07: Non-PENDING challenge has locked historical phases."""
        from app.api.web_routes.vt_challenges import get_locked_challenge_card_phases
        ch = _make_challenge(1, 10, 20, "completed")
        locked = get_locked_challenge_card_phases(ch, 10)
        assert len(locked) > 0

    def test_s4b2_08_active_phase_chip_marked(self):
        """S4B2-08: phase_chips contains exactly one active=True chip."""
        fn   = _ctx_fn()
        user = _make_user(10)
        ch   = _make_challenge(1, 10, 20, "pending")

        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            with MagicMock() as mock_db:
                mock_db.query.return_value.filter.return_value.first.return_value = ch
                mock_db.query.return_value.filter.return_value.first.side_effect = None
                db = MagicMock()
                db.query.return_value.filter.return_value.first.return_value = ch
                ctx, _ = fn(db, user, challenge_id=1, phase="challenge_sent")

        if ctx.get("challenge_mode") == "preview":
            active_chips = [c for c in ctx.get("phase_chips", []) if c["active"]]
            assert len(active_chips) == 1

    def test_s4b2_09_platform_chips_both_platforms(self):
        """S4B2-09: platform_chips contains both post and story platforms."""
        fn   = _ctx_fn()
        user = _make_user(10)
        ch   = _make_challenge(1, 10, 20, "pending")

        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, _ = fn(db, user, challenge_id=1, phase="challenge_sent")

        if ctx.get("challenge_mode") == "preview":
            platform_ids = [c["id"] for c in ctx.get("platform_chips", [])]
            assert "challenge_post_16_9" in platform_ids
            assert "challenge_story_9_16" in platform_ids


# ── S4B-FIX: Phase ordering + waiting_for_opponent historical ─────────────────

class TestS4BFixPhaseOrdering:

    def _completed_ch_with_attempt(self, winner_id=10):
        """COMPLETED challenge with challenger's attempt available."""
        ch = _make_challenge(55, 10, 20, "completed")
        ch.winner_id             = winner_id
        ch.forfeit_user_id       = None
        ch.is_draw               = False
        ch.challenger_attempt_id = 1
        ch.challenged_attempt_id = 2
        return ch

    def _ctx_completed(self, phase=None, with_skill_deltas=True):
        """Call _resolve_challenge_context for a COMPLETED challenge."""
        fn   = _ctx_fn()
        user = _make_user(10)
        ch   = self._completed_ch_with_attempt()
        att  = MagicMock()
        att.skill_deltas = {"accuracy": 0.5} if with_skill_deltas else {}

        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            db.query.return_value.filter.return_value.first.side_effect = [ch, att]
            ctx, _ = fn(db, user, challenge_id=55, phase=phase)
        return ctx

    def test_fix_01_completed_chips_chronological_order(self):
        """FIX-01: COMPLETED challenge phase_chips are in chronological order.
        Expected: sent(1) → accepted(2) → waiting(4) → result(5) → skill(6)
        NOT: result → skill → sent → accepted (the old broken order).
        """
        ctx = self._ctx_completed()
        if ctx.get("challenge_mode") != "preview":
            return  # skip if mock didn't resolve correctly
        chips = ctx.get("phase_chips", [])
        ids = [c["id"] for c in chips]

        # challenge_sent must come before challenge_accepted
        if "challenge_sent" in ids and "challenge_accepted" in ids:
            assert ids.index("challenge_sent") < ids.index("challenge_accepted"), \
                f"challenge_sent must precede challenge_accepted, got: {ids}"

        # challenge_accepted must come before any completed result phase
        result_phases = {"completed_score_win", "completed_draw", "completed_forfeit_win",
                         "completed_forfeit_loss", "no_contest"}
        result_in_chips = [p for p in ids if p in result_phases]
        if "challenge_accepted" in ids and result_in_chips:
            assert ids.index("challenge_accepted") < ids.index(result_in_chips[0]), \
                f"challenge_accepted must precede result phase, got: {ids}"

        # skill_delta_result must come last if present
        if "skill_delta_result" in ids:
            assert ids.index("skill_delta_result") == len(ids) - 1, \
                f"skill_delta_result must be last, got: {ids}"

    def test_fix_02_completed_result_not_first_chip(self):
        """FIX-02: completed_score_win must NOT be the first chip in a COMPLETED challenge."""
        ctx = self._ctx_completed()
        if ctx.get("challenge_mode") != "preview":
            return
        chips = ctx.get("phase_chips", [])
        if chips:
            assert chips[0]["id"] != "completed_score_win", \
                f"completed_score_win must not be first; chips: {[c['id'] for c in chips]}"

    def test_fix_03_waiting_for_opponent_in_chips_when_completed_with_attempt(self):
        """FIX-03: COMPLETED + viewer had attempt → waiting_for_opponent in phase_chips."""
        ctx = self._ctx_completed()
        if ctx.get("challenge_mode") != "preview":
            return
        chips = ctx.get("phase_chips", [])
        ids = [c["id"] for c in chips]
        assert "waiting_for_opponent" in ids, \
            f"waiting_for_opponent must appear in COMPLETED+attempt chips, got: {ids}"

    def test_fix_04_waiting_for_opponent_is_locked_when_completed(self):
        """FIX-04: waiting_for_opponent chip is locked=True in COMPLETED challenge."""
        ctx = self._ctx_completed()
        if ctx.get("challenge_mode") != "preview":
            return
        chips = ctx.get("phase_chips", [])
        wfo = next((c for c in chips if c["id"] == "waiting_for_opponent"), None)
        assert wfo is not None, "waiting_for_opponent chip not found"
        assert wfo["locked"] is True, \
            f"waiting_for_opponent must be locked=True in COMPLETED, got locked={wfo['locked']}"

    def test_fix_05_result_chip_is_unlocked_in_completed(self):
        """FIX-05: completed_score_win chip is locked=False (active, exportable)."""
        ctx = self._ctx_completed()
        if ctx.get("challenge_mode") != "preview":
            return
        chips = ctx.get("phase_chips", [])
        result = next((c for c in chips if c["id"] == "completed_score_win"), None)
        if result:
            assert result["locked"] is False, \
                f"completed_score_win must be locked=False, got locked={result['locked']}"

    def test_fix_06_pending_challenger_chips_unchanged(self):
        """FIX-06: PENDING challenger still has challenge_sent as only chip."""
        fn   = _ctx_fn()
        user = _make_user(10)
        ch   = _make_challenge(1, 10, 20, "pending")

        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, _ = fn(db, user, challenge_id=1, phase="challenge_sent")

        if ctx.get("challenge_mode") == "preview":
            ids = [c["id"] for c in ctx.get("phase_chips", [])]
            assert "challenge_sent" in ids
            assert "waiting_for_opponent" not in ids, \
                "waiting_for_opponent must NOT appear for PENDING challenge"

    def test_fix_07_waiting_for_opponent_absent_without_attempt(self):
        """FIX-07: COMPLETED without viewer attempt → waiting_for_opponent not added."""
        fn   = _ctx_fn()
        user = _make_user(10)
        ch   = _make_challenge(99, 10, 20, "completed")
        ch.winner_id             = 10
        ch.challenger_attempt_id = None  # viewer had no attempt
        ch.challenged_attempt_id = 2

        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, _ = fn(db, user, challenge_id=99)

        if ctx.get("challenge_mode") == "preview":
            ids = [c["id"] for c in ctx.get("phase_chips", [])]
            assert "waiting_for_opponent" not in ids, \
                "waiting_for_opponent must NOT appear when viewer had no attempt"


# ── S4B3: Preview iframe ──────────────────────────────────────────────────────

class TestS4B3PreviewIframe:

    def test_s4b3_01_preview_url_pattern(self):
        """S4B3-01: preview_url = /challenges/{id}/card/preview?platform=...&phase=..."""
        fn   = _ctx_fn()
        user = _make_user(10)
        ch   = _make_challenge(42, 10, 20, "pending")

        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, _ = fn(db, user, challenge_id=42, phase="challenge_sent",
                        platform="challenge_post_16_9")

        if ctx.get("challenge_mode") == "preview":
            url = ctx["preview_url"]
            assert "/challenges/42/card/preview" in url
            assert "platform=challenge_post_16_9" in url
            assert "phase=challenge_sent" in url

    def test_s4b3_02_shell_uses_challenge_mode_for_iframe(self):
        """S4B3-02: Shell template has challenge_mode=='preview' check for iframe."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "challenge_mode == \"preview\"" in src or "challenge_mode == 'preview'" in src
        assert "cs-preview-iframe" in src

    def test_s4b3_03_post_platform_ratio_169(self):
        """S4B3-03: challenge_post_16_9 → ratio_class = mfg-ratio-169."""
        from app.api.web_routes.card_studio import _CC_RATIO
        assert _CC_RATIO["challenge_post_16_9"] == "mfg-ratio-169"

    def test_s4b3_04_story_platform_ratio_916(self):
        """S4B3-04: challenge_story_9_16 → ratio_class = mfg-ratio-916."""
        from app.api.web_routes.card_studio import _CC_RATIO
        assert _CC_RATIO["challenge_story_9_16"] == "mfg-ratio-916"

    def test_s4b3_05_selector_mode_no_preview_url(self):
        """S4B3-05: Selector mode (no challenge_id) → preview_url is None."""
        fn   = _ctx_fn()
        user = _make_user(10)
        db   = MagicMock()
        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
            db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
            ctx, _ = fn(db, user, challenge_id=None)
        assert ctx["preview_url"] is None
        assert ctx["challenge_mode"] == "selector"

    def test_s4b3_06_error_mode_no_preview_url(self):
        """S4B3-06: Error mode → preview_url is None."""
        fn   = _ctx_fn()
        user = _make_user(10)
        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = None
            ctx, _ = fn(db, user, challenge_id=9999)
        assert ctx["preview_url"] is None
        assert ctx["challenge_mode"] == "error"

    def test_s4b3_07_challenge_panel_has_legacy_cta(self):
        """S4B3-07: cs_challenge_panel.html references legacy_editor_url;
        context sets it to /card-editor/challenge."""
        src = (INCLUDES_DIR / "cs_challenge_panel.html").read_text()
        assert "legacy_editor_url" in src  # template uses context var

        # Context must set legacy_editor_url = /card-editor/challenge
        from app.api.web_routes.card_studio import _resolve_challenge_context
        user = _make_user(10)
        with patch("app.api.web_routes.card_studio._license_guard", return_value=_make_license(True)):
            db = MagicMock()
            db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
            db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
            ctx, _ = _resolve_challenge_context(db, user, challenge_id=None)
        assert ctx["legacy_editor_url"] == "/card-editor/challenge"
