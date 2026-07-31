/**
 * Connect to Microsoft Fabric — read-only sign-in.
 *
 * The primary path is the **device code** flow, which works whether the app runs
 * on your machine or is hosted remotely (a tunnel/server): the user opens a
 * Microsoft page in *their own* browser and enters a short code. Two local-only
 * fallbacks remain for when the browser is on the same machine as the API —
 * opening the sign-in window on the host, or reusing `az login`.
 *
 * Sign-in is asynchronous: a flow starts and this page polls until it resolves,
 * then moves straight to workspace selection.
 *
 * The Fabric access token never reaches the browser; this page only ever holds
 * an opaque session id.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ErrorBanner, Spinner } from "@/components/ui";
import { useAuditContext } from "@/context/AuditContext";
import { getDiagnostics } from "@/services/auditService";
import {
  getLoginConfig,
  getMe,
  loginWithAzureCli,
  logout,
  startAuthCodeLogin,
  startDeviceCodeLogin,
  startInteractiveLogin,
  waitForSignIn,
} from "@/services/authService";
import type { Diagnostics } from "@/types/api";

export function SignInPage() {
  const navigate = useNavigate();
  const { session, setSession, isSignedIn, setUser } = useAuditContext();

  const [email, setEmail] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showLocal, setShowLocal] = useState(false);
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deviceCode, setDeviceCode] = useState<string | null>(null);
  const [verificationUri, setVerificationUri] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [diagnosing, setDiagnosing] = useState(false);
  const [redirectEnabled, setRedirectEnabled] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Stop polling if the user navigates away mid sign-in.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Offer redirect ("Sign in with Microsoft") only when the server has an Entra
  // app configured for it; otherwise the device-code flow is the primary path.
  useEffect(() => {
    getLoginConfig()
      .then((cfg) => setRedirectEnabled(cfg.redirect_enabled))
      .catch(() => setRedirectEnabled(false));
  }, []);

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

  // Standard hosted-web sign-in: redirect the browser to Microsoft, which returns
  // to /auth/callback with a code the server exchanges. Works for remote users.
  const signInRedirect = useCallback(async () => {
    setError(null);
    setBusy(true);
    setStep("Redirecting you to Microsoft…");
    try {
      const { auth_url } = await startAuthCodeLogin(`${window.location.origin}/auth/callback`);
      window.location.href = auth_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStep(null);
      setBusy(false);
    }
  }, []);

  // Primary flow: works whether the app is local or hosted remotely, because the
  // user signs in in *their own* browser at the Microsoft device-login page.
  const signInDeviceCode = useCallback(async () => {
    setError(null);
    setBusy(true);
    setDeviceCode(null);
    setVerificationUri(null);
    setCopied(false);
    setStep("Starting sign-in…");
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const started = await startDeviceCodeLogin({
        tenantId: tenantId || undefined,
        clientId: clientId || undefined,
      });
      setDeviceCode(started.user_code ?? null);
      setVerificationUri(started.verification_uri ?? null);
      setStep("Waiting for you to finish signing in with Microsoft…");
      const confirmed = await waitForSignIn(started.session, controller.signal);
      setStep("Connected.");
      finish(confirmed);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : String(err));
      setStep(null);
    } finally {
      setBusy(false);
      setDeviceCode(null);
      setVerificationUri(null);
    }
  }, [tenantId, clientId, finish]);

  const copyCode = useCallback(async () => {
    if (!deviceCode) return;
    try {
      await navigator.clipboard.writeText(deviceCode);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — the code is shown for manual copy */
    }
  }, [deviceCode]);

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

      {/* Primary sign-in. Redirect ("Sign in with Microsoft") when the server has
          an Entra app configured; otherwise the device-code flow. Both sign the
          user in in THEIR OWN browser and keep the token server-side. */}
      <div className="card space-y-3">
        {deviceCode ? (
          <div className="space-y-3 text-center">
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Open the Microsoft sign-in page and enter this code:
            </p>
            <div className="flex items-center justify-center gap-2">
              <code className="rounded-md bg-slate-100 px-3 py-2 text-lg font-semibold tracking-[0.3em] dark:bg-slate-800">
                {deviceCode}
              </code>
              <button type="button" className="btn-secondary" onClick={copyCode}>
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            {verificationUri && (
              <a
                href={verificationUri}
                target="_blank"
                rel="noopener noreferrer"
                className="btn-primary inline-block"
              >
                Open Microsoft sign-in ↗
              </a>
            )}
            <p className="text-xs text-slate-500">
              After you finish in that tab, this page connects automatically.
            </p>
          </div>
        ) : redirectEnabled ? (
          <>
            <button
              type="button"
              className="btn-primary w-full"
              onClick={signInRedirect}
              disabled={busy}
            >
              {busy ? "Redirecting…" : "Sign in with Microsoft"}
            </button>
            <p className="text-center text-xs text-slate-500">
              Redirects to the Microsoft sign-in in <strong>your</strong> browser and back.
              The access token never reaches the browser.
            </p>
            <button
              type="button"
              className="mx-auto block text-xs text-slate-500 underline hover:text-slate-700 dark:hover:text-slate-300"
              onClick={signInDeviceCode}
              disabled={busy}
            >
              Use a one-time code instead
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn-primary w-full"
              onClick={signInDeviceCode}
              disabled={busy}
            >
              {busy ? "Starting…" : "Connect with Microsoft"}
            </button>
            <p className="text-center text-xs text-slate-500">
              Opens the Microsoft sign-in in <strong>your</strong> browser. No app
              registration needed, and the access token never reaches the browser.
            </p>
          </>
        )}
      </div>

      {/* Host-only fallbacks: only work when the browser and the API are on the
          same machine (i.e. running locally, not through a tunnel/server). */}
      <details
        className="card"
        open={showLocal}
        onToggle={(event) => setShowLocal(event.currentTarget.open)}
      >
        <summary className="cursor-pointer text-sm font-medium text-slate-600 dark:text-slate-400">
          Running the app on this machine?
        </summary>
        <div className="mt-3 space-y-3">
          <p className="text-xs text-slate-500">
            These only work when your browser and the API run on the same computer.
          </p>
          <input
            type="email"
            className="input"
            placeholder="you@contoso.com (optional)"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-label="Email"
          />
          <button
            type="button"
            className="btn-secondary w-full"
            onClick={signInInteractive}
            disabled={busy}
          >
            Open the sign-in window on the API host
          </button>
          <button
            type="button"
            className="btn-secondary w-full"
            onClick={signInAzureCli}
            disabled={busy}
          >
            Reuse my Azure CLI session
          </button>
        </div>
      </details>

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
