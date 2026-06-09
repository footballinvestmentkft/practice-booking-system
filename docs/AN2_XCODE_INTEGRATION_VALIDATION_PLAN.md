# AN-2 — Xcode Integration and Runtime Validation Plan

**Status:** DRAFT — awaiting approval. No `.pbxproj` edits, builds, or test
runs have been performed yet. This document is the read-only audit +
implementation plan requested before any further work on AN-2.

**Branch:** `feat/ios-juggling-annotation-data-an2` (base `f1054056`, main)
**Scope:** Xcode project wiring only — unit test target, scheme, AN-2 source
membership. No SwiftUI, no AN-3, no backend changes, no PR.

---

## 1. Xcode project audit (evidence)

### 1.1 `xcodebuild -list`

```
$ xcodebuild -list -project LFAEducationCenter.xcodeproj
Targets:
    LFAEducationCenter
Schemes:
    LFAEducationCenter
```

Only **one target** and **one scheme** are visible to `xcodebuild`.

### 1.2 `project.pbxproj` — targets

```
$ grep -n "isa = PBXNativeTarget\|productType" project.pbxproj
470: /* Begin PBXNativeTarget section */
472:    isa = PBXNativeTarget;
483:    name = LFAEducationCenter;
486:    productType = "com.apple.product-type.application";
488: /* End PBXNativeTarget section */
```

Exactly **one** `PBXNativeTarget`, `productType = "com.apple.product-type.application"`.
`PBXProject.targets = (BB000001000000000000002 /* LFAEducationCenter */)` — single entry.

No `PBXFileSystemSynchronizedRootGroup` / `fileSystemSynchronizedGroups` (0
matches) — this project uses classic explicit `PBXFileReference` +
`PBXBuildFile` + `PBXGroup`, not Xcode-16 folder sync. Every source file that
should compile must have an explicit `PBXBuildFile` entry in the `Sources`
build phase.

### 1.3 AN-2 files are NOT referenced anywhere in `project.pbxproj`

```
$ grep -n "Annotation|ContactTaxonomy|ContactEventDraft|AnnotationSyncEngine|\
JugglingAnnotationViewModel|JugglingAnnotationAPIClient|ContactEventResponse|\
LocalAnnotationStore|contact_types_v1" project.pbxproj
(0 matches)
```

On disk, `ios/LFAEducationCenter/Juggling/Annotation/` contains 8 new Swift
files + `Resources/contact_types_v1.json` — **none are PBXFileReference /
PBXBuildFile members**. The existing `Juggling` PBXGroup
(`BC53ECBBF23C75435C8218AB`) only lists the 4 pre-AN-2 files
(`JugglingVideoItem.swift`, `JugglingVideoListView.swift`,
`JugglingVideoListViewModel.swift`, `JugglingPlayerView.swift`).

### 1.4 No unit test target — but a **dangling scheme reference** to one

`LFAEducationCenterTests/` exists on disk (8 files: 3 Biometric, 4 new AN-2
Juggling, 1 pre-existing `JugglingVideoTests.swift`) but has **zero**
`PBXGroup` / `PBXFileReference` / `PBXBuildFile` entries — confirmed by the
`Tests|TEST_HOST|TestTarget|XCTest` grep (0 matches) and by the absence of any
second `PBXNativeTarget`.

However, the **shared scheme**
(`xcshareddata/xcschemes/LFAEducationCenter.xcscheme`, committed in
`e67ee91f`) already contains a `TestAction` and a second `BuildActionEntry`
pointing at:

```xml
BlueprintIdentifier = "CC100002000000000000001"
BuildableName = "LFAEducationCenterTests.xctest"
BlueprintName = "LFAEducationCenterTests"
```

— a target ID that **does not exist** in `project.pbxproj`. `git log -S
"LFAEducationCenterTests.xctest" -- project.pbxproj` returns no commits, so
this target was never added to the project file. This is why `xcodebuild
-list` shows only one target/scheme name even though the scheme XML
internally references two: the scheme is broken/incomplete, not absent.

Separately, the gitignored `ios/build/` and `ios/ios/build_sim/` directories
contain **stale build products** for `LFAEducationCenterTests.xctest`
(`empty-LFAEducationCenterTests.plist`, `.swiftsourceinfo`, etc.) — evidence
that a working test target configuration existed in someone's local Xcode
session at some point, but was never committed. These directories are
build output (gitignored), not source of truth, and will not be used as a
basis for the new target — the new target will be built fresh against
`CC100002000000000000001` to match the scheme's existing (dangling)
reference, avoiding a second scheme edit.

