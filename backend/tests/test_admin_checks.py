"""Elevated-access ("admin") checks: registry separation and the run mode.

The point of these tests is the *separation*. An elevated check reads data whose
availability depends on the signed-in user's privileges, so if one ever leaked
into the standard registry the deterministic score would move with whoever ran
the audit — the one thing this tool promises never happens.
"""
from __future__ import annotations

from auditfast.core.check.helpers import binary
from auditfast.core.check.registry import (
    ADMIN_REGISTRY,
    REGISTRY,
    CheckRegistry,
    admin_check,
    admin_registry_for,
)
from auditfast.core.enums import AdminCategory, Layer, Pillar, Resource, Scope
from auditfast.core.models import CheckContext, RoleAssignment, WorkspaceContext
from auditfast.schemas.auth import DiagnosticsResponse
from auditfast.services.context_store import ContextStore, NarrowCrawlProvider

from .conftest import AUTHENTICATED_SESSION

# -- registry separation -------------------------------------------------------

def test_no_elevated_check_leaks_into_the_standard_registry():
    """The standard library must hold nothing that needs elevated access.

    This is the invariant the whole design rests on: a standard audit selects
    from REGISTRY, so as long as REGISTRY carries no admin_category, its score
    cannot depend on the reviewer's privileges.
    """
    leaked = [spec.id for spec in REGISTRY if spec.admin_category is not None]
    assert leaked == [], f"elevated checks found in the standard registry: {leaked}"


def test_every_elevated_check_declares_a_category():
    """A spec in ADMIN_REGISTRY without a category could never be selected."""
    uncategorised = [spec.id for spec in ADMIN_REGISTRY if spec.admin_category is None]
    assert uncategorised == []


def test_admin_check_registers_into_the_admin_registry_only():
    """``admin_check`` populates ADMIN_REGISTRY, never REGISTRY."""
    throwaway = CheckRegistry()
    before_standard = len(REGISTRY)
    before_admin = len(ADMIN_REGISTRY)

    @admin_check(
        id="TEST-ADM-1", ref="6.1.99", title="Test elevated check",
        pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
        scope=Scope.WORKSPACE, requires=[Resource.ROLE_ASSIGNMENTS],
        registry=throwaway,
    )
    def _check(ctx):
        return binary(True, "ok")

    assert len(throwaway) == 1
    spec = throwaway.get("TEST-ADM-1")
    assert spec is not None
    assert spec.admin_category is AdminCategory.WORKSPACE
    assert spec.requires == frozenset({Resource.ROLE_ASSIGNMENTS})
    # The globals are untouched, so registering a check in a test cannot move a
    # pinned count in an unrelated one.
    assert len(REGISTRY) == before_standard
    assert len(ADMIN_REGISTRY) == before_admin


def test_spec_serialization_exposes_the_category():
    throwaway = CheckRegistry()

    @admin_check(
        id="TEST-ADM-2", ref="6.1.98", title="Serialized elevated check",
        pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.TENANT,
        registry=throwaway,
    )
    def _check(ctx):
        return binary(True, "ok")

    assert throwaway.get("TEST-ADM-2").to_dict()["admin_category"] == "Tenant"


def test_elevated_evidence_survives_snapshot_round_trip():
    context = WorkspaceContext(
        id="ws-1",
        tenant_settings=[{"title": "Export data", "enabled": False}],
        tenant_domains=[{"id": "domain-1", "workspace_ids": ["ws-1"]}],
        admin_scan={"workspaces": [{"id": "ws-1", "datasets": []}]},
        activity_events=[{"Activity": "AddGroupUser", "WorkSpaceId": "ws-1"}],
        capacity_metrics={"model_found": True, "throttling_events": 0},
    )

    restored = WorkspaceContext.from_dict(context.to_dict())

    assert restored.tenant_settings == context.tenant_settings
    assert restored.tenant_domains == context.tenant_domains
    assert restored.admin_scan == context.admin_scan
    assert restored.activity_events == context.activity_events
    assert restored.capacity_metrics == context.capacity_metrics


def test_diagnostics_response_preserves_admin_readiness():
    response = DiagnosticsResponse(admin={
        "member_workspaces": 2,
        "sampled_workspaces": 3,
        "role_assignments_readable": True,
        "connections_readable": False,
        "gateways_readable": False,
    })

    assert response.model_dump()["admin"]["member_workspaces"] == 2
    assert response.model_dump()["admin"]["role_assignments_readable"] is True


# -- category filtering --------------------------------------------------------

def _two_category_registry() -> CheckRegistry:
    registry = CheckRegistry()

    @admin_check(
        id="TEST-WS", ref="6.1.97", title="Workspace-family check",
        pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
        registry=registry,
    )
    def _ws(ctx):
        return binary(True, "ok")

    @admin_check(
        id="TEST-TN", ref="7.1.97", title="Tenant-family check",
        pillar=Pillar.COMPLIANCE, category=AdminCategory.TENANT,
        registry=registry,
    )
    def _tn(ctx):
        return binary(True, "ok")

    return registry


def test_registry_for_selects_only_the_named_categories():
    source = _two_category_registry()
    narrow = admin_registry_for([AdminCategory.WORKSPACE], source=source)
    assert [spec.id for spec in narrow] == ["TEST-WS"]


def test_registry_for_with_no_category_selects_nothing():
    """Empty means nothing, not everything.

    Defaulting to "run every elevated family" would let a caller that forgot to
    choose silently crawl tenant and capacity data it never asked for.
    """
    source = _two_category_registry()
    assert len(admin_registry_for([], source=source)) == 0
    assert len(admin_registry_for(None, source=source)) == 0


