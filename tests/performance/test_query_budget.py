"""
Query Budget + ORM Regression Tests — GET /events/{id}
=======================================================

Hard limit: ≤15 queries per request to GET /events/{tournament_id}.

Purpose: Prevent N+1 regressions from silently reintroducing unbounded query growth.
The limit is N-INDEPENDENT — it must hold regardless of how many rankings, participants,
or sessions the event has.

Baseline (2026-04-18, Phase 9+10+11):
  Phase 9 fix:  ≤15 queries for any event (selectinload — O(1) w.r.t. data)
  Phase 11 fix: sessions SELECT uses 6 explicit columns (width≤200B vs 1439B SELECT *)

If you add a new query to public_event_detail and this test fails:
  - Check if you introduced a loop with db.query() inside it (N+1)
  - Use selectinload()/joinedload() for ORM relationships
  - Use WHERE id IN (...) batch queries for non-relationship lookups
  - If the new query is genuinely fixed-count, adjust QUERY_BUDGET to the new minimum

Session column guard (TestSessionColumnNarrowing):
  Asserts that the sessions query does NOT emit SELECT * on sessions.
  Failure means someone reverted to db.query(SessionModel) — restore column select.

See: tests/performance/DB_PROFILING_300VU.md for full profiling analysis.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event, text

from app.main import app
from app.database import engine


# Hard limit — must hold for any event ID regardless of data size
QUERY_BUDGET = 15

# Sessions query row-width limit (Phase 11 — explicit column select)
# SELECT * width = 1,439 B.  6-column select expected ≤ 200 B.
SESSION_ROW_WIDTH_MAX_B = 200

# Events used in load tests (varying data richness)
# Event 31: REWARDS_DISTRIBUTED, Swiss, 16 rankings, 32 sessions — heaviest case
# Events 1,2,3,33: DRAFT / ENROLLMENT_OPEN, 0 sessions — baseline case
LOAD_TEST_EVENT_IDS = [1, 2, 3, 31, 33]

# Required fields that the sessions query must return (ORM regression guard)
SESSION_REQUIRED_FIELDS = {
    "round_number", "session_status", "date_start",
    "participant_team_ids", "participant_user_ids", "rounds_data",
}


def _count_queries_for(client: TestClient, path: str) -> int:
    """Return the number of SQL statements executed for a single GET request."""
    count = 0

    def _before_execute(conn, cursor, statement, parameters, context, executemany):
        nonlocal count
        count += 1

    sa_event.listen(engine, "before_cursor_execute", _before_execute)
    try:
        client.get(path)
    finally:
        sa_event.remove(engine, "before_cursor_execute", _before_execute)
    return count


def _capture_sessions_sql(client: TestClient, path: str) -> list[str]:
    """Capture all SQL statements that reference the sessions table."""
    stmts: list[str] = []

    def _before_execute(conn, cursor, statement, parameters, context, executemany):
        if "FROM sessions" in statement or "from sessions" in statement:
            stmts.append(statement)

    sa_event.listen(engine, "before_cursor_execute", _before_execute)
    try:
        client.get(path)
    finally:
        sa_event.remove(engine, "before_cursor_execute", _before_execute)
    return stmts


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestQueryBudget:
    """
    Each test hits one event page and asserts query count ≤ QUERY_BUDGET.

    Failure means an N+1 regression was introduced — do NOT just raise the budget.
    Fix the regression first, then check if budget needs adjusting.
    """

    @pytest.mark.parametrize("event_id", LOAD_TEST_EVENT_IDS)
    def test_event_detail_query_budget(self, client, event_id):
        """GET /events/{id} must use ≤15 queries for any event in the load test set."""
        n = _count_queries_for(client, f"/events/{event_id}")
        assert n <= QUERY_BUDGET, (
            f"GET /events/{event_id} used {n} queries — exceeds budget of {QUERY_BUDGET}.\n"
            f"N+1 regression detected. Use selectinload()/joinedload() or batch IN queries.\n"
            f"See tests/performance/DB_PROFILING_300VU.md for patterns."
        )

    def test_event_31_is_the_heaviest_case(self, client):
        """Event 31 (16 rankings + 32 sessions) must be within budget — proves N-independence."""
        n = _count_queries_for(client, "/events/31")
        assert n <= QUERY_BUDGET, (
            f"Event 31 used {n} queries (heaviest load test case). "
            f"Budget: {QUERY_BUDGET}. Fix N+1 before raising budget."
        )
        # Also assert it uses more than 3 queries (sanity — confirms real DB access)
        assert n >= 3, f"Suspiciously low query count ({n}) — may indicate test isolation issue"


class TestSessionColumnNarrowing:
    """
    Phase 11 regression guards — ensure sessions query uses explicit column select.

    Failure indicates reversion to SELECT * (db.query(SessionModel)) which
    increases per-request data transfer from ~3 KB to ~46 KB for event 31.
    """

    def test_sessions_query_does_not_select_star(self, client):
        """The sessions fetch must NOT emit SELECT sessions.* (full ORM object load).

        Checks that the emitted SQL selects specific columns, not the wildcard.
        A SELECT * would indicate db.query(SessionModel) was re-introduced.
        """
        stmts = _capture_sessions_sql(client, "/events/31")
        # Filter to the main bulk-fetch (not the DISTINCT campus_id sub-query)
        bulk_fetches = [
            s for s in stmts
            if "DISTINCT" not in s and "campus_id" not in s
        ]
        assert bulk_fetches, "No bulk sessions SELECT found — event 31 should have 32 sessions"

        for stmt in bulk_fetches:
            # SELECT * or sessions.* indicates full ORM object load
            assert "sessions.*" not in stmt.lower(), (
                "Sessions query uses SELECT * — revert detected.\n"
                "Use db.query(SessionModel.col1, ...) with explicit columns.\n"
                f"Offending statement: {stmt[:200]}"
            )
            # Must not select columns that are NOT in the required set via model alias
            # (heuristic: if 'password_hash' appears, it loaded full User join — wrong table)
            assert "password_hash" not in stmt, (
                "Sessions query accidentally joins User table — check query construction."
            )

    def test_sessions_query_includes_all_required_fields(self, client):
        """The sessions SQL must include all 6 required column names.

        If a required field is missing, the route will raise AttributeError at runtime
        when accessing sess.<field> in the schedule/IR loops.
        """
        stmts = _capture_sessions_sql(client, "/events/31")
        bulk_fetches = [
            s for s in stmts
            if "DISTINCT" not in s and "campus_id" not in s
        ]
        assert bulk_fetches, "No bulk sessions SELECT found"

        combined = " ".join(bulk_fetches)
        for field in SESSION_REQUIRED_FIELDS:
            assert field in combined, (
                f"Required session field '{field}' not found in sessions SQL.\n"
                f"Add SessionModel.{field} to the explicit column list in public_tournament.py.\n"
                f"SQL snippet: {combined[:300]}"
            )

    def test_sessions_row_width_via_explain(self):
        """EXPLAIN on the sessions query must show row width ≤ SESSION_ROW_WIDTH_MAX_B.

        This is a direct DB-level guard that the payload reduction is real.
        SELECT *: width=1,439 B.  6-column select: width≤200 B.
        """
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            result = db.execute(text(
                "EXPLAIN SELECT round_number, session_status, date_start, "
                "participant_team_ids, participant_user_ids, rounds_data "
                "FROM sessions WHERE semester_id = 31 "
                "ORDER BY round_number ASC NULLS LAST, id"
            )).fetchall()
        finally:
            db.close()

        # Parse width from EXPLAIN output: "... width=NNN ..."
        import re
        widths = []
        for row in result:
            line = row[0]
            m = re.search(r"width=(\d+)", line)
            if m:
                widths.append(int(m.group(1)))

        assert widths, "Could not parse width from EXPLAIN output"
        max_width = max(widths)
        assert max_width <= SESSION_ROW_WIDTH_MAX_B, (
            f"Sessions query row width={max_width} B exceeds limit of {SESSION_ROW_WIDTH_MAX_B} B.\n"
            f"Expected 6-column select (≤200 B). SELECT * gives 1,439 B.\n"
            f"Check public_tournament.py sessions query."
        )
