# AN-2 PR Readiness Report

**PR:** [#296](https://github.com/football-investment/practice-booking-system/pull/296)
**Title:** `feat(ios): add juggling annotation data and sync layer`
**Branch:** `feat/ios-juggling-annotation-data-an2` → **base:** `main`
**HEAD SHA:** `f076adaa6530ba54698cbeb69c587899176d9e67`
**Snapshot time:** 2026-06-13 (CI in progress — see §8, this report will need
a follow-up CI-status update before merge)

---

## 1. Commit list (11 commits, base `f1054056`)

```
0b0e87cb feat(ios): add deterministic bundled juggling taxonomy
02e09544 feat(ios): add annotation API contracts and raw HTTP client support
1f261bc9 feat(ios): add versioned atomic annotation store with user isolation
8e96395d feat(ios): add annotation sync and reconciliation engine
04aa7c58 feat(ios): add annotation view model with finish guard and recovery
1c06e5bd test(ios): add AN-2 taxonomy, persistence, sync and recovery coverage
0100a944 docs(ios): add AN-2 implementation report
5695af86 build(ios): restore LFAEducationCenter unit test target and wire AN-2 sources
213fac4c fix(ios): resolve AN-2 compile and contract issues
7783e95e test(ios): complete AN-2 sync, reconciliation and Finish guard coverage
f076adaa docs(ios): add AN-2 Xcode build and validation evidence
```

---

## 2. Changed files (21)

```
docs/AN2_IMPLEMENTATION_REPORT.md                                    +183
docs/AN2_XCODE_INTEGRATION_VALIDATION_REPORT.md                      +384
ios/LFAEducationCenter.xcodeproj/project.pbxproj                     +201
ios/LFAEducationCenter/Auth/AuthManager.swift                        +37
ios/LFAEducationCenter/Juggling/Annotation/AnnotationSyncEngine.swift +265
ios/LFAEducationCenter/Juggling/Annotation/ContactEventDraft.swift   +81
ios/LFAEducationCenter/Juggling/Annotation/ContactEventResponse.swift +163
ios/LFAEducationCenter/Juggling/Annotation/ContactTaxonomy.swift     +101
ios/LFAEducationCenter/Juggling/Annotation/ContactTaxonomyStore.swift +159
ios/LFAEducationCenter/Juggling/Annotation/JugglingAnnotationAPIClient.swift +236
ios/LFAEducationCenter/Juggling/Annotation/JugglingAnnotationViewModel.swift +238
ios/LFAEducationCenter/Juggling/Annotation/LocalAnnotationStore.swift +168
ios/LFAEducationCenter/Juggling/Annotation/Resources/contact_types_v1.json +507
ios/LFAEducationCenter/Juggling/JugglingVideoItem.swift              +4
ios/LFAEducationCenter/Networking/APIClient.swift                    +73
ios/LFAEducationCenterTests/Juggling/AnnotationSyncEngineTests.swift +359
ios/LFAEducationCenterTests/Juggling/ContactEventDraftTests.swift    +139
ios/LFAEducationCenterTests/Juggling/ContactTaxonomyTests.swift      +80
ios/LFAEducationCenterTests/Juggling/JugglingAnnotationViewModelTests.swift +69
ios/LFAEducationCenterTests/Juggling/LocalAnnotationStoreTests.swift +128
scripts/check_taxonomy_bundle_drift.py                               +76
```
All additions, 0 deletions (net-new feature on top of AN-1).

---

## 3. Scope proof

**Present in the diff (all expected, all in-scope):**
- AN-2 production Swift files: `ContactTaxonomy.swift`,
  `ContactTaxonomyStore.swift`, `ContactEventDraft.swift`,
  `ContactEventResponse.swift`, `LocalAnnotationStore.swift`,
  `AnnotationSyncEngine.swift`, `JugglingAnnotationAPIClient.swift`,
  `JugglingAnnotationViewModel.swift`, plus small additive edits to
  `AuthManager.swift`, `APIClient.swift`, `JugglingVideoItem.swift`
  (raw HTTP helpers + `annotation_status` field)
- Taxonomy resource: `contact_types_v1.json`
- Xcode project/test-target wiring: `project.pbxproj` (+201 lines,
  0 deletions — new `LFAEducationCenterTests` target only)
- AN-2 + related unit tests: 5 files under
  `ios/LFAEducationCenterTests/Juggling/`
- Validation/implementation docs: `AN2_IMPLEMENTATION_REPORT.md`,
  `AN2_XCODE_INTEGRATION_VALIDATION_REPORT.md`
- `scripts/check_taxonomy_bundle_drift.py` (drift guard used by the
  taxonomy bundle proof)

**Confirmed absent:**
- No SwiftUI annotation screen / picker / timeline / video overlay
- No backend route, model, schema, or migration (`app/` untouched,
  `tests/snapshots/openapi_snapshot.json` untouched)
- No unrelated Biometric or UI refactor (`SpikeLivenessViewModel.swift`,
  `AcademyIDColorPickerView.swift`, `BiometricPhotoCapture.swift` etc. are
  NOT in this diff)
- No build artifacts, `DerivedData`, `.xcresult`, or `ios/build/` —
  `git status` for `ios/` shows only pre-existing untracked items
  (xcuserdata, `ios/ios/`, `ios/LFASkeleton.xcodeproj/`, an unrelated
  `LionCrest.imageset` and `BiometricPhotoCapture.swift` from other
  in-progress work on this machine), none staged or part of the PR

**Scope verdict: clean — no drift.**

---

## 4. Xcode build proof (local, from validation report)

- `xcodebuild -list`: 2 targets (`LFAEducationCenter` app,
  `LFAEducationCenterTests` unit-test bundle, GUID
  `CC100002000000000000001`, `TestTargetID` = app target),
  `plutil -lint` → OK
- Debug simulator build (`iPhone 16`, iOS 18.0,
  UDID `96E63DB5-3974-445F-944B-AB1D2DBB9923`): **BUILD SUCCEEDED**,
  4 warnings, all pre-existing/unrelated to AN-2
- Release simulator build (same destination): **BUILD SUCCEEDED**,
  2 warnings, both pre-existing/unrelated to AN-2

---

## 5. Unit test proof (local)

- Full suite: **56/56 PASS**, 0 failures (`/tmp/an2_full_test.xcresult`)
- AN-2 suite (`-only-testing` filter on AnnotationSyncEngineTests,
  ContactEventDraftTests, ContactTaxonomyTests, LocalAnnotationStoreTests,
  JugglingAnnotationViewModelTests): **37/37 PASS**, 0 failures
  (`/tmp/an2_filtered.xcresult`)
- 12 new tests (AN2-T26..T37) close all 15 items from the section-5
  sync/reconciliation/Finish gap list

---

## 6. Taxonomy drift proof

```
$ python3 scripts/check_taxonomy_bundle_drift.py
OK — bundled taxonomy matches dataset source (md5=7198de62733493537450275dadc6f445)
```
`contact_types_v1.json` is present at the root of both the Debug and
Release built `.app` bundles, MD5 `7198de62733493537450275dadc6f445` —
identical to the dataset source-of-truth checksum.

---

## 7. Local store / reconciliation / user-isolation / Finish-guard proof

- **Local store** (`LocalAnnotationStoreTests` T05-T10, 6/6 PASS):
  empty load, save/load round trip, per-user isolation
  (`juggling_annotations/{userId}/{videoId}.json`), corrupt-file
  quarantine (not deleted), checksum-mismatch quarantine, delete removes
  file.
- **Reconciliation** (`AnnotationSyncEngineTests` T23/T24 pre-existing,
  T32/T33 new): server-present `needsReconciliation` draft → `.synced`
  with server state applied; absent → `.deleted`; unrelated server-side
  events for other device-event-ids are ignored; non-`needsReconciliation`
  drafts are left untouched by `reconcile`.
- **User isolation**: `LocalAnnotationStoreTests` T07 — two different
  `userId` values never read each other's session file
  (`juggling_annotations/{userId}/...`); `JugglingAnnotationViewModel`'s
  `userId` is immutable per instance (set at init, cannot be re-targeted).
- **Finish guard** (`ContactEventDraftTests` T34/T35,
  `JugglingAnnotationViewModelTests` T36/T37): all 7 blocking sync statuses
  (`localOnly, syncing, updating, deleting, retryPending, conflicted,
  needsReconciliation`) individually block `finishReadiness`;
  `failedPermanent` does not block; zero active events →
  `confirm_zero_contacts=true` + session cleared on success; permanent API
  error on finish → `finishError` set, session preserved for retry.

---

## 8. CI status (snapshot — IN PROGRESS, not yet final)

```
mergeable        = MERGEABLE
mergeStateStatus = BLOCKED   (checks still running)
reviewDecision   = (none yet)
```

| Check | Status |
|---|---|
| API Module Integrity (import + route count) | ✅ SUCCESS |
| Hardcoded FK ID Guard (lint) | ✅ SUCCESS |
| Credit Balance Audit (static AST) | ✅ SUCCESS |
| Migration Chain Integrity (empty → head → base → head) | ✅ SUCCESS |
| Migration Volume Safety (data-present roundtrip) | ✅ SUCCESS |
| Operational Smoke Test (full stack) | ✅ SUCCESS |
| Skill Weight Pipeline — 28 required tests | ✅ SUCCESS |
| Preset Weight Audit (informational) | ⏭️ SKIPPED (informational, non-blocking) |
| Unit Tests (Baseline: 0 failed, 0 errors) | ⏳ IN PROGRESS |
| API Tests (Baseline: 0 failed, 0 errors) | ⏳ IN PROGRESS |
| Load Gate — Phase 6.3 (50 VUs, 10 min) | ⏳ IN PROGRESS |
| Migration Chain Integrity rerun (cypress prep) | ⏳ IN PROGRESS |
| cypress-web-by-role (admin) | ⏳ IN PROGRESS |
| cypress-web-by-role (instructor) | ⏳ IN PROGRESS |
| cypress-web-by-role (student) | ⏳ IN PROGRESS |
| cypress-web-by-role (business-workflow) | ⏳ IN PROGRESS |

**0 failed, 0 pending-forever, 6 in progress** at snapshot time. No iOS
build/test check exists in this repository's required-checks set — see
§9.

---

## 9. iOS CI gap (flagged separately, per instruction)

This repository's CI does **not** run an iOS build or `xcodebuild test`
job — all required/optional checks visible on PR #296 are backend
(API/DB/migrations), web (Cypress), and load-test jobs. There is no
"iOS build check", "unit tests (iOS)", or "project integrity (iOS)" gate.

Per instruction, **the local Xcode build/test evidence in
`docs/AN2_XCODE_INTEGRATION_VALIDATION_REPORT.md` (Debug+Release
BUILD SUCCEEDED, 56/56 + 37/37 unit tests PASS, taxonomy checksum proof)
remains the required merge evidence for the iOS portion of this PR** —
this is a pre-existing repository-CI gap, not something introduced or
fixable by this PR.

---

## 10. Pre-existing excluded Biometric test files

| File | Why excluded | Previously in a target? | Causes regression? | Follow-up needed? |
|---|---|---|---|---|
| `ios/LFAEducationCenterTests/Biometric/BiometricPhotoCaptureTests.swift` | Excluded in the prior AN-2 session for an analogous compile incompatibility with current production code | No — no test target existed in `project.pbxproj` at all before AN-2 added one | No — it has never built or run; AN-2 introduces the first working test target and these 3 files are simply not members of it, exactly as before | Yes — separate follow-up to fix/rewrite against current Biometric production code, out of AN-2 scope |
| `ios/LFAEducationCenterTests/Biometric/SpikeLivenessViewModelPR2Tests.swift` | Assigns to `@Published private(set) var stepState`/`currentStepIndex` and reads `private let sequence` on `SpikeLivenessViewModel` from a same-module extension in a different file — `private` is file-scoped, inaccessible even with `@testable import`, under the active Swift 6 toolchain | No (see above) | No (see above) | Yes — requires either changing `SpikeLivenessViewModel`'s access levels (production-code change, Biometric feature) or rewriting the test; out of AN-2 scope |
| `ios/LFAEducationCenterTests/Biometric/FaceGestureDetectorTests.swift` | Calls `.neutral()`/`.headLeft()`/etc. on a `FaceAnchorInput` *protocol existential* parameter; Swift resolves these against the protocol's own (empty) static members rather than the concrete `MockFaceAnchorInput` type — a Swift-toolchain/implicit-member-lookup incompatibility | No (see above) | No (see above) | Yes — requires either an explicit factory/cast at each call site (test-only rewrite) or a protocol-design change; out of AN-2 scope |

**None of these three files were ever part of a working target** — the
`LFAEducationCenterTests` target itself did not exist in `project.pbxproj`
prior to this PR (commit `5695af86`). Their exclusion therefore changes
nothing about what built or ran before; it is **not a regression**, and
all 56 tests that now run (including the 25 pre-AN-2 + 12 new AN-2 tests
and the 19 pre-existing `JugglingVideoTests`) pass. A follow-up
issue/PR to repair these 3 Biometric test files against the current
Swift 6 toolchain and `SpikeLivenessViewModel`/`FaceGestureDetector`
production code is recommended, scoped to the Biometric feature track —
**not stated as "problem-free without evidence"**, but as a documented,
non-blocking, pre-existing gap.

---

## 11. Review status

`reviewDecision` is empty — **no reviews yet**.

---

## 12. git status (local, final)

```
$ git status --porcelain=v1 -- ios/ docs/
?? docs/AN2_XCODE_INTEGRATION_VALIDATION_PLAN.md
?? docs/P5_PHASE2_STAGING_EXECUTION_CHECKLIST.md
?? docs/P5_STAGING_TEST_DATA_EXECUTION_PLAN.md
?? ios/LFAEducationCenter.xcodeproj/project.xcworkspace/xcuserdata/
?? ios/LFAEducationCenter.xcodeproj/xcuserdata/
?? ios/LFAEducationCenter/Assets.xcassets/LionCrest.imageset/
?? ios/LFAEducationCenter/Biometric/Spike/BiometricPhotoCapture.swift
?? ios/LFASkeleton.xcodeproj/
?? ios/ios/
```
All untracked entries pre-date this PR and belong to other in-progress
work on this machine (Biometric MVP, staging docs) — none are part of
PR #296's diff.

---

## 13. Final verdict

**`BLOCKED_CI`**

Rationale: the PR is `MERGEABLE` with a clean scope (§3), full local
Xcode build/test evidence (§4-§7), and 0 failed checks so far — but
`mergeStateStatus = BLOCKED` because 6 required CI checks (Unit Tests,
API Tests, Load Gate, 4× Cypress) are still `IN_PROGRESS` at snapshot
time, and `reviewDecision` is empty (no review yet). No PR merge has
been performed. AN-3 has not been started.

This report will be updated once CI completes (either to
`AN2_PR_READY_FOR_REVIEW` if all required checks pass with 0 failures,
or to a more specific `BLOCKED_*` if any check fails).
