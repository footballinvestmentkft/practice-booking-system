# AN-3B1 Playback and Video Loader — Implementation Report

**Branch:** feat/ios-juggling-annotation-an3b1
**Base:** main (05bcf3b3)
**Head:** e2d4d7c9
**Date:** 2026-06-13

---

## Verdict: AN3B1_READY_FOR_REVIEW

---

## Scope delivered (6 commits)

| Commit | SHA | Description |
|--------|-----|-------------|
| 1 | 3460010b | feat(ios): expose AVPlayer safely and add layer wrapper |
| 2 | 58dc348e | feat(ios): add authenticated annotation video loader |
| 3 | 81c30b79 | feat(ios): add playback control bar |
| 4 | 2616ce36 | test(ios): add AN-3B1 playback and loader coverage |
| 5 | 8d79f38b | build(ios): wire AN-3B1 sources and tests |
| 6 | e2d4d7c9 | fix(ios): handle unauthorized state in cancel test (AN3B-L10) |

**Files changed:** 8 files, +1059 / −1 lines

---

## Contract fulfilment

### PlaybackController.avPlayer (Commit 1)
- `var avPlayer: AVPlayer? { player as? AVPlayer }` — returns nil for MockPlayer in tests.
- Located in `PlaybackController.swift` after `private var timeObserver`.

### AVPlayerLayerView (Commit 1)
- `UIViewRepresentable` hosting `AVPlayerLayer` via `layerClass` override.
- `videoGravity = .resizeAspect` (letter-box, no crop).
- `updateUIView`: player swap via `!==` identity check, avoids AVPlayerLayer reset cost.
- `dismantleUIView`: `playerLayer.player = nil` breaks retain cycle before dealloc.
- Black background; no autoplay side-effect (no `player.play()` call in `makeUIView`).

### AuthManager.performRefresh() visibility (Commit 2)
- Changed `private func performRefresh()` → `func performRefresh()` (internal).
- Documented: the `pendingRefresh` barrier still prevents concurrent double-refresh.
- `runRefresh()` and `saveTokens()` remain private — minimal footprint.

### AnnotationVideoLoader (Commit 2)
- `URLSessionTaskProtocol`: `resume()`, `cancel()`, `var progress: Progress`.
- `URLSessionDownloadProtocol.annotationDownloadTask(with:completionHandler:)` returns `URLSessionTaskProtocol`.
- `URLSession` and `URLSessionDownloadTask` conform via extensions.
- **Media URL**: first-party only — `APIConfig.baseURL + /api/v1/users/me/juggling/videos/<id>/media`.
- **Disk-space preflight**: 200 MB free required before starting.
- **Duplicate load guard**: `guard case .idle = state else { return }`.
- **401 retry**: one `performRefresh()` call if first attempt returns 401.
- **File protection**: `completeUnlessOpen` on partial (move from system temp) → `complete` after atomic rename.
- **Backup exclusion**: `isExcludedFromBackup = true` via `URLResourceValues`.
- **Cancel**: `cancel()` guards on `.downloading`, sets `isCancelled = true`, cancels task, cleans partial.
- **Logout cleanup**: `cleanupAll(userId:)` removes entire user directory.
- **Stale sweep**: `static sweepStalePartials(userId:fileManager:)`.
- **No personal data in filenames**: directory = `juggling_annotation/<userId>/<videoId>/video.mp4`.
- **Progress**: NSProgress KVO `fractionCompleted`; -1.0 when `totalUnitCount ≤ 0`.

### PlaybackControlBar (Commit 3)
- Play/pause toggle, frame step ±1, speed selector (0.25×/0.5×/1× via `Menu`).
- Millisecond-precision timestamp: `M:SS.mmm` format via `static formatTimestamp(ms:)`.
- All touch targets 44×44 pt (`frame(width:44, height:44)` / `frame(minWidth:44, minHeight:44)`).
- `accessibilityLabel` on every control; timestamp carries `.updatesFrequently`.
- No animations → inherently Reduce Motion compatible.
- NOT in scope: event timeline, tap-to-seek, event picker (AN-3B2).

---

## Build validation

| Configuration | Result |
|--------------|--------|
| Debug | BUILD SUCCEEDED |
| Release | BUILD SUCCEEDED |

Platform: iOS Simulator iPhone 16 (iOS 18)

---

## Test results

| Suite | Tests | Pass | Fail |
|-------|-------|------|------|
| AN3B-B* (PlaybackBarTests) | 8 | 8 | 0 |
| AN3B-L* (AnnotationVideoLoaderTests) | 20 | 20 | 0 |
| AN3-T* regression (EditEvent/FlushPending/Playback) | 31 | 31 | 0 |
| **Full suite** | **115** | **115** | **0** |

---

## Additional checks

| Check | Result |
|-------|--------|
| Taxonomy MD5 | 7198de62 — unchanged |
| pbxproj lint | plutil: OK |
| AN-3A regression | 31/31 PASS |
| Scope drift | None — ContactPickerView/timeline/AN-3B2 NOT touched |

---

## AN-3B2 prerequisites (not in this PR)

- `ContactEventDraft` additions: `pendingServerSnapshot: ContactEventOut?`, `conflictRetryCount: Int`
- `acceptServerVersion(deviceEventId:)` / `keepLocalVersion(deviceEventId:)` on `JugglingAnnotationViewModel`
- `ContactPickerView` + `EventTimelineView` + tap-to-seek wiring
- Conflict panel with user confirmation before server-wins

---

*Branch: feat/ios-juggling-annotation-an3b1 | Head: e2d4d7c9 | 115/115 PASS*
