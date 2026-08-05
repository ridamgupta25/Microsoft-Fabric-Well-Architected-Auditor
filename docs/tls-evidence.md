# TLS Evidence for Source Connections

## Purpose

Check `WS-TLS` (`6.3.4`) evaluates whether API and source connections use TLS
1.2 or newer. The Fabric Connections API is the source for connection identity
and configuration metadata, but it does not expose the negotiated or minimum
TLS protocol version.

The connection field `connectionEncryption = "Encrypted"` is not sufficient
evidence for TLS 1.2+. It only states that the connection attempts encryption;
it does not establish the minimum protocol version.

## Data Flow

```text
Fabric Connections API
  -> connection ID, type, endpoint, authentication, encryption mode

TLS evidence source
  -> explicit minimum TLS version and verification status

WorkspaceContext
  -> connections + tls_evidence

WS-TLS
  -> PASS / FAIL / N/A
```

## Connection Metadata

The live provider reads `GET https://api.fabric.microsoft.com/v1/connections`
and stores sanitized metadata in `WorkspaceContext.connections`:

| Field | Meaning |
|---|---|
| `id` | Fabric connection ID |
| `connection_type` | Source type, such as SQL or Web |
| `endpoint` | Source path or endpoint returned by Fabric |
| `credential_type` | Authentication credential type |
| `single_sign_on_type` | Entra ID, Kerberos, SAML, or none |
| `connection_encryption` | Fabric encryption mode |
| `connectivity_type` | Cloud, gateway, or virtual network connectivity |
| `gateway_id` | Associated gateway, where applicable |
| `last_credential_used_date_time` | Last recorded credential use |

Credentials, tokens, and secret values are never stored in the workspace
context or KB snapshot.

## TLS Evidence Contract

TLS evidence is keyed by Fabric connection ID. A future provider evidence source
should produce records in this shape:

```json
{
  "connection-123": {
    "minimum_tls_version": "TLS1.2",
    "status": "verified",
    "source": "Azure Resource Manager",
    "verified_at": "2026-08-04T12:00:00Z"
  }
}
```

The evidence source must be read-only and must identify where the result came
from. It must not be populated from `connectionEncryption` alone.

## Evidence Sources

Use the source system's configuration where possible:

| Source | Evidence to retrieve |
|---|---|
| Azure SQL | Server minimum TLS configuration |
| Azure Storage | Storage account `minimumTlsVersion` |
| App Service or API Management | Resource `minTlsVersion` |
| On-premises SQL or API | Gateway/server configuration or an approved TLS probe |
| External SaaS/API | Provider security configuration or an approved TLS probe |

Endpoint-to-resource mapping is not universal. Unsupported, ambiguous, or
unreachable sources must remain N/A rather than being inferred from the URL or
connection type.

## Verdict Rules

| Evidence | Result |
|---|---|
| Every applicable connection explicitly verifies TLS 1.2 or newer | PASS |
| Any applicable connection explicitly allows TLS below 1.2 | FAIL |
| TLS evidence is missing, unsupported, or unreadable | N/A |
| Fabric reports `Encrypted` but no minimum version is available | N/A |

The check is deliberately conservative: inability to obtain evidence is not
the same as evidence of an insecure connection.

## Promotion Path

To make `WS-TLS` produce live PASS/FAIL results, add a `TLS_EVIDENCE` resource,
populate `WorkspaceContext.tls_evidence` in the provider, persist it through
`to_dict()`/`from_dict()`, and declare it in the check's `requires` list. Keep
source-specific API calls in `clients/`; the check body must remain a pure
function of `CheckContext`.

Add tests for:

1. Explicit TLS 1.2 or TLS 1.3 evidence produces PASS.
2. Explicit TLS 1.0 or TLS 1.1 evidence produces FAIL.
3. Missing or unsupported evidence produces N/A.
4. Evidence is persisted and restored by the KB cache.