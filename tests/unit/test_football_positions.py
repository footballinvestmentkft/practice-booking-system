"""
Unit tests for app/utils/football_positions.py

Taxonomy v2 (21 positions):
  Forwards    (5): striker, centre_forward, left_wing, right_wing, second_striker
  Midfielders (7): attacking_midfield, centre_midfield, defensive_midfield,
                   left_midfield, right_midfield,
                   left_centre_midfield, right_centre_midfield   ← NEW D2
  Defenders   (7): centre_back, left_back, right_back,
                   left_wing_back, right_wing_back,
                   left_centre_back, right_centre_back           ← NEW D1
  Goalkeepers (2): goalkeeper, sweeper_keeper

FP-01: POSITIONS_21 has exactly 21 entries
FP-02: VALID_POSITION_VALUES has exactly 21 members
FP-03: POSITIONS_21 entries cover exactly 4 groups
FP-04: positions_grouped() returns 4 groups in correct order
FP-05: positions_grouped() group sizes = 5, 7, 7, 2
FP-06: normalize_position — legacy uppercase STRIKER → striker
FP-07: normalize_position — legacy MIDFIELDER → centre_midfield
FP-08: normalize_position — legacy DEFENDER → centre_back
FP-09: normalize_position — legacy GOALKEEPER → goalkeeper
FP-10: normalize_position — all 21 canonical values pass through (idempotent)
FP-11: normalize_position — unknown value returns None
FP-12: normalize_position — empty string returns None
FP-13: normalize_positions — valid list normalises all values
FP-14: normalize_positions — mixed legacy + canonical
FP-15: normalize_positions — empty list returns None
FP-16: normalize_positions — unknown value returns None
FP-17: normalize_positions — preserves order (positions[0] = primary)
FP-18: position_label — known values return correct labels (including new D1/D2)
FP-19: position_label — unknown value returns the value itself
FP-20: position_short — known values return correct abbreviations (including LCB/RCB/LCM/RCM)
FP-21: position_short — goalkeeper group abbreviations
FP-22: backward compat — centre_back still valid (legacy DB records)
FP-23: backward compat — centre_midfield still valid (legacy DB records)
FP-24: backward compat — second_striker still valid (D4 gated removal)
FP-25: D1 split — left_centre_back and right_centre_back are independent canonical values
FP-26: D2 split — left_centre_midfield and right_centre_midfield are independent canonical values
FP-27: normalize_position does NOT map centre_back → LCB or RCB (no auto-migration)
FP-28: positions_grouped — new defender values in defender group
FP-29: positions_grouped — new midfielder values in midfielder group
"""
import pytest
from app.utils.football_positions import (
    POSITIONS_21,
    VALID_POSITION_VALUES,
    LEGACY_POSITION_MAP,
    normalize_position,
    normalize_positions,
    position_label,
    position_short,
    positions_grouped,
)


# ── FP-01 / FP-02: registry sizes ────────────────────────────────────────────

def test_fp01_positions_21_has_21_entries():
    assert len(POSITIONS_21) == 21


def test_fp02_valid_position_values_has_21_members():
    assert len(VALID_POSITION_VALUES) == 21


# ── FP-03: 4 groups covered ───────────────────────────────────────────────────

def test_fp03_positions_cover_exactly_4_groups():
    groups = {p["group"] for p in POSITIONS_21}
    assert groups == {"forward", "midfielder", "defender", "goalkeeper"}


# ── FP-04: positions_grouped structure ───────────────────────────────────────

def test_fp04_positions_grouped_returns_4_groups():
    groups = positions_grouped()
    assert len(groups) == 4
    assert [g["key"] for g in groups] == ["forward", "midfielder", "defender", "goalkeeper"]
    assert groups[0]["label"] == "Forwards"
    assert groups[1]["label"] == "Midfielders"
    assert groups[2]["label"] == "Defenders"
    assert groups[3]["label"] == "Goalkeepers"


# ── FP-05: group sizes (5 / 7 / 7 / 2 after D1+D2 expansion) ────────────────

