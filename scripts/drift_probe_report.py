#!/usr/bin/env python3
"""
Automatic Drift Probe Report — downloads drift JSON from two physical iOS
devices (iPad + iPhone) via USB, pairs by session UUID + cycle index, computes
pairwise drift, and outputs a formatted report.

Usage:
    python3 scripts/drift_probe_report.py \
        --session-uuid <uuid> \
        --ipad-udid <udid> \
        --iphone-udid <udid> \
        [--cycle <N>]           # report single cycle only
        [--output-dir <path>]   # default: /tmp/drift_probe_<session_uuid>
        [--aggregate]           # produce 10-cycle aggregate stats

Requires: macOS with Xcode 16+ (xcrun devicectl), two physical iOS devices
connected via USB.

Dependencies: Python stdlib only (no pip packages).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_BUNDLE_ID = "com.lovas-zoltan.lfa-education-center"
DEVICE_CONTAINER_PATH = "Library/Application Support/multicamera_captures"
DRIFT_THRESHOLD_PASS_MS = 100.0
DRIFT_THRESHOLD_WARN_MS = 500.0


# ---------------------------------------------------------------------------
# ISO 8601 parsing
# ---------------------------------------------------------------------------
def parse_iso(s: str) -> datetime:
    """Parse ISO 8601 timestamp with fractional seconds.

    Python < 3.11 does not handle trailing 'Z'; replace with +00:00.
    """
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# P95 calculation
# ---------------------------------------------------------------------------
def percentile_95(values: List[float]) -> float:
    """Compute P95 from a sorted list of values.

    P95 index = ceil(0.95 * N) - 1  (0-based).
    For N=10: index = ceil(9.5) - 1 = 10 - 1 = 9 -> last element.
    For N=1:  index = ceil(0.95) - 1 = 0 -> the single element.
    """
    if not values:
        raise ValueError("Cannot compute P95 of empty list")
    sorted_vals = sorted(values)
    idx = math.ceil(0.95 * len(sorted_vals)) - 1
    return sorted_vals[idx]


# ---------------------------------------------------------------------------
# xcrun devicectl helpers
# ---------------------------------------------------------------------------
def _run_devicectl(args: List[str], description: str) -> str:
    """Run an xcrun devicectl command and return stdout.

    Raises SystemExit on failure with a clear message.
    """
    cmd = ["xcrun", "devicectl"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        print(
            f"ERROR: 'xcrun' not found. Ensure Xcode Command Line Tools are "
            f"installed.",
            file=sys.stderr,
        )
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(
            f"ERROR: {description} timed out after 60s. Is the device "
            f"connected and unlocked?",
            file=sys.stderr,
        )
        sys.exit(1)

    if result.returncode != 0:
        print(
            f"ERROR: {description} failed (exit code {result.returncode}).\n"
            f"  Command: {' '.join(cmd)}\n"
            f"  Stderr:  {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)

    return result.stdout


def list_device_files(udid: str) -> str:
    """List files in the app's data container on a device."""
    return _run_devicectl(
        [
            "device", "info", "files",
            "--device", udid,
            "--domain-type", "appDataContainer",
            "--domain-identifier", APP_BUNDLE_ID,
        ],
        f"Listing files on device {udid}",
    )


