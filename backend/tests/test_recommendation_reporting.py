from openpyxl import load_workbook

from auditfast.core.enums import Layer, Pillar, Scope, Severity, Status
from auditfast.core.models import CheckResult
from auditfast.core.scoring import aggregate
from auditfast.reporting.excel import build_excel


def test_excel_wraps_evidence_and_recommendations(tmp_path):
    recommendation = (
        'Target: notebook "NB_Load" in workspace "Data Prep". '
        "Observed gap: no retry policy. Action: configure a bounded retry. "
        "Verification: re-run the audit."
    )
    result = CheckResult(
        check_id="NB-TEST",
        ref="T.1",
        title="Test finding",
        pillar=Pillar.OPERATIONS,
        status=Status.FAIL,
        score=0,
        evidence="No retry policy is configured.",
        recommendation=recommendation,
        severity=Severity.HIGH,
        workspace="Data Prep",
        layer=Layer.PREP,
        obj="NB_Load",
        scope=Scope.NOTEBOOK,
    )
    output = tmp_path / "audit.xlsx"

    build_excel(str(output), "Test project", aggregate([result]), [result])

    workbook = load_workbook(output)
    checks = workbook["Checks"]
    risks = workbook["Risk Register"]
    assert checks["L2"].alignment.wrap_text is True
    assert checks["M2"].alignment.wrap_text is True
    assert risks["G2"].alignment.wrap_text is True
    assert risks["H2"].alignment.wrap_text is True
    assert checks["M2"].value == recommendation
    assert risks["H2"].value == recommendation
