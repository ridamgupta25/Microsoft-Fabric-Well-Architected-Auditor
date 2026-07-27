/**
 * Read-only Microsoft Entra sign-in for live mode.
 *
 * Sign-in is asynchronous: a flow is started, a browser window opens on the
 * server host, and this page polls until it resolves. The browser only ever
 * receives an opaque session id — the Fabric access token stays server-side.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ErrorBanner, Section, Spinner } from "@/components/ui";
import { useAuditContext } from "@/context/AuditContext";
import { getDiagnostics } from "@/services/auditService";
import { loginWithAzureCli, logout, startInteractiveLogin, waitForSignIn } from "@/services/authService";
import type { Diagnostics } from "@/types/api";

export function SignInPage() {
  const navigate = useNavigate();
  const { session, setSession, isSignedIn, setMode } = useAuditContext();

  const [email, setEmail] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Stop polling if the user navigates away mid sign-in.
  useEffect(() => () => abortRef.current?.abort(), []);

  const finish = useCallback(
    (newSession: string) => {
      setSession(newSession);
      setMode("live");
      setStatus("Signed in. Live mode is now active.");
    },
    [setSession, setMode],
  );

  const signInInteractive = useCallback(async () => {
    setError(null);
    setBusy(true);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const started = await startInteractiveLogin({
        email: email || undefined,
        tenantId: tenantId || undefined,
        clientId: clientId || undefined,
      });
      setStatus(started.message);
      const confirmed = await waitForSignIn(started.session, controller.signal);
      finish(confirmed);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : String(err));
      setStatus(null);
    } finally {
      setBusy(false);
    }
  }, [email, tenantId, clientId, finish]);

  const signInAzureCli = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const result = await loginWithAzureCli();
      finish(result.session);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [finish]);

  const signOut = useCallback(async () => {
    if (!session) return;
    try {
      await logout(session);
    } finally {
      setSession(null);
      setDiagnostics(null);
      setStatus("Signed out.");
    }
  }, [session, setSession]);

  const runDiagnostics = useCallback(async () => {
    if (!session) return;
    setError(null);
    setBusy(true);
    try {
      setDiagnostics(await getDiagnostics(session));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [session]);

  return (
    <div className="space-y-6">
      <Section
        title="Sign in to Microsoft Fabric"
        description="Read-only. The tool never writes to your tenant, and the access token never reaches the browser."
      >
        {error && <ErrorBanner message={error} />}
        {status && (
          <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300">
            {status}
          </div>
        )}

        {isSignedIn ? (
          <div className="card space-y-3">
            <p className="text-sm">
              <span className="badge bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300">
                Signed in
              </span>
            </p>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              You can now run a live audit against the workspaces you have access to.
            </p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary" onClick={() => navigate("/run")}>
                Run a live audit
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={runDiagnostics}
                disabled={busy}
              >
                Diagnose access
              </button>
              <button type="button" className="btn-secondary" onClick={signOut}>
                Sign out
              </button>
            </div>
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="card space-y-3">
              <h3 className="font-medium">Sign in with your email</h3>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                Opens the Microsoft sign-in in a browser on the machine running the API.
                With no client id configured, Microsoft&apos;s first-party client is used, so
                no app registration is required.
              </p>
              <div className="space-y-2">
                <input
                  type="email"
                  className="input"
                  placeholder="you@contoso.com"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  aria-label="Email"
                />
                <input
                  type="text"
                  className="input"
                  placeholder="Tenant ID (optional)"
                  value={tenantId}
                  onChange={(event) => setTenantId(event.target.value)}
                  aria-label="Tenant ID"
                />
                <input
                  type="text"
                  className="input"
                  placeholder="Client ID (optional)"
                  value={clientId}
                  onChange={(event) => setClientId(event.target.value)}
                  aria-label="Client ID"
                />
              </div>
              <button
                type="button"
                className="btn-primary"
                onClick={signInInteractive}
                disabled={busy}
              >
                {busy ? "Waiting for sign-in…" : "Sign in with Microsoft"}
              </button>
              {busy && <Spinner label="Complete the sign-in in the browser window…" />}
            </div>

            <div className="card space-y-3">
              <h3 className="font-medium">Use an existing Azure CLI session</h3>
              <p className="text-sm text-slate-600 dark:text-slate-400">
                If you have already run <code className="font-mono text-xs">az login</code> on
                the API host, reuse that session. No app registration needed.
              </p>
              <button
                type="button"
                className="btn-secondary"
                onClick={signInAzureCli}
                disabled={busy}
              >
                Sign in with Azure CLI
              </button>
              <p className="text-xs text-slate-500">
                Some tenants block both methods via Conditional Access. In that case supply a
                registered client id above.
              </p>
            </div>
          </div>
        )}
      </Section>

      {diagnostics && (
        <Section
          title="Access diagnostics"
          description="Per-resource HTTP status for the first few workspaces. Use this when a live audit returns less than expected."
        >
          {diagnostics.error && <ErrorBanner message={diagnostics.error} />}
          <div className="card scroll-x">
            <p className="mb-2 text-sm text-slate-600 dark:text-slate-400">
              Workspace listing returned HTTP {diagnostics.list_status} ·{" "}
              {diagnostics.count} workspace(s) visible.
            </p>
            <table className="table-base">
              <thead>
                <tr>
                  <th scope="col">Workspace</th>
                  <th scope="col">Items</th>
                  <th scope="col">Items HTTP</th>
                  <th scope="col">Pipelines</th>
                  <th scope="col">Roles HTTP</th>
                </tr>
              </thead>
              <tbody>
                {diagnostics.samples.map((sample) => (
                  <tr key={sample.name}>
                    <td className="font-medium">{sample.name}</td>
                    <td>{sample.items}</td>
                    <td>{sample.items_status ?? "—"}</td>
                    <td>{sample.pipelines}</td>
                    <td>{sample.roles_status ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}
