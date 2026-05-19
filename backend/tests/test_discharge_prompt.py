"""Locks the discharge_summary prompt contract: required section headings
must appear in the system prompt so a future refactor can't silently drop
them."""
from __future__ import annotations


REQUIRED_SECTIONS = [
    "## Reason for admission",
    "## Past medical history",
    "## Home medications on admission",
    "## Hospital course",
    "## Discharge medications",
    "## Follow-up plan",
    "## Safety notes",
]


def test_discharge_summary_prompt_has_all_required_sections():
    from app.prompts.templates import summary_system_for
    prompt = summary_system_for("discharge_summary")
    for section in REQUIRED_SECTIONS:
        assert section in prompt, f"missing required section heading: {section!r}"


def test_detailed_summary_falls_back_to_legacy_prompt():
    from app.prompts.templates import summary_system_for
    prompt = summary_system_for("detailed")
    assert "Reason for admission" not in prompt, (
        "detailed prompt should remain free-form, not adopt discharge structure"
    )