### 1.5 Out of scope / untracked clutter (will not be touched)

`ios/ios/` and `ios/LFASkeleton.xcodeproj/` are untracked, unrelated trees
(per `git status`). They are not referenced by `LFAEducationCenter.xcodeproj`
and will be left alone.

### 1.6 Resources build phase

```
Resources = (
    Assets.xcassets,
    skeleton_receiver.html,
)
```
`contact_types_v1.json` is not present — must be added as a Copy Bundle
Resources member of the **app** target (taxonomy is loaded at runtime by the
app, not just by tests).

---

## 2. Unit test target restoration plan

A new `PBXNativeTarget` will be added to `project.pbxproj`:

- **Name:** `LFAEducationCenterTests`
- **GUID:** `CC100002000000000000001` (reuses the ID the scheme already
  references — makes the scheme valid with a minimal diff, no need to
  regenerate `xcshareddata`)
- **productType:** `com.apple.product-type.bundle.unit-test`
- **productReference:** new `LFAEducationCenterTests.xctest` in `Products`
  group
- **Build settings (Debug + Release):**
  - `TEST_HOST = "$(BUILT_PRODUCTS_DIR)/LFAEducationCenter.app/LFAEducationCenter"`
  - `BUNDLE_LOADER = "$(TEST_HOST)"`
  - `TARGETED_DEVICE_FAMILY`, `IPHONEOS_DEPLOYMENT_TARGET` matching the app
    target
  - `GENERATE_INFOPLIST_FILE = YES` (avoids hand-writing a test Info.plist)
  - `SWIFT_VERSION` matching app target
- **Target dependency:** `LFAEducationCenterTests` depends on
  `LFAEducationCenter` (PBXTargetDependency + PBXContainerItemProxy)
- **PBXProject.targets:** append the new target
- **New PBXGroup** `LFAEducationCenterTests` (path `LFAEducationCenterTests`)
  mirroring the on-disk tree: `Biometric/` (3 files) + `Juggling/` (4 AN-2
  files) + `JugglingVideoTests.swift` (8 files total)
- **Sources build phase** for the new target: all 8 files above
- This is **not** a UI test target (`xctest` bundle, no
  `com.apple.product-type.bundle.ui-testing`, no `XCUIApplication`).

The scheme (`LFAEducationCenter.xcscheme`) already has the correct
`TestAction`/`BuildActionEntry` referencing `CC100002000000000000001` — by
reusing that GUID for the real target, **the scheme file needs no edits**.
This keeps the diff to `project.pbxproj` only (reviewable as a single,
mechanical, append-only diff: new target, new build phases, new group, new
build configuration list, two appended `targets`/`children` array entries).

---

## 3. AN-2 app target integration plan

Add to the **existing** `LFAEducationCenter` app target:

**Compile Sources** (new `PBXFileReference` + `PBXBuildFile`, appended to the
existing `Juggling` PBXGroup and `Sources` build phase):
- `ContactTaxonomy.swift`
- `ContactTaxonomyStore.swift`
- `ContactEventDraft.swift`
- `ContactEventResponse.swift`
- `LocalAnnotationStore.swift`
- `AnnotationSyncEngine.swift`
- `JugglingAnnotationAPIClient.swift`
- `JugglingAnnotationViewModel.swift`

These will live under a new `Annotation` sub-group inside the `Juggling`
PBXGroup (matching the on-disk `Juggling/Annotation/` path).

**Copy Bundle Resources** (new `PBXFileReference` + `PBXBuildFile` in the
`Resources` build phase):
- `Juggling/Annotation/Resources/contact_types_v1.json`

**Already-modified files** (`APIClient.swift`, `AuthManager.swift`,
`JugglingVideoItem.swift`) are already app-target members — no membership
change needed, only their contents changed (already committed).

**Test-only membership guarantee:** the 4 AN-2 test files
(`ContactTaxonomyTests.swift`, `LocalAnnotationStoreTests.swift`,
`ContactEventDraftTests.swift`, `AnnotationSyncEngineTests.swift`) will be
added **only** to the new `LFAEducationCenterTests` target's Sources phase —
never to the app target's Sources phase. Verified post-edit by grepping the
app target's Sources build phase for `Tests.swift` (must be 0 matches).

---

## 4. Build proof plan

Commands to run, in order, with full stdout/stderr captured:

1. `xcodebuild -list -project LFAEducationCenter.xcodeproj` — confirm 2
   targets, 1 scheme, test target visible.
2. `xcodebuild -showBuildSettings -scheme LFAEducationCenter -configuration Debug` —
   sanity check `TEST_HOST`/`BUNDLE_LOADER` resolve.
