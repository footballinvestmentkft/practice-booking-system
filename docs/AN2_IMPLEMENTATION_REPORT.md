# AN-2 — iOS Data and Synchronization Layer — Implementation Report

**Branch:** `feat/ios-juggling-annotation-data-an2` (base `f1054056`, main)
**Date:** 2026-06-13
**Scope:** Data/sync layer only. No SwiftUI screens, no pickers, no timeline,
no video overlay, no AN-3 UX, no backend changes. PR not opened (per
instruction).

---

## 1. Commits (6, consolidated from the 7-item roadmap)

| # | Commit | Files |
|---|--------|-------|
| 1 | `0b0e87cb` feat(ios): add deterministic bundled juggling taxonomy | `ContactTaxonomy.swift`, `ContactTaxonomyStore.swift`, `Resources/contact_types_v1.json`, `scripts/check_taxonomy_bundle_drift.py` |
| 2 | `02e09544` feat(ios): add annotation API contracts and raw HTTP client support | `ContactEventResponse.swift`, `JugglingAnnotationAPIClient.swift`, `APIClient.swift` (+getRaw/postRaw/patchRaw), `AuthManager.swift` (+authenticatedGetRaw/PostRaw/PatchRaw) |
| 3 | `1f261bc9` feat(ios): add versioned atomic annotation store with user isolation | `ContactEventDraft.swift`, `LocalAnnotationStore.swift` |
| 4 | `8e96395d` feat(ios): add annotation sync and reconciliation engine | `AnnotationSyncEngine.swift` |
| 5 | `04aa7c58` feat(ios): add annotation view model with finish guard and recovery | `JugglingAnnotationViewModel.swift`, `JugglingVideoItem.swift` (+`annotation_status`) |
| 6 | `1c06e5bd` test(ios): add AN-2 taxonomy, persistence, sync and recovery coverage | 4 test files, 25 tests |

The original roadmap's item 6 ("user isolation and recovery handling") was
folded into commits 3 and 5 — `userId` isolation and quarantine recovery are
load-bearing parts of `LocalAnnotationStore` and `JugglingAnnotationViewModel`
respectively, not a separable change. Splitting them out would have produced
an empty or purely-documentation commit.

---

## 2. What was built

### 2.1 Taxonomy (Commit 1)
- `ContactTaxonomy.swift`: `TaxonomyDocument` / `TaxonomyGroup` /
  `TaxonomyContactType`, exact `Codable` mirror of
  `datasets/juggling/contact_types_v1.json`.
- `ContactTaxonomyStore`: `loadBundled()` (sync, always available) +
  `refreshFromBackend()` (async, ETag/304, silent fallback to bundled/cached
  on any error — an annotation session is never blocked by a taxonomy
  network failure).
- Bundled copy at
  `ios/LFAEducationCenter/Juggling/Annotation/Resources/contact_types_v1.json`
  — MD5 `7198de62733493537450275dadc6f445`, identical to
  `datasets/juggling/contact_types_v1.json` and to
  `ContactTaxonomyStore.bundledChecksum`.
- `scripts/check_taxonomy_bundle_drift.py`: compares dataset source ↔
  bundled copy ↔ the `bundledChecksum` Swift constant; exit 1 on any
  mismatch. **Run now: OK.**

### 2.2 API contracts + raw HTTP client (Commit 2)
- `ContactEventResponse.swift`: exact mirror of all AN-1 schemas, verified
  against `tests/snapshots/openapi_snapshot.json` @ `f1054056` — including
  the nullable fields (`side`, `annotation_status`, batch `event_id`/`detail`)
  and the `duplicate_skipped` wire key.
- `JugglingAnnotationAPIClient` + new `JugglingAnnotationAPIClientProtocol`
  (5 sync-relevant endpoints — taxonomy fetch stays separate, owned by
  `ContactTaxonomyStore`). Error classification:
  retryable / permanent / idempotencyConflict / versionConflict /
  unauthorized / decodeFailed.
- `APIClient`: added `getRaw` (raw `(Data, URLResponse)`, no 2xx check — lets
  304 pass through for ETag handling), `postRaw` / `patchRaw` (raw
  `(Data, statusCode)` for 2xx, `APIError.httpError` with parsed detail for
  non-2xx — needed to distinguish 200 dup vs 201 created, and to surface 409
  `idempotency_conflict` / `version_conflict` bodies).
- `AuthManager`: added `authenticatedGetRaw` (no 401 retry — `getRaw` never
  throws on 401, so taxonomy fallback handles it), `authenticatedPostRaw`,
  `authenticatedPatchRaw` (both with the existing single-refresh-and-retry
  pattern).

### 2.3 Local annotation store (Commit 3)
- `ContactEventDraft`: 10-state `ContactEventSyncStatus` (`localOnly`,
  `syncing`, `synced`, `updating`, `deleting`, `deleted`, `failedPermanent`,
  `retryPending`, `conflicted`, `needsReconciliation`), keyed by immutable
  `deviceEventId`.
- `LocalAnnotationStore`: one `AnnotationSessionFile` per `(userId, videoId)`
  at `juggling_annotations/{userId}/{videoId}.json`. Atomic writes (temp
  file → `replaceItemAt` with `.bak.json` backup). SHA256 checksum over the
  drafts array, verified on load. Decode failure or checksum mismatch →
  **quarantine** (move to `quarantine/`, never delete), with a best-effort
  scan for `"localOnly"` markers to flag possible data loss to the caller.

