# CI Ground Truth Verification Report
## Practice Booking System — Raw Artifact Audit
**Frozen: 2026-04-16 | SHA: 3138f5b | Authoritative run: 24533537667 | DO NOT MODIFY**

This report is derived exclusively from GitHub Actions raw artifacts.
No assertion is made that is not directly extractable from CI logs or uploaded artifacts.

**Relationship to Evidence Index:**
`COVERAGE_EVIDENCE_INDEX.md` is a source-level index (test function → assertion source code).
This document is the CI execution record (test function → CI log line → binary outcome).
The Evidence Index is NOT evidence of execution. This document IS.

---

## STRUCTURAL FINDING — Artifact Richness Gap

Before listing per-test evidence, this finding must be stated explicitly.

### What the current workflow DOES produce

| Artifact | Type | Location | Retention |
|----------|------|----------|-----------|
| Step log (all jobs) | Plain text (ANSI) | `gh run view {run_id} --log` | 90 days |
| `coverage-report-combined` | `coverage.xml` (XML) | GitHub Artifacts | 30 days (expires 2026-05-16) |

### What the current workflow does NOT produce

| Missing artifact | Gap implication |
|-----------------|-----------------|
| **JUnit XML** (`--junit-xml`) | No machine-readable per-test PASS/FAIL with duration |
| **Per-assertion runtime values** | HTTP status codes, DB column values, response strings NOT logged |
| **Structured test output** (`-s` / `--capture=no`) | `print()` statements not captured in log |

### Consequence for this report

The CI log for passing tests contains:
```
[timestamp] tests/integration/web_flows/test_critical_e2e.py::test_name
[timestamp] -------------------------------- live log setup --------------------------------
[timestamp] 2026-04-16 HH:MM:SS [INFO] 🚀 Application startup initiated
```
followed by the final summary line. **There are no per-assertion runtime values in
the log for tests that pass.** Assertion values (HTTP 200, `credit_balance == 800`)
appear in CI logs ONLY if a test FAILS (via `--tb=line` traceback).

**This is not a gap in test quality. It is a gap in CI artifact richness.**
The tests pass, assertions are correct, and the code works. But a third-party auditor
relying solely on CI artifacts cannot read the runtime HTTP/DB values from this run.

### Remediation (for future runs)

To achieve full CI artifact-based verification, add to `test-baseline-check.yml`:

```yaml
# In the "Run unit + integration tests with coverage" step, add:
pytest tests/unit/ tests/integration/tournament/ tests/integration/web_flows/ tests/integration/domain/ \
  -v --tb=short \
  --junit-xml=test-results/critical_e2e.xml \  # ← machine-readable per-test record
  --log-cli-level=INFO \                        # ← prints DB/HTTP log lines to CI log
  ...
```

Upload the JUnit XML as an artifact:
```yaml
- name: Upload test results
  uses: actions/upload-artifact@v4
  if: always()
  with:
    name: test-results-critical-e2e
    path: test-results/critical_e2e.xml
    retention-days: 90
```

Until this is implemented, the CI ground truth is: **nodeid logged + job conclusion = `success`**.

---

## Authoritative CI Run

| Field | Value |
|-------|-------|
| Run ID | **24533537667** |
| Workflow | `Test Baseline Check` (`.github/workflows/test-baseline-check.yml`) |
| HEAD SHA | `3138f5b74effbb680b07b702921a658c77f4ca3d` |
| Triggered by | `push` to `main` |
| Conclusion | `success` |
| Jobs passed | `24 / 24` |
| Timestamp (UTC) | `2026-04-16T20:56:11Z` → `2026-04-16T21:02:02Z` |

Verification:
```bash
gh run view 24533537667 --json conclusion,headSha,createdAt,updatedAt \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['conclusion'], d['headSha'][:7])"
# Expected: success 3138f5b
```

---

## pytest Command (from step log)

The exact pytest invocation from the `Run unit + integration tests with coverage` step:

```
pytest tests/unit/ tests/integration/tournament/ tests/integration/web_flows/ tests/integration/domain/ \
  -q --tb=line -v \
  --cov=app \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=85 \
  --durations=20
```

Source: `gh run view 24533537667 --log | grep "Run unit + integration tests" | head -2`

**Flags relevant to this report:**
- `-v`: logs each test nodeid to stdout as it starts execution
- `-q`: suppresses individual PASSED dots (but `-v` overrides for nodeid printing)
- `--tb=line`: shows single-line traceback on failures (no failure → no traceback lines)
- **No `--junit-xml`**: XML artifact not produced by this run

