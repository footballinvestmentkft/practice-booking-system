import json
from pathlib import Path


CONFIG = Path(__file__).parents[3] / "config" / "specializations"


def _load(name: str):
    return json.loads((CONFIG / name).read_text())


def test_internship_is_18_plus_and_levels_add_no_age_threshold():
    config = _load("internship.json")
    assert config["min_age"] == 18
    assert {level["requirements"].get("min_age") for level in config["levels"]} <= {None, 18}


def test_football_progression_levels_add_no_age_threshold():
    config = _load("lfa_football_player.json")
    assert config["min_age"] == 5
    assert {level["requirements"].get("min_age") for level in config["levels"]} <= {None, 5}
