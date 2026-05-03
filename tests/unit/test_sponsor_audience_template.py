"""
Sponsor audience list + detail templates — form structure and count regression tests.

  SAT-01  promote-form contains no nested <form> elements
  SAT-02  cleanup action forms (/suppress, /delete, /unlink) are outside the promote-form
  SAT-03  entry_ids checkboxes carry form="promote-form"
  SAT-04  promote submit button carries form="promote-form"
  SAT-05  sponsor_campaign_detail.html audience count uses active_entries (rejects DELETED)
  SAT-06  sponsor_campaign_detail.html raw campaign.entries|length not shown as active count

These tests parse the raw Jinja2 template files — no running server needed.
"""
import os
import re

TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../app/templates/admin/sponsor_audience_list.html",
)

CAMPAIGN_DETAIL_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../app/templates/admin/sponsor_campaign_detail.html",
)


def _load_template() -> str:
    with open(TEMPLATE_PATH, encoding="utf-8") as fh:
        return fh.read()


def _load_campaign_detail_template() -> str:
    with open(CAMPAIGN_DETAIL_TEMPLATE_PATH, encoding="utf-8") as fh:
        return fh.read()


def _promote_form_body(html: str) -> str:
    """Return the raw text between the opening of id="promote-form" and its first </form>."""
    start = html.index('id="promote-form"')
    end = html.index("</form>", start) + len("</form>")
    return html[start:end]


# ── SAT-01 ────────────────────────────────────────────────────────────────────

def test_sat_01_promote_form_has_no_nested_form():
    """The promote-form must close before any other <form> tag appears inside it."""
    body = _promote_form_body(_load_template())
    # Strip the opening tag itself; any subsequent <form is a nested form
    first_tag_end = body.index(">") + 1
    inner = body[first_tag_end:]
    assert "<form" not in inner, (
        "Nested <form> found inside #promote-form — cleanup forms must be standalone"
    )


# ── SAT-02 ────────────────────────────────────────────────────────────────────

def test_sat_02_cleanup_forms_outside_promote_form():
    """Cleanup action routes must not appear anywhere inside the promote-form body."""
    body = _promote_form_body(_load_template())
    for keyword in ("/suppress", "/delete", "/unlink"):
        assert keyword not in body, (
            f"Cleanup action '{keyword}' is inside #promote-form — "
            "it will submit the promote route instead of the cleanup route"
        )


# ── SAT-03 ────────────────────────────────────────────────────────────────────

def test_sat_03_entry_ids_checkbox_has_form_attribute():
    """Every entry_ids checkbox must carry form="promote-form" so it belongs to the
    promote form even though it is physically outside the <form> element."""
    html = _load_template()
    # Find all <input … name="entry_ids" … > tags in the template
    pattern = re.compile(r'<input\b[^>]*name="entry_ids"[^>]*>', re.DOTALL)
    matches = pattern.findall(html)
    assert matches, "No input[name=entry_ids] found in template"
    for tag in matches:
        assert 'form="promote-form"' in tag, (
            f"input[name=entry_ids] is missing form=\"promote-form\":\n{tag}"
        )


# ── SAT-04 ────────────────────────────────────────────────────────────────────

def test_sat_04_promote_button_has_form_attribute():
    """The sticky promote submit button must carry form="promote-form"."""
    html = _load_template()
    pattern = re.compile(r'<button\b[^>]*btn-promote[^>]*>', re.DOTALL)
    matches = pattern.findall(html)
    assert matches, "No button.btn-promote found in template"
    for tag in matches:
        assert 'form="promote-form"' in tag, (
            f"btn-promote is missing form=\"promote-form\":\n{tag}"
        )


# ── SAT-05 ────────────────────────────────────────────────────────────────────

def test_sat_05_detail_audience_count_uses_active_entries():
    """Campaign overview must display only active (non-DELETED) entry count.

    P3 moved the audience view from sponsor_detail to sponsor_campaign_detail.
    The Active Entries count must use active_entries|length (filtered via rejectattr),
    not the raw campaign.entries|length which includes soft-deleted entries.
    """
    html = _load_campaign_detail_template()
    # active_entries defined via rejectattr to exclude DELETED
    assert "rejectattr('status', 'equalto', 'DELETED')" in html, (
        "sponsor_campaign_detail.html must filter out DELETED entries via rejectattr"
    )
    # The count shown to users must use the filtered variable
    assert "active_entries|length" in html, (
        "Campaign overview must use active_entries|length, not campaign.entries|length"
    )
    # Raw unfiltered length must NOT be presented as the active entry count
    assert "campaign.entries|length" not in html, (
        "campaign.entries|length must not appear — it would include DELETED entries"
    )


# ── SAT-06 ────────────────────────────────────────────────────────────────────

def test_sat_06_campaign_detail_promoted_count_uses_filtered_set():
    """Promoted entry count must be derived from the campaign entries, not a raw count.

    In P3, all three counters (active, promoted, deleted) are computed from
    filtered Jinja2 sets; campaign.entries|length must not appear raw.
    """
    html = _load_campaign_detail_template()
    # promoted_entries must be built via selectattr on the relationship
    assert "promoted_entries" in html, (
        "sponsor_campaign_detail.html must define promoted_entries"
    )
    assert "promoted_entries|length" in html, (
        "Promoted count must use promoted_entries|length, not campaign.entries|length"
    )
    # No raw unfiltered count
    assert "campaign.entries|length" not in html, (
        "campaign.entries|length must not appear — DELETED entries would inflate it"
    )
