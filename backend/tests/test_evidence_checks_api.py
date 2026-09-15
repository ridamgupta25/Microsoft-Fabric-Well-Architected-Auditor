"""Evidence-checks API tests — catalog listing, upload drafting, approval flow.

AI is off in tests, so an upload with no injected model drafts nothing (every row
is AI_REQUIRED). That keeps the endpoint assertions deterministic without a key.
"""
from __future__ import annotations

import io


def _docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_catalog_lists_the_intake_form(client):
    body = client.get("/api/v1/evidence-checks/catalog").json()
    assert len(body) == 45
    entry = next(e for e in body if e["ref"] == "13.2.1")
    assert entry["ask_for"]
    assert set(entry["rubric"]) == {"0", "1", "2", "3"}


def test_upload_requires_a_document(client):
    resp = client.post("/api/v1/evidence-checks", data={"refs": "[]"})
    assert resp.status_code == 422


def test_upload_returns_a_ledger(client):
    files = {"files": ("runbook.md", b"# Runbook\nDaily and weekly tasks.", "text/markdown")}
    resp = client.post(
        "/api/v1/evidence-checks",
        files=files,
        data={"refs": '["13.2.1"]'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["files"] == 1
    assert body["checks"] == 1
    assert body["ledger"][0]["ref"] == "13.2.1"
    # AI off in tests -> the check cannot be drafted, never guessed.
    assert body["ledger"][0]["status"] == "AI_REQUIRED"
    assert "Document-evidenced checks" in body["report_markdown"]


def test_unknown_ref_is_ignored(client):
    files = {"files": ("doc.md", b"content", "text/markdown")}
    resp = client.post(
        "/api/v1/evidence-checks",
        files=files,
        data={"refs": '["0.0.0"]'},
    )
    assert resp.status_code == 200
    assert resp.json()["checks"] == 0


def test_unsupported_file_is_skipped(client):
    files = {"files": ("image.png", b"\x89PNG", "image/png")}
    resp = client.post(
        "/api/v1/evidence-checks",
        files=files,
        data={"refs": '["13.2.1"]'},
    )
    assert resp.status_code == 200
    assert "image.png" in resp.json()["skipped_files"]


def test_docx_upload_is_parsed(client):
    files = {"files": ("dq.docx", _docx_bytes(["DQ framework with rules and ownership."]), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    resp = client.post(
        "/api/v1/evidence-checks",
        files=files,
        data={"refs": '["5.1.1"]'},
    )
    assert resp.status_code == 200
    assert resp.json()["files"] == 1
