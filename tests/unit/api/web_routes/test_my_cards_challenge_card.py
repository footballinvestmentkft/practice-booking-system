"""
MCC — My Cards Challenge Card template tests (CE-3.6-C).

Template source: app/templates/my_cards_challenge_card.html
Route: GET /my-cards/challenge

MCC-01  template contains /card-editor/challenge Studio entry CTA
MCC-02  Studio CTA text: "Open Challenge Studio"
MCC-03  template contains /challenges/results (per-format + footer CTA)
MCC-04  template contains /shop/cards/challenge (shop link + empty state)
MCC-05  Studio CTA is inside the owned {% if cc_format_rows %} block
MCC-06  template does not contain preview iframe or export link
"""
from pathlib import Path

_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[4]
    / "app" / "templates" / "my_cards_challenge_card.html"
)


def _src() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


# ── MCC-01: Studio entry CTA href ────────────────────────────────────────────

class TestMCC01StudioCTA:

    def test_mcc_01_template_has_studio_cta(self):
        """MCC-01: template contains /card-editor/challenge Studio entry CTA."""
        assert 'href="/card-editor/challenge"' in _src()

    def test_mcc_02_studio_cta_text(self):
        """MCC-02: Studio CTA label text is 'Open Challenge Studio'."""
        assert "Open Challenge Studio" in _src()


# ── MCC-03: /challenges/results links unchanged ───────────────────────────────

class TestMCC03ChallengeResultsLinks:

    def test_mcc_03_challenges_results_present(self):
        """MCC-03: /challenges/results appears in template (per-format + footer CTA)."""
        assert "/challenges/results" in _src()


# ── MCC-04: shop link unchanged ──────────────────────────────────────────────

class TestMCC04ShopLink:

    def test_mcc_04_shop_link_present(self):
        """MCC-04: /shop/cards/challenge appears in template (shop link + empty state)."""
        assert "/shop/cards/challenge" in _src()


# ── MCC-05: Studio CTA is inside owned block ─────────────────────────────────

class TestMCC05OwnedBlockContainsCTA:

    def test_mcc_05_studio_cta_inside_owned_block(self):
        """MCC-05: Studio CTA appears after {% if cc_format_rows %} and before {% else %}."""
        src = _src()
        owned_block_start = src.find("{% if cc_format_rows %}")
        else_marker       = src.find("{% else %}", owned_block_start)
        studio_cta_pos    = src.find('href="/card-editor/challenge"', owned_block_start)

        assert owned_block_start != -1, "{% if cc_format_rows %} block must exist"
        assert studio_cta_pos   != -1, "/card-editor/challenge link must exist"
        assert studio_cta_pos   <  else_marker, (
            "Studio CTA must be inside the owned branch (before {% else %})"
        )


# ── MCC-06: no preview iframe, no export link ─────────────────────────────────

class TestMCC06NoPreviewExport:

    def test_mcc_06_no_preview_iframe(self):
        """MCC-06: template must not contain a preview iframe."""
        assert "<iframe" not in _src()

    def test_mcc_06b_no_export_link(self):
        """MCC-06b: template must not contain a /card/export link."""
        assert "/card/export" not in _src()
        assert "export_url" not in _src()
