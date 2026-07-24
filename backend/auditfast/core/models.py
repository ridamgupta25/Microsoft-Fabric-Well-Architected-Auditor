"""Core data models for AuditFAST Core (Phase 1, rule-based, no AI)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Status(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NA = "N/A"
    INFO = "INFO"


class Severity(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INFO = "Informational"


# ---- Well-Architected pillars ------------------------------------------------
RELIABILITY = "Reliability"
SECURITY = "Security"
COST = "Cost Optimization"
OPEX = "Operational Excellence"
PERFORMANCE = "Performance Efficiency"
FOUNDATION = "Foundation"

# The five WAF pillars the user selects from (Foundation is cross-cutting/info).
PILLARS = [RELIABILITY, SECURITY, COST, OPEX, PERFORMANCE]

SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}


@dataclass
class CheckResult:
    """One deterministic best-practice check against one implemented object."""

    check_id: str
    ref: str  # existing checklist reference, e.g. "2.4.1"
    title: str
    pillar: str
    status: Status
    score: Optional[int] = None       # 0-3 (None for INFO/NA)
    coverage: Optional[float] = None  # 0..1 for portfolio checks
    evidence: str = ""
    recommendation: str = ""
    severity: Severity = Severity.MEDIUM
    workspace: str = ""
    workspace_role: str = ""
    obj: str = ""                     # object name (pipeline etc.); blank = workspace-level
    scored: bool = True

    MAX_SCORE = 3