3. `xcodebuild build -scheme LFAEducationCenter -configuration Debug -destination 'generic/platform=iOS Simulator'` —
   app target Debug build.
4. `xcodebuild build -scheme LFAEducationCenter -configuration Debug -destination 'platform=iOS Simulator,name=<available simulator>'` —
   concrete simulator build (simulator name resolved via `xcrun simctl list
   devices available` at execution time).
5. `xcodebuild build -scheme LFAEducationCenter -configuration Release -destination 'generic/platform=iOS Simulator'` —
   Release build, best-effort (Release build of a `.xctest`-bearing scheme
   can be skipped for the test target; app target Release build is the goal).
6. Warnings audit: `grep -c "warning:"` on build logs for steps 3–4; any new
   warning attributable to AN-2 files must be fixed (commit 3 below) or
   explicitly justified.
7. Resource bundle proof: after step 3/4, inspect
   `<DerivedData>/.../LFAEducationCenter.app/contact_types_v1.json` (or
   `.app/*.bundle/contact_types_v1.json` if nested) exists.

Report format per command: scheme, destination, configuration, exit code,
`** BUILD SUCCEEDED **` / `** BUILD FAILED **` line, and (if failed) the
first real error from the log.

---

## 5. Test proof plan

1. `xcodebuild test -scheme LFAEducationCenter -destination 'platform=iOS Simulator,name=<sim>' -resultBundlePath AN2_TestResults.xcresult`
2. Parse `.xcresult` (via `xcrun xcresulttool get --legacy --format json` or
   `xcrun xcresulttool get test-results summary`) for:
   - total executed / passed / failed / skipped
   - per-test breakdown for `AN2-T01`..`AN2-T25` (4 files)
   - existing suites: `JugglingVideoTests`, `Biometric/*Tests`
   - wall-clock test time
3. If any test fails, capture the exact assertion/failure message and file:line.
4. Run `python3 scripts/check_taxonomy_bundle_drift.py` again post-wiring
   (no behavior change expected, re-confirms checksum match).
5. `.xcresult` bundle path and a plain-text summary will be included in the
   final report; the bundle itself is large/binary and will **not** be
   committed to git — only referenced by path (and optionally summarized
   into the docs report).

---

## 6. Backend contract fixture audit plan

Cross-check `ContactEventResponse.swift` and `JugglingAnnotationAPIClient`
error classification against `tests/snapshots/openapi_snapshot.json` @
`f1054056` for each AN-1 endpoint:

| Behavior | Expected | Where verified |
|---|---|---|
| Single create 201 | `CreateContactResult.isNew = true` | `AnnotationSyncEngineTests` T17 (mock) + schema field check |
| Exact duplicate 200 | `CreateContactResult.isNew = false`, same `event_id` | schema check (200 response schema vs 201) |
| Batch 201/200/207 | `ContactEventBatchResult` with `created`/`duplicate_skipped`/`conflict`/`results[]` | T16 decode test + schema |
| PATCH 200 | `ContactEventOut` decode, `version` incremented | schema field check (no direct test yet — noted as gap if absent) |
| DELETE 204 | `DeleteContactResult.deleted` | T21 |
| DELETE 404 (ambiguous) | `DeleteContactResult.notFoundAmbiguous` → `needsReconciliation` | T22 |
| Finish 200/409/422 | `FinishAnnotationOut` decode; 409/422 surfaced as `finishError` | schema check (no direct unit test — noted as gap if absent) |
| Taxonomy 200/304 | `ContactTaxonomyStore.refreshFromBackend` ETag handling | existing implementation, not re-tested here (AN-1 scope) |
| Error body decode | `AnnotationAPIError` cases map from `ErrorBody.detail` | T19 (403 `consent_blocked`) |

Any gap found (e.g., no direct PATCH-200 or Finish-409/422 decode test) will
be **listed as a known gap in the final report**, not silently added as new
test code beyond what's needed to make the existing 25 tests pass — adding
new tests is in-scope only if required to prove an already-claimed behavior
that currently has zero coverage and is cheap to add without expanding scope
into AN-3. This will be decided case-by-case and called out explicitly in the
final report rather than assumed now.

---

## 7. Bundled taxonomy proof plan

Within `ContactTaxonomyTests` (already exists, T01–T04) plus the drift script:

- T01: `ContactTaxonomyStore.bundledChecksum` constant == MD5 of bundled
  `contact_types_v1.json` (already written)
