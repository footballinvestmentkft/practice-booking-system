#!/usr/bin/env python3
"""
validate_local.py — CI-independent one-command validation
==========================================================
Runs all 8 validation gates and writes a pass/fail report.

Gates:
  G1  pytest -m sched                                   (expect ≥20 pass)
  G2  pytest tests/integration/api_smoke/               (expect ≥1700 pass)
  G3  pytest tests/performance/test_query_budget.py     (9 tests: budget + ORM guards)
  G4  EXPLAIN: Q1 (Semester PK) is plain scan — no JOIN
  G5  EXPLAIN: sessions 6-column row width ≤ 200 B
  G6  Mini ramp: Browse p95 ≤ 100ms @ 50 VU
  G7  Mini ramp: Browse p95 ≤ 1000ms @ 300 VU
  G8  Mini ramp: error rate 0% @ 300 VU

Usage:
    python3 scripts/validate_local.py
    MINI_RAMP=0 python3 scripts/validate_local.py    # skip G6-G8 (fast mode)

Output: tests/performance/VALIDATION_REPORT_<timestamp>.md
"""
import csv
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STAMP = time.strftime("%Y%m%d_%H%M")
REPORT_PATH = PROJECT_ROOT / f"tests/performance/VALIDATION_REPORT_{STAMP}.md"
SKIP_MINI_RAMP = os.environ.get("MINI_RAMP", "1") == "0"

MINI_VU_LEVELS = [50, 300, 500]
MINI_HOLD_S = 60
MINI_COOLDOWN_S = 10
PORT = 8003
WORKERS = 4
SEMESTER_IDS = os.environ.get("LOAD_SEMESTER_IDS", "9932")
EVENT_IDS = os.environ.get("LOAD_EVENT_IDS", "1,31,2,3,33")

# Gate thresholds
G1_MIN_PASS = 20
G2_MIN_PASS = 1700
G4_JOIN_KEYWORDS = ("nested loop", "hash join", "merge join")
G5_ROW_WIDTH_MAX_B = 200
G6_BROWSE_P95_50VU_MS = 100
G7_BROWSE_P95_300VU_MS = 1000
G8_ERROR_PCT_300VU = 0.0

GATE_DESCS = {
    "G1": "pytest -m sched (expect ≥20 pass)",
    "G2": "pytest api_smoke (expect ≥1700 pass)",
    "G3": "pytest test_query_budget (9 tests: budget + ORM guards)",
    "G4": "EXPLAIN Q1: Semester PK = plain scan (no JOIN)",
    "G5": "EXPLAIN sessions: row width ≤ 200B (6-col select)",
    "G6": f"Mini ramp: Browse p95 ≤ {G6_BROWSE_P95_50VU_MS}ms @ 50 VU",
    "G7": f"Mini ramp: Browse p95 ≤ {G7_BROWSE_P95_300VU_MS}ms @ 300 VU",
    "G8": f"Mini ramp: error rate ≤ {G8_ERROR_PCT_300VU}% @ 300 VU",
}

TEMP_LOCUSTFILE = textwrap.dedent("""\
    import sys
    sys.path.insert(0, {root!r})
    from tests.performance.locustfile import SoakBurstUser  # noqa: F401
""")


# ── Gate runners ──────────────────────────────────────────────────────────────