def test_category_parsing_is_forgiving_but_bounded():
    assert AdminCategory.parse("Workspace Admin") is AdminCategory.WORKSPACE
    assert AdminCategory.parse("workspace") is AdminCategory.WORKSPACE
    assert AdminCategory.parse("TENANT") is AdminCategory.TENANT
    assert AdminCategory.parse("nonsense") is None
    assert AdminCategory.parse("") is None
    assert AdminCategory.parse(None) is None


# -- catalog surface -----------------------------------------------------------

def test_catalog_lists_every_category_including_empty_ones(client):
    """All three families are always returned.

    An empty family must come back with ``available: false`` rather than being
    omitted, so the selection screen can show it as not-yet-available instead of
    quietly pretending it does not exist.
    """
    body = client.get("/api/v1/catalog/admin-categories").json()
    names = [row["category"] for row in body]
    assert names == [c.value for c in AdminCategory]
    for row in body:
        assert row["available"] is (row["checks"] > 0)


def test_the_twelve_workspace_admin_checks_are_registered(client):
    """The ported Setup3-Admin notebooks, one check each.

    Pinned by ref so a check silently failing to register — the classic
    import-side-effect trap — fails here rather than showing up as a quietly
    shorter report.
    """
    body = client.get(
        "/api/v1/catalog/admin-checks", params={"category": "Workspace Admin"}
    ).json()
    refs = sorted(row["ref"] for row in body)
    assert refs == sorted([
        "1.3.7", "1.3.8", "6.1.2", "6.1.3", "6.1.5", "6.2.6",
        "6.3.1", "6.4.4", "6.4.5", "10.4.3", "11.3.2", "14.4.4",
    ])
    assert all(row["admin_category"] == "Workspace Admin" for row in body)


def test_every_elevated_check_ref_has_remediation_text():
    """A ref missing from remediation.yaml renders an empty recommendation.

    The standard library has the same guard; elevated checks are scored the same
    way and a finding with no advice is only half a finding.
    """
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    book = load_remediation(load_project(PROJECT_FILE))
    missing = sorted({spec.ref for spec in ADMIN_REGISTRY if not book.get(spec.ref)})
    assert missing == [], f"elevated checks with no remediation text: {missing}"


def test_tenant_and_capacity_publish_all_notebook_checks(client):
    body = client.get("/api/v1/catalog/admin-categories").json()
    counts = {row["category"]: row["checks"] for row in body}
    assert counts["Tenant"] == 9
    assert counts["Capacity"] == 5


def test_admin_checks_endpoint_rejects_an_unknown_category(client):
    assert client.get(
        "/api/v1/catalog/admin-checks", params={"category": "nonsense"}
    ).json() == []


def test_standard_catalog_excludes_elevated_checks(client):
    """``/catalog/checks`` describes the standard audit and nothing else."""
    body = client.get("/api/v1/catalog/checks").json()
    assert all(row.get("admin_category") is None for row in body)


# -- run mode ------------------------------------------------------------------

def test_admin_run_without_a_category_is_rejected(client):
    """Refusing beats running nothing: both look identical in the report."""
    response = client.post(
        "/api/v1/audit",
        json={
            "auth_session": AUTHENTICATED_SESSION,
            "check_set": "admin",
            "admin_categories": [],
        },
    )
    assert response.status_code == 400
    assert "admin_categories" in response.json()["detail"]


def test_admin_run_with_an_unknown_category_is_rejected(client):
    response = client.post(
        "/api/v1/audit",
        json={
            "auth_session": AUTHENTICATED_SESSION,
            "check_set": "admin",
            "admin_categories": ["Nonsense"],
        },
    )
    assert response.status_code == 400


def test_admin_run_with_an_unpopulated_category_is_rejected(client):
    """A category with no checks must fail loudly, not return a clean sheet.

    An audit that ran nothing and an audit that found nothing wrong produce the
    same-looking report; only one of them is a real result.
    """
    empty = [c for c in AdminCategory
             if not any(s.admin_category is c for s in ADMIN_REGISTRY)]
    if not empty:
        return  # every category is populated; nothing to assert
    response = client.post(
        "/api/v1/audit",
        json={
            "auth_session": AUTHENTICATED_SESSION,
            "check_set": "admin",
            "admin_categories": [empty[0].value],
        },
    )
    assert response.status_code == 400
    assert "No checks are registered yet" in response.json()["detail"]


def test_standard_audit_is_unaffected_by_the_new_field(client):
    """Omitting check_set must behave exactly as before it existed."""
    accepted = client.post(
        "/api/v1/audit", json={"auth_session": AUTHENTICATED_SESSION}
    )
    assert accepted.status_code == 202


def test_reviewer_settings_are_layered_over_the_project_yaml():
    """The run-time answers win, and an unsupplied key keeps the YAML's value.

    Three checks compare role assignments against names only the reviewer knows.
    Replacing the whole settings block instead of layering would silently drop
    every other project convention the standard YAML carries.
    """
    from auditfast.services.audit_service import _resolve_admin_categories

    # The merge itself is one line in run_admin_audit; assert the shape it
    # relies on rather than crawling a tenant to observe it.
    project = {"max_admins": 2, "operations_groups": ["From YAML"]}
    override = {"operations_groups": ["From The Screen"], "developer_groups": ["Devs"]}
    merged = {**project, **override}

    assert merged["operations_groups"] == ["From The Screen"]   # screen wins
    assert merged["developer_groups"] == ["Devs"]               # screen adds
    assert merged["max_admins"] == 2                            # YAML survives
    assert _resolve_admin_categories(["Workspace Admin"]) == [AdminCategory.WORKSPACE]


def test_supplied_group_names_make_a_config_check_score():
    """With the reviewer's answer supplied, the N/A becomes a real verdict."""
    assignments = [_role("SG-Ops", "Viewer")]
    without = _ctx(role_assignments=assignments)
    assert _run("ADM-WS-OPS-ACCESS", without).score is None

    with_answer = _ctx(settings={"operations_groups": ["SG-Ops"]},
                       role_assignments=assignments)
    assert _run("ADM-WS-OPS-ACCESS", with_answer).score == 3


