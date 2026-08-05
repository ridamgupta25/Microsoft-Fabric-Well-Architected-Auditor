# Checks

The check library is the heart of the tool. This page lists every check and
explains how to add one.

---

## What a check is

A small **pure function** that inspects one implemented object and returns a
`Verdict` — a score plus the evidence for it. No AI, no network calls of its own,
no side effects: it is handed data and returns a judgement.

```python
@check(
    id="WS-GIT", ref="11.1.2", title="Git integration enabled",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE,
    severity=Severity.MEDIUM, requires=[Resource.GIT],
)
def git_connected(ctx: CheckContext) -> Verdict:
    """The workspace is connected to Git so its items are source-controlled."""
    ok = ctx.workspace.git_connected
    return binary(ok, "Workspace is connected to Git" if ok
                  else "Workspace is not connected to Git")
```

Everything else — id, ref, title, pillar, severity, weight, scope, workspace
name, object name, and the remediation lookup — comes from the registered
`CheckSpec` and the run context. That is why check bodies stay short.

---

## Catalog

**148 checks** live under [`core/check/<pillar>/<layer>/`](../backend/src/auditfast/core/check/),
split into four module kinds per pillar × layer:

| Module | Automation | Meaning |
|--------|-----------|---------|
| `automated.py` | `automated` | Verified now from data the provider fetches — **64** checks |
| `questionnaire.py` | `interactive` | **Self-assessed** — the reviewer answers a scored question during the audit (Azure Well-Architected Review style); registered with `questionnaire_check(...)` — **0** checks today (machinery intact; the 16 original points were removed) |
| `roadmap.py` | `roadmap` | Automatable, but needs a Fabric API not yet called — reported as an attestation and **generated** (**84** checks; see *Promoting* below) |
| `manual.py` | `manual` | Never machine-verifiable — a legal / process / judgement attestation (**0** today) |

### By pillar

| Pillar | Checks |
|--------|-------:|
| Data Management & Quality | 53 |
| Operations & Reliability | 33 |
| Performance & Capacity | 23 |
| Security | 16 |
| Cost & Resource Optimization | 15 |
| Governance & Compliance | 7 |
| Foundation (informational, unscored) | 1 |

### By scope

| Scope | Checks | Examples |
|-------|-------:|----------|
| `workspace` | 107 | naming, roles via security groups, least-privilege admins, sensitivity labels, capacity assigned, Git enabled, deployment pipeline, orphaned items, layer content / separation, inventory |
| `pipeline` | 12 | naming, descriptions, parameterization, retry policy, on-failure path, failure notification, timeouts, no hardcoded secrets |
| `notebook` | 29 | Delta MERGE / OPTIMIZE / VACUUM / Z-ORDER / V-ORDER, table properties, retention, Spark env & pinned libraries, shuffle / cache / repartition, `SELECT *` |

The reserved scopes `lakehouse`, `semantic_model`, `report`, and `eventhouse`
await the data needed to judge them.

### Synthetic result

| Check ID | Meaning |
|----------|---------|
| `WS-ACCESS` | The workspace could not be read at all. Never scored; surfaced by the API in a separate `errors` array. |

The catalog is **not** maintained by hand here — browse the live source of truth:
`GET /api/v1/catalog/checks`, or `auditfast checks --pillar Security`.

### TLS source-connection evidence

`WS-TLS` (`6.3.4`) is backed by Fabric connection metadata and requires an
explicit minimum TLS version to produce a scored result. Fabric's
`connectionEncryption = "Encrypted"` value alone does not prove TLS 1.2+;
missing minimum-version evidence is reported as N/A. See
[TLS Evidence for Source Connections](tls-evidence.md) for the provider data
contract and the source-specific evidence required for live PASS/FAIL results.

---

## Which checks run

Two filters, both applied **before** execution.

**Layer** — a check declares which layers it applies to. Pipeline checks use:

```python
PIPELINE_LAYERS = (Layer.PREP, Layer.OPERATIONS, Layer.MIXED)
```

So a `Data Storage` workspace gets the workspace-scoped checks and no pipeline
checks, even if it contains pipelines. Its layer-separation check will still flag
those pipelines as foreign — that *is* the finding.

