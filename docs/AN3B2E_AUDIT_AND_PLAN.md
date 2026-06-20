# AN-3B2E / PR-3 — XP és Credit Reward for Train AI (Ball Annotation)
## Teljes Audit és Implementációs Terv (v2 — policy + concurrency korrekciókkal)

**Dátum:** 2026-06-20  
**Státusz:** AUDIT COMPLETE — PR-3A implementáció JÓVÁHAGYVA  
**Branch:** `feat/an3b2e-pr3a-annotation-reward`

---

## 1. Meglévő infrastruktúra audit

### 1.1 XP infrastruktúra

**`award_xp(db, user_id, xp_amount, reason, idempotency_key, transaction_type, semester_id)`**  
`app/services/gamification/xp_service.py`

- Atomic `UPDATE users SET xp_balance = xp_balance + :delta RETURNING xp_balance`
- `XPTransaction` ledger sor; `idempotency_key` egyedi partial index (`rw01concurr00` migration)
- Savepoint IntegrityError catch → silent skip duplikátumoknál
- `UserStats.total_xp` aggregate + level recompute (`total_xp // 1000 + 1`)
- Belülről `db.commit()`-ot hív — emiatt a reward service-ben **nem** hívjuk közvetlenül; az inline implementáció biztosítja, hogy az advisory lock az egész cap-check + write szekvenciát lefedi

**`XPTransaction`** — `app/models/xp_transaction.py`  
`transaction_type` → DB-ben `String(50)`, szabad szöveg; **no migration needed**

### 1.2 Credit infrastruktúra

**`CreditService.create_transaction()`** — `app/services/credit_service.py`  
Check-first idempotency + `idempotency_key unique=True` hard DB constraint.  
`CreditTransaction.transaction_type` → DB-ben `String(50)`; Python enum bővítés; **no ALTER TYPE**

**Új TransactionType**: `BALL_ANNOTATION_REWARD = "BALL_ANNOTATION_REWARD"`

### 1.3 Ball feedback / assignment infrastruktúra

- `JugglingBallFeedback.created_at` — létezik (`DateTime(timezone=True)`, indexed)
- `BallFeedbackDecision`: confirm | reject | no_ball | corrected
- `BallFeedbackApprovalState`: pending | approved | needs_review | rejected | spam
- `UserAnnotationReliability.ball_annotation_reliability` (Float, default=0.5, nullable=False) — lazy-create, soha nem NULL a reward service-ben

### 1.4 Consensus task — gap azonosítva

`run_compute_frame_consensus()` beállítja `approval_state` → approval_state update után nincs XP/credit hook.  
**PR-3A bővíti** a consensus taskot az accuracy bonus hívásával.

---

## 2. Reward policy (végleges)

### Upfront reward (beküldéskor)

| Decision | XP | Credit | Feltétel |
|---|---|---|---|
| `confirm` | 5 XP | 0 CR | eligibility check ✓, napi cap alatt |
| `no_ball` | 5 XP | 0 CR | eligibility check ✓, napi cap alatt |
| `corrected` | 10 XP | 0 CR | eligibility check ✓, napi cap alatt |
| `reject` (B1) | 0 | 0 | — |
| skip (client) | 0 | 0 | nem kerül backendre |

**Credit upfront NINCS** — a reliability ≥ 0.4 csak eligibility előfeltétel; a tényleges credit kiadás
consensus approval után történik (posterior).

### Posterior reward (consensus jóváhagyás után)

| Esemény | XP | Credit |
|---|---|---|
| `approval_state = "approved"` (standard) | +5 XP | 0 CR |
| `approval_state = "approved"` + `is_gold_standard=True` | +5+10 = +15 XP | 0 CR |
| `approved` + `decision = "corrected"` + reliability ≥ 0.4 + napi credit cap alatt | +5 XP | +1 CR |
| `approval_state = "rejected"` | 0 XP | 0 CR (büntetés nincs) |
| `approval_state = "spam"` | 0 XP | 0 CR |

A posterior XP és credit idempotency kulcsai külön kulcson állnak (§7).

---

## 3. Spam és farming protection

### Eligibility check sorrend (upfront, award előtt)

1. **Napi submission cap**: `daily_count >= BALL_ANNOTATION_MAX_TASKS_PER_DAY` → `xp=0, credit=0`
2. **Ismert spammer**: `UserAnnotationReliability.spam_flags_count >= 10` → `xp=0, credit=0`
3. **Napi XP cap**: `daily_xp >= BALL_ANNOTATION_MAX_XP_PER_DAY` → `xp=0`
4. **Rapid-submit flag**: ha az utolsó 60s-ban >5 feedback volt, `"rapid_submit"` flag kerül a `spam_flags`-be. **Nem blokkolja a jutalmat** — admin-review jelzése.

