"""
Performance Baseline — Locust Load Test
========================================

Phase 5 baseline (TournamentCreationUser):
  Scenario: Admin creates tournaments via OPS Scenario endpoint.
  Target: /api/v1/tournaments/ops/run-scenario

Phase 6.2 (BrowseAndEnrollUser):
  Scenario: 70% browse public event page / 30% student enroll + withdraw
  Target: GET /events/{id} (public), POST /semesters/request-enrollment,
          POST /semesters/withdraw-enrollment

Phase 6.3 (SoakBurstUser + Phase63LoadShape):
  Scenario: 5-stage soak + burst with 100-account multi-user pool
  Stages: warmup → soak (5 min) → burst → spike → recovery  (~10 min total)
  Default peak: 1000 VUs (local) / 50 VUs (CI via LOAD_PEAK_VUS=50)
  Metrics: p95/p99 per endpoint, 429/5xx/network split, DB pool saturation
  Validity gates: see tests/performance/LOAD_BASELINE.md §GATE-1..5

Usage — Phase 5 baseline:
  locust -f tests/performance/locustfile.py --host=http://localhost:8000 \\
         --users=1 --spawn-rate=1 --run-time=10s --headless

Usage — Phase 6.2 (requires running uvicorn + seed data):
  # Start server first:
  #   uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

  # Smoke (5 users, 30s):
  locust -f tests/performance/locustfile.py --host=http://localhost:8000 \\
         --users=5 --spawn-rate=2 --run-time=30s --headless \\
         --class-picker  # select BrowseAndEnrollUser

  # Baseline load (30 users, 120s):
  locust -f tests/performance/locustfile.py --host=http://localhost:8000 \\
         --users=30 --spawn-rate=5 --run-time=120s --headless \\
         --class-picker

  # Stress (100 users, 180s — rate limit fires at this scale by design):
  locust -f tests/performance/locustfile.py --host=http://localhost:8000 \\
         --users=100 --spawn-rate=10 --run-time=180s --headless \\
         --class-picker

Usage — Phase 6.3 (soak + burst, requires seed + uvicorn):
  # Seed 100 load-test accounts first:
  #   python scripts/seed_load_test_users.py

  # Full local run (1000 VUs, 10 min, 4 uvicorn workers):
  #   bash scripts/run_phase63_load.sh

  # Manual / custom:
  LOAD_PEAK_VUS=200 \\
  locust -f tests/performance/locustfile.py SoakBurstUser \\
    --host=http://localhost:8000 --headless --run-time=10m \\
    --csv=tests/performance/results/run

Phase 6.2 environment variables:
  LOAD_STUDENT_EMAIL     Student email with active LFA_FOOTBALL_PLAYER license
                         and sufficient credits (default: perf-student@lfa.com)
  LOAD_STUDENT_PASSWORD  Student password (default: Test1234!)
  LOAD_SEMESTER_IDS      Comma-separated MINI_SEASON ONGOING semester IDs
                         (default: none — enroll tasks skipped if not set)
  LOAD_EVENT_IDS         Comma-separated public tournament IDs for browse
                         (default: none — browse tasks use /events/1 fallback)

Phase 6.3 environment variables:
  LOAD_PEAK_VUS          Peak VU count for Phase63LoadShape (default: 1000)
                         CI sets this to 50 automatically
  LOAD_USERS_COUNT       Number of seeded user accounts to rotate (default: 100)
  LOAD_SEMESTER_IDS      Same as Phase 6.2 — comma-separated semester IDs
  LOAD_EVENT_IDS         Same as Phase 6.2 — comma-separated event IDs

Phase 5 environment variables:
  ADMIN_EMAIL:    Admin user email (default: admin@lfa.com)
  ADMIN_PASSWORD: Admin user password (default: admin123)
"""

import itertools
import os
import random
import re
import threading

try:
    import requests as _stdlib_requests  # for DB metrics probe (not locust client)
except ImportError:
    _stdlib_requests = None

from locust import HttpUser, LoadTestShape, between, events, task


# ============================================================================
# CONFIGURATION
# ============================================================================

ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "admin@lfa.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# Phase 6.2 — single-account student credentials
LOAD_STUDENT_EMAIL    = os.getenv("LOAD_STUDENT_EMAIL", "perf-student@lfa.com")
LOAD_STUDENT_PASSWORD = os.getenv("LOAD_STUDENT_PASSWORD", "Test1234!")

# Semester IDs for enrollment tasks (comma-separated string → list of ints)
_raw_sem = os.getenv("LOAD_SEMESTER_IDS", "")
LOAD_SEMESTER_IDS = [int(x) for x in _raw_sem.split(",") if x.strip()] if _raw_sem else []

