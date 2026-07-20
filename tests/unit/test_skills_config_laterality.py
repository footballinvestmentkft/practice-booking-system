"""
Taxonomy integrity tests for skills_config.SKILL_LATERALITY.

SC-LAT-01  All 44 skills have a laterality_domain field
SC-LAT-02  laterality_domain values are limited to {"foot", "hand", "none"}
SC-LAT-03  throwing == "hand"
SC-LAT-04  heading  == "none"  (bilateral; not foot-dominant)
SC-LAT-05  foot skill count == 20
SC-LAT-06  hand skill count ==  1  (only throwing)
SC-LAT-07  none skill count == 23
SC-LAT-08  SKILL_LATERALITY covers all 44 keys and mirrors ALL_SKILLS
"""

from app.skills_config import ALL_SKILLS, SKILL_LATERALITY

_VALID_DOMAINS = frozenset({"foot", "hand", "none"})

_EXPECTED_FOOT_SKILLS = frozenset({
    "ball_control", "dribbling", "finishing", "shot_power", "long_shots",
    "volleys", "crossing", "passing", "tackle", "marking", "shooting",
    "technique", "creativity", "long_passing", "flair", "touch",
    "forward_runs", "free_kicks", "corners", "penalties",
})

_EXPECTED_NONE_SKILLS = frozenset({
    "heading",
    "positioning_off", "positioning_def", "vision", "aggression", "reactions",
    "composure", "consistency", "tactical_awareness", "anticipation",
    "concentration", "decisions", "determination", "teamwork", "leadership",
    "acceleration", "sprint_speed", "agility", "jumping", "strength",
    "stamina", "balance", "work_rate",
})


class TestSkillsConfigLateralityTaxonomy:

    def test_all_skills_have_laterality_domain(self):
        """SC-LAT-01: every key in ALL_SKILLS carries laterality_domain."""
        missing = [k for k, v in ALL_SKILLS.items() if "laterality_domain" not in v]
        assert not missing, f"Skills missing laterality_domain: {missing}"

    def test_laterality_domain_values_are_valid(self):
        """SC-LAT-02: no laterality_domain outside the known set."""
        invalid = {
            k: v["laterality_domain"]
            for k, v in ALL_SKILLS.items()
            if v["laterality_domain"] not in _VALID_DOMAINS
        }
        assert not invalid, f"Invalid laterality_domain values: {invalid}"

    def test_throwing_is_hand(self):
        """SC-LAT-03: throwing is the sole hand-lateral skill."""
        assert ALL_SKILLS["throwing"]["laterality_domain"] == "hand"

    def test_heading_is_none(self):
        """SC-LAT-04: heading is not foot-dominant — common misclassification guard."""
        assert ALL_SKILLS["heading"]["laterality_domain"] == "none"

    def test_foot_count_is_20(self):
        """SC-LAT-05: exactly 20 foot-lateral skills."""
        foot_skills = {k for k, v in ALL_SKILLS.items() if v["laterality_domain"] == "foot"}
        assert foot_skills == _EXPECTED_FOOT_SKILLS, (
            f"Foot skill mismatch.\n"
            f"  Extra:   {foot_skills - _EXPECTED_FOOT_SKILLS}\n"
            f"  Missing: {_EXPECTED_FOOT_SKILLS - foot_skills}"
        )

    def test_hand_count_is_1(self):
        """SC-LAT-06: exactly 1 hand-lateral skill (throwing)."""
        hand_skills = [k for k, v in ALL_SKILLS.items() if v["laterality_domain"] == "hand"]
        assert hand_skills == ["throwing"], (
            f"Expected [\"throwing\"], got {hand_skills}"
        )

    def test_none_count_is_23(self):
        """SC-LAT-07: exactly 23 non-lateral skills (heading + all mental + all physical)."""
        none_skills = {k for k, v in ALL_SKILLS.items() if v["laterality_domain"] == "none"}
        assert none_skills == _EXPECTED_NONE_SKILLS, (
            f"None skill mismatch.\n"
            f"  Extra:   {none_skills - _EXPECTED_NONE_SKILLS}\n"
            f"  Missing: {_EXPECTED_NONE_SKILLS - none_skills}"
        )

    def test_skill_laterality_covers_all_44_keys(self):
        """SC-LAT-08: SKILL_LATERALITY is a complete, consistent mirror of ALL_SKILLS."""
        assert len(SKILL_LATERALITY) == 44, (
            f"SKILL_LATERALITY has {len(SKILL_LATERALITY)} keys, expected 44"
        )
        assert set(SKILL_LATERALITY.keys()) == set(ALL_SKILLS.keys()), (
            "SKILL_LATERALITY key set differs from ALL_SKILLS key set"
        )
        mismatches = {
            k: (SKILL_LATERALITY[k], ALL_SKILLS[k]["laterality_domain"])
            for k in ALL_SKILLS
            if SKILL_LATERALITY[k] != ALL_SKILLS[k]["laterality_domain"]
        }
        assert not mismatches, f"SKILL_LATERALITY value mismatches: {mismatches}"