### Pre-award spam check fontossága

Az `approval_state` beküldéskor mindig `pending` — ezért az eligibility ellenőrzés `approval_state`-alapú tiltást nem végez upfrontban. Az `approval_state = "spam"` feldolgozása a posterior útvonalra vonatkozik.

### Post-submission spam kezelés

Ha egy feedback később `spam`-re kerül:
- Posterior jutalom és credit nem jár (`approval_state != "approved"` ág nem fut le).
- MVP-ben nem lesz automatikus XP levonás (retroaktív visszavonás).
- Az audit trail az `XPTransaction` és `CreditTransaction` sorokon keresztül ellenőrizhető.

---

## 4. Napi cap — pontos viselkedés

```
BALL_ANNOTATION_MAX_XP_PER_DAY = 100     # upfront + posterior annotation XP együtt
BALL_ANNOTATION_MAX_TASKS_PER_DAY = 30   # submission count cap (reward nélküli submittek is 201-et adnak)
BALL_ANNOTATION_MAX_CORRECTED_CREDIT_PER_DAY = 10
```

**Részleges reward** (partial cap): ha a napi XP 95/100, és egy 10 XP-s reward jönne, csak 5 XP-t adunk:
```python
remaining_xp = max(0, settings.BALL_ANNOTATION_MAX_XP_PER_DAY - daily_xp)
xp_to_award = min(base_xp, remaining_xp)
```

**Shared cap (upfront + posterior együtt)**: A `get_daily_annotation_stats()` az összes
`BALL_ANNOTATION_XP`, `BALL_ANNOTATION_XP_CORRECTED`, `BALL_ANNOTATION_ACCURACY_BONUS`
típusú `XPTransaction` összegét számolja a mai napra.

**Credit cap az XP captől független**: 100 XP cap elérése nem blokkol egy jóváhagyott creditetet,
ha a credit saját napi capje (10 CR) még nem telt be.

**Feedback 30+ task után**: a backend 201-et ad vissza (`xp_awarded=0, credit_awarded=0`).

---

## 5. Napi limit

Config (Python-only, `app/config.py`):

```python
BALL_ANNOTATION_XP_BASE: int = 5           # confirm, no_ball upfront
BALL_ANNOTATION_XP_CORRECTED: int = 10     # corrected upfront
BALL_ANNOTATION_XP_ACCURACY_BONUS: int = 5 # posterior: approved feedback
BALL_ANNOTATION_XP_GOLD_BONUS: int = 10    # posterior: gold standard addíció
BALL_ANNOTATION_MAX_XP_PER_DAY: int = 100  # upfront + posterior együtt
BALL_ANNOTATION_MAX_TASKS_PER_DAY: int = 30
BALL_ANNOTATION_MAX_CORRECTED_CREDIT_PER_DAY: int = 10
BALL_ANNOTATION_MIN_RELIABILITY_FOR_CREDIT: float = 0.4
BALL_ANNOTATION_RAPID_SUBMIT_WINDOW_S: int = 60
BALL_ANNOTATION_RAPID_SUBMIT_THRESHOLD: int = 5
BALL_ANNOTATION_SPAM_FLAG_BLOCK_THRESHOLD: int = 10
```

---

## 6. Race condition védelem

### Mechanizmus: PostgreSQL advisory lock (tranzakcióhatókör)

```python
raw = f"ball_annotation_reward_{user_id}".encode()
lock_key = int(hashlib.sha256(raw).hexdigest()[:8], 16) % (2**31 - 1)
db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})
```

- Azonos mintát követ mint a `ball_training_service._get_or_create_assignment()`
- A lock a tranzakció végéig él (`db.commit()` felszabadítja)
- Concurrent reward requestek sorba állnak ennél a locknál — nincs retry logika szükséges

### Atomic szekvencia (upfront és posterior egyaránt)

```
1. Advisory lock (user_id)
2. get_daily_annotation_stats()  ← napi állapot kiolvasása a lockon belül
3. Részleges jutalom kiszámítása
4. Inline XP update (raw SQL + XPTransaction savepoint)
5. Inline credit update ha posterior (raw SQL + CreditTransaction savepoint)
6. UserStats aggregate update
7. db.commit()  ← lock felszabadítása
```

Mivel az `award_xp()` belülről `db.commit()`-ot hív, a reward service **nem** hívja — az inline
implementáció biztosítja, hogy a lock az egész szekvenciát lefedi.

### Tesztelt párhuzamos esetek

