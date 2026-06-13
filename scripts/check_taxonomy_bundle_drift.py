#!/usr/bin/env python3
"""Fail if the iOS-bundled juggling taxonomy diverges from the dataset source.

AN-2: ContactTaxonomyStore.bundledChecksum (Swift) and the bundled copy of
contact_types_v1.json must stay byte-identical to
datasets/juggling/contact_types_v1.json. CI runs this script; any taxonomy
edit must update the bundled copy AND bundledChecksum in the same commit.

Usage: python scripts/check_taxonomy_bundle_drift.py
Exit codes: 0 = in sync, 1 = drift detected, 2 = file missing.
"""
import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = REPO_ROOT / "datasets" / "juggling" / "contact_types_v1.json"
BUNDLED_PATH = (
    REPO_ROOT / "ios" / "LFAEducationCenter" / "Juggling" / "Annotation"
    / "Resources" / "contact_types_v1.json"
)
STORE_SWIFT_PATH = (
    REPO_ROOT / "ios" / "LFAEducationCenter" / "Juggling" / "Annotation"
    / "ContactTaxonomyStore.swift"
)


def md5_of(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def extract_bundled_checksum_constant(swift_source: str) -> str:
    match = re.search(
        r'bundledChecksum\s*=\s*"([0-9a-f]{32})"', swift_source
    )
    if not match:
        raise ValueError("bundledChecksum constant not found in ContactTaxonomyStore.swift")
    return match.group(1)


def main() -> int:
    for path in (SOURCE_PATH, BUNDLED_PATH, STORE_SWIFT_PATH):
        if not path.exists():
            print(f"MISSING: {path}")
            return 2

    source_md5 = md5_of(SOURCE_PATH)
    bundled_md5 = md5_of(BUNDLED_PATH)
    constant_md5 = extract_bundled_checksum_constant(STORE_SWIFT_PATH.read_text())

    errors = []
    if source_md5 != bundled_md5:
        errors.append(
            f"Bundled copy differs from dataset source:\n"
            f"  source : {source_md5}  {SOURCE_PATH.relative_to(REPO_ROOT)}\n"
            f"  bundled: {bundled_md5}  {BUNDLED_PATH.relative_to(REPO_ROOT)}"
        )
    if source_md5 != constant_md5:
        errors.append(
            f"ContactTaxonomyStore.bundledChecksum is stale:\n"
            f"  source  : {source_md5}\n"
            f"  constant: {constant_md5}  ({STORE_SWIFT_PATH.relative_to(REPO_ROOT)})"
        )

    if errors:
        print("TAXONOMY BUNDLE DRIFT DETECTED:\n")
        print("\n\n".join(errors))
        return 1

    print(f"OK — bundled taxonomy matches dataset source (md5={source_md5})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
