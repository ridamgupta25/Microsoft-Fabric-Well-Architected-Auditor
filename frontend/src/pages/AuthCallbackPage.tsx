/**
 * OAuth redirect landing page.
 *
 * Microsoft redirects here (the app's registered redirect URI) after the user
 * signs in, with `?code=…&state=…` in the query. This page hands those params to
 * the server, which exchanges the code for a token and returns a session id —
 * the token itself never touches the browser. On success it goes straight to
 * workspace selection; on failure it shows the error with a link back.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Spinner } from "@/components/ui";
import { useAuditContext } from "@/context/AuditContext";
import { completeAuthCodeLogin, getMe } from "@/services/authService";

export function AuthCallbackPage() {
  const navigate = useNavigate();
  const { setSession, setUser } = useAuditContext();
  const [error, setError] = useState<string | null>(null);
  const ran = useRef(false); // the auth code is single-use — exchange it once

  const complete = useCallback(async () => {
    const params = Object.fromEntries(new URLSearchParams(window.location.search));

    if (params.error) {
      setError(params.error_description || params.error);
      return;
    }
    if (!params.code || !params.state) {
      setError("This sign-in link is missing its code — please start again.");
      return;
    }

    try {
      const result = await completeAuthCodeLogin(params);
      setSession(result.session);
      getMe(result.session).then(setUser).catch(() => setUser(null));
      // Clear the code/state from the address bar, then continue.
      window.history.replaceState({}, "", "/run");
      navigate("/run", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [navigate, setSession, setUser]);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    void complete();
  }, [complete]);

  return (
    <div className="mx-auto mt-24 max-w-md text-center">
      {error ? (
        <div className="card space-y-3">
          <h1 className="text-lg font-semibold text-red-600 dark:text-red-400">
            Sign-in didn&apos;t complete
          </h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">{error}</p>
          <Link to="/sign-in" className="btn-primary inline-block">
            Try again
          </Link>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3">
          <Spinner label="Finishing sign-in…" />
          <p className="text-sm text-slate-500">Connecting you to Microsoft Fabric.</p>
        </div>
      )}
    </div>
  );
}
