# LFA PC6B Capture / Social Policy Reconciliation Audit (2026)

## Owner-approved policy amendment — 2026-09-14

This amendment supersedes the undecided items recorded in section 6. It does
not publish or implement a social renderer.

- `User.nickname` is the only canonical public Player display identity. It is
  globally case-insensitive unique; `User.id` remains the stable internal key.
- A valid nickname is required at public challenge/social boundaries. Email,
  legal/full name, and other personal-data fallbacks are forbidden.
- A verified result stores the valid nickname in effect at verification time as
  an immutable snapshot while retaining the account link by `User.id`.
- Social export v1 costs **1 global credit per verified-result unlock**.
- One successful unlock covers the normal challenge-template family in all
  supported formats, including unlimited rerender and retry. A failed render
  must not consume another credit. `CreditService` remains the only credit
  authority.
- Social rendering remains a later phase. No renderer, export entitlement, or
  credit charge is implemented by PC6B capture reconciliation.

The approved canonical SOLO intake is server-issued `SOLO_VERIFIED`, bound to
the authenticated Player, one attempt ID, and `front` camera policy. Client
`source_type`, `upload_source`, and metadata are claims only. Gallery/file
imports and current Instructor/MultiCamera POCs remain non-canonical and cannot
be promoted to PC6A verification candidates.

## Reconciliation implementation state — 2026-09-14

The narrow findings above have been implemented on
`codex/pc6b-direct-player-capture`, pending the required physical-iPhone smoke:

- the server issues an idempotent `SOLO_VERIFIED` attempt only to a Player with
  active canonical `LFA_FOOTBALL_PLAYER` entitlement and current WS1 season
  assignment;
- each attempt is bound to its authenticated `User.id`, front-camera policy,
  and at most one `JugglingVideo`; persisted source labels are server-set;
- the Player Solo view opens only the front camera and no longer presents the
  Photos picker; legacy imports remain non-canonical and PC6A rejects them;
- new human verifications require a valid unique nickname and persist its
  immutable snapshot alongside `player_user_id`;
- public Player and current challenge/card boundaries no longer substitute
  email or legal/full name when nickname is unavailable;
- no Instructor/MultiCamera canonical verification, social renderer, social
  unlock, reward charge, production import, or historical nickname rewrite was
  added.

The original inventory below records the state at audited commit
`8589eef8cea79ea967e3ccbcefd7dfed16827ccd`; statements marked missing or
non-compliant there are historical audit evidence rather than the post-change
runtime status.

## Scope and baseline

- Repository worktree: `/private/tmp/lfa-pc6b-direct-capture`
- Branch: `codex/pc6b-direct-player-capture`
- Audited commit: `8589eef8cea79ea967e3ccbcefd7dfed16827ccd`
- Baseline: `LFA_CANONICAL_BASELINE_PC6A` (`a14af5238578ca6fae50da798192330b8a6c44bc`)
- Method: static repository and contract audit. No application code, schema, migration, data, remote branch, deployment, or production environment was changed.
- GitHub Actions expected/started by this audit: `0` / `0` (no push was performed).

The PC6B branch and the requested commit are present and intact. The linked worktree already existed and was clean before this document was created.

## Executive decision

PC6B is **not merge-ready under the newly approved capture policy**. The branch proves a direct capture, preview, consent, upload, quality-check, human-review, and PC6A/PC5 path, but the Player client currently records with the **rear** camera and continues to expose **Photos/gallery import**. The upload contract also trusts client-declared source labels and has no authoritative capture-policy record. These are narrow reconciliation gaps; they do not require a second video pipeline.

The existing `User.nickname` is the strongest canonical `public_display_identity` candidate. A new nickname column is not justified. Before social/challenge display can rely on it, writes and fallback behavior must be made consistent, and the owner must decide whether the nickname is a globally unique handle or a non-unique display label.

## 1. Public Player identity inventory

### Persisted and computed identities

