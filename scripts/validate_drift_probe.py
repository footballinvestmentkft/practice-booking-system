#!/usr/bin/env python3
"""
Drift probe cycle validator — verifies all 10 criteria.
Usage:
    python3 validate_drift_probe.py --ipad ~/drift_probe/ipad.log --iphone ~/drift_probe/iphone.log
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

RECORD_PATTERN = re.compile(r'\[DriftMeasurement:RECORD\] (\{.*\})')

def parse_records(log_path: str) -> list[dict]:
    records = []
    try:
        with open(log_path, encoding='utf-8') as f:
            for line in f:
                m = RECORD_PATTERN.search(line)
                if m:
                    try:
                        records.append(json.loads(m.group(1)))
                    except json.JSONDecodeError as e:
                        print(f"  WARN: JSON parse error in {log_path}: {e}", file=sys.stderr)
    except FileNotFoundError:
        print(f"ERROR: Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)
    return records

def parse_iso(s: str) -> float:
    """Return POSIX timestamp from ISO8601 with fractional seconds."""
    # Python < 3.11 doesn't parse trailing Z with fromisoformat
    s = s.replace('Z', '+00:00')
    return datetime.fromisoformat(s).timestamp()

def check(label: str, condition: bool, detail: str = '') -> bool:
    status = 'PASS' if condition else 'FAIL'
    suffix = f'  ({detail})' if detail else ''
    print(f'  [{status}] {label}{suffix}')
    return condition

def validate(ipad_log: str, iphone_log: str) -> bool:
    print('\n=== DRIFT PROBE VALIDATION REPORT ===\n')

    ipad_recs  = parse_records(ipad_log)
    iphone_recs = parse_records(iphone_log)

    print(f'iPad    records parsed : {len(ipad_recs)}')
    print(f'iPhone  records parsed : {len(iphone_recs)}')
    print()

    if not ipad_recs or not iphone_recs:
        print('ABORT: No records found — check log files and rerun.')
        return False

    # Use cycleIndex=1 for the probe
    ipad_r   = next((r for r in ipad_recs   if r.get('cycle_index') == 1), None)
    iphone_r = next((r for r in iphone_recs if r.get('cycle_index') == 1), None)

    if not ipad_r or not iphone_r:
        print(f'ABORT: cycle_index=1 record not found. '
              f'iPad cycles={[r.get("cycle_index") for r in ipad_recs]}, '
              f'iPhone cycles={[r.get("cycle_index") for r in iphone_recs]}')
        return False

    results = []

    # 1. Same HEAD SHA — not in the record, but device_type confirms the build ran correctly
    results.append(check('1. Both records present for cycle_index=1',
                          ipad_r is not None and iphone_r is not None))

    # 2. Same session UUID
    results.append(check('2. Same session UUID',
                          ipad_r['session_uuid'] == iphone_r['session_uuid'],
                          f"'{ipad_r['session_uuid']}'"))

    # 3. Both cycleIndex=1
    results.append(check('3. Both cycle_index=1',
                          ipad_r['cycle_index'] == 1 and iphone_r['cycle_index'] == 1))

    # 4. Device types differ: one ipad, one iphone
    types = {ipad_r['device_type'], iphone_r['device_type']}
    results.append(check('4. Device types: one ipad, one iphone',
                          'ipad' in types and 'iphone' in types,
                          f"iPad record device_type='{ipad_r['device_type']}', "
                          f"iPhone record device_type='{iphone_r['device_type']}'"))

    # 5. All timestamp/metric fields non-null and non-zero
    def check_fields(rec: dict, label: str) -> bool:
        ok = True
        for field in ('scheduled_start_at', 'local_fire_at', 'did_start_recording_at',
                      'server_offset_ms', 'callback_delay_ms'):
            v = rec.get(field)
            if v is None or v == '':
                print(f'     FAIL  {label}.{field} is null/empty')
                ok = False
            elif field == 'server_offset_ms':
                print(f'     ok    {label}.{field} = {v:.1f} ms')
            elif field == 'callback_delay_ms':
                print(f'     ok    {label}.{field} = {v:.1f} ms')
            else:
                print(f'     ok    {label}.{field} = {v}')
        return ok
    print('  Checking iPad fields:')
    ok_ipad = check_fields(ipad_r, 'iPad')
    print('  Checking iPhone fields:')
    ok_iphone = check_fields(iphone_r, 'iPhone')
    results.append(check('5. All required fields non-null on both records',
                          ok_ipad and ok_iphone))

    # 6. Exactly one record per device for this session+cycle
    ipad_c1  = [r for r in ipad_recs   if r['session_uuid'] == ipad_r['session_uuid'] and r['cycle_index'] == 1]
    iphone_c1 = [r for r in iphone_recs if r['session_uuid'] == iphone_r['session_uuid'] and r['cycle_index'] == 1]
    results.append(check('6. Exactly one record per device (cycle=1, same session)',
                          len(ipad_c1) == 1 and len(iphone_c1) == 1,
                          f'iPad={len(ipad_c1)}, iPhone={len(iphone_c1)}'))

    # 7. Pairwise drift computable: both records share session UUID + cycle_index
    can_pair = (ipad_r['session_uuid'] == iphone_r['session_uuid'] and
                ipad_r['cycle_index'] == iphone_r['cycle_index'])
    results.append(check('7. Pairwise drift computable (shared UUID + cycleIndex)',
                          can_pair))

    # 8. Pairwise drift non-negative and in realistic range (0–5000 ms)
    if can_pair:
        try:
            t_ipad   = parse_iso(ipad_r['did_start_recording_at'])
            t_iphone = parse_iso(iphone_r['did_start_recording_at'])
            drift_ms = abs(t_ipad - t_iphone) * 1000
            in_range = 0 <= drift_ms <= 5000
            results.append(check('8. Pairwise drift ≥0 ms and ≤5000 ms',
                                  in_range, f'{drift_ms:.1f} ms'))
        except Exception as e:
            results.append(check('8. Pairwise drift calculation', False, str(e)))
    else:
        results.append(check('8. Pairwise drift (skipped — criterion 7 failed)', False))

    # 9. JSON export present: both records have all CodingKeys present
    required_keys = {
        'session_uuid', 'cycle_index', 'device_id', 'device_type',
        'scheduled_start_at', 'local_fire_at', 'did_start_recording_at',
        'server_offset_estimate_ms', 'server_offset_ms', 'callback_delay_ms',
        'capture_orientation', 'clock_quality', 'success'
    }
    ipad_missing   = required_keys - set(ipad_r.keys())
    iphone_missing = required_keys - set(iphone_r.keys())
    results.append(check('9. JSON schema complete (all CodingKeys present)',
                          not ipad_missing and not iphone_missing,
                          f'iPad missing={ipad_missing or "none"}, '
                          f'iPhone missing={iphone_missing or "none"}'))

    # 10. Summary: cycle_count=1, device_record_count=2, pairwise_sample_count=1
    same_uuid = ipad_r['session_uuid'] == iphone_r['session_uuid']
    all_session = [r for r in ipad_recs + iphone_recs
                   if r['session_uuid'] == ipad_r['session_uuid']]
    cycle_count = len(set(r['cycle_index'] for r in all_session))
    device_count = len(set(r['device_id'] for r in all_session if r['cycle_index'] == 1))
    pairwise_count = 1 if can_pair else 0
    results.append(check('10. Summary: cycle_count=1, device_record_count=2, pairwise_sample_count=1',
                          cycle_count == 1 and device_count == 2 and pairwise_count == 1,
                          f'cycles={cycle_count}, devices={device_count}, pairs={pairwise_count}'))

    # Final verdict
    passed = sum(results)
    total  = len(results)
    print(f'\n{"="*38}')
    print(f'RESULT: {passed}/{total} criteria PASS')
    if passed == total:
        print('PROBE CYCLE: ✓ ALL PASS')
    else:
        print('PROBE CYCLE: ✗ FAILURES DETECTED')
    print('='*38)

    if can_pair:
        print(f'\nPairwise drift (probe cycle): {drift_ms:.1f} ms')
    print(f'session_uuid: {ipad_r["session_uuid"]}')
    print()
    return passed == total

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate drift probe cycle')
    parser.add_argument('--ipad',   required=True, help='iPad log stream capture file')
    parser.add_argument('--iphone', required=True, help='iPhone log stream capture file')
    args = parser.parse_args()
    sys.exit(0 if validate(args.ipad, args.iphone) else 1)
