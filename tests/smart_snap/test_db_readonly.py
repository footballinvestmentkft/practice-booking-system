"""
SS-DBRW: DB read-only enforcement tests.

Verifies that the POC-1 audit script queries are read-only (SELECT only)
and that benchmark/report scripts do not interact with the database at all.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

POC1_DIR = Path(__file__).resolve().parents[2] / "scripts/smart_snap_poc1"

WRITE_PATTERN = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE TABLE|REPLACE|MERGE|UPSERT|GRANT|REVOKE)\b',
    re.IGNORECASE,
)


class TestDBReadOnly:
    def test_SS_DBRW_01_audit_script_exists(self):
        """SS-DBRW-01: 00_audit_eligible_frames.py exists."""
        assert (POC1_DIR / "00_audit_eligible_frames.py").is_file()

    def test_SS_DBRW_02_audit_sql_contains_only_select(self):
        """SS-DBRW-02: SQL embedded in audit script has no write keywords."""
        source = (POC1_DIR / "00_audit_eligible_frames.py").read_text(encoding="utf-8")
        # Extract text between triple-quotes (SQL blocks)
        sql_blocks = re.findall(r'"""(.*?)"""', source, re.DOTALL)
        for block in sql_blocks:
            if "SELECT" in block or "FROM" in block:
                match = WRITE_PATTERN.search(block)
                assert match is None, f"Write keyword in SQL block: '{match.group()}'\nBlock: {block[:200]}"

    def test_SS_DBRW_03_audit_queries_from_feedback_table(self):
        """SS-DBRW-03: Audit queries juggling_ball_feedback (source inspection)."""
        source = (POC1_DIR / "00_audit_eligible_frames.py").read_text(encoding="utf-8")
        assert "juggling_ball_feedback" in source

    def test_SS_DBRW_04_audit_queries_trajectory_table(self):
        """SS-DBRW-04: Audit queries juggling_ball_trajectories."""
        source = (POC1_DIR / "00_audit_eligible_frames.py").read_text(encoding="utf-8")
        assert "juggling_ball_trajectories" in source

    def test_SS_DBRW_05_audit_closes_connection_in_finally(self):
        """SS-DBRW-05: DB connection is closed in a finally block."""
        source = (POC1_DIR / "00_audit_eligible_frames.py").read_text(encoding="utf-8")
        assert "finally:" in source
        assert "conn.close()" in source

    def test_SS_DBRW_06_audit_no_commit(self):
        """SS-DBRW-06: Audit script has no .commit() calls."""
        source = (POC1_DIR / "00_audit_eligible_frames.py").read_text(encoding="utf-8")
        assert ".commit()" not in source

    def test_SS_DBRW_07_benchmark_no_db_imports(self):
        """SS-DBRW-07: 03_benchmark.py does not import DB modules."""
        source = (POC1_DIR / "03_benchmark.py").read_text(encoding="utf-8")
        assert "psycopg2" not in source
        assert "juggling_ball_feedback" not in source
        assert "juggling_ball_trajectories" not in source

    def test_SS_DBRW_08_report_no_db_imports(self):
        """SS-DBRW-08: 04_report.py does not import DB modules."""
        source = (POC1_DIR / "04_report.py").read_text(encoding="utf-8")
        assert "psycopg2" not in source

    def test_SS_DBRW_09_report_builder_no_db_imports(self):
        """SS-DBRW-09: report_builder.py does not import DB modules or run queries."""
        source = (POC1_DIR / "report_builder.py").read_text(encoding="utf-8")
        assert "psycopg2" not in source
        assert "import psycopg2" not in source
        # Table cell references to DB table names (as strings) are allowed;
        # check that actual query keywords are absent
        assert "SELECT " not in source
        assert "FROM juggling" not in source

    def test_SS_DBRW_10_utils_no_db_imports(self):
        """SS-DBRW-10: utils.py does not import DB modules."""
        source = (POC1_DIR / "utils.py").read_text(encoding="utf-8")
        assert "psycopg2" not in source
        assert "connect" not in source