| Candidate | Model / source | API | Native consumer | Web consumer / current display | Uniqueness | User-editable | Public today | Canonical social/challenge suitability |
|---|---|---|---|---|---|---|---|---|
| `User.nickname` | `app/models/user.py`, `User.nickname`; nullable string | Present in `UserBase`, `UserCreate`, `UserUpdate`, and `UserUpdateSelf` in `app/schemas/user.py`; invitation registration and web registration accept it; `PATCH /users/me` writes it | `ios/LFAEducationCenter/Auth/AuthToken.swift`, `RegisterRequest.nickname`; `RegisterView.swift`; decoded by `Models/UserProfile.swift`; shown on `AcademyIDCardView.swift` | Public Player profile prints `@nickname`; friend search/results, tournament rankings, virtual-training opponents, messages, booking DTOs, card studio and VT cards use it or expose it | **Not enforced in DB.** `email` is unique; `nickname` is not. `validate_nickname()` performs a case-insensitive availability check, but profile/registration write paths do not consistently call it | Yes | Yes, in multiple social/competition views | **Best existing candidate**, provided its semantics and write policy are canonicalized. Stable joins must still use `User.id`, never nickname |
| `User.name` | `app/models/user.py`, required string | Present across user schemas and profile endpoints | `UserProfile.displayName` currently returns `name`; main/profile views consume it | Public Player profile and rankings can show it; several responses expose it | No | Yes | Yes in current routes | Poor default for public social display because it commonly represents a real/full name and has no uniqueness or privacy boundary |
| `User.first_name`, `User.last_name` | `app/models/user.py`, optional strings | Present in portions of profile/auth behavior, not a canonical public identity contract | Used as personal/profile data where available | Personal/profile use; no evidence of a single public handle policy | No | Profile data | Not established as public identity authority | Unsuitable; personal-name data should not silently become a public handle |
| `display_name` | **No DB field.** Computed aliases vary by surface | Friend response builds `nickname or name`; other DTO/template contexts create their own display values | `UserProfile.displayName` returns `name`, not nickname | Friends use `nickname or name`; VT/card helpers often use `nickname or email` | Inherits inconsistent source | Indirectly | Yes | Not canonical: identical label currently resolves differently by surface |
| `username` | **No persisted User field found** | OAuth/login variables named `username` resolve to email credentials | No independent model consumer found | No independent public display use found | N/A | N/A | No | Unsuitable; it is an authentication variable, not a public identity |
| `public_name` | **No model field found** | No canonical contract found | None found | None found | N/A | N/A | No | Does not exist; no new field is warranted while nickname can serve the role |
| `player_name` | **No model field found.** Response/template aliases only | Derived values appear in route contexts/DTOs | No independent persisted field | Used as a presentation label in selected pages | Inherits source | Indirectly | Sometimes | Not an authority; it is a view label |
| `lfa_academy_id` | System-issued User identifier in `app/models/user.py` and profile flows | Exposed for Academy identity use | Shown on Academy ID card | Used as an identifier | Unique/system-generated | No normal free edit | Can be displayed | Suitable as an unambiguous secondary identifier, not as the human-facing social name |
| `email` | `app/models/user.py`, unique login identifier | Authentication and user DTOs | Account/auth views | Some card/VT helpers currently fall back to email | DB-unique | Controlled account update | Accidentally public through fallbacks in some surfaces | **Forbidden as a public-display fallback** for the proposed social flow; it is credential/contact data |

### Exact policy evidence and inconsistency

- `app/models/user.py`, class `User`: `name` is required; `nickname` is nullable; only `email` declares `unique=True`.
- `alembic/versions/2026_02_21_0859-1ec11c73ea62_squashed_baseline_schema.py`, `users.nickname`: no unique constraint.
- `app/api/api_v1/endpoints/users/helpers.py`, `validate_nickname()`: 3–30 character format, reserved-word check, and case-insensitive availability query. This establishes intent but not write-time enforcement.
- `app/api/api_v1/endpoints/users/profile.py`, `update_own_profile()`: writes fields from `UserUpdateSelf` without calling `validate_nickname()`.
- `app/api/api_v1/endpoints/auth.py`, invitation registration, and `app/api/web_routes/auth.py`, web registration: accept/save nickname but do not establish one shared write authority.
- `app/api/web_routes/profile.py`: separately updates nickname without the canonical helper.
- `ios/LFAEducationCenter/Models/UserProfile.swift`, `displayName`: returns `name`; it does not select nickname.
- `app/api/web_routes/friends.py`: derives `display_name` as `nickname or name`.
- `app/api/web_routes/vt_card.py`, `app/api/web_routes/card_studio.py`, and `app/api/web_routes/virtual_training.py`: use `nickname or email` in selected presentation paths.
- `app/templates/public/player_profile.html`: displays name and, when present, `@nickname`.

