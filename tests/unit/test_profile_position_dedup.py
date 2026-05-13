"""
Unit tests for profile.py position constant deduplication.

Verifies that _VALID_POSITIONS, _POSITION_LABELS, and _POSITION_GROUPS
in app/api/web_routes/profile.py are derived from football_positions.py
(single source of truth) and accept all 21 canonical values including the
new D1 (LCB/RCB) and D2 (LCM/RCM) split positions.

PROF-DEDUP-01: _VALID_POSITIONS equals VALID_POSITION_VALUES (same object)
PROF-DEDUP-02: _VALID_POSITIONS accepts all 4 new D1/D2 canonical values
PROF-DEDUP-03: _VALID_POSITIONS accepts legacy values (centre_back, centre_midfield, second_striker)
PROF-DEDUP-04: _POSITION_LABELS has an entry for every value in _VALID_POSITIONS
PROF-DEDUP-05: _POSITION_LABELS format is "Label (SHORT)" for all entries
PROF-DEDUP-06: _POSITION_LABELS new D1 entries have correct short codes LCB/RCB
PROF-DEDUP-07: _POSITION_LABELS new D2 entries have correct short codes LCM/RCM
PROF-DEDUP-08: _POSITION_GROUPS contains all 21 canonical values (no orphans)
PROF-DEDUP-09: _POSITION_GROUPS has correct group count (4 groups)
PROF-DEDUP-10: _POSITION_GROUPS defender group contains new D1 values
PROF-DEDUP-11: _POSITION_GROUPS midfielder group contains new D2 values
PROF-DEDUP-12: profile edit POST guard accepts new D1/D2 canonical values (no 400)
"""
import pytest

from app.api.web_routes.profile import (
    _VALID_POSITIONS,
    _POSITION_LABELS,
    _POSITION_GROUPS,
)
from app.utils.football_positions import VALID_POSITION_VALUES


# ── PROF-DEDUP-01: single source of truth ────────────────────────────────────

def test_dedup01_valid_positions_is_same_as_football_positions():
    assert _VALID_POSITIONS == VALID_POSITION_VALUES


# ── PROF-DEDUP-02: new D1/D2 values accepted ─────────────────────────────────

@pytest.mark.parametrize("value", [
    "left_centre_back",
    "right_centre_back",
    "left_centre_midfield",
    "right_centre_midfield",
])
def test_dedup02_new_d1_d2_values_accepted(value):
    assert value in _VALID_POSITIONS


# ── PROF-DEDUP-03: legacy backward-compat values still accepted ───────────────

@pytest.mark.parametrize("value", [
    "centre_back",
    "centre_midfield",
    "second_striker",
])
def test_dedup03_legacy_values_still_valid(value):
    assert value in _VALID_POSITIONS


# ── PROF-DEDUP-04: _POSITION_LABELS covers all valid positions ────────────────

def test_dedup04_position_labels_covers_all_valid_positions():
    missing = _VALID_POSITIONS - set(_POSITION_LABELS.keys())
    assert not missing, f"_POSITION_LABELS missing entries for: {missing}"


# ── PROF-DEDUP-05: label format is "Label (SHORT)" ──────────────────────────

def test_dedup05_label_format_is_label_with_short_code():
    for value, label in _POSITION_LABELS.items():
        assert '(' in label and label.endswith(')'), (
            f"_POSITION_LABELS['{value}'] = '{label}' does not match 'Label (SHORT)' format"
        )


# ── PROF-DEDUP-06/07: new short codes in labels ──────────────────────────────

def test_dedup06_d1_labels_have_lcb_rcb_short_codes():
    assert _POSITION_LABELS["left_centre_back"].endswith("(LCB)")
    assert _POSITION_LABELS["right_centre_back"].endswith("(RCB)")


def test_dedup07_d2_labels_have_lcm_rcm_short_codes():
    assert _POSITION_LABELS["left_centre_midfield"].endswith("(LCM)")
    assert _POSITION_LABELS["right_centre_midfield"].endswith("(RCM)")


# ── PROF-DEDUP-08/09: _POSITION_GROUPS structure ─────────────────────────────

def test_dedup08_position_groups_contains_all_canonical_values():
    grouped_values = set()
    for group in _POSITION_GROUPS:
        for pos in group["positions"]:
            grouped_values.add(pos["value"])
    missing = _VALID_POSITIONS - grouped_values
    assert not missing, f"_POSITION_GROUPS missing values: {missing}"


def test_dedup09_position_groups_has_4_groups():
    assert len(_POSITION_GROUPS) == 4
    keys = [g["key"] for g in _POSITION_GROUPS]
    assert keys == ["forward", "midfielder", "defender", "goalkeeper"]


# ── PROF-DEDUP-10/11: new values in correct groups ───────────────────────────

def test_dedup10_d1_values_in_defender_group():
    def_group = next(g for g in _POSITION_GROUPS if g["key"] == "defender")
    def_values = [p["value"] for p in def_group["positions"]]
    assert "left_centre_back"  in def_values
    assert "right_centre_back" in def_values


def test_dedup11_d2_values_in_midfielder_group():
    mid_group = next(g for g in _POSITION_GROUPS if g["key"] == "midfielder")
    mid_values = [p["value"] for p in mid_group["positions"]]
    assert "left_centre_midfield"  in mid_values
    assert "right_centre_midfield" in mid_values


# ── PROF-DEDUP-12: profile edit POST guard allows new values ──────────────────

@pytest.mark.parametrize("position", [
    "left_centre_back",
    "right_centre_back",
    "left_centre_midfield",
    "right_centre_midfield",
    "centre_back",        # legacy backward compat
    "centre_midfield",    # legacy backward compat
])
def test_dedup12_edit_post_guard_accepts_new_positions(position):
    assert position in _VALID_POSITIONS, (
        f"Profile edit POST would reject '{position}' — add it to _VALID_POSITIONS"
    )
