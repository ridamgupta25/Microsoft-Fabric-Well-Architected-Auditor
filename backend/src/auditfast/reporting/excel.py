"""Excel report generator (openpyxl): Scorecard, Checks, Risk Register sheets."""
from __future__ import annotations

from datetime import date

from ..core.enums import SEVERITY_RANK, Pillar, Status
from ..core.scoring import rating


def _pct(pct):
    return "N/A" if pct is None else round(pct, 1)


def build_excel(path: str, project_name: str, agg: dict, results: list, mode: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="305496")
    title_font = Font(bold=True, size=14)

    def style_header(ws, row=1, ncols=1):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=row, column=c)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center")

    # -- Scorecard sheet -------------------------------------------------------
    ws = wb.active
    ws.title = "Scorecard"
    ws["A1"] = "AuditFAST Core — Fabric Well-Architected Audit"
    ws["A1"].font = title_font
    ws["A2"] = f"Project: {project_name}"
    ws["A3"] = f"Date: {date.today().isoformat()}    Mode: {mode} (rule-based, no AI)"
    o_label, _ = rating(agg["overall"])
    ws["A5"] = "Overall score"
    ws["B5"] = _pct(agg["overall"])
    ws["C5"] = o_label
    for cell in ("A5", "B5", "C5"):
        ws[cell].font = Font(bold=True)

    ws["A7"] = "Pillar"
    ws["B7"] = "Score %"
    ws["C7"] = "Rating"
    ws["D7"] = "Checks"
    style_header(ws, row=7, ncols=4)
    r = 8
    for p in Pillar.scored():
        pv = agg["by_pillar"][p]
        lbl, _ = rating(pv["pct"])
        ws.cell(row=r, column=1, value=p)
        ws.cell(row=r, column=2, value=_pct(pv["pct"]))
        ws.cell(row=r, column=3, value=lbl if pv["count"] else "Phase 2 / Excel")
        ws.cell(row=r, column=4, value=pv["count"])
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Workspace").font = Font(bold=True)
    ws.cell(row=r, column=2, value="Layer role").font = Font(bold=True)
    ws.cell(row=r, column=3, value="Score %").font = Font(bold=True)
    ws.cell(row=r, column=4, value="Rating").font = Font(bold=True)
    style_header(ws, row=r, ncols=4)
    r += 1
    for wsn, wv in agg["by_workspace"].items():
        lbl, _ = rating(wv["pct"])
        ws.cell(row=r, column=1, value=wsn)
        ws.cell(row=r, column=2, value=wv.get("role", ""))
        ws.cell(row=r, column=3, value=_pct(wv["pct"]))
        ws.cell(row=r, column=4, value=lbl)
        r += 1
    for col, width in {"A": 34, "B": 22, "C": 18, "D": 10}.items():
        ws.column_dimensions[col].width = width

    # -- Checks sheet ----------------------------------------------------------
    cs = wb.create_sheet("Checks")
    headers = ["Workspace", "Layer role", "Object", "Check ID", "Ref", "Title",
               "Pillar", "Status", "Score", "Severity", "Evidence", "Recommendation"]
    cs.append(headers)
    style_header(cs, row=1, ncols=len(headers))
    for res in results:
        cs.append([
            res.workspace, res.workspace_role, res.obj, res.check_id, res.ref, res.title,
            res.pillar, res.status.value,
            "" if res.score is None else res.score,
            res.severity.value, res.evidence, res.recommendation,
        ])
    widths = [22, 16, 22, 14, 8, 34, 22, 10, 7, 12, 46, 52]
    for i, w in enumerate(widths, start=1):
        cs.column_dimensions[cs.cell(row=1, column=i).column_letter].width = w
    cs.freeze_panes = "A2"

    # -- Risk Register sheet ---------------------------------------------------
    rr = wb.create_sheet("Risk Register")
    rr_headers = ["Risk ID", "Severity", "Pillar", "Workspace", "Object",
                  "Finding", "Evidence", "Recommendation"]
    rr.append(rr_headers)
    style_header(rr, row=1, ncols=len(rr_headers))
    findings = [r for r in results if r.status in (Status.FAIL, Status.PARTIAL)]
    findings.sort(key=lambda r: (SEVERITY_RANK.get(r.severity, 9),
                                 r.score if r.score is not None else 9))
    for i, res in enumerate(findings, start=1):
        rr.append([
            f"R-{i:03d}", res.severity.value, res.pillar, res.workspace, res.obj,
            f"{res.title} ({res.status.value})", res.evidence, res.recommendation,
        ])
    widths = [10, 12, 22, 22, 22, 40, 46, 52]
    for i, w in enumerate(widths, start=1):
        rr.column_dimensions[rr.cell(row=1, column=i).column_letter].width = w
    rr.freeze_panes = "A2"

    wb.save(path)
