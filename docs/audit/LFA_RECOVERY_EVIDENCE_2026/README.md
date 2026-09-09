# Recovery audit evidence — 2026-09-08

Audit-only companion files. No application source or migration was edited.
Repository SHA: a14380c7697817c7c75587007839ba319dc39cc6.

The main report and machine inventories are one directory above this folder.
JSON/CSV source references are repository-relative. Runtime source references
inside .venv describe framework-owned routes, not repository source.

## Reproduction boundary

The .py.txt files preserve audit harness source as documentation. Do not run
the application lifespan, migrations, seeders, full pytest or integration/E2E
suites against a configured development/production database to reproduce this.

To reproduce the selected tests in a separately approved audit environment,
review the harness, restore its original basename under /tmp, and run from
the matching checkout using its .venv interpreter with
PYTHONDONTWRITEBYTECODE=1. lfa_run_safe_tests.py, lfa_additional_tests.py and
lfa_evidence_probes.py load the safety prefix of lfa_runtime_inventory.py from
/tmp. That prefix disables dotenv/settings-file loading, points DB/Redis to
unused loopback port1 and blocks psycopg2/socket connection calls. Logging is
disabled. Tests use fake persistence or are skipped. Do not remove these guards
merely to get a green run. A dedicated disposable PostgreSQL environment is
needed for meaningful integration/concurrency/migration tests.

Executed selected-test commands:

- PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /tmp/lfa_run_safe_tests.py
- PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /tmp/lfa_additional_tests.py
- PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /tmp/lfa_evidence_probes.py
- PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /tmp/lfa_runtime_inventory.py

Final core:630 pass/17 skipped. Additional:138 pass. Per-case JSON records
the final runs. Earlier blocked DB attempts are explained in report M.
The31 content files were validated directly with existing
app.services.al_import_service._validate_file(data,"LFA_FOOTBALL_PLAYER"),
under the same no-network bootstrap; no DB import was called.

## Security/build evidence

Scanner tools were isolated under /tmp/lfa-audit-tools, not installed into the
application .venv or added to requirements. pip-audit2.10.1 used exact direct
pins with --no-deps/--disable-pip and separately the installed environment.
These are different scans; installed extras are not assumed deployed.
Bandit1.9.4 scanned app excluding app/tests; npm audit used the Cypress lock
without npm install or fix. Sanitized JSON retains advisory IDs and scanner
results, not source snippets or credentials.

Native build commands and compiler errors are in LFA_NATIVE_BUILD_EVIDENCE_2026.json.
Both used the same checkout and Xcode27.0 beta with code signing disabled.
Raw log hashes preserve identity; compact errors avoid shipping large logs.
The first sandbox-denied macro build was not treated as a source failure.
No native tests, devices, deployments or signing were run.

Secrets scan boundaries/exclusions are in LFA_SECRETS_SCAN_REDACTED_2026.json.
Zero regex hits is not a complete secret/history/data-provenance certification.
No DB dumps were restored and no production account/data was accessed.

## Inventory interpretation

API test/consumer candidates are text matches, not coverage certification.
Actual return expressions are source extraction, not HTTP recordings.
Native view ownership does not imply implemented navigation or functional PASS.
Template candidate non-use does not authorize deletion.
Module recommendation counts are units, not LOC or project-completion percentages.
The final manifest hashes the delivered audit artifacts; it excludes itself.
