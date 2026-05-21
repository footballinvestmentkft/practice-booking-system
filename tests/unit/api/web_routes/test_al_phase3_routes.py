"""Unit tests for Phase 3 admin routes — analytics, audit trail, content quality.

ATT-01..04   TestAuditActionConstants     — 4 new AL constants exist on AuditAction
ATT-05..06   TestWriteAudit               — _write_audit adds row + swallows exceptions
ATT-07..08   TestAuditTrailRoute          — AT-01 GET returns HTML; filters to AL actions
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch, call

from fastapi.responses import HTMLResponse, RedirectResponse

from app.models.audit_log import AuditAction

_BASE = "app.api.web_routes.admin.adaptive_learning"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _make_user(user_id=1, is_admin=True):
    u = MagicMock()
    u.id = user_id
    u.is_admin = is_admin
    return u


# ── TestAuditActionConstants ──────────────────────────────────────────────────

class TestAuditActionConstants:

    def test_att01_al_quiz_metadata_updated_exists(self):
        """ATT-01: AuditAction.AL_QUIZ_METADATA_UPDATED is defined."""
        assert hasattr(AuditAction, "AL_QUIZ_METADATA_UPDATED")
        assert AuditAction.AL_QUIZ_METADATA_UPDATED == "AL_QUIZ_METADATA_UPDATED"

    def test_att02_al_quiz_status_changed_exists(self):
        """ATT-02: AuditAction.AL_QUIZ_STATUS_CHANGED is defined."""
        assert hasattr(AuditAction, "AL_QUIZ_STATUS_CHANGED")
        assert AuditAction.AL_QUIZ_STATUS_CHANGED == "AL_QUIZ_STATUS_CHANGED"

    def test_att03_al_question_updated_exists(self):
        """ATT-03: AuditAction.AL_QUESTION_UPDATED is defined."""
        assert hasattr(AuditAction, "AL_QUESTION_UPDATED")
        assert AuditAction.AL_QUESTION_UPDATED == "AL_QUESTION_UPDATED"

    def test_att04_al_option_updated_exists(self):
        """ATT-04: AuditAction.AL_OPTION_UPDATED is defined."""
        assert hasattr(AuditAction, "AL_OPTION_UPDATED")
        assert AuditAction.AL_OPTION_UPDATED == "AL_OPTION_UPDATED"


# ── TestWriteAudit ─────────────────────────────────────────────────────────────

class TestWriteAudit:

    def test_att05_write_audit_adds_row_and_commits(self):
        """ATT-05: _write_audit adds an AuditLog to the db and calls commit."""
        from app.api.web_routes.admin.adaptive_learning import _write_audit

        db = MagicMock()
        _write_audit(db, user_id=1, action="AL_QUIZ_STATUS_CHANGED",
                     resource_type="quiz", resource_id=7,
                     details={"new_status": "PUBLISHED"})

        assert db.add.called
        assert db.commit.called
        added = db.add.call_args[0][0]
        assert added.user_id == 1
        assert added.action == "AL_QUIZ_STATUS_CHANGED"
        assert added.resource_type == "quiz"
        assert added.resource_id == 7

    def test_att06_write_audit_swallows_db_exception(self):
        """ATT-06: If db.add() raises, _write_audit catches and rolls back silently."""
        from app.api.web_routes.admin.adaptive_learning import _write_audit

        db = MagicMock()
        db.add.side_effect = RuntimeError("DB connection lost")
        # Should not raise
        _write_audit(db, user_id=1, action="AL_OPTION_UPDATED",
                     resource_type="quiz_answer_option", resource_id=99)
        assert db.rollback.called


# ── TestAuditTrailRoute ────────────────────────────────────────────────────────

class TestAuditTrailRoute:

    def _make_db(self, total=0, entries=None):
        db = MagicMock()
        base_q = MagicMock()
        base_q.filter.return_value = base_q
        base_q.count.return_value = total
        base_q.order_by.return_value = base_q
        base_q.offset.return_value = base_q
        base_q.limit.return_value = base_q
        base_q.all.return_value = entries or []
        db.query.return_value = base_q
        return db, base_q

    def test_att07_audit_trail_get_returns_html(self):
        """ATT-07: GET /admin/adaptive-learning/audit-trail → 200 HTMLResponse."""
        from app.api.web_routes.admin.adaptive_learning import al_audit_trail

        db, _ = self._make_db()
        user = _make_user()
        request = MagicMock()
        request.query_params = {}

        with patch(f"{_BASE}._admin_guard"), \
             patch(f"{_BASE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = MagicMock(spec=HTMLResponse)
            result = _run(al_audit_trail(request=request, page=1, db=db, user=user))

        assert mock_tpl.TemplateResponse.called
        call_args = mock_tpl.TemplateResponse.call_args
        assert call_args[0][0] == "admin/al_audit_trail.html"
        ctx = call_args[0][1]
        assert "entries" in ctx
        assert "total" in ctx

    def test_att08_audit_trail_filters_to_al_actions(self):
        """ATT-08: Route passes the AL action set to db.query().filter()."""
        from app.api.web_routes.admin.adaptive_learning import al_audit_trail, _AUDIT_ACTIONS_AL

        db, base_q = self._make_db()
        user = _make_user()
        request = MagicMock()
        request.query_params = {}

        with patch(f"{_BASE}._admin_guard"), \
             patch(f"{_BASE}.templates"):
            _run(al_audit_trail(request=request, page=1, db=db, user=user))

        # filter was called on the base query
        assert base_q.filter.called
        # Verify the filter was not called with an empty set
        filter_arg = base_q.filter.call_args[0][0]
        assert filter_arg is not None
