# Release Decision Recommendation
## Practice Booking System — GO / NO-GO Analysis
**Issued: 2026-04-16 | Evidence: code-level audit of MF-01..MF-03 | Authority: Engineering Lead**

---

## 0. Purpose

This document finalises the GO/NO-GO release decision by providing a code-level
evidence analysis of the three MUST FIX items from the Coverage Acceptance Sign-off,
then issues a binding recommendation.

---

## 1. MF Item Re-Assessment — Code-Level Evidence

### MF-01 — REVISED: Team credit flow has no refund mechanism (design gap)

**Original hypothesis:** Team enrollment rejection does not refund captain credits.

**Actual finding after code audit:**

| Fact | Source |
|------|--------|
| Team enrollment uses `TournamentTeamEnrollment`, NOT `SemesterEnrollment` | `team_service.py:316` |
| No "rejection" path exists for team enrollments — only `remove` (sets `is_active=False`) | `tournaments.py:1592–1613` |
| `admin_tournament_teams_remove` sets `is_active=False` with **no credit refund** | `tournaments.py:1608` |
| Tournament cancel (`cancellation.py`) processes only `SemesterEnrollment` rows → team captain credits NOT included | `cancellation.py:318–321` |
| Admin team removal and tournament cancellation both leave captain's `UserLicense.credit_balance` unreduced | Code audit confirmed |

**Implication:** TEAM tournament credit flow has **no automated refund mechanism** for either
(a) admin team removal or (b) tournament cancellation. This is a design gap, not a code bug.

**Risk exposure:**
- Scope: TEAM tournament format only. Individual tournament flows are fully covered (F-19..F-22).
- Frequency: Admin team removal is a rare, deliberate admin action.
- Recovery: Admin can restore credits manually via `POST /admin/credits/grant` (F-28, tested).
- Financial ceiling: `TournamentConfiguration.team_enrollment_cost` (typically 200–500 credits).

**Verdict: ACCEPTED RISK with documented protocol.**
Resolution is a backlog item (design of team-credit-refund service), NOT a release blocker.
Condition: admin must use F-28 manually if team is removed or TEAM tournament cancelled.

---

### MF-02 — CONFIRMED: Tournament delete has no enrollment guard

**Finding after code audit:**

```python
# tournaments.py:758–764
t = db.query(Semester).filter(Semester.id == tournament_id).first()
code = t.code
db.delete(t)        # ← no check for active enrollments
db.commit()
```

| Fact | Source |
|------|--------|
| `db.delete(t)` triggers SQLAlchemy cascade delete on `Semester.enrollments` | Model inspection: `cascade='delete,delete-orphan'` |
| Cascade deletes `SemesterEnrollment` rows — `User.credit_balance` is NOT restored | Individual enrollment deducted at creation time (auto-APPROVED) |
| Credits are permanently lost if tournament with active enrollments is deleted | Code confirms: no refund step before `db.delete()` |
| The CORRECT route for cancellation (with refunds) is `/api/v1/tournaments/{id}/cancel` | `cancellation.py:169–414` (full refund logic present) |
| Delete and Cancel are two separate admin UI buttons — **no UI guard** distinguishes them | `tournaments.py:748` vs. API cancel endpoint |

**Implication:** This is an **admin operation error** risk, not a logical code bug.
The cancel path (F-21) correctly handles all refunds. The delete path is a destructive
shortcut that bypasses the refund logic. Any admin who clicks Delete instead of Cancel on
a tournament with enrolled users causes permanent credit loss.

**Risk exposure:**
- Probability: Low — requires deliberate admin action on a tournament that has enrollments.
- Impact: High if triggered — irreversible credit loss for all enrolled users.
- Mitigation available: two options (see Section 3).

**Verdict: CONDITIONAL RELEASE BLOCKER.**
This item requires ONE of the two mitigations in Section 3 before the delete route is
accessible in production. See GO conditions below.

---

### MF-03 — CLEARED: Tournament rollback is status-only by design

**Finding after code audit:**

```python
# tournaments.py:791–793
t.tournament_status = "ENROLLMENT_CLOSED"
t.status = SemesterStatus.READY_FOR_ENROLLMENT
db.commit()
```

| Fact | Source |
|------|--------|
| Rollback changes only `tournament_status` and `status` fields | `tournaments.py:791–793` |
| All `SemesterEnrollment` rows remain APPROVED, credits remain deducted | No enrollment query in handler |
| Participants stay enrolled — rollback is for stuck session generation, not enrollment reversal | Docstring: "rollback stuck IN_PROGRESS → ENROLLMENT_CLOSED for re-generation" |
| Available only when `tournament_status == "IN_PROGRESS"` (guard at line 785) | `tournaments.py:785–788` |

**Implication:** Rollback is designed to reset the lifecycle state for re-generation without
touching enrollment or credits. This is correct product behavior — after rollback, the
tournament will regenerate sessions and participants remain enrolled.

**Verdict: CLEARED — not a financial risk. No action required.**

---

## 2. Revised Risk Matrix

| Item | Original Classification | Revised Verdict | Release Impact |
|------|------------------------|-----------------|----------------|
| MF-01 | Must Fix | **Accepted Risk + Protocol** | ✅ Not blocking |
| MF-02 | Must Fix | **Conditional blocker** | ⚠️ Requires one mitigation |
| MF-03 | Must Fix | **Cleared — by design safe** | ✅ Not blocking |

---

## 3. MF-02 Mitigation Options

Choose ONE of the following before enabling the Delete button in production:

### Option A — UI Guard (Recommended, 30 min implementation)

Add a pre-delete enrollment check to the route handler:

