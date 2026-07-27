/**
 * Small presentational primitives shared across pages.
 *
 * Grouped in one module because each is a handful of lines; splitting them into
 * separate files would add navigation cost without adding clarity.
 */
import type { ReactNode } from "react";

import type { CheckStatus, Severity } from "@/types/api";
import { formatPercent, ratingFor, severityClass, statusClass } from "@/utils/format";

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500" role="status">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600"
        aria-hidden="true"
      />
      {label ?? "Loading…"}
    </div>
  );
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800
                 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
    >
      <div className="flex items-start justify-between gap-4">
        <span>{message}</span>
        {onRetry && (
          <button type="button" onClick={onRetry} className="shrink-0 underline">
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-md border border-dashed border-slate-300 p-8 text-center dark:border-slate-700">
      <p className="font-medium text-slate-700 dark:text-slate-300">{title}</p>
      {hint && <p className="mt-1 text-sm text-slate-500">{hint}</p>}
    </div>
  );
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`badge ${severityClass(severity)}`}>{severity}</span>;
}

export function StatusBadge({ status }: { status: CheckStatus }) {
  return <span className={`badge ${statusClass(status)}`}>{status}</span>;
}

/**
 * A score with its rating and a proportional bar.
 *
 * A null score renders as "—" with an empty bar: *not assessed* must never look
 * like a zero.
 */
export function ScoreBar({ pct }: { pct: number | null }) {
  const rating = ratingFor(pct);
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className={`text-lg font-semibold ${rating.textClass}`}>
          {formatPercent(pct)}
        </span>
        <span className="text-xs text-slate-500">{rating.label}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
        <div
          className={`h-full rounded-full ${rating.barClass}`}
          style={{ width: `${pct ?? 0}%` }}
        />
      </div>
    </div>
  );
}

export function Section({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">{title}</h2>
          {description && <p className="text-sm text-slate-500">{description}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}