def copy_file_from_device(
    udid: str, remote_filename: str, local_dest: str
) -> None:
    """Download a single file from the device's app container."""
    remote_path = f"{DEVICE_CONTAINER_PATH}/{remote_filename}"
    _run_devicectl(
        [
            "device", "copy", "from",
            "--device", udid,
            "--domain-type", "appDataContainer",
            "--domain-identifier", APP_BUNDLE_ID,
            "--source", remote_path,
            "--destination", local_dest,
        ],
        f"Downloading {remote_filename} from device {udid}",
    )


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
def find_drift_files_on_device(
    udid: str, session_uuid: str, device_label: str
) -> List[str]:
    """Discover drift JSON filenames matching the session UUID on a device.

    Returns a list of matching filenames (basename only).
    """
    listing_output = list_device_files(udid)

    # The expected filename pattern:
    #   drift_session_<uuid>_device_<N>.json
    prefix = f"drift_session_{session_uuid}_device_"
    matches = []
    for line in listing_output.splitlines():
        line = line.strip()
        # The file listing may include paths or just filenames; extract the
        # basename that matches our pattern.
        for token in line.split():
            basename = token.split("/")[-1]
            if basename.startswith(prefix) and basename.endswith(".json"):
                if basename not in matches:
                    matches.append(basename)

    if not matches:
        print(
            f"ERROR: No drift JSON files found for session {session_uuid} on "
            f"{device_label} device ({udid}).\n"
            f"  Expected pattern: {prefix}*.json\n"
            f"  in: {DEVICE_CONTAINER_PATH}/\n"
            f"  File listing output:\n{listing_output[:2000]}",
            file=sys.stderr,
        )
        sys.exit(1)

    return matches


