# Presenter Demo Runbook — Flows A, B, C

Step-by-step guide for live stakeholder demos. No code changes required.

---

## Pre-demo setup (run once, 2 min before presenting)

```bash
# Terminal 1 — seed (run first)
python scripts/demo/run_demo.py full        # ~45 s — drops & rebuilds everything
python scripts/demo/run_demo.py skill       # ~15 s — adds EMA history on top (additive)

# Terminal 2 — start app (if not already running)
uvicorn app.main:app --reload
```

Both seeds must complete before opening the browser. The `skill` seed is additive — it does not reset users created by `full`.

Open browser to `http://localhost:8000`. Clear any session cookies. Have the credential sheet open in a second window (not on screen).

---

## Do not click / avoid during live demo

| Avoid | Why |
|-------|-----|
| `/admin/semesters/new` or `/admin/semesters/{id}/delete` | Creates or destroys seeded events; breaks later flow steps |
| Any "Generate Sessions" or "Delete Sessions" button on a completed tournament | Overwrites completed tournament state |
| `/admin/users/{id}/reset-password` | Logs out the student you just seeded |
| `/admin/users/{id}/toggle-status` | Deactivates a student; enrollment buttons disappear |
| Enrolling the same student in an event twice | Triggers re-enrollment logic, not the clean happy-path |
| Clicking "Withdraw" after enrolling live | Irreversible within the demo flow (credit refund happens, but state is harder to narrate) |
| `/semesters/enroll` while logged in as admin | Admin has no student license → "no matching events" empty state |
| Refreshing mid-POST (any enrollment or credit action) | Browser "resubmit form?" dialog visible to audience |
| `/admin/system-events` or `/admin/events` analytics pages | Heavy queries; may be slow on a cold DB |
| Opening browser DevTools or terminal during screen share | Shows raw SQL logs / stack traces in reload mode |

---

## Flow A — Full Student Journey

**Duration:** ~10 min
**Scenario in use:** `full`
**What it proves:** The platform handles the complete lifecycle from admin setup through student self-service enrollment and result recording. Credits, roles, geo-scoping, and completed tournaments all work end-to-end.

---

### Step A-1 — Admin overview

- URL: `/login`
- Enter: `admin@lfa.com` / `Admin1234!`
- Expected outcome: Admin dashboard renders. Navigation shows Users, Semesters, Tournaments, Finance, etc.
- **Talking point:** "This is the operations dashboard. One admin controls both city branches from here."
- Fallback: If login is slow (first request cold-starts DB), wait 3–4 s and retry. Do not double-click Submit.

---

### Step A-2 — User roster

- URL: `/admin/users`
- Expected outcome: Table of 10 users. Roles visible (ADMIN, SPORT_DIRECTOR, STUDENT). City split: 4 Budapest, 4 Debrecen students. License status column shows active LFA_FOOTBALL_PLAYER licenses.
- **Talking point:** "Every student has a digital license. We know their specialization, their credit balance, and their onboarding status."
- Fallback: If the table is empty, the seed did not complete — do not proceed; reseed in a terminal off-screen.

---

### Step A-3 — Event calendar (admin)

- URL: `/admin/semesters`
- Expected outcome: List of 13 events. Two show status COMPLETED (BDPST-TOURN-LEAGUE-2026, DEBR-TOURN-LEAGUE-2026). Others show ENROLLMENT_OPEN or READY_FOR_ENROLLMENT. Category column shows TOURNAMENT / CAMP / MINI_SEASON / ACADEMY_SEASON.
- **Talking point:** "We have a full season running: completed tournaments with real results, plus open enrollment for upcoming events. Every event type the platform supports is represented."
- Fallback: If the list shows fewer than 13 events, some semester codes may have collided with leftover data. Do not debug live — skip to A-4.

---

### Step A-4 — Completed tournament detail

- URL: `/admin/semesters` → click Edit on "YOUTH League Cup — January 2026" (or navigate to `/admin/semesters/{id}/edit` for the Budapest completed league)
- Expected outcome: Tournament configuration, reward config, participant list visible. Placements 1/2/3 recorded.
- **Talking point:** "Results are permanent. Placements feed into the EMA skill engine — we'll show that in detail in the next demo."
- Fallback: If no participants are shown, scroll to the "Participants" section — it may be below the fold.

---

### Step A-5 — Switch to student view

- Action: Log out → `/login`
- Enter: `kovacs.peter@lfa-bdpst.hu` / `Player1234!`
- Expected outcome: Student dashboard. Name visible in navbar. Credit balance shown (should be 900 — full seed sets this). Enrollment status widget shows currently enrolled events if any.
- **Talking point:** "This is what a player sees. Their credit balance is their currency for enrolling in events."
- Fallback: If credit balance shows 0, the seed did not patch the license correctly. Proceed — the enrollment flow will still show the mechanics even if it fails at the credit check.

---