- T02: decode produces 18 total contact types
- T03: `stable_count == 17`, `custom_other` present, no entry named `thigh`
- T04: invalid `total_count` rejected
- `scripts/check_taxonomy_bundle_drift.py`: 3-way checksum match (dataset
  source ↔ bundled copy ↔ Swift constant)
- **New proof needed**: confirm the bundle *resource lookup itself* works at
  runtime inside the test host — i.e. `Bundle(for: ContactTaxonomyStoreTests.self)`
  or `Bundle.main` (whichever `ContactTaxonomyStore.loadBundled()` actually
  uses) successfully locates `contact_types_v1.json` after step 3's Copy
  Bundle Resources wiring. If `loadBundled()` currently hardcodes
  `Bundle.main`, the test (running inside the test bundle, hosted by the app)
  must still resolve it via `Bundle(for:)` or the host app bundle — this will
  be checked against the actual `ContactTaxonomyStore.swift` implementation
  before relying on T01–T04 as sufficient proof.

---

## 8. Local store runtime proof plan

Already covered by `LocalAnnotationStoreTests` T05–T10 (all using a real
temp directory via `FileManager.default.temporaryDirectory`, real disk I/O —
not mocked):

- T05 empty load
- T06 save/load round trip + checksum
- T07 user A / user B isolation (same videoId, different userId)
- T08 corrupt file → quarantine, bytes preserved
- T09 checksum mismatch → quarantine
- T10 delete → empty

**Gaps vs. the user's 8-point list** (to be confirmed True/False, not
assumed):
- "video A / video B isolation" — not yet a dedicated test (T07 only varies
  userId, same videoId). If `LocalAnnotationStore`'s path is
  `juggling_annotations/{userId}/{videoId}.json`, video isolation follows
  directly from the path structure, but no test currently asserts it
  explicitly.
- "app relaunch restore" — T06 *is* this (save in one `LocalAnnotationStore`
  instance, load in a fresh `store.load()` call simulates relaunch since the
  store is stateless / disk-backed). Will be confirmed by reading
  `LocalAnnotationStore.swift` to ensure no in-memory cache defeats this.
- "previous-good backup" (`.bak.json`) — implementation detail mentioned in
  the AN-2 report; no test currently asserts the `.bak.json` file exists or
  is used on restore. Will check the implementation and, if the backup path
  is dead code (never read back), flag it; if it's load-bearing, a small test
  may be added (same scope-cost judgment as section 6).

These gaps will be resolved by reading `LocalAnnotationStore.swift` during
implementation and either (a) confirming existing tests already cover the
behavior via the on-disk path structure, or (b) adding minimal targeted
tests, or (c) reporting as a known gap — decided per-item, reported in final
doc.

---

## 9. Sync state machine proof plan

Already covered by `AnnotationSyncEngineTests` T17–T25 + `ContactEventDraftTests`
T13–T15:

| Transition | Test |
|---|---|
| localOnly → syncing → synced (201) | T17 |
| 200 duplicate → synced | implied by `CreateContactResult.isNew=false` path — same code path as T17, `isNew` flag only affects reporting, not status; will confirm in code |
| network/retryable error → retryPending (retry count++) | T18 |
| retryable at maxRetries → failedPermanent | T20 |
| 409 idempotency conflict → failedPermanent | T19 (via `permanent(403,...)` — 409 idempotency case will be checked specifically) |
| 409 version conflict (PATCH) → conflicted | **no existing test** — `pushPatch` is not covered by T17-T25 (all current tests target `pushCreate`/`pushDelete`/`reconcile`). Gap. |
| PATCH timeout → needsReconciliation | **no existing test** — same gap |
| DELETE 204 → deleted | T21 |
| DELETE timeout/404 → needsReconciliation | T22 (404 case; timeout case not separately tested) |
| reconcile found → synced w/ server state | T23 |
| reconcile absent → deleted | T24 |
| retry delays 2/4/8s + jitter | `retryDelaysSeconds` constant exists and is asserted nowhere; jitter is described as "caller schedules" — not part of `AnnotationSyncEngine` itself per the implementation report. Will verify against code. |
| 422 → no retry | **no existing test** |
| Finish blocked by unsynced/conflicted/failed | T15 (`.blocked([.localOnly])` — only one state tested; `conflicted`/`failedPermanent`/`retryPending`/`needsReconciliation` blocking not individually tested) |
| Finish requires reconciliation first | covered at the `JugglingAnnotationViewModel.finish()` level per the implementation report, but **no unit test exists for the view model** — all 25 tests are taxonomy/store/draft/sync-engine only, zero `JugglingAnnotationViewModelTests`. |