def test_fp05_group_sizes():
    groups = {g["key"]: len(g["positions"]) for g in positions_grouped()}
    assert groups["forward"]    == 5
    assert groups["midfielder"] == 7
    assert groups["defender"]   == 7
    assert groups["goalkeeper"] == 2


# ── FP-06..09: legacy uppercase mapping ──────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("STRIKER",    "striker"),
    ("MIDFIELDER", "centre_midfield"),
    ("DEFENDER",   "centre_back"),
    ("GOALKEEPER", "goalkeeper"),
])
def test_fp06_fp09_legacy_uppercase_mapping(raw, expected):
    assert normalize_position(raw) == expected


# ── FP-10: all 21 canonical values pass through ───────────────────────────────

@pytest.mark.parametrize("canonical", [
    # Forwards
    "striker", "centre_forward", "left_wing", "right_wing", "second_striker",
    # Midfielders (original 5 + 2 new)
    "attacking_midfield", "centre_midfield", "defensive_midfield",
    "left_midfield", "right_midfield",
    "left_centre_midfield", "right_centre_midfield",
    # Defenders (original 5 + 2 new)
    "centre_back", "left_back", "right_back", "left_wing_back", "right_wing_back",
    "left_centre_back", "right_centre_back",
    # Goalkeepers
    "goalkeeper", "sweeper_keeper",
])
def test_fp10_all_canonical_pass_through(canonical):
    assert normalize_position(canonical) == canonical


# ── FP-11 / FP-12: invalid inputs ────────────────────────────────────────────

def test_fp11_unknown_value_returns_none():
    assert normalize_position("UNKNOWN") is None
    assert normalize_position("winger") is None
    assert normalize_position("forward") is None    # group key, not a position value
    assert normalize_position("LCB") is None        # short code, not a canonical value


def test_fp12_empty_string_returns_none():
    assert normalize_position("") is None


# ── FP-13..17: normalize_positions ───────────────────────────────────────────

def test_fp13_valid_list_normalises_all():
    result = normalize_positions(["striker", "left_wing", "left_centre_back"])
    assert result == ["striker", "left_wing", "left_centre_back"]


def test_fp14_mixed_legacy_and_canonical():
    result = normalize_positions(["STRIKER", "left_centre_midfield", "GOALKEEPER"])
    assert result == ["striker", "left_centre_midfield", "goalkeeper"]


def test_fp15_empty_list_returns_none():
    assert normalize_positions([]) is None


def test_fp16_unknown_value_returns_none():
    assert normalize_positions(["striker", "bad_position"]) is None
    assert normalize_positions(["left_centre_back", "LCB"]) is None   # short code invalid


def test_fp17_preserves_order():
    raw = ["right_centre_midfield", "striker", "left_centre_back"]
    result = normalize_positions(raw)
    assert result is not None
    assert result[0] == "right_centre_midfield"   # primary preserved at [0]
    assert result[1] == "striker"
    assert result[2] == "left_centre_back"


# ── FP-18 / FP-19: position_label ────────────────────────────────────────────

def test_fp18_known_labels():
    assert position_label("striker")               == "Striker"
    assert position_label("left_wing")             == "Left Wing"
    assert position_label("sweeper_keeper")        == "Sweeper Keeper"
    assert position_label("defensive_midfield")    == "Defensive Midfielder"
    assert position_label("centre_back")           == "Centre Back"
    # D1 new values
    assert position_label("left_centre_back")      == "Left Centre-Back"
    assert position_label("right_centre_back")     == "Right Centre-Back"
    # D2 new values
    assert position_label("left_centre_midfield")  == "Left Centre Midfielder"
    assert position_label("right_centre_midfield") == "Right Centre Midfielder"


def test_fp19_unknown_label_returns_value_itself():
    assert position_label("some_unknown_pos") == "some_unknown_pos"


# ── FP-20 / FP-21: position_short ────────────────────────────────────────────

