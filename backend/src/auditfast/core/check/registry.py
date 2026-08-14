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
from ..models import CheckContext, CheckOption, CheckSpec, GroupCheckSpec, GroupContext


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
    question: str = "",
    options: Sequence[CheckOption] = (),
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
        question: For an interactive check, the question shown to the reviewer.
        options: For an interactive check, the scored answers to choose between.
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
                question=question,
                options=tuple(options),
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


def questionnaire_check(
    *,
    id: str,
    ref: str,
    title: str,
    pillar: Pillar,
    options: Sequence[CheckOption],
    question: str = "",
    layers: Sequence[Layer] = (Layer.ANY,),
    scope: Scope = Scope.WORKSPACE,
    severity: Severity = Severity.MEDIUM,
    required: bool = True,
    registry: CheckRegistry | None = None,
) -> None:
    """Register an interactive, self-assessed checklist point.

    The point cannot be read from Fabric, but — unlike a plain manual attestation
    — it *can* be scored by asking the reviewer to pick one of ``options`` during
    the audit, exactly like the Azure Well-Architected Review questionnaire. The
    engine never runs it (``manual=True``); the chosen option's score is merged
    into the audit afterwards, per workspace whose layer the check applies to.

    Skipping is always available and records N/A (unscored); it does not need to
    be one of ``options``.
    """
    from .helpers import manual as _manual_verdict

    if not options:
        raise ValueError(f"interactive check {id!r} must declare at least one option")

    check(
        id=id,
        ref=ref,
        title=title,
        pillar=pillar,
        scope=scope,
        severity=severity,
        layers=layers,
        requires=(),
        required=required,
        manual=True,
        automation=Automation.INTERACTIVE,
        question=question or title,
        options=options,
        registry=registry,
    )(lambda ctx: _manual_verdict())


# -- cross-workspace (group) checks -------------------------------------------

class GroupCheckRegistry:
    """An ordered, id-keyed collection of :class:`GroupCheckSpec`.

    Kept separate from :class:`CheckRegistry` because a group check has a
    different signature (it takes a :class:`GroupContext`, not a
    :class:`CheckContext`) and a different dispatch (once per project group, not
    per object). Keeping them apart means the per-workspace engine loop can never
    accidentally run a group check, so single-workspace behaviour is untouched.
    """

    def __init__(self) -> None:
        self._specs: dict[str, GroupCheckSpec] = {}

    def register(self, spec: GroupCheckSpec) -> None:
        if spec.id in self._specs:
            raise DuplicateCheckError(
                f"group check id {spec.id!r} is already registered by "
                f"{self._specs[spec.id].fn.__module__}"
            )
        self._specs[spec.id] = spec

    def __len__(self) -> int:
        return len(self._specs)

    def __iter__(self):
        return iter(self._specs.values())

    def all(self) -> list[GroupCheckSpec]:
        return list(self._specs.values())

    def get(self, check_id: str) -> GroupCheckSpec | None:
        return self._specs.get(check_id)

    def select(self, *, pillars: Iterable[Pillar] | None = None) -> list[GroupCheckSpec]:
        """Return the group checks matching the pillar filter (None = all)."""
        wanted = set(pillars) if pillars is not None else None
        return [
            spec for spec in self._specs.values()
            if wanted is None or spec.pillar in wanted
        ]

    @staticmethod
    def required_resources(specs: Iterable[GroupCheckSpec]) -> set[Resource]:
        """Union of what the given group checks need fetched, per member."""
        needed: set[Resource] = set()
        for spec in specs:
            needed |= spec.requires
        return needed


#: The process-wide group-check registry. Empty until a group check is imported,
#: so an ordinary audit runs no cross-workspace checks and is unchanged.
GROUP_REGISTRY = GroupCheckRegistry()


def group_check(
    *,
    id: str,
    ref: str,
    title: str,
    pillar: Pillar,
    severity: Severity = Severity.MEDIUM,
    requires: Sequence[Resource] = (),
    weight: float = 1.0,
    description: str = "",
    required: bool = True,
    registry: GroupCheckRegistry | None = None,
) -> Callable:
    """Register a cross-workspace (group) check and return it unchanged.

    The decorated function takes a :class:`GroupContext` — the group's readable
    member workspaces, dev → prod — and returns a
    :class:`~auditfast.core.check.helpers.Verdict` for the project. It must obey
    the same rules as every check: pure, deterministic, and **N/A-not-FAIL** when
    the data needed to compare is missing.
    """

    def decorate(fn: Callable[[GroupContext], object]):
        target = registry if registry is not None else GROUP_REGISTRY
        target.register(
            GroupCheckSpec(
                id=id,
                ref=ref,
                title=title,
                pillar=pillar,
                fn=fn,
                severity=severity,
                requires=frozenset(requires),
                weight=weight,
                description=description,
                required=required,
            )
        )
        return fn

    return decorate