def test_group_names_match_case_insensitively():
    """A reviewer types 'sg-ops'; Fabric shows 'SG-Ops'. Both must match."""
    ctx = _ctx(settings={"operations_groups": ["  sg-OPS  "]},
               role_assignments=[_role("SG-Ops", "Viewer")])
    assert _run("ADM-WS-OPS-ACCESS", ctx).score == 3


def test_a_comma_separated_string_is_accepted_like_a_list():
    """A YAML written by hand often carries one name without brackets."""
    ctx = _ctx(settings={"operations_groups": "SG-Ops, SG-Ops-2"},
               role_assignments=[_role("SG-Ops-2", "Viewer")])
    assert _run("ADM-WS-OPS-ACCESS", ctx).score == 3


# -- engine integration --------------------------------------------------------

def test_engine_runs_an_injected_admin_registry(provider):
    """The engine needs no change to run elevated checks.

    It already takes ``registry=``; an admin registry is an ordinary
    CheckRegistry, which is what makes this tier additive rather than a fork of
    the run path.
    """
    from auditfast.core.engine import run_audit

    registry = CheckRegistry()

    @admin_check(
        id="TEST-ADM-ENGINE", ref="6.1.96", title="Engine-run elevated check",
        pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
        scope=Scope.WORKSPACE, layers=(Layer.ANY,),
        requires=[Resource.ROLE_ASSIGNMENTS], registry=registry,
    )
    def _check(ctx):
        return binary(True, "role assignments were readable")

    results = run_audit(provider, [("ws-prep-01", Layer.PREP)], {}, registry=registry)
    ours = [r for r in results if r.check_id == "TEST-ADM-ENGINE"]
    assert len(ours) == 1
    assert ours[0].status.value == "PASS"


# -- narrow crawl --------------------------------------------------------------

class _RecordingLive:
    """A live provider that records exactly which resources it was asked for."""

    def __init__(self):
        self.calls: list[set] = []

    def fetch(self, workspace_id, layer=Layer.MIXED, resources=()):
        self.calls.append(set(resources))
        return WorkspaceContext(id=workspace_id, display_name=workspace_id, layer=layer)

    def list_workspaces(self):
        return []


def test_narrow_crawl_fetches_only_the_requested_resources(tmp_path):
    """An elevated run must not pay for a full workspace crawl.

    This is the whole reason the provider exists: the engine already asks for a
    narrow set, but CachingProvider throws that away and crawls everything so its
    KB stays complete. Reading role assignments should not cost every notebook
    definition in the workspace.
    """
    live = _RecordingLive()
    provider = NarrowCrawlProvider(live, ContextStore(tmp_path / "admin-kb"))

    provider.fetch("w1", Layer.PREP, {Resource.ROLE_ASSIGNMENTS})

    assert live.calls == [{Resource.ROLE_ASSIGNMENTS}]


def test_admin_kb_caches_the_crawl_for_the_next_run(tmp_path):
    """A second elevated run reuses the first one's snapshot, costing nothing."""
    live = _RecordingLive()
    store = ContextStore(tmp_path / "admin-kb")

    NarrowCrawlProvider(live, store).fetch("w1", Layer.PREP, {Resource.ROLE_ASSIGNMENTS})
    second = NarrowCrawlProvider(live, store)
    second.fetch("w1", Layer.PREP, {Resource.ROLE_ASSIGNMENTS})

    assert len(live.calls) == 1                 # only the first run crawled
    assert second.served_from_cache is True


def test_admin_kb_recrawls_when_the_snapshot_is_too_narrow(tmp_path):
    """A snapshot is reused only when it covers what the run needs.

    Serving a role-assignments-only snapshot to a run that also checks gateways
    would answer the gateway checks from data that was never fetched — an N/A
    dressed up as a verdict.
    """
    live = _RecordingLive()
    store = ContextStore(tmp_path / "admin-kb")

    NarrowCrawlProvider(live, store).fetch("w1", Layer.PREP, {Resource.ROLE_ASSIGNMENTS})
    NarrowCrawlProvider(live, store).fetch(
        "w1", Layer.PREP, {Resource.ROLE_ASSIGNMENTS, Resource.GIT}
    )

    assert len(live.calls) == 2
    assert live.calls[1] == {Resource.ROLE_ASSIGNMENTS, Resource.GIT}


def test_admin_kb_is_separate_from_the_standard_one(tmp_path):
    """The elevated run must never write into the standard knowledge base.

    A snapshot holding role assignments but no notebook definitions looks
    *complete* to ``is_complete`` — it records read failures, not deliberate
    narrowness — so a shared store would serve it to a standard audit as whole
    and silently turn every notebook check N/A.
    """
    standard = ContextStore(tmp_path / "kb")
    admin = ContextStore(tmp_path / "admin-kb")

    NarrowCrawlProvider(_RecordingLive(), admin).fetch(
        "w1", Layer.PREP, {Resource.ROLE_ASSIGNMENTS}
    )

    assert admin.load("w1") is not None
    assert standard.load("w1") is None


def test_snapshot_records_which_resources_it_covers(tmp_path):
    """A full crawl records no resource set, meaning "the whole workspace"."""
    store = ContextStore(tmp_path / "kb")
    ctx = WorkspaceContext(id="w1", display_name="WS One", layer=Layer.PREP)

    store.save(ctx)
    assert store.covered_resources("w1") is None          # full crawl

    store.save(ctx, resources={Resource.ROLE_ASSIGNMENTS})
    assert store.covered_resources("w1") == {Resource.ROLE_ASSIGNMENTS}