def run_pytest_gate(
    args: list[str], label: str, min_pass: int = 0
) -> tuple[bool, str]:
    """Run pytest subprocess; return (passed, summary_line)."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest"] + args + ["--tb=no", "-q"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    output = (result.stdout + result.stderr).strip()
    lines = [l for l in output.splitlines() if l.strip()]
    summary = lines[-1] if lines else "(no output)"

    m_pass = re.search(r"(\d+) passed", summary)
    m_fail = re.search(r"(\d+) failed", summary)
    n_pass = int(m_pass.group(1)) if m_pass else 0
    n_fail = int(m_fail.group(1)) if m_fail else 0

    ok = result.returncode == 0
    if min_pass and n_pass < min_pass:
        ok = False
        summary += f" [only {n_pass} passed, expected ≥{min_pass}]"

    return ok, summary


def run_explain_gates() -> list[tuple[str, bool, str]]:
    """G4 + G5: EXPLAIN-based query plan checks via SQLAlchemy."""
    results: list[tuple[str, bool, str]] = []
    try:
        from app.database import engine  # type: ignore
        from sqlalchemy import text as _text
    except Exception as e:
        err = f"import error: {e}"
        return [("G4", False, err), ("G5", False, err)]

    # G4: Semester PK lookup must NOT involve a JOIN
    try:
        with engine.connect() as conn:
            rows = conn.execute(_text(
                "EXPLAIN SELECT * FROM semesters WHERE id = 31"
            )).fetchall()
        plan = " ".join(r[0] for r in rows).lower()
        has_join = any(kw in plan for kw in G4_JOIN_KEYWORDS)
        results.append(("G4", not has_join, (
            f"Q1 = plain scan ✅ | {plan[:120]}"
            if not has_join else
            f"Q1 CONTAINS JOIN ❌ | {plan[:120]}"
        )))
    except Exception as e:
        results.append(("G4", False, f"EXPLAIN error: {e}"))

    # G5: 6-column sessions query row width ≤ G5_ROW_WIDTH_MAX_B
    try:
        with engine.connect() as conn:
            rows = conn.execute(_text(
                "EXPLAIN SELECT round_number, session_status, date_start, "
                "participant_team_ids, participant_user_ids, rounds_data "
                "FROM sessions WHERE semester_id = 31 "
                "ORDER BY round_number ASC NULLS LAST, id"
            )).fetchall()
        widths = [
            int(m.group(1))
            for r in rows
            for m in [re.search(r"width=(\d+)", r[0])]
            if m
        ]
        if not widths:
            results.append(("G5", False, "Could not parse width from EXPLAIN output"))
        else:
            max_w = max(widths)
            ok = max_w <= G5_ROW_WIDTH_MAX_B
            results.append(("G5", ok, (
                f"sessions row width={max_w}B (≤{G5_ROW_WIDTH_MAX_B}B) ✅"
                if ok else
                f"sessions row width={max_w}B EXCEEDS {G5_ROW_WIDTH_MAX_B}B ❌"
            )))
    except Exception as e:
        results.append(("G5", False, f"EXPLAIN error: {e}"))

    return results


def run_mini_ramp() -> tuple[list[tuple[str, bool, str]], dict[int, dict]]:
    """G6-G8: run mini stepped ramp. Returns (gate_results, level_stats)."""
    import shutil
    import urllib.request

    def _fail(reason: str):
        return [(f"G{g}", False, reason) for g in [6, 7, 8]], {}

    if not shutil.which("locust"):
        return _fail("locust not found — pip install locust")
    if not shutil.which("uvicorn"):
        return _fail("uvicorn not found")

    tmp_lf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="validate_ramp_", dir="/tmp", delete=False
    )
    tmp_lf.write(TEMP_LOCUSTFILE.format(root=str(PROJECT_ROOT)))
    tmp_lf.close()

    env = {
        **os.environ,
        "ENABLE_RATE_LIMITING": "false",
        "DB_STATEMENT_TIMEOUT_MS": "8000",
    }
    host = f"http://127.0.0.1:{PORT}"

    server_proc = subprocess.Popen(
        [
            "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(PORT),
            "--workers", str(WORKERS), "--log-level", "warning",
        ],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(PROJECT_ROOT),
    )

    def cleanup() -> None:
        server_proc.send_signal(signal.SIGTERM)
        try:
            server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        Path(tmp_lf.name).unlink(missing_ok=True)

    # Wait for server to be ready
    deadline = time.monotonic() + 30
    server_ok = False
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{host}/health", timeout=2) as r:
                if r.status == 200:
                    server_ok = True
                    break
        except Exception:
            pass
        time.sleep(0.5)

    if not server_ok:
        cleanup()
        return _fail("Server did not start within 30s")

    results_dir = PROJECT_ROOT / "tests/performance/results" / f"mini_ramp_{STAMP}"
    results_dir.mkdir(parents=True, exist_ok=True)

    level_stats: dict[int, dict] = {}

    try:
        for i, vu in enumerate(MINI_VU_LEVELS):
            print(f"    {vu:3d} VU × {MINI_HOLD_S}s …", flush=True)
            csv_prefix = str(results_dir / f"level_{vu:04d}")
            locust_env = {
                **env,
                "LOAD_SEMESTER_IDS": SEMESTER_IDS,
                "LOAD_EVENT_IDS": EVENT_IDS,
                "LOAD_USERS_COUNT": "100",
                "LOAD_PEAK_VUS": str(vu),
            }
            subprocess.run(
                [
                    "locust", "-f", tmp_lf.name, "SoakBurstUser",
                    "--headless", "--host", host,
                    "--users", str(vu), "--spawn-rate", str(min(vu, 50)),
                    "--run-time", f"{MINI_HOLD_S}s",
                    "--csv", csv_prefix,
                    "--loglevel", "WARNING",
                ],
                env=locust_env, capture_output=True, text=True,
            )

            stats_csv = Path(f"{csv_prefix}_stats.csv")
            if stats_csv.exists():
                with open(stats_csv) as f:
                    for row in csv.DictReader(f):
                        name = row.get("Name", "")
                        try:
                            level_stats.setdefault(vu, {})[name] = {
                                "requests": int(row["Request Count"]),
                                "failures": int(row["Failure Count"]),
                                "p50":  float(row["50%"]),
                                "p95":  float(row["95%"]),
                                "p99":  float(row["99%"]),
                            }
                        except (ValueError, KeyError):
                            pass

            if i < len(MINI_VU_LEVELS) - 1:
                time.sleep(MINI_COOLDOWN_S)
    finally:
        cleanup()

    gate_results: list[tuple[str, bool, str]] = []

    # G6: Browse p95 ≤ G6_BROWSE_P95_50VU_MS @ 50 VU
    br_50 = level_stats.get(50, {}).get("[P63] Browse event", {}).get("p95", 9999)
    ok6 = br_50 <= G6_BROWSE_P95_50VU_MS
    gate_results.append(("G6", ok6, (
        f"Browse p95@50VU={br_50:.0f}ms (≤{G6_BROWSE_P95_50VU_MS}ms) ✅"
        if ok6 else
        f"Browse p95@50VU={br_50:.0f}ms EXCEEDS {G6_BROWSE_P95_50VU_MS}ms ❌"
    )))

    # G7: Browse p95 ≤ G7_BROWSE_P95_300VU_MS @ 300 VU
    br_300 = level_stats.get(300, {}).get("[P63] Browse event", {}).get("p95", 9999)
    ok7 = br_300 <= G7_BROWSE_P95_300VU_MS
    gate_results.append(("G7", ok7, (
        f"Browse p95@300VU={br_300:.0f}ms (≤{G7_BROWSE_P95_300VU_MS}ms) ✅"
        if ok7 else
        f"Browse p95@300VU={br_300:.0f}ms EXCEEDS {G7_BROWSE_P95_300VU_MS}ms ❌"
    )))

    # G8: error rate ≤ 0% @ 300 VU
    agg = level_stats.get(300, {}).get("Aggregated", {})
    req = agg.get("requests", 0)
    fail = agg.get("failures", 0)
    err_pct = 100 * fail / req if req else 0
    ok8 = err_pct <= G8_ERROR_PCT_300VU
    gate_results.append(("G8", ok8, (
        f"error%@300VU={err_pct:.2f}% (≤{G8_ERROR_PCT_300VU}%) ✅"
        if ok8 else
        f"error%@300VU={err_pct:.2f}% EXCEEDS {G8_ERROR_PCT_300VU}% ❌"
    )))

    return gate_results, level_stats


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(
    gate_table: list[tuple[str, bool, str]],
    level_stats: dict[int, dict],
    elapsed: float,
) -> None:
    counted = [(g, ok, d) for g, ok, d in gate_table if "SKIPPED" not in d]
    total = len(counted)
    passed = sum(1 for _, ok, _ in counted if ok)
    overall = "✅ ALL GATES PASSED" if passed == total else f"❌ {total - passed}/{total} GATES FAILED"

    lines = [
        "# Local Validation Report — Phase 10 + Phase 11",
        "",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M')} local",
        f"**Runtime:** {elapsed:.0f}s",
        f"**SHA:** (run `git rev-parse --short HEAD` to capture)",
        f"**Result: {overall}**",
        "",
        "---",
        "",
        "## Gate Summary",
        "",
        "| Gate | Description | Result | Detail |",
        "|------|-------------|--------|--------|",
    ]

    for gate, ok, detail in gate_table:
        if "SKIPPED" in detail:
            status = "⏭ SKIP"
        else:
            status = "✅ PASS" if ok else "❌ FAIL"
        lines.append(f"| {gate} | {GATE_DESCS.get(gate, gate)} | {status} | {detail} |")

    # Mini ramp table
    if level_stats:
        lines += ["", "---", "", "## Mini Ramp Results", ""]
        lines += [
            "| VUs | Browse p50 | Browse p95 | Browse p99 | Enroll p95 | Error% | Status |",
            "|-----|-----------|-----------|-----------|-----------|--------|--------|",
        ]
        for vu in MINI_VU_LEVELS:
            s = level_stats.get(vu, {})
            br = s.get("[P63] Browse event", {})
            en = s.get("[P63] Enroll", {})
            agg = s.get("Aggregated", {})
            req = agg.get("requests", 0)
            fail = agg.get("failures", 0)
            err = 100 * fail / req if req else 0
            broken = (
                (vu == 300 and br.get("p95", 0) > G7_BROWSE_P95_300VU_MS)
                or (vu == 300 and err > G8_ERROR_PCT_300VU)
                or (vu == 50 and br.get("p95", 0) > G6_BROWSE_P95_50VU_MS)
            )
            status = "❌ BROKEN" if broken else "✅ Stable"
            lines.append(
                f"| {vu:3d} | {br.get('p50', 0):9.0f}ms "
                f"| {br.get('p95', 0):9.0f}ms "
                f"| {br.get('p99', 0):9.0f}ms "
                f"| {en.get('p95', 0):9.0f}ms "
                f"| {err:5.2f}% | {status} |"
            )

    lines += [
        "",
        "---",
        "",
        "*Generated by `scripts/validate_local.py`*",
    ]

    REPORT_PATH.write_text("\n".join(lines) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    t0 = time.monotonic()
    gate_table: list[tuple[str, bool, str]] = []

    print(f"\n{'═' * 64}")
    print(f"  Local Validation — Phase 10 + Phase 11")
    print(f"  {time.strftime('%Y-%m-%d %H:%M')}  |  stamp={STAMP}")
    print(f"{'═' * 64}\n")

    # G1: scheduling tests
    print("G1  pytest -m sched …", flush=True)
    ok, detail = run_pytest_gate(
        ["-m", "sched", "--ignore=tests/performance"], "G1", min_pass=G1_MIN_PASS
    )
    gate_table.append(("G1", ok, detail))
    print(f"    {'✅' if ok else '❌'}  {detail}\n")

    # G2: API smoke suite
    print("G2  pytest tests/integration/api_smoke/ …", flush=True)
    ok, detail = run_pytest_gate(
        ["tests/integration/api_smoke/"], "G2", min_pass=G2_MIN_PASS
    )
    gate_table.append(("G2", ok, detail))
    print(f"    {'✅' if ok else '❌'}  {detail}\n")

    # G3: query budget + ORM regression guards
    print("G3  pytest tests/performance/test_query_budget.py …", flush=True)
    ok, detail = run_pytest_gate(
        ["tests/performance/test_query_budget.py", "-v"], "G3"
    )
    gate_table.append(("G3", ok, detail))
    print(f"    {'✅' if ok else '❌'}  {detail}\n")

    # G4 + G5: EXPLAIN plan checks
    print("G4  EXPLAIN Q1 (Semester PK, no JOIN) …", flush=True)
    print("G5  EXPLAIN sessions width (6-col, ≤200B) …", flush=True)
    for gate, ok, detail in run_explain_gates():
        gate_table.append((gate, ok, detail))
        print(f"    {gate}: {'✅' if ok else '❌'}  {detail}")
    print()

    # G6-G8: mini ramp
    level_stats: dict[int, dict] = {}
    if SKIP_MINI_RAMP:
        print("Mini ramp SKIPPED (MINI_RAMP=0)\n")
        for g in ["G6", "G7", "G8"]:
            gate_table.append((g, True, "SKIPPED — set MINI_RAMP=1 to enable"))
    else:
        print(
            f"G6-G8  Mini ramp ({len(MINI_VU_LEVELS)} levels × {MINI_HOLD_S}s, "
            f"cooldown={MINI_COOLDOWN_S}s) …",
            flush=True,
        )
        ramp_gates, level_stats = run_mini_ramp()
        for gate, ok, detail in ramp_gates:
            gate_table.append((gate, ok, detail))
            print(f"    {gate}: {'✅' if ok else '❌'}  {detail}")
        print()

    elapsed = time.monotonic() - t0
    write_report(gate_table, level_stats, elapsed)

    counted = [(g, ok, d) for g, ok, d in gate_table if "SKIPPED" not in d]
    total = len(counted)
    n_pass = sum(1 for _, ok, _ in counted if ok)
    n_fail = total - n_pass

    print(f"{'═' * 64}")
    if n_fail == 0:
        print(f"  ✅  ALL {total} GATES PASSED")
    else:
        print(f"  ❌  {n_fail}/{total} GATES FAILED")
        for gate, ok, detail in gate_table:
            if not ok and "SKIPPED" not in detail:
                print(f"      {gate}: {detail}")
    print(f"  Runtime: {elapsed:.0f}s  |  Report: {REPORT_PATH.name}")
    print(f"{'═' * 64}\n")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
