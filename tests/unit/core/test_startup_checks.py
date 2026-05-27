"""Unit tests for app/core/startup_checks.py

SC-01  WARNING logged when virtual_training_games is empty
SC-02  WARNING logged when no challenge-compatible games are active
SC-03  No WARNING when memory_sequence + target_tracking are both active
SC-04  Function does not raise even when db.query raises an exception
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch


_MODULE = "app.core.startup_checks"


def _make_query_mock(total: int, compat: int):
    """Return a mock db where .count() returns the given values in sequence."""
    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        q.count.return_value = total
        filtered = MagicMock()
        filtered.count.return_value = compat
        q.filter.return_value = filtered
        return q

    db.query.side_effect = query_side_effect
    return db


class TestStartupChecks:

    def test_sc01_warns_when_vt_empty(self, caplog):
        """SC-01: WARNING when virtual_training_games count == 0."""
        from app.core.startup_checks import check_reference_data_integrity

        db = _make_query_mock(total=0, compat=0)

        with caplog.at_level(logging.WARNING, logger=_MODULE):
            check_reference_data_integrity(db)

        assert any("virtual_training_games is empty" in r.message for r in caplog.records)

    def test_sc02_warns_when_no_challenge_compat(self, caplog):
        """SC-02: WARNING when total > 0 but no challenge-compatible games active."""
        from app.core.startup_checks import check_reference_data_integrity

        db = _make_query_mock(total=3, compat=0)

        with caplog.at_level(logging.WARNING, logger=_MODULE):
            check_reference_data_integrity(db)

        assert any("challenge-compatible" in r.message for r in caplog.records)

    def test_sc03_silent_when_all_ok(self, caplog):
        """SC-03: No WARNING when both challenge-compatible games are active."""
        from app.core.startup_checks import check_reference_data_integrity

        db = _make_query_mock(total=6, compat=2)

        with caplog.at_level(logging.WARNING, logger=_MODULE):
            check_reference_data_integrity(db)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 0

    def test_sc04_does_not_raise_on_db_exception(self, caplog):
        """SC-04: Exception from db.query() is caught — function returns without raising."""
        from app.core.startup_checks import check_reference_data_integrity

        db = MagicMock()
        db.query.side_effect = Exception("DB connection error")

        # Must not raise
        with caplog.at_level(logging.WARNING, logger=_MODULE):
            check_reference_data_integrity(db)

        # Should log a warning about the failure
        assert any("check failed" in r.message.lower() or r.levelno >= logging.WARNING
                   for r in caplog.records)