# Tournament IDs for browse tasks (public /events/{id} page)
_raw_evt = os.getenv("LOAD_EVENT_IDS", "")
LOAD_EVENT_IDS = [int(x) for x in _raw_evt.split(",") if x.strip()] if _raw_evt else [1]

# Campus IDs for tournament session generation (Phase 5 baseline)
CAMPUS_IDS = [9]

# Phase 6.3 — multi-user pool configuration
_LOAD_PEAK_VUS    = int(os.getenv("LOAD_PEAK_VUS", "1000"))
_LOAD_USERS_COUNT = int(os.getenv("LOAD_USERS_COUNT", "100"))

# Round-robin pool of 100 seeded student accounts
_LOAD_USERS = [
    (f"load-user-{i:04d}@lfa.com", "LoadTest1234!")
    for i in range(1, _LOAD_USERS_COUNT + 1)
]
_USER_CYCLE = itertools.cycle(_LOAD_USERS)
_USER_LOCK  = threading.Lock()

# Phase 6.3 error counters — 429 / 5xx / network split (GATE-2b)
_P63_COUNTERS: dict = {"rate_limited": 0, "server_errors": 0, "network_errors": 0}
_P63_LOCK = threading.Lock()

# Phase 6.3 DB metrics baseline (captured at test_start)
_P63_METRICS_START: dict = {}


# ============================================================================
# BASELINE SCENARIO: Tournament Creation + Enrollment
# ============================================================================

class TournamentCreationUser(HttpUser):
    """
    Simulates admin creating tournaments via OPS Scenario endpoint.

    Workflow:
    1. Login as admin (once per user session)
    2. Create tournament via OPS Scenario (smoke_test, 4 players)
    3. Verify response: tournament_id + enrolled_count = 4

    Metrics tracked:
    - /api/v1/auth/login: p50, p95, p99 latency
    - /api/v1/tournaments/ops/run-scenario: p50, p95, p99 latency, RPS
    """

    # Wait time between tasks (simulates user think time)
    wait_time = between(1, 3)

    # Admin token (set on_start, reused across tasks)
    admin_token = None


    def on_start(self):
        """
        Called once when a simulated user starts.
        Login as admin and store token for subsequent requests.
        """
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            name="Login (Admin)"
        )

        if response.status_code == 200:
            self.admin_token = response.json()["access_token"]
        else:
            # Login failed - mark as failure and stop
            response.failure(f"Admin login failed: {response.text}")
            self.environment.runner.quit()


    @task
    def create_tournament_smoke_test(self):
        """
        Baseline scenario: Create tournament via OPS Scenario.

        Measures:
        - Tournament creation latency (revenue-critical path)
        - Auto-enrollment success rate
        - API error rate (4xx, 5xx)

        Expected:
        - HTTP 200 (success)
        - tournament_id present
        - enrolled_count = 4 (auto mode with @lfa-seed.hu users)
        """
        if not self.admin_token:
            # Skip if login failed
            return

        # Randomize tournament type to simulate realistic load distribution
        tournament_types = ["knockout", "league"]
        selected_type = random.choice(tournament_types)

        response = self.client.post(
            "/api/v1/tournaments/ops/run-scenario",
            headers={"Authorization": f"Bearer {self.admin_token}"},
            json={
                "scenario": "smoke_test",
                "player_count": 4,  # Auto mode (uses @lfa-seed.hu users)
                "tournament_format": "HEAD_TO_HEAD",
                "tournament_type_code": selected_type,
                "simulation_mode": "manual",
                "dry_run": False,
                "confirmed": False,
                "campus_ids": CAMPUS_IDS,
            },
            name="Create Tournament (OPS Scenario)"
        )

        # Validate response structure
        if response.status_code == 200:
            data = response.json()
            tournament_id = data.get("tournament_id")
            enrolled_count = data.get("enrolled_count", 0)

            if not tournament_id:
                response.failure("Missing tournament_id in response")
            elif enrolled_count != 4:
                response.failure(f"Expected 4 enrollments, got {enrolled_count}")
        else:
            # HTTP error (4xx, 5xx) - automatically marked as failure by Locust
            pass


# ============================================================================
# PHASE 6.2 SCENARIO: Browse + Enroll + Withdraw (70 / 20 / 10 split)
# ============================================================================

