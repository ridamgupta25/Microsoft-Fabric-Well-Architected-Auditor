"""OAuth2 delegated (read-only) authentication via MSAL device-code flow.

Used by the CLI's ``run`` command to sign in before every audit. The device-code
flow works in a terminal: it prints a URL and code, the auditor signs in in a
browser, and we receive a read-only delegated access token. No secrets are
stored; only read scopes are requested.
"""
from __future__ import annotations


def acquire_token(tenant_id: str, client_id: str, scopes: list[str]) -> str:
    import msal  # local import so callers that never sign in need no dependency

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.PublicClientApplication(client_id, authority=authority)

    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to start device flow: {flow.get('error_description', flow)}")

    print("\n=== Sign in to your Fabric tenant (read-only) ===")
    print(flow["message"])  # includes the URL + code
    print("Waiting for you to complete sign-in in the browser...\n")

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" in result:
        return result["access_token"]
    raise RuntimeError(f"Authentication failed: {result.get('error_description', result)}")
