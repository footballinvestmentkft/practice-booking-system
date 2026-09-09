# LFA P1 WS1 Implementation Specification 2026

**Status:** CANONICAL BUSINESS LOGIC COMPLETE — IMPLEMENTATION NOT STARTED  
**Baseline:** `LFA_CANONICAL_BASELINE_P0` / `894c43a4a37af4a8a583240a5a2d0b1f31a3fdee`  
**Scope:** WS1 only — F15 canonical program/legacy mapping, F16 age/season/category policy, F17 global credit versus legacy license wallet, plus the direct account/consent and coach-authorization dependencies approved by the owner.  
**Policy evidence:** owner decisions recorded through 2026-09-09 and `docs/audit/LFA_ALL_PROGRAM_AGE_POLICY_INVENTORY_2026.md`.

This specification replaces unresolved age/category placeholders in the prior WS1 draft. It does not create a branch, application change, migration, database write, backfill, deployment, or historical reinterpretation. `LFA_P1_REMEDIATION_PLAN_2026.md` and `LFA_P1_WORKSTREAMS_2026.json` remain unchanged.

## 1. Canonical owner decisions

### 1.1 Global account/profile policy

- A user profile must have a valid date of birth before normal account use.
- Minimum age is 5. A person younger than 5 cannot receive an account/profile, program enrollment, or entitlement.
- There is no canonical maximum age. A future/invalid DOB is rejected as invalid data; the current web-only age-120 cap is not a program or entitlement rule.
- Every user younger than 18 requires active parent/guardian consent before normal account use, enrollment, entitlement, or age-gated activity.
- Admin, web, API, program-specific, legacy, and native routes cannot bypass DOB, minimum-age, or minor-consent policy.
- Biometric functions require age 18+. Guardian consent does not waive this rule. Missing/unverifiable DOB fails closed.
- DOB is treated as a date, not a timestamp. Calendar-age policies increment on the birthday at the domain date in `Europe/Budapest`; football season classification uses the season-start date instead of the current date.

A 5–17-year-old account may exist only as a consent-pending, non-usable record needed to complete the guardian-consent flow. It cannot receive entitlement or participate until consent is active. This is an implementation sequencing rule derived from the global consent decision, not a separate age exception.

### 1.2 Canonical programs and legacy entitlement aliases

Exactly four product identifiers exist:

```text
LFA_FOOTBALL_PLAYER
LFA_COACH
GANCUJU_PLAYER
INTERNSHIP
```

Deterministic compatibility aliases:

```text
PLAYER     -> GANCUJU_PLAYER
COACH      -> LFA_COACH
INTERNSHIP -> INTERNSHIP
```

Ambiguous, contradictory, season-only, or multiply interpretable legacy records remain fail-closed and require manual review. `LFA_PLAYER` is not inferred automatically. No historical entitlement row is rewritten in WS1.

### 1.3 Football Player

- Program minimum: 5; no maximum.
- Season: July 1 through June 30 of the following year.
- Season age is age on July 1 of the season-start year.
- Base categories and canonical level scopes:
  - PRE: age 5–13, levels 1–2;
  - YOUTH: age 14–18, levels 3–4;
  - AMATEUR: automatic base for age 19+, levels 5–6;
  - PRO: never an automatic age-based base, levels 7–8.
- A 14–18-year-old may reach AMATEUR through explicit promotion.
- Any player age 14+ may reach PRO through explicit promotion if professional progression/eligibility requirements pass.
- Numeric level has no independent age threshold. Category determines age eligibility; professional progression determines level advancement.
- Season base is immutable during the season. Birthday does not recategorize it. A new season assignment is evaluated at the next season boundary.
- Effective category starts equal to base and may move upward by one or several categories.
- A target-qualified Head Coach may authorize normal upward movement. Assistant Coach and player self-service cannot.
- Admin may perform audited override, downward reassignment, or revocation.
- Effective category may move downward, but never below season base. Base never changes.
- Every category change is append-only, audited, replay-safe, and updates the effective projection in the same transaction.
- Each promotion explicitly chooses whether base participation remains available. Retaining base does not automatically grant participation in intermediate categories.

### 1.4 LFA Coach

- Program minimum: 14; no maximum.
- Ages 14–17 require global active guardian consent.
- Coach progression is not age-based after valid 14+ admission.
- The repository's progressive 14/16/16/18/18/20/21/23 level-age ladder is legacy and must not be enforced.
- Four player-category scopes exist: PRE, YOUTH, AMATEUR, PRO.
- Each scope has Assistant and Head qualifications.
- Category authority inherits downward: PRO > AMATEUR > YOUTH > PRE.
- Role authority inherits from Head to Assistant; Assistant never inherits Head authority.
- Lower-category qualification cannot act in a higher player category.
- Normal football category promotion requires a Head qualification valid for the target category. Assistant qualifications cannot issue final promotion.

### 1.5 GānCuju Player