# ---------------------------------------------------------------------------
# JSON loading and validation
# ---------------------------------------------------------------------------
def load_drift_records(path: str, source_label: str) -> List[dict]:
    """Load and validate drift JSON records from a local file.

    The file must contain a JSON array of CaptureDriftRecord objects.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(
            f"ERROR: Corrupt or non-JSON file ({source_label}): {path}\n"
            f"  {e}",
            file=sys.stderr,
        )
        sys.exit(1)
    except OSError as e:
        print(
            f"ERROR: Cannot read file ({source_label}): {path}\n  {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not isinstance(data, list):
        # Maybe the file is a single record (not wrapped in an array)?
        if isinstance(data, dict):
            data = [data]
        else:
            print(
                f"ERROR: Expected JSON array in {path} ({source_label}), "
                f"got {type(data).__name__}.",
                file=sys.stderr,
            )
            sys.exit(1)

    return data


def validate_record(rec: dict, expected_session_uuid: str, source_label: str) -> None:
    """Validate a single drift record.  Exits on failure."""
    # Required field
    if "did_start_recording_at" not in rec or rec["did_start_recording_at"] is None:
        print(
            f"ERROR: Record missing 'did_start_recording_at' ({source_label}, "
            f"cycle={rec.get('cycle_index', '?')}).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Session UUID must match
    if rec.get("session_uuid") != expected_session_uuid:
        print(
            f"ERROR: Session UUID mismatch in {source_label}. "
            f"Expected '{expected_session_uuid}', got '{rec.get('session_uuid')}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    # success must be true
    if not rec.get("success", False):
        reason = rec.get("failure_reason", "unknown")
        print(
            f"ERROR: Record has success=false ({source_label}, "
            f"cycle={rec.get('cycle_index', '?')}, reason={reason}).",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Device record grouping
# ---------------------------------------------------------------------------
def group_records_by_device(
    all_records: List[dict], source_label: str
) -> Tuple[List[dict], List[dict]]:
    """Split records into iPad and iPhone groups.

    Exits if the split is not exactly one device type each.
    """
    ipad_records: List[dict] = []
    iphone_records: List[dict] = []
    other_records: List[dict] = []

    for rec in all_records:
        dt = rec.get("device_type", "").lower()
        if dt == "ipad":
            ipad_records.append(rec)
        elif dt == "iphone":
            iphone_records.append(rec)
        else:
            other_records.append(rec)

    if not ipad_records:
        print(
            f'ERROR: No records with device_type="ipad" found in {source_label}.',
            file=sys.stderr,
        )
        sys.exit(1)

    if not iphone_records:
        print(
            f'ERROR: No records with device_type="iphone" found in {source_label}.',
            file=sys.stderr,
        )
        sys.exit(1)

    if other_records:
        types = {r.get("device_type") for r in other_records}
        print(
            f"WARNING: Ignoring {len(other_records)} record(s) with "
            f"unexpected device_type(s): {types}",
            file=sys.stderr,
        )

    return ipad_records, iphone_records


def check_duplicate_records(records: List[dict], device_label: str) -> None:
    """Exit if any (session_uuid, cycle_index) pair appears more than once."""
    seen: set = set()
    for rec in records:
        key = (rec.get("session_uuid"), rec.get("cycle_index"))
        if key in seen:
            print(
                f"ERROR: Duplicate record for {device_label}: "
                f"session={key[0]}, cycle={key[1]}.",
                file=sys.stderr,
            )
            sys.exit(1)
        seen.add(key)


# ---------------------------------------------------------------------------
# Drift computation
# ---------------------------------------------------------------------------
def compute_drift_ms(ipad_rec: dict, iphone_rec: dict) -> float:
    """Compute pairwise drift in ms from did_start_recording_at timestamps."""
    t_ipad = parse_iso(ipad_rec["did_start_recording_at"])
    t_iphone = parse_iso(iphone_rec["did_start_recording_at"])
    return abs((t_ipad - t_iphone).total_seconds()) * 1000.0


def drift_verdict(drift_ms: float) -> str:
    """Return PASS / WARN / FAIL based on drift thresholds."""
    if drift_ms < DRIFT_THRESHOLD_PASS_MS:
        return f"PASS (drift < {DRIFT_THRESHOLD_PASS_MS:.0f}ms threshold)"
    elif drift_ms < DRIFT_THRESHOLD_WARN_MS:
        return (
            f"WARN (drift {drift_ms:.1f}ms is between "
            f"{DRIFT_THRESHOLD_PASS_MS:.0f}ms and {DRIFT_THRESHOLD_WARN_MS:.0f}ms)"
        )
    else:
        return f"FAIL (drift {drift_ms:.1f}ms exceeds {DRIFT_THRESHOLD_WARN_MS:.0f}ms threshold)"


def clock_sync_status(ipad_rec: dict, iphone_rec: dict) -> str:
    """Determine clock synchronization status from clock_quality fields."""
    ipad_cq = ipad_rec.get("clock_quality", "unknown")
    iphone_cq = iphone_rec.get("clock_quality", "unknown")

    if ipad_cq == "synchronized" and iphone_cq == "synchronized":
        return "VALIDATED (both devices synchronized)"
    elif ipad_cq == "synchronized" or iphone_cq == "synchronized":
        degraded = "iPad" if ipad_cq != "synchronized" else "iPhone"
        return f"PARTIAL ({degraded} is {ipad_cq if degraded == 'iPad' else iphone_cq})"
    else:
        return f"NOT VALIDATED (both devices {ipad_cq if ipad_cq == iphone_cq else f'iPad={ipad_cq}, iPhone={iphone_cq}'})"


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
SEPARATOR = "=" * 80


def format_cycle_report(
    session_uuid: str,
    cycle_index: int,
    ipad_rec: dict,
    iphone_rec: dict,
) -> str:
    """Format a single-cycle drift report block."""
    drift_ms = compute_drift_ms(ipad_rec, iphone_rec)
    verdict = drift_verdict(drift_ms)
    sync_status = clock_sync_status(ipad_rec, iphone_rec)

    lines = [
        SEPARATOR,
        f"Session UUID: {session_uuid}",
        f"Cycle: {cycle_index}",
        SEPARATOR,
        "",
        f"iPad (device_id={ipad_rec.get('device_id', '?')}):",
        f"  record found:           PASS",
        f"  didStartRecording:      {ipad_rec['did_start_recording_at']}",
        f"  clock_quality:          {ipad_rec.get('clock_quality', 'unknown')}",
        f"  server_offset_estimate: {ipad_rec.get('server_offset_estimate_ms', 0):.3f} ms",
        f"  server_offset_ms:       {ipad_rec.get('server_offset_ms', 0):.3f} ms",
        f"  callback_delay_ms:      {ipad_rec.get('callback_delay_ms', 0):.3f} ms",
        "",
        f"iPhone (device_id={iphone_rec.get('device_id', '?')}):",
        f"  record found:           PASS",
        f"  didStartRecording:      {iphone_rec['did_start_recording_at']}",
        f"  clock_quality:          {iphone_rec.get('clock_quality', 'unknown')}",
        f"  server_offset_estimate: {iphone_rec.get('server_offset_estimate_ms', 0):.3f} ms",
        f"  server_offset_ms:       {iphone_rec.get('server_offset_ms', 0):.3f} ms",
        f"  callback_delay_ms:      {iphone_rec.get('callback_delay_ms', 0):.3f} ms",
        "",
        f"Pairwise drift:           {drift_ms:.3f} ms",
        f"Clock sync status:        {sync_status}",
        f"Verdict:                  {verdict}",
        SEPARATOR,
    ]
    return "\n".join(lines)


def format_aggregate_report(
    session_uuid: str,
    ipad_records: List[dict],
    iphone_records: List[dict],
    paired_cycles: List[int],
) -> str:
    """Format the multi-cycle aggregate statistics report."""
    # Build lookup dicts by cycle_index
    ipad_by_cycle = {r["cycle_index"]: r for r in ipad_records}
    iphone_by_cycle = {r["cycle_index"]: r for r in iphone_records}

    drift_values: List[float] = []
    ipad_offset_values: List[float] = []
    iphone_offset_values: List[float] = []
    clock_quality_counts: Dict[str, int] = {}
    successful = 0
    failed = 0

    for ci in sorted(paired_cycles):
        ipad_r = ipad_by_cycle[ci]
        iphone_r = iphone_by_cycle[ci]

        if not ipad_r.get("success", False) or not iphone_r.get("success", False):
            failed += 1
            continue

        successful += 1
        drift_values.append(compute_drift_ms(ipad_r, iphone_r))
        ipad_offset_values.append(ipad_r.get("server_offset_ms", 0.0))
        iphone_offset_values.append(iphone_r.get("server_offset_ms", 0.0))

        for rec in (ipad_r, iphone_r):
            cq = rec.get("clock_quality", "unknown")
            clock_quality_counts[cq] = clock_quality_counts.get(cq, 0) + 1

    total = successful + failed

    def stat_block(label: str, values: List[float]) -> List[str]:
        if not values:
            return [f"\n{label}:", "  (no data)"]
        return [
            f"\n{label}:",
            f"  avg:    {statistics.mean(values):.1f}",
            f"  median: {statistics.median(values):.1f}",
            f"  min:    {min(values):.1f}",
            f"  max:    {max(values):.1f}",
            f"  stddev: {statistics.stdev(values):.1f}" if len(values) > 1 else f"  stddev: 0.0",
            f"  P95:    {percentile_95(values):.1f}",
        ]

    # Determine overall server offset verdict
    all_synced = all(
        rec.get("clock_quality") == "synchronized"
        for ci in paired_cycles
        for rec in (ipad_by_cycle.get(ci, {}), iphone_by_cycle.get(ci, {}))
        if rec.get("success", False)
    )
    offset_verdict = "VALIDATED" if all_synced else "NOT VALIDATED"

    lines = [
        SEPARATOR,
        "10-CYCLE AGGREGATE REPORT",
        f"Session UUID: {session_uuid}",
        SEPARATOR,
        f"Cycles: {total} total, {successful} successful, {failed} failed",
    ]

    lines.extend(stat_block("Pairwise Drift (ms)", drift_values))
    lines.extend(stat_block("iPad Server Offset (ms)", ipad_offset_values))
    lines.extend(stat_block("iPhone Server Offset (ms)", iphone_offset_values))

    lines.append("\nClock Quality Distribution:")
    # Show known quality levels first, then any extras
    known_qualities = [
        "synchronized",
        "degradedMissingServerDate",
        "degradedHighRTT",
    ]
    shown = set()
    for cq in known_qualities:
        count = clock_quality_counts.get(cq, 0)
        lines.append(f"  {cq + ':':35s}{count}")
        shown.add(cq)
    for cq, count in sorted(clock_quality_counts.items()):
        if cq not in shown:
            lines.append(f"  {cq + ':':35s}{count}")

    lines.append(f"\nServer Offset Verdict: {offset_verdict}")
    lines.append(SEPARATOR)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Safe file output (never overwrite)
# ---------------------------------------------------------------------------
def safe_output_path(directory: str, filename: str) -> str:
    """Return a path in directory/filename, appending a timestamp suffix if
    the file already exists. Never overwrites an existing file."""
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate
    # Append timestamp
    stem, ext = os.path.splitext(filename)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return os.path.join(directory, f"{stem}_{ts}{ext}")


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------
def download_drift_files(
    session_uuid: str,
    ipad_udid: str,
    iphone_udid: str,
    output_dir: str,
) -> Tuple[List[str], List[str]]:
    """Discover and download drift JSON files from both devices.

    Returns (ipad_local_paths, iphone_local_paths).
    """
    os.makedirs(output_dir, exist_ok=True)

    ipad_local_paths: List[str] = []
    iphone_local_paths: List[str] = []

    for udid, label, paths_list in [
        (ipad_udid, "iPad", ipad_local_paths),
        (iphone_udid, "iPhone", iphone_local_paths),
    ]:
        print(f"[*] Discovering drift files on {label} ({udid})...")
        filenames = find_drift_files_on_device(udid, session_uuid, label)
        print(f"    Found {len(filenames)} file(s): {filenames}")

        for fname in filenames:
            local_path = safe_output_path(output_dir, f"{label.lower()}_{fname}")
            print(f"    Downloading {fname} -> {local_path}")
            copy_file_from_device(udid, fname, local_path)
            paths_list.append(local_path)

    return ipad_local_paths, iphone_local_paths


def load_all_records(
    local_paths: List[str],
    session_uuid: str,
    device_label: str,
) -> List[dict]:
    """Load and validate all records from downloaded files for one device."""
    all_records: List[dict] = []
    for path in local_paths:
        records = load_drift_records(path, device_label)
        for rec in records:
            validate_record(rec, session_uuid, f"{device_label} ({path})")
        all_records.extend(records)
    return all_records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automatic Drift Probe Report — download, pair, and "
        "analyze drift measurements from two iOS devices.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--session-uuid", required=True, help="Session UUID to analyze"
    )
    parser.add_argument(
        "--ipad-udid", required=True, help="UDID of the iPad device"
    )
    parser.add_argument(
        "--iphone-udid", required=True, help="UDID of the iPhone device"
    )
    parser.add_argument(
        "--cycle",
        type=int,
        default=None,
        help="Report a single cycle only (1-based index)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for downloaded files (default: /tmp/drift_probe_<uuid>)",
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Produce multi-cycle aggregate statistics",
    )

    args = parser.parse_args()

    session_uuid: str = args.session_uuid
    output_dir: str = args.output_dir or f"/tmp/drift_probe_{session_uuid}"

    # -----------------------------------------------------------------------
    # Step 1: Download drift files from devices
    # -----------------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print(f"  Drift Probe Report")
    print(f"  Session: {session_uuid}")
    print(f"  Output:  {output_dir}")
    print(f"{'=' * 60}\n")

    ipad_paths, iphone_paths = download_drift_files(
        session_uuid, args.ipad_udid, args.iphone_udid, output_dir
    )

    # -----------------------------------------------------------------------
    # Step 2: Load and validate records
    # -----------------------------------------------------------------------
    print("\n[*] Loading records...")
    ipad_all = load_all_records(ipad_paths, session_uuid, "iPad")
    iphone_all = load_all_records(iphone_paths, session_uuid, "iPhone")

    print(f"    iPad records:   {len(ipad_all)}")
    print(f"    iPhone records: {len(iphone_all)}")

    # Check for duplicates within each device
    check_duplicate_records(ipad_all, "iPad")
    check_duplicate_records(iphone_all, "iPhone")

    # Verify device types
    ipad_types = {r.get("device_type", "").lower() for r in ipad_all}
    iphone_types = {r.get("device_type", "").lower() for r in iphone_all}

    if ipad_types and not ipad_types <= {"ipad"}:
        print(
            f"WARNING: iPad device file contains non-iPad device_types: "
            f"{ipad_types}",
            file=sys.stderr,
        )

    if iphone_types and not iphone_types <= {"iphone"}:
        print(
            f"WARNING: iPhone device file contains non-iPhone device_types: "
            f"{iphone_types}",
            file=sys.stderr,
        )

    # -----------------------------------------------------------------------
    # Step 3: Pair by cycle_index
    # -----------------------------------------------------------------------
    ipad_by_cycle = {r["cycle_index"]: r for r in ipad_all}
    iphone_by_cycle = {r["cycle_index"]: r for r in iphone_all}

    common_cycles = sorted(set(ipad_by_cycle.keys()) & set(iphone_by_cycle.keys()))

    if not common_cycles:
        ipad_cycles = sorted(ipad_by_cycle.keys())
        iphone_cycles = sorted(iphone_by_cycle.keys())
        print(
            f"ERROR: No common cycle indices between iPad and iPhone.\n"
            f"  iPad cycles:   {ipad_cycles}\n"
            f"  iPhone cycles: {iphone_cycles}",
            file=sys.stderr,
        )
        return 1

    # Filter to requested cycle if --cycle provided
    if args.cycle is not None:
        if args.cycle not in ipad_by_cycle:
            print(
                f"ERROR: Cycle {args.cycle} not found in iPad records. "
                f"Available: {sorted(ipad_by_cycle.keys())}",
                file=sys.stderr,
            )
            return 1
        if args.cycle not in iphone_by_cycle:
            print(
                f"ERROR: Cycle {args.cycle} not found in iPhone records. "
                f"Available: {sorted(iphone_by_cycle.keys())}",
                file=sys.stderr,
            )
            return 1
        common_cycles = [args.cycle]

    print(f"    Paired cycles:  {common_cycles}")
    print()

    # -----------------------------------------------------------------------
    # Step 4: Per-cycle reports
    # -----------------------------------------------------------------------
    report_lines: List[str] = []
    has_failure = False

    for ci in common_cycles:
        cycle_report = format_cycle_report(
            session_uuid, ci, ipad_by_cycle[ci], iphone_by_cycle[ci]
        )
        report_lines.append(cycle_report)
        print(cycle_report)
        print()

        # Track overall verdict
        drift_ms = compute_drift_ms(ipad_by_cycle[ci], iphone_by_cycle[ci])
        if drift_ms >= DRIFT_THRESHOLD_WARN_MS:
            has_failure = True

    # -----------------------------------------------------------------------
    # Step 5: Aggregate report (if requested and multiple cycles)
    # -----------------------------------------------------------------------
    if args.aggregate and len(common_cycles) > 0:
        agg_report = format_aggregate_report(
            session_uuid, ipad_all, iphone_all, common_cycles
        )
        report_lines.append(agg_report)
        print()
        print(agg_report)

    # -----------------------------------------------------------------------
    # Step 6: Save report to file
    # -----------------------------------------------------------------------
    report_filename = f"drift_report_{session_uuid}.txt"
    if args.cycle is not None:
        report_filename = f"drift_report_{session_uuid}_cycle{args.cycle}.txt"
    elif args.aggregate:
        report_filename = f"drift_report_{session_uuid}_aggregate.txt"

    report_path = safe_output_path(output_dir, report_filename)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(report_lines))
        f.write("\n")

    print(f"\n[*] Report saved to: {report_path}")
    print(f"[*] Downloaded files in: {output_dir}")

    return 1 if has_failure else 0


if __name__ == "__main__":
    sys.exit(main())