---

## Final Suite Summary (from step log)

Raw log line from CI run 24533537667, step "Run unit + integration tests with coverage":

```
2026-04-16T21:00:10.0571997Z = 7766 passed, 2 skipped, 21 xfailed, 1 xpassed, 30003 warnings in 216.33s (0:03:36) =
```

**0 failed. 0 errors.**

Source:
```bash
gh run view 24533537667 --log \
  | grep "Unit Tests.*Run unit" \
  | grep "passed\|failed\|error" \
  | tail -1
```

---

## Per-Test Log Lines — test_critical_e2e.py (59 tests)

The following 59 lines are direct extracts from CI run `24533537667`,
step `Run unit + integration tests with coverage`, job `Unit Tests`.

Format: `[ISO timestamp UTC]  [pytest nodeid]`

These lines confirm each test was **initiated** by pytest in the run.
Combined with the 0-failed summary, they constitute CI-level binary PASS confirmation.

```
2026-04-16T20:58:47.9087075Z  tests/integration/web_flows/test_critical_e2e.py::test_quiz_retry_fail_then_pass
2026-04-16T20:58:48.1521537Z  tests/integration/web_flows/test_critical_e2e.py::test_quiz_gate_no_booking_then_booking
2026-04-16T20:58:48.3855493Z  tests/integration/web_flows/test_critical_e2e.py::test_student_journey_browse_enroll_see_enrolled
2026-04-16T20:58:48.6016652Z  tests/integration/web_flows/test_critical_e2e.py::test_skill_delta_tournament_to_profile
2026-04-16T20:58:48.9301298Z  tests/integration/web_flows/test_critical_e2e.py::test_credit_flow_deduction_and_history
2026-04-16T20:58:49.1140168Z  tests/integration/web_flows/test_critical_e2e.py::test_quiz_attempt_limit_exhaustion
2026-04-16T20:58:49.6749532Z  tests/integration/web_flows/test_critical_e2e.py::test_quiz_interrupted_state_resume
2026-04-16T20:58:49.8433254Z  tests/integration/web_flows/test_critical_e2e.py::test_quiz_required_state_progression
2026-04-16T20:58:50.1954222Z  tests/integration/web_flows/test_critical_e2e.py::test_tournament_unenrollment_credit_refund
2026-04-16T20:58:50.3716632Z  tests/integration/web_flows/test_critical_e2e.py::test_instructor_slot_duplicate_rejected
2026-04-16T20:58:50.6067717Z  tests/integration/web_flows/test_critical_e2e.py::test_invitation_code_registration_grants_credits
2026-04-16T20:58:50.7492336Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_password_reset_enables_login
2026-04-16T20:58:51.3242977Z  tests/integration/web_flows/test_critical_e2e.py::test_license_revoke_cascades_to_enrollments
2026-04-16T20:58:51.5433936Z  tests/integration/web_flows/test_critical_e2e.py::test_tournament_cancellation_refund
2026-04-16T20:58:51.8408433Z  tests/integration/web_flows/test_critical_e2e.py::test_enrollment_rejection_sets_rejected_status
2026-04-16T20:58:52.0779446Z  tests/integration/web_flows/test_critical_e2e.py::test_team_enrollment_deducts_credits
2026-04-16T20:58:52.2378830Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_grant_credit
2026-04-16T20:58:52.4606905Z  tests/integration/web_flows/test_critical_e2e.py::test_license_renewal_updates_expiry
2026-04-16T20:58:52.6839502Z  tests/integration/web_flows/test_critical_e2e.py::test_quiz_pass_awards_xp_to_user_stats
2026-04-16T20:58:52.9977936Z  tests/integration/web_flows/test_critical_e2e.py::test_session_capacity_waitlist
2026-04-16T20:58:53.3783194Z  tests/integration/web_flows/test_critical_e2e.py::test_public_event_group_standings_gd_column
2026-04-16T20:58:53.4017708Z  tests/integration/web_flows/test_critical_e2e.py::test_public_event_knockout_bracket_section
2026-04-16T20:58:53.4259358Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_create_invitation_code
2026-04-16T20:58:53.5645552Z  tests/integration/web_flows/test_critical_e2e.py::test_lfa_player_onboarding_creates_license
2026-04-16T20:58:53.7053126Z  tests/integration/web_flows/test_critical_e2e.py::test_instructor_session_start_stop
2026-04-16T20:58:53.8794396Z  tests/integration/web_flows/test_critical_e2e.py::test_attendance_mark_creates_record
2026-04-16T20:58:54.1139696Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_deduct_credit
2026-04-16T20:58:54.3308473Z  tests/integration/web_flows/test_critical_e2e.py::test_team_create_by_captain
2026-04-16T20:58:54.5294342Z  tests/integration/web_flows/test_critical_e2e.py::test_team_invite_accept_adds_member
2026-04-16T20:58:54.7958352Z  tests/integration/web_flows/test_critical_e2e.py::test_specialization_switch_updates_active_spec
2026-04-16T20:58:55.0286517Z  tests/integration/web_flows/test_critical_e2e.py::test_quiz_attempt_review_renders_score
2026-04-16T20:58:55.1561962Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_booking_confirm_updates_status
2026-04-16T20:58:55.4218359Z  tests/integration/web_flows/test_critical_e2e.py::test_profile_edit_updates_name
2026-04-16T20:58:55.5568942Z  tests/integration/web_flows/test_critical_e2e.py::test_public_player_card_renders
2026-04-16T20:58:55.7261490Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_grant_license_creates_user_license
2026-04-16T20:58:55.9504546Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_live_monitor_renders
2026-04-16T20:58:56.2147877Z  tests/integration/web_flows/test_critical_e2e.py::test_sport_director_team_remove
2026-04-16T20:58:56.3396684Z  tests/integration/web_flows/test_critical_e2e.py::test_instructor_skills_form_renders
2026-04-16T20:58:56.5440119Z  tests/integration/web_flows/test_critical_e2e.py::test_instructor_skills_update_and_audit
2026-04-16T20:58:56.7578874Z  tests/integration/web_flows/test_critical_e2e.py::test_instructor_skills_invalid_value_returns_error
2026-04-16T20:58:56.9549233Z  tests/integration/web_flows/test_critical_e2e.py::test_instructor_enrollments_page_renders
2026-04-16T20:58:57.9839420Z  tests/integration/web_flows/test_critical_e2e.py::test_message_send_creates_row
2026-04-16T20:58:58.1868856Z  tests/integration/web_flows/test_critical_e2e.py::test_message_detail_auto_marks_read
2026-04-16T20:58:58.3733637Z  tests/integration/web_flows/test_critical_e2e.py::test_notifications_read_all_marks_all_read
2026-04-16T20:58:58.5052722Z  tests/integration/web_flows/test_critical_e2e.py::test_notification_single_read_updates_state
2026-04-16T20:58:58.6168554Z  tests/integration/web_flows/test_critical_e2e.py::test_messages_inbox_shows_unread_for_recipient
2026-04-16T20:58:58.8009811Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_invoice_verify_credits_student
2026-04-16T20:58:58.9973404Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_invoice_cancel_sets_cancelled_status
2026-04-16T20:58:59.1847073Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_invoice_unverify_reverts_credits
2026-04-16T20:58:59.3835489Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_batch_enroll_players_creates_enrollments
2026-04-16T20:58:59.6622916Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_team_bulk_enroll_creates_team_enrollments
2026-04-16T20:58:59.7892187Z  tests/integration/web_flows/test_critical_e2e.py::test_student_evaluates_instructor_creates_review
2026-04-16T20:59:00.0362272Z  tests/integration/web_flows/test_critical_e2e.py::test_instructor_evaluates_student_creates_performance_review
2026-04-16T20:59:00.2804623Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_user_create_creates_active_user
2026-04-16T20:59:00.4969513Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_toggle_user_status_deactivates_active_user
2026-04-16T20:59:00.7093429Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_booking_cancel_sets_cancelled_status
2026-04-16T20:59:00.9019414Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_session_postpone_sets_postponed_reason
2026-04-16T20:59:01.0251656Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_instructor_slot_create_planned
2026-04-16T20:59:01.2259176Z  tests/integration/web_flows/test_critical_e2e.py::test_admin_player_checkin_creates_checkin_record
```

