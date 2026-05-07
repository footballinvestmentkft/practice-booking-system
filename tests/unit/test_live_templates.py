"""Static template tests for Live-2 templates — PR Live-2 + badge fix.

Tests parse the raw Jinja2/HTML template files — no running server required.

LS-01  tournament_live.html contains live-format-section div
LS-02  tournament_live.html includes _live_group_knockout.html dispatch
LS-03  _live_group_knockout.html contains group standings table headers (GF, GA, GD, Pts)
LS-04  _live_group_knockout.html contains live-snapshot fetch call (in parent)
LS-05  _live_group_knockout.html renders Best Runner-Up only inside qualification_state guard
LS-06  _live_group_knockout.html contains GA column header
"""
import pathlib
import re

_TEMPLATES = pathlib.Path(__file__).parents[2] / "app" / "templates" / "admin"
_LIVE = (_TEMPLATES / "tournament_live.html").read_text(encoding="utf-8")
_GK = (_TEMPLATES / "_live_group_knockout.html").read_text(encoding="utf-8")


# ── LS-01 ─────────────────────────────────────────────────────────────────────

def test_ls_01_live_html_contains_format_section():
    assert 'id="live-format-section"' in _LIVE, (
        "tournament_live.html is missing id='live-format-section'"
    )


# ── LS-02 ─────────────────────────────────────────────────────────────────────

def test_ls_02_live_html_dispatches_group_knockout():
    assert "_live_group_knockout.html" in _LIVE, (
        "tournament_live.html does not include _live_group_knockout.html"
    )


# ── LS-03 ─────────────────────────────────────────────────────────────────────

def test_ls_03_group_knockout_standings_table():
    for header in ("Pts", "GD", "GF", "GA"):
        assert header in _GK, (
            f"_live_group_knockout.html is missing standings column: {header}"
        )


# ── LS-04 ─────────────────────────────────────────────────────────────────────

def test_ls_04_live_html_snapshot_fetch():
    assert "live-snapshot" in _LIVE, (
        "tournament_live.html is missing snapshot fetch call"
    )


# ── LS-05 ─────────────────────────────────────────────────────────────────────

def test_ls_05_best_runner_up_badge_guarded_by_qualification_state():
    """'Best Runner-Up' text must only appear inside a qualification_state condition."""
    # Find every occurrence of "Best Runner-Up" in the template
    positions = [m.start() for m in re.finditer(r"Best Runner-Up", _GK)]
    assert positions, "_live_group_knockout.html has no 'Best Runner-Up' text at all"

    for pos in positions:
        # Search backward from the badge text for the nearest Jinja2 {% if %} block
        preceding = _GK[:pos]
        last_if = preceding.rfind("{%")
        assert last_if != -1, "No Jinja2 block before 'Best Runner-Up'"
        if_block = _GK[last_if : last_if + 80]
        assert "best_runner_up" in if_block, (
            f"'Best Runner-Up' badge at pos {pos} is not guarded by qualification_state == 'best_runner_up'.\n"
            f"Nearest preceding block: {if_block!r}"
        )


# ── LS-06 ─────────────────────────────────────────────────────────────────────

def test_ls_06_ga_column_present():
    """Standings table must include a GA (Goals Against) column."""
    # Must appear both as header and as {{ row.ga }} data cell
    assert ">GA<" in _GK or "GA</th" in _GK or ">GA</th>" in _GK, (
        "_live_group_knockout.html is missing GA column header"
    )
    assert "row.ga" in _GK, (
        "_live_group_knockout.html is missing {{ row.ga }} data cell"
    )