- Program minimum: 5; no maximum.
- All eight belts/levels are age-independent after admission.
- No youth/adult category split exists.
- Progression is performance/professional-requirement based.
- A young participant may reach a high belt if every non-age progression requirement passes.
- Legacy `PLAYER` is handled only by the approved deterministic compatibility resolver.

### 1.6 Internship

- Program minimum: 18; no maximum.
- The old 16+ config is legacy and must not authorize admission.
- No additional age threshold applies to levels, semesters, projects, workplace activity, qualification, or completion after valid admission.
- Project and workplace activities are semester content, not separate age-gated domains.

### 1.7 Global credit ownership

- `User.credit_balance` plus the immutable user-owned `CreditTransaction` ledger remain the only spendable credit authority.
- Program/license/semester/enrollment/specialization identifiers are transaction context only.
- Legacy license-wallet fields are read-only, non-spendable, and excluded from the global balance.
- WS1 may produce reconciliation/report output only. It must not transfer, value, delete, or backfill historical wallet state.
- Any later value transfer needs a separate owner decision and migration gate.

## 2. Program age matrix

| Program / surface | Min age | Max age | Age-based progression | Season based | Minor consent |
|---|---:|---:|---|---|---|
| Global usable account/profile | 5 | None | No | No; calendar-age admission | Required under 18 |
| LFA_FOOTBALL_PLAYER | 5 | None | Base category only; numeric level is not age-based | Yes, July 1–June 30 | Required under 18 |
| LFA_COACH | 14 | None | No after admission | No | Required at ages 14–17 |
| GANCUJU_PLAYER | 5 | None | No after admission | No | Required under 18 |
| INTERNSHIP | 18 | None | No after admission | No | Not applicable to admitted users |
| Biometrics | 18 | None | No | No | Consent cannot waive 18+ |

## 3. Canonical policy boundaries

### 3.1 `AccountProfileEligibilityPolicy`

One policy is called by every account creation, profile activation, login/use gate, entitlement, enrollment, and age-gated activity adapter. It returns:

```text
ELIGIBLE
CONSENT_PENDING
DOB_REQUIRED
INVALID_DOB
BELOW_MINIMUM_AGE
BIOMETRIC_ADULT_REQUIRED
```

Rules:

1. DOB is mandatory and cannot be in the future.
2. Age below 5 returns `BELOW_MINIMUM_AGE`; normal creation routes create no account/profile.
3. Age 5–17 without active guardian consent returns `CONSENT_PENDING`; no normal use, entitlement, enrollment, booking, competition, teaching, project/workplace activity, or biometric action is authorized.
4. Age 5–17 with active consent may use non-biometric functions subject to program eligibility.
5. Biometrics require age 18+ and valid DOB regardless of guardian consent.
6. Role/admin caller cannot convert denial to eligibility. Admin may record/verify consent only through the dedicated command.

### 3.2 `ProgramEligibilityPolicy`

| Program | Admission reference date | Admission rule | Later age revalidation |
|---|---|---|---|
| Football Player | Current domain date for admission; July 1 for season base | 5+ | New season assignment at boundary; birthday does not change current base |
| Coach | Current domain date | 14+ and active consent if under 18 | Consent remains enforced while minor; levels are not age-gated |
| GānCuju | Current domain date | 5+ | No belt age gates; global consent remains enforced while minor |
| Internship | Current domain date | 18+ | No semester/level age gates after valid admission |

API, web, program-specific services, admin routes, and native consumers do not retain local minimum-age dictionaries or missing-DOB exceptions.

### 3.3 Canonical program resolver

The pure resolver returns `CANONICAL`, `LEGACY_COMPATIBLE`, or `REVIEW_REQUIRED`. Public responses emit canonical IDs. A passed deprecation date cannot disable an owner-approved deterministic alias. Raw legacy values are restricted reconciliation evidence only.

## 4. Canonical domain model

### 4.1 Entitlement authority

`UserLicense` remains the entitlement aggregate. A user may own zero to four canonical program entitlements, one per program.

Planned `canonical_program_id` is authoritative when populated. Existing `specialization_type` remains raw legacy evidence and a temporary compatibility projection. Reads validate both; contradictory values fail closed. New writes use canonical ID and mirror it into the old field only during a bounded compatibility window.

Required invariants:

- uniqueness on `(user_id, canonical_program_id)` when populated;
- canonical creation also locks/checks deterministically compatible legacy rows;
- football categories and season types are never entitlement IDs;
- `User.specialization` cannot create/revoke entitlement;
- activation, renewal, cancellation, and credits retain the P0 transactional policy;
- every entitlement entry point invokes global profile and program eligibility first.

### 4.2 Progression authority

- `SpecializationProgress`: accumulated program measures;
- `LicenseProgression`: immutable level-transition history;
- `UserLicense.current_level` and `max_achieved_level`: current/max projections;
- track/module progress: education state, not independent license authority.

One progression command locks records, validates the program's non-age professional rules, appends history, updates projections, and commits once. Coach/GānCuju levels and Internship semesters/levels do not introduce age gates. Football numeric level progression never recategorizes season base implicitly.

### 4.3 Guardian consent authority

