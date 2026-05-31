"""
Static no-FIFA guard — PR-FC-1F final cleanup verification.

Scans production code and templates for any non-whitelisted "fifa"/"FIFA"
occurrences and fails the test suite if found.

Whitelist (legitimate remaining occurrences):
  - Historical Alembic migrations (immutable history)
  - _DESIGN_ID_ALIAS / resolve_design_id: input-only legacy sanitizer
  - Explicit backward-compatibility alias tests (test_fclassic_family.py,
    test_fc1b_db_migration.py, test_fc1c_template_rename.py)
  - Functional legacy-forbidden-pattern guards (test_card_export_cs4b.py)
  - This file itself
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_APP = _REPO / "app"
_TESTS = _REPO / "tests"

# Patterns that signal a non-whitelisted "fifa" occurrence
_FORBIDDEN = re.compile(
    r'(?i)\b(fifa)\b|player_card_fifa|export/[a-z]+/fifa\.html|FIFA_CLASSIC\b',
    re.IGNORECASE,
)

# Files / path segments that are explicitly whitelisted
_WHITELIST_PATHS = {
    # Historical Alembic migrations — immutable history
    "alembic/versions",
    # Input-only sanitizer — explicitly approved legacy alias
    "card_design_service.py",
    # Backward-compatibility alias tests
    "test_fclassic_family.py",
    "test_fc1b_db_migration.py",
    "test_fc1c_template_rename.py",
    # Functional legacy-forbidden-pattern guards
    "test_card_export_cs4b.py",
    # This guard itself
    "test_no_fifa_strings.py",
    # OpenAPI snapshots (generated files)
    "openapi_snapshot.json",
    "openapi_schema.json",
    # Card system specs — has backward-compat variant_id test
    "test_card_system_specs.py",
}


def _is_whitelisted(path: Path) -> bool:
    path_str = str(path).replace("\\", "/")
    return any(w in path_str for w in _WHITELIST_PATHS)


def _scan(root: Path, glob: str) -> list[tuple[Path, int, str]]:
    violations: list[tuple[Path, int, str]] = []
    for fpath in sorted(root.rglob(glob)):
        if "__pycache__" in str(fpath) or _is_whitelisted(fpath):
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _FORBIDDEN.search(line):
                violations.append((fpath.relative_to(_REPO), i, line.strip()[:120]))
    return violations


class TestNoFifaStrings:

    def test_no_fifa_in_production_python(self):
        """Production Python source must not contain unwhitelisted 'fifa' references."""
        violations = _scan(_APP, "*.py")
        assert not violations, (
            f"Non-whitelisted 'fifa' found in {len(violations)} location(s):\n"
            + "\n".join(f"  {p}:{ln}: {text}" for p, ln, text in violations[:20])
        )

    def test_no_fifa_in_templates(self):
        """Production Jinja2 templates must not contain 'fifa' in non-comment text."""
        violations = []
        for fpath in sorted((_APP / "templates").rglob("*.html")):
            if _is_whitelisted(fpath):
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                # Skip HTML/Jinja comments (already handled in PR-FC-1E)
                stripped = line.strip()
                if stripped.startswith("{#") or stripped.startswith("<!--"):
                    continue
                if re.search(r'(?i)\bfifa\b', line):
                    violations.append((
                        fpath.relative_to(_REPO), i, stripped[:120]
                    ))
        assert not violations, (
            f"'fifa' found in {len(violations)} template location(s):\n"
            + "\n".join(f"  {p}:{ln}: {text}" for p, ln, text in violations[:20])
        )

    def test_no_fifa_in_test_fixtures(self):
        """Test files must not contain 'fifa' as a canonical fixture value
        outside whitelisted backward-compatibility test files."""
        violations = _scan(_TESTS, "*.py")
        assert not violations, (
            f"Non-whitelisted 'fifa' found in {len(violations)} test location(s):\n"
            + "\n".join(f"  {p}:{ln}: {text}" for p, ln, text in violations[:20])
        )
