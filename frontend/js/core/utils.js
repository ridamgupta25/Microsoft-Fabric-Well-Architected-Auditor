// Small, pure formatting and DOM helpers shared across modules.

/** Map a percentage to a {label, color} rating (mirrors the backend bands). */
export function ratingOf(pct) {
  if (pct === null || pct === undefined) return { label: "Not assessed", color: "var(--na)" };
  if (pct >= 91) return { label: "Excellent", color: "var(--exc)" };
  if (pct >= 76) return { label: "Good", color: "var(--good)" };
  if (pct >= 61) return { label: "Medium", color: "var(--med)" };
  if (pct >= 41) return { label: "High", color: "var(--high)" };
  return { label: "Critical", color: "var(--crit)" };
}

/** Severity -> CSS colour variable. */
export const sevColor = {
  Critical: "var(--crit)", High: "var(--high)", Medium: "var(--med)",
  Low: "var(--na)", Informational: "var(--na)",
};

/** Format a percentage for display (em dash when not assessed). */
export const fmt = p => (p === null || p === undefined) ? "—" : p.toFixed(1) + "%";

/** Escape user/text values before inserting into innerHTML. */
export const esc = s => (s ?? "").toString().replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

/** Write the small status line under the Run button. */
export const setStatus = t => { document.getElementById("status").textContent = t; };
