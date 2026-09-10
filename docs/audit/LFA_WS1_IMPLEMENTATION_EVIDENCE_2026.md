# LFA WS1 implementation evidence (2026-09-09)

## Scope and provenance

- Baseline: `LFA_CANONICAL_BASELINE_P0` / `894c43a4a37af4a8a583240a5a2d0b1f31a3fdee`
- Branch: `codex/ws1-canonical-domain`
- Implemented findings: F15, F16, F17 and their direct compatibility dependencies.
- Excluded: WS2 and later workstreams, P2/P3 implementation, deployment, production database work, historical reinterpretation and balance/category backfill.

## Finding status

| Finding | Status | Evidence |
|---|---|---|
| F15 — conflicting specialization/license representations | CLOSED in WS1 | Four canonical program IDs; proven aliases resolve through one policy; ambiguous IDs fail closed; new entitlement writes persist a canonical ID. |
| F16 — age/season/category policy | CLOSED in WS1 | Shared age/consent/program policy; July 1 season boundary; immutable season base separated from effective assignment; audited movement command; canonical Coach matrix. |
| F17 — global credit vs legacy license wallet | CLOSED in WS1 | `User.credit_balance` and user-owned ledger remain write authority; license wallet is database-enforced read-only; license reference is context only; historical rows preserved. |

## Canonical authorities

- `app/services/canonical_policy.py`: program identity and aliases, profile/DOB/guardian/biometric policy, program minimum age, football season/base category, movement rules and Coach authorization matrix.
- `app/services/program_eligibility_service.py`: database-aware consent and program eligibility decision used by API/web/service adapters.
- `app/services/football_category_movement_service.py`: object-authorized, transactional, idempotent and PostgreSQL concurrency-safe football movement command.
- `app/services/credit_service.py`: global user balance/ledger write path; specialization/license is transaction context.

Compatibility facades call these authorities. The web and API layers no longer own separate age, entitlement, Coach or credit rules in the changed paths. The Swift unlock consumer uses the canonical program identifier and builds/tests successfully.

## Additive schema migration

Migration: `alembic/versions/2026_09_09_1000_ws1_canonical_domain.py`, revision `2026_09_09_1000`, parent `2026_06_24_1000`.

Changes:

- nullable `user_licenses.canonical_program_id`, constrained to the four canonical IDs, with a partial per-user uniqueness index;
- database trigger blocks new non-zero legacy license wallets and all later mutation of existing license wallet balances;
- `user_guardian_consents`, allowing one active consent per user and preserving grant/revoke evidence;
- `football_season_category_assignments`, separating `season_base_category` and `effective_category`, with player/license season uniqueness and optimistic version;
- append-only `football_category_movement_events`, including actor, authorization basis, source, reason/context, prior/target category, retained-base flag, timestamp and unique idempotency key;
- nullable enrollment projection link `semester_enrollments.football_category_assignment_id`;
- nullable `credit_transactions.context_user_license_id`;
- a `NOT VALID` check enforces user-owned ledger rows for new writes while retaining historical license-owned records;
- historical financial FK deletion behavior changes from `CASCADE` to `RESTRICT`.

No migration DML, entitlement reinterpretation, wallet aggregation, category backfill or historical progression rewrite is present. Downgrade and re-upgrade were exercised on disposable PostgreSQL.

## Legacy compatibility

- Proven aliases: `PLAYER -> GANCUJU_PLAYER`, `COACH -> LFA_COACH`, `INTERNSHIP -> INTERNSHIP`.
- Canonical IDs: `LFA_FOOTBALL_PLAYER`, `LFA_COACH`, `GANCUJU_PLAYER`, `INTERNSHIP`.
- Ambiguous `LFA_PLAYER` and category-as-license forms resolve to `MANUAL_REVIEW` and fail closed.
- Existing legacy entitlement values remain stored; no automatic update was run. A row whose new canonical ID conflicts with its legacy value also fails closed.
- Legacy wallet values and license-owned ledger rows remain readable evidence, but cannot become a second spending authority.
- Existing enrollment `age_category` remains a compatibility projection from the canonical assignment after an explicit movement.

## Policy boundary results

### Global and programs

- Account/profile: age 4 denied, age 5 accepted; DOB required; guardian consent required below 18; biometrics denied at 17 and allowed at 18.
- Football and GānCuju: age 4 denied, age 5 accepted.
- Coach: age 13 denied, age 14 accepted subject to guardian consent.
- Internship: age 17 and the legacy 16+ path denied; age 18 accepted; later semester does not add an age threshold.
- GānCuju level progression remains performance/progression based after entry.

### Football

