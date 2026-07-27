/**
 * Presentation helpers.
 *
 * The rating bands mirror the backend's scoring rubric. They are duplicated here
 * deliberately: the API returns raw percentages, and the alternative — round
 * tripping every label through the server — would make the UI slower for no
 * gain. If the bands change, both sides must change.
 */
import type { CheckStatus, Severity } from "@/types/api";

export type RatingKey = "excellent" | "good" | "medium" | "high" | "critical" | "unknown";

export interface Rating {
  key: RatingKey;
  /** Read as *risk*: "Critical" means critical risk, i.e. a low score. */
  label: string;
  textClass: string;
  bgClass: string;
  barClass: string;
}

const RATINGS: Record<RatingKey, Rating> = {
  excellent: {
    key: "excellent", label: "Excellent",
    textClass: "text-blue-600 dark:text-blue-400",
    bgClass: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
    barClass: "bg-blue-600",
  },
  good: {
    key: "good", label: "Good",
    textClass: "text-green-600 dark:text-green-400",
    bgClass: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
    barClass: "bg-green-600",
  },
  medium: {
    key: "medium", label: "Medium",
    textClass: "text-yellow-600 dark:text-yellow-400",
    bgClass: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-300",
    barClass: "bg-yellow-500",
  },
  high: {
    key: "high", label: "High",
    textClass: "text-orange-600 dark:text-orange-400",
    bgClass: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
    barClass: "bg-orange-500",
  },
  critical: {
    key: "critical", label: "Critical",
    textClass: "text-red-600 dark:text-red-400",
    bgClass: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
    barClass: "bg-red-600",
  },
  unknown: {
    key: "unknown", label: "Not assessed",
    textClass: "text-slate-500 dark:text-slate-400",
    bgClass: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
    barClass: "bg-slate-400",
  },
};

/** `null` means *not assessed* — never render it as zero. */
export function ratingFor(pct: number | null | undefined): Rating {
  if (pct === null || pct === undefined) return RATINGS.unknown;
  if (pct >= 91) return RATINGS.excellent;
  if (pct >= 76) return RATINGS.good;
  if (pct >= 61) return RATINGS.medium;
  if (pct >= 41) return RATINGS.high;
  return RATINGS.critical;
}

export function formatPercent(pct: number | null | undefined, digits = 1): string {
  if (pct === null || pct === undefined) return "—";
  return `${pct.toFixed(digits)}%`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

const SEVERITY_CLASSES: Record<Severity, string> = {
  Critical: "bg-red-600 text-white",
  High: "bg-orange-500 text-white",
  Medium: "bg-yellow-500 text-slate-900",
  Low: "bg-slate-400 text-white",
  Informational: "bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200",
};

export function severityClass(severity: Severity): string {
  return SEVERITY_CLASSES[severity] ?? SEVERITY_CLASSES.Informational;
}

const STATUS_CLASSES: Record<CheckStatus, string> = {
  PASS: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  PARTIAL: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-300",
  FAIL: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  "N/A": "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
  INFO: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
};

export function statusClass(status: CheckStatus): string {
  return STATUS_CLASSES[status] ?? STATUS_CLASSES.INFO;
}

/** Severity order for sorting findings worst-first. Mirrors SEVERITY_RANK. */
export const SEVERITY_RANK: Record<Severity, number> = {
  Critical: 0,
  High: 1,
  Medium: 2,
  Low: 3,
  Informational: 4,
};
