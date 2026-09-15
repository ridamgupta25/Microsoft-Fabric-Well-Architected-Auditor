"""Evidence-intake: score manual checklist points from user-uploaded documents.

This package is additive and isolated from the deterministic audit. A user
uploads supporting documents; the documents are parsed, chunked and embedded; and
a grounded evaluator drafts a 0-3 score with citations that a human confirms. It
reuses the AI-config, RAG and guardrail machinery already built for custom-checks.

Not to be confused with :mod:`auditfast.ai.evidence` (per-object advisory
evidence for the deterministic checks) - that is unrelated.
"""
