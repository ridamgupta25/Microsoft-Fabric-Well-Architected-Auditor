# Business insight modeling reference

How to turn a **verified** telemetry + business dataset into a correlation model
without asking the user for joins, KQL, bins, or thresholds. The user confirms
**business meaning**; the Skill translates it into the technical model.

## Inputs the model is inferred from

- Verified top-level schema (`getschema`).
- Verified dynamic fields (sampled `Properties` / `CustomDimensions`).
- Sampled values.
- Business table schema.
- Actual join test results (non-zero match counts).
- Data freshness (real min/max time range).

Never propose a model before these are verified (Stage 12).

## Direct join vs time-window correlation

- **Direct join (preferred, high confidence):** a shared identifier exists and a
  test join returns non-zero matches. Use it. State the match count.
- **Time-window correlation (fallback):** no shared identifier after inspecting
  top-level and dynamic fields. Correlate events within the same time bin. Always
  state that this shows **correlation, not causality**.

## Bins

- **Default bin size: 5 minutes.** Explain it as granular enough to catch short
  operational spikes while aggregating enough events to be meaningful and reduce
  per-event noise.
- Use a **larger** bin when data is **sparse** (few events → 15–60 min) or when
  business KPIs are **slower** (e.g. daily bookings). Adjust for very noisy /
  high-volume data to stabilize the signal.

## Derived entity

- Default derived entity name: **IncidentBin** (unless a better
  business-specific name exists).
- Each IncidentBin represents one time window and is the unit of analysis for
  metrics and alert rules.

## Confirming business meaning (not SQL)

Ask about meaning, e.g.:

> "I found `BookingId` inside `AppEvents.Properties` and it matches
> `Bookings.BookingId`. Should I treat this as the booking identifier for
> business impact?"

Do not ask "what join should I use?" or "what bin/threshold do you want?".

## Correlation plan contents (present before generating instructions)

- Operational signal (which table/event indicates the problem).
- Business impact table.
- Verified join keys.
- Whether the join came from **top-level columns** or **dynamic fields**
  (`Properties` / `CustomDimensions`).
- Match count / validation result.
- Time window + freshness status.
- Proposed bin size.
- Metrics and thresholds.

Then ask for confirmation and STOP.

### Correlation planning validation (REQUIRED)

When finalizing the correlation model, explain **why** the selected telemetry
source was chosen, justified with real data. Include: match counts, business
identifiers discovered, direct-join confidence, and validation results. The
selected telemetry source must be justified against actual data — never chosen by
default or by assumption. Candidate telemetry sources must have been inspected and
scored before selection (see app-insights-table-reference.md).

## Metrics (typical)

- `ErrorCount` — faulted operational events per IncidentBin.
- `AffectedCustomers` — distinct impacted customers per IncidentBin.
- `AffectedBusinessRecords` — impacted business records per IncidentBin.
- `RevenueAtRisk` — summed revenue of impacted records per IncidentBin.
- `TopImpactedEntities` — top impacted entities by impact.
- `ImpactBand` — high / low / none.

## Thresholds

Always include **both** sets in the generated instructions.

- **Production-like:**
  - ErrorCount increases by more than 20% vs previous-hour baseline.
  - AffectedCustomers ≥ configured business threshold.
  - RevenueAtRisk ≥ configured business threshold.
- **Relaxed POC / debug** (validate Start + Teams alert end-to-end):
  - `ErrorCount >= 1`
  - `ErrorCount >= 5 and AffectedBusinessRecords >= 1`
  - `AffectedCustomers >= 1`

## Freshness and time window

Check the real data range before generating instructions:

```kusto
TableName
| summarize Rows=count(), MinTime=min(TimeGenerated), MaxTime=max(TimeGenerated)
```

If data is old or sparse, do not assume `ago(1h)` — use a window covering the
actual range and explain it in user terms. Watch for locale-formatted timestamps
(DD/MM vs MM/DD) when reading results.

## Handoff to instruction generation

The materialization query output columns MUST match the alert fields exactly. See
operations-agent-reference.md for the explicit-KQL requirement and the full
instruction template.
