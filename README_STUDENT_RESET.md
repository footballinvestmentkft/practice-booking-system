# Dev DB Reset Checklist

Use this checklist every time you run a destructive DB reset in development.

---

## 1. Full Reset + Bootstrap (single command)

```bash
DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lfa_intern_system" \
  PYTHONPATH=. python scripts/reset_and_bootstrap.py --yes
```

This runs automatically:
- `DROP SCHEMA public CASCADE` + `CREATE SCHEMA public`
- `alembic upgrade head` — recreates all tables
- `bootstrap_clean.py` Steps 1–8:
  - TournamentType (4 rows), GamePreset (5 rows), Location + Campus, Pitches, Admin, Instructor, Bootstrap Club
  - **Step 8: VirtualTrainingGame (12 rows, 6 active)** — included since 2026-05-27
- Post-reset validation: hard fail if `virtual_training_games` is empty

No manual seed step required for VT data.

---

## 2. Validate Seed State

```bash
PYTHONPATH=. python scripts/validate_seed_state.py
```

Checks (7 total):
1. TournamentType ≥ 4 rows
2. GamePreset ≥ 3 rows
3. Campus ≥ 1 active row
4. Admin user present
5. Instructor user present
6. Club with active teams + players
7. **VirtualTrainingGame: ≥ 1 active, memory_sequence + target_tracking both active**

Exits 0 on success, 1 with details on failure.

---

## 3. Optional: Dev Friendship Seed

Creates ACCEPTED friendship pairs between bootstrap LFA Adult players.
Required for testing the Friends + Challenge flow in a clean environment.

```bash
PYTHONPATH=. python scripts/seed_dev_friendships.py
```

- Idempotent: safe to run multiple times
- 4 pairs from LFA Adult bootstrap team
- PENDING → ACCEPTED upgrade, DECLINED → ACCEPTED replace
- Output: `N created, N upgraded, N skipped`

---

## 4. Start the App

```bash
# Terminal 1 — FastAPI
BG_REMOVAL_PROCESSOR=rembg uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 --reload --reload-dir app

# Terminal 2 — Celery (if using rembg background removal)
BG_REMOVAL_PROCESSOR=rembg celery -A app.celery_app worker \
  -Q mood_photos -c 1 -l info --pool=solo
```

---

## 5. Quick Sanity Checks

| URL | Expected |
|-----|----------|
| `/training` | Virtual Games link visible under Adaptive Learning |
| `/virtual-training` | 6 game cards (Color Reaction, Go/No-Go, etc.) |
| `/challenges/send` | memory_sequence + target_tracking selectable; friend list populated (after friendship seed) |
| `/friends` | ACCEPTED pairs visible (after friendship seed) |

---

## Notes

- `virtual_training_games` is **reference data** — same as `TournamentType` or `GamePreset`. The app cannot function without it.
- If the Virtual Games link is missing on `/training`, run `validate_seed_state.py` — it will tell you exactly what is missing.
- The friendship seed uses only bootstrap-guaranteed users. It does NOT require `rdias@manchestercity.com` or other dev-only seeds.