- Season is July 1 through June 30.
- Base category uses age on season start: PRE 5–13, YOUTH 14–18, AMATEUR 19+; PRO is never an automatic base.
- June 30/July 1 and age 4/5, 13/14, 18/19 boundaries pass.
- Birthday during a season does not recalculate the stored base category.
- Explicit PRE→YOUTH, PRE→AMATEUR, YOUTH→PRO and multi-category movement pass subject to the target age/policy.
- Head Coach and admin override paths pass; Assistant and player self-service paths are denied.
- Admin downward movement passes only at or above season base; movement below base is denied.
- `base_participation_retained=true/false` projections pass.
- Replay returns the original event; conflicting replay fails; competing versioned movements produce one commit and one version conflict.

### Coach 8×8 matrix

All 64 source-level/target-scope combinations pass. Levels 1–8 map in order to PRE Assistant/Head, YOUTH Assistant/Head, AMATEUR Assistant/Head, PRO Assistant/Head. Authority inherits only toward lower categories. Head grants Head and Assistant at the same/lower category; Assistant grants only Assistant at the same/lower category. Lower-category coaches cannot teach higher categories.

## Test evidence

| Gate | Result |
|---|---|
| Full unit suite | PASS — 13,930 passed; 21 skipped; 21 xfailed; 1 xpassed; 9,771 warnings; 97.29 s |
| Full integration suite | 2,660 passed; 3 skipped; 6 failed; 4,561 warnings; 124.85 s |
| Final P0 + WS1 PostgreSQL regression | PASS — 40 passed; 212 warnings; 1.04 s |
| OpenAPI + affected web contract | PASS — 102 passed; 218 warnings; 6.34 s |
| iOS simulator build | PASS — `BUILD SUCCEEDED` |
| iOS simulator tests | PASS — 962 executed; 0 failures; `TEST SUCCEEDED` |
| Python compileall | PASS |
| `git diff --check` | PASS |
| Alembic head/current | PASS — `2026_09_09_1000 (head)` |

The six full integration failures are retained as failures rather than hidden:

1. Four `test_promotion_seed_smoke.py` cases stop because a clean migrated database has no required `TournamentType(code='group_knockout')`. This seed prerequisite is outside WS1 and is also explicitly skipped by another case in the same module.
2. Two `test_users_realistic.py` list cases encounter suite-created reserved `.test` email data and the response schema correctly rejects those addresses. On an otherwise clean database the module instead lacks its assumed three smoke users, confirming an integration fixture/isolation dependency rather than a WS1 domain-policy failure.
3. The three skips are: absent `group_knockout`; absent pre-existing `rw01concurr00` XP idempotency constraint; and no tournament rankings fixture.

The unit skips are browser tests without Playwright. The xfails/xpass are existing expected-status tests outside WS1. No failing WS1 policy, migration, replay, authorization, credit or native test remains.

## Disposable PostgreSQL evidence

- All WS1 databases were created with `UTF8` encoding on local port 55433; no production endpoint was used.
- Clean upgrade from base through `2026_09_09_1000`: PASS.
- Downgrade to `2026_06_24_1000` and re-upgrade: PASS.
- Historical fixture before upgrade: user balance 100, legacy license wallet 777, one license-owned ledger row.
- After upgrade and downgrade/re-upgrade: `100|777|1|1|true` — global balance unchanged; wallet preserved; one historical license-owned row preserved; one new canonical user-owned/context row present; no canonical ID backfill.
- Unrelated historical license update: accepted.
- Legacy wallet mutation: rejected by PostgreSQL.
- New user-owned ledger row with license context: accepted.
- New license-owned ledger row: rejected by PostgreSQL.
- Credit replay/concurrency/rollback, unlock, renewal and cancellation/refund P0 invariants: PASS in the final 40-test PostgreSQL gate.
- Football movement replay and conflicting replay, row/advisory locking, version conflict and rollback behavior: PASS.

## API, web and native contract

- OpenAPI snapshot is deliberately updated for additive DOB/guardian-consent and canonical program contract changes; snapshot verification passes.
- Affected web registration, dashboard, specialization and instructor-dashboard contract tests pass.
- Invalid/ambiguous program IDs produce controlled 4xx behavior and do not expose internal exceptions.
- Swift unlock model sends the canonical football program ID. Generic iOS simulator build and 962 tests pass.
- Three absent native program flows were not built because that is explicitly outside WS1.

## Security and scope review

- WS1 diff scan found no new secret, token, authorization-header, cookie or raw credential logging.
- P0 password/hash, sensitive-header, authorization, replay, rollback and concurrency regressions pass in the final targeted gate.
- Pre-existing baseline observation: `app/core/init_admin.py` prints `settings.ADMIN_PASSWORD`. The line is unchanged from baseline SHA `894c43a...`; it was not altered because the current instruction prohibits work outside WS1. It requires a separately authorized P0 security closure before an unconditional security merge recommendation.
- No production deployment, network environment mutation or production DB mutation occurred.
- Historical records modified: NO.
- Data migration performed: NO.
- WS2 implementation started: NO.

