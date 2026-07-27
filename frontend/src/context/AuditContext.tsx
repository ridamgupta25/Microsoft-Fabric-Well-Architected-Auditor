/**
 * Application state shared across pages.
 *
 * Deliberately small: the sign-in session, the mode, and the most recent audit.
 * Everything else is fetched per-page. Holding report data here would keep large
 * payloads alive long after the page that needed them unmounted.
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { AuditMode, AuditReport } from "@/types/api";

interface AuditContextValue {
  mode: AuditMode;
  setMode: (mode: AuditMode) => void;

  /** Opaque sign-in session. Never a Fabric token — that stays server-side. */
  session: string | null;
  setSession: (session: string | null) => void;
  isSignedIn: boolean;

  /** Id of the most recently submitted audit, so pages can link to it. */
  lastAuditId: string | null;
  setLastAuditId: (id: string | null) => void;

  /** Cached report for the current audit, to avoid refetching on navigation. */
  report: AuditReport | null;
  setReport: (report: AuditReport | null) => void;

  reset: () => void;
}

const AuditContext = createContext<AuditContextValue | undefined>(undefined);

export function AuditProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<AuditMode>("mock");
  const [session, setSession] = useState<string | null>(null);
  const [lastAuditId, setLastAuditId] = useState<string | null>(null);
  const [report, setReport] = useState<AuditReport | null>(null);

  const reset = useCallback(() => {
    setLastAuditId(null);
    setReport(null);
  }, []);

  const value = useMemo<AuditContextValue>(
    () => ({
      mode,
      setMode,
      session,
      setSession,
      isSignedIn: session !== null,
      lastAuditId,
      setLastAuditId,
      report,
      setReport,
      reset,
    }),
    [mode, session, lastAuditId, report, reset],
  );

  return <AuditContext.Provider value={value}>{children}</AuditContext.Provider>;
}

export function useAuditContext(): AuditContextValue {
  const context = useContext(AuditContext);
  if (!context) {
    throw new Error("useAuditContext must be used inside an <AuditProvider>.");
  }
  return context;
}
