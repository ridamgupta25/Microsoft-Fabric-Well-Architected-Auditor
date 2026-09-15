"""Manual-checks memory: record, recall approvals, and scope by workspace."""
from __future__ import annotations

from auditfast.services.evidence_memory import EvidenceMemory


def _rows():
    return [
        {"ref": "13.2.1", "title": "Runbook", "score": 3, "rung_matched": "3",
         "citations": [], "recommendations": [], "source": "ai"},
        {"ref": "5.1.1", "title": "DQ", "score": 2, "rung_matched": "2",
         "citations": [], "recommendations": [], "source": "manual"},
    ]


def test_record_and_approved_for_scope(tmp_path):
    mem = EvidenceMemory(tmp_path / "mem.json")
    mem.record(_rows(), ["WS-A"])
    # Same workspace -> both recalled.
    got = mem.approved_for(["WS-A"])
    assert {r["ref"] for r in got} == {"13.2.1", "5.1.1"}
    # A different workspace -> not recalled (scope does not intersect).
    assert mem.approved_for(["WS-B"]) == []


def test_previously_approved_only_within_scope(tmp_path):
    mem = EvidenceMemory(tmp_path / "mem.json")
    mem.record(_rows(), ["WS-A", "WS-B"])
    assert mem.previously_approved(["WS-A"]) == {"13.2.1", "5.1.1"}
    assert mem.previously_approved(["WS-C"]) == set()
    # Selection must be WITHIN the approved scope.
    assert mem.previously_approved(["WS-A", "WS-B"]) == {"13.2.1", "5.1.1"}


def test_record_skips_rows_without_score(tmp_path):
    mem = EvidenceMemory(tmp_path / "mem.json")
    mem.record([{"ref": "9.9.9", "score": None}], ["WS-A"])
    assert mem.approved_for(["WS-A"]) == []


def test_service_report_and_recall(tmp_path, monkeypatch):
    from auditfast.services import evidence_checks_service

    monkeypatch.setattr(evidence_checks_service, "_memory", lambda: EvidenceMemory(tmp_path / "m.json"))
    evidence_checks_service.save_approved(_rows(), ["WS-A"])
    section = evidence_checks_service.approved_evidence_report(["WS-A"])
    assert section and {c["ref"] for c in section["checks"]} == {"13.2.1", "5.1.1"}
    assert evidence_checks_service.approved_evidence_report(["WS-Z"]) is None
