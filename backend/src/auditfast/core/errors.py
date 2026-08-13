"""Provider failures the pure engine can classify without importing adapters."""
from __future__ import annotations


class ProviderError(Exception):
    """Base class for anything that stops a provider returning a context."""


class WorkspaceAccessError(ProviderError):
    """A workspace could not be read — missing, forbidden, or unreachable."""

    def __init__(self, workspace_id: str, status: int | None = None):
        self.workspace_id = workspace_id
        self.status = status
        super().__init__(self.friendly_message())

    def friendly_message(self) -> str:
        """Plain-English guidance, written for the person running the audit."""
        if self.status == 401:
            return (
                "Sign-in/token problem (HTTP 401): the token was rejected or has "
                "expired. Sign in again, then retry."
            )
        if self.status == 403:
            return (
                "Access denied (HTTP 403): the signed-in user does not have access "
                "to this workspace. Ask for at least a Viewer role, then retry."
            )
        if self.status == 404:
            return (
                "Not found (HTTP 404): no workspace with this name/ID is visible to "
                "you. Use “Load my Fabric workspaces” to pick the right one."
            )
        if self.status is None:
            return "Could not reach Fabric to read this workspace (network/connection error)."
        return f"Fabric returned HTTP {self.status} for this workspace, so it was not audited."
