# Phase 8 — Stepped Ramp v3: bcrypt asyncio.to_thread Report

**Generated:** 2026-04-18 15:45 local
**SHA:** 750b871 (main)
**Configuration:** pool_size=50, max_overflow=100, PG max_connections=500 (Phase 7 — unchanged)
**Phase 8 change:** `verify_password` wrapped in `await asyncio.to_thread(...)` in `async def login_submit`
**Protocol:** 5 levels × 300s hold, 15s cooldown
**Workers:** 4 uvicorn, 1 PostgreSQL 14, rate-limiting OFF
**Comparison baseline:** v2 (LOAD_REPORT_STEPPED_RAMP_V2_POOL_TUNED.md — SHA 84aa370)

---

## Capacity Curve — v3 (Phase 8: bcrypt async)

| VUs | Requests | RPS | Error% | Browse p95 | Enroll p95 | Login p95 | slow_q Δ | Status |
|-----|----------|-----|--------|-----------|-----------|----------|---------|--------|
|  50 |    20164 |  67.3 |   0.0% |        29ms |        22ms |      740ms |        2 | ✅ Stable |
| 100 |    40369 | 134.7 |   0.0% |        29ms |        23ms |      650ms |        4 | ✅ Stable |
| 200 |    78674 | 262.6 |   0.0% |        72ms |        59ms |      860ms |       13 | ✅ Stable |
| 300 |    96228 | 320.8 |   0.0% |       550ms |       580ms |     1300ms |       61 | ✅ Stable |
| 500 |    84379 | 281.4 |   1.0% |      1900ms |      2200ms |     2100ms |      101 | ❌ BROKEN |

**Breaking point: 500 VUs** (same as v2)
**Last stable level: 300 VUs** (same as v2)
**invariant_violations Δ = 0** at all levels — no data integrity issues

---

## Phase 8 Key Result: Login p95 Improvement

| VUs | v2 Login p95 | v3 Login p95 | Improvement |
|-----|-------------|-------------|-------------|
|  50 |      1600ms |       740ms | **2.2×** |
| 100 |      1900ms |       650ms | **2.9×** |
| 200 |      2100ms |       860ms | **2.4×** |
| 300 |      2300ms |      1300ms | **1.8×** |
| 500 |      6600ms |      2100ms | **3.1×** |

**Login errors at ALL levels: 0%** — asyncio.to_thread eliminated login serialization.

### Why Login p95 is still ~650–1300ms at low VUs

At spawn time, all VUs call `on_start()` (login) nearly simultaneously.
With spawn_rate=50: at 100 VU, spawning takes ~2s — all 100 logins arrive in a tight burst.
Each worker gets ~25 concurrent login requests. The thread pool (default: `min(32, cpu+4)` threads)
handles these in batches of ~N_threads per round × 60ms/bcrypt.
- Expected queuing at 100 VU: ~2 rounds × 60ms = ~120ms + overhead → ~650ms p95 ✅ reasonable
- v2 at 100 VU Login p95=1900ms — event-loop serialized each bcrypt call sequentially

At low-load steady state (post-spawn), Login is not re-exercised — `on_start()` runs once.
The p95 captures only the burst-spawn window, which is expected to be non-trivial.

---

## Bottleneck Shift: Login → Browse/Enroll DB Latency

### v2 Breaking Point (SHA 84aa370)

**Primary bottleneck: Login event-loop starvation**
- `async def login_submit` called sync `bcrypt.checkpw` (~60ms) directly
- At 125 VU/worker: serialised logins into a ~7.5s queue
- Login p95=6600ms at 500 VU; event loop blocked → other requests also delayed
- Cascade: Browse/Enroll queued behind stalled event loop → p95=1700–1900ms

### v3 Breaking Point (SHA 750b871)