```python
# In admin_delete_tournament(), before db.delete(t):
active_enrollments = db.query(SemesterEnrollment).filter(
    SemesterEnrollment.semester_id == tournament_id,
    SemesterEnrollment.is_active == True,
).count()
if active_enrollments > 0:
    return RedirectResponse(
        url=f"/admin/tournaments?error=Cannot+delete:+{active_enrollments}+active+enrollments.+Use+Cancel+instead.",
        status_code=303,
    )
```

**Effect:** Delete becomes impossible when enrollments exist. Admin is redirected to use Cancel.
**Risk after fix:** Zero. Delete only works on tournaments with no enrollment history.

### Option B — Operational Protocol (Zero implementation)

Document and enforce the following admin constraint:

> **Admin DELETE operation constraint:**
> The Delete button must NEVER be used for tournaments that have had any enrollments
> (PENDING, APPROVED, or historical). Always use Cancel (which processes full refunds)
> for tournaments in any state with financial history.
> Delete is only safe for: DRAFT tournaments created in error with 0 enrollment history.

**Effect:** Relies on admin training, not code enforcement.
**Risk after fix:** Low but non-zero — human error possible.

---

## 4. Production Risk Impact Analysis — TEAM Tournament + Financial Flows

### Individual Tournament (IND) Flows — FULLY SAFE ✅

| Flow | Credit model | Test | Risk |
|------|-------------|------|------|
| Enroll → deduct | `User.credit_balance -= cost` (atomic SQL) | F-19 | None |
| Unenroll → 50% refund | `User.credit_balance += cost//2` | F-20 (TCR) | None |
| Cancel → 100% refund (all APPROVED) | `UserLicense.credit_balance += cost` per enrollment | F-21 (GAP-01) | None |
| Admin rejection → no refund (PENDING = no payment) | No credit change | F-22 (GAP-03) | None |
| Delete with enrollments | ❌ **No refund** (see MF-02) | None | **Requires mitigation** |

### TEAM Tournament Flows — PARTIAL GAP ⚠️

| Flow | Credit model | Test | Risk |
|------|-------------|------|------|
| Team create → deduct | `UserLicense.credit_balance -= team_enrollment_cost` | F-23 (GAP-02) | None |
| Team cancel (admin) → refund | ❌ **No refund** (cancel processes SemesterEnrollment, not TournamentTeamEnrollment) | None | **Gap — protocol mitigation** |
| Admin team remove → refund | ❌ **No refund** (sets is_active=False only) | None | **Gap — protocol mitigation** |
| Team unenroll (captain) → refund | Unknown — needs investigation | None | Investigate before TEAM scale-up |

**TEAM tournament financial gap summary:**
The entire TEAM credit flow is one-directional: deduction is proven (F-23), refund
on cancellation/removal is not implemented. Admin must manually restore credits via F-28.

**Financial ceiling per incident:**
`team_enrollment_cost` is configurable per `TournamentConfiguration`. Default 0 for
admin-enrolled teams (bypassed, `payment_verified=True`). Only applies to captain-enrolled
teams via `create_team_with_cost`.

---

## 5. Release Decision

### **VERDICT: CONDITIONAL GO**

The system is **release-ready for production** under the following conditions:

---

### GO Conditions (all required)

| # | Condition | Status | Owner |
|---|-----------|--------|-------|
| **C-1** | MF-02 mitigation implemented (Option A or B) | ⚠️ Pending | Engineering |
| **C-2** | Admin trained: "Delete = no refund; Cancel = full refund" | ⚠️ Pending | Operations |
| **C-3** | Admin trained: "Team removal = no refund; use grant-credit (F-28) if needed" | ⚠️ Pending | Operations |
| **C-4** | CI 8/8 green on main | ✅ Confirmed | CI |
| **C-5** | 62/62 E2E flows green | ✅ Confirmed | CI |

---

### Domain-Level Release Status

| Domain | Release Status | Notes |
|--------|---------------|-------|
| Authentication + Registration | ✅ GO | Fully covered, no residual risk |
| Individual tournament enroll/refund | ✅ GO | F-19..F-22 all proven |
| Quiz / XP / learning path | ✅ GO | 7 flows, all paths covered |
| Session booking + attendance | ✅ GO | Start/stop/cancel/attend proven |
| Communications (messages + notifications) | ✅ GO | 100% domain coverage |
| Invoice management | ✅ GO | Verify/cancel/unverify all proven |
| License lifecycle | ✅ GO | Grant/renew/revoke cascade all proven |
| Instructor evaluation | ✅ GO | F-63/F-64 (bidirectional) proven |
| TEAM tournament credit flow | ⚠️ CONDITIONAL | No automated refund on cancel/removal; manual protocol required |
| Tournament delete (admin) | ⚠️ CONDITIONAL | MF-02 mitigation required (Option A recommended) |
| Bulk admin operations | ✅ GO | F-55/F-56/F-57..F-62 proven |

---

### Sprint 8 Recommendation

Sprint 8 is **NOT required** for production release. It is recommended only if:
- TEAM tournament format enters high-volume production use → design team-credit-refund service
- Tournament delete without enrollment guard remains as code risk (Option A not chosen)
- F-65..F-70 (finalize, bracket, cascading deletes) are prioritised by business

**If TEAM tournaments are in active use, recommend addressing the refund design gap as a
targeted fix sprint (2–3 days), not a full Sprint 8.**

---

*Release Decision issued by Claude Sonnet 4.6 — 2026-04-16*
*Based on direct code audit of: `tournaments.py`, `team_service.py`, `cancellation.py`,*
*`workflow.py`, `semester_enrollment.py` (model cascade inspection)*