class BrowseAndEnrollUser(HttpUser):
    """
    Phase 6.2 — realistic student workload simulation.

    Task weights:
      70 %  browse_public_event      GET  /events/{id}              (no auth)
      20 %  enroll_in_semester       POST /semesters/request-enrollment
      10 %  withdraw_from_semester   POST /semesters/withdraw-enrollment

    Auth: cookie-based web login (POST /login).  HttpUser maintains cookies
    automatically so subsequent requests carry the session cookie.

    Prerequisites (set via env vars — see module docstring):
      - LOAD_STUDENT_EMAIL / LOAD_STUDENT_PASSWORD
      - LOAD_SEMESTER_IDS  (comma-separated IDs; if empty, enroll tasks no-op)
      - LOAD_EVENT_IDS     (comma-separated IDs; defaults to [1])

    KPI targets:
      Browse  GET /events/{id}               p95 < 100 ms   error < 0.5 %
      Enroll  POST /semesters/request-*      p95 < 500 ms   real error < 1 %
      Withdraw POST /semesters/withdraw-*    p95 < 500 ms   real error < 1 %
      Rate limit (429): expected at > 100 concurrent users (by design, not a bug)
    """

    wait_time = between(0.5, 1.5)

    # Per-user state
    _logged_in   = False
    _enrolled_id = None   # enrollment_id from last successful enroll

    def on_start(self):
        """Login once per simulated user, store the session cookie."""
        with self.client.post(
            "/login",
            data={
                "email":    LOAD_STUDENT_EMAIL,
                "password": LOAD_STUDENT_PASSWORD,
            },
            allow_redirects=False,
            name="Login (student web)",
            catch_response=True,
        ) as resp:
            if resp.status_code in (302, 303):
                self._logged_in = True
            else:
                resp.failure(
                    f"Student web login failed: {resp.status_code} — "
                    f"check LOAD_STUDENT_EMAIL / LOAD_STUDENT_PASSWORD"
                )

    # ── Tasks ────────────────────────────────────────────────────────────────

    @task(7)
    def browse_public_event(self):
        """GET /events/{id} — public page, no auth, no rate limit pressure."""
        event_id = random.choice(LOAD_EVENT_IDS)
        self.client.get(f"/events/{event_id}", name="Browse public event")

    @task(2)
    def enroll_in_semester(self):
        """POST /semesters/request-enrollment.

        Business error "Already enrolled" (303 with error in location) is
        EXPECTED and counted as success — it means the guard works.
        Real failures are 5xx or unexpected 4xx.
        """
        if not LOAD_SEMESTER_IDS or not self._logged_in:
            return

        sem_id = random.choice(LOAD_SEMESTER_IDS)
        with self.client.post(
            "/semesters/request-enrollment",
            data={"semester_id": str(sem_id)},
            allow_redirects=False,
            name="Enroll semester",
            catch_response=True,
        ) as resp:
            if resp.status_code == 303:
                loc = resp.headers.get("location", "")
                if "error" not in loc:
                    # Success: extract enrollment_id from success redirect if present
                    self._enrolled_id = sem_id  # store sem_id for withdraw
                resp.success()  # 303 with business error = expected, not a failure
            elif resp.status_code == 429:
                resp.success()  # rate limit firing = expected behaviour under load
            else:
                resp.failure(f"Unexpected enroll response: {resp.status_code}")

    @task(1)
    def withdraw_from_semester(self):
        """POST /semesters/withdraw-enrollment (requires prior enrollment).

        Needs enrollment_id — fetched from the browse page or stored from enroll.
        Skipped if no enrollment is known for this user.
        """
        if not self._logged_in or not self._enrolled_id:
            return

        # Fetch enrollment_id from the enroll page (carries session cookie)
        list_resp = self.client.get(
            "/semesters/enroll",
            name="Fetch enrollment list (for withdraw)",
        )
        # Extract first enrollment_id from hidden form inputs in the page
        match = re.search(
            r'name=["\']enrollment_id["\']\s+value=["\'](\d+)["\']',
            list_resp.text,
        )
        if not match:
            return  # no enrollment to withdraw; skip quietly

        enrollment_id = match.group(1)
        with self.client.post(
            "/semesters/withdraw-enrollment",
            data={"enrollment_id": enrollment_id},
            allow_redirects=False,
            name="Withdraw semester",
            catch_response=True,
        ) as resp:
            if resp.status_code == 303:
                self._enrolled_id = None  # reset so next enroll attempt is fresh
                resp.success()
            elif resp.status_code == 429:
                resp.success()
            else:
                resp.failure(f"Unexpected withdraw response: {resp.status_code}")


# ============================================================================
# PHASE 6.3 SCENARIO: Soak + Burst (100-account pool, 5-stage shape)
# ============================================================================

