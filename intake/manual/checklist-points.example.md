# Example checklist (Markdown)
#
# One best-practice point per line. Lines starting with `#` (headings/comments),
# blank lines, and table rules are ignored; leading bullets (`-`, `*`) and
# checkboxes (`- [ ]`) are stripped. Run with:
#   auditfast checklist intake/manual/checklist-points.example.md

# Performance & Capacity

- [ ] Delta tables are OPTIMIZE-compacted after large writes
- [ ] Notebooks broadcast small dimension tables to avoid shuffle joins

# Security

- Row-level security is enforced on the semantic model
- Sensitivity labels are applied to every lakehouse

# Operations & Reliability

- Pipelines retry failed activities with backoff
