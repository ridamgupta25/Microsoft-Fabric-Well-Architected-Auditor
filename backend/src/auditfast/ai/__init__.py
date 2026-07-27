"""AI layer — structure only, nothing implemented.

The auditor is and remains **deterministic**: every score comes from a fixed rule
with a fixed threshold, so the same input always produces the same number. AI is
additive and must never influence a score. Its job is to explain findings, draft
remediation prose, and answer questions about a report.

Two rules keep that boundary intact:

1. **Nothing in :mod:`auditfast.core` may import this package.** Scoring cannot
   depend on a model.
2. **Every AI dependency is optional** (``pip install auditfast[ai]``) and
   imported lazily, so the audit engine runs with none of it installed.

Intended structure:

* :mod:`.recommendations` — turn findings into richer remediation text.
* :mod:`.rag`             — retrieval over the checklist, Fabric docs, past audits.
* :mod:`.agents`          — task-scoped agents (triage, prioritisation, planning).
* :mod:`.orchestrator`    — routes a request to a model/agent; handles fallback.
* :mod:`.prompts`         — versioned prompt templates, kept out of code.
* :mod:`.embeddings`      — chunking and vector-store adapters.

Provider-agnostic by design: Azure OpenAI, Claude, and Gemini differ behind one
interface in :mod:`.orchestrator`, so switching model vendors is a config change.
"""
