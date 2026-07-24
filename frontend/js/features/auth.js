// Read-only sign-in: show/hide the auth panel, interactive Microsoft sign-in,
// Azure CLI sign-in, and the connectivity diagnostic.

import { state } from "../core/state.js";
import { esc, setStatus } from "../core/utils.js";
import { authLogin, authPoll, authAzcli, postDiag } from "../core/api.js";
import { loadLiveWorkspaces } from "./workspaces.js";

/** Show the sign-in box only in live mode. */
export function toggleAuth() {
  const live = document.getElementById("mode").value === "live";
  document.getElementById("authPanel").classList.toggle("show", live);
}

/** Email-first interactive sign-in, then poll until the token is acquired. */
export async function signIn() {
  const email = document.getElementById("email").value.trim();
  const tenant_id = document.getElementById("tenantId").value.trim();
  const client_id = document.getElementById("clientId").value.trim();
  const msg = document.getElementById("authMsg");
  if (!email) { msg.innerHTML = `<span class="err">Enter your email.</span>`; return; }
  msg.textContent = "Opening Microsoft sign-in…";
  try {
    const r = await authLogin({ email, tenant_id, client_id });
    if (r.error) { msg.innerHTML = `<span class="err">${esc(r.error)}</span>`; return; }
    state.authSession = r.session;
    msg.textContent = r.message || "Complete the Microsoft sign-in in the browser window.";
    if (state.authTimer) clearInterval(state.authTimer);
    state.authTimer = setInterval(async () => {
      const p = await authPoll(state.authSession);
      if (p.status === "done") {
        clearInterval(state.authTimer);
        msg.innerHTML = `<span class="ok">✓ Signed in — read-only token acquired.</span>`;
        loadLiveWorkspaces();
      } else if (p.status === "error") {
        clearInterval(state.authTimer);
        msg.innerHTML = `<span class="err">${esc(p.error)}</span>`;
        state.authSession = null;
      }
    }, 3000);
  } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}

/** Reuse an existing `az login` session. */
export async function signInAz() {
  const msg = document.getElementById("authMsg");
  msg.textContent = "Getting token from Azure CLI\u2026";
  try {
    const r = await authAzcli();
    if (r.error) { msg.innerHTML = `<span class="err">${esc(r.error)}</span>`; return; }
    state.authSession = r.session;
    msg.innerHTML = `<span class="ok">\u2713 ${esc(r.message || "Signed in via Azure CLI.")}</span>`;
    loadLiveWorkspaces();
  } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}

/** Probe Fabric connectivity and print what the token can actually read. */
export async function diagnose() {
  if (!state.authSession) { setStatus("Sign in first."); return; }
  const msg = document.getElementById("authMsg");
  msg.textContent = "Probing Fabric\u2026";
  try {
    const d = await postDiag(state.authSession);
    if (d.list_status == null && d.error) { msg.innerHTML = `<span class="err">${esc(d.error)}</span>`; return; }
    let t = `GET /workspaces \u2192 HTTP ${d.list_status}, found ${d.count} workspace(s).`;
    (d.samples || []).forEach(s => {
      t += `\n\u2022 ${s.name}: items HTTP ${s.items_status} (${s.items} items, ${s.pipelines} pipelines), roles HTTP ${s.roles_status}`;
    });
    if (d.error) t += `\nNote: ${d.error}`;
    msg.textContent = t;
  } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}
