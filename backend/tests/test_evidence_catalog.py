"""Milestone 1 — the evidence catalog loads, is well-formed, and unique."""
from __future__ import annotations

import pytest

from auditfast.ai.evidence_intake import catalog


def test_catalog_loads_the_tier_a_entries():
    entries = catalog.load()
    assert len(entries) == 45
    assert all(req.tier == "A" for req in entries.values())


def test_every_entry_has_a_four_rung_rubric():
    for req in catalog.load().values():
        assert set(req.rubric) == {"0", "1", "2", "3"}, req.ref
        assert all(text.strip() for text in req.rubric.values()), req.ref


def test_every_entry_asks_for_a_document_and_accepts_types():
    for req in catalog.load().values():
        assert req.ask_for.strip(), req.ref
        assert req.accepted_types, req.ref


def test_refs_are_unique_and_lookupable():
    entries = catalog.load()
    assert len(entries) == len({req.ref for req in entries.values()})
    assert catalog.get("13.2.1").title.startswith("Runbook")


def test_intake_prompt_names_the_ref_and_document():
    prompt = catalog.get("13.2.1").intake_prompt()
    assert "13.2.1" in prompt
    assert "runbook" in prompt.lower()


def test_unknown_ref_raises():
    with pytest.raises(KeyError):
        catalog.get("0.0.0")