Existing `User.parental_consent`, `parental_consent_at`, and `parental_consent_by` remain legacy compatibility fields. They are not canonical global evidence because they are mutable, Coach-specific in current behavior, and lack revocation/version history.

Planned additive table: `user_guardian_consents`.

| Field | Constraint / meaning |
|---|---|
| `id` | Repository-standard generated PK |
| `subject_user_id` | Non-null user FK; delete restricted |
| `guardian_name` | Non-null bounded text |
| `guardian_relationship` | Nullable bounded text |
| `policy_version` | Non-null bounded string |
| `source` | Non-null constrained web/API/admin verified-flow source |
| `evidence_reference` | Nullable bounded reference; no raw secret/document payload |
| `granted_at` | Non-null timezone-aware server timestamp |
| `recorded_by_user_id` | Nullable user FK; subject self-recording is not guardian verification |
| `revoked_at` | Nullable timezone-aware timestamp |
| `revoked_by_user_id` | Nullable user FK |
| `revocation_reason` | Nullable bounded text; required when revoked |

Only one active row per subject is allowed by a partial unique index. Re-consent creates a new row and preserves prior rows. No route writes the legacy boolean directly after cutover; it may be projected for old readers.

### 4.4 Global credit authority

`User.credit_balance` and user-owned `CreditTransaction` remain authoritative. License-wallet fields and ownership semantics attached to legacy `CreditTransaction.user_license_id` are quarantined read-only evidence. New license context is non-owning. All P0 idempotency, locking, replay, rollback, ledger/balance, cancellation, renewal, and refund invariants remain mandatory.

## 5. Football season/category model

### 5.1 `FootballSeasonCategoryAssignment`

Planned table: `football_season_category_assignments`.

| Field | Type/constraint | Meaning |
|---|---|---|
| `id` | Repository-standard generated PK | Stable assignment identity |
| `user_license_id` | FK, non-null, delete restricted | Canonical Football entitlement |
| `season_start_date` | date, non-null | July 1 |
| `season_end_date` | date, non-null | Following June 30 |
| `policy_version` | string, non-null | Versioned category policy |
| `age_at_season_start` | integer, non-null | Age on July 1 |
| `season_base_category` | constrained string, non-null | PRE, YOUTH, or AMATEUR; immutable |
| `effective_category` | constrained string, non-null | PRE/YOUTH/AMATEUR/PRO; initially base |
| `base_participation_retained` | boolean, non-null | Additional base participation while effective is above base |
| `classification_source` | constrained string, non-null | `POLICY` or reviewed/manual source |
| `classified_at` | timezone-aware timestamp, non-null | Classification time |
| `classified_by_user_id` | nullable user FK | Null for system policy |
| `effective_updated_at` | timezone-aware timestamp, non-null | Projection update time |
| `created_at` | timezone-aware timestamp, non-null | Creation time |

Constraints:

- unique `(user_license_id, season_start_date)`;
- dates are July 1 and following June 30;
- base is PRE, YOUTH, or AMATEUR; PRO base is invalid;
- age/base: 5–13 PRE, 14–18 YOUTH, 19+ AMATEUR;
- effective rank cannot be below base;
- when effective equals base, retained is false because effective already grants base participation;
- base cannot be changed by movement or birthday;
- entitlement resolves canonically to Football Player.

Existing `SemesterEnrollment.age_category` remains a compatibility projection and cannot be a canonical write target after cutover.

### 5.2 `FootballCategoryMovementEvent`

Planned append-only table: `football_category_movement_events`.

| Field | Constraint / meaning |
|---|---|
| `id` | Stable event/result identity |
| `season_assignment_id` | Non-null FK, delete restricted |
| `movement_type` | `PROMOTION`, `DOWNWARD_REASSIGNMENT`, or `REVOCATION_TO_BASE` |
| `from_category` / `to_category` | Effective categories before/after |
| `season_base_category` | Captured immutable base |
| `previous_base_participation_retained` | Prior projection |
| `base_participation_retained` | New explicit projection |
| `actor_user_id` | Non-null actor FK |
| `approving_head_coach_user_id` | Required for normal promotion; null for admin override |
| `authorization_type` | `QUALIFIED_HEAD_COACH` or `ADMIN_OVERRIDE` |
| `authorization_reference_id` | Qualification/assignment evidence |
| `target_context_id` | Nullable semester/session/tournament context |
| `reason` | Non-null bounded text |
| `source` | Non-null web/API/admin compatibility source |
| `idempotency_key` | Non-null caller-supplied replay key |
| `occurred_at` | Non-null server timestamp |
| `correlation_id` | Nullable audit correlation |

Rules:

- unique `(season_assignment_id, idempotency_key)`;
- `from_category <> to_category`;
- events are never updated/deleted;
- promotion may skip categories but must rank above current;
- downward/admin target is at or above base and below current;
- return to base sets retained false;
- normal promotion requires target-qualified Head Coach and explicit retained flag;
- downward/revocation requires admin override;
- event and effective/retained projection commit once under row lock;
- replay returns original result without another change.

