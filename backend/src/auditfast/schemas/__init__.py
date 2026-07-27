"""Pydantic request/response models — the API's public contract.

Kept separate from :mod:`auditfast.core` models on purpose: the domain must be
free to change shape without breaking API consumers, and the API must be free to
present a different shape from the internals. The two meet only in the routers.
"""
from .audit import (
    AuditAccepted,
    AuditJobOut,
    AuditJobSummary,
    AuditMode,
    AuditReport,
    AuditRequest,
    CheckResultOut,
    JobStatus,
    SingleCheckRequest,
    WorkspaceOut,
    WorkspaceSelection,
)
from .auth import (
    DeviceFlowRequest,
    DiagnosticsResponse,
    LoginRequest,
    SessionResponse,
    SessionStatus,
    SignInStatus,
)
from .catalog import CatalogSummary, CheckSpecOut, LayerOut, PillarOut
from .common import ErrorResponse, HealthResponse, HealthStatus, Message, Page

__all__ = [
    "AuditAccepted",
    "AuditJobOut",
    "AuditJobSummary",
    "AuditMode",
    "AuditReport",
    "AuditRequest",
    "CatalogSummary",
    "CheckResultOut",
    "CheckSpecOut",
    "DeviceFlowRequest",
    "DiagnosticsResponse",
    "ErrorResponse",
    "HealthResponse",
    "HealthStatus",
    "JobStatus",
    "LayerOut",
    "LoginRequest",
    "Message",
    "Page",
    "PillarOut",
    "SessionResponse",
    "SessionStatus",
    "SignInStatus",
    "SingleCheckRequest",
    "WorkspaceOut",
    "WorkspaceSelection",
]