**This is the largest gap area.** `pushPatch` (version conflict +
reconciliation), the 422-no-retry rule, and the full `FinishReadiness.blocked`
enumeration over all blocking states have no direct test today. Per the scope
guard (section 10), closing these gaps with **new unit tests against the
existing `AnnotationSyncEngine`/`ContactEventDraft`/`JugglingAnnotationViewModel`
APIs** is in-scope (it's test-only, exercises code already written, no new
production behavior) — these would be additional tests beyond the original
25, added in commit 4 (test enablement) if time permits, otherwise reported
as explicit gaps with a verdict that still allows
`AN2_XCODE_VALIDATED_READY_FOR_PR` (build/test infra working is the gate;
100% transition coverage is not, but will be transparently reported either
way).

---

## 10. Scope guard

**Allowed in this work:**
- `LFAEducationCenter.xcodeproj/project.pbxproj` — new test target, target
  membership for AN-2 sources + resource, new group entries
- `LFAEducationCenterTests/` — no new files unless closing a sync-state-machine
  gap from section 9 (test-only, same APIs)
- Compile/contract fixes to AN-2 production files **only if** the build/tests
  reveal a real error (signature mismatch, missing import, etc.) — fixes
  will be minimal and scoped to the specific error
- `docs/` — new validation report

**Forbidden (re-confirmed):**
- No SwiftUI annotation screen, picker, timeline, or video overlay
- No AN-3 UX
- No backend/API changes — `tests/snapshots/openapi_snapshot.json` and
  `app/` stay untouched
- No PR opened
- `ios/ios/`, `ios/LFASkeleton.xcodeproj/`, `ios/build/` — untouched (out of
  scope per section 1.5)

---

## 11. Commit structure

1. `build(ios): add unit test target and shared scheme`
   — new `PBXNativeTarget LFAEducationCenterTests`, build configs, target
   dependency, reuses scheme's existing `CC100002000000000000001` reference
   (no scheme file edit needed per 1.4/2)
2. `build(ios): wire AN-2 sources and taxonomy resource into app target`
   — 8 Swift files + `contact_types_v1.json` added to app target Compile
   Sources / Copy Bundle Resources; 8 test files (4 AN-2 + `JugglingVideoTests`
   + 3 Biometric) added to test target Sources
3. `fix(ios): resolve AN-2 compile and contract issues` — only if build/test
   reveals real errors; **may be empty/omitted** if nothing needs fixing
4. `test(ios): enable and validate AN-2 unit test suite` — any new tests
   closing section-9 gaps (pushPatch conflict/reconciliation, 422-no-retry,
   full Finish-blocked enumeration), if added
5. `docs(ios): add AN-2 build and test evidence` — the final validation
   report

If commit 3 is empty, it will be omitted rather than created as a no-op.

---

## 12. Final deliverable

`docs/AN2_XCODE_INTEGRATION_VALIDATION_REPORT.md` containing:
- branch, base SHA, HEAD SHA, commit list
- `.pbxproj` target list (before/after), scheme list, target membership diffs
- changed-file list
- app build result (scheme/destination/configuration/exit code per section 4)
- unit test result (counts, timing, failures, `.xcresult` path per section 5)
- taxonomy resource proof (section 7)
- contract decode proof (section 6, including any gaps)
- local store proof (section 8, including any gaps)
- sync proof (section 9, including any gaps)
- warnings audit
- scope-guard confirmation (diff stat showing only allowed paths touched)
- final `git status`
- **Final verdict** — exactly one of:
  `AN2_XCODE_VALIDATED_READY_FOR_PR`, `BLOCKED_TEST_TARGET`,
  `BLOCKED_APP_BUILD`, `BLOCKED_UNIT_TESTS`, `BLOCKED_RESOURCE_BUNDLE`,
  `BLOCKED_CONTRACT_DECODE`, `BLOCKED_SCOPE_CONTAMINATION`

---

## Open items requiring no further user input but flagged for transparency

- The exact simulator destination for steps 4/5 will be picked from
  `xcrun simctl list devices available` at execution time (first available
  iOS runtime matching `IPHONEOS_DEPLOYMENT_TARGET = 14.0`+).
- Section 9's gap-closing tests are additive and test-only; if they reveal a
  real bug in `AnnotationSyncEngine`, fixing it is covered by commit 3 (still
  within "AN-2 compile and contract issues" — a sync-state-machine bug found
  by a new unit test is a contract issue, not new feature work).

**No implementation, `.pbxproj` edits, builds, or test runs will begin until
this plan is approved.**
