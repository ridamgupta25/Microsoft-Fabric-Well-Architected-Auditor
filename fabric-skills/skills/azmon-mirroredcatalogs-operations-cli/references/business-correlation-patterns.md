# Business correlation patterns reference

Reusable **observability signal → business impact** patterns, candidate join
keys, and scoring hints for ranking candidate business tables. Use these to guide
discovery and correlation planning — always confirm business meaning with the
user and verify keys against real data before relying on them.

## Domain-agnostic rule (examples only)

Every business entity named in this file — bookings, orders, customers, flights,
revenue, tenants, payments, and similar — is an **EXAMPLE ONLY**. Do NOT infer the
user's business domain from these examples. Discover the user's actual business
entities from real Fabric data and confirm them with the user before use.

Business tables are **not** expected to exist in Log Analytics. They may live in
an Eventhouse, KQL database, Warehouse, Lakehouse, or via Fabric shortcuts.
Absence of business tables in Log Analytics MUST NOT be read as absence of
business data.

## Patterns

### 1. Availability → Bookings
- **Operational signal:** `AppRequests` failures, `AvailabilityResults` failures,
  `AppExceptions`.
- **Business impact:** failed / incomplete bookings.
- **Candidate keys:** `BookingId`, `CustomerId`, `SessionId`, `OperationId`,
  custom `Properties`.

### 2. Latency → Conversion
- **Operational signal:** `AppRequests` duration, `AppPageViews`,
  `AppBrowserTimings`.
- **Business impact:** conversion drop, abandonment, funnel degradation.
- **Candidate keys:** `SessionId`, `UserId`, `CustomerId`, funnel event IDs.

### 3. Exceptions → Orders / Revenue
- **Operational signal:** `AppExceptions`, `AppTraces`.
- **Business impact:** failed orders, revenue at risk.
- **Candidate keys:** `OrderId`, `CustomerId`, `TenantId`, `OperationId`,
  `Properties`.

### 4. Dependency failures → Customer / Tenant impact
- **Operational signal:** `AppDependencies` failures / latency.
- **Business impact:** affected customers, tenants, regions, services.
- **Candidate keys:** `TenantId`, `AccountId`, `Region`, `DependencyTarget`.

### 5. Regional outage → Business KPI impact
- **Operational signal:** `CloudRoleName`, `Region`, `ClientCountryOrRegion`,
  location fields.
- **Business impact:** regional bookings, customers, transactions, SLA.
- **Candidate keys:** `Region`, `Country`, `Location`, `AirportCode`,
  `DataCenter`.

### 6. Traffic drop → Usage KPI degradation
- **Operational signal:** `AppEvents`, `AppPageViews`, `AppRequests` volume drop.
- **Business impact:** lower active users, reduced transactions, reduced revenue
  events.
- **Candidate keys:** `UserId`, `SessionId`, `ProductId`, event names.

## Business data scoring hints

When the exact business table or join is not known, **do not fail**. Score
candidate business databases/tables by likely relevance to the stated goal,
present a ranked list with explanation, propose shortcut creation for the top
candidates, and ask the user to choose. Never conclude "no correlation is
possible" without options.

| Business goal | Prioritize tables |
|---------------|-------------------|
| Booking impact | Bookings, Reservations, Orders, Flights, Customers, Transactions |
| Revenue impact | Orders, Payments, Revenue, Invoices, Subscriptions |
| Customer impact | Customers, Accounts, Tenants, Users, Subscriptions |
| Regional impact | Regions, Locations, Airports, DataCenters, Countries |
| Service availability | AppRequests, AvailabilityResults, AppExceptions, AppDependencies + business completion tables |
| Conversion | AppEvents, AppPageViews, AppRequests + funnel/business event tables |

## Candidate key reference

Infer candidates from column/table names; confirm meaning with the user and
verify against real data before relying on them.

- **Customer identifiers:** `CustomerId`, `UserId`, `PassportRef`, `AccountId`,
  `TenantId`, `SubscriptionId`.
- **Entity identifiers:** `FlightId`, `OrderId`, `BookingId`, `SessionId`,
  `OperationId`, `RequestId`, `ResourceId`.
- **Timestamps:** `TimeGenerated`, `Timestamp`, `EventTime`, `BookingTime`,
  `CreatedTime`.
- **Region/location:** `Region`, `Location`, `originAirport`,
  `destinationAirport`, `Country`, `DataCenter`.

## Table classification hints

- **Operational telemetry:** AppEvents, AppExceptions, AppRequests, AppPageViews,
  AppBrowserTimings, AppDependencies, AvailabilityResults, AzureMetrics, Perf,
  Usage.
- **Business tables:** Bookings, Customers, Flights, Orders, Payments,
  CounterCheckins, Subscriptions, Invoices.
- **Context/enrichment tables:** Flights, Regions, Products, Services, Tenants,
  Airports.

Confirm business meaning with the user; never fabricate tables the user has not
confirmed.