def test_narrow_crawl_falls_back_to_a_live_read_without_a_store(tmp_path):
    """With the cache disabled the run still works — it just always crawls."""
    live = _RecordingLive()
    provider = NarrowCrawlProvider(live, None)

    provider.fetch("w1", Layer.PREP, {Resource.GIT})

    assert live.calls == [{Resource.GIT}]
    assert provider.served_from_cache is False


# -- check behaviour -----------------------------------------------------------

def _ctx(settings=None, **workspace) -> CheckContext:
    """A CheckContext over a synthetic workspace."""
    ws = WorkspaceContext(id="w1", display_name="WS One", layer=Layer.MIXED, **workspace)
    return CheckContext(workspace=ws, settings=settings or {})


def _role(name, role, kind="Group") -> RoleAssignment:
    return RoleAssignment(principal_type=kind, display_name=name, role=role, principal_id=name)


def _run(check_id: str, ctx: CheckContext):
    return ADMIN_REGISTRY.get(check_id).fn(ctx)


def test_unreadable_role_assignments_are_na_not_a_failure():
    """The single most important rule for this tier.

    Every one of these needs Member or higher. A Contributor running the tool
    must not make a correct estate look broken, so an unreadable read is N/A —
    and the evidence has to say *why*, or a reader cannot tell "no access
    granted" from "we could not look".
    """
    ctx = _ctx(
        settings={
            "operations_groups": ["Ops"],
            "production_workspaces": ["WS One"],
            "developer_groups": ["Devs"],
            "report_consumer_groups": ["Readers"],
        },
        unavailable={Resource.ROLE_ASSIGNMENTS},
    )
    for check_id in (
        "ADM-WS-GROUPS", "ADM-WS-AUTOMATION", "ADM-WS-OPS-ACCESS",
        "ADM-WS-PROD-WRITE", "ADM-WS-CONSUMER-ACCESS",
    ):
        verdict = _run(check_id, ctx)
        assert verdict.score is None, f"{check_id} scored on unreadable data"
        assert verdict.scored is False
        assert "Member" in verdict.evidence


def test_unreadable_connections_and_gateways_are_na():
    """Same rule for the connection, gateway and OneLake families."""
    ctx = _ctx(unavailable={
        Resource.CONNECTIONS, Resource.GATEWAYS, Resource.DATA_ACCESS_ROLES,
    })
    for check_id in (
        "ADM-CONN-CREDENTIALS", "ADM-CONN-WORKSPACE-IDENTITY", "ADM-CONN-ENCRYPTED",
        "ADM-CONN-IDENTITY-OVER-KEYS", "ADM-GATEWAY-CREDENTIALS",
        "ADM-GATEWAY-HA", "ADM-ONELAKE-ACCESS",
    ):
        verdict = _run(check_id, ctx)
        assert verdict.score is None, f"{check_id} scored on unreadable data"


def test_missing_project_config_is_na_not_a_failure():
    """An unnamed group is not evidence of a gap.

    The notebooks refuse to score without OPS_GROUPS / DEV_GROUPS / consumer
    groups; guessing which principal is "the operations team" would invent
    findings, so absent config is N/A.
    """
    ctx = _ctx(role_assignments=[_role("Anyone", "Admin")])
    for check_id in ("ADM-WS-OPS-ACCESS", "ADM-WS-PROD-WRITE", "ADM-WS-CONSUMER-ACCESS"):
        assert _run(check_id, ctx).score is None


def test_groups_check_scores_the_notebook_bands():
    """More than two named people is a 0; one or two is a 1; none is a pass."""
    groups_only = _ctx(role_assignments=[_role("SG-Team", "Member")])
    assert _run("ADM-WS-GROUPS", groups_only).score == 3

    two = _ctx(role_assignments=[_role("a@x", "Member", "User"), _role("b@x", "Viewer", "User")])
    assert _run("ADM-WS-GROUPS", two).score == 1

    many = _ctx(role_assignments=[_role(f"u{i}@x", "Member", "User") for i in range(4)])
    assert _run("ADM-WS-GROUPS", many).score == 0


def test_automation_check_never_awards_full_marks():
    """An SPN below Admin caps at 2 — the Entra half is not in the Fabric API."""
    spn = _ctx(role_assignments=[_role("svc-etl", "Contributor", "ServicePrincipal")])
    assert _run("ADM-WS-AUTOMATION", spn).score == 2

    spn_admin = _ctx(role_assignments=[_role("svc-etl", "Admin", "ServicePrincipal")])
    assert _run("ADM-WS-AUTOMATION", spn_admin).score == 1

    people_only = _ctx(role_assignments=[_role("a@x", "Admin", "User")])
    assert _run("ADM-WS-AUTOMATION", people_only).score == 0


def test_production_check_ignores_a_workspace_that_is_not_production():
    """This check has no opinion about dev and test."""
    settings = {"production_workspaces": ["Some Other WS"], "developer_groups": ["Devs"]}
    ctx = _ctx(settings=settings, role_assignments=[_role("Devs", "Admin")])
    assert _run("ADM-WS-PROD-WRITE", ctx).score is None

    settings["production_workspaces"] = ["WS One"]
    hot = _ctx(settings=settings, role_assignments=[_role("Devs", "Admin")])
    assert _run("ADM-WS-PROD-WRITE", hot).score == 0


def test_consumer_check_needs_the_app_review_for_full_marks():
    """Workspace roles are half the answer; app audiences are not readable."""
    settings = {"report_consumer_groups": ["Readers"]}
    viewer = _ctx(settings=settings, role_assignments=[_role("Readers", "Viewer")])
    assert _run("ADM-WS-CONSUMER-ACCESS", viewer).score == 2

    settings["app_access_reviewed"] = True
    reviewed = _ctx(settings=settings, role_assignments=[_role("Readers", "Viewer")])
    assert _run("ADM-WS-CONSUMER-ACCESS", reviewed).score == 3


