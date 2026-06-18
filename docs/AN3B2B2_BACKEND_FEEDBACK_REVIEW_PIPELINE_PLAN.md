# AN-3B2B2 — Backend Feedback Review Pipeline
## Audit + Implementation Plan

**Branch:** `feat/an3b2b2-backend-feedback-review`
**Status:** PLAN ONLY — implementation pending separate approval
**Date:** 2026-06-18
**Depends on:** AN-3B2B1 B1 iOS Feedback UI (PR #304, merged `23594783`)

---

## 1. Current Backend Audit (B0 state)

### 1.1 Tables (migration `2026_06_18_2000` — already on main)

All three tables exist and are fully schema-complete. **No migration needed in B2.**

#### `juggling_ball_feedback`
| Column | Type | B0 State |
|--------|------|----------|
| `decision` | `VARCHAR(20)` CHECK `confirm/reject/no_ball/corrected` | Written by B1 iOS |
| `approval_state` | `VARCHAR(20)` CHECK `pending/approved/needs_review/rejected/spam` | Always `"pending"` — no logic sets it |
| `user_reliability_at_submit` | `FLOAT` | Snapshot of 0.5 (lazy default, never updated) |
| `weighted_vote_contribution` | `FLOAT` | Always NULL — never computed |
| `is_gold_standard` | `BOOLEAN` | Always False |
| `is_control_sample` | `BOOLEAN` | Always False |
| `spam_flags` | `JSONB` | Always `[]` |
| `reviewed_at` | `TIMESTAMPTZ` | Always NULL |
| `reviewed_by_user_id` | `INTEGER` | Always NULL |

#### `juggling_frame_ground_truth`
| Column | B0 State |
|--------|----------|
| `gt_decision` | Always `"uncertain"` — never updated |
| `gt_x`, `gt_y` | Always NULL |
| `confidence_score`, `agreement_rate` | Always 0.0 |
| `vote_count`, `yes_votes`, `no_votes`, `no_ball_votes`, `correction_count` | Always 0 |
| `training_eligible` | Always `false` — explicitly noted in migration |
| `exported_at`, `dataset_version` | Always NULL |

**Table exists but is never written to in B0.**

#### `user_annotation_reliability`
| Column | B0 State |
|--------|----------|
| `ball_annotation_reliability` | Lazy-created at 0.5, never updated |
| `total_feedbacks`, `correct_feedbacks` | Always 0 — never incremented |
| `gold_attempts`, `gold_correct` | Always 0 |
| `spam_flags_count` | Always 0 |

### 1.2 Endpoints (B0)

| Method | Path | Status |
|--------|------|--------|
| `POST` | `/users/me/juggling/videos/{video_id}/ball-feedback` | Live, gated by `BALL_FEEDBACK_ENABLED` |
| `GET` | `/users/me/juggling/videos/{video_id}/ball-feedback/queue` | Live, gated by `BALL_FEEDBACK_ENABLED` |
| — | Admin review endpoints | **Do not exist** |
| — | Export/retraining feed endpoints | **Do not exist** |

### 1.3 Services (B0)

- `ball_feedback_service.submit_feedback()` — persists row, sets `approval_state="pending"`, does NOT update reliability
- `ball_feedback_service.get_feedback_queue()` — priority queue based on `(1-confidence)*0.6`, excludes reviewed/saturated frames
- **No Celery tasks** for consensus, reliability update, or training export

### 1.4 Tests (B0)

18 backend tests in `tests/unit/juggling/test_ball_feedback_api.py` (bfb01–bfb18).
All pass on main. No tests exist for approval_state transitions, consensus, admin endpoints, or export.

---

## 2. Approval State Design

### 2.1 State machine

```
           ┌─────────────┐
  submit   │   pending   │
  ─────────►             ├──── auto-approve threshold met ──────► approved
           └──────┬──────┘
                  │ spam signals detected
                  ▼
           ┌─────────────┐
           │    spam     │
           └─────────────┘
                  │ (manual override by admin)
                  ▼
           ┌─────────────┐
           │needs_review │◄─── conflicting votes / low confidence ─────
           └──────┬──────┘
                  │ admin decision
          ┌───────┴──────────┐
          ▼                  ▼
      approved           rejected
```

### 2.2 Transition triggers

| Transition | Trigger | Actor |
|---|---|---|
| `pending` → `approved` | Auto-approve threshold met (see §3) | Celery task |
| `pending` → `spam` | Spam signal detected (see §5) | Celery task |
| `pending` → `needs_review` | Consensus below threshold, or conflicting corrections | Celery task |
| `needs_review` → `approved` | Admin manual approval | Admin endpoint |
| `needs_review` → `rejected` | Admin manual rejection | Admin endpoint |
| `spam` → `needs_review` | Admin manual escalation (override) | Admin endpoint |

---

## 3. Auto-Approve Threshold Logic

### 3.1 Principle

After each new feedback submission for a `(video_id, frame_ms)` pair, a Celery task
(`compute_frame_consensus`) runs and attempts to auto-approve if the frame reaches
sufficient agreement.

### 3.2 Threshold rules (proposed)

```python
AUTO_APPROVE_MIN_VOTES       = 3    # minimum total feedbacks before auto-approve
AUTO_APPROVE_AGREEMENT_RATE  = 0.8  # 80% agreement among votes
AUTO_APPROVE_MIN_RELIABILITY = 0.4  # all contributing voters above this floor

SPAM_MAX_SESSION_RATE        = 0.95 # >95% same decision in one session = spam candidate
SPAM_MIN_SAMPLES             = 5    # need at least 5 feedbacks before spam flag
```

### 3.3 `compute_frame_consensus` Celery task flow

```
1. Fetch all approved/pending feedback rows for (video_id, frame_ms)
2. Exclude spam-flagged rows
3. Compute weighted vote counts (weight = user_reliability_at_submit):
     yes_votes     = sum(weight) where decision in ('confirm', 'corrected')
     no_ball_votes = sum(weight) where decision == 'no_ball'
     no_votes      = sum(weight) where decision == 'reject'
4. Determine majority:
     if yes_votes / total >= AUTO_APPROVE_AGREEMENT_RATE:
         gt_decision = 'ball_present'
         if corrected rows exist: gt_x/gt_y = reliability-weighted centroid
     elif no_ball_votes / total >= AUTO_APPROVE_AGREEMENT_RATE:
         gt_decision = 'no_ball'
     else:
         gt_decision = 'uncertain' → mark pending rows → needs_review
5. Upsert juggling_frame_ground_truth
6. Update approval_state of contributing feedback rows:
     - consensus reached → 'approved'
     - uncertain → 'needs_review'
7. Update user_annotation_reliability for all contributing users (see §4)
8. Evaluate training_eligible (see §6)
```

### 3.4 Triggering

The task is triggered by `submit_feedback()` after commit, via:
```python
compute_frame_consensus.apply_async(
    args=[str(video_id), frame_ms],
    countdown=2,   # 2s delay to batch rapid submissions
)
```

Idempotent: re-running for the same `(video_id, frame_ms)` always produces the same result from current DB state.

---

## 4. User Annotation Reliability Updates

### 4.1 Current state

`user_annotation_reliability.ball_annotation_reliability` starts at 0.5 and is never updated in B0.

### 4.2 B2 update strategy

Reliability is updated as part of `compute_frame_consensus` after consensus is reached for a frame.

```
For each user who submitted feedback for the now-resolved frame:
  match = (user_decision aligns with gt_decision)
  correct_feedbacks += 1  if match
  total_feedbacks   += 1
  new_reliability = correct_feedbacks / total_feedbacks
  # Clamp to [0.1, 1.0] per existing DB CHECK constraint
  ball_annotation_reliability = max(0.1, min(1.0, new_reliability))
  last_updated = NOW()
```

Gold standard frames (is_gold_standard=True) count double in the reliability calculation, to
prioritise ground-truth-verified calibration frames.

### 4.3 `weighted_vote_contribution`

Set at consensus time:
```python
weighted_vote_contribution = user_reliability_at_submit / sum(all_reliabilities_in_frame)
```

Stored on the `juggling_ball_feedback` row for audit. Not re-computed after the fact.

---

## 5. Spam Detection

### 5.1 Signals checked at submit time (synchronous, lightweight)

| Signal | Logic | Action |
|--------|-------|--------|
| Velocity: same decision, same video, >10 frames within 60s | Query last 60s submissions | Set `spam_flags += ["velocity"]` |
| Uniform rate: >90% same decision across >20 frames for same video | Aggregate query | Set `spam_flags += ["uniform_rate"]` |

Spam signal detection runs inside `submit_feedback()` after the persist, before commit.
A non-empty `spam_flags` list immediately sets `approval_state = "spam"` on that row.

### 5.2 Spam flags do not affect `approval_state` of existing rows for other frames.

### 5.3 Admin override

Admin can clear `spam_flags` and set `approval_state = "needs_review"` via the admin review endpoint (§7.2).

---

## 6. `training_eligible` Flag Management

### 6.1 Eligibility conditions (all must hold)

```
gt_decision  != 'uncertain'
agreement_rate >= 0.75
vote_count     >= 3
is_gold_standard == False   (gold standard rows exported separately)
exported_at    IS NULL       (not already in a dataset)
```

### 6.2 Setting the flag

`training_eligible = True` is set by `compute_frame_consensus` at the end of its run,
only when all conditions above are met.

### 6.3 Clearing the flag

`training_eligible` is set back to `False` if a subsequent admin rejection changes `gt_decision`
to `uncertain`, or if a new feedback submission changes the consensus outcome.

---

## 7. Admin Review Queue

### 7.1 New admin endpoint: list review queue

```
GET /admin/juggling/ball-feedback/review-queue
Query params:
  ?video_id=<uuid>   (optional filter)
  ?state=needs_review|spam  (default: needs_review)
  ?limit=50&offset=0
Response: {
  items: [{ id, video_id, frame_ms, decision, corrected_x, corrected_y,
             model_predicted_x, model_predicted_y, model_confidence,
             approval_state, spam_flags, user_id, created_at,
             existing_feedback_count }],
  total: int
}
```

### 7.2 New admin endpoint: approve / reject / escalate

```
PATCH /admin/juggling/ball-feedback/{feedback_id}/review
Body: { "action": "approve" | "reject" | "escalate_to_review" }
Response: BallFeedbackOut (updated)
```

Logic:
- `approve`: sets `approval_state = "approved"`, `reviewed_at = NOW()`, `reviewed_by_user_id = admin.id`
  → triggers `compute_frame_consensus` for that frame to re-evaluate gt
- `reject`: sets `approval_state = "rejected"`, timestamps
  → triggers `compute_frame_consensus`
- `escalate_to_review`: clears `spam_flags`, sets `approval_state = "needs_review"`

### 7.3 Admin endpoint gating

All admin ball-feedback endpoints: `Depends(get_current_admin_user)`.
No `BALL_FEEDBACK_ENABLED` gate on admin endpoints (admins can always access review queue).

---

## 8. Retraining Feed (Export Preparation)

### 8.1 Export endpoint (read-only, admin only)

```
GET /admin/juggling/ball-feedback/training-export
Query params:
  ?dataset_version=<str>  (optional, to re-export a specific version)
  ?limit=1000
Response: {
  version: str (e.g. "v1_2026-06-18"),
  exported_at: datetime,
  frames: [
    {
      video_id, frame_ms,
      gt_decision, gt_x, gt_y,
      confidence_score, agreement_rate, vote_count,
      correction_count,
      is_gold_standard
    }
  ]
}
```

Only rows where `training_eligible = True AND exported_at IS NULL` are included.
After response is served, `exported_at` and `dataset_version` are stamped on all included rows.
Idempotent with `?dataset_version=` param (returns already-exported rows for that version).

### 8.2 Scope limitation

B2 export is JSON only (no S3 push, no model integration, no pipeline trigger).
The consumer (model retraining) reads the JSON export manually in B2.
S3/pipeline integration is deferred to B3+.

---

## 9. DB Migration

**No new migration required.** All columns used by B2 exist in `2026_06_18_2000`:
- `approval_state`, `reviewed_at`, `reviewed_by_user_id`, `spam_flags`, `weighted_vote_contribution`
  on `juggling_ball_feedback`
- All vote count / gt / training_eligible columns on `juggling_frame_ground_truth`
- All reliability tracking columns on `user_annotation_reliability`

The only runtime addition is one new Celery queue and two new admin API routes.
These do not require schema changes.

---

## 10. Endpoint / API Changes Summary

| Change | Type | Backward compat |
|--------|------|-----------------|
| `POST /users/me/.../ball-feedback` — add async Celery dispatch after commit | Modification | Yes — response unchanged |
| `GET /users/me/.../ball-feedback/queue` | No change | Yes |
| `GET /admin/juggling/ball-feedback/review-queue` | New endpoint | Yes (additive) |
| `PATCH /admin/juggling/ball-feedback/{id}/review` | New endpoint | Yes (additive) |
| `GET /admin/juggling/ball-feedback/training-export` | New endpoint | Yes (additive) |

---

## 11. Backward Compatibility

- **B1 iOS client is unaffected.** It reads `approval_state` only to display it; any state value
  already conforms to the existing schema enum.
- **B0 `submit_feedback()` response** (`BallFeedbackOut`) is unchanged. The new field
  `reviewed_at` is already in the schema (nullable).
- **Existing 18 backend tests (bfb01–bfb18)** continue to pass with no modification, because:
  - They assert `approval_state == "pending"` immediately post-submit, which is still correct
    (Celery task runs asynchronously, not in test sync flow).
  - Celery calls in tests will be mocked / eager with `CELERY_TASK_ALWAYS_EAGER=True`.

---

## 12. Test Plan

### 12.1 Naming convention

`BFC-*` = Ball Feedback Consensus  
`BFA-*` = Ball Feedback Admin  
`BFX-*` = Ball Feedback Export  
`BFS-*` = Ball Feedback Spam

### 12.2 Consensus task tests (BFC-01..12)

| ID | What |
|----|------|
| BFC-01 | 3 `confirm` votes → `gt_decision=ball_present`, `agreement_rate>=0.8`, `training_eligible=True` |
| BFC-02 | 3 `no_ball` votes → `gt_decision=no_ball`, `training_eligible=True` |
| BFC-03 | 2 confirm + 1 no_ball → `uncertain`, feedback rows → `needs_review` |
| BFC-04 | 2 corrected with coords → `gt_x/gt_y` = reliability-weighted centroid |
| BFC-05 | consensus reached → contributing feedback rows → `approval_state=approved` |
| BFC-06 | uncertain frame → contributing rows → `approval_state=needs_review` |
| BFC-07 | task idempotent: re-run produces same result |
| BFC-08 | reliability scores updated correctly after consensus |
| BFC-09 | `weighted_vote_contribution` set on each feedback row |
| BFC-10 | gold standard frame: reliability update counts double |
| BFC-11 | `training_eligible` reset to False after admin rejection changes gt |
| BFC-12 | `exported_at != NULL` → frame excluded from next export run |

### 12.3 Admin queue tests (BFA-01..08)

| ID | What |
|----|------|
| BFA-01 | `GET review-queue` returns only `needs_review` rows by default |
| BFA-02 | `?state=spam` filter returns only spam rows |
| BFA-03 | `?video_id=` filter narrows results |
| BFA-04 | Non-admin user → 403 |
| BFA-05 | `PATCH /review` approve → `approval_state=approved`, timestamps set |
| BFA-06 | `PATCH /review` reject → `approval_state=rejected`, triggers consensus re-eval |
| BFA-07 | `PATCH /review` escalate_to_review → clears spam_flags, → needs_review |
| BFA-08 | Approve on already-approved row → 409 (idempotency guard) |

### 12.4 Export tests (BFX-01..05)

| ID | What |
|----|------|
| BFX-01 | Export returns only `training_eligible=True AND exported_at IS NULL` rows |
| BFX-02 | After export, `exported_at` and `dataset_version` stamped on rows |
| BFX-03 | Re-export with same `?dataset_version=` returns already-exported rows |
| BFX-04 | `limit` param respected |
| BFX-05 | Non-admin → 403 |

### 12.5 Spam detection tests (BFS-01..04)

| ID | What |
|----|------|
| BFS-01 | Velocity signal: 11 submissions within 60s → `spam_flags=["velocity"]`, `approval_state=spam` |
| BFS-02 | Uniform rate: >90% same decision across 21 frames → `spam_flags=["uniform_rate"]` |
| BFS-03 | No spam signal: varied decisions → `spam_flags=[]` |
| BFS-04 | Admin escalate_to_review → clears spam_flags |

Total new tests: 12 + 8 + 5 + 4 = **29 tests**

---

## 13. Rollout Order and Risks

### 13.1 Proposed commit rollout

| Commit | Scope |
|--------|-------|
| D1 | `compute_frame_consensus` Celery task (no endpoint wiring yet) + BFC-01..12 |
| D2 | Spam detection in `submit_feedback()` (synchronous path) + BFS-01..04 |
| D3 | Admin review endpoints (list + patch) + BFA-01..08 |
| D4 | Training export endpoint + BFX-01..05 |
| D5 | Wire Celery dispatch into `submit_feedback()` + integration smoke test |

### 13.2 Celery queue

New queue: `ball_feedback`. Separate from `mood_photos` queue.
Worker: `celery -A app.celery_app worker -Q ball_feedback --pool=solo`

Existing workers are unaffected (no queue sharing).

### 13.3 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Celery task runs before DB transaction commits → stale read | Medium | Wrong consensus | `countdown=2` delay; task re-reads DB fresh |
| Reliability update race: two tasks updating same user simultaneously | Low (1 task per frame) | Incorrect reliability score | Task-level upsert with `SELECT FOR UPDATE` on reliability row |
| Spam false positives on legitimate rapid annotators | Low | Wrongly flags good users | Admin escalate_to_review path; threshold tunable via config |
| Export called during active consensus run → partial data | Low | Incomplete dataset version | Export only reads `training_eligible=True`; Celery task sets this at end atomically |
| B0 tests break if Celery eager mode not configured in test env | Medium | CI failure | Fixture: `CELERY_TASK_ALWAYS_EAGER=True` in conftest |
| `weighted_vote_contribution` summing across non-uniform reliability values | Low | Slight numerical drift | Always re-computed at consensus time; not accumulated |

### 13.4 Deploy requirements (when implemented)

- New Celery worker process for `ball_feedback` queue
- `BALL_FEEDBACK_ENABLED=true` already set
- No migration, no static asset changes, no iOS changes

---

## 14. Out of Scope (B3+)

- S3 / object storage upload of training dataset
- Automated model retraining pipeline trigger
- Credit grants to users for high-quality feedback
- Gold standard frame injection UI
- Cross-video consensus (same frame_ms across multiple videos)
- AN3B2E (separate approval required, plan-only)
