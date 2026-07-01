#!/usr/bin/env python3
"""MC1 regression CLI (MC1-AUTO-2) — invoked by run_mc1_regression.sh.

Logs in, runs one or more scenarios from scenarios.SCENARIOS against the
backend + two physical devices, and writes a structured report plus backend
state dumps and extracted debug-snapshot/console-log evidence into --out-dir
(which the caller has already created, with console capture already running
into <out-dir>/console/*.log).

PASS/FAIL is decided per scenario.step() calls inside scenarios.py, all of
which assert on backend responses — never on-device UI or console text.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# Allow `python3 runner.py` direct execution without package installation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc1_regression.lib import (  # noqa: E402
    ArtifactRun,
    ConsoleOffsetTracker,
    ScenarioContext,
    ValidationError,
    login,
    preflight_url_scheme,
    send_deep_link,
    utc_now_iso,
)
from mc1_regression.scenarios import SCENARIOS  # noqa: E402

_DEFAULT_INSTRUCTOR_EMAIL = "staging-instructor@lfa-staging.io"
_DEFAULT_PLAYER_EMAIL = "staging-player1@lfa-staging.io"


def _prompt_credentials() -> tuple[str, str, str, str]:
    # When invoked via run_mc1_regression.sh the shell already collected
    # credentials interactively and passes them through private env vars.
    # Fall back to interactive prompting when called standalone.
    instructor_email = os.environ.get("_INST_EMAIL") or _DEFAULT_INSTRUCTOR_EMAIL
    instructor_password = os.environ.get("_INST_PASS") or getpass.getpass("Instructor password: ")
    player_email = os.environ.get("_PL_EMAIL") or _DEFAULT_PLAYER_EMAIL
    player_password = os.environ.get("_PL_PASS") or getpass.getpass("Player password: ")
    # Clear from env immediately — tokens are what we keep.
    for key in ("_INST_EMAIL", "_INST_PASS", "_PL_EMAIL", "_PL_PASS"):
        os.environ.pop(key, None)
    return instructor_email, instructor_password, player_email, player_password


def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    artifact = ArtifactRun(out_dir)
    offsets = ConsoleOffsetTracker(artifact)

    instructor_email, instructor_password, player_email, player_password = _prompt_credentials()

    print(f"=== MC1 regression run: {args.scenario} ===")
    print(f"api_base={args.api_base} out_dir={out_dir}")

    instructor_token = login(args.api_base, instructor_email, instructor_password)
    player_token = login(args.api_base, player_email, player_password)
    del instructor_password, player_password
    print("Logged in as instructor + player.")

    # Device preflight: fail fast if lfa-mc1:// scheme is not registered.
    # This happens when the installed build predates PR #353 (main 4c8d3632).
    preflight_url_scheme(args.ipad_udid, "iPad")
    preflight_url_scheme(args.iphone_udid, "iPhone")

    if args.scenario == "all":
        scenario_names = [k for k in SCENARIOS.keys() if not k.startswith("gopro-")]
    else:
        scenario_names = [args.scenario]
    overall_pass = True
    summary: list[dict] = []

    for name in scenario_names:
        fn = SCENARIOS.get(name)
        if fn is None:
            print(f"Unknown scenario: {name}")
            overall_pass = False
            summary.append({"name": name, "passed": False, "error": "unknown scenario"})
            continue

        print(f"\n--- Scenario: {name} ---")

        # Reset both ViewModels to .idle before every scenario so that
        # vm.joinSession() (which guards on .idle) works even when the previous
        # scenario left the ViewModel in .error or .inLobby state.
        print("[reset] Sending reset-session to both devices...")
        try:
            send_deep_link(args.ipad_udid, "reset-session")
            send_deep_link(args.iphone_udid, "reset-session")
        except ValidationError as e:
            print(f"[reset] warning: {e}")
        import time as _t; _t.sleep(4)

        offsets.mark_scenario_start()
        ctx = ScenarioContext(
            api_base=args.api_base,
            ipad_udid=args.ipad_udid,
            iphone_udid=args.iphone_udid,
            instructor_token=instructor_token,
            player_token=player_token,
            artifact=artifact,
            offsets=offsets,
            cycles=args.cycles,
        )

        try:
            report = fn(ctx)
        except NotImplementedError as e:
            print(f"  SKIPPED (not implemented): {e}")
            summary.append({"name": name, "passed": None, "skipped": True, "reason": str(e)})
            continue
        except ValidationError as e:
            print(f"  FATAL: {e}")
            report_dict = {"name": name, "passed": False, "error": str(e)}
            artifact.write_json(f"backend_state/{name}_report.json", report_dict)
            summary.append(report_dict)
            overall_pass = False
            continue

        if report.session_uuid:
            try:
                artifact.dump_backend_state(name, args.api_base, instructor_token, report.session_uuid)
            except ValidationError as e:
                print(f"  warning: could not dump backend state: {e}")

        offsets.extract_snapshots(name)
        cco_lines = offsets.extract_tagged_lines(name, "[CCO]")
        pco_lines = offsets.extract_tagged_lines(name, "[PCO]")
        artifact.write_text(f"console/{name}_cco_lines.txt", "\n".join(cco_lines))
        artifact.write_text(f"console/{name}_pco_lines.txt", "\n".join(pco_lines))

        report_dict = {
            "name": report.name,
            "passed": report.passed,
            "session_uuid": report.session_uuid,
            "steps": report.steps,
            "error": report.error,
        }
        artifact.write_json(f"backend_state/{name}_report.json", report_dict)
        summary.append(report_dict)
        overall_pass = overall_pass and report.passed

        status = "PASS" if report.passed else "FAIL"
        print(f"  {name}: {status}")

    # A skipped static preflight (SKIP_STATIC_PREFLIGHT=1, set by run_mc1_regression.sh)
    # means known-regression classes were never re-checked before this physical run.
    # Such a run can NEVER be read back as a valid PASS, no matter what every scenario
    # reported — force overall_pass=False and stamp the report unambiguously.
    static_preflight_skipped = os.environ.get("MC1_STATIC_PREFLIGHT_SKIPPED") == "1"
    static_preflight_skip_reason = os.environ.get("MC1_STATIC_PREFLIGHT_SKIP_REASON")
    if static_preflight_skipped:
        overall_pass = False

    final = {
        "timestamp": utc_now_iso(),
        "scenario_arg": args.scenario,
        "overall_pass": overall_pass,
        "static_preflight_skipped": static_preflight_skipped,
        "static_preflight_skip_reason": static_preflight_skip_reason,
        "scenarios": summary,
    }
    artifact.write_json("report.json", final)

    lines = [f"=== MC1 regression report ({utc_now_iso()}) ===", f"scenario(s): {args.scenario}", ""]
    if static_preflight_skipped:
        lines.append("!!! STATIC PREFLIGHT WAS SKIPPED (SKIP_STATIC_PREFLIGHT=1) !!!")
        lines.append(f"!!! reason: {static_preflight_skip_reason} !!!")
        lines.append("!!! THIS RUN IS NOT A VALID PASS, REGARDLESS OF SCENARIO RESULTS BELOW !!!")
        lines.append("")
    for s in summary:
        if s.get("skipped"):
            lines.append(f"{s['name']}: SKIPPED — {s.get('reason')}")
            continue
        status = "PASS" if s.get("passed") else "FAIL"
        lines.append(f"{s['name']}: {status}" + (f" — {s['error']}" if s.get("error") else ""))
        for step in s.get("steps", []):
            mark = "OK" if step["ok"] else "FAIL"
            lines.append(f"  [{mark}] {step['description']}")
    lines.append("")
    lines.append(f"OVERALL: {'PASS' if overall_pass else 'FAIL'}"
                  + (" (static preflight skipped — cannot be a valid PASS)" if static_preflight_skipped else ""))
    report_text = "\n".join(lines)
    artifact.write_text("report.txt", report_text)
    print("\n" + report_text)
    print(f"\nArtifacts: {out_dir}")

    return 0 if overall_pass else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenario", required=True, choices=[*SCENARIOS.keys(), "all"])
    p.add_argument("--cycles", type=int, default=3, help="cycle count for the multicycle scenario")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--api-base", required=True)
    p.add_argument("--ipad-udid", required=True)
    p.add_argument("--iphone-udid", required=True)
    return p.parse_args()


if __name__ == "__main__":
    try:
        sys.exit(run(parse_args()))
    except ValidationError as e:
        print(f"\nFATAL: {e}")
        sys.exit(2)
