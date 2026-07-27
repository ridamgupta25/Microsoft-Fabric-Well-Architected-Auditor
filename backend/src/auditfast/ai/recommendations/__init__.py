"""Turn deterministic findings into richer remediation prose.

Input is the finding as scored — check id, evidence, severity, workspace — and
output is guidance only. The score is already fixed before this package is ever
called, and nothing here may change it.
"""
