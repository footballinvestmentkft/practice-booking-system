#!/usr/bin/env python3
"""
Deterministic xcodebuild test wrapper for GHA macOS runners.

Starts xcodebuild as a child process in its own process group,
monitors for test completion via raw log markers, validates the
xcresult bundle, and handles the known post-test hang where
xcodebuild stays alive after tests finish.

Exit 0 only when:
  - xcresult exists, is readable, tests > 0, failures == 0
  - xcodebuild exited naturally OR was killed due to proven post-test hang

Exit 1 for:
  - real test failures
  - pre-test timeout (tests never started or never finished)
  - incomplete or missing xcresult
"""
import json
import os
import re
import signal
import subprocess
import sys
import time

LOG_PATH = "/tmp/xcodebuild-test.log"
XCRESULT_PATH = "/tmp/TestResults.xcresult"
JUNIT_PATH = "/tmp/test-results.xml"
POLL_INTERVAL = 5
GRACE_SECONDS = 30
MAX_WAIT = 600


def main():
    if len(sys.argv) < 2:
        print("Usage: ios_test_runner.py <simulator_udid>")
        sys.exit(1)

    sim_udid = sys.argv[1]

    if os.path.exists(XCRESULT_PATH):
        subprocess.run(["rm", "-rf", XCRESULT_PATH], check=False)

    log_fd = open(LOG_PATH, "w")
    proc = subprocess.Popen(
        [
            "xcodebuild", "test",
            "-project", "ios/LFAEducationCenter.xcodeproj",
            "-scheme", "LFAEducationCenter",
            "-configuration", "Debug",
            "-destination", f"platform=iOS Simulator,id={sim_udid}",
            "-derivedDataPath", "/tmp/DerivedData",
            "-resultBundlePath", XCRESULT_PATH,
            "CODE_SIGNING_ALLOWED=NO",
            "COMPILER_INDEX_STORE_ENABLE=NO",
        ],
        stdout=log_fd,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    pgid = os.getpgid(proc.pid)
    print(f"xcodebuild PID={proc.pid} PGID={pgid}")

    marker = poll_for_completion(proc)
    exit_code = reap(proc, pgid, marker)

    print(f"xcodebuild exit code: {exit_code}")

    log_fd.close()
    generate_junit()
    cleanup_simulator(sim_udid)

    verdict = validate_xcresult()
    write_summary(exit_code, marker, verdict)
    decide(exit_code, marker, verdict)


def poll_for_completion(proc):
    waited = 0
    marker_time = None
    marker_type = None

    while proc.poll() is None:
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL

        if marker_type is None:
            marker_type = check_log_marker()
            if marker_type:
                marker_time = time.time()
                print(f"Test marker: ** TEST {marker_type} ** (at {waited}s)")

        if marker_type and marker_time:
            elapsed = time.time() - marker_time
            if elapsed >= GRACE_SECONDS:
                print(f"Post-test hang: xcodebuild alive {GRACE_SECONDS}s after completion")
                log_diagnostics(proc.pid, pgid=os.getpgid(proc.pid))
                terminate_group(os.getpgid(proc.pid))
                return marker_type

        if waited >= MAX_WAIT and marker_type is None:
            print(f"No test marker after {MAX_WAIT}s — tests did not complete")
            log_diagnostics(proc.pid, pgid=os.getpgid(proc.pid))
            terminate_group(os.getpgid(proc.pid))
            return None

    return marker_type or check_log_marker()


def check_log_marker():
    if not os.path.exists(LOG_PATH):
        return None
    try:
        with open(LOG_PATH, "r", errors="replace") as f:
            content = f.read()
        if "** TEST SUCCEEDED **" in content:
            return "SUCCEEDED"
        if "** TEST FAILED **" in content:
            return "FAILED"
    except OSError:
        pass
    return None


def reap(proc, pgid, marker):
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        print("xcodebuild did not exit after kill — force kill")
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
    return proc.returncode or 0


def terminate_group(pgid):
    print(f"SIGTERM → PGID {pgid}")
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(5)
    try:
        os.killpg(pgid, signal.SIGKILL)
        print(f"SIGKILL → PGID {pgid}")
    except ProcessLookupError:
        pass


def log_diagnostics(pid, pgid):
    print("=== DIAGNOSTIC: Process group ===")
    subprocess.run(
        ["ps", "-o", "pid,ppid,pgid,stat,command", "-g", str(pgid)],
        timeout=10, check=False,
    )
    print("=== DIAGNOSTIC: Child processes ===")
    subprocess.run(
        f"pgrep -P {pid} 2>/dev/null | xargs -I{{}} ps -o pid,command -p {{}} 2>/dev/null",
        shell=True, timeout=10, check=False,
    )
    print("=== DIAGNOSTIC: Open FDs (xcodebuild) ===")
    subprocess.run(["lsof", "-p", str(pid)], timeout=10, check=False)


def validate_xcresult():
    if not os.path.isdir(XCRESULT_PATH):
        print(f"xcresult not found: {XCRESULT_PATH}")
        return None

    result = try_xcresulttool()
    if result:
        return result

    result = try_junit_xml()
    if result:
        print("xcresulttool unavailable — JUnit XML fallback")
        return result

    print("Cannot parse test results from any source")
    return None


def try_xcresulttool():
    for cmd in [
        ["xcrun", "xcresulttool", "get", "test-results", "summary", "--path", XCRESULT_PATH],
        ["xcrun", "xcresulttool", "get", "test-results", "tests", "--path", XCRESULT_PATH],
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode != 0 or not r.stdout.strip():
                continue
            print(f"xcresulttool raw ({cmd[-2]}): {r.stdout[:300]}")
            try:
                data = json.loads(r.stdout)
                total = extract_int(data, "totalTests", "testCount", "testsCount")
                failures = extract_int(data, "failedTests", "failureCount", "failuresCount")
                if total is not None and total > 0:
                    return (total, failures or 0)
            except (json.JSONDecodeError, TypeError):
                pass
            for key_t in [r'"totalTests"\s*:\s*(\d+)', r'"testCount"\s*:\s*(\d+)']:
                m = re.search(key_t, r.stdout)
                if m:
                    total = int(m.group(1))
                    fm = re.search(r'"(?:failedTests|failureCount)"\s*:\s*(\d+)', r.stdout)
                    failures = int(fm.group(1)) if fm else 0
                    if total > 0:
                        return (total, failures)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return None


def extract_int(data, *keys):
    for k in keys:
        v = data.get(k)
        if isinstance(v, int):
            return v
        if isinstance(v, dict) and "_value" in v:
            return int(v["_value"])
    return None


def try_junit_xml():
    if not os.path.exists(JUNIT_PATH):
        return None
    try:
        with open(JUNIT_PATH, "r") as f:
            content = f.read()
        tm = re.search(r'tests="(\d+)"', content)
        fm = re.search(r'failures="(\d+)"', content)
        if tm:
            total = int(tm.group(1))
            failures = int(fm.group(1)) if fm else 0
            if total > 0:
                return (total, failures)
    except OSError:
        pass
    return None


def generate_junit():
    if os.path.exists(LOG_PATH):
        subprocess.run(
            f"cat {LOG_PATH} | xcpretty --report junit --output {JUNIT_PATH}",
            shell=True, capture_output=True, timeout=60, check=False,
        )


def cleanup_simulator(sim_udid):
    print(f"Cleanup simulator {sim_udid}")
    subprocess.run(["xcrun", "simctl", "shutdown", sim_udid],
                   capture_output=True, timeout=30, check=False)
    subprocess.run(["xcrun", "simctl", "delete", sim_udid],
                   capture_output=True, timeout=30, check=False)


def write_summary(exit_code, marker, verdict):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null")
    total, failures = verdict if verdict else ("?", "?")
    with open(summary_path, "a") as f:
        f.write("### iOS Test Results\n")
        f.write(f"- Total tests: {total}\n")
        f.write(f"- Failures: {failures}\n")
        f.write(f"- xcodebuild exit: {exit_code}\n")
        f.write(f"- Test marker: {marker or 'none'}\n")
        f.write(f"- Post-test hang: {'yes' if marker == 'SUCCEEDED' and exit_code != 0 else 'no'}\n")


def decide(exit_code, marker, verdict):
    if verdict is None:
        print("FAIL — cannot validate test results")
        sys.exit(1)

    total, failures = verdict

    if failures > 0:
        print(f"FAIL — {total} tests, {failures} failures")
        sys.exit(1)

    if total == 0:
        print("FAIL — no tests found")
        sys.exit(1)

    if exit_code == 0:
        print(f"PASS — {total} tests, xcodebuild exited cleanly")
        sys.exit(0)

    if marker == "SUCCEEDED":
        print(f"PASS — {total} tests, xcodebuild killed after proven post-test hang")
        sys.exit(0)

    print(f"FAIL — xcodebuild exit {exit_code}, marker={marker}")
    sys.exit(1)


if __name__ == "__main__":
    main()