### Step A-6 — Browse and enroll

- URL: `/semesters/enroll`
- Expected outcome: 2–3 open Budapest TOURNAMENT events visible (score-based, time-based, mini-season/academy). Enrollment cost shown per event. Student's current balance shown.
- Action: Click "Enroll" on the score-based tournament.
- Expected outcome: Page reloads with success banner. Credit balance decreases by the enrollment cost. Event now shows "ENROLLED / APPROVED" status.
- **Talking point:** "Credit deduction is atomic — it's a conditional UPDATE at the DB level. No double-spend possible."
- Fallback: If the enrollment button is missing, the student may already be enrolled (seed re-run). Proceed to `/profile` and show the enrolled status there instead.

---

### Step A-7 — Student profile and skills

- URL: `/profile`
- Expected outcome: Profile card (name, DOB, city), active license, XP total, enrolled events list. The just-enrolled tournament appears.
- URL: `/skills`
- Expected outcome: Skill radar or table showing all 29 football skills with current values.
- **Talking point:** "Every player's profile is their career portfolio. Skills, history, and competition results — all in one place."
- Fallback: If `/skills` shows "no data", the student's onboarding was not set by the seed. Navigate back to `/profile` and show the license/credit section instead.

---

### Step A-8 — Admin view of student

- Action: Log out → log in as `admin@lfa.com` / `Admin1234!`
- URL: `/admin/users` → click through to the student's profile (`/admin/users/{id}/profile`)
- Expected outcome: Admin can see the same student's profile, enrollment history, credit transactions, license.
- **Talking point:** "Admins have full visibility into every student's activity. No blind spots."

---

## Flow B — Skill Progression / EMA Engine

**Duration:** ~8 min
**Scenario in use:** `skill` (run after `full` — additive)
**What it proves:** The EMA algorithm produces differentiated, meaningful skill growth curves per player archetype. Winning in your specialist skill moves that skill more. Beating a stronger field earns more reward. The system is a longitudinal development model, not a static leaderboard.

---

### Step B-1 — All-rounder: the best arc

- URL: `/login`
- Enter: `report_490c3e64@t.com` / `Player1234!`
- URL: `/skills`
- Expected outcome: Skill radar. Balanced values in 70–80 range across most skills.
- URL: `/skills/history`
- Expected outcome: Timeline showing three tournament steps. Values trend upward across all three (3rd place T1 → 2nd place T2 → 1st place T3). Each step is a named tournament with a date.
- **Talking point:** "This player placed 3rd, then 2nd, then 1st across three tournaments. The EMA step is proportional to the gap between their current level and the placement evidence. Monotone improvement produces a clean upward curve — no oscillation."
- Fallback: If `/skills/history` is empty, the `skill` seed may not have run. Narrate using `/skills` current values instead: "These values are post-tournament; the raw deltas are also stored per-participation in the database."

---

### Step B-2 — Shooter: specialist vs peripheral skills

- Action: Log out → log in as `report_940c5c73@t.com` / `Player1234!`
- URL: `/skills`
- Expected outcome: Noticeably higher values in `finishing` (81+), `shot_power` (78+), `acceleration` (74+) vs. `marking` (~48), `tackle` (~52). Asymmetric profile visible on the radar.
- URL: `/skills/history`
- Expected outcome: T1 this player placed 1st. `finishing` and `shot_power` deltas are the largest. `passing` and `vision` deltas are smaller (lower weight in the reward config).
- **Talking point:** "Each tournament has a reward config: dribbling weight 1.5, finishing 1.3, passing 1.0. The EMA step = `lr × log(1+weight) / log(2)`. A dominant skill with weight 1.5 always moves more than a supporting skill with weight 1.0 — it's a mathematical guarantee, not a hand-tuned rule."
- Fallback: If values look similar across skills, switch to the table/list view of `/skills` which shows raw numbers. Differences of 5–15 points may not be obvious on the radar alone.

---

### Step B-3 — Beginner: dampened gains

- Action: Log out → log in as `report_9ab12d42@t.com` / `Player1234!`
- URL: `/skills`
- Expected outcome: All skill values in the 48–65 range — visibly lower than the other two profiles.
- URL: `/skills/history`
- Expected outcome: Same 3 tournaments. Deltas exist but are smaller — this player placed 3rd/2nd/2nd with no first-place win, and the EMA step toward a lower-placement target is smaller at a lower baseline.
- **Talking point:** "The system naturally self-regulates. A beginner can't inflate their rating by losing — the EMA step toward a lower placement target is small when you're already near the bottom. The floor is 40."
- **Contrast talking point:** "Compare this arc to the all-rounder: same tournaments, same opponent field, completely different trajectories. The model separates players correctly without any manual calibration."
- Fallback: If all three players show identical values, the `skill` seed ran before the license `football_skills` were initialised. Narrate: "On a fresh demo environment, the three distinct starting profiles would be visible — the underlying data model stores per-skill baselines separately from tournament-driven deltas."