class SoakBurstUser(HttpUser):
    """
    Phase 6.3 — soak + burst load test with 100-account multi-user pool.

    Each VU gets a unique account (round-robin from _LOAD_USERS pool) so that
    concurrent write operations (enroll/withdraw) hit different DB rows,
    avoiding artificial single-row serialization.

    Prerequisites:
      - Run: python scripts/seed_load_test_users.py
      - Set: LOAD_SEMESTER_IDS, LOAD_EVENT_IDS (or use run_phase63_load.sh)

    Task weights (70/20/10 = read-heavy, matches production profile):
      70 %  browse_event    GET  /events/{id}
      20 %  enroll          POST /semesters/request-enrollment
      10 %  withdraw        POST /semesters/withdraw-enrollment

    Use with Phase63LoadShape for full 5-stage soak + burst cycle.
    """

    wait_time = between(0.3, 1.0)  # tighter think-time for higher throughput

    _email: str = ""
    _password: str = ""
    _logged_in: bool = False
    _enrolled_semester_id: int = 0
    _csrf_token: str = ""

    def _get_csrf_token(self) -> str:
        """Return the CSRF token from the session cookie jar (refresh if missing)."""
        token = self.client.cookies.get("csrf_token", "")
        if not token:
            # Trigger a GET to receive the CSRF cookie from the middleware
            self.client.get("/semesters/enroll", name="[P63] Refresh CSRF")
            token = self.client.cookies.get("csrf_token", "")
        return token

    def on_start(self) -> None:
        """Assign a pooled account to this VU, log in, and capture CSRF token."""
        with _USER_LOCK:
            self._email, self._password = next(_USER_CYCLE)

        with self.client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            allow_redirects=False,
            name="[P63] Login",
            catch_response=True,
        ) as resp:
            if resp.status_code in (302, 303):
                self._logged_in = True
            else:
                resp.failure(
                    f"[P63] Login failed: {resp.status_code} ({self._email}) — "
                    f"run scripts/seed_load_test_users.py first"
                )

        # Fetch CSRF token: one GET to receive the csrf_token cookie
        if self._logged_in:
            self.client.get("/semesters/enroll", name="[P63] Init CSRF")
            self._csrf_token = self.client.cookies.get("csrf_token", "")

    # ── Tasks ────────────────────────────────────────────────────────────────

    @task(7)
    def browse_event(self) -> None:
        """GET /events/{id} — public page, no auth required."""
        event_id = random.choice(LOAD_EVENT_IDS)
        self.client.get(f"/events/{event_id}", name="[P63] Browse event")

    @task(2)
    def enroll(self) -> None:
        """POST /semesters/request-enrollment.

        Already-enrolled (303 + error in location) = guard works = success.
        429 = rate limit as expected = success.
        5xx = real failure.
        """
        if not LOAD_SEMESTER_IDS or not self._logged_in:
            return

        sem_id = random.choice(LOAD_SEMESTER_IDS)
        csrf = self._get_csrf_token()
        with self.client.post(
            "/semesters/request-enrollment",
            data={"semester_id": str(sem_id)},
            headers={"X-CSRF-Token": csrf} if csrf else {},
            allow_redirects=False,
            name="[P63] Enroll",
            catch_response=True,
        ) as resp:
            if resp.status_code == 303:
                loc = resp.headers.get("location", "")
                if "error" not in loc:
                    self._enrolled_semester_id = sem_id
                resp.success()
            elif resp.status_code == 429:
                resp.success()  # expected under load
            else:
                resp.failure(f"[P63] Enroll unexpected: {resp.status_code}")

    @task(1)
    def withdraw(self) -> None:
        """POST /semesters/withdraw-enrollment.

        Skipped if this VU has no known active enrollment.
        """
        if not self._logged_in or not self._enrolled_semester_id:
            return

        list_resp = self.client.get(
            "/semesters/enroll",
            name="[P63] Fetch enrollments (pre-withdraw)",
        )
        # Refresh CSRF token from the GET response cookie
        self._csrf_token = self.client.cookies.get("csrf_token", self._csrf_token)

        match = re.search(
            r'name=["\']enrollment_id["\']\s+value=["\'](\d+)["\']',
            list_resp.text,
        )
        if not match:
            return

        enrollment_id = match.group(1)
        csrf = self._csrf_token
        with self.client.post(
            "/semesters/withdraw-enrollment",
            data={"enrollment_id": enrollment_id},
            headers={"X-CSRF-Token": csrf} if csrf else {},
            allow_redirects=False,
            name="[P63] Withdraw",
            catch_response=True,
        ) as resp:
            if resp.status_code == 303:
                self._enrolled_semester_id = 0
                resp.success()
            elif resp.status_code == 429:
                resp.success()
            else:
                resp.failure(f"[P63] Withdraw unexpected: {resp.status_code}")


