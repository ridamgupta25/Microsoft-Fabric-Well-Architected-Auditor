// Renders the audit result into the main panel: error banner, overall score,
// pillar scorecard, per-workspace breakdown, findings, and the full check list.

import { state } from "../core/state.js";
import { esc, fmt, ratingOf, sevColor } from "../core/utils.js";

export function render(d) {
  const r = ratingOf(d.overall);
  let html = "";

  if (d.errors && d.errors.length) {
    html += `<div class="section"><div class="errbanner">
      <strong>⚠ ${d.errors.length} workspace(s) could not be audited</strong>
      <ul>` +
      d.errors.map(e => `<li><b>${esc(e.workspace)}</b>${e.role ? ` <span class="tag">${esc(e.role)}</span>` : ""}<br>
        <span class="why">${esc(e.message)}</span></li>`).join("") +
      `</ul><div class="why" style="margin-top:6px;">These were <b>skipped</b> and are not included in the scores below.</div>
      </div></div>`;
  }

  html += `<div class="section"><div class="overall">
    <div><div class="scorebig">${fmt(d.overall)}</div>
      <div class="counts">${d.counts.PASS || 0} pass · ${d.counts.PARTIAL || 0} partial · ${d.counts.FAIL || 0} fail · ${d.total_scored} scored</div></div>
    <span class="pill" style="background:${r.color}">${r.label}</span>
  </div></div>`;

  html += `<div class="section"><h2>Pillar Scorecard</h2><div class="cards">`;
  for (const p of state.CONFIG.pillars) {
    const pv = d.by_pillar[p]; if (!pv) continue;
    const rr = ratingOf(pv.pct);
    const width = pv.pct === null ? 0 : pv.pct;
    html += `<div class="card"><div class="name">${p}</div>
      <div class="val" style="color:${rr.color}">${fmt(pv.pct)}</div>
      <div class="bar"><span style="width:${width}%;background:${rr.color}"></span></div>
      <div class="counts">${rr.label} · ${pv.count} checks</div></div>`;
  }
  html += `</div></div>`;

  html += `<div class="section"><h2>Per-Workspace Breakdown</h2><table>
    <tr><th>Workspace</th><th>Layer role</th><th>Score</th><th>Rating</th></tr>`;
  for (const [ws, wv] of Object.entries(d.by_workspace)) {
    const rr = ratingOf(wv.pct);
    html += `<tr><td>${esc(ws)}</td><td><span class="tag">${esc(wv.role || "—")}</span></td>
      <td>${fmt(wv.pct)}</td><td><span class="sev" style="background:${rr.color}">${rr.label}</span></td></tr>`;
  }
  html += `</table></div>`;

  const findings = d.results.filter(x => x.status === "FAIL" || x.status === "PARTIAL")
    .sort((a, b) => (a.score ?? 9) - (b.score ?? 9));
  html += `<div class="section"><h2>Findings (${findings.length})</h2>`;
  if (!findings.length) { html += `<div class="muted">No failing or partial checks 🎉</div>`; }
  else {
    html += `<table><tr><th>Sev</th><th>Ref</th><th>Check</th><th>Pillar</th><th>Workspace</th><th>Object</th><th>Status</th><th>Evidence</th><th>Recommendation</th></tr>`;
    for (const f of findings) {
      html += `<tr>
        <td><span class="sev" style="background:${sevColor[f.severity] || 'var(--na)'}">${f.severity}</span></td>
        <td>${f.ref}</td><td>${esc(f.title)}</td><td>${esc(f.pillar)}</td>
        <td>${esc(f.workspace)}</td><td>${esc(f.obj) || "—"}</td><td>${f.status}</td>
        <td>${esc(f.evidence)}</td><td class="muted">${esc(f.recommendation)}</td></tr>`;
    }
    html += `</table>`;
  }
  html += `</div>`;

  html += `<div class="section"><details><summary>All checks that ran (${d.results.length}) — workspace checks are common to every project</summary>
    <table><tr><th>Common?</th><th>Ref</th><th>Check</th><th>Pillar</th><th>Workspace</th><th>Object</th><th>Status</th><th>Score</th><th>Evidence</th></tr>`;
  for (const c of d.results) {
    html += `<tr><td>${c.common ? "✓ common" : ""}</td><td>${c.ref}</td><td>${esc(c.title)}</td>
      <td>${esc(c.pillar)}</td><td>${esc(c.workspace)}</td><td>${esc(c.obj) || "—"}</td>
      <td>${c.status}</td><td>${c.score ?? "—"}</td><td class="muted">${esc(c.evidence)}</td></tr>`;
  }
  html += `</table></details></div>`;

  html += `<div class="section downloads">
    <a href="/api/download/md" target="_blank">⬇ Markdown report</a>
    <a href="/api/download/xlsx">⬇ Excel workbook</a></div>`;

  document.getElementById("results").innerHTML = html;
}