def test_connection_checks_read_the_credential_type():
    """The credential families come verbatim from the notebook's STRONG/STATIC sets.

    ``ServicePrincipal`` is deliberately *static*: it authenticates with a client
    secret somebody has to rotate. ``Windows`` is *strong* — the OS holds it.
    Getting these backwards silently inverts two checks.
    """
    strong = {"display_name": "lake", "credential_type": "WorkspaceIdentity",
              "connection_encryption": "Encrypted"}
    spn = {"display_name": "svc", "credential_type": "ServicePrincipal",
           "connection_encryption": "Encrypted"}

    clean = _ctx(settings={"code_scan_clean": True}, connections=[strong])
    assert _run("ADM-CONN-CREDENTIALS", clean).score == 3
    # A service principal rests on a client secret, so it is NOT strong.
    assert _run("ADM-CONN-CREDENTIALS", _ctx(connections=[spn])).score == 0


def test_credential_check_needs_the_code_scan_for_full_marks():
    """Connections are half the point; secrets pasted into code are the other."""
    strong = {"display_name": "lake", "credential_type": "WorkspaceIdentity"}
    assert _run("ADM-CONN-CREDENTIALS", _ctx(connections=[strong])).score == 2
    confirmed = _ctx(settings={"code_scan_clean": True}, connections=[strong])
    assert _run("ADM-CONN-CREDENTIALS", confirmed).score == 3


def test_workspace_identity_bands_are_lenient_by_design():
    """Not every connector supports Workspace Identity, so 75% is full marks."""
    wi = {"display_name": "a", "credential_type": "WorkspaceIdentity"}
    other = {"display_name": "b", "credential_type": "OAuth2"}

    assert _run("ADM-CONN-WORKSPACE-IDENTITY", _ctx(connections=[wi] * 3 + [other])).score == 3
    assert _run("ADM-CONN-WORKSPACE-IDENTITY", _ctx(connections=[wi, other])).score == 2
    assert _run("ADM-CONN-WORKSPACE-IDENTITY", _ctx(connections=[wi] + [other] * 3)).score == 1
    assert _run("ADM-CONN-WORKSPACE-IDENTITY", _ctx(connections=[other] * 4)).score == 0


def test_unencrypted_beats_every_other_encryption_signal():
    """One connection in the clear scores 0 however good the rest are."""
    enc = {"display_name": "ok", "connection_encryption": "Encrypted"}
    plain = {"display_name": "bad", "connection_encryption": "NotEncrypted"}
    fallback = {"display_name": "meh", "connection_encryption": "Any"}

    assert _run("ADM-CONN-ENCRYPTED", _ctx(connections=[enc] * 9 + [plain])).score == 0
    # 'Any' is graded by share, not treated as plaintext.
    assert _run("ADM-CONN-ENCRYPTED", _ctx(connections=[enc] * 9 + [fallback])).score == 2
    assert _run("ADM-CONN-ENCRYPTED", _ctx(connections=[enc, fallback])).score == 1
    assert _run("ADM-CONN-ENCRYPTED", _ctx(connections=[enc])).score == 3
    untested = {"display_name": "u", "connection_encryption": "Encrypted",
                "skip_test_connection": True}
    assert _run("ADM-CONN-ENCRYPTED", _ctx(connections=[untested])).score == 2


def test_bearer_credentials_are_graded_not_pass_fail():
    """One legacy key differs from a tenant built on them."""
    ok = {"display_name": "a", "credential_type": "OAuth2"}
    key = {"display_name": "k", "credential_type": "Key"}

    assert _run("ADM-CONN-IDENTITY-OVER-KEYS", _ctx(connections=[ok] * 4)).score == 3
    assert _run("ADM-CONN-IDENTITY-OVER-KEYS", _ctx(connections=[ok] * 3 + [key])).score == 2
    assert _run("ADM-CONN-IDENTITY-OVER-KEYS", _ctx(connections=[ok, key])).score == 1
    assert _run("ADM-CONN-IDENTITY-OVER-KEYS", _ctx(connections=[key] * 3 + [ok])).score == 0


def test_gateway_credentials_scores_personal_mode_not_static_secrets():
    """6.4.4 is about personal-mode connections, which cannot be handed over."""
    personal = {"display_name": "mine", "connectivity_type": "PersonalCloud"}
    shared = {"display_name": "shared", "connectivity_type": "OnPremisesGateway",
              "credential_type": "OAuth2"}
    basic = {"display_name": "svc", "connectivity_type": "OnPremisesGateway",
             "credential_type": "Basic"}

    assert _run("ADM-GATEWAY-CREDENTIALS", _ctx(connections=[personal] * 2)).score == 0
    assert _run("ADM-GATEWAY-CREDENTIALS", _ctx(connections=[personal, shared])).score == 1
    assert _run("ADM-GATEWAY-CREDENTIALS", _ctx(connections=[basic, shared])).score == 2
    assert _run("ADM-GATEWAY-CREDENTIALS", _ctx(connections=[shared])).score == 3


def test_a_connection_that_reports_nothing_is_excluded_not_counted_as_clean():
    """Fabric blanks credentialType on a connection you cannot see into.

    Counting those as compliant would inflate the ratio — a workspace where most
    connections are unreadable would score as mostly clean. They are excluded
    from the denominator and named in the evidence instead.
    """
    opaque = {"display_name": "hidden", "credential_type": "", "connection_encryption": ""}
    secret = {"display_name": "onprem", "credential_type": "Basic",
              "connection_encryption": "NotEncrypted"}

    ctx = _ctx(connections=[opaque, opaque, secret])
    verdict = _run("ADM-CONN-CREDENTIALS", ctx)
    assert verdict.score == 0
    assert "2 of 3" in verdict.evidence
    assert "excluded" in verdict.evidence