# ============================================================================
# PHASE 6.3 LOAD SHAPE: 5-stage soak + burst profile
# ============================================================================

class Phase63LoadShape(LoadTestShape):
    """
    5-stage soak + burst load profile (total ~10 min).

    Controlled by LOAD_PEAK_VUS env var (default 1000 local, 50 in CI).
    CI sets LOAD_PEAK_VUS=50 — same 5 stages run, just at scaled VU counts.

    Stages (cumulative end times, VU counts proportional to peak):
      Stage 1 — Warmup:   0   → peak//10,  end at t=30s
      Stage 2 — Soak:     peak//10 (hold), end at t=330s (5 min steady state)
      Stage 3 — Burst:    peak//10 → peak,  end at t=420s (90s ramp)
      Stage 4 — Spike:    peak (hold),      end at t=540s (2 min max stress)
      Stage 5 — Recovery: peak → peak//5,   end at t=600s (60s cooldown)

    Usage without explicit -u / -r flags (shape controls them):
      locust -f locustfile.py SoakBurstUser --headless --run-time 10m \\
             --host http://localhost:8001 --csv results/phase63
    """

    def _stages(self):
        peak = max(2, _LOAD_PEAK_VUS)
        warmup  = max(2, peak // 10)
        recover = max(1, peak // 5)
        return [
            # (end_time_s, target_users, spawn_rate)
            (30,   warmup,   max(1, warmup  // 10)),   # Warmup ramp
            (330,  warmup,   1),                         # Soak hold
            (420,  peak,     max(1, peak    // 9)),     # Burst ramp
            (540,  peak,     1),                         # Spike hold
            (600,  recover,  max(1, peak    // 20)),    # Recovery ramp-down
        ]

    def tick(self):
        run_time = self.get_run_time()
        for end_t, users, spawn_rate in self._stages():
            if run_time < end_t:
                return (users, spawn_rate)
        return None  # all stages complete → stop


# ============================================================================
# PHASE 6.3 ERROR TRACKING LISTENER (429 / 5xx / network split — GATE-2b)
# ============================================================================

@events.request.add_listener
def _track_p63_errors(
    request_type, name, response_time, response_length,
    response, exception, context, **kwargs
):
    """Separate Phase 6.3 errors into 429 / 5xx / network buckets."""
    if not name.startswith("[P63]"):
        return  # only track SoakBurstUser requests
    code = getattr(response, "status_code", 0)
    with _P63_LOCK:
        if code == 429:
            _P63_COUNTERS["rate_limited"] += 1
        elif code >= 500:
            _P63_COUNTERS["server_errors"] += 1
        elif code == 0 and exception:
            _P63_COUNTERS["network_errors"] += 1


# ============================================================================
# PHASE 6.3 DB METRICS PROBE (GATE-4)
# ============================================================================

@events.test_start.add_listener
def _capture_metrics_baseline(environment, **kwargs):
    """Capture Prometheus counters at test start for delta computation."""
    if _stdlib_requests is None:
        return
    try:
        host = environment.host or "http://localhost:8000"
        resp = _stdlib_requests.get(
            f"{host}/metrics?format=prometheus", timeout=5
        )
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.rsplit(" ", 1)
                if len(parts) == 2:
                    try:
                        _P63_METRICS_START[parts[0].strip()] = float(parts[1])
                    except ValueError:
                        pass
    except Exception:
        pass  # server may not be fully up yet; skip silently


# ============================================================================
# EVENT LISTENERS — INIT + STOP
# ============================================================================

@events.init.add_listener
def on_locust_init(environment, **kwargs):
    """Print baseline performance expectations for Phase 5, 6.2, and 6.3."""
    print("=" * 72)
    print("PERFORMANCE BASELINE EXPECTATIONS")
    print("=" * 72)
    print()
    print("── Phase 5 (TournamentCreationUser) ─────────────────────────────────")
    print("  Endpoint: POST /api/v1/tournaments/ops/run-scenario")
    print("  p50 < 500ms | p95 < 1500ms | p99 < 3000ms | RPS > 5 | err < 1%")
    print("  Load: 1-10 concurrent users, 60s")
    print()
    print("── Phase 6.2 (BrowseAndEnrollUser) ──────────────────────────────────")
    print("  70% Browse  GET  /events/{id}                p95 < 100ms  err < 0.5%")
    print("  20% Enroll  POST /semesters/request-enroll  p95 < 500ms  err < 1%")
    print("  10% Withdraw POST /semesters/withdraw-*     p95 < 500ms  err < 1%")
    print()
    print("  Rate limit (429): EXPECTED at > 100 concurrent users (IP limit = 100/60s)")
    print("  DB pool: 50 connections/worker × 4 workers = 200 total")
    print("  503 responses = pool exhaustion (increase pool_size or reduce workers)")
    print()
    peak = _LOAD_PEAK_VUS
    is_ci = bool(os.getenv("CI"))
    print("── Phase 6.3 (SoakBurstUser + Phase63LoadShape) ─────────────────────")
    print(f"  Peak VUs: {peak}  ({'CI-scaled' if is_ci else 'local full load'})")
    print(f"  User pool: {_LOAD_USERS_COUNT} accounts (load-user-0001..{_LOAD_USERS_COUNT:04d}@lfa.com)")
    print("  5-stage profile: warmup→soak(5m)→burst→spike(2m)→recovery  ~10 min")
    print()
    if is_ci:
        print("  CI KPI thresholds (scaled 50 VUs):")
        print("    Browse  [P63] p95 ≤ 500ms   5xx ≤ 2%")
        print("    Enroll  [P63] p95 ≤ 1000ms  5xx ≤ 2%")
        print("    Withdraw [P63] p95 ≤ 1000ms  5xx ≤ 2%")
    else:
        print("  Local KPI thresholds (1000 VUs):")
        print("    Browse  [P63] p95 ≤ 200ms   5xx ≤ 1%")
        print("    Enroll  [P63] p95 ≤ 800ms   5xx ≤ 1%")
        print("    Withdraw [P63] p95 ≤ 800ms   5xx ≤ 1%")
    print()
    print("  GATE-2: p95/p99 + 429/5xx/network split + breaking point VU")
    print("  GATE-4: slow_queries_total Δ + invariant_violations_total = 0")
    print("=" * 72)
    print()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print performance summary, Phase 6.2 + Phase 6.3 bottleneck analysis."""
    stats = environment.runner.stats if environment.runner else None
    print()
    print("=" * 72)
    print("PERFORMANCE TEST COMPLETE")
    print("=" * 72)

    if stats:
        _print_phase62_analysis(stats)

    # Phase 6.3 report fires only if SoakBurstUser data is present
    has_p63 = any(
        name.startswith("[P63]")
        for name, _ in (stats.entries.keys() if stats else [])
    )
    if has_p63 and stats:
        _print_phase63_report(environment)

    print()
    print("Phase 5 next steps:")
    print("  1. Review Locust report (latency percentiles, RPS, error rate)")
    print("  2. Compare actual vs baseline expectations above")
    print("  3. If degradation > 20%: Investigate performance regression")
    print("=" * 72)


# ============================================================================
# PHASE 6.2 HELPERS
# ============================================================================

def _print_phase62_analysis(stats):
    """Phase 6.2 bottleneck analysis — failure modes + mitigation suggestions."""
    print()
    print("── Phase 6.2 Bottleneck Analysis ──────────────────────────────────")

    endpoints = {
        "Browse public event":   ("GET  /events/{id}",                  100, 0.5),
        "Enroll semester":       ("POST /semesters/request-enrollment",  500, 1.0),
        "Withdraw semester":     ("POST /semesters/withdraw-enrollment",  500, 1.0),
        "Login (student web)":   ("POST /login",                         300, 0.5),
    }

    for name, (label, p95_target_ms, err_target_pct) in endpoints.items():
        entry = stats.entries.get((name, "POST")) or stats.entries.get((name, "GET"))
        if entry is None:
            continue

        p95_ms    = entry.get_response_time_percentile(0.95) or 0
        err_pct   = (entry.num_failures / max(entry.num_requests, 1)) * 100
        ok_p95    = p95_ms   <= p95_target_ms
        ok_err    = err_pct  <= err_target_pct

        status = "✅" if (ok_p95 and ok_err) else "⚠️ "
        print(f"  {status} {label}")
        print(f"       p95={p95_ms:.0f}ms (target≤{p95_target_ms}ms)   "
              f"err={err_pct:.2f}% (target≤{err_target_pct}%)")

        if not ok_p95 or not ok_err:
            _print_mitigation(name, p95_ms, p95_target_ms, err_pct)

    print()
    print("── Rate Limit Behaviour ────────────────────────────────────────────")
    print("  IP limit: 100 req / 60s per source IP (sliding window)")
    print("  Per-user limit: NON-FUNCTIONAL (JWT decode TODO stub — see RATELIMIT-02)")
    print("  429s expected at > 100 concurrent users — counted as success in tasks")
    print()
    print("── DB Pool ─────────────────────────────────────────────────────────")
    print("  pool_size=20 + max_overflow=30 = 50 connections / worker")
    print("  With 4 workers: 200 total connections available")
    print("  503 responses → pool exhausted; solution: increase pool_size or add workers")


def _print_mitigation(name: str, p95_actual: float, p95_target: float, err_pct: float):
    mitigations = {
        "Browse public event": (
            "N+1 query risk in events.py — TournamentRanking loads teams per row.\n"
            "       Mitigation: eager-load via joinedload(TournamentRanking.team) in the route.\n"
            "       Also check: index on semester_id + status in Semester table."
        ),
        "Enroll semester": (
            "Bottleneck candidates:\n"
            "       (a) Credit deduction: atomic UPDATE on users row — add index on users.id\n"
            "       (b) Session auto-book: N inserts in create_enrollment_with_bookings()\n"
            "           → use bulk_save_objects() with batch dedup query\n"
            "       (c) SemesterEnrollment unique constraint check — add composite index\n"
            "           on (user_id, semester_id, is_active)"
        ),
        "Withdraw semester": (
            "Likely booking cleanup query is slow under load.\n"
            "       Mitigation: add index on Booking(user_id, status) for batch delete."
        ),
        "Login (student web)": (
            "Bcrypt hash verification is CPU-bound; expected ~100-200ms.\n"
            "       If > 300ms: consider reducing bcrypt rounds in test config (not production)."
        ),
    }
    hint = mitigations.get(name, "No specific mitigation mapped for this endpoint.")
    print(f"       Bottleneck hint: {hint}")


# ============================================================================
# PHASE 6.3 REPORT (GATE-2 + GATE-4 compliant)
# ============================================================================

def _print_phase63_report(environment) -> dict:
    """
    Structured Phase 6.3 bottleneck report — satisfies GATE-2 and GATE-4.

    Returns a dict with all metrics for use by analyze_load_results.py
    when called programmatically after the test.
    """
    stats = environment.runner.stats
    is_ci = bool(os.getenv("CI"))

    # KPI thresholds: CI-tier (50 VUs) vs local (1000 VUs)
    thresholds = {
        "[P63] Browse event":             500  if is_ci else 200,
        "[P63] Enroll":                  1000  if is_ci else 800,
        "[P63] Withdraw":                1000  if is_ci else 800,
    }

    print()
    print("=" * 72)
    print("PHASE 6.3 — BOTTLENECK REPORT  (GATE-2 / GATE-4)")
    print("=" * 72)
    print(f"VU peak: {_LOAD_PEAK_VUS}  |  user pool: {_LOAD_USERS_COUNT} accounts  "
          f"|  env: {'CI' if is_ci else 'local'}")
    print()

    # ── GATE-2a: p95 / p99 per endpoint ─────────────────────────────────────
    print("── p95 / p99 per Endpoint ──────────────────────────────────────────")
    results = {}
    for name, threshold in thresholds.items():
        for method in ("GET", "POST"):
            entry = stats.entries.get((name, method))
            if entry:
                break
        if not entry:
            print(f"  (no data)  {name}")
            results[name] = {"ok": False, "p95": -1, "p99": -1, "threshold": threshold}
            continue
        p95 = entry.get_response_time_percentile(0.95) or 0
        p99 = entry.get_response_time_percentile(0.99) or 0
        ok  = p95 <= threshold
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}")
        print(f"       p95={p95:.0f}ms  p99={p99:.0f}ms  threshold≤{threshold}ms")
        results[name] = {"ok": ok, "p95": p95, "p99": p99, "threshold": threshold}

    # ── GATE-2b: Error split ─────────────────────────────────────────────────
    print()
    print("── Error Split (429 / 5xx / network) ──────────────────────────────")
    total_req = sum(e.num_requests for e in stats.entries.values())
    rl  = _P63_COUNTERS["rate_limited"]
    srv = _P63_COUNTERS["server_errors"]
    net = _P63_COUNTERS["network_errors"]
    rl_pct  = rl  / max(total_req, 1) * 100
    srv_pct = srv / max(total_req, 1) * 100
    net_pct = net / max(total_req, 1) * 100

    print(f"  429 rate limited : {rl:>7}  ({rl_pct:.2f}%)  ← expected under load")
    print(f"  5xx server error : {srv:>7}  ({srv_pct:.2f}%)  ← target ≤ 2% (CI) / 1% (local)")
    print(f"  network error    : {net:>7}  ({net_pct:.2f}%)")
    print(f"  total [P63] reqs : {total_req:>7}")

    srv_threshold = 2.0 if is_ci else 1.0
    server_error_ok = srv_pct <= srv_threshold
    print(f"  {'✅' if server_error_ok else '❌'} 5xx: {srv_pct:.2f}% vs threshold {srv_threshold}%")

    # ── GATE-2c: Breaking point estimate ────────────────────────────────────
    print()
    print("── Breaking Point Estimate ─────────────────────────────────────────")
    if srv_pct > srv_threshold or any(not r["ok"] for r in results.values()):
        print(f"  ⚠️  Breaking point likely ≤ {_LOAD_PEAK_VUS} VUs")
        print(f"       Precise value: run analyze_load_results.py against _stats_history.csv")
    else:
        print(f"  ✅ No breaking point detected at {_LOAD_PEAK_VUS} VUs")
        print(f"       All KPIs within threshold across full 5-stage cycle")

    # ── GATE-4: DB metrics delta ─────────────────────────────────────────────
    print()
    print("── DB Metrics (GATE-4) ─────────────────────────────────────────────")
    db_ok = True
    if _stdlib_requests is None:
        print("  (requests library not available — DB probe skipped)")
    else:
        try:
            host = environment.host or "http://localhost:8000"
            resp = _stdlib_requests.get(
                f"{host}/metrics?format=prometheus", timeout=5
            )
            if resp.status_code == 200:
                current: dict = {}
                for line in resp.text.splitlines():
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.rsplit(" ", 1)
                    if len(parts) == 2:
                        try:
                            current[parts[0].strip()] = float(parts[1])
                        except ValueError:
                            pass
                for key, warn_threshold, label in [
                    ("slow_queries_total",        50, "slow queries (>200ms)"),
                    ("invariant_violations_total",  0, "invariant violations"),
                ]:
                    start_v = _P63_METRICS_START.get(key, 0.0)
                    end_v   = current.get(key, start_v)
                    delta   = end_v - start_v
                    if key == "invariant_violations_total":
                        ok = delta == 0
                        icon = "✅" if ok else "❌ CRITICAL"
                        if not ok:
                            db_ok = False
                    else:
                        ok = delta <= warn_threshold
                        icon = "✅" if ok else "⚠️  CONTENTION"
                    print(f"  {icon} {label}: Δ{delta:.0f}  "
                          f"(start={start_v:.0f} → end={end_v:.0f})")
            else:
                print(f"  (metrics endpoint: {resp.status_code})")
        except Exception as exc:
            print(f"  (DB probe error: {exc})")

    # ── Summary verdict ──────────────────────────────────────────────────────
    print()
    print("── Verdict ─────────────────────────────────────────────────────────")
    all_ok = all(r["ok"] for r in results.values()) and server_error_ok and db_ok
    if all_ok:
        print("  ✅ ALL KPIs WITHIN THRESHOLD — Phase 6.3 VALID")
    else:
        print("  ❌ KPI VIOLATIONS — Phase 6.3 NOT COMPLETE:")
        if not server_error_ok:
            print(f"     [CRITICAL] 5xx rate {srv_pct:.2f}% > {srv_threshold}%")
            print("               → DB pool exhaustion or unhandled exception in route")
        for name, r in results.items():
            if not r["ok"]:
                bottleneck = _p63_bottleneck_hint(name)
                print(f"     [PERF] {name}: p95={r['p95']:.0f}ms > {r['threshold']}ms")
                print(f"            → {bottleneck}")
        if not db_ok:
            print("     [CRITICAL] invariant_violations_total > 0 — data integrity risk")

    print()
    print("Mitigation reference: tests/performance/LOAD_BASELINE.md")
    print("=" * 72)

    return {
        "results": results,
        "server_error_ok": server_error_ok,
        "db_ok": db_ok,
        "counters": dict(_P63_COUNTERS),
        "total_req": total_req,
        "all_ok": all_ok,
    }


def _p63_bottleneck_hint(endpoint_name: str) -> str:
    hints = {
        "[P63] Browse event": (
            "N+1 in tournaments.py:110-117 (1+3N queries). "
            "Fix: joinedload(Semester.enrollments, master_instructor)"
        ),
        "[P63] Enroll": (
            "Capacity check loop (N ROW LOCKs) or credit deduction hot-spot. "
            "Fix: single GROUP BY query for capacity; index sessions(auto_generated)"
        ),
        "[P63] Withdraw": (
            "Booking batch-delete or credit refund contention. "
            "Fix: composite index booking(enrollment_id, user_id)"
        ),
    }
    return hints.get(endpoint_name, "Review slow_queries_total delta in DB metrics section")
