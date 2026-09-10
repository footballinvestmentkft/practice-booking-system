"""
Unit tests for app/core/init_admin.py.

Targets 56% branch/stmt coverage gap.
All 4 branches: admin-exists early return, new admin created,
exception rollback, finally close.
"""

import pytest
from unittest.mock import MagicMock, patch, call


_PATCH_SL = "app.core.init_admin.SessionLocal"
_PATCH_SETTINGS = "app.core.init_admin.settings"
_PATCH_HASH = "app.core.init_admin.get_password_hash"
_PATCH_USER = "app.core.init_admin.User"


class TestCreateInitialAdmin:

    def _make_db(self) -> MagicMock:
        """Return a minimal SessionLocal() mock with chained query support."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        return db

    def _make_settings(self) -> MagicMock:
        s = MagicMock()
        s.ADMIN_EMAIL = "admin@test.lfa"
        s.ADMIN_NAME = "Test Admin"
        s.ADMIN_PASSWORD = "testpass123"
        return s

    # ------------------------------------------------------------------
    # Branch 1: admin already exists → early return, no add/commit
    # ------------------------------------------------------------------

    def test_admin_already_exists_early_return(self):
        existing_admin = MagicMock()
        db = self._make_db()
        db.query.return_value.filter.return_value.first.return_value = existing_admin

        with patch(_PATCH_SL, return_value=db), \
             patch(_PATCH_SETTINGS, self._make_settings()), \
             patch(_PATCH_HASH, return_value="hashed"):
            from app.core.init_admin import create_initial_admin
            create_initial_admin()

        db.add.assert_not_called()
        db.commit.assert_not_called()
        db.close.assert_called_once()

    # ------------------------------------------------------------------
    # Branch 2: admin does not exist → creates, commits, refreshes
    # ------------------------------------------------------------------

    def test_admin_created_successfully(self):
        db = self._make_db()  # first.return_value = None → no existing admin

        with patch(_PATCH_SL, return_value=db), \
             patch(_PATCH_SETTINGS, self._make_settings()), \
             patch(_PATCH_HASH, return_value="hashed"), \
             patch(_PATCH_USER) as MockUser:
            mock_admin = MagicMock()
            MockUser.return_value = mock_admin

            from app.core.init_admin import create_initial_admin
            create_initial_admin()

        db.add.assert_called_once_with(mock_admin)
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(mock_admin)
        db.close.assert_called_once()

    def test_admin_password_is_never_written_to_logs_or_console(self, caplog, capsys):
        """A configured bootstrap credential must never appear in output."""
        db = self._make_db()
        settings = self._make_settings()
        settings.ADMIN_PASSWORD = "Bearer-like-admin-password-DO-NOT-LOG"

        with patch(_PATCH_SL, return_value=db), \
             patch(_PATCH_SETTINGS, settings), \
             patch(_PATCH_HASH, return_value="hashed-admin-password-DO-NOT-LOG"), \
             patch(_PATCH_USER):
            from app.core.init_admin import create_initial_admin
            create_initial_admin()

        captured = capsys.readouterr()
        emitted = "\n".join((captured.out, captured.err, caplog.text))
        assert settings.ADMIN_PASSWORD not in emitted
        assert "hashed-admin-password-DO-NOT-LOG" not in emitted

    # ------------------------------------------------------------------
    # Branch 3: exception during creation → rollback called
    # ------------------------------------------------------------------

    def test_admin_creation_exception_triggers_rollback(self):
        db = self._make_db()
        db.add.side_effect = RuntimeError("DB error")

        with patch(_PATCH_SL, return_value=db), \
             patch(_PATCH_SETTINGS, self._make_settings()), \
             patch(_PATCH_HASH, return_value="hashed"), \
             patch(_PATCH_USER):
            from app.core.init_admin import create_initial_admin
            # Should NOT raise — exception is caught internally
            create_initial_admin()

        db.rollback.assert_called_once()
        db.close.assert_called_once()

    # ------------------------------------------------------------------
    # Branch 4: finally block always closes DB
    # ------------------------------------------------------------------

    def test_db_close_called_even_after_exception(self):
        """Verify the finally block runs regardless of exception."""
        db = self._make_db()
        db.commit.side_effect = Exception("commit failed")

        with patch(_PATCH_SL, return_value=db), \
             patch(_PATCH_SETTINGS, self._make_settings()), \
             patch(_PATCH_HASH, return_value="hashed"), \
             patch(_PATCH_USER):
            from app.core.init_admin import create_initial_admin
            create_initial_admin()

        db.close.assert_called_once()
        db.rollback.assert_called_once()
