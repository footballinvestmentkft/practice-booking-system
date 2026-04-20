# Demo Scenarios

Canonical demo seeds for stakeholder presentations and local development walkthroughs.

## Quick start

```bash
# From the project root:
python scripts/demo/run_demo.py           # full (default)
python scripts/demo/run_demo.py skill
python scripts/demo/run_demo.py events
python scripts/demo/run_demo.py minimal
```

---

## Scenarios

### `full` — Full platform walkthrough
**File:** `reset_full.py` (copy of `scripts/reset_and_seed_full.py`)

| Property | Value |
|----------|-------|
| **Destructive** | YES — drops and recreates the entire schema |
| **Idempotent** | YES — always produces the same state |
| **Runtime** | ~30–60 s |
| **Recommended for** | First-time setup, stakeholder demos, investor walkthroughs |

**What it creates:**
- 2 locations: Budapest (CENTER), Debrecen (PARTNER)
- 10 users: 1 admin, 1 grandmaster/SD, 4 Budapest students, 4 Debrecen students
- 13 events: 1 completed tournament per city (with placement + skill delta), 2 open tournaments, 2 camps, 1 mini-season, 1 academy season per city
- Full enrollment matrix (APPROVED + payment_verified for every event-user pair)
- Invitation codes: 13 event-specific + 4 general unredeemed, 2 redeemed

**Credentials:**

| Role | Email | Password |
|------|-------|----------|
| Admin | `admin@lfa.com` | `Admin1234!` |
| Grandmaster / Sport Director | `grandmaster@lfa.com` | `Admin1234!` |
| Student (Budapest) | `kovacs.peter@lfa-bdpst.hu` | `Player1234!` |
| Student (Budapest) | `nagy.balazs@lfa-bdpst.hu` | `Player1234!` |
| Student (Budapest) | `horvath.daniel@lfa-bdpst.hu` | `Player1234!` |
| Student (Budapest) | `szabo.adam@lfa-bdpst.hu` | `Player1234!` |
| Student (Debrecen) | `fekete.tamas@lfa-debr.hu` | `Player1234!` |
| Student (Debrecen) | `varga.laszlo@lfa-debr.hu` | `Player1234!` |
| Student (Debrecen) | `kiss.gabor@lfa-debr.hu` | `Player1234!` |
| Student (Debrecen) | `toth.bence@lfa-debr.hu` | `Player1234!` |

---

### `skill` — Skill progression arc
**File:** `seed_skill_progression.py` (copy of `scripts/seed_full_playable.py`)

| Property | Value |
|----------|-------|
| **Destructive** | NO — additive; safe on any existing DB state |
| **Idempotent** | YES — keyed by unique codes; re-running is safe |
| **Runtime** | ~10–20 s |
| **Recommended for** | EMA algorithm demos, skill report walkthroughs, technical audiences |
| **Prerequisite** | `report_*` users must exist — run `seed_report_users_login.py` first if they are missing, or run `full` which creates all users from scratch |

**What it creates:**
- Activates 4 archetype `report_*` players with distinct skill profiles
- 3 completed historical tournaments (Jan–Mar 2026): league, score-based, time-based
- EMA placement arc: T1 (940c wins) → T2 (7b85 wins) → T3 (490c wins)
- `TournamentParticipation` rows with `skill_rating_delta` written by the EMA engine
- 3 ENROLLMENT_OPEN tournaments + 2 ENROLLMENT_OPEN camps for live enrollment demo

**Player archetypes:**

| Email | Archetype | Highlight skill |
|-------|-----------|----------------|
| `report_940c5c73@t.com` | Shooter / attacking specialist | `finishing` 81, `shot_power` 78 |
| `report_7b85cdfa@t.com` | Playmaker / passer | `passing` 80, `vision` 78 |
| `report_490c3e64@t.com` | All-rounder — best development arc | Balanced (70–75 range) |
| `report_9ab12d42@t.com` | Developing player (beginner) | All skills 48–63 |

**Credentials:** All 4 players: password `Player1234!`

**Best demo entry point:** `report_490c3e64@t.com / Player1234!` — shows the most dramatic upward arc (3rd → 2nd → 1st across 3 tournaments).

---

### `events` — Frontend event calendar
**File:** `seed_events.py` (copy of `scripts/seed_events_demo.py`)

| Property | Value |
|----------|-------|
| **Destructive** | PARTIAL — truncates operational tables (semesters, sessions, campuses, locations, enrollments, tournament data) but **preserves user accounts** |
| **Idempotent** | YES — truncate → recreate always produces same state |
| **Runtime** | ~5–15 s |
| **Recommended for** | UX / design walkthroughs, frontend calendar demo, session scheduling review |

**What it creates:**
- 2 locations: Budapest (CENTER), Debrecen (PARTNER)
- 4 campuses: 3 × Budapest, 1 × Debrecen
- 10 semesters: 3 Academy, 4 Tournament, 3 Camp
- 33 sessions: 16 MATCH, 17 TRAINING

**Credentials:** 8 demo players created (if not already present):
`demo.youth.player1@lfa-seed.hu` through `demo.youth.player8@lfa-seed.hu` — password `Player123!`

---

### `minimal` — Quick-start (live demo safe)
**File:** `seed_minimal.py` (copy of `scripts/seed_minimum_playable.py`)

| Property | Value |
|----------|-------|
| **Destructive** | NO — purely additive, never truncates |
| **Idempotent** | YES — all objects keyed by unique codes |
| **Runtime** | ~3–8 s |
| **Recommended for** | Live demos when DB already has users; fast "add open events" without reset; quick local re-seeding |
| **Prerequisite** | `report_*` users must exist (same as `skill`) |

**What it creates:**
- Fixes `report_*` user licenses: 29 football skills, `onboarding_completed=True`, `credit_balance=900`
- 1 Location + 1 Campus (reuses existing if already present)
- 3 ENROLLMENT_OPEN tournaments: league H2H, score-based, time-based
- 2 ENROLLMENT_OPEN camps

**Credentials:** `report_490c3e64@t.com / Player1234!` (and the other 3 `report_*` players)

---

## Scenario decision guide

```
Is this the first run on a fresh DB?
  → full

Do you need to show EMA skill progression / skill report?
  → skill  (or full — it includes a completed tournament too)

Is the DB already set up with users and you only need open events?
  → minimal

Are you validating frontend UI / calendar layout only?
  → events
```

---

## File structure

```
scripts/demo/
├── README.md                    ← this file
├── run_demo.py                  ← single dispatcher entrypoint
├── reset_full.py                ← copy of scripts/reset_and_seed_full.py
├── seed_skill_progression.py    ← copy of scripts/seed_full_playable.py
├── seed_events.py               ← copy of scripts/seed_events_demo.py
└── seed_minimal.py              ← copy of scripts/seed_minimum_playable.py
```

The originals in `scripts/` are unchanged. If the original is updated, re-copy it here.

---

## Naming convention

| Prefix | Purpose | Location |
|--------|---------|----------|
| `demo/reset_*.py` | Destructive full-reset demo seeds | `scripts/demo/` |
| `demo/seed_*.py` | Additive/idempotent demo seeds | `scripts/demo/` |
| `demo/run_demo.py` | Single scenario dispatcher | `scripts/demo/` |
| `reset_e2e_web_db.py` | CI scenario fixtures | `scripts/` root |
| `validate_*.py` / `audit_*.py` | Operational verification tooling | `scripts/` root |
| `maintenance/` | Long-term DB maintenance | `scripts/maintenance/` |