def test_fp20_forward_and_midfield_shorts():
    assert position_short("striker")               == "ST"
    assert position_short("centre_forward")        == "CF"
    assert position_short("left_wing")             == "LW"
    assert position_short("right_wing")            == "RW"
    assert position_short("second_striker")        == "SS"
    assert position_short("attacking_midfield")    == "AM"
    assert position_short("centre_midfield")       == "CM"
    assert position_short("defensive_midfield")    == "DM"
    assert position_short("left_midfield")         == "LM"
    assert position_short("right_midfield")        == "RM"
    assert position_short("centre_back")           == "CB"
    assert position_short("left_back")             == "LB"
    assert position_short("right_back")            == "RB"
    assert position_short("left_wing_back")        == "LWB"
    assert position_short("right_wing_back")       == "RWB"
    # D1 new values
    assert position_short("left_centre_back")      == "LCB"
    assert position_short("right_centre_back")     == "RCB"
    # D2 new values
    assert position_short("left_centre_midfield")  == "LCM"
    assert position_short("right_centre_midfield") == "RCM"


def test_fp21_goalkeeper_shorts():
    assert position_short("goalkeeper")     == "GK"
    assert position_short("sweeper_keeper") == "SK"


# ── FP-22..24: backward compat — legacy values still valid ────────────────────

def test_fp22_centre_back_still_valid():
    assert "centre_back" in VALID_POSITION_VALUES
    assert normalize_position("centre_back") == "centre_back"


def test_fp23_centre_midfield_still_valid():
    assert "centre_midfield" in VALID_POSITION_VALUES
    assert normalize_position("centre_midfield") == "centre_midfield"


def test_fp24_second_striker_still_valid():
    assert "second_striker" in VALID_POSITION_VALUES
    assert normalize_position("second_striker") == "second_striker"


# ── FP-25 / FP-26: D1 and D2 are independent, not aliased ────────────────────

def test_fp25_d1_lcb_rcb_are_independent_canonical_values():
    assert "left_centre_back"  in VALID_POSITION_VALUES
    assert "right_centre_back" in VALID_POSITION_VALUES
    assert normalize_position("left_centre_back")  == "left_centre_back"
    assert normalize_position("right_centre_back") == "right_centre_back"
    # They are distinct from the legacy centre_back
    assert normalize_position("left_centre_back")  != "centre_back"
    assert normalize_position("right_centre_back") != "centre_back"


def test_fp26_d2_lcm_rcm_are_independent_canonical_values():
    assert "left_centre_midfield"  in VALID_POSITION_VALUES
    assert "right_centre_midfield" in VALID_POSITION_VALUES
    assert normalize_position("left_centre_midfield")  == "left_centre_midfield"
    assert normalize_position("right_centre_midfield") == "right_centre_midfield"
    assert normalize_position("left_centre_midfield")  != "centre_midfield"
    assert normalize_position("right_centre_midfield") != "centre_midfield"


# ── FP-27: no silent auto-migration of legacy → new values ───────────────────

def test_fp27_centre_back_does_not_map_to_lcb_or_rcb():
    # Existing DB records with centre_back must normalize to centre_back, not LCB/RCB
    result = normalize_position("centre_back")
    assert result == "centre_back"
    assert result != "left_centre_back"
    assert result != "right_centre_back"


# ── FP-28 / FP-29: new values appear in correct groups ───────────────────────

def test_fp28_new_defender_values_in_defender_group():
    grouped = positions_grouped()
    defender_group = next(g for g in grouped if g["key"] == "defender")
    defender_values = [p["value"] for p in defender_group["positions"]]
    assert "left_centre_back"  in defender_values
    assert "right_centre_back" in defender_values
    assert "centre_back"       in defender_values   # legacy still present


def test_fp29_new_midfielder_values_in_midfielder_group():
    grouped = positions_grouped()
    mid_group = next(g for g in grouped if g["key"] == "midfielder")
    mid_values = [p["value"] for p in mid_group["positions"]]
    assert "left_centre_midfield"  in mid_values
    assert "right_centre_midfield" in mid_values
    assert "centre_midfield"       in mid_values    # legacy still present