### 2.4 Sync / reconciliation engine (Commit 4)
- `AnnotationSyncEngine` (max 3 retries, 2s/4s/8s base delays — caller
  schedules with jitter):
  - `flushPending`: pushes `localOnly`/`retryPending` creates; drafts deleted
    locally before ever syncing → `.deleted` with no network call; drafts
    that finished syncing but were marked deleted in the meantime get a
    DELETE pushed.
  - `pushCreate`: 200/201 → `.synced`; retryable → `.retryPending`
    (`retryCount`++) until `maxRetries`, then `.failedPermanent`; 409
    `idempotency_conflict` → `.failedPermanent` (requires user resolution,
    not auto-retried).
  - `pushPatch`: 409 `version_conflict` → `.conflicted`; timeout/network →
    `.needsReconciliation` (PATCH is not idempotent — outcome unknown).
  - `pushDelete`: 404 → `.needsReconciliation` (ownership-ambiguous, **not**
    auto-success); timeout/network → `.needsReconciliation`.
  - `reconcile`: GET `/contacts` is the source of truth for every
    `.needsReconciliation` draft — present on server → `.synced` with server
    state applied; absent → `.deleted`.
  - `finishReadiness`: `.blocked([...])` while any active event is in-flight,
    retryable, conflicted, or unresolved; otherwise `.readyZero` /
    `.readyWithCount(n)`.

### 2.5 View model (Commit 5)
- `JugglingAnnotationViewModel` (`@MainActor`, `ObservableObject` — no
  SwiftUI view consumes it yet):
  - `onAppear()`: load bundled taxonomy → load/create local session (Hungarian
    user-facing warning on quarantine recovery) → background taxonomy
    refresh.
  - `addEvent` / `markDeleted` — local-first, persisted immediately.
  - `finish()`: flush pending → reconcile if needed → re-check readiness
    (abort with `finishError` if still blocked) → `POST /finish` with
    `confirm_zero_contacts = true` iff zero active events → on success,
    delete the local session file.
  - `clearSession()` for logout/user-switch; `userId` is immutable per
    instance — a session is never re-targeted to a different user.
  - Second initializer accepts `JugglingAnnotationAPIClientProtocol` +
    stores directly for test injection.
- `JugglingVideoItem.annotationStatus: String?` added (`annotation_status`,
  optional — `nil` if the video-list endpoint predates AN-1; Codable
  synthesized `decodeIfPresent` handles the missing key without a custom
  decoder).

### 2.6 Tests (Commit 6) — 25 tests, AN2-T01..T25

| File | Tests | Covers |
|------|-------|--------|
| `ContactTaxonomyTests.swift` | T01–T04 | bundled checksum vs constant, decode counts (18/17), custom_other present + thigh forbidden, invalid `total_count` rejected |
| `LocalAnnotationStoreTests.swift` | T05–T10 | empty load, save/load round trip, userId isolation, corrupt-file quarantine (bytes preserved), checksum-mismatch quarantine, delete |
| `ContactEventDraftTests.swift` | T11–T16 | `.new()` defaults, immutable `deviceEventId`, `FinishReadiness` (readyZero/readyWithCount/blocked), `duplicate_skipped` wire decode |
| `AnnotationSyncEngineTests.swift` | T17–T25 | pushCreate (success/retryable/permanent/max-retries-exhausted), pushDelete (204/404-ambiguous), reconcile (found/absent on server), flushPending (create + skip never-synced deleted draft) — via `MockAnnotationAPIClient` |

---

## 3. Known limitation — test execution not verified

**The `LFAEducationCenter.xcodeproj` currently has no test target/scheme**
(`xcodebuild -list` shows only the `LFAEducationCenter` app target; the
existing `LFAEducationCenterTests/` directory, including the pre-AN-2
`JugglingVideoTests.swift`, is not wired into `project.pbxproj`). This is a
**pre-existing condition**, not introduced by AN-2.

Consequences:
- The 25 new tests cannot be run via `xcodebuild test` until a test target is
  added to the project (Xcode: New Target → Unit Testing Bundle, add the 4
  new files + the pre-existing `JugglingVideoTests.swift` + `Biometric/`
  tests).
- I reviewed every new/changed file for type and signature consistency
  (struct field order, protocol conformance, `Codable` key mappings,
  `Equatable` requirements for `XCTAssertEqual`) but **this is not a
  substitute for a compiler run**.
- The app target itself was not rebuilt in this session (no production code
  outside `Annotation/` was changed except the additive `APIClient` /
  `AuthManager` / `JugglingVideoItem` edits, which preserve all existing
  signatures).

**Recommendation:** before AN-3 begins, add a unit-test target in Xcode (one-
time manual step) and run `xcodebuild test` to confirm the 25 AN-2 tests plus
existing suites pass.

---

## 4. Drift guard

```
$ python3 scripts/check_taxonomy_bundle_drift.py
OK — bundled taxonomy matches dataset source (md5=7198de62733493537450275dadc6f445)
```

---

## 5. Explicitly NOT done (per constraints)

- No SwiftUI annotation screen, picker, timeline, or video overlay.
- No AN-3 UX.
- No backend changes (AN-1 stays at `f1054056` on `main`, untouched).
- No PR opened.
- No retry-delay scheduler (timer) — `AnnotationSyncEngine.retryDelaysSeconds`
  is exposed as a constant for the future UI/background layer to use.

## 6. Suggested next step (not started)

AN-3: SwiftUI annotation screen consuming `JugglingAnnotationViewModel` —
gated on separate approval per existing constraints.
