# Phase 6.3 — Local High-Load Test Report (1000 VUs)

**Generated:** 2026-04-18 13:51 local
**SHA:** a0210b9 (main)
**Environment:** Local — 4 uvicorn workers, Python 3.13, PostgreSQL 14 (local)
**Peak VUs:** 1000 | 5-stage shape (~10 min)
**Purpose:** Determine real breaking point — NOT CI-gated, NOT constrained to 50 VU CI limit
**Status:** ❌ BREAKING POINT REACHED — system NOT stable at 1000 VUs

---

## Results Summary

| Endpoint | Requests | Failures | Fail% | p50 | p95 | p99 | CI Threshold |
|----------|----------|----------|-------|-----|-----|-----|--------------|
| [P63] Browse event | 96,095 | 38,577 | **40.1%** | 1,900ms | **3,200ms** | 4,200ms | ≤500ms |
| [P63] Enroll | 22,188 | 5,824 | **26.2%** | 39ms | **3,400ms** | 4,300ms | ≤1000ms |
| [P63] Fetch enrollments | 3,963 | 354 | 8.9% | 10ms | 2,300ms | 3,100ms | — |
| [P63] Login | 1,000 | 259 | **25.9%** | 5,300ms | **13,000ms** | 14,000ms | — |
| [P63] Withdraw | 3,608 | 147 | 4.1% | 18ms | 2,500ms | 3,600ms | ≤1000ms |
| **Aggregated** | **127,595** | **45,161** | **35.4%** | 1,600ms | 3,300ms | 4,600ms | — |

**Throughput:** 212.6 req/s (but 35.4% failure rate → ~137 successful req/s effective)

---

## Breaking Point Analysis

### Primary Bottleneck: DB Connection Pool Exhaustion

Login failure mode: **500 Internal Server Error (25.9% of 1000 logins)**.
At 1000 VUs all spawning simultaneously, the PostgreSQL connection pool saturates during the
warmup ramp. Login handlers wait for an available connection and timeout → 500.
This cascades: VUs that fail login skip all tasks → reduces actual load but browse p95 still
degrades to 3.2s (queue depth from pool contention).

### Secondary: Response Time Degradation

All endpoints degrade 3–26x above CI thresholds:
- Browse p95: 3,200ms (6.4× CI threshold of 500ms)
- Enroll p95: 3,400ms (3.4× CI threshold of 1000ms)
- Login p95: 13,000ms (no threshold, but functionally unusable)

### Failure Type Breakdown

| Failure | Count | Root Cause |
|---------|-------|------------|
| Browse timeout/connection error | 38,577 | DB pool queue depth |
| Login 500 (DB pool exhaustion) | 259 | Pool saturated during burst spawn |
| Enroll failures | 5,824 | Queued behind pool contention |
| Withdraw 401 | 147 | Session lost mid-soak (pool timeout → 401) |

---

## Breaking Point Estimate

| Scale | Status | Evidence |
|-------|--------|----------|
| 50 VUs (CI) | ✅ Stable | All 5 GATEs passed, 0 failures, p95 ≤76ms |
| 1000 VUs (local) | ❌ Broken | 35% failure rate, p95 3,200–13,000ms |
| **~100–300 VUs** | **⚠️ Estimated threshold** | Stepped ramp test needed to pinpoint |

**Verdict:** Breaking point is between 50 VUs and 1000 VUs — estimated **~100–300 VUs** based on
the failure signature (pool exhaustion on 1000-VU ramp, zero failures at 50 VU).
A stepped ramp test (50→100→200→300→500 VUs, 5 min each) would pinpoint the exact cliff.

---

## Constraint Root Causes

1. **DB connection pool size** — default SQLAlchemy pool_size (likely 5–20) is the hard ceiling.
   At 1000 concurrent VUs × 10% executing DB queries simultaneously = 100 concurrent connections
   needed. Default pool cannot provide this.

2. **Single DB instance** — local PostgreSQL 14, no read replicas, no connection pooler (PgBouncer).

3. **Uvicorn 4 workers × 1 async event loop each** — handles I/O concurrency, but DB-bound
   handlers block the pool and create queue depth.

---

## Recommended Next Steps (for scaling beyond 300 VUs)

1. **Increase DB pool**: `create_engine(..., pool_size=50, max_overflow=100)` → validate at 500 VUs
2. **Add PgBouncer** (transaction pooling) → multiplexes DB connections, eliminates pool exhaustion
3. **Run stepped ramp test** to find exact breaking point: 50→100→200→300→500→750→1000 VUs
4. **Index audit**: Browse p95=1,900ms (p50) under load suggests missing index on tournament/semester
   browse queries — run `EXPLAIN ANALYZE` on `/events/{id}` at high concurrency

---

## CI vs Local Comparison

| Metric | CI 50 VUs (SHA 255aaa7) | Local 1000 VUs (SHA a0210b9) |
|--------|-------------------------|-------------------------------|
| Browse p95 | 76ms | 3,200ms |
| Enroll p95 | 65ms | 3,400ms |
| Withdraw p95 | 65ms | 2,500ms |
| Failure rate | 0% | 35.4% |
| Login 500s | 0 | 259 |
| Total requests | 18,091 | 127,595 |
| Effective req/s | ~30 | ~137 (successful only) |
| Status | ✅ ALL GATES | ❌ BROKEN |

---

## Conclusion

**Phase 6.3 CI gate validates stability at 50 VUs — this is the correct CI scope.**
The system is NOT designed for 1000 concurrent VUs without infrastructure changes
(pool sizing, PgBouncer, read replicas).
The breaking point is empirically confirmed to be **below 1000 VUs**, estimated **~100–300 VUs**.
A stepped ramp test is the next required action before any capacity planning decision.

*This report is the decision basis for Phase 7 infrastructure scaling work.*
