"""
Football position definitions for the LFA Player onboarding system.

21-position taxonomy: 4 groups × (5, 7, 7, 2 positions each).
DB values are snake_case strings (e.g. "striker", "left_centre_back").

Taxonomy history:
  v1 (17 positions): original — centre_back/centre_midfield as single nodes
  v2 (21 positions): LCB/RCB split → left_centre_back/right_centre_back;
                     LCM/RCM split → left_centre_midfield/right_centre_midfield.
  Legacy values (centre_back, centre_midfield, second_striker) remain in
  VALID_POSITION_VALUES and LEGACY_POSITION_MAP for backward compatibility.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple


# ── Canonical position registry ───────────────────────────────────────────────

# (group_key, value, label, short)
_POSITIONS: List[Tuple[str, str, str, str]] = [
    # Forwards (5)
    ("forward",    "striker",               "Striker",                "ST"),
    ("forward",    "centre_forward",        "Centre Forward",         "CF"),
    ("forward",    "left_wing",             "Left Wing",              "LW"),
    ("forward",    "right_wing",            "Right Wing",             "RW"),
    ("forward",    "second_striker",        "Second Striker",         "SS"),
    # Midfielders (7)
    ("midfielder", "attacking_midfield",    "Attacking Midfielder",   "AM"),
    ("midfielder", "centre_midfield",       "Central Midfielder",     "CM"),
    ("midfielder", "defensive_midfield",    "Defensive Midfielder",   "DM"),
    ("midfielder", "left_midfield",         "Left Midfielder",        "LM"),
    ("midfielder", "right_midfield",        "Right Midfielder",       "RM"),
    ("midfielder", "left_centre_midfield",  "Left Centre Midfielder", "LCM"),
    ("midfielder", "right_centre_midfield", "Right Centre Midfielder","RCM"),
    # Defenders (7)
    ("defender",   "centre_back",           "Centre Back",            "CB"),
    ("defender",   "left_back",             "Left Back",              "LB"),
    ("defender",   "right_back",            "Right Back",             "RB"),
    ("defender",   "left_wing_back",        "Left Wing Back",         "LWB"),
    ("defender",   "right_wing_back",       "Right Wing Back",        "RWB"),
    ("defender",   "left_centre_back",      "Left Centre-Back",       "LCB"),
    ("defender",   "right_centre_back",     "Right Centre-Back",      "RCB"),
    # Goalkeepers (2)
    ("goalkeeper", "goalkeeper",            "Goalkeeper",             "GK"),
    ("goalkeeper", "sweeper_keeper",        "Sweeper Keeper",         "SK"),
]

POSITIONS_21: List[Dict] = [
    {"group": g, "value": v, "label": l, "short": s}
    for g, v, l, s in _POSITIONS
]

VALID_POSITION_VALUES: frozenset = frozenset(p["value"] for p in POSITIONS_21)

# Maps legacy 4-value uppercase strings → canonical snake_case.
# All 21 canonical values also map to themselves (idempotent passthrough).
LEGACY_POSITION_MAP: Dict[str, str] = {
    "STRIKER":    "striker",
    "MIDFIELDER": "centre_midfield",
    "DEFENDER":   "centre_back",
    "GOALKEEPER": "goalkeeper",
    **{p["value"]: p["value"] for p in POSITIONS_21},
}

_GROUP_LABELS: Dict[str, str] = {
    "forward":    "Forwards",
    "midfielder": "Midfielders",
    "defender":   "Defenders",
    "goalkeeper": "Goalkeepers",
}

_GROUP_ORDER = ["forward", "midfielder", "defender", "goalkeeper"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def normalize_position(raw: str) -> Optional[str]:
    """Return canonical snake_case value or None if unrecognised."""
    if not raw:
        return None
    canonical = LEGACY_POSITION_MAP.get(raw) or LEGACY_POSITION_MAP.get(raw.upper())
    return canonical


def normalize_positions(raw_list: List[str]) -> Optional[List[str]]:
    """
    Normalise a list of raw position strings.

    Returns None if any value is unrecognised or if the list is empty.
    Preserves order; position[0] is treated as primary by convention.
    """
    if not raw_list:
        return None
    result = []
    for raw in raw_list:
        canon = normalize_position(raw)
        if canon is None:
            return None
        result.append(canon)
    return result


def position_label(value: str) -> str:
    """Return human-readable label for a canonical position value."""
    for p in POSITIONS_21:
        if p["value"] == value:
            return p["label"]
    return value


def position_short(value: str) -> str:
    """Return abbreviation (e.g. 'ST') for a canonical position value."""
    for p in POSITIONS_21:
        if p["value"] == value:
            return p["short"]
    return value.upper()[:3]


def positions_grouped() -> List[Dict]:
    """
    Return positions organised by group for Jinja2 rendering.

    Shape: [{"key": "forward", "label": "Forwards", "positions": [...]}, ...]
    """
    groups: Dict[str, List[Dict]] = {g: [] for g in _GROUP_ORDER}
    for p in POSITIONS_21:
        groups[p["group"]].append(p)
    return [
        {"key": key, "label": _GROUP_LABELS[key], "positions": groups[key]}
        for key in _GROUP_ORDER
    ]


# ── Pitch display nodes ────────────────────────────────────────────────────────
# Coordinates are the authoritative values from pitch-selector.js PITCH_NODES.
# x/y are fractions of the landscape SVG canvas (GK on left, ST on right).
# "striker" has two visual nodes (ST1 at y≈0.34, ST2 at y≈0.66) so both
# halves of the dual-node highlight when "striker" is the selected value.
# "second_striker" and "centre_back" have no dedicated pitch node (legacy
# taxonomy gap — consistent with interactive pitch-selector.js behaviour).

_PITCH_NODES_RAW: List[Tuple[str, str, str, str, float, float]] = [
    # (node_id, label, name, role, x, y)  — x/y from pitch-selector.js
    ("GK",  "GK",  "Goalkeeper",             "goalkeeper", 0.02, 0.50),
    ("SK",  "SK",  "Sweeper Keeper",          "goalkeeper", 0.10, 0.50),
    ("LB",  "LB",  "Left Back",               "defender",   0.19, 0.15),
    ("LCB", "LCB", "Left Centre-Back",        "defender",   0.19, 0.37),
    ("RCB", "RCB", "Right Centre-Back",       "defender",   0.19, 0.63),
    ("RB",  "RB",  "Right Back",              "defender",   0.19, 0.85),
    ("LWB", "LWB", "Left Wing-Back",          "defender",   0.28, 0.10),
    ("RWB", "RWB", "Right Wing-Back",         "defender",   0.28, 0.90),
    ("DM",  "DM",  "Defensive Mid",           "midfielder", 0.37, 0.50),
    ("LCM", "LCM", "Left Centre Mid",         "midfielder", 0.47, 0.33),
    ("CM",  "CM",  "Centre Mid",              "midfielder", 0.50, 0.50),
    ("RCM", "RCM", "Right Centre Mid",        "midfielder", 0.47, 0.67),
    ("LM",  "LM",  "Left Midfielder",         "midfielder", 0.55, 0.17),
    ("RM",  "RM",  "Right Midfielder",        "midfielder", 0.55, 0.83),
    ("AM",  "AM",  "Att. Midfielder",         "midfielder", 0.68, 0.50),
    ("LW",  "LW",  "Left Winger",             "forward",    0.73, 0.07),
    ("RW",  "RW",  "Right Winger",            "forward",    0.73, 0.93),
    ("CF",  "CF",  "Centre Forward",          "forward",    0.83, 0.50),
    ("ST1", "ST",  "Striker",                 "forward",    0.88, 0.34),
    ("ST2", "ST",  "Striker",                 "forward",    0.88, 0.66),
]

# Canonical value for each node — mirrors pitch-selector.js PITCH_NODES[i].canonical.
_NODE_CANONICAL: Dict[str, str] = {
    "GK":  "goalkeeper",
    "SK":  "sweeper_keeper",
    "LB":  "left_back",
    "LCB": "left_centre_back",
    "RCB": "right_centre_back",
    "RB":  "right_back",
    "LWB": "left_wing_back",
    "RWB": "right_wing_back",
    "DM":  "defensive_midfield",
    "LCM": "left_centre_midfield",
    "CM":  "centre_midfield",
    "RCM": "right_centre_midfield",
    "LM":  "left_midfield",
    "RM":  "right_midfield",
    "AM":  "attacking_midfield",
    "LW":  "left_wing",
    "RW":  "right_wing",
    "CF":  "centre_forward",
    "ST1": "striker",
    "ST2": "striker",
}


def get_pitch_display_nodes(
    primary_position: str,
    all_positions: List[str],
) -> List[Dict]:
    """Build annotated pitch node list for the FClassic card position panel.

    Each entry mirrors a node from pitch-selector.js PITCH_NODES.
    is_primary  — True for the primary position (one node, or both ST nodes).
    is_selected — True if the node's canonical value appears in all_positions.

    ST1 and ST2 share canonical="striker"; both are marked is_selected when
    "striker" appears in all_positions, and both is_primary when "striker" is
    the primary position — preserving pitch-selector.js dual-node behaviour.

    Positions without a pitch node (second_striker, centre_back legacy) are
    silently absent — consistent with the interactive selector.
    """
    selected_set = frozenset(all_positions)
    return [
        {
            "node_id":    node_id,
            "label":      label,
            "name":       name,
            "role":       role,
            "x":          x,
            "y":          y,
            "canonical":  _NODE_CANONICAL[node_id],
            "is_primary": _NODE_CANONICAL[node_id] == primary_position,
            "is_selected": _NODE_CANONICAL[node_id] in selected_set,
        }
        for node_id, label, name, role, x, y in _PITCH_NODES_RAW
    ]