### 5.3 Enrollment link

Add nullable indexed `football_season_category_assignment_id` FK to `semester_enrollments` with delete restriction.

- New Football enrollments link to annual assignment.
- Eligibility uses effective plus explicit base-retention state.
- If retained and effective is above base, permitted contexts are exactly `{base, effective}`; intermediate categories are not implied.
- Existing rows remain untouched/unlinked.
- Proven current-season legacy state may remain readable; ambiguous state is `REVIEW_REQUIRED` and cannot authorize new participation.
- At next boundary a canonical assignment is required; current-day-age fallback is prohibited.

## 6. Football transition matrix

Category rank: `PRE < YOUTH < AMATEUR < PRO`.

### 6.1 Supported base/effective combinations

| Season base | Base age | Effective PRE | Effective YOUTH | Effective AMATEUR | Effective PRO |
|---|---:|---|---|---|---|
| PRE | 5–13 | Allowed; initial/base | Promotion | Promotion only when current age is 14+; skip allowed | Promotion only when current age is 14+ and professional eligibility passes |
| YOUTH | 14–18 | Forbidden below base | Allowed; initial/base | Promotion | Promotion |
| AMATEUR | 19+ | Forbidden below base | Forbidden below base | Allowed; initial/base | Promotion |
| PRO | Never automatic base | Invalid | Invalid | Invalid | Invalid base; PRO is effective-only |

The PRE→PRO cell preserves both owner rules. Category skipping is supported, while PRO requires current age 14+. A PRE-base player who turns 14 during the season keeps PRE base, but may then receive explicit PRO promotion if professional eligibility and authorization pass. This does not perform birthday recategorization.

### 6.2 Movement matrix

| Current effective | Target | Direction | Authority | Result |
|---|---|---|---|---|
| PRE | YOUTH | Upward | Head qualified for YOUTH or higher | Allowed if professional eligibility passes |
| PRE | AMATEUR | Upward skip | Head qualified for AMATEUR or higher | Allowed only when current age is 14+ and professional eligibility passes |
| PRE | PRO | Upward skip | PRO Head | Allowed only at current age 14+ and with professional eligibility |
| YOUTH | AMATEUR | Upward | Head qualified for AMATEUR or higher | Allowed if professional eligibility passes |
| YOUTH | PRO | Upward skip | PRO Head | Allowed if professional eligibility passes |
| AMATEUR | PRO | Upward | PRO Head | Allowed if professional eligibility passes |
| Any above base | Lower category still ≥ base | Downward | Admin override | Allowed and audited; base unchanged |
| Any above base | Season base | Revocation to base | Admin override | Allowed; retained becomes false |
| Any | Same category | No movement | Nobody | Rejected unless exact replay of original key |
| Any | Below base | Invalid | Nobody, including admin | Rejected |
| Player | Higher category | Self-service | Player | Rejected |
| Assistant Coach | Higher category | Approval attempt | Assistant | Rejected |

Every promotion requires explicit `base_participation_retained`. True grants exactly base plus effective; false grants effective only. Downward above base preserves the prior choice unless admin explicitly changes it in the audited command; return to base always sets false.

Admin override may also authorize an otherwise valid upward target when the normal Head Coach path cannot be used. It must still enforce target age, professional eligibility, base floor, history, reason, and idempotency; “override” is not an eligibility bypass.

### 6.3 Commands

```text
promote_football_effective_category(
  actor, season_assignment_id, expected_current_category,
  target_category, base_participation_retained,
  authority_context, reason, source, idempotency_key
)

reassign_football_effective_category(
  admin_actor, season_assignment_id, expected_current_category,
  target_category, base_participation_retained,
  reason, source, idempotency_key
)
```

Both resolve canonical objects, replay-check, lock assignment, validate expected state, append one event, update projections, and commit once. Web/API are adapters only.

## 7. Coach authorization matrix

`✓` means permitted to teach in the named role/category; `—` means denied.

| Coach qualification | PRE Assistant | PRE Head | YOUTH Assistant | YOUTH Head | AMATEUR Assistant | AMATEUR Head | PRO Assistant | PRO Head |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| PRE Assistant | ✓ | — | — | — | — | — | — | — |
| PRE Head | ✓ | ✓ | — | — | — | — | — | — |
| YOUTH Assistant | ✓ | — | ✓ | — | — | — | — | — |
| YOUTH Head | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| AMATEUR Assistant | ✓ | — | ✓ | — | ✓ | — | — | — |
| AMATEUR Head | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| PRO Assistant | ✓ | — | ✓ | — | ✓ | — | ✓ | — |
| PRO Head | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

### 7.1 Promotion approval

| Target category | Head qualifications allowed | Assistant | Lower Head |
|---|---|---|---|
| PRE | PRE, YOUTH, AMATEUR, or PRO Head | No | N/A |
| YOUTH | YOUTH, AMATEUR, or PRO Head | No | PRE Head denied |
| AMATEUR | AMATEUR or PRO Head | No | PRE/Youth Head denied |
| PRO | PRO Head only | No | PRE/Youth/Amateur Head denied |

