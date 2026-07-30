# `intake/` — inputs the authoring agents read

Raw material a user drops in for the `checklist-author` agent (and the
`POST /api/v1/checklist/assess` endpoint) to work from. Nothing here is executed
during an audit; it only feeds the design-time authoring loop.

| Folder | What goes here |
|--------|----------------|
| `manual/` | Checklist points to assess/author, as CSV. See `checklist-points.example.csv`. |
| `diagrams/` | Architecture diagrams (target layout, data flow) the agent can reference. |
| `domain-reference/` | Domain notes, glossaries, and source-system specifics that inform pillar/scope choices. |

## Batch flow
Each row in a `manual/*.csv` is one checklist point. Assess them with the
Checklist page or the API; covered points map to an existing check id, uncovered
points produce a draft proposal the `checklist-author` agent finishes. Results and
proposals belong in [`../output`](../output).

Client-specific material is git-ignored via the repo's `Local/` convention — keep
tenant ids, GUIDs, and private diagrams out of history.
