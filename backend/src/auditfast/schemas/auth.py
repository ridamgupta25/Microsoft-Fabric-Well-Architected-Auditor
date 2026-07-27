"""Schemas for read-only Microsoft Entra sign-in.

Every flow here requests **read-only** Fabric scopes. Tokens are never returned
to the client — the client holds an opaque session id and the server keeps the
token, so a compromised browser never yields a Fabric access token.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SignInStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    ERROR = "error"


class LoginRequest(BaseModel):
    """Start an interactive browser sign-in."""

    email: str | None = Field(default=None, description="Pre-fills the Microsoft login.")
    tenant_id: str | None = None
    client_id: str | None = Field(
        default=None,
        description="Entra app client id. When absent, Microsoft's first-party "
                    "Azure CLI client is used so no app registration is needed.",
    )


class DeviceFlowRequest(BaseModel):
    """Start a device-code sign-in, for headless environments."""

    tenant_id: str | None = None
    client_id: str | None = None
    scopes: list[str] = Field(default_factory=list)


class SessionResponse(BaseModel):
    """An opaque session the client polls until sign-in completes."""

    session: str
    message: str
    status: SignInStatus = SignInStatus.PENDING
    user_code: str | None = Field(default=None, description="Device-code flow only.")
    verification_uri: str | None = None
    expires_in: int | None = None


class SessionStatus(BaseModel):
    status: SignInStatus
    error: str | None = None


class DiagnosticSample(BaseModel):
    """What the token could read for one workspace."""

    name: str
    items_status: int | None = None
    items: int = 0
    pipelines: int = 0
    roles_status: int | None = None


class DiagnosticsResponse(BaseModel):
    """Per-resource HTTP status codes, so partial permissions are visible."""

    list_status: int | None = None
    count: int = 0
    samples: list[DiagnosticSample] = Field(default_factory=list)
    error: str | None = None
