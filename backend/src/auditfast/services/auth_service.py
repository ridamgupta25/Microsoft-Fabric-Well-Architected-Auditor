"""Read-only OAuth2 sign-in and the in-memory session store.

Framework-agnostic by design: plain functions that acquire a delegated,
**least-privilege** token via MSAL (interactive, device-code, or by reusing an
existing ``az login``) and stash it in a session keyed by an opaque id. The tool
only ever reads; the single ReadWrite scope exists solely because Fabric gates
``getDefinition`` (a read of an item's content) behind it.

The token never leaves the server — clients hold only the session id, so a
compromised browser cannot yield a Fabric access token. Tokens live for the
process lifetime only and are never written to disk or logged.

:class:`AuthError` carries the HTTP status the API should use; the handler in
:mod:`auditfast.api.errors` translates it, so this module needs no knowledge of
the transport.

.. note::
   ``_SESSIONS`` is process-local, so sign-in does not survive a restart and is
   not shared across replicas. Horizontal scaling requires moving it to a shared
   cache (Redis) or adopting a token-per-request model.
"""
from __future__ import annotations

import base64
import json
import threading
import uuid

# Fabric scopes requested at sign-in. Item.ReadWrite.All is required only because
# Fabric gates getDefinition (reading an item's *content* — e.g. a notebook or
# pipeline) behind a ReadWrite scope; the tool still issues reads exclusively and
# never writes. No admin or tenant-settings scope is ever requested.
_DEFAULT_FABRIC_SCOPES = [
    "https://api.fabric.microsoft.com/Workspace.Read.All",
    "https://api.fabric.microsoft.com/Item.ReadWrite.All",
    "https://api.fabric.microsoft.com/OneLake.Read.All",
]
# Microsoft's first-party Azure CLI public client - lets a user sign in with
# just their email when no app registration is available.
_AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
_POWERBI_DEFAULT_SCOPE = ["https://analysis.windows.net/powerbi/api/.default"]
#: The SQL analytics endpoint (TDS, port 1433). A third audience alongside Fabric
#: and Power BI: Fabric REST *discovers* the endpoint, TDS *reads* it. The Azure
#: CLI public client is pre-authorized for it, so the SQL token comes silently
#: from the refresh token minted at sign-in - the user is never prompted twice.
_SQL_DEFAULT_SCOPE = ["https://database.windows.net/.default"]
#: The OneLake data plane (DFS / Blob / Table). OneLake only accepts tokens in the
#: *Storage* audience ``https://storage.azure.com`` - a fourth audience alongside
#: Fabric, Power BI and SQL. Reading Lakehouse table *column schemas* over the
#: OneLake Table (Unity-Catalog) API needs this token, not the Fabric one; the
#: Azure CLI public client is pre-authorized for it, so it too is minted silently
#: from the sign-in refresh token.
_ONELAKE_DEFAULT_SCOPE = ["https://storage.azure.com/.default"]
#: The Fabric API, requested as .default so the built-in Azure CLI client's full
#: set of pre-authorized Fabric permissions (including the item read/write needed
#: for getDefinition) is included. Power BI .default did not carry them.
_FABRIC_DEFAULT_SCOPE = ["https://api.fabric.microsoft.com/.default"]

# session_id -> {"result": <token>|None, "error": <str>|None, "done": <bool>}
_SESSIONS: dict[str, dict] = {}


