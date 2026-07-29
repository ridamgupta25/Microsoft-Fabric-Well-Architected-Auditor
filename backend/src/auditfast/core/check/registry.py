"""The check registry.

One registry holds every check, whatever kind of object it inspects. A check
declares its metadata up front via the :func:`check` decorator, so the catalog
can be listed, filtered, and costed *before* anything runs:

    @check(id="WS-GIT", ref="11.1.2", title="Git integration enabled",
           pillar=Pillar.OPEX, scope=Scope.WORKSPACE,
           severity=Severity.MEDIUM, requires=[Resource.GIT])
    def git_enabled(ctx: CheckContext) -> CheckResult:
        ...

This replaces the previous design, where two bare lists held naked functions and
a check's pillar/ref/severity were hardcoded inside its body — unknowable until
the check had already executed.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from ..enums import Automation, Layer, Pillar, Resource, Scope, Severity
from ..models import CheckContext, CheckSpec


class DuplicateCheckError(ValueError):
    """Raised when two checks claim the same id."""


class CheckRegistry:
    """An ordered, id-keyed collection of :class:`CheckSpec`."""

    def __init__(self) -> None:
        self._specs: dict[str, CheckSpec] = {}

    # -- population -----------------------------------------------------------
    def register(self, spec: CheckSpec) -> None:
        if spec.id in self._specs:
            raise DuplicateCheckError(
                f"check id {spec.id!r} is already registered by "
                f"{self._specs[spec.id].fn.__module__}"
            )
        self._specs[spec.id] = spec

    # -- reading --------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._specs)

    def __iter__(self):
        return iter(self._specs.values())

    def all(self) -> list[CheckSpec]:
        return list(self._specs.values())

    def get(self, check_id: str) -> CheckSpec | None:
        return self._specs.get(check_id)

    def select(
        self,
        *,
        pillars: Iterable[Pillar] | None = None,
        scope: Scope | None = None,
        layer: Layer | None = None,
        ids: Iterable[str] | None = None,
    ) -> list[CheckSpec]:
        """Return the checks matching every supplied filter.

        A filter left as ``None`` is not applied. ``layer`` matches checks
        registered for that layer *or* for :attr:`Layer.ANY`.
        """
        wanted_pillars = set(pillars) if pillars is not None else None
        wanted_ids = set(ids) if ids is not None else None

        out = []
        for spec in self._specs.values():
            if wanted_ids is not None and spec.id not in wanted_ids:
                continue
            if scope is not None and spec.scope is not scope:
                continue
            if wanted_pillars is not None and spec.pillar not in wanted_pillars:
                continue
            if layer is not None and not spec.applies_to(layer):
                continue
            out.append(spec)
        return out

    def scopes(self, specs: Sequence[CheckSpec] | None = None) -> list[Scope]:
        """The distinct scopes present, in :class:`Scope` declaration order."""
        present = {s.scope for s in (specs if specs is not None else self._specs.values())}
        return [scope for scope in Scope if scope in present]

    @staticmethod
    def required_resources(specs: Iterable[CheckSpec]) -> set[Resource]:
        """Union of what the given checks need fetched.

        The engine passes this to the provider so a run only pays for the data
        its selected checks actually read.
        """
        needed: set[Resource] = set()
        for spec in specs:
            needed |= spec.requires
        return needed


#: The process-wide registry. Populated by importing :mod:`auditfast.checks`.
REGISTRY = CheckRegistry()


def check(
    *,
    id: str,
    ref: str,
    title: str,
    pillar: Pillar,
    scope: Scope,
    severity: Severity = Severity.MEDIUM,
    layers: Sequence[Layer] = (Layer.ANY,),
    requires: Sequence[Resource] = (),
    weight: float = 1.0,
    description: str = "",
    required: bool = True,
    manual: bool = False,
    automation: Automation = Automation.AUTOMATED,
    registry: CheckRegistry | None = None,
) -> Callable:
    """Register a check function and return it unchanged.

    Args:
        id: Stable code, unique across the whole library (e.g. ``"PL-RETRY"``).
        ref: Audit-checklist reference — also the remediation lookup key.
        title: Human-readable name shown in reports.
        pillar: Which Well-Architected pillar this rolls up into.
        scope: What kind of object the check inspects.
        severity: Severity reported when the check does *not* pass.
        layers: Layer roles this applies to; ``Layer.ANY`` means all of them.
        requires: Data the provider must fetch for this check to work.
        weight: Relative influence on the roll-up. Defaults to ``1.0``, which
            reproduces a flat unweighted mean.
        description: Optional long text; falls back to the function docstring.
    """

    def decorate(fn: Callable[[CheckContext], object]):
        # `registry or REGISTRY` would be wrong: CheckRegistry defines __len__,
        # so an *empty* registry is falsy and a caller passing a fresh one would
        # silently register into the global registry instead.
        target = registry if registry is not None else REGISTRY
        target.register(
            CheckSpec(
                id=id,
                ref=ref,
                title=title,
                pillar=pillar,
                scope=scope,
                fn=fn,
                severity=severity,
                layers=frozenset(layers),
                requires=frozenset(requires),
                weight=weight,
                description=description,
                required=required,
                manual=manual,
                automation=automation,
            )
        )
        return fn

    return decorate


def manual_check(
    *,
    id: str,
    ref: str,
    title: str,
    pillar: Pillar,
    layers: Sequence[Layer] = (Layer.ANY,),
    required: bool = True,
    severity: Severity = Severity.MEDIUM,
    automation: Automation = Automation.MANUAL,
    registry: CheckRegistry | None = None,
) -> None:
    """Register a catalogue-only attestation check the engine never runs.

    Manual checks describe industry-standard points that are not verified from
    Fabric data today. ``automation`` records *why*: :attr:`Automation.ROADMAP`
    (automatable once the provider integrates the needed API) or
    :attr:`Automation.MANUAL` (only a human can attest). Either way the engine
    skips them (``manual=True``); they are scoped to the workspace purely for
    catalogue grouping.
    """
    from .helpers import manual as _manual_verdict

    check(
        id=id,
        ref=ref,
        title=title,
        pillar=pillar,
        scope=Scope.WORKSPACE,
        severity=severity,
        layers=layers,
        requires=(),
        required=required,
        manual=True,
        automation=automation,
        registry=registry,
    )(lambda ctx: _manual_verdict())