**Canonical candidate:** `public_display_identity = User.nickname`, with `User.id` as the immutable identity key. It should be a required publication/challenge precondition rather than falling back to email or full name. Historical null or conflicting values must remain untouched until an explicit migration/manual-resolution decision exists.

**Confidence:** high for model/API/web/native evidence; high that no independent `username`, `public_name`, or `player_name` DB authority exists.

## 2. Current capture and import audit

| Capability | Current status | Evidence | Runtime classification |
|---|---|---|---|
| Front camera | **Missing** in PC6B direct Player recorder | `ios/LFAEducationCenter/Juggling/Upload/JugglingVideoUploadView.swift`, `JugglingCameraRecorder.configureAndStart()`, selects `.back` | Runtime-reachable; policy gap |
| Rear camera | **Implemented** | Same recorder selects built-in wide-angle rear camera; metadata reports `camera_position=back` | Runtime-reachable; conflicts with `SOLO_VERIFIED` |
| Camera switching | **Missing** | No switch state/control or input replacement in `JugglingCameraRecorder` | Missing |
| Photos/gallery import | **Implemented and prominently offered** | `JugglingVideoUploadView.swift` presents `JugglingVideoPHPicker`; idle state invites gallery selection. `JugglingVideoPHPicker.swift` filters videos. View model sends `source_type=uploaded_video`, `upload_source=gallery` | Runtime-reachable; conflicts with Player SOLO policy |
| Files/external import | Client UI not found; backend accepts it | No `fileImporter`/document picker in this Player flow. `app/schemas/juggling.py` allows `upload_source=file` and `source_type=uploaded_video` | API capability exists; unbound/free-import risk if exposed |
| Local preview / retry/delete | Implemented | `JugglingVideoUploadView.swift`, `capturePreview` uses `VideoPlayer`; `discardCapturedVideo()` deletes the temporary file before upload | Runtime-reachable and reusable |
| Player consent | Implemented | Capture upload calls `grantJugglingConsent(service=true, training=?, adminReview=?)`; UI requires admin-review toggle. Backend upload init requires service consent and applies PC1 Player policy to `in_app_capture` | Runtime-reachable and reusable |
| Minor/guardian policy | Implemented at canonical Player boundary | PC1 identity/profile policy is invoked for in-app capture; UI explains guardian requirement | Runtime-reachable and reusable; backend remains authority |
| Player linkage | Implemented | `JugglingVideo.user_id` FK; upload routes use authenticated `current_user`; read/media/delete operations are owner-scoped | Runtime-reachable and reusable |
| Instructor upload for Player(s) | **Missing** | Upload init always creates media for `current_user`; no reviewed instructor-on-behalf-of participant upload contract found | Missing |
| GoPro upload | **Missing** | MultiCamera iOS can fetch a GoPro file locally for debug/diagnostics, but the capture manager explicitly emits `uploadStatus=not_implemented`, `backendMediaId=null` | POC only |
| Session linkage | **Missing on `JugglingVideo`** | No canonical PC2 `Session`/participation FK or MultiCamera session/cycle FK on `JugglingVideo` | Missing |
| Instructor linkage | **Missing on media** | No capturing/reviewing instructor provenance on upload record. PC6A final human reviewer is recorded separately | Missing |
| Source/provenance | **Partial, client-declared** | `JugglingVideo.source_type`, `upload_source`, client metadata, device/camera/orientation plus original/processed checksum. Model comments mark client metadata non-authoritative; ffprobe fields are server-authoritative | Partial; insufficient to prove approved capture mode |
| Video integrity / completeness | Implemented | Original checksum and processed checksum/version; upload size/checksum checks; server ffprobe metadata and deterministic quality gate | Runtime-reachable and reusable |
| Upload replay/concurrency | Implemented for one authenticated Player upload attempt | `user_id + upload_idempotency_key` unique constraint; exact replay returns the same record; conflicting payload returns conflict; completion is claimed once with locking | Runtime-reachable and reusable |
| Capture-attempt/device anti-replay | **Missing as policy provenance** | UUID upload key protects transport replay, but no server-issued attempt, registered-device identity, authoritative camera position, or session binding exists | Missing |