class AuthError(Exception):
    """Raised for a sign-in problem; carries the HTTP status the API should use."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _clean(value):
    """Treat blank or ``<placeholder>`` config values as 'not set'."""
    return None if (not value or str(value).startswith("<")) else value


def _decode_jwt_claims(token: str | None) -> dict:
    """Best-effort decode of a JWT payload, for display only (never verified).

    Used to read a friendly name/username out of an Azure CLI access token, which
    arrives as a raw JWT with no accompanying id-token claims.
    """
    if not token or token.count(".") < 2:
        return {}
    try:
        payload = token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload + padding))
    except Exception:
        return {}


def _profile_from_claims(claims: dict | None) -> dict:
    """Pull a display name + username out of id-token / access-token claims."""
    claims = claims or {}
    username = (claims.get("preferred_username") or claims.get("upn")
                or claims.get("unique_name") or claims.get("email"))
    return {"name": claims.get("name") or username, "username": username}


def token_for(session_id: str | None) -> str | None:
    """Return the access token for a completed session, or ``None``."""
    sess = _SESSIONS.get(session_id or "")
    return sess["result"] if sess else None


def powerbi_token_for(session_id: str | None) -> str | None:
    """Mint a Power BI-audience token for a signed-in session, or ``None``.

    Semantic-model refresh history lives only on the Power BI Datasets API
    (audience ``https://analysis.windows.net/powerbi/api``) — a different
    audience from the Fabric token the crawl uses. This acquires that token
    *silently* from the session's existing MSAL cache, or, for an az-cli
    session, by re-invoking ``az`` for the Power BI resource. It never prompts;
    on any failure it returns ``None`` and the crawl simply leaves semantic-model
    recency unknown rather than guessing.
    """
    sess = _SESSIONS.get(session_id or "")
    if not sess:
        return None
    app = sess.get("_msal_app")
    account = sess.get("_msal_account")
    if app and account:
        try:
            res = app.acquire_token_silent(_POWERBI_DEFAULT_SCOPE, account=account)
            if res and "access_token" in res:
                return res["access_token"]
        except Exception:
            pass
    if sess.get("_azcli"):
        return _azcli_powerbi_token()
    return None


def _azcli_powerbi_token() -> str | None:
    """Get a Power BI-audience token from an existing ``az login``, or ``None``."""
    return _azcli_token("https://analysis.windows.net/powerbi/api")


def sql_token_for(session_id: str | None) -> str | None:
    """Mint a SQL-analytics-endpoint token for a signed-in session, or ``None``.

    Lakehouse/Warehouse column schemas and Warehouse security policies are not in
    the Fabric REST API at all - they are only readable over TDS against the SQL
    analytics endpoint, whose audience is ``https://database.windows.net``. The
    endpoint address itself *is* discoverable over REST, so the user is never
    asked for a connection string.

    Acquired *silently* from the session's existing MSAL refresh token (the Azure
    CLI public client is pre-authorized for this resource), so one sign-in covers
    Fabric, Power BI and SQL. It never prompts; on any failure it returns ``None``
    and the crawl leaves column schemas empty rather than guessing - exactly the
    behaviour before the SQL endpoint existed.
    """
    sess = _SESSIONS.get(session_id or "")
    if not sess:
        return None
    app = sess.get("_msal_app")
    account = sess.get("_msal_account")
    if app and account:
        try:
            res = app.acquire_token_silent(_SQL_DEFAULT_SCOPE, account=account)
            if res and "access_token" in res:
                return res["access_token"]
        except Exception:
            pass
    if sess.get("_azcli"):
        return _azcli_token("https://database.windows.net")
    return None


def onelake_token_for(session_id: str | None) -> str | None:
    """Mint a OneLake *Storage*-audience token for a signed-in session, or ``None``.

    Lakehouse table **column schemas** are served by the OneLake Table
    (Unity-Catalog) API, whose host ``onelake.table.fabric.microsoft.com`` only
    accepts tokens in the ``https://storage.azure.com`` audience - a different
    audience from the Fabric token the crawl uses. This acquires that token
    *silently* from the session's existing MSAL cache, or, for an az-cli session,
    by re-invoking ``az`` for the storage resource. It never prompts; on any
    failure it returns ``None`` and the crawl falls back to the SQL endpoint (or
    leaves column schemas empty), never guessing.
    """
    sess = _SESSIONS.get(session_id or "")
    if not sess:
        return None
    app = sess.get("_msal_app")
    account = sess.get("_msal_account")
    if app and account:
        try:
            res = app.acquire_token_silent(_ONELAKE_DEFAULT_SCOPE, account=account)
            if res and "access_token" in res:
                return res["access_token"]
        except Exception:
            pass
    if sess.get("_azcli"):
        return _azcli_token("https://storage.azure.com")
    return None


def _azcli_token(resource: str) -> str | None:
    """Get a token for ``resource`` from an existing ``az login``, or ``None``."""
    import shutil
    import subprocess

    if not shutil.which("az"):
        return None
    try:
        out = subprocess.run(
            ["az", "account", "get-access-token", "--resource", resource,
             "--output", "json"],
            capture_output=True, text=True, timeout=90)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout).get("accessToken")
    except Exception:
        return None


def make_token_refresher(session_id: str | None):
    """Return a callable that silently refreshes the token, or None.

    The returned function, when called, attempts to acquire a fresh token using
    MSAL's cached refresh token. On success it updates the session and returns
    the new access token; on failure it returns None.
    """
    sess = _SESSIONS.get(session_id or "")
    if not sess:
        return None
    app = sess.get("_msal_app")
    account = sess.get("_msal_account")
    scopes = sess.get("_msal_scopes")
    if not app or not account or not scopes:
        # az-cli sessions: re-invoke az to get a fresh token
        if sess.get("_azcli"):
            return _make_azcli_refresher(sess)
        return None

    def _refresh() -> str | None:
        try:
            res = app.acquire_token_silent(scopes, account=account)
            if res and "access_token" in res:
                sess["result"] = res["access_token"]
                return res["access_token"]
        except Exception:
            pass
        return None

    return _refresh


def _make_azcli_refresher(sess: dict):
    """Refresher for az-cli sessions — re-invokes az to get a fresh token."""
    import shutil
    import subprocess

    def _refresh() -> str | None:
        if not shutil.which("az"):
            return None
        try:
            out = subprocess.run(
                ["az", "account", "get-access-token", "--resource",
                 "https://api.fabric.microsoft.com", "--output", "json"],
                capture_output=True, text=True, timeout=90)
        except Exception:
            return None
        if out.returncode != 0:
            return None
        try:
            token = json.loads(out.stdout).get("accessToken")
        except Exception:
            return None
        if token:
            sess["result"] = token
        return token

    return _refresh


def logout(session_id: str | None) -> bool:
    """Discard a session's token. Returns whether there was one to discard.

    Tokens live only in this process and are never written to disk, so dropping
    the entry is a complete sign-out.
    """
    return _SESSIONS.pop(session_id or "", None) is not None


def me(session_id: str | None) -> dict:
    """Return the signed-in user's display profile for a session (never a token)."""
    sess = _SESSIONS.get(session_id or "")
    if not sess or not sess.get("result"):
        return {"signed_in": False, "name": None, "username": None}
    user = sess.get("user") or {}
    return {"signed_in": True, "name": user.get("name"), "username": user.get("username")}


