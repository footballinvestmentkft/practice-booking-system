"""
Unit tests for GET /profile/onboarding-card (Phase C — Welcome Card preview).

Route: app/api/web_routes/profile.onboarding_welcome_card
Template: app/templates/public/welcome_card.html

Test groups:
  TestWelcomeCardRoute   — route-level: auth redirects, no-license redirect,
                           incomplete-onboarding redirect, 200 happy path,
                           context keys present, only self_assessment read
  TestWelcomeCardTemplate — static template assertions: required strings present,
                            forbidden EMA field names absent,
                            self_assessment rendered in skill rows
"""
import asyncio
import pathlib
import re
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import RedirectResponse

from app.api.web_routes.profile import onboarding_welcome_card
from app.models.user import UserRole

_BASE = "app.api.web_routes.profile"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    return MagicMock()


def _user(uid=10, role=UserRole.STUDENT):
    u = MagicMock()
    u.id = uid
    u.role = role
    u.email = "player@test.com"
    u.name = "Test Player"
    u.nickname = "tester"
    u.nationality = "HU"
    u.secondary_nationality = None
    u.credit_balance = 500
    return u


def _license(onboarding_completed=True, football_skills=None):
    lic = MagicMock()
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.onboarding_completed = onboarding_completed
    lic.player_card_photo_url = None
    lic.right_foot_score = 70.0
    lic.left_foot_score = 30.0
    lic.motivation_scores = {
        "position":     "striker",
        "positions":    ["striker"],
        "height_cm":    178,
        "weight_kg":    74,
        "preferred_foot": "right",
        "goals":        "become_professional",
    }
    # Build football_skills with self_assessment values for all 44 keys
    # (including Phase 3 new skills: throwing, anticipation)
    if football_skills is None:
        from app.skills_config import get_all_skill_keys
        football_skills = {
            key: {
                "self_assessment":  65.0,
                "current_level":    60.0,   # must NOT appear in rendered output
                "system_baseline":  60.0,   # must NOT appear in rendered output
                "baseline":         60.0,
                "tournament_delta": 0.0,    # must NOT appear in rendered output
                "assessment_delta": 0.0,    # must NOT appear in rendered output
            }
            for key in get_all_skill_keys()
        }
    lic.football_skills = football_skills
    lic.average_motivation_score = 65.0
    return lic


