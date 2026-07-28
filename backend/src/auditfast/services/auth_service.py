"""Read-only OAuth2 sign-in and the in-memory session store.

Framework-agnostic by design: plain functions that acquire a delegated,
**read-only** token via MSAL (interactive, device-code, or by reusing an
existing ``az login``) and stash it in a session keyed by an opaque id.

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

# Fabric read-only scopes requested by default (no write/admin scope ever).
_DEFAULT_FABRIC_SCOPES = [
    "https://api.fabric.microsoft.com/Workspace.Read.All",
    "https://api.fabric.microsoft.com/Item.Read.All",
]
# Microsoft's first-party Azure CLI public client - lets a user sign in with
# just their email when no app registration is available.
_AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
_POWERBI_DEFAULT_SCOPE = ["https://analysis.windows.net/powerbi/api/.default"]

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
        scopes = _POWERBI_DEFAULT_SCOPE
        using_builtin = True

    authority = (f"https://login.microsoftonline.com/{tenant}" if tenant
                 else "https://login.microsoftonline.com/organizations")
    try:
        app = msal.PublicClientApplication(client, authority=authority)
    except Exception as exc:
        raise AuthError(f"could not initialize sign-in: {exc}", 400) from exc

    sid = uuid.uuid4().hex
    sess = {"result": None, "error": None, "done": False, "user": None}
    _SESSIONS[sid] = sess

    def worker():
        try:
            res = app.acquire_token_interactive(scopes=scopes, login_hint=email or None)
            if "access_token" in res:
                sess["result"] = res["access_token"]
                sess["user"] = _profile_from_claims(res.get("id_token_claims"))
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
                      "user": _profile_from_claims(_decode_jwt_claims(token))}
    return {"session": sid, "status": "done", "message": "Signed in via Azure CLI."}


def start_device_flow(tenant_id, client_id, scopes) -> dict:
    """Begin a device-code sign-in (used by the CLI / headless scenarios)."""
    try:
        import msal
    except ImportError as exc:
        raise AuthError("msal is not installed", 500) from exc
    if not tenant_id or not client_id:
        raise AuthError("tenant_id and client_id are required", 400)
    scopes = scopes or _DEFAULT_FABRIC_SCOPES
    try:
        app = msal.PublicClientApplication(
            client_id, authority=f"https://login.microsoftonline.com/{tenant_id}")
        flow = app.initiate_device_flow(scopes=scopes)
    except Exception as exc:
        raise AuthError(f"could not start sign-in: {exc}", 400) from exc
    if "user_code" not in flow:
        raise AuthError(flow.get("error_description", "device flow failed"), 400)

    sid = uuid.uuid4().hex
    sess = {"result": None, "error": None, "done": False, "user": None}
    _SESSIONS[sid] = sess

    def worker():
        try:
            res = app.acquire_token_by_device_flow(flow)
            if "access_token" in res:
                sess["result"] = res["access_token"]
                sess["user"] = _profile_from_claims(res.get("id_token_claims"))
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
