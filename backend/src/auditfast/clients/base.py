"""The provider contract.

A provider turns some external system into a
:class:`~auditfast.domain.models.WorkspaceContext`. That single method is the
whole interface — implement it and the entire check library, engine, scoring, and
reporting stack works against your data source unchanged.

Providers receive the set of :class:`~auditfast.domain.enums.Resource` values the
selected checks actually need, so an implementation can skip expensive calls
nobody is going to read.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from ..core.enums import Layer, Resource
from ..core.models import WorkspaceContext

#: Everything a provider could be asked for — the default when a caller does not
#: narrow the request.
ALL_RESOURCES: frozenset[Resource] = frozenset(Resource)


@runtime_checkable
class Provider(Protocol):
    """Read-only access to one Fabric tenant (or a stand-in for one)."""

    def fetch(
        self,
        workspace_id: str,
        layer: Layer = Layer.MIXED,
        resources: Iterable[Resource] = ALL_RESOURCES,
    ) -> WorkspaceContext:
        """Return a normalized snapshot of one workspace.

        Raises:
            WorkspaceAccessError: the workspace is missing, forbidden, or the
                source could not be reached. Never return a half-empty context to
                signal failure — the engine relies on the exception to tell a
                genuine "no access" apart from a genuine "nothing configured".
        """
        ...

    def list_workspaces(self) -> list[dict]:
        """Enumerate the workspaces this provider can see.

        Returns rows of ``{id, name, layer, items, pipelines}``; counts may be
        ``None`` when the provider cannot know them cheaply.
        """
        ...