The legacy annotation dataset contains values such as `app_gallery_upload`. Those files are existing annotation/test/reference material; they do not authorize Player gallery upload under the new product policy and must not be rewritten as part of this reconciliation.

## 3. Approved capture modes: current gap analysis

### `SOLO_VERIFIED`

**Current status: NOT COMPLIANT, narrow remediation required before merge.**

- Reusable: authenticated Player ownership, PC1 eligibility/guardian checks, local capture, preview, retry/delete, consent, upload, server checksum/metadata/quality, human review, PC6A verification, PC5 outcome.
- Conflict: direct capture is rear-camera-only; approved policy requires front-camera-only.
- Conflict: Player gallery import remains visible and functional.
- Gap: backend accepts client-declared `uploaded_video/gallery/file`; labels alone do not prove in-app/front capture.
- Gap: no server-issued capture attempt/revision binds the upload to the approved SOLO mode. The current upload idempotency key proves replay identity, not capture origin.

### `INSTRUCTOR_VERIFIED`

**Current status: PARTIAL POC, not an approved runtime flow.**

- MultiCamera has session, participant, device, device-role, stream-metadata, capture-cycle and cycle-idempotency structures.
- It is not linked to the canonical PC2 session/participation lifecycle or to `JugglingVideo`/PC6A media.
- There is no instructor-on-behalf-of upload that binds the canonical session, authorized instructor, and all Player participants.
- The current MultiCamera session API lets any active user create a session and be joined as `instructor`; the join endpoint accepts the caller-selected role. Its participant-role check is not the WS1 Coach authorization matrix. Therefore it must not authorize `INSTRUCTOR_VERIFIED`.
- Native MultiCamera capture uses rear camera only; front/rear switching is absent.
- GoPro retrieval is local/diagnostic; backend media upload is explicitly not implemented.

### `MANAGED_MULTICAMERA`

**Current status: PARTIAL PROVENANCE SCAFFOLD / POC ONLY.**

- Present: `ManagedDevice` identity, MultiCamera session participants and devices, capture cycles, cycle idempotency, stream type/preset/revision metadata.
- Missing: canonical PC2 session reference, WS1-authorized instructor authority, a server-issued attempt binding, actual media/checksum/storage linkage, `JugglingVideo`/PC6A linkage, and prevention of post-hoc arbitrary media pairing.
- `CaptureStream` is a metadata contract; it is not an uploaded media lifecycle.

### Minimum capture changes required before PC6B merge

1. Make direct Player capture front-camera-only and report the actual selected device/position; no switch is needed for SOLO.
2. Remove/disable Photos/gallery selection from the Player SOLO journey.
3. Enforce the SOLO source policy on the server. A Player must not be able to create a canonical SOLO verification candidate through `uploaded_video`, `gallery`, `file`, or spoofed source labels.
4. Add the smallest authoritative capture-mode/attempt provenance needed to distinguish server-accepted `SOLO_VERIFIED` media from future instructor/managed media. Preserve the existing `JugglingVideo` pipeline; do not build a second media system.
5. Update contracts and tests so web/API/native cannot bypass the same policy.

Instructor and managed-multicamera implementation are not required to prove SOLO capture, provided the POC routes remain isolated and cannot label their media as canonically verified. They require their own later authorization and provenance phase.

## 4. Social export inventory

