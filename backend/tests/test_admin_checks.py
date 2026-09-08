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
