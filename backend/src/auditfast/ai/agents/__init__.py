"""Task-scoped agents.

Deliberately narrow: a triage agent that clusters related findings, a
prioritisation agent that sequences remediation by effort and risk, a planning
agent that drafts a remediation roadmap. Each takes a finished report and
returns advice — none of them can trigger a re-score.
"""