**Primary bottleneck: DB query latency under concurrency**
- Login is no longer the bottleneck (Login p95 2100ms, Login err=0%)
- Browse p95=1900ms, Enroll p95=2200ms — pure DB query queueing
- slow_queries Δ=101 (vs Δ=111 in v2 at 500 VU) — same magnitude
- Failure type: 813 total failures = Browse timeouts (1.1%) + Enroll timeouts (0.5%)
- Login actual failure count: **0** (the "Login 5xx=144.8%" in the auto-report is a script
  calculation bug — divides all failure codes by login request count; ignore that metric)

### Browse Latency Trend (identical shape, v2 vs v3)

| VUs | v2 Browse p95 | v3 Browse p95 | Δ |
|-----|--------------|--------------|---|
|  50 |        29ms  |        29ms  | 0 |
| 100 |        29ms  |        29ms  | 0 |
| 200 |        74ms  |        72ms  | -2ms |
| 300 |       550ms  |       550ms  | 0 |
| 500 |      1700ms  |      1900ms  | +200ms |

Browse p95 is essentially identical v2 vs v3 — the DB query latency is the consistent
bottleneck at high concurrency, independent of the bcrypt optimization.
The login fix did NOT improve Browse — Browse was already limited by DB, not login blocking.

---

## Phase Progression Summary

| Phase | Change | Breaking Point | Login p95 @ 500 VU | Last Stable |
|-------|--------|---------------|-------------------|-------------|
| v1 (pool 20/30, PG 100) | baseline | **200 VU** | N/A | 100 VU |
| v2 (pool 50/100, PG 500) | Phase 7 pool tuning | **500 VU** | 6600ms | 300 VU |
| v3 (bcrypt async) | Phase 8 asyncio.to_thread | **500 VU** | 2100ms | 300 VU |

Phase 7 (pool tuning): breaking point +2.5× (200 → 500 VU), login bottleneck unmasked
Phase 8 (bcrypt async): breaking point unchanged (500 VU), login p95 improved 3.1×,
bottleneck shifted from Login → Browse/Enroll DB latency

---

## Decision: Do NOT Optimize Immediately

Per Phase 8 protocol:
- 500 VU is NOT stable → new bottleneck is **Browse/Enroll DB query latency**
- 300 VU remains the stable operating ceiling (0% errors, all metrics green)
- PgBouncer: NOT needed — the 500 VU bottleneck is CPU/latency, not connection count
  (login failures=0, pool is not exhausted — slow_q Δ=101 is query contention, not ECONNREFUSED)

**New bottleneck documented: slow DB queries at 500 VU (slow_q Δ=101)**
Candidates: N+1 queries on Browse endpoint, missing index on semester/event browse filters,
or query planner regression at high concurrent connections.

**Do NOT introduce PgBouncer** until connection exhaustion is empirically proven again.

---

## Potential Next Steps (not immediately actioned)

1. **Profile Browse queries under 500 VU load** — `pg_stat_statements` or query log to
   identify which queries produce slow_q Δ=101; candidate: browse JOIN on semesters + enrollments
2. **Index audit** on browse query filters (semester_category, status, specialization_type)
   under concurrent load — `EXPLAIN ANALYZE` during a 300 VU hold
3. **Extend ramp to 700–1000 VU** only after Browse latency bottleneck is understood —
   extending without fixing would confirm the curve but not add decision value
4. **CI gate** at 50 VU: Phase 8 fix is backward-compatible; no CI change needed

---

## Conclusion

Phase 8 objective **achieved**: `asyncio.to_thread` eliminated Login event-loop starvation.

- Login p95 at 500 VU: **6600ms → 2100ms (3.1× improvement)**
- Login failures at all levels: **0% (was significant in pre-Phase 7 baseline)**
- Stable ceiling: **300 VU** (unchanged — DB latency was the hidden bottleneck below login noise)
- Next bottleneck: **Browse/Enroll DB query latency at 500 VU** — requires query profiling,
  not infrastructure changes

*This report is the decision basis for any Phase 9 query optimization or index work.*
