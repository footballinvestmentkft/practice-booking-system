#!/usr/bin/env python3
"""
Demo scenario dispatcher.

Runs one of the four canonical demo seeds by name.  Each seed is a verbatim
copy of the corresponding script in scripts/ — no logic lives here.

Usage:
    python scripts/demo/run_demo.py [scenario]

Scenarios (default: full):
    full     Full DB reset + complete dataset.              [DESTRUCTIVE]
    skill    Skill-progression arc (4 players, 3 tournaments). [ADDITIVE]
    events   Frontend event calendar (10 semesters, 33 sessions). [DESTRUCTIVE*]
    minimal  Quick-start open tournaments + camps.          [ADDITIVE]

* events truncates operational tables but preserves user accounts.

Examples:
    python scripts/demo/run_demo.py           # runs 'full'
    python scripts/demo/run_demo.py skill
    python scripts/demo/run_demo.py minimal
"""
import runpy
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

_SCENARIOS: dict[str, str] = {
    "full":    "reset_full.py",
    "skill":   "seed_skill_progression.py",
    "events":  "seed_events.py",
    "minimal": "seed_minimal.py",
}


def main() -> None:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "full"

    if scenario in ("-h", "--help"):
        print(__doc__)
        return

    if scenario not in _SCENARIOS:
        print(f"Unknown scenario: {scenario!r}")
        print(f"Available: {', '.join(_SCENARIOS)}")
        sys.exit(1)

    script_path = _HERE / _SCENARIOS[scenario]
    print(f"[run_demo] Running scenario '{scenario}' → {script_path.name}\n")

    # runpy.run_path with run_name='__main__' executes the script exactly as if
    # invoked directly (triggers the `if __name__ == '__main__':` guard).
    runpy.run_path(str(script_path), run_name="__main__")


if __name__ == "__main__":
    main()