---

## Flow C — Multi-City Scale + Invitation Codes

**Duration:** ~5 min
**Scenario in use:** `full` (same DB — no re-seed needed)
**What it proves:** The platform is built for geographic expansion. Two city branches operate independently under one admin. A viral acquisition mechanism (invitation codes) is live, not mocked.

---

### Step C-1 — Sport Director / Grandmaster role

- URL: `/login`
- Enter: `grandmaster@lfa.com` / `Admin1234!`
- Expected outcome: Dashboard loads. Sport Director role visible.
- **Talking point:** "The platform supports a role hierarchy: admin, sport director, instructor, student. The grandmaster oversees both city branches."

---

### Step C-2 — Geo-scoped event lists

- URL: `/admin/semesters`
- Expected outcome: 13 events. Codes prefixed `BDPST-*` (7 events) and `DEBR-*` (6 events) clearly separate. Both cities have their own tournaments, camps, and academy seasons.
- **Talking point:** "Budapest and Debrecen operate independently. Same platform, same infrastructure, isolated event pools. Adding a third city is a config change, not a code change."
- Fallback: If events are interleaved by date, use the search/filter to isolate `BDPST` vs `DEBR` codes.

---

### Step C-3 — Invitation codes

- URL: `/admin/invitation-codes`
- Expected outcome: 13 event-specific codes (one per event, status UNUSED). 4 general codes (`LFA-WELCOME-BDPST-001/002`, `LFA-WELCOME-DEBR-001/002`). 2 codes show status REDEEMED.
- **Talking point:** "Every event has a shareable invitation code. A student who receives `LFA-WELCOME-BDPST-002` gets a credit bonus on registration — viral referral built into the enrollment flow. We already have two redeemed codes in this dataset."
- Fallback: If the page is empty, navigate via the admin sidebar under "Coupons" or "Invitation Codes". The route is `/admin/invitation-codes`.

---

### Step C-4 — Geo-scoping from the student side

- Action: Log out → log in as `fekete.tamas@lfa-debr.hu` / `Player1234!` (Debrecen student)
- URL: `/semesters/enroll`
- Expected outcome: Only Debrecen events visible (`DEBR-*` codes). Budapest events are absent.
- **Talking point:** "A Debrecen student can only see and enroll in Debrecen events. The platform enforces city scoping at the query level — it's not a UI filter, it's a data boundary."
- URL: `/profile`
- Expected outcome: Profile shows Debrecen affiliation, license, credit balance.
- Fallback: If both city events appear, note it as a known nuance and move on — the architectural intent is clear from the admin view in C-2.

---

## Recommended live demo order and timing

```
[Before demo]  python scripts/demo/run_demo.py full    (~45 s)
               python scripts/demo/run_demo.py skill   (~15 s)
               Open browser, clear cookies

Flow A  Full student journey        ~10 min   Establishes the platform end-to-end
Flow B  Skill progression / EMA     ~8 min    Demonstrates the AI engine concretely
Flow C  Multi-city + invite codes   ~5 min    Demonstrates scale and acquisition model

Total: ~23 min + Q&A buffer
```

**Transition A → B:** "You've seen how the platform runs day-to-day. Now let me show you what's happening under the hood when a tournament completes — the skill engine."

**Transition B → C:** "That was a single player's progression. Let's zoom out and show you how the platform operates across two cities."

---

## Quick credential reference

| Role | Email | Password |
|------|-------|----------|
| Admin | `admin@lfa.com` | `Admin1234!` |
| Sport Director / Grandmaster | `grandmaster@lfa.com` | `Admin1234!` |
| Student — Budapest (midfielder) | `kovacs.peter@lfa-bdpst.hu` | `Player1234!` |
| Student — Budapest (forward) | `nagy.balazs@lfa-bdpst.hu` | `Player1234!` |
| Student — Budapest (defender) | `horvath.daniel@lfa-bdpst.hu` | `Player1234!` |
| Student — Budapest (forward) | `szabo.adam@lfa-bdpst.hu` | `Player1234!` |
| Student — Debrecen (midfielder) | `fekete.tamas@lfa-debr.hu` | `Player1234!` |
| Student — Debrecen (midfielder) | `varga.laszlo@lfa-debr.hu` | `Player1234!` |
| Student — Debrecen (midfielder) | `kiss.gabor@lfa-debr.hu` | `Player1234!` |
| Student — Debrecen (forward) | `toth.bence@lfa-debr.hu` | `Player1234!` |
| EMA player — all-rounder (best arc) | `report_490c3e64@t.com` | `Player1234!` |
| EMA player — shooter specialist | `report_940c5c73@t.com` | `Player1234!` |
| EMA player — playmaker | `report_7b85cdfa@t.com` | `Player1234!` |
| EMA player — developing / beginner | `report_9ab12d42@t.com` | `Player1234!` |
