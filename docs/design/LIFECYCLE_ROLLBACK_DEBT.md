# Architecture / UX Debt: Lifecycle Rollback / Admin Recovery Controls

**Status:** Documented, not scheduled  
**Date:** 2026-05-06  
**Context:** Tournament wizard (7-step forward-only state machine)

---

## What is missing

The wizard has no "step back" action. Once a transition is confirmed, there is
no built-in way to revert to a previous status from the UI.

## Why it was not built

Each forward transition carries irreversible side effects:

| Transition | Side effects |
|---|---|
| `DRAFT → ENROLLMENT_OPEN` | Preview sessions generated (Phase 1); `TournamentConfiguration.sessions_generated = True` |
| `ENROLLMENT_CLOSED → CHECK_IN_OPEN` | Self check-in window opens; `checkin_opens_at` stamped |
| `CHECK_IN_OPEN → IN_PROGRESS` | Final draw: existing sessions **deleted**, new sessions generated from checked-in pool |

Rolling back any of these requires:
1. Cascaded deletion of generated sessions (and any results already recorded).
2. Clearing `tournament_checked_in_at` from all `SemesterEnrollment` rows.
3. Clearing all `TournamentPlayerCheckin` rows.
4. Resetting `TournamentConfiguration.sessions_generated`.
5. Potential credit-refund logic if enrollment costs were charged.

This is full rollback semantics per transition — not a cosmetic status flip.
Scoping it correctly requires one dedicated implementation track per
transition step, with explicit DB cleanup logic and edge-case handling for
partially-entered results.

## Current escape valves (what works today)

| Need | Escape valve |
|---|---|
| Edit config after enrollment opens | Wizard steps collapse but remain editable; most config fields accept updates at any status |
| Delete and redo session generation | "Delete Sessions" button in Section 5; regenerate at will |
| Undo a player/team check-in | "Undo" button on the Attendance page |
| Full event reset | Set `tournament_status = CANCELLED`; manual DB cleanup via admin tools |

## If this is scheduled

Minimum viable rollback set (suggested scope boundary):

- `CHECK_IN_OPEN → ENROLLMENT_CLOSED`: clear `checkin_opens_at`, clear all
  `TournamentPlayerCheckin` rows for this tournament, clear
  `SemesterEnrollment.tournament_checked_in_at` for all enrolled players.
  Sessions from Phase 1 are unaffected (preview draw stays).

- `ENROLLMENT_CLOSED → ENROLLMENT_OPEN`: no session cleanup needed (Phase 1
  hasn't run yet). Safe if no sessions exist; must block if
  `sessions_generated = True`.

- `IN_PROGRESS → CHECK_IN_OPEN`: requires deleting all generated sessions and
  any recorded results. High-risk; must require explicit admin confirmation
  with a destructive-action warning. Out of scope until results recording is
  confirmed stable.

Each rollback action should be a separate POST endpoint (not a status PATCH)
to make the destructive intent explicit and auditable.
