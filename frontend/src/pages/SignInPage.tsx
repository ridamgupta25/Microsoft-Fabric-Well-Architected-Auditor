/**
 * Connect to Microsoft Fabric — read-only sign-in.
 *
 * One primary path (email sign-in), one fallback (reuse `az login`), and an
 * advanced section for the rare tenant that needs an explicit app registration.
 * Sign-in is asynchronous: a flow starts, a browser window opens on the server
 * host, and this page polls until it resolves — then moves straight to workspace
 * selection, since the whole point of connecting is to see what you can audit.
 *
 * The Fabric access token never reaches the browser; this page only ever holds
 * an opaque session id.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ErrorBanner, Spinner } from "@/components/ui";
import { useAuditContext } from "@/context/AuditContext";
import { getDiagnostics } from "@/services/auditService";
import { loginWithAzureCli, logout, startInteractiveLogin, waitForSignIn, getMe } from "@/services/authService";
import type { Diagnostics } from "@/types/api";

export function SignInPage() {
  const navigate = useNavigate();
  const { session, setSession, isSignedIn, setUser } = useAuditContext();

  const [email, setEmail] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [diagnosing, setDiagnosing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Stop polling if the user navigates away mid sign-in.
  useEffect(() => () => abortRef.current?.abort(), []);

  const finish = useCallback(
    (newSession: string) => {
      setSession(newSession);
      // Load the display profile so the header can greet the user by name.
      getMe(newSession).then(setUser).catch(() => setUser(null));
      // Connecting exists to see what you can audit — go straight there.
      navigate("/run");
    },
    [setSession, setUser, navigate],
  );

  const signInInteractive = useCallback(async () => {
    setError(null);
    setBusy(true);
    setStep("Opening the Microsoft sign-in in your browser…");
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const started = await startInteractiveLogin({
        email: email || undefined,
        tenantId: tenantId || undefined,
        clientId: clientId || undefined,
      });
      setStep("Waiting for you to complete sign-in…");
      const confirmed = await waitForSignIn(started.session, controller.signal);
      setStep("Connected.");
      finish(confirmed);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : String(err));
      setStep(null);
    } finally {
      setBusy(false);
    }
  }, [email, tenantId, clientId, finish]);

  const signInAzureCli = useCallback(async () => {
    setError(null);
    setBusy(true);
    setStep("Reusing your Azure CLI session…");
    try {
      const result = await loginWithAzureCli();
      finish(result.session);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStep(null);
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
      setUser(null);
      setDiagnostics(null);
      setStep(null);
    }
  }, [session, setSession, setUser]);

  const runDiagnostics = useCallback(async () => {
    if (!session) return;
    setError(null);
    setDiagnosing(true);
    try {
      setDiagnostics(await getDiagnostics(session));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDiagnosing(false);
    }
  }, [session]);

  if (isSignedIn) {
    return (
      <div className="mx-auto max-w-lg space-y-4">
        {error && <ErrorBanner message={error} />}
        <div className="card space-y-3 text-center">
          <span className="badge mx-auto bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300">
            Connected to Fabric
          </span>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            You can now run an audit against the workspaces you have access to.
          </p>
          <div className="flex flex-wrap justify-center gap-2 pt-1">
            <button type="button" className="btn-primary" onClick={() => navigate("/run")}>
              Choose workspaces
            </button>
            <button type="button" className="btn-secondary" onClick={signOut}>
              Disconnect
            </button>
          </div>
        </div>

        <details className="card">
          <summary className="cursor-pointer text-sm font-medium">
            Troubleshoot access
          </summary>
          <div className="mt-3 space-y-3">
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Reports per-resource HTTP status for the first few workspaces — useful when an
              audit returns less than expected.
            </p>
            <button
              type="button"
              className="btn-secondary"
              onClick={runDiagnostics}
              disabled={diagnosing}
            >
              {diagnosing ? "Checking…" : "Run diagnostics"}
            </button>

            {diagnostics && (
              <div className="scroll-x">
                {diagnostics.error && <ErrorBanner message={diagnostics.error} />}
                <p className="my-2 text-xs text-slate-500">
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
            )}
          </div>
        </details>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-md space-y-4">
      <div className="text-center">
        <h1 className="text-lg font-semibold">Connect to Microsoft Fabric</h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Read-only. The tool never writes to your tenant, and the access token never
          reaches the browser.
        </p>
      </div>

      {error && <ErrorBanner message={error} />}
      {step && !error && (
        <div className="flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300">
          {busy && <Spinner label="" />}
          {step}
        </div>
      )}

      <div className="card space-y-3">
        <input
          type="email"
          className="input"
          placeholder="you@contoso.com (optional)"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          aria-label="Email"
          autoFocus
        />
        <button
          type="button"
          className="btn-primary w-full"
          onClick={signInInteractive}
          disabled={busy}
        >
          {busy ? "Connecting…" : "Connect with Microsoft"}
        </button>
        <p className="text-center text-xs text-slate-500">
          Opens the Microsoft sign-in in a browser on the machine running the API. No app
          registration needed.
        </p>
      </div>

      <div className="flex items-center gap-3 text-xs text-slate-400">
        <span className="h-px flex-1 bg-slate-200 dark:bg-slate-800" />
        or
        <span className="h-px flex-1 bg-slate-200 dark:bg-slate-800" />
      </div>

      <button
        type="button"
        className="btn-secondary w-full"
        onClick={signInAzureCli}
        disabled={busy}
      >
        Reuse my Azure CLI session
      </button>

      <details
        className="card"
        open={showAdvanced}
        onToggle={(event) => setShowAdvanced(event.currentTarget.open)}
      >
        <summary className="cursor-pointer text-sm font-medium text-slate-600 dark:text-slate-400">
          Advanced — tenant blocked by Conditional Access?
        </summary>
        <div className="mt-3 space-y-2">
          <p className="text-xs text-slate-500">
            Only needed if the default sign-in is blocked. Supply your own registered
            Entra app.
          </p>
          <input
            type="text"
            className="input"
            placeholder="Tenant ID"
            value={tenantId}
            onChange={(event) => setTenantId(event.target.value)}
            aria-label="Tenant ID"
          />
          <input
            type="text"
            className="input"
            placeholder="Client ID"
            value={clientId}
            onChange={(event) => setClientId(event.target.value)}
            aria-label="Client ID"
          />
        </div>
      </details>
    </div>
  );
}
