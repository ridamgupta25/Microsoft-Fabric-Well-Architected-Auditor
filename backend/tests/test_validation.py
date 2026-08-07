"""The Phase 1 validation flag — one editable checklist, surfaced everywhere.

The flag is keyed by the checklist **ref id**. Every ref in
``VALIDATED_CHECKLIST`` must be a real registered check's ``ref`` (a typo would
silently flag nothing), and the flag must reach both serialised surfaces:
``CheckSpec.to_dict()`` (the catalog / UI / Catalog page) and
``CheckResult.to_dict()`` (the audit report the UI, Excel, and Markdown read).
"""
from __future__ import annotations

from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Pillar, Scope, Status
from auditfast.core.models import CheckResult
from auditfast.core.validation import (
    PENDING_LABEL,
    VALIDATED_CHECKLIST,
    VALIDATED_LABEL,
    VALIDATED_REFS,
    is_validated,
    validation_label,
)


def test_every_validated_ref_belongs_to_a_registered_check():
    """A ref typo would flag nothing — catch it here, not in production."""
    registered = {spec.ref for spec in REGISTRY}
    unknown = VALIDATED_REFS - registered
    assert unknown == set(), f"VALIDATED_CHECKLIST has unknown refs: {sorted(unknown)}"


def test_validated_refs_matches_the_checklist_keys():
    assert set(VALIDATED_REFS) == set(VALIDATED_CHECKLIST)


def test_is_validated_matches_the_checklist():
    for ref in VALIDATED_REFS:
        assert is_validated(ref) is True
    assert is_validated("NO-SUCH-REF") is False


def test_validation_label():
    sample = next(iter(VALIDATED_REFS))
    assert validation_label(sample) == VALIDATED_LABEL
    assert validation_label("NO-SUCH-REF") == PENDING_LABEL


def test_spec_to_dict_carries_the_flag():
    for spec in REGISTRY:
        assert spec.to_dict()["validated"] == is_validated(spec.ref)


def test_result_to_dict_carries_the_flag():
    validated_ref = next(iter(VALIDATED_REFS))
    validated = CheckResult(
        check_id="ANY", ref=validated_ref, title="t", pillar=Pillar.SECURITY,
        status=Status.PASS, score=3, scope=Scope.WORKSPACE,
    )
    assert validated.to_dict()["validated"] is True

    pending = CheckResult(
        check_id="ANY", ref="NOT-A-REAL-REF", title="t", pillar=Pillar.SECURITY,
        status=Status.PASS, score=3, scope=Scope.WORKSPACE,
    )
    assert pending.to_dict()["validated"] is False


def test_checks_sharing_a_ref_are_all_validated():
    """Keying by ref validates every check that shares that ref (pipeline +
    notebook variants of the same point), which is the intended behaviour."""
    ref_to_ids: dict[str, list[str]] = {}
    for spec in REGISTRY:
        ref_to_ids.setdefault(spec.ref, []).append(spec.id)
    shared_validated = [
        (ref, ids) for ref, ids in ref_to_ids.items()
        if len(ids) > 1 and ref in VALIDATED_REFS
    ]
    for ref, ids in shared_validated:
        for spec in REGISTRY:
            if spec.id in ids:
                assert spec.to_dict()["validated"] is True, (
                    f"{spec.id} shares validated ref {ref} but is not flagged"
                )