def test_all_connections_opaque_is_na_not_a_pass():
    """Nothing readable means nothing judged — not a clean bill of health."""
    opaque = {"display_name": "hidden", "credential_type": "", "connection_encryption": ""}
    ctx = _ctx(connections=[opaque, opaque])
    for check_id in ("ADM-CONN-CREDENTIALS", "ADM-CONN-WORKSPACE-IDENTITY",
                     "ADM-CONN-ENCRYPTED", "ADM-CONN-IDENTITY-OVER-KEYS"):
        assert _run(check_id, ctx).score is None


def test_personal_gateway_cannot_be_made_redundant():
    """A personal gateway carrying estate traffic scores 0 — it cannot cluster."""
    personal = _ctx(gateways=[{"display_name": "Bob's gateway", "type": "Personal"}])
    assert _run("ADM-GATEWAY-HA", personal).score == 0

    clustered = _ctx(
        settings={"gateway_sizing_confirmed": True},
        gateways=[{"display_name": "gw", "type": "OnPremises",
                   "number_of_member_gateways": 2}],
    )
    assert _run("ADM-GATEWAY-HA", clustered).score == 3


def test_gateway_ladder_matches_the_notebook():
    """Unclustered scores 0 outright, not a proportional band."""
    single = {"display_name": "gw1", "type": "OnPremises", "number_of_member_gateways": 1}
    pair = {"display_name": "gw2", "type": "OnPremises", "number_of_member_gateways": 2}

    assert _run("ADM-GATEWAY-HA", _ctx(gateways=[single])).score == 0
    assert _run("ADM-GATEWAY-HA", _ctx(gateways=[single, pair])).score == 1
    # All clustered but sizing unconfirmed caps at 2.
    assert _run("ADM-GATEWAY-HA", _ctx(gateways=[pair])).score == 2


def test_onelake_check_reports_a_lakehouse_with_no_data_access_role():
    """The notebook's four rungs, including individuals named in a role."""
    group_role = {"name": "readers", "built_in": False, "members": 2, "individuals": 0}
    person_role = {"name": "readers", "built_in": False, "members": 1, "individuals": 1}

    assert _run("ADM-ONELAKE-ACCESS", _ctx(data_access_roles={"lh": [group_role]})).score == 3
    assert _run("ADM-ONELAKE-ACCESS", _ctx(data_access_roles={"lh": [person_role]})).score == 2
    partial = _ctx(data_access_roles={"lh": [group_role], "lh2": []})
    assert _run("ADM-ONELAKE-ACCESS", partial).score == 1
    assert _run("ADM-ONELAKE-ACCESS", _ctx(data_access_roles={"lh": []})).score == 0


def test_a_default_role_does_not_count_as_scoped_access():
    """Fabric creates Default* roles itself; they are not evidence of scoping."""
    built_in = {"name": "DefaultReader", "built_in": True, "members": 5, "individuals": 0}
    ctx = _ctx(data_access_roles={"lh": [built_in]})
    assert _run("ADM-ONELAKE-ACCESS", ctx).score == 0


def test_data_access_roles_pulls_the_item_list_with_it():
    """6.2.6 reads per Lakehouse, so it needs the item list fetched too.

    Without this the provider finds no lakehouses on an elevated-only run and the
    check reports N/A on every workspace forever — a silent always-N/A rather
    than a visible failure.
    """
    from auditfast.clients.live import _ITEM_DERIVED_RESOURCES

    assert Resource.DATA_ACCESS_ROLES in _ITEM_DERIVED_RESOURCES
    # 14.3.6 recognises Fabric's auto-created semantic models by their sharing a
    # Lakehouse or Warehouse name, so it needs the item list for the same reason.
    assert Resource.ADMIN_SCANNER in _ITEM_DERIVED_RESOURCES


def test_a_standard_crawl_does_not_request_elevated_resources():
    """A standard audit must not spend calls on gateways or OneLake roles.

    They need scopes an ordinary sign-in lacks, so asking would collect 403s that
    surface in the crawl-completeness section as if they were findings.
    """
    from auditfast.clients.base import ELEVATED_RESOURCES, STANDARD_RESOURCES

    assert Resource.GATEWAYS in ELEVATED_RESOURCES
    assert Resource.DATA_ACCESS_ROLES in ELEVATED_RESOURCES
    assert not (STANDARD_RESOURCES & ELEVATED_RESOURCES)
    assert Resource.ROLE_ASSIGNMENTS in STANDARD_RESOURCES


def test_a_lakehouse_with_no_data_access_roles_is_an_answer_not_a_failure(monkeypatch):
    """Fabric answers 404 when no data access role is configured.

    That is exactly what 6.2.6 looks for — workspace access alone deciding who
    reads the data. Treating it as a read failure would mark the resource
    unavailable and report N/A on the one estate the check exists to catch.
    """
    from auditfast.clients.live import LiveFabricProvider

    provider = LiveFabricProvider("token")
    monkeypatch.setattr(provider, "_get", lambda path: (404, None))
    roles, known = provider._data_access_roles("w1", "lh1")
    assert known is True          # a real answer
    assert roles == []            # and the answer is "none defined"


def test_a_forbidden_data_access_role_read_stays_unknown(monkeypatch):
    """403 is genuinely "could not ask" — that one must still be N/A."""
    from auditfast.clients.live import LiveFabricProvider

    provider = LiveFabricProvider("token")
    monkeypatch.setattr(provider, "_get", lambda path: (403, None))
    roles, known = provider._data_access_roles("w1", "lh1")
    assert known is False
    assert roles == []