- **BAR-CC-1**: 5 concurrent upfront submit, 1 user → max 100 XP cap nem lép túl
- **BAR-CC-2**: same `feedback_id` consensus task 2× futtatva → XP és credit nem duplikálódik

---

## 7. Assignment-level idempotency

| Award | Idempotency key |
|---|---|
| Upfront XP | `f"ball_annotation_xp_{assignment_id}"` |
| Posterior XP | `f"ball_annotation_accuracy_{feedback_id}"` |
| Posterior credit | `f"ball_annotation_credit_{feedback_id}"` |

Kulcsok: `String(255)`. Az `assignment_id` és a `feedback_id` UUID4 formátumú stringek.

`XPTransaction.idempotency_key` partial unique index → savepoint IntegrityError catch (duplikátum esetén silent skip).  
`CreditTransaction.idempotency_key` full unique index → check-first (advisory lock alatt, concurrent safe).

---

## 8. Reliability kezelés

- Default: `UserAnnotationReliability.ball_annotation_reliability = 0.5` (DB default, lazy-create → soha nem NULL)
- **Upfront eligibility**: `user_reliability_at_submit` (a submitkori snapshot) van mentve a `JugglingBallFeedback`-en — ez az alap
- **Posterior credit feltétel**: `feedback.user_reliability_at_submit >= BALL_ANNOTATION_MIN_RELIABILITY_FOR_CREDIT`
- **Retroaktív módosítás**: a reliability later változása **nem** módosítja a már kiadott rewardot — a submit-time snapshot rögzített

---

## 9. Backend service és adat modell

### Új fájl: `app/services/juggling/ball_annotation_reward_service.py`

```python
# Nyilvános interface:

_ANNOTATION_XP_TYPES = frozenset({
    "BALL_ANNOTATION_XP",
    "BALL_ANNOTATION_XP_CORRECTED",
    "BALL_ANNOTATION_ACCURACY_BONUS",
})

def get_daily_annotation_stats(db, user_id) -> tuple[int, int, int]:
    """(daily_task_count, daily_xp_earned, daily_credits_earned) — mai UTC napra."""

def compute_upfront_reward(decision, daily_count, daily_xp, reliability, config) -> int:
    """Pure function. Returns xp_to_award (0 ha cap)."""

def award_annotation_upfront(db, user_id, assignment_id, decision, reliability) -> tuple[int, int]:
    """Upfront XP. Returns (xp_awarded, 0). Advisory lock + partial reward."""

def award_annotation_accuracy_bonus(db, feedback_id, user_id, decision, is_gold_standard, reliability_at_submit) -> tuple[int, int]:
    """Posterior XP + credit. Returns (xp_awarded, credit_awarded). Advisory lock."""
```

### `BallTrainingFeedbackResponse` kiterjesztés (`app/schemas/juggling.py`)

```python
xp_awarded:       int = 0
credit_awarded:   int = 0
daily_xp_total:   int = 0   # upfront + posterior annotation XP ma
daily_tasks_done: int = 0   # mai non-spam feedback count
```

Backward compatible: minden új mező `= 0` default → régebbi iOS kliensek nem törnek el.

### `submit_training_feedback()` bővítése (`app/services/juggling/ball_training_service.py`)

```python
# db.commit() után — új tranzakcióban, advisory lockkal:
xp_awarded, _ = award_annotation_upfront(db, user_id, req.assignment_id, req.decision, reliability)
daily_count, daily_xp, _ = get_daily_annotation_stats(db, user_id)
return BallTrainingFeedbackResponse(..., xp_awarded=xp_awarded, daily_xp_total=daily_xp, daily_tasks_done=daily_count)
```

### `run_compute_frame_consensus()` bővítése (`app/tasks/juggling_feedback_task.py`)

```python
# approval_state update után:
from app.services.juggling.ball_annotation_reward_service import award_annotation_accuracy_bonus
for r in rows:
    if r.approval_state == "approved":
        award_annotation_accuracy_bonus(
            db, r.id, r.user_id, r.decision, r.is_gold_standard, r.user_reliability_at_submit or 0.5
        )
```

---

## 10. Migration szükségessége

**DB migráció NEM szükséges** — minden változtatás Python kód szintű:

| Komponens | DB változás? |
|---|---|
| Új XP transaction típusok | Nem — `String(50)` szabad szöveg |
| `BALL_ANNOTATION_REWARD` credit típus | Nem — `String(50)` a DB-ben |
| Config értékek | Nem — Python-only |
| Schema bővítés | Nem — Pydantic |
| Reward service | Nem — új Python modul |

---

## 11. iOS eredmény és reward visszajelzés (PR-3B — gated on PR-3A merge)