**Pillar** — narrows the set before anything is fetched, so deselecting a pillar
genuinely skips its Fabric API calls.

Because selection precedes fetching, the engine unions the `requires` of the
selected checks and asks the provider for only that. A run with no pipeline
checks never pays for `getDefinition` — one call per pipeline.

---

## Verdict builders

Never construct a `CheckResult`. Return a `Verdict` from one of these
([`core/check/helpers.py`](../backend/src/auditfast/core/check/helpers.py)):

| Builder | Use when | Score |
|---------|----------|-------|
| `binary(ok, evidence)` | It is either done or not | `3` or `0` |
| `covered(n, total, evidence)` | *N of M* objects comply | banded from the ratio |
| `graded(score, evidence)` | Genuine middle ground | you supply `0`–`3` |
| `note(evidence)` | Reporting a fact, not a verdict | unscored, `INFO` |
| `not_applicable(evidence)` | The data could not be read | unscored, `N/A` |

`covered()` handles the empty population (an empty collection is vacuously
compliant), removing the `x / len(items) if items else 1.0` idiom every coverage
check used to repeat — along with its zero-division risk.

`not_applicable()` matters: *"we could not determine this"* must not score the
same as *"this is not configured"*.

---

## Interactive (self-assessed) checks

> **None are registered today.** The 16 original questionnaire points were
> removed; the machinery below remains, so re-adding a `questionnaire_check`
> brings them back.

Some Well-Architected points can't be read from a workspace — whether a
disaster-recovery plan has actually been restore-tested, whether a cost review
happens on a schedule — but a reviewer *can* attest to them. These are
**interactive** checks (`automation=interactive`), the **Azure Well-Architected
Review** model: the reviewer picks a scored option during the audit.

They live in `questionnaire.py` modules and are registered with
`questionnaire_check(...)` rather than `@check`:

```python
from auditfast.core.check import Option, questionnaire_check

questionnaire_check(
    id="Q-OPS-DR", ref="Q-OPS-1",
    title="Disaster-recovery / restore plan documented and tested",
    pillar=Pillar.OPERATIONS, layers=(Layer.ANY,),
    question="Is there a documented, restore-tested DR plan for this workspace?",
    options=(
        Option("tested", "Documented and restore-tested within the last year", 3),
        Option("documented", "Documented but never tested", 1,
               guidance="Run a restore drill to prove the RTO/RPO are achievable."),
        Option("none", "No DR plan", 0,
               guidance="Document a recovery plan with target RTO/RPO and test it."),
    ),
)
```

How they behave:

- **The engine skips them** (they are `manual=True`).
  [`services/questionnaire_service.py`](../backend/src/auditfast/services/questionnaire_service.py)
  builds the questionnaire for a run — filtered by the selected pillars and the
  audited workspaces' layers — and, when the reviewer answers, scores each option
  **0–3** and merges the result into the report, **fanned out to every workspace
  whose layer the check applies to**.
- **Skipping records N/A, never a low score.** A blank self-assessment can't drag
  the number down.
- Each non-passing option carries `guidance`, which becomes the finding's
  recommendation — so interactive checks are **exempt from the `remediation.yaml`
  requirement** (their guidance lives on the options).
- Merging is **idempotent** and re-applied after the KB background refresh, so an
  answer can never be double-counted or lost.

The runtime wiring: submitting an audit computes the questionnaire and returns it
on `GET /api/v1/audit/{id}`; the frontend shows it while the crawl runs;
`POST /api/v1/audit/{id}/answers` records the answers, which are scored in as soon
as the automated crawl finishes.

---

## Adding a check

> **Assisted path.** Before writing one by hand, assess a plain-language
> best-practice point with `POST /api/v1/checklist/assess` (or the **Checklist**
> page): it tells you whether an existing check already covers it and, if not,
> drafts a proposal (pillar/scope/severity + a ready-to-edit `@check` skeleton +
> a remediation stub). The `.github/` agents (`checklist-author` →
> `check-researcher` → `check-implementer` → `check-reviewer`) then turn that
> proposal into a merged, tested check. The steps below are exactly what those
> agents automate — and what you follow to do it by hand. See
> [AGENTS.md §11](../AGENTS.md) and [`.github/README.md`](../.github/README.md).