Qualification alone is insufficient: the Head must also have active object/season/location assignment authority for the player/target context. Admin override is separately audited and is not a Coach qualification.

## 8. Canonical age-policy decision table

Each ID maps to the inventory conflict register.

| ID | Old value(s) | Canonical value | Affected runtime paths | Required remediation | Schema impact | Compatibility treatment | Required tests |
|---|---|---|---|---|---|---|---|
| AC-01 | Football min 5 vs 6 | 5 | config/helper/player service/native | Shared policy; remove service 6+ gate | None | Disable old rule; no rewrite | Ages 4/5 on every path |
| AC-02 | PRE 5–13 vs 6–11 vs `<7` vs U6–U8 | PRE 5–13 | category/player/card/coach labels | One policy; consumers display server result | Season assignment | Preserve historical values | Ages 5/13; helper parity |
| AC-03 | YOUTH 14–18 vs 12–18; L3/L4 min 9; card 7–14 | YOUTH 14–18; levels no age | config/service/cards | Remove level-age/local calculations | Season constraints | Raw history unchanged | Ages 13/14/18; L3/L4 age ignored |
| AC-04 | Adult auto/manual/default differs | 19+ base AMATEUR; PRO promotion-only; 14–18 may promote | season/tournament/web | Canonical season and movement policy | Assignment/events | Proven current state read-only | Ages 18/19; promotion |
| AC-05 | Season vs calendar recategorization | Base uses July 1; birthday no base change | helpers/service/cards/tournament | Read canonical assignment | Stored age/date/policy | UI fallback cannot authorize | Birthday; June30/July1 |
| AC-06 | Locked narrative vs one mutable field | Immutable base + effective + event history | enrollment/override | Add model and adapter | Assignment/event/FK | Old field projection only | Immutability/history |
| AC-07 | Any instructor/admin, self-service, unchecked | Qualified Head promotion; admin override/revocation | override/tournament/service | One object policy | Actor/authority fields | Old routes become adapters | Head/Assistant/self/admin scope |
| AC-08 | Upward docs vs executable downward/bypass | Head upward; admin downward; never below base | validator/session/override | Canonical transition matrix | Movement type/base constraint | No history rewrite | Downward/base-floor |
| AC-09 | API match vs web bypass vs stale helper | Shared effective/retained eligibility | tournament API/web/helper | Both consumers call policy | Enrollment assignment FK | Shape adapter only | Web/API parity |
| AC-10 | Unified entitlement vs season-type license | Only `LFA_FOOTBALL_PLAYER` entitlement | license/service/resolver | Shared resolver; prohibit category license IDs | Canonical program field | Ambiguous review-required | Canonical/season-only contradiction |
| AC-11 | Annual vs monthly/quarterly/~15-month periods | Eligibility season Jul1–Jun30; periods cannot redefine base | generators/templates | Separate eligibility assignment | Annual assignment link | Periods unchanged | Crossing period no recategorization |
| AC-12 | Coach levels all 14 vs age ladder | Admission 14; no level ages | coach config/service/tests | Remove level-age gates | None | Levels/history unchanged | Progress at 14 with professional rules |
| AC-13 | Coach target bands conflict | Canonical PRE/YOUTH/AMATEUR/PRO scopes | coach service/display | Consume canonical category scope | None | Old labels display-only | Scope inheritance/denial |
| AC-14 | Coach-only consent and bypasses | Global consent under 18 | central/bearer/web/program routes | Shared profile gate | Guardian consent table | Legacy boolean projection | 14–17 with/without consent |
| AC-15 | Missing DOB pass vs reject | Missing DOB fails normal use/entitlement | validator/admin/API/web | Fail-closed policy | DOB unchanged initially | Existing null retained but blocked | Missing DOB all paths |
| AC-16 | Coach ladder/exam coupling | No age after 14; professional exam policy separate | promotion/service/config | Remove age coupling | None | History unchanged | Age-14 higher progression |
| AC-17 | GānCuju missing-DOB variants | DOB required; 5+; consent under 18 | generic/direct/web | Shared gates | Consent table | Incomplete rows blocked | Missing DOB; 4/5; consent |
| AC-18 | PLAYER maps or expires | `PLAYER -> GANCUJU_PLAYER` always | resolver/parallel/service | One resolver; remove deadline rejection | Canonical program field | No rewrite | Old-deadline parity |
| AC-19 | Internship 16 vs 18 vs none | 18 | config/service/helper/native/web | Shared program policy | None | Old config cannot authorize | Ages 17/18 every route |
| AC-20 | Internship missing DOB varies | Missing DOB fails; 18+ | central/helper/web/program | Shared gates | None | Incomplete rows blocked | DOB/route parity |
| AC-21 | Internship 3 levels vs 5 semesters/8 levels | All age-independent after 18; structural mapping not an age gate | config/service/enum | Remove any later age threshold | None for age | No reinterpretation | All later stages age-independent |
| AC-22 | Web account 5–120 vs API/admin no bound | DOB required; min 5; no canonical max | registration/profile/admin/API | Central creation/activation policy | Consent table | Existing records unchanged | 4/5, future DOB, parity |
| AC-23 | Missing DOB means pass/reject/not-minor | Always `DOB_REQUIRED`; biometrics fail closed | User/spec/biometric | Stop unknown-age bypass | Optional computed status only | Null retained but blocked | Account/program/biometric |
| AC-24 | Three unlock policies | One profile + program + entitlement policy | central/bearer/web/program | Thin adapters | Canonical program/consent | Response compatibility only | All unlock routes |