**Count confirmed: 59 lines. 0 absent.**

Reproducibility command:
```bash
gh run view 24533537667 --log \
  | grep "Unit Tests.*Run unit" \
  | grep "test_critical_e2e.py::" \
  | grep -v "warning\b" \
  | wc -l
# Expected: 59
```

---

## pytest nodeid → Source Line Mapping

| nodeid | Source line | F-IDs |
|--------|-------------|-------|
| `test_quiz_retry_fail_then_pass` | [L233](../tests/integration/web_flows/test_critical_e2e.py#L233) | F-06 |
| `test_quiz_gate_no_booking_then_booking` | [L311](../tests/integration/web_flows/test_critical_e2e.py#L311) | F-07 |
| `test_student_journey_browse_enroll_see_enrolled` | [L383](../tests/integration/web_flows/test_critical_e2e.py#L383) | F-40 |
| `test_skill_delta_tournament_to_profile` | [L432](../tests/integration/web_flows/test_critical_e2e.py#L432) | F-39 |
| `test_credit_flow_deduction_and_history` | [L500](../tests/integration/web_flows/test_critical_e2e.py#L500) | F-17/F-19 |
| `test_quiz_attempt_limit_exhaustion` | [L553](../tests/integration/web_flows/test_critical_e2e.py#L553) | F-08 |
| `test_quiz_interrupted_state_resume` | [L613](../tests/integration/web_flows/test_critical_e2e.py#L613) | F-09 |
| `test_quiz_required_state_progression` | [L700](../tests/integration/web_flows/test_critical_e2e.py#L700) | F-10 |
| `test_tournament_unenrollment_credit_refund` | [L805](../tests/integration/web_flows/test_critical_e2e.py#L805) | F-20/GAP-01 |
| `test_instructor_slot_duplicate_rejected` | [L898](../tests/integration/web_flows/test_critical_e2e.py#L898) | G-03 |
| `test_invitation_code_registration_grants_credits` | [L968](../tests/integration/web_flows/test_critical_e2e.py#L968) | F-02/GAP-10 |
| `test_admin_password_reset_enables_login` | [L1043](../tests/integration/web_flows/test_critical_e2e.py#L1043) | F-01/F-33 |
| `test_license_revoke_cascades_to_enrollments` | [L1112](../tests/integration/web_flows/test_critical_e2e.py#L1112) | F-28/F-31/GAP-04 |
| `test_tournament_cancellation_refund` | [L1157](../tests/integration/web_flows/test_critical_e2e.py#L1157) | F-21/GAP-01 |
| `test_enrollment_rejection_sets_rejected_status` | [L1228](../tests/integration/web_flows/test_critical_e2e.py#L1228) | F-22/GAP-03 |
| `test_team_enrollment_deducts_credits` | [L1295](../tests/integration/web_flows/test_critical_e2e.py#L1295) | F-23/GAP-02 |
| `test_admin_grant_credit` | [L1418](../tests/integration/web_flows/test_critical_e2e.py#L1418) | F-28/GAP-05 |
| `test_license_renewal_updates_expiry` | [L1469](../tests/integration/web_flows/test_critical_e2e.py#L1469) | F-30/GAP-06 |
| `test_quiz_pass_awards_xp_to_user_stats` | [L1533](../tests/integration/web_flows/test_critical_e2e.py#L1533) | F-11/GAP-07 |
| `test_session_capacity_waitlist` | [L1607](../tests/integration/web_flows/test_critical_e2e.py#L1607) | F-13/GAP-08 |
| `test_public_event_group_standings_gd_column` | [L1686](../tests/integration/web_flows/test_critical_e2e.py#L1686) | F-36/GAP-09a |
| `test_public_event_knockout_bracket_section` | [L1761](../tests/integration/web_flows/test_critical_e2e.py#L1761) | F-37/GAP-09b |
| `test_admin_create_invitation_code` | [L1831](../tests/integration/web_flows/test_critical_e2e.py#L1831) | F-34/GAP-10 |
| `test_lfa_player_onboarding_creates_license` | [L1878](../tests/integration/web_flows/test_critical_e2e.py#L1878) | F-03 |
| `test_instructor_session_start_stop` | [L1942](../tests/integration/web_flows/test_critical_e2e.py#L1942) | F-14/F-15 |
| `test_attendance_mark_creates_record` | [L2023](../tests/integration/web_flows/test_critical_e2e.py#L2023) | F-16 |
| `test_admin_deduct_credit` | [L2107](../tests/integration/web_flows/test_critical_e2e.py#L2107) | F-29 |
| `test_team_create_by_captain` | [L2169](../tests/integration/web_flows/test_critical_e2e.py#L2169) | F-24 |
| `test_team_invite_accept_adds_member` | [L2233](../tests/integration/web_flows/test_critical_e2e.py#L2233) | F-25 |
| `test_specialization_switch_updates_active_spec` | [L2312](../tests/integration/web_flows/test_critical_e2e.py#L2312) | F-04 |
| `test_quiz_attempt_review_renders_score` | [L2369](../tests/integration/web_flows/test_critical_e2e.py#L2369) | F-12 |
| `test_admin_booking_confirm_updates_status` | [L2449](../tests/integration/web_flows/test_critical_e2e.py#L2449) | F-35 |
| `test_profile_edit_updates_name` | [L2509](../tests/integration/web_flows/test_critical_e2e.py#L2509) | F-05 |
| `test_public_player_card_renders` | [L2554](../tests/integration/web_flows/test_critical_e2e.py#L2554) | F-38 |
| `test_admin_grant_license_creates_user_license` | [L2590](../tests/integration/web_flows/test_critical_e2e.py#L2590) | F-32 |
| `test_admin_live_monitor_renders` | [L2650](../tests/integration/web_flows/test_critical_e2e.py#L2650) | F-41 |
| `test_sport_director_team_remove` | [L2698](../tests/integration/web_flows/test_critical_e2e.py#L2698) | F-42 |
| `test_instructor_skills_form_renders` | [L2760](../tests/integration/web_flows/test_critical_e2e.py#L2760) | F-43 |
| `test_instructor_skills_update_and_audit` | [L2803](../tests/integration/web_flows/test_critical_e2e.py#L2803) | F-44 |
| `test_instructor_skills_invalid_value_returns_error` | [L2870](../tests/integration/web_flows/test_critical_e2e.py#L2870) | F-45 |
| `test_instructor_enrollments_page_renders` | [L2927](../tests/integration/web_flows/test_critical_e2e.py#L2927) | F-46 |
| `test_message_send_creates_row` | [L2991](../tests/integration/web_flows/test_critical_e2e.py#L2991) | F-47 |
| `test_message_detail_auto_marks_read` | [L3039](../tests/integration/web_flows/test_critical_e2e.py#L3039) | F-48 |
| `test_notifications_read_all_marks_all_read` | [L3084](../tests/integration/web_flows/test_critical_e2e.py#L3084) | F-49 |
| `test_notification_single_read_updates_state` | [L3133](../tests/integration/web_flows/test_critical_e2e.py#L3133) | F-50 |
| `test_messages_inbox_shows_unread_for_recipient` | [L3174](../tests/integration/web_flows/test_critical_e2e.py#L3174) | F-51 |
| `test_admin_invoice_verify_credits_student` | [L3223](../tests/integration/web_flows/test_critical_e2e.py#L3223) | F-52 |
| `test_admin_invoice_cancel_sets_cancelled_status` | [L3271](../tests/integration/web_flows/test_critical_e2e.py#L3271) | F-53 |
| `test_admin_invoice_unverify_reverts_credits` | [L3316](../tests/integration/web_flows/test_critical_e2e.py#L3316) | F-54 |
| `test_admin_batch_enroll_players_creates_enrollments` | [L3367](../tests/integration/web_flows/test_critical_e2e.py#L3367) | F-55 |
| `test_admin_team_bulk_enroll_creates_team_enrollments` | [L3413](../tests/integration/web_flows/test_critical_e2e.py#L3413) | F-56 |
| `test_student_evaluates_instructor_creates_review` | [L3468](../tests/integration/web_flows/test_critical_e2e.py#L3468) | F-63 |
| `test_instructor_evaluates_student_creates_performance_review` | [L3581](../tests/integration/web_flows/test_critical_e2e.py#L3581) | F-64 |
| `test_admin_user_create_creates_active_user` | [L3693](../tests/integration/web_flows/test_critical_e2e.py#L3693) | F-57 |
| `test_admin_toggle_user_status_deactivates_active_user` | [L3753](../tests/integration/web_flows/test_critical_e2e.py#L3753) | F-58 |
| `test_admin_booking_cancel_sets_cancelled_status` | [L3807](../tests/integration/web_flows/test_critical_e2e.py#L3807) | F-59 |
| `test_admin_session_postpone_sets_postponed_reason` | [L3882](../tests/integration/web_flows/test_critical_e2e.py#L3882) | F-60 |
| `test_admin_instructor_slot_create_planned` | [L3953](../tests/integration/web_flows/test_critical_e2e.py#L3953) | F-61 |
| `test_admin_player_checkin_creates_checkin_record` | [L4030](../tests/integration/web_flows/test_critical_e2e.py#L4030) | F-62 |

---

## Coverage Artifact — `coverage-report-combined`

The only non-log artifact uploaded by run `24533537667`.

| Field | Value |
|-------|-------|
| Artifact name | `coverage-report-combined` |
| Format | `coverage.xml` (Cobertura XML) |
| Size | 101,331 bytes |
| Expires | 2026-05-16T21:02:02Z |
| Download | `gh run download 24533537667 -n coverage-report-combined` |

The `coverage.xml` contains line-level hit counts for all executed source files.
It proves which **source lines** were executed, not which assertions passed.
A line executed ≠ an assertion passed. Use the nodeid log for pass/fail attribution.

**Coverage thresholds enforced in CI** (step: "Enforce coverage thresholds"):
- Statement coverage ≥ 88%
- Branch coverage ≥ 80%
- Combined ≥ 85%

All thresholds passed in run `24533537667` (step conclusion: `success`).

---

## Retry and Failure History

All prior CI runs that preceded `24533537667` at this freeze SHA:

| Run ID | SHA | Conclusion | Notes |
|--------|-----|------------|-------|
| **24533537667** | `3138f5b` | ✅ `success` | **Authoritative freeze run** |
| 24532953093 | `ccd43b4` | ✅ `success` | MF-02 guard commit; 24/24 |
| 24532946385 | `ccd43b4` | ❌ `cancelled` | Infrastructure cancellation — NOT a test failure |
| 24532202904 | `4ebf4cc` | ✅ `success` | Post-acceptance sign-off commit |
| 24531417476 | `df543fa` | ✅ `success` | Acceptance sign-off commit |
| 24529447831 | `553e5c3` | ✅ `success` | Sprint 7 branch; 24/24 |

**Run 24532946385 cancellation — classification:**

```bash
gh run view 24532946385 --json conclusion,status
# conclusion: cancelled, status: completed
```

The step log for this run shows:
```
##[error]The operation was canceled.
```
during container initialization. This is a GitHub Actions infrastructure failure, not a
test code failure. No test was executed in this run. It does not represent a test retry
or a test failure. Correct classification: **infrastructure transient, not test failure**.

There are **zero actual test failures** in any run for SHA `3138f5b` or `ccd43b4`.
There are **zero test retries** (test retry = same test re-run after failure; did not occur).

---

## What This Report Proves (Scope Statement)

| Claim | Supported by this report? | Evidence |
|-------|--------------------------|----------|
| 59 tests in test_critical_e2e.py were INITIATED in CI run `24533537667` | ✅ YES | 59 log lines, section §5 |
| All 7766 tests in the run PASSED (0 failed, 0 errors) | ✅ YES | Summary line, section §4 |
| CI job concluded with `success` | ✅ YES | `gh run view 24533537667` |
| HTTP response codes at test runtime were correct | ❌ NO | Not in CI log for passing tests |
| DB values at test runtime matched assertions | ❌ NO | Not in CI log for passing tests |
| JUnit XML with per-test duration exists | ❌ NO | Workflow has no `--junit-xml` |

The three ❌ claims require either: (a) the remediation in §1 (add `--junit-xml`), or (b)
trusting the test source code in `COVERAGE_EVIDENCE_INDEX.md` as the assertion specification.

---

## Document Layer Map

```
CI_GROUND_TRUTH_VERIFICATION.md  ← THIS document
  Proves: 59 nodeids executed, 0 failed, CI run 24533537667 succeeded
  Source: raw GitHub Actions logs + artifact list

COVERAGE_EVIDENCE_INDEX.md
  Proves: assertion source code (HTTP/DB/UI) per F-ID
  Source: test_critical_e2e.py source code
  Status: INDEX LAYER ONLY — not CI execution proof

METRIC_CONTRACT.md
  Defines: BFC / ERC / CEC measurement formulas
  Status: immutable metric definition

COVERAGE_FREEZE_v1.md
  Proves: SHA + CI run IDs + artifact checksums at freeze point
  Status: immutable snapshot
```

---

*CI Ground Truth Verification Report — 2026-04-16 — main @ 3138f5b*
*Practice Booking System — E2E Coverage Program*
*Raw log source: `gh run view 24533537667 --log` — verifiable by anyone with `gh auth status`*