def poll(session_id: str | None) -> dict:
    """Report a sign-in session's status: pending / done / error."""
    sess = _SESSIONS.get(session_id or "")
    if not sess:
        return {"status": "error", "error": "unknown session"}
    if sess["error"]:
        return {"status": "error", "error": sess["error"]}
    if sess["result"]:
        return {"status": "done"}
    return {"status": "pending"}


def login_interactive(email: str, tenant_id, client_id, auth_cfg: dict) -> dict:
    """Open the Microsoft sign-in in a browser (email-first) on a background
    thread and return a session id the caller can poll."""
    try:
        import msal
    except ImportError as exc:
        raise AuthError("msal is not installed", 500) from exc

    client = _clean(client_id) or _clean(auth_cfg.get("client_id"))
    tenant = _clean(tenant_id) or _clean(auth_cfg.get("tenant_id"))
    scopes = auth_cfg.get("scopes") or _DEFAULT_FABRIC_SCOPES

    # No app registered? Fall back to Microsoft's built-in Azure CLI client so
    # the user can sign in with just their email (no admin needed). Some tenants
    # block this via Conditional Access - then a client id must be supplied.
    using_builtin = False
    if not client:
        client = _AZURE_CLI_CLIENT_ID
        # Target the Fabric API with .default so the built-in client's full set of
        # pre-authorized Fabric permissions (item read/write, needed to read
        # notebook/pipeline/semantic-model definitions) is included.
        scopes = _FABRIC_DEFAULT_SCOPE
        using_builtin = True

    authority = (f"https://login.microsoftonline.com/{tenant}" if tenant
                 else "https://login.microsoftonline.com/organizations")
    try:
        app = msal.PublicClientApplication(client, authority=authority)
    except Exception as exc:
        raise AuthError(f"could not initialize sign-in: {exc}", 400) from exc

    sid = uuid.uuid4().hex
    sess = {"result": None, "error": None, "done": False, "user": None,
            "_msal_app": app, "_msal_account": None, "_msal_scopes": scopes}
    _SESSIONS[sid] = sess

    def worker():
        try:
            res = app.acquire_token_interactive(scopes=scopes, login_hint=email or None)
            if "access_token" in res:
                sess["result"] = res["access_token"]
                sess["user"] = _profile_from_claims(res.get("id_token_claims"))
                accounts = app.get_accounts()
                if accounts:
                    sess["_msal_account"] = accounts[0]
            else:
                sess["error"] = res.get("error_description", "authentication failed")
        except Exception as exc:  # pragma: no cover
            sess["error"] = str(exc)
        finally:
            sess["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    note = (" (using Microsoft's built-in sign-in - no app registration needed)"
            if using_builtin else "")
    return {
        "session": sid,
        "message": f"A browser window is opening{(' for ' + email) if email else ''}"
                   f"{note} - complete the Microsoft sign-in there.",
    }


def login_azcli() -> dict:
    """Reuse an existing ``az login`` session - no app registration needed."""
    import shutil
    import subprocess

    if not shutil.which("az"):
        raise AuthError(
            "Azure CLI (az) is not installed. Install it without admin via "
            "'winget install -e --id Microsoft.AzureCLI', run 'az login', then retry.", 400)
    try:
        out = subprocess.run(
            ["az", "account", "get-access-token", "--resource",
             "https://api.fabric.microsoft.com", "--output", "json"],
            capture_output=True, text=True, timeout=90)
    except Exception as exc:
        raise AuthError(str(exc), 500) from exc
    if out.returncode != 0:
        raise AuthError("Not signed in to Azure CLI. Run 'az login' first. "
                        + (out.stderr or "").strip()[:300], 400)
    try:
        token = json.loads(out.stdout).get("accessToken")
    except Exception:
        token = None
    if not token:
        raise AuthError("Could not read token from Azure CLI.", 400)

    sid = uuid.uuid4().hex
    _SESSIONS[sid] = {"result": token, "error": None, "done": True,
                      "user": _profile_from_claims(_decode_jwt_claims(token)),
                      "_azcli": True}
    return {"session": sid, "status": "done", "message": "Signed in via Azure CLI."}


def start_device_flow(tenant_id, client_id, scopes) -> dict:
    """Begin a device-code sign-in — the browser-based flow for a hosted app.

    The user opens the returned ``verification_uri`` in **their own** browser,
    enters the ``user_code``, and completes sign-in there; the token is acquired
    and stored server-side, so it never reaches the browser. This is the flow to
    use when the app is hosted/remote (a tunnel, a server), because the
    interactive flow would open a browser on the *server* instead.

    Like the interactive flow, it falls back to Microsoft's built-in Azure CLI
    public client so **no app registration is required**.
    """
    try:
        import msal
    except ImportError as exc:
        raise AuthError("msal is not installed", 500) from exc

    client = _clean(client_id)
    tenant = _clean(tenant_id)
    scopes = scopes or _DEFAULT_FABRIC_SCOPES

    # No app registered? Use Microsoft's built-in Azure CLI client so the user can
    # sign in with just their Microsoft account (no admin needed), targeting the
    # Fabric API with .default to include its pre-authorized permissions.
    if not client:
        client = _AZURE_CLI_CLIENT_ID
        scopes = _FABRIC_DEFAULT_SCOPE

    authority = (f"https://login.microsoftonline.com/{tenant}" if tenant
                 else "https://login.microsoftonline.com/organizations")
    try:
        app = msal.PublicClientApplication(client, authority=authority)
        flow = app.initiate_device_flow(scopes=scopes)
    except Exception as exc:
        raise AuthError(f"could not start sign-in: {exc}", 400) from exc
    if "user_code" not in flow:
        raise AuthError(flow.get("error_description", "device flow failed"), 400)

    sid = uuid.uuid4().hex
    sess = {"result": None, "error": None, "done": False, "user": None,
            "_msal_app": app, "_msal_account": None, "_msal_scopes": scopes}
    _SESSIONS[sid] = sess

    def worker():
        try:
            res = app.acquire_token_by_device_flow(flow)
            if "access_token" in res:
                sess["result"] = res["access_token"]
                sess["user"] = _profile_from_claims(res.get("id_token_claims"))
                accounts = app.get_accounts()
                if accounts:
                    sess["_msal_account"] = accounts[0]
            else:
                sess["error"] = res.get("error_description", "authentication failed")
        except Exception as exc:  # pragma: no cover
            sess["error"] = str(exc)
        finally:
            sess["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return {
        "session": sid,
        "user_code": flow.get("user_code"),
        "verification_uri": flow.get("verification_uri"),
        "message": flow.get("message"),
        "expires_in": flow.get("expires_in"),
    }


# -- redirect Authorization Code flow (hosted web sign-in) --------------------
# The standard browser redirect flow for a hosted app: the user signs in on
# Microsoft's page in their own browser and is redirected back with a code, which
# the server exchanges for a token. Needs an Entra app registration whose redirect
# URI matches the frontend's callback. Pending flows are held server-side, keyed
# by the CSRF `state` MSAL generates, between the redirect out and the callback.
_PENDING_FLOWS: dict[str, dict] = {}
_FLOWS_LOCK = threading.Lock()
#: Cap the pending-flow store so an abandoned-sign-in flood can't grow it forever.
_MAX_PENDING_FLOWS = 500


def _auth_code_app(client_id, tenant, client_secret):
    """Build the MSAL app for the redirect flow — confidential if a secret is set."""
    import msal

    authority = f"https://login.microsoftonline.com/{tenant or 'organizations'}"
    if client_secret:
        return msal.ConfidentialClientApplication(
            client_id, authority=authority, client_credential=client_secret
        )
    return msal.PublicClientApplication(client_id, authority=authority)


def start_auth_code_flow(
    redirect_uri: str, client_id, tenant_id, client_secret, scopes=None
) -> dict:
    """Begin the redirect flow: return the Microsoft URL to send the user to.

    ``redirect_uri`` must exactly match one registered on the Entra app. The
    returned ``auth_url`` is where the browser is sent; MSAL's ``state`` ties the
    eventual callback back to this flow (and defends against CSRF).
    """
    try:
        import msal  # noqa: F401
    except ImportError as exc:
        raise AuthError("msal is not installed", 500) from exc

    client = _clean(client_id)
    if not client:
        raise AuthError(
            "Redirect sign-in is not configured. Set AUDITFAST_AUTH_CLIENT_ID and "
            "AUDITFAST_AUTH_TENANT_ID on the server.", 400)
    if not redirect_uri:
        raise AuthError("a redirect_uri is required", 400)

    app = _auth_code_app(client, _clean(tenant_id), _clean(client_secret))
    try:
        flow = app.initiate_auth_code_flow(
            scopes=scopes or _DEFAULT_FABRIC_SCOPES, redirect_uri=redirect_uri
        )
    except Exception as exc:
        raise AuthError(f"could not start sign-in: {exc}", 400) from exc
    if "auth_uri" not in flow:
        raise AuthError(flow.get("error_description", "could not start sign-in"), 400)

    with _FLOWS_LOCK:
        if len(_PENDING_FLOWS) >= _MAX_PENDING_FLOWS:
            _PENDING_FLOWS.clear()  # drop stale/abandoned flows wholesale
        _PENDING_FLOWS[flow["state"]] = flow
    return {"auth_url": flow["auth_uri"], "state": flow["state"]}


def complete_auth_code_flow(
    auth_response: dict, client_id, tenant_id, client_secret, scopes=None
) -> dict:
    """Finish the redirect flow: exchange the returned code for a token.

    ``auth_response`` is the set of query parameters Microsoft redirected back
    with (``code``, ``state``, …). The token is stored server-side and only a
    session id is returned to the browser.
    """
    try:
        import msal  # noqa: F401
    except ImportError as exc:
        raise AuthError("msal is not installed", 500) from exc

    state = (auth_response or {}).get("state")
    if not state:
        raise AuthError("missing state in the sign-in response", 400)
    with _FLOWS_LOCK:
        flow = _PENDING_FLOWS.pop(state, None)
    if flow is None:
        raise AuthError("this sign-in expired or was already used — please try again", 400)
    if auth_response.get("error"):
        raise AuthError(
            auth_response.get("error_description") or auth_response["error"], 400)

    app = _auth_code_app(_clean(client_id), _clean(tenant_id), _clean(client_secret))
    try:
        res = app.acquire_token_by_auth_code_flow(
            flow, auth_response, scopes=scopes or _DEFAULT_FABRIC_SCOPES
        )
    except Exception as exc:
        raise AuthError(f"sign-in failed: {exc}", 400) from exc
    if "access_token" not in res:
        raise AuthError(res.get("error_description", "authentication failed"), 400)

    sid = uuid.uuid4().hex
    _SESSIONS[sid] = {
        "result": res["access_token"], "error": None, "done": True,
        "user": _profile_from_claims(res.get("id_token_claims")),
    }
    return {"session": sid, "status": "done", "message": "Signed in."}