## Encoding and language hygiene

- PostgreSQL encoding: `UTF8` for every successful disposable migration and test database.
- SQL_ASCII failure root cause: the PostgreSQL client cannot encode Unicode text from the pre-existing squashed baseline migration. The failure occurs in `2026_02_21_0859-1ec11c73ea62_squashed_baseline_schema.py` inside its large `op.execute(...)`, first at source line 955 (`Budaörs`; statement position 36241). This occurs before the WS1 migration.
- Offending baseline file: `alembic/versions/2026_02_21_0859-1ec11c73ea62_squashed_baseline_schema.py` (notably lines 955, 1795, 2227 and 2734). Other migration sources with Unicode SQL comments are `2026_03_15_1400-drop_is_tournament_game.py`, `2026_04_21_1100_session_segments.py`, `2026_05_16_1000_add_wc_photo_fields.py`, `2026_06_08_1000_add_profile_photo.py`, `2026_06_09_1000_add_academy_id_phase_2a.py`, `2026_06_10_1000_add_biometric_foundation.py`, and `2026_06_11_1100_add_juggling_retention_fields.py`.
- Hardcoded non-English runtime strings found: YES. The repository already contains Hungarian runtime/template text; touched `app/services/parallel_specialization_service.py` also contains Hungarian display and decision reasons.
- Localization follow-up required: YES. It is deferred because localization refactoring is outside WS1.

## Remaining risks and recommendation

- The complete integration run is not fully green because of the six documented seed/fixture failures. These are outside WS1 but should be made deterministic before treating the repository-wide integration job as a strict required CI gate.
- Historical records with ambiguous entitlement meaning remain intentionally unmapped and fail closed; an owner-approved DISCOVER → MAP → REHEARSE → VALIDATE → MIGRATE gate would be required for any later conversion.
- The `NOT VALID` global-ledger constraint protects new rows but intentionally does not validate historical rows. A later validation requires explicit historical review, not an automatic backfill.
- Hardcoded localized strings remain distributed through the application.
- The unchanged raw admin-password print is a security blocker for an unconditional merge recommendation.

Merge recommendation: **HOLD** until the pre-existing raw admin-password print is closed in an explicitly authorized security-only change. After that closure, the WS1 domain changes are merge-ready subject to accepting the documented repository integration fixture/seed limitations. No WS1 owner-domain decision remains open.

## Modified-file manifest

The manifest below is generated from the WS1 worktree immediately before commit.

### Application/config/native/migration files

- `app/api/api_v1/endpoints/_semesters_main.py`
- `app/api/api_v1/endpoints/admin_players.py`
- `app/api/api_v1/endpoints/auth.py`
- `app/api/api_v1/endpoints/instructor_management/assignments.py`
- `app/api/api_v1/endpoints/instructor_management/masters/direct_hire.py`
- `app/api/api_v1/endpoints/licenses/instructor.py`
- `app/api/api_v1/endpoints/semester_enrollments/category_override.py`
- `app/api/api_v1/endpoints/semester_enrollments/crud.py`
- `app/api/api_v1/endpoints/semester_enrollments/schemas.py`
- `app/api/api_v1/endpoints/specializations/info.py`
- `app/api/api_v1/endpoints/tournaments/cancellation.py`
- `app/api/api_v1/endpoints/tournaments/enroll.py`
- `app/api/api_v1/endpoints/users/crud.py`
- `app/api/web_routes/admin/users.py`
- `app/api/web_routes/auth.py`
- `app/api/web_routes/dashboard.py`
- `app/api/web_routes/helpers.py`
- `app/api/web_routes/instructor_dashboard.py`
- `app/api/web_routes/onboarding.py`
- `app/api/web_routes/programs.py`
- `app/api/web_routes/specialization.py`
- `app/api/web_routes/tournaments/browse.py`
- `app/api/web_routes/tournaments/camps.py`
- `app/models/__init__.py`
- `app/models/credit_transaction.py`
- `app/models/license.py`
- `app/models/semester_enrollment.py`
- `app/models/user.py`
- `app/services/age_category_service.py`
- `app/services/biometric/disclosure_service.py`
- `app/services/coach_level_service.py`
- `app/services/credit_service.py`
- `app/services/csv_import_service.py`
- `app/services/license_service.py`
- `app/services/parallel_specialization_service.py`
- `app/services/semester_service.py`
- `app/services/shared/license_validator.py`
- `app/services/specialization/validation.py`
- `app/services/specialization_validation.py`
- `app/services/specs/semester_based/gancuju_player_service.py`
- `app/services/specs/semester_based/lfa_coach_service.py`
- `app/services/specs/semester_based/lfa_internship_service.py`
- `app/services/specs/session_based/lfa_player_service.py`
- `app/services/sponsor_promote_service.py`
- `app/services/teaching_permission_service.py`
- `app/services/tournament/core.py`
- `app/services/tournament/instructor_eligibility_service.py`
- `app/services/tournament/team_service.py`
- `app/templates/admin/users.html`
- `app/templates/register.html`
- `app/utils/age_requirements.py`
- `config/specializations/internship.json`
- `config/specializations/lfa_football_player.json`
- `ios/LFAEducationCenter/Unlock/UnlockViewModel.swift`
- `alembic/versions/2026_09_09_1000_ws1_canonical_domain.py`
- `app/models/ws1_domain.py`
- `app/services/canonical_policy.py`
- `app/services/football_category_movement_service.py`
- `app/services/program_eligibility_service.py`

