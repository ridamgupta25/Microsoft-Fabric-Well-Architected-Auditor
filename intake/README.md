# `intake/` — inputs the authoring agents read

Raw material a user drops in for the `checklist-author` agent (and the
`POST /api/v1/checklist/assess` endpoint) to work from. Nothing here is executed
during an audit; it only feeds the design-time authoring loop.

| Folder | What goes here |
|--------|----------------|
| `manual/` | Checklist points to assess/author or run in bulk — CSV, JSON, or Markdown. See `checklist-points.example.{csv,json,md}`. |
| `diagrams/` | Architecture diagrams (target layout, data flow) the agent can reference. |
| `domain-reference/` | Domain notes, glossaries, and source-system specifics that inform pillar/scope choices. |

## Batch flow
Each point in a `manual/` file is one checklist statement, in any of three
formats:

- **CSV** — a `point` column, plus optional `pillar`, `scope`, `notes`.
- **JSON** — an array of strings, an array of `{point, pillar?, scope?, notes?}`
  objects, or `{ "points": [ … ] }`.
- **Markdown / plain text** — one point per line; headings and bullets/checkboxes
  are stripped.

Assess and run them with the **Checklist** page's *Run a custom checklist* upload,
the CLI (`auditfast checklist manual/<file>`), the `POST /api/v1/checklist/batch`
endpoint, or the MCP `assess_checklist_batch` tool. Covered points map to an
existing check id and are evaluated over the offline knowledge base; uncovered
points produce a draft proposal the `checklist-author` agent finishes. Results and
proposals belong in [`../output`](../output).

Client-specific material is git-ignored via the repo's `Local/` convention — keep
tenant ids, GUIDs, and private diagrams out of history.