All 24 age-policy conflicts are resolved at business-rule level. Repository remediation is future implementation work.

## 9. Authorization policy

### 9.1 Football movement

Normal promotion requires active target-qualified Head Coach entitlement, exact object/season/period/location scope, player professional eligibility, and current age 14+ only for PRO. Player self-service, Assistant approval, inactive/wrong-scope Head, and lower-category Head are denied.

Downward reassignment/revocation requires active admin authority. Admin cannot change season base, erase history, create below-base state, omit reason/source, or bypass idempotency/transaction rules.

### 9.2 Coach teaching

Section 7 is canonical. API/web receive one policy result and do not compare raw integer levels/local strings. Instructor assignment scope is required in addition to qualification.

### 9.3 Global eligibility

Admin may manage records but cannot bypass DOB, age, consent, or biometric restrictions to create usable entitlement/participation. Jobs/imports use the same policies and return structured denial/review results.

## 10. API, web, and native contracts

### 10.1 Profile eligibility

```json
{
  "profile_eligibility_status": "ELIGIBLE",
  "date_of_birth_present": true,
  "guardian_consent_required": true,
  "guardian_consent_active": true,
  "biometric_eligible": false
}
```

### 10.2 Football season

```json
{
  "program_id": "LFA_FOOTBALL_PLAYER",
  "season": {"start_date": "YYYY-07-01", "end_date": "YYYY+1-06-30", "policy_version": "..."},
  "classification_status": "CANONICAL",
  "season_base_category": "PRE",
  "effective_category": "YOUTH",
  "base_participation_retained": true,
  "eligible_participation_categories": ["PRE", "YOUTH"],
  "last_movement_at": "..."
}
```

`classification_status` is `CANONICAL`, `LEGACY_COMPATIBLE`, or `REVIEW_REQUIRED`.

### 10.3 Commands/errors

Planned reads cover own assignment and scoped assignment/history. Promotion accepts target, expected current, retained flag, reason, authority context, and idempotency key. Admin reassignment/revocation uses the same domain boundary. Existing override becomes an adapter and cannot mutate enrollment directly.

Stable errors:

```text
PROFILE_DOB_REQUIRED
PROFILE_BELOW_MINIMUM_AGE
GUARDIAN_CONSENT_REQUIRED
BIOMETRIC_ADULT_REQUIRED
PROGRAM_AGE_NOT_ELIGIBLE
PROGRAM_ENTITLEMENT_REVIEW_REQUIRED
SEASON_CLASSIFICATION_REQUIRED
CATEGORY_MOVEMENT_NOT_ALLOWED
CATEGORY_BELOW_SEASON_BASE
CATEGORY_PROMOTION_HEAD_REQUIRED
CATEGORY_ASSIGNMENT_FORBIDDEN
CATEGORY_ASSIGNMENT_CONFLICT
LEGACY_WALLET_READ_ONLY
```

### 10.4 Web/native compatibility

- Web/native display server eligibility and base/effective/retention state; they do not calculate policy locally.
- Consent-pending users reach only consent completion.
- Player self-service promotion is absent.
- Controls follow server permission; server remains authoritative.
- New native fields are additive/optional during compatibility.
- Disabled Coach/GānCuju/Internship native journeys remain disabled in WS1.
- Every HTTP change requires OpenAPI diff and Swift decoding fixture. Shape compatibility cannot preserve conflicting behavior.

## 11. Expected additive schema changes

One schema-only Alembic revision is planned for disposable PostgreSQL rehearsal:

1. Nullable `user_licenses.canonical_program_id` with four-value check.
2. Partial unique index `(user_id, canonical_program_id)` where non-null.
3. `user_guardian_consents` plus one-active-consent partial unique index.
4. `football_season_category_assignments` with season/base/effective/retention constraints.
5. `football_category_movement_events` with replay uniqueness/history-preserving FKs.
6. Nullable indexed `semester_enrollments.football_season_category_assignment_id` FK.
7. Nullable `credit_transactions.context_user_license_id` as non-owning context.
8. History-preserving replacement of legacy credit-context cascade only if PostgreSQL rehearsal proves safe.

The revision performs no DML/backfill. Existing nullable columns do not become non-null initially. DOB/consent is enforced by canonical policy during compatibility; later constraint tightening requires separate evidence/review. Existing specialization, category, consent projection, license-wallet, and ledger values remain untouched.

## 12. Historical compatibility and migration strategy

### DISCOVER