### Test files

- `tests/fixtures/builders.py`
- `tests/integration/api_smoke/conftest.py`
- `tests/integration/api_smoke/test_admin_smoke.py`
- `tests/integration/api_smoke/test_license_advancement_smoke.py`
- `tests/integration/api_smoke/test_licenses_smoke.py`
- `tests/integration/conftest.py`
- `tests/integration/test_lfa_coach_service.py`
- `tests/integration/test_lfa_coach_service_simple.py`
- `tests/integration/web_flows/test_attack_surface.py`
- `tests/integration/web_flows/test_captain_reenroll.py`
- `tests/integration/web_flows/test_club_csv_import.py`
- `tests/integration/web_flows/test_credit_transactions.py`
- `tests/integration/web_flows/test_critical_e2e.py`
- `tests/integration/web_flows/test_onboarding_cancel_refund.py`
- `tests/integration/web_flows/test_sponsor_baseline.py`
- `tests/integration/web_flows/test_sponsor_campaign.py`
- `tests/integration/web_flows/test_sponsor_campaign_promotion.py`
- `tests/integration/web_flows/test_sponsor_cleanup_http.py`
- `tests/integration/web_flows/test_sponsor_promote.py`
- `tests/integration/web_flows/test_team_business_flow.py`
- `tests/integration/web_flows/test_team_ui_flow.py`
- `tests/p0/test_credit_postgres.py`
- `tests/snapshots/openapi_snapshot.json`
- `tests/unit/api/web_routes/test_admin_web.py`
- `tests/unit/api/web_routes/test_dashboard_web.py`
- `tests/unit/api/web_routes/test_instructor_dashboard_web.py`
- `tests/unit/api/web_routes/test_registration_web.py`
- `tests/unit/api/web_routes/test_specialization_web.py`
- `tests/unit/juggling/test_juggling_video_list.py`
- `tests/unit/services/semester_based/test_gancuju_player_service.py`
- `tests/unit/services/semester_based/test_lfa_coach_service.py`
- `tests/unit/services/test_age_category_service.py`
- `tests/unit/services/test_assignments_endpoint.py`
- `tests/unit/services/test_coach_level_service.py`
- `tests/unit/services/test_credit_service.py`
- `tests/unit/services/test_credit_service_unit.py`
- `tests/unit/services/test_instructor_eligibility_service.py`
- `tests/unit/services/test_instructor_masters_direct_hire.py`
- `tests/unit/services/test_lfa_internship_service.py`
- `tests/unit/services/test_lfa_player_service.py`
- `tests/unit/services/test_license_service.py`
- `tests/unit/services/test_license_validator.py`
- `tests/unit/services/test_licenses_instructor_endpoint.py`
- `tests/unit/services/test_semester_enrollments_payment_endpoint.py`
- `tests/unit/services/test_semester_enrollments_workflow_endpoint.py`
- `tests/unit/services/test_specialization_validation.py`
- `tests/unit/services/test_teaching_permission_service.py`
- `tests/unit/services/test_user_model.py`
- `tests/unit/services/test_users_crud_endpoint.py`
- `tests/unit/test_admin_players_helpers.py`
- `tests/unit/test_age_requirements.py`
- `tests/unit/test_parallel_specialization.py`
- `tests/unit/tournament/test_stats_service.py`
- `tests/integration/test_ws1_credit_authority_postgres.py`
- `tests/integration/test_ws1_football_movement_postgres.py`
- `tests/integration/test_ws1_identity_profile_postgres.py`
- `tests/unit/services/test_ws1_canonical_policy.py`
- `tests/unit/services/test_ws1_config_contract.py`
- `tests/unit/services/test_ws1_schema_contract.py`