### 1. Write it

Pick the `automated.py` under the pillar × layer it belongs to, e.g.
`core/check/data_management_quality/data_prep/automated.py`:

```python
@check(
    id="WS-DF-GEN1", ref="1.2.3",
    title="No deprecated Dataflow Gen1 items",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE,
    severity=Severity.MEDIUM,
    requires=[Resource.ITEMS],
)
def no_dataflow_gen1(ctx: CheckContext) -> Verdict:
    """Dataflow Gen1 items have been migrated to Gen2."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    gen1 = [i for i in ctx.workspace.items if i.type == "Dataflow"]
    return binary(not gen1, f"{len(gen1)} Gen1 dataflow(s) found")
```

Rules:

- **Declare `requires`.** It drives fetching. A check reading data it did not
  declare will see empty values.
- **Guard unavailable data** with `ctx.workspace.has(...)` → `not_applicable()`.
- **Write evidence as a fact, with numbers** — `"3 of 12 items carry a label"`,
  not `"labels are bad"`. Evidence lands verbatim in the report and risk register.
- **Return, don't raise.** A crash is caught and converted to an unscored `N/A`
  result, but that is a safety net, not a design.

### 2. Add remediation text

Keyed by `ref` in [`config/remediation.yaml`](../backend/config/remediation.yaml):

```yaml
"1.2.3": "Migrate Dataflow Gen1 items to Gen2 and retire the originals."
```

A missing key silently yields an empty recommendation, so a test asserts every
scoreable check's ref has text. It will fail if you skip this.

### 3. The loader finds it automatically

The check package **auto-discovers** every leaf module named `automated`,
`manual`, `questionnaire`, or `roadmap` by walking the tree — adding a check to an
existing `automated.py`, or creating a new `<pillar>/<layer>/automated.py`, needs
**no** `__init__.py` edit. Shared helpers must be named with a leading underscore
(e.g. `_spark.py`, `_pipeline.py`) so the loader skips them.

Registration is still an import side effect: `registered_modules()` and
`/api/v1/health`'s `checks_registered` exist so a missing registration is
visible rather than silent.

### 4. Add fixture data and tests

Give the check something to fail against in
[`tests/fixtures/tenant.json`](../backend/tests/fixtures/tenant.json), then
assert on it in [`tests/test_engine.py`](../backend/tests/test_engine.py). That
fixture is test-only infrastructure — see
[migration.md § Test strategy](migration.md#test-strategy) for why it is not
part of the shipped product.

`test_registry_is_fully_populated` pins the registry counts and the parity tests
pin the overall score — both will fail, correctly, and both need updating with
the new expected numbers.

### 5. Promoting a `roadmap` point to `automated`

`roadmap.py` modules are **generated** by [`build-manual-checks.py`](../../build-manual-checks.py).
To turn a roadmap attestation into a verified automated check:

1. write the evaluator in the pillar/layer `automated.py` with its `ref`;
2. add that `ref` to the `AUTOMATED` set in `build-manual-checks.py`;
3. run `python build-manual-checks.py` to regenerate the `roadmap.py` modules —
   the promoted `ref` drops out, so there is no duplicate-id clash;
4. add remediation text (step 2 above) and update the pinned test counts
   (step 4 above).

---

## Checklist

- [ ] Decorated with `@check(...)`, unique `id`, real checklist `ref`
- [ ] Returns a `Verdict` from a builder, never a raw `CheckResult`
- [ ] `requires` declares every resource it reads
- [ ] Guards unavailable data with `not_applicable()`
- [ ] Evidence states a fact and includes the numbers
- [ ] Remediation text added under the same `ref`
- [ ] Module named `automated` / `manual` / `questionnaire` / `roadmap` (auto-loaded); helpers prefixed `_`
- [ ] Fixture data added so it both passes and fails somewhere
- [ ] Tests added; registry counts and parity values updated