| Component | Existing evidence | Current usefulness / gap |
|---|---|---|
| Local video export/transcode | `ios/LFAEducationCenter/Juggling/Upload/JugglingVideoExportService.swift` normalizes selected/captured media before upload | Reusable media normalization only; it is not a social compositor |
| On-device overlay/rendering | Live training/annotation overlays exist in SwiftUI, but no evidence of an exported composited result video | Missing for social export |
| Watermark/logo overlay | LFA branding exists in server-rendered card templates | Existing template asset/presentation reference; no native video watermark pipeline |
| End-card | Challenge/post/story HTML templates render branded score cards | Server/template component exists; no canonical juggling-result end-card on device |
| ShareSheet | No `UIActivityViewController` or SwiftUI `ShareLink` found in the iOS target | Missing |
| Photos save | No result-export `PHPhotoLibrary` save flow found | Missing |
| Score/result rendering | Server challenge-card templates render score/outcome | Reusable visual concept; not bound to PC6A canonical result |
| Challenge title rendering | Challenge-card templates render game/challenge labels | Existing server template behavior; no native template family |
| Multiplayer participants | Server challenge templates render authenticated participant names/scores | Existing web/server presentation; it does not yet enforce canonical public identity |
| Verified badge | No social badge bound to immutable `JugglingVerification` found | Missing; must only derive from canonical verified result |
| Server PNG/video rendering | `app/services/card_export_service.py` uses Playwright for PNG/WebM and FFmpeg for MP4; challenge/VT export routes exist | Proven legacy/server export, but approved direction is on-device social export |
| Credit unlock pattern | `app/services/card_design_service.py::purchase_design()` uses `CreditService.deduct()` and an ownership record in one transaction; `CardDesignOwnership` has a unique user/type/design key | Useful idempotent ownership pattern. It is design-level, not per verified result/social unlock, and its existing prices are not an approved social-export price |

### Social policy reconciliation

- The existing server card pipeline demonstrates branding, result layouts, format-specific templates, and an atomic CreditService-backed unlock pattern.
- No complete on-device social export exists for the canonical PC6A result.
- The future renderer must read immutable verification/result data; the rendered file must never become outcome authority.
- `Verified` may be displayed only when the source is a canonical valid PC6A verification.
- A failed render must occur before any final credit consumption, or credit deduction and unlock creation must be rolled back atomically.
- One successful unlock must be represented by a durable, unique entitlement so rerender/retry does not charge again.
- Current `nickname or email` and `nickname or name` fallbacks are unsafe for a social export contract. Social rendering should require the canonical public identity policy.

Social export implementation is **not required for the direct-capture PC6B merge** unless the owner expands PC6B scope. Its price and unlock granularity remain product decisions.

## 5. Source-of-truth and reuse decisions

| Area | Decision from evidence |
|---|---|
| Public identity | Reuse `User.nickname`; use `User.id` as stable key. Do not add another nickname/public-name field |
| SOLO capture | Reuse PC6B local recorder and Juggling upload pipeline; correct camera/import policy and make provenance authoritative |
| Media integrity | Keep server checksums, ffprobe metadata, quality checks, owner-scoped access, retention/deletion, and replay controls |
| Review/annotation | Keep PC6A contact-event taxonomy, revisions, human-review authority, immutable verification and provenance |
| Instructor/managed capture | Reuse useful managed-device/session/cycle concepts only after linking them to PC2/WS1 and real media; do not treat current POC roles as authorization |
| Social output | Reuse branding/layout ideas and the CreditService ownership pattern; build a small native result renderer later rather than promote server card export to outcome authority |

## 6. Owner decisions still required

1. **Nickname semantics.** Should `User.nickname` be a globally case-insensitive unique public handle, or may duplicate display labels exist? **Recommendation:** globally case-insensitive unique for challenge/social discovery, with `User.id` retained as the immutable internal key. Existing null/conflicting records should be manual-review/fail-closed for publication; no automatic backfill.
2. **Public fallback.** May a Player without a valid nickname appear under any fallback? **Recommendation:** no public social/challenge publication until a nickname is set; never fall back to email or legal/full name.
3. **Rename/history display.** Should an old verified result render the current nickname or the nickname captured when the result was verified/unlocked? **Recommendation:** store an immutable public-display snapshot with the social export entitlement/result presentation provenance, while keeping the account link by `User.id`.
4. **Social-export credit price.** Exact unlock price remains undecided.
5. **Social unlock granularity.** Does one unlock cover every approved template/format for one verified result, or one result plus one template family? **Recommendation:** one unlock per verified result covering rerenders and approved formats in that challenge template family; a different paid template family may be a separate entitlement only if the product explicitly prices it.