def _mock_db(license_return=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = license_return
    return db


# ── Route tests ───────────────────────────────────────────────────────────────

class TestWelcomeCardRoute:

    def test_no_license_redirects_to_dashboard(self):
        user = _user()
        db = _mock_db(license_return=None)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_incomplete_onboarding_redirects_to_onboarding(self):
        user = _user()
        lic = _license(onboarding_completed=False)
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        assert isinstance(result, RedirectResponse)
        assert "onboarding" in result.headers["location"]

    def test_completed_onboarding_renders_template(self):
        user = _user()
        lic = _license(onboarding_completed=True)
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        tmpl, _ = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "public/welcome_card.html"

    def test_context_contains_required_keys(self):
        user = _user()
        lic = _license()
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        for key in ("skill_categories", "overall_sa", "top_skills", "position",
                    "height_cm", "weight_kg", "preferred_foot", "display_name", "initials"):
            assert key in ctx, f"Missing context key: {key}"

    def test_top_skills_are_five(self):
        user = _user()
        lic = _license()
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert len(ctx["top_skills"]) == 5

    def test_skill_categories_count_matches_taxonomy(self):
        user = _user()
        lic = _license()
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        from app.skills_config import SKILL_CATEGORIES
        assert len(ctx["skill_categories"]) == len(SKILL_CATEGORIES)

    def test_context_reads_only_self_assessment_not_current_level(self):
        """Verify that skill values in the context come from self_assessment,
        not current_level, even when they differ."""
        user = _user()
        from app.skills_config import get_all_skill_keys
        # self_assessment=80, current_level=50 — context must show 80
        skills = {
            key: {
                "self_assessment":  80.0,
                "current_level":    50.0,
                "system_baseline":  60.0,
                "baseline":         60.0,
                "tournament_delta": 2.5,
                "assessment_delta": 1.0,
            }
            for key in get_all_skill_keys()
        }
        lic = _license(football_skills=skills)
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        # Every skill in every category must have value=80 (self_assessment), not 50
        for cat in ctx["skill_categories"]:
            for skill in cat["skills"]:
                assert skill["value"] == 80.0, (
                    f"Skill {skill['key']} value={skill['value']}, expected 80 (self_assessment)"
                )

    def test_overall_sa_is_mean_of_self_assessments(self):
        user = _user()
        from app.skills_config import get_all_skill_keys
        keys = get_all_skill_keys()
        skills = {key: {"self_assessment": 70.0, "current_level": 60.0} for key in keys}
        lic = _license(football_skills=skills)
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx["overall_sa"] == 70.0

    def test_physical_fields_come_from_motivation_scores(self):
        user = _user()
        lic = _license()
        lic.motivation_scores = {
            "position": "goalkeeper", "positions": ["goalkeeper"],
            "height_cm": 190, "weight_kg": 85, "preferred_foot": "left", "goals": "fun",
        }
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx["height_cm"] == 190
        assert ctx["weight_kg"] == 85
        assert ctx["preferred_foot"] == "left"
        assert ctx["position"] == "goalkeeper"

    def test_new_phase3_skills_present_in_context(self):
        """throwing (outfield) and anticipation (mental) must be in skill categories."""
        user = _user()
        lic = _license()
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        all_keys = {s["key"] for cat in ctx["skill_categories"] for s in cat["skills"]}
        assert "throwing"     in all_keys, "Phase 3 outfield skill 'throwing' missing"
        assert "anticipation" in all_keys, "Phase 3 mental skill 'anticipation' missing"

    def test_initials_derived_from_user_name(self):
        user = _user()
        user.name = "Lionel Messi"
        lic = _license()
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(request=_req(), db=db, user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx["initials"] == "LM"


# ── Template static assertions ────────────────────────────────────────────────

_TEMPLATE_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "public" / "welcome_card.html"
)


@pytest.fixture(scope="module")
def wc_template_src():
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


class TestWelcomeCardTemplate:
    """Static analysis of welcome_card.html — no rendering required."""

    def test_template_file_exists(self):
        assert _TEMPLATE_PATH.exists(), "welcome_card.html not found"

    def test_contains_welcome_card_text(self, wc_template_src):
        assert "Welcome Card" in wc_template_src

    def test_contains_self_assessment_text(self, wc_template_src):
        assert "Self-Assessment" in wc_template_src or "self_assessment" in wc_template_src

    def test_contains_disclaimer_text(self, wc_template_src):
        assert "onboarding self-assessment" in wc_template_src.lower() or \
               "not calculated player level" in wc_template_src

    def test_has_noindex_meta_tag(self, wc_template_src):
        assert "noindex" in wc_template_src

    def test_renders_skill_key_variable(self, wc_template_src):
        """Template must iterate and render skill.key (data-skill attribute)."""
        assert "skill.key" in wc_template_src

    def test_renders_self_assessment_value_not_current_level(self, wc_template_src):
        """Template renders skill.value (= self_assessment). Must NOT reference
        current_level, system_baseline, tournament_delta, or assessment_delta."""
        assert "current_level"    not in wc_template_src
        assert "system_baseline"  not in wc_template_src
        assert "tournament_delta" not in wc_template_src
        assert "assessment_delta" not in wc_template_src

    def test_renders_overall_sa_variable(self, wc_template_src):
        assert "overall_sa" in wc_template_src

    def test_renders_position_variable(self, wc_template_src):
        assert "position" in wc_template_src

    def test_renders_physical_fields(self, wc_template_src):
        assert "height_cm"  in wc_template_src
        assert "weight_kg"  in wc_template_src
        assert "preferred_foot" in wc_template_src

    def test_renders_skill_categories_loop(self, wc_template_src):
        assert "skill_categories" in wc_template_src
        assert "for cat in skill_categories" in wc_template_src or \
               "for cat in skill_categories" in wc_template_src

    def test_renders_top_skills_loop(self, wc_template_src):
        assert "top_skills" in wc_template_src
        assert "for skill in top_skills" in wc_template_src

    def test_back_link_to_profile(self, wc_template_src):
        assert "href=\"/profile\"" in wc_template_src or "href='/profile'" in wc_template_src


class TestWelcomeCardRendered:
    """Render the template with Jinja2 using a mock context and assert output."""

    @pytest.fixture(scope="class")
    def rendered_html(self):
        from jinja2 import Environment, FileSystemLoader, Undefined
        template_dir = str(_TEMPLATE_PATH.parents[1])  # app/templates
        env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=True,
            undefined=Undefined,
        )
        # Minimal mock context
        context = {
            "request":          MagicMock(),
            "user":             MagicMock(name="Test Player", nickname="tester",
                                          nationality="HU", secondary_nationality=None),
            "license":          MagicMock(player_card_photo_url=None,
                                          right_foot_score=70.0, left_foot_score=30.0),
            "display_name":     "Test Player",
            "initials":         "TP",
            "overall_sa":       65.0,
            "position":         "striker",
            "positions":        ["striker"],
            "height_cm":        178,
            "weight_kg":        74,
            "preferred_foot":   "right",
            "goals":            "become_professional",
            "right_foot_score": 70.0,
            "left_foot_score":  30.0,
            "top_skills": [
                {"key": "ball_control", "name_en": "Ball Control",  "value": 80.0},
                {"key": "throwing",     "name_en": "Throwing",      "value": 78.0},
                {"key": "anticipation", "name_en": "Anticipation",  "value": 75.0},
                {"key": "dribbling",    "name_en": "Dribbling",     "value": 72.0},
                {"key": "finishing",    "name_en": "Finishing",     "value": 70.0},
            ],
            "skill_categories": [
                {
                    "key": "outfield", "name_en": "Outfield", "name_hu": "Mezőnyjáték",
                    "emoji": "🟦", "avg": 65.0,
                    "skills": [
                        {"key": "ball_control", "name_en": "Ball Control", "name_hu": "Labdakontroll", "value": 80.0},
                        {"key": "throwing",     "name_en": "Throwing",     "name_hu": "Dobás",         "value": 78.0},
                    ],
                },
                {
                    "key": "mental", "name_en": "Mental", "name_hu": "Mentális",
                    "emoji": "🧠", "avg": 65.0,
                    "skills": [
                        {"key": "anticipation", "name_en": "Anticipation", "name_hu": "Anticipáció", "value": 75.0},
                    ],
                },
            ],
        }
        tpl = env.get_template("public/welcome_card.html")
        return tpl.render(**context)

    def test_html_contains_welcome_card(self, rendered_html):
        assert "Welcome Card" in rendered_html

    def test_html_contains_self_assessment(self, rendered_html):
        assert "Self-Assessment" in rendered_html or "self-assessment" in rendered_html.lower()

    def test_html_contains_new_outfield_skill_throwing(self, rendered_html):
        assert "throwing" in rendered_html.lower() or "Throwing" in rendered_html

    def test_html_contains_new_mental_skill_anticipation(self, rendered_html):
        assert "anticipation" in rendered_html.lower() or "Anticipation" in rendered_html

    def test_html_does_not_contain_current_level(self, rendered_html):
        assert "current_level" not in rendered_html

    def test_html_does_not_contain_system_baseline(self, rendered_html):
        assert "system_baseline" not in rendered_html

    def test_html_does_not_contain_tournament_delta(self, rendered_html):
        assert "tournament_delta" not in rendered_html

    def test_html_does_not_contain_assessment_delta(self, rendered_html):
        assert "assessment_delta" not in rendered_html

    def test_html_contains_overall_sa_value(self, rendered_html):
        assert "65" in rendered_html  # overall_sa=65.0

    def test_html_contains_player_name(self, rendered_html):
        assert "Test Player" in rendered_html

    def test_html_contains_disclaimer(self, rendered_html):
        assert "onboarding self-assessment" in rendered_html.lower()

    def test_html_has_noindex(self, rendered_html):
        assert "noindex" in rendered_html

    def test_data_skill_attributes_present(self, rendered_html):
        """Skill rows must have data-skill attributes with the skill key."""
        assert 'data-skill="ball_control"' in rendered_html
        assert 'data-skill="throwing"'     in rendered_html
        assert 'data-skill="anticipation"' in rendered_html


class TestWelcomeCardStep7Integration:
    """Verify that the step-7 HTML in the onboarding template has the View Welcome Card CTA."""

    @pytest.fixture(scope="class")
    def onboarding_src(self):
        path = (
            pathlib.Path(__file__).resolve().parents[4]
            / "app" / "templates" / "lfa_player_onboarding.html"
        )
        return path.read_text(encoding="utf-8")

    def test_view_welcome_card_link_present(self, onboarding_src):
        assert 'id="btn-view-welcome-card"' in onboarding_src

    def test_view_welcome_card_href_correct(self, onboarding_src):
        assert 'href="/profile/onboarding-card"' in onboarding_src

    def test_view_welcome_card_opens_in_new_tab(self, onboarding_src):
        assert 'target="_blank"' in onboarding_src

    def test_download_placeholder_still_disabled(self, onboarding_src):
        assert "btn-disabled-placeholder" in onboarding_src
        assert "Download Welcome Card" in onboarding_src