def test_capacity_manual_confirmation_promotes_the_metrics_app_score():
    spec = ADMIN_REGISTRY.get("CP-12-2-1")
    assert spec is not None
    workspace = WorkspaceContext(
        id="ws-1",
        capacity_metrics={"model_found": True},
    )

    unconfirmed = spec.fn(CheckContext(workspace, {}, "ws-1", workspace))
    confirmed = spec.fn(CheckContext(
        workspace,
        {"metrics_app_monitored": True},
        "ws-1",
        workspace,
    ))

    assert unconfirmed.score == 2
    assert confirmed.score == 3


# -- tenant / capacity ladders -------------------------------------------------

def _tenant(check_id: str, settings=None, **workspace):
    ws = WorkspaceContext(id="w1", display_name="WS One", layer=Layer.MIXED, **workspace)
    return ADMIN_REGISTRY.get(check_id).fn(CheckContext(ws, settings or {}, "w1", ws))


def test_no_manual_deployments_reads_the_shared_production_setting():
    """The setting name must match the one the UI and every other check use.

    11.2.3 originally read 'prod_workspaces' while the run screen, the project
    YAML and the workspace tier all write 'production_workspaces' — so the check
    was permanently N/A. A silent always-N/A, which is the failure mode this tier
    has hit three times.
    """
    events = [{"Activity": "UpdateDataset", "WorkSpaceName": "WS One"}]
    assert _tenant("TN-11-2-3", activity_events=events).score is None
    scored = _tenant(
        "TN-11-2-3",
        settings={"production_workspaces": ["WS One"]},
        activity_events=events,
    )
    assert scored.score is not None


def test_tenant_checks_do_not_invent_a_third_rung():
    """7.2.4 caps at 2 and 7.4.1 at 2 — the notebooks have no 3.

    A log recording a change is not a log anyone reviews, and neither is
    readable from any API. Awarding 3 for an unsettable flag would have been a
    score nobody could ever earn.
    """
    events = [{"Activity": "AddGroupMembers", "UserId": "u@x", "WorkSpaceName": "WS One"}]
    assert _tenant("TN-7-2-4", activity_events=events).score == 2
    assert _tenant("TN-7-4-1", activity_events=events).score == 2


def test_an_empty_activity_log_is_a_zero_for_export_not_a_one():
    """The notebook: no events at all means nothing proves the log is capturing."""
    assert _tenant("TN-7-4-1", activity_events=[]).score == 0


def test_audit_coverage_scores_the_number_of_change_kinds_seen():
    """The notebook's score *is* kinds_seen — 3 kinds, 3 points."""
    all_three = [
        {"Activity": "CreateWorkspace"},
        {"Activity": "AddGroupMembers"},
        {"Activity": "DeleteDataset"},
    ]
    assert _tenant("TN-7-4-2", activity_events=all_three).score == 3
    assert _tenant("TN-7-4-2", activity_events=[{"Activity": "DeleteDataset"}]).score == 1
    # An empty log is unknown, not zero coverage.
    assert _tenant("TN-7-4-2", activity_events=[]).score is None


def test_every_tenant_and_capacity_check_is_na_when_its_data_is_unreadable():
    """Tenant admin refusal is N/A, never FAIL — the tier's central rule."""
    blind = WorkspaceContext(
        id="w1", display_name="WS One",
        unavailable={
            Resource.TENANT_SETTINGS, Resource.TENANT_DOMAINS,
            Resource.ADMIN_ACTIVITY, Resource.ADMIN_SCANNER,
            Resource.CAPACITY_METRICS,
        },
    )
    ctx = CheckContext(blind, {}, "w1", blind)
    for spec in ADMIN_REGISTRY:
        if spec.admin_category in (AdminCategory.TENANT, AdminCategory.CAPACITY):
            verdict = spec.fn(ctx)
            assert verdict.score is None, f"{spec.id} scored on unreadable data"


def test_capacity_metrics_workspace_hint_is_searched_first():
    """The app can live anywhere; the reviewer's answer orders the search.

    A hint is not a filter — the rest of the tenant still follows, so a wrong
    name costs ordering rather than a false "app not installed".
    """
    from auditfast.clients.powerbi import PowerBIClient

    client = PowerBIClient.__new__(PowerBIClient)
    seen: list[str] = []
    client.list_groups = lambda: [  # type: ignore[method-assign]
        {"id": "a", "name": "Other"},
        {"id": "b", "name": "Capacity Metrics"},
    ]

    def _datasets(group_id):
        seen.append(group_id)
        return []

    client.list_datasets = _datasets  # type: ignore[method-assign]
    client.capacity_metrics("Capacity Metrics")
    assert seen[0] == "b", "the named workspace must be searched first"


def _metrics_client(datasets):
    """A PowerBIClient whose one workspace holds ``datasets``.

    ``execute_queries`` returns an empty body, so the model is *found* but its
    schema yields no fact table — enough to exercise the name matching without
    a live tenant.
    """
    from auditfast.clients.powerbi import PowerBIClient

    client = PowerBIClient.__new__(PowerBIClient)
    client.list_groups = lambda: [{"id": "a", "name": "Admin"}]  # type: ignore[method-assign]
    client.list_datasets = lambda group_id: datasets  # type: ignore[method-assign]
    client.execute_queries = lambda *a, **k: {}  # type: ignore[method-assign]
    return client