```swift
struct BallTrainingFeedbackResponse: Decodable {
    let assignmentId: UUID
    let decision: String
    let submittedAt: Date
    let correctedX: Double?
    let correctedY: Double?
    let xpAwarded: Int         // 0 ha cap elérve
    let creditAwarded: Int     // 0 vagy 1
    let dailyXpTotal: Int
    let dailyTasksDone: Int
}
```

UX:
- `xpAwarded > 0` → animált badge (`"+5 XP"` vagy `"+10 XP"`)
- `creditAwarded > 0` → arany badge (`"+10 XP +1 CR"`)
- `xpAwarded == 0` + 201 status → `"Napi XP limit elérve"`
- Daily progress bar a Train AI képernyőn

---

## 12. Tesztek (PR-3A)

### `tests/unit/juggling/test_ball_annotation_reward.py` — teljes tesztlista

| ID | Leírás |
|---|---|
| BAR-01 | `confirm` → 5 XP, 0 CR; `XPTransaction(BALL_ANNOTATION_XP)` létrejön |
| BAR-02 | `no_ball` → 5 XP, 0 CR |
| BAR-03 | `corrected` → 10 XP, 0 CR upfront (credit nem jár még) |
| BAR-04 | Napi XP cap (100) elérve → upfront 0 XP |
| BAR-05 | Részleges reward cap közelében: 95/100 XP + 10 XP-s corrected → csak 5 XP |
| BAR-06 | Napi submission cap (30) → 201, `xp_awarded=0` |
| BAR-07 | Upfront idempotency: ugyanaz az `assignment_id` → 2. hívás 0 XP |
| BAR-08 | `spam_flags_count >= 10` → 0 XP (ismert spammer blokkolt) |
| BAR-09 | Posterior approved (standard) → +5 XP, 0 CR |
| BAR-10 | Posterior approved + gold → +15 XP, 0 CR |
| BAR-11 | Posterior approved corrected + reliability ≥ 0.4 → +5 XP + 1 CR |
| BAR-12 | Posterior approved corrected + reliability < 0.4 → +5 XP, 0 CR |
| BAR-13 | Posterior rejected → 0 XP, 0 CR |
| BAR-14 | Posterior spam → 0 XP, 0 CR |
| BAR-15 | Posterior XP idempotency: feedback_id kétszer → duplikáció nincs |
| BAR-16 | Posterior credit idempotency: feedback_id kétszer → credit nem duplikál |
| BAR-17 | Napi credit cap (10 CR) elérve → posterior approved corrected 0 CR |
| BAR-18 | Upfront + posterior shared XP cap: nem léphető túl 100 XP |
| BAR-19 | Response mezők: `xp_awarded, credit_awarded, daily_xp_total, daily_tasks_done` |
| BAR-20 | BTH-07 frissítés: confirm → response `xp_awarded=5` |
| BAR-21 | BTH-08 frissítés: no_ball → response `xp_awarded=5` |
| BAR-22 | Null/default reliability kezelése (0.5 default, nem crashel) |
| BAR-23 | Response backward compat: régi mezők változatlanok |
| BAR-CC-1 | 5 concurrent upfront submit → napi cap nem léphető túl (ThreadPoolExecutor) |
| BAR-CC-2 | Consensus task 2× futtatva ugyanarra a feedback_id-re → XP + CR nem duplikál |

---

## 13. PR breakdown

### PR-3A — Backend reward engine (migration-free)

**Branch**: `feat/an3b2e-pr3a-annotation-reward`

| Fájl | Változtatás |
|---|---|
| `app/services/juggling/ball_annotation_reward_service.py` | ÚJ |
| `app/config.py` | +11 konstans |
| `app/models/credit_transaction.py` | `BALL_ANNOTATION_REWARD` |
| `app/schemas/juggling.py` | `BallTrainingFeedbackResponse` +4 mező |
| `app/services/juggling/ball_training_service.py` | reward hívás + kiterjesztett return |
| `app/tasks/juggling_feedback_task.py` | accuracy bonus hook |
| `tests/unit/juggling/test_ball_annotation_reward.py` | ÚJ — BAR-01..BAR-CC-2 |
| `tests/unit/juggling/test_ball_training_hub.py` | BTH-07, BTH-08 frissítés |

Target: 21 BTH + 25 BAR = **46 PASS**

### PR-3B — iOS reward UI (gated on PR-3A merge + green CI)

Decoder + badge + progress bar. Loupe/marker/session flow változatlan.

---

**CONSTRAINT**: A loupe annotációs flow, a frame serving (PR-1B), és a consensus pipeline változatlan marad.