On restored/disposable PostgreSQL report without writes: entitlement values/duplicates; missing/invalid DOB; minor consent; canonical age outcome; football categories/history gaps; season boundaries; Coach levels depending on old age ladder; Internship admitted by 16+ paths; legacy wallets/ledger reconciliation.

### MAP

Canonical/approved aliases map for reporting. Ambiguous evidence is `REVIEW_REQUIRED`. Existing active/proven history is not rewritten. Underage/missing-DOB/consent/Coach/GānCuju/Internship records are reported, not reinterpreted/deleted. Financial value is reported only.

### REHEARSE

Apply/downgrade schema-only migration on disposable PostgreSQL. With synthetic rows prove profile/consent, four-program eligibility, entitlement uniqueness, season creation, all football transitions/retention, Coach matrix, replay/concurrency/rollback, untouched legacy compatibility, and non-owning credit context.

### VALIDATE

Compare counts/values before and after, run WS1 and P0 gates, OpenAPI diff, web/API parity, and affected native decoding/build gate.

### MIGRATE

Not authorized. No historical canonical-ID population, DOB correction, consent fabrication, category reconstruction, entitlement reinterpretation, wallet transfer, or ledger rewrite occurs without a separate approved migration gate.

## 13. Boundary and regression test matrix

### 13.1 Global account/profile

| Case | Expected result |
|---|---|
| Age 4 | Account/profile rejected; no side effect |
| Turns 5 tomorrow | Rejected today |
| Exactly age 5 | Creation allowed; guardian consent required before normal use |
| Missing DOB | `PROFILE_DOB_REQUIRED`; no route/admin bypass |
| Future/invalid DOB | `INVALID_DOB`; no side effect |
| Under 18 without consent | Consent-pending only; normal use/enrollment/unlock denied |
| Under 18 with active consent | Non-biometric use permitted subject to program policy |
| Consent revoked while minor | Protected activity denied; history retained |
| Biometric age 17 | Denied even with guardian consent |
| Biometric age 18 | Eligible after biometric disclosure/consent prerequisites |
| Web/API/admin/program creation | Identical result/errors |

### 13.2 Football

| Case | Expected result |
|---|---|
| Age 4 / 5 at July 1 | 4 denied; 5 PRE |
| Age 13 / 14 at July 1 | 13 PRE; 14 YOUTH |
| Age 18 / 19 at July 1 | 18 YOUTH; 19 AMATEUR |
| June 30 / July 1 | Prior season remains / new season recalculated |
| Birthday during season | Base unchanged; no automatic event |
| PRE-base player turns 14 | Base remains PRE; PRO age prerequisite checked only on explicit promotion |
| PRE → AMATEUR | Allowed at current age 14+ with Amateur/Pro Head, professional eligibility, retained flag |
| YOUTH → PRO | Allowed only with PRO Head and professional eligibility |
| PRE → PRO while current age under 14 | Rejected |
| PRE base, AMATEUR effective → YOUTH | Admin-only audited downward event |
| PRE base, YOUTH effective → PRE | Admin-only return; retained false |
| YOUTH base → PRE | Rejected below base, including admin |
| retained=true / false | Base+effective only / effective only |
| PRE→AMATEUR retained=true | PRE and AMATEUR allowed; YOUTH not implied |
| Assistant promotion | Forbidden |
| Target-qualified Head | Allowed only with active object scope |
| Lower Head / unassigned Head | Forbidden |
| Admin override/revocation | Event/reason/idempotency required; base immutable |

### 13.3 Coach

| Case | Expected result |
|---|---|
| Age 13 / 14 | 13 denied; 14 allowed with active consent |
| Age 14–17 without / with consent | Denied / admitted |
| Age 14 at higher level | No age denial if professional requirements pass |
| Eight qualification rows | Exact section-7 cells |
| Head / Assistant inheritance | Head includes Assistant; Assistant never Head |
| Higher / lower category qualification | Downward inheritance / higher scope denied |
| Assistant promotion approval | Denied |
| Target-qualified Head approval | Allowed only with assignment scope |
| Consent revoked for minor Coach | Protected activity denied; history/level unchanged |

### 13.4 GānCuju

| Case | Expected result |
|---|---|
| Age 4 / 5 | 4 denied; 5 eligible with consent |
| Missing DOB | Denied |
| Minor without / with consent | Denied / allowed |
| High belt at young age | No age denial if performance requirements pass |
| Every belt transition | No additional age threshold |
| `PLAYER` after old deadline | Resolves identically everywhere |

### 13.5 Internship

| Case | Expected result |
|---|---|
| Age 17 / 18 | 17 denied; 18 admitted |
| Old 16+ route | Age 16/17 cannot be authorized |
| Missing DOB | Denied |
| Semester 2–5 | No later age threshold after valid admission |
| Config/numeric levels | No later age threshold |
| Project/workplace | Valid 18+ admission; no separate threshold |

### 13.6 Entitlement/movement/PostgreSQL