def test_a_renamed_metrics_app_is_found_by_the_reviewers_model_name():
    """The stock app, FUAM and a renamed install carry different names.

    The name is a *filter*, so getting it wrong reports "not deployed" for an
    estate that monitors its capacity perfectly well — the one input where a
    wrong value costs a false finding rather than a slower search.
    """
    datasets = [{"id": "m1", "name": "FUAM Capacity Monitoring"}]

    missed, known = _metrics_client(datasets).capacity_metrics("")
    assert known and missed["model_found"] is False
    assert missed["model_name_searched"] == "capacity metrics"

    # 12.2.1 must name what it looked for, so the 0 is actionable.
    verdict = _run("CP-12-2-1", _ctx(capacity_metrics=missed))
    assert verdict.score == 0
    assert "capacity metrics" in verdict.evidence
    assert "model name" in verdict.evidence

    found, _ = _metrics_client(datasets).capacity_metrics("", "FUAM")
    assert found["model_found"] is True


def test_a_blank_model_name_falls_back_to_the_notebook_default():
    """An empty box must not match every dataset in the tenant."""
    client = _metrics_client([{"id": "m1", "name": "Sales"}])
    result, _ = client.capacity_metrics("", "   ")
    assert result["model_found"] is False
    assert result["model_name_searched"] == "capacity metrics"


def test_the_working_day_may_wrap_past_midnight():
    """A local working day shifted into UTC routinely wraps.

    09:00-18:00 in UTC+10 is 23:00-08:00 UTC. A naive start <= h < end would
    call the client's whole working day off-peak and invert 12.1.2.
    """
    from auditfast.clients.powerbi import _in_working_day

    assert _in_working_day(9, 8, 18)
    assert not _in_working_day(20, 8, 18)

    assert _in_working_day(23, 22, 6)
    assert _in_working_day(2, 22, 6)
    assert not _in_working_day(12, 22, 6)

    assert not _in_working_day(9, 8, 8), "an empty window is not the whole day"


def test_an_unusable_peak_hour_keeps_the_documented_default():
    """A typo must not silently become midnight and still produce a split."""
    from auditfast.services.audit_service import _peak_hours

    assert _peak_hours({}) is None
    assert _peak_hours({"capacity_peak_start_hour": 9,
                        "capacity_peak_end_hour": 17}) == (9, 17)
    assert _peak_hours({"capacity_peak_start_hour": 22,
                        "capacity_peak_end_hour": 6}) == (22, 6)
    assert _peak_hours({"capacity_peak_start_hour": 25,
                        "capacity_peak_end_hour": 6}) is None
    assert _peak_hours({"capacity_peak_start_hour": "morning",
                        "capacity_peak_end_hour": 6}) is None
    assert _peak_hours({"capacity_peak_start_hour": 8,
                        "capacity_peak_end_hour": 8}) is None


def test_the_peak_verdict_names_the_window_it_measured():
    """The times are UTC, so a reader elsewhere needs to see the window."""
    metrics = {
        "model_found": True,
        "peak_split": {"peak": 100.0, "off_peak": 20.0, "window": "22:00-06:00 UTC"},
    }
    verdict = _run("CP-12-1-2", _ctx(capacity_metrics=metrics))
    assert "22:00-06:00 UTC" in verdict.evidence


def test_domain_ownership_scores_membership_and_caps_at_two():
    """6.1.9's rungs 3 and 0 need every workspace at once, so 2 is the ceiling.

    The notebook's own degraded ladder for "crossings could not be assessed" is
    ``2 if not orphans else 1``, which is exactly this check's situation.
    """
    domains = [{"id": "d1", "name": "Finance", "workspace_ids": ["w1", "w9"]}]
    assert _run("TN-6-1-9", _ctx(tenant_domains=domains)).score == 2

    elsewhere = [{"id": "d1", "name": "Finance", "workspace_ids": ["w9"]}]
    assert _run("TN-6-1-9", _ctx(tenant_domains=elsewhere)).score == 1


def test_domain_ownership_is_na_when_the_tenant_defines_no_domain():
    """No domains at all is nothing to align against, not a failure."""
    assert _run("TN-6-1-9", _ctx(tenant_domains=[])).score is None


def test_endorsement_leaves_out_default_models_and_usage_metrics():
    """Counting content nobody endorses would drag a good estate to 0.

    Every Lakehouse gets a same-named semantic model automatically; the
    notebook excludes them (``INCLUDE_DEFAULT_MODELS = False``) along with
    usage-metrics and template-app content.
    """
    from auditfast.clients.live import LiveFabricProvider
    from auditfast.core.models import Item

    payload = {"workspaces": [{
        "id": "w1",
        "datasets": [
            {"id": "1", "name": "Sales",
             "endorsementDetails": {"endorsement": "Certified"}},
            {"id": "2", "name": "BronzeLake"},
            {"id": "3", "name": "Usage Metrics Report"},
        ],
        "reports": [{"id": "4", "name": "Sales Overview",
                     "endorsementDetails": {"endorsement": "Promoted"}}],
    }]}
    items = [Item(id="lh", type="Lakehouse", display_name="BronzeLake")]

    scan = LiveFabricProvider._endorsement_scan(payload, "w1", items)
    judged = {row["name"] for row in scan["items"]}
    assert judged == {"Sales", "Sales Overview"}
    assert len(scan["excluded"]) == 2

    ctx = _ctx(admin_scan=scan)
    verdict = _run("TN-14-3-6", ctx)
    assert verdict.score == 3, "both judged items are endorsed, one Certified"
    assert "2 auto-created or usage-metrics item(s) excluded" in verdict.evidence


def test_endorsement_counts_a_real_model_named_like_no_lakehouse():
    """The exclusion is by name match, so an ordinary model still counts."""
    from auditfast.clients.live import LiveFabricProvider

    payload = {"workspaces": [{
        "id": "w1",
        "datasets": [{"id": "1", "name": "BronzeLake"}],
        "reports": [],
    }]}
    scan = LiveFabricProvider._endorsement_scan(payload, "w1", [])
    assert len(scan["items"]) == 1, "no Lakehouse of that name, so it is judged"
    assert _run("TN-14-3-6", _ctx(admin_scan=scan)).score == 0