The approved capture-mode rules themselves need no further owner decision. Front-camera configuration, authoritative attempt tokens, source validation, and PC2/WS1 link enforcement are technical decisions that can be implemented within those rules.

## 7. Merge-readiness conclusion

- Branch/commit integrity: **PASS**.
- Public identity candidate: **FOUND — `User.nickname`**, policy normalization pending.
- PC6B functional foundation: **REUSABLE**.
- Approved SOLO capture compliance: **FAIL** until front-only, no-gallery, and authoritative source enforcement are implemented.
- Instructor verified flow: **NOT IMPLEMENTED; POC scaffolding only**.
- Managed multicamera provenance: **PARTIAL POC; not connected to canonical media/session authority**.
- Social export: **legacy/server components exist; canonical on-device flow is future work**.
- Merge recommendation at this checkpoint: **DO NOT MERGE YET**. Apply only the five narrow capture-policy reconciliation changes listed above, characterize them first, and rerun the physical-device merge gate.

## Evidence index

Primary runtime evidence:

- `app/models/user.py` — `User`
- `app/schemas/user.py` — `UserBase`, `UserCreate`, `UserUpdate`, `UserUpdateSelf`
- `app/api/api_v1/endpoints/users/helpers.py` — `validate_nickname`
- `app/api/api_v1/endpoints/users/profile.py` — `update_own_profile`
- `app/api/web_routes/friends.py` — friend search/result display identity
- `app/api/web_routes/vt_card.py`, `card_studio.py`, `virtual_training.py` — display fallbacks
- `app/templates/public/player_profile.html` — public Player presentation
- `ios/LFAEducationCenter/Models/UserProfile.swift` — `displayName`
- `ios/LFAEducationCenter/Auth/AcademyIDCardView.swift` — name/nickname presentation
- `ios/LFAEducationCenter/Juggling/Upload/JugglingVideoUploadView.swift` — gallery UI, direct recorder, rear camera, preview/consent
- `ios/LFAEducationCenter/Juggling/Upload/JugglingVideoPHPicker.swift` — Photos video import
- `ios/LFAEducationCenter/Juggling/Upload/JugglingVideoUploadViewModel.swift` — source labels, consent, upload lifecycle
- `ios/LFAEducationCenter/Juggling/Upload/JugglingVideoExportService.swift` — upload normalization/export
- `app/models/juggling.py` — `JugglingVideo`
- `app/schemas/juggling.py` — upload-init contract and accepted source values
- `app/api/api_v1/endpoints/users/juggling_videos.py` — owner-scoped intake/media/delete routes
- `app/services/juggling/video_service.py` — upload idempotency/concurrency behavior
- `app/services/juggling/verification_service.py` — PC6A review and immutable verification
- `app/services/juggling/golden_dataset.py` — privacy-safe reviewed manifest builder
- `app/models/managed_device.py`, `app/models/multicamera_session.py` — managed device/session/cycle/stream POC
- `app/api/api_v1/endpoints/multicamera/sessions.py`, `multicamera/cycles.py` — current POC role authorization
- `ios/LFAEducationCenter/MultiCamera/SessionCaptureManager.swift` — rear-camera capture and explicit missing upload
- `app/services/card_export_service.py` — server PNG/video rendering
- `app/services/card_design_service.py` — atomic CreditService-backed design unlock
- `app/models/card_design_ownership.py` — durable unique design entitlement
- `app/templates/public/export/challenge/post_16_9.html`, `story_9_16.html` — branded challenge result templates

Evidence confidence is **high** for current repository behavior. Physical-device behavior was not re-run because this task expressly requested audit-only work; the previously requested device gate remains after policy reconciliation implementation.