- Four canonical IDs only; aliases consistent; ambiguity fails closed.
- Canonical-plus-alias race cannot create duplicate entitlement.
- Event and effective/retained projection commit or roll back together.
- Same-key replay returns one result/event.
- Concurrent same-key requests create one event.
- Different-key requests serialize; stale expected-current gets conflict.
- History reconstructs effective/retained state; base never changes.
- Movement does not change level/XP/curriculum implicitly.

### 13.7 Credit/P0/contracts

- Global balance and user-owned ledger mutate atomically; license context never owns balance.
- Legacy wallet remains excluded/read-only; license deletion cannot delete history.
- All P0 replay/rollback/concurrency/unlock/renewal/cancellation/refund/auth/logging tests remain green.
- OpenAPI diff, web/API policy parity, Swift additive/legacy decoding, and affected native build gate pass.
- Raw exceptions and guardian evidence are not exposed.

All race tests use independent sessions/connections and synchronization barriers on disposable PostgreSQL. SQLite/single-session mocks are insufficient concurrency evidence.

## 14. Rollback plan

Before canonical rows exist, schema-only migration may be downgraded on disposable PostgreSQL after proving no references exist. Once canonical rows exist, do not drop consent, assignment, movement, entitlement, or financial history. Roll back application routing while retaining additive schema read-only, then forward-repair.

- Profile/consent: retain consent records and fail-closed eligibility.
- Resolver: retain raw/canonical values; ambiguity stays fail-closed.
- Football: stop commands; preserve base/effective/retained/events.
- Coach: revert adapters without re-enabling excess scope or age-ladder writes.
- Progression: preserve committed transitions; no reverse sync/history deletion.
- Credits: retain P0 global service/ledger; never re-enable license-wallet spending.
- Web/API/native: disable new surfaces while server policy remains authoritative.

Rollback cannot fabricate consent, activate underage/unknown-DOB profiles, alter base, delete events, automatically move category, reinterpret legacy entitlement, or mutate financial history.

## 15. Planned implementation touchpoints

Expected existing modules:

- `app/models/user.py`, `specialization.py`, `license.py`, `user_progress.py`, `semester_enrollment.py`, `credit_transaction.py`;
- `app/services/age_category_service.py`, both specialization validation modules, four program services, teaching/coach-level/tournament/renewal/credit services;
- account/profile/onboarding/specialization/enrollment/tournament/category-override API/web adapters;
- biometric disclosure gate;
- native profile/program/season response models;
- one additive Alembic revision and targeted WS1 tests.

Bounded new components:

- canonical program resolver;
- global profile/program eligibility policy;
- guardian-consent model/service;
- football season/category model/policy;
- category movement command/policy;
- Coach qualification/authorization policy;
- additive schemas;
- read-only reconciliation report.

No generalized policy framework, event platform, broad progression rewrite, historical backfill, or new product feature is planned.

## 16. Final decision status

### Resolved age conflicts

- All 24 inventory conflicts have canonical outcomes in section 8.
- Football minimum/categories/season/adult base/PRO promotion/level ages are resolved.
- Base/effective separation, skipped promotion, retained-base participation, downward effective reassignment, and authorization are resolved.
- Coach admission, global minor consent, non-age progression, eight-role matrix, and promotion authority are resolved.
- GānCuju admission/age-independent belts and Internship 18+/age-independent later stages are resolved.
- Missing DOB, route bypass, global age, and biometric 18+ are resolved.

### Unresolved owner decisions

**None for age/category/Coach business logic.** Legacy wallet valuation/transfer remains intentionally deferred and does not block WS1 planning. Historical migration remains unauthorized and requires a separate gate; this is a control boundary, not an unresolved rule.

### Planned schema changes

- canonical program ID/uniqueness;
- guardian-consent history;
- annual football base/effective/retention assignment;
- append-only category events;
- enrollment-to-assignment link;
- non-owning credit context;
- history-preserving FK behavior where rehearsal proves safe.

### Planned policy changes

- one global DOB/age/minor-consent/biometric policy;
- one per-program admission policy;
- one football season/category/movement policy;
- one Coach qualification/permission policy;
- one entitlement resolver/command path across web/API;
- global credit ownership and read-only legacy wallet retained.

### Planned tests

- global 4/5, DOB, consent, biometric 17/18;
- football 4/5, 13/14, 18/19, June30/July1, birthday, skip promotion, retention, downward/base floor, Head/Assistant;
- Coach 13/14, consent, full 8×8 matrix, inheritance/higher-scope denial;
- GānCuju 4/5 and high-belt age independence;
- Internship 17/18, old 16+ rejection, later-stage age independence;
- alias/entitlement, PostgreSQL replay/concurrency/rollback, P0/security, OpenAPI/web/native parity.

# WS1 CANONICAL BUSINESS LOGIC SPEC COMPLETE

- **Resolved age conflicts:** 24/24
- **Unresolved owner decisions:** none for age/category/Coach business logic
- **Baseline SHA:** `894c43a4a37af4a8a583240a5a2d0b1f31a3fdee`
- **Implementation status:** not started

`OWNER AGE/CATEGORY POLICY COMPLETE`

**IMPLEMENTATION NOT STARTED**
