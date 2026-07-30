# Application Insights / Azure Monitor table reference

Common Application Insights / Azure Monitor (Log Analytics) telemetry tables, what
signal each contains, the operational questions it answers, and how it can
correlate with business data.

> **Not a schema guarantee.** These are common shapes only. The **actual** schema
> must always be verified against the real Eventhouse / KQL database with
> `getschema` and sampling (see app-insights-dynamic-fields-reference.md). Column
> names vary by app, SDK, and ingestion settings.

## External references

- [Azure Monitor tables for microsoft.insights/components (learn)](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/microsoft-insights-components)
- [Azure Monitor Logs table reference](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/)

## Workspace-based Application Insights tables (`App*`)

### AppRequests
- **Signal:** incoming server requests — success/failure, duration, result code,
  operation name, endpoint.
- **Operational questions:** Which endpoints fail or are slow? What is availability
  / p95 latency? Where are 5xx spikes?
- **Business correlation:** conversion, booking completion, checkout failures, API
  SLA.
- **Common time column:** `TimeGenerated`.
- **Common identifying fields:** `OperationId`, `Id`, `OperationName`, `Url`,
  `ResultCode`, `Success`, `DurationMs`, `Name`.
- **Common dynamic fields:** `Properties`.

### AppDependencies
- **Signal:** outbound/downstream dependency calls (DB, HTTP, queue) — success,
  duration, target, type.
- **Operational questions:** Which downstream dependency failed or slowed? Which
  target/region is degraded?
- **Business correlation:** payment failures, inventory lookup failures, regional
  dependency outages.
- **Common time column:** `TimeGenerated`.
- **Common identifying fields:** `OperationId`, `Target`, `DependencyType`,
  `Name`, `Success`, `DurationMs`, `ResultCode`.
- **Common dynamic fields:** `Properties`.

### AppExceptions
- **Signal:** application exceptions / failures — type, method, outer message,
  problem id.
- **Operational questions:** What is failing and where? Which exception types
  spike during an incident?
- **Business correlation:** failed bookings, failed orders, customer-impacting
  errors.
- **Common time column:** `TimeGenerated`.
- **Common identifying fields:** `OperationId`, `ProblemId`, `ExceptionType`,
  `Method`, `OuterMessage`.
- **Common dynamic fields:** `Properties`, `Details`.

### AppTraces
- **Signal:** diagnostic traces / structured logs — message, severity.
- **Operational questions:** What diagnostic evidence supports a failure or
  degraded behavior?
- **Business correlation:** supporting evidence for failures or degraded service;
  business keys sometimes embedded in `Properties`.
- **Common time column:** `TimeGenerated`.
- **Common identifying fields:** `OperationId`, `Message`, `SeverityLevel`.
- **Common dynamic fields:** `Properties`.

### AppEvents
- **Signal:** custom business/product events emitted by the app (e.g.
  `BookingStarted`, `CheckoutCompleted`).
- **Operational questions:** Which funnel step dropped? Which product events
  stopped firing during an incident?
- **Business correlation:** funnel steps, booking started/completed, checkout,
  sign-in — often the **richest** source of business keys in `Properties`.
- **Common time column:** `TimeGenerated`.
- **Common identifying fields:** `Name`, `OperationId`, `UserId`, `SessionId`.
- **Common dynamic fields:** `Properties`, `Measurements`.

### AppPageViews
- **Signal:** frontend page views — page name, url, load duration.
- **Operational questions:** Which pages are slow or abandoned?
- **Business correlation:** conversion, abandonment, user-journey drop-off.
- **Common time column:** `TimeGenerated`.
- **Common identifying fields:** `Name`, `Url`, `OperationId`, `UserId`,
  `SessionId`, `DurationMs`.
- **Common dynamic fields:** `Properties`.

### AppBrowserTimings
- **Signal:** client-side timing (network, send, receive, processing, total).
- **Operational questions:** Where is frontend latency introduced?
- **Business correlation:** conversion / abandonment tied to page latency.
- **Common time column:** `TimeGenerated`.
- **Common identifying fields:** `Name`, `Url`, `OperationId`,
  `TotalDuration`.
- **Common dynamic fields:** `Properties`.

### AppAvailabilityResults / AvailabilityResults (if present)
- **Signal:** synthetic availability tests — success, duration, location.
- **Operational questions:** Is the service reachable from each test location?
- **Business correlation:** availability failures → booking/checkout completion,
  SLA.
- **Common time column:** `TimeGenerated`.
- **Common identifying fields:** `Name`, `Location`, `Success`, `DurationMs`,
  `OperationId`.
- **Common dynamic fields:** `Properties`.

### AppMetrics (where useful)
- **Signal:** custom metrics (name, value, aggregations).
- **Business correlation:** business-relevant custom metrics (e.g. cart value,
  active sessions) by time.
- **Common time column:** `TimeGenerated`.
- **Common identifying fields:** `Name`, `Sum`, `Count`, `ItemCount`.
- **Common dynamic fields:** `Properties`.

### AppPerformanceCounters (where useful)
- **Signal:** host/process performance counters (CPU, memory, etc.).
- **Business correlation:** resource saturation preceding user-facing degradation.
- **Common time column:** `TimeGenerated`.
- **Common identifying fields:** `Name`, `Category`, `Value`, `Instance`.

### AppSystemEvents (where useful)
- **Signal:** SDK/system events about the telemetry pipeline.
- **Business correlation:** rarely a direct business key; useful for data-quality
  context.
- **Common time column:** `TimeGenerated`.

### Usage-related tables (where relevant)
- **Signal:** usage/volume of events, page views, sessions over time.
- **Business correlation:** traffic drop → usage KPI degradation (active users,
  transactions, revenue events).

## Common cloud/role/location fields (across App* tables)

Useful for regional / service-scoping of impact:
`AppRoleName` / `CloudRoleName`, `AppRoleInstance`, `ClientCountryOrRegion`,
`ClientCity`, `ClientType`, `OperationName`. Confirm presence via `getschema`.

## Observability → business quick map

| Table | Typical business impact |
|-------|------------------------|
| AppRequests | conversion, booking completion, checkout failures, API SLA |
| AppExceptions | failed bookings/orders, customer-impacting errors |
| AppDependencies | payment/inventory failures, regional dependency outages |
| AppEvents | funnel steps, booking/checkout/sign-in events |
| AppPageViews / AppBrowserTimings | conversion, abandonment, journey drop-off |
| AppTraces | supporting evidence for failures / degradation |
| AppAvailabilityResults | availability failures → booking/SLA impact |

Always confirm the real schema before relying on any field above.

## Telemetry source selection (data-driven, REQUIRED)

No telemetry table is "best" by default. The winning source in one environment
(e.g. AppEvents) is **not** a general rule. Telemetry source selection MUST be
data-driven.

Before selecting a correlation model, inspect **all** candidate telemetry sources
discovered (AppEvents, AppExceptions, AppRequests, AppDependencies, AppTraces,
AppPageViews, AppBrowserTimings, AvailabilityResults, and any others present).
Score each candidate by:

1. Business identifiers discovered.
2. Dynamic-field richness.
3. Direct-join confidence.
4. Validated match count.
5. Business-process context.
6. Relevance to the selected business goal.

Select the highest-scoring source. Do NOT automatically prioritize AppExceptions.
Do NOT automatically prioritize AppEvents.
