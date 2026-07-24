// Workspace selection: loading (mock/live), grouped rendering, add/remove,
// select-all, and the "N of M selected" counter.

import { state, ROLES } from "../core/state.js";
import { esc, setStatus } from "../core/utils.js";
import { getWorkspaces, getLiveWorkspaces } from "../core/api.js";

/** Load workspaces for the current mode and (re)render the list. */
export async function loadWorkspaces() {
  const mode = document.getElementById("mode").value;
  try {
    state.fetchedWs = await getWorkspaces(state.CONFIG.project, mode);
  } catch {
    state.fetchedWs = [];
  }
  [...state.fetchedWs, ...state.manualWs].forEach(w => {
    if (!(w.id in state.roleById)) state.roleById[w.id] = w.role || "Mixed";
  });
  state.selected = new Set(
    [...state.fetchedWs.map(w => w.id), ...state.manualWs.map(w => w.id)]
      .filter(id => !state.removed.has(id)));
  renderWorkspaces();
}

/** Fetched + manual workspaces, minus any the user removed (de-duplicated). */
export function combinedWs() {
  const map = new Map();
  [...state.fetchedWs, ...state.manualWs].forEach(w => {
    if (!state.removed.has(w.id)) map.set(w.id, w);
  });
  return [...map.values()];
}

/** Render the workspace list grouped by layer role, with per-row controls. */
export function renderWorkspaces() {
  const box = document.getElementById("workspaces");
  const all = combinedWs();
  document.getElementById("wsCount").textContent = `(${all.length})`;
  const roleOf = w => state.roleById[w.id] || w.role || "Mixed";
  const byRole = {};
  all.forEach(w => { const r = roleOf(w); (byRole[r] = byRole[r] || []).push(w); });
  const order = [...ROLES, ...Object.keys(byRole).filter(r => !ROLES.includes(r))];
  let html = "";
  order.forEach(role => {
    const items = byRole[role]; if (!items) return;
    html += `<div class="group-head">${esc(role)} <span class="count">(${items.length})</span></div>`;
    items.forEach(w => {
      const meta = (w.items !== null && w.items !== undefined) ? `${w.pipelines} pipelines · ${w.items} items`
        : (w.manual ? "added manually" : "");
      const opts = ROLES.map(r => `<option ${roleOf(w) === r ? "selected" : ""}>${r}</option>`).join("");
      html += `<div class="wsrow"><label class="checkitem" title="${esc(w.name)} — ${esc(w.id)}"><input type="checkbox" class="wsChk" value="${esc(w.id)}" ${state.selected.has(w.id) ? "checked" : ""}>
        <span>${esc(w.name)}<div class="meta">${meta}</div></span></label>
        <select class="wsRole" data-id="${esc(w.id)}" title="Layer role">${opts}</select>
        <button class="wsremove" data-id="${esc(w.id)}" title="Remove from list">×</button></div>`;
    });
  });
  box.innerHTML = html;
  updateSelCount();
  box.querySelectorAll(".wsChk").forEach(chk => chk.addEventListener("change", e => {
    if (e.target.checked) state.selected.add(e.target.value); else state.selected.delete(e.target.value);
    updateSelCount();
  }));
  box.querySelectorAll(".wsRole").forEach(sel => sel.addEventListener("change", e => {
    state.roleById[e.target.dataset.id] = e.target.value; renderWorkspaces();
  }));
  box.querySelectorAll(".wsremove").forEach(btn => btn.addEventListener("click", e => {
    const id = e.currentTarget.dataset.id;
    state.removed.add(id);
    state.manualWs = state.manualWs.filter(w => w.id !== id);
    state.selected.delete(id);
    renderWorkspaces();
  }));
}

/** Update the "N of M selected" label. */
export function updateSelCount() {
  const all = combinedWs();
  const n = all.filter(w => state.selected.has(w.id)).length;
  const el = document.getElementById("wsSelCount");
  if (el) el.textContent = all.length ? `${n} of ${all.length} selected` : "";
}

/** Select or deselect every workspace. */
export function selectAllWs(on) {
  const all = combinedWs();
  if (on) all.forEach(w => state.selected.add(w.id));
  else all.forEach(w => state.selected.delete(w.id));
  renderWorkspaces();
}

/** Add a workspace by typed name/ID under the chosen role. */
export function addWorkspace() {
  const idEl = document.getElementById("wsAddId");
  const id = idEl.value.trim();
  if (!id) return;
  const role = document.getElementById("wsAddRole").value;
  state.removed.delete(id);
  state.roleById[id] = role;
  if (!combinedWs().some(w => w.id === id)) {
    state.manualWs.push({ id, name: id, role, items: null, pipelines: null, manual: true });
  }
  state.selected.add(id);
  idEl.value = "";
  renderWorkspaces();
}

/** Enumerate the signed-in user's Fabric workspaces (a fresh reload). */
export async function loadLiveWorkspaces() {
  if (!state.authSession) { setStatus("Sign in first, then load your workspaces."); return; }
  setStatus("Loading your Fabric workspaces\u2026");
  try {
    const ws = await getLiveWorkspaces(state.authSession);
    if (ws.error) { setStatus("Error: " + ws.error); return; }
    state.fetchedWs = ws;
    state.removed.clear();   // a fresh reload brings back any workspaces removed earlier
    ws.forEach(w => { if (!(w.id in state.roleById)) state.roleById[w.id] = "Mixed"; });
    state.selected = new Set(ws.map(w => w.id));
    renderWorkspaces();
    setStatus(`Loaded ${ws.length} workspace(s) from Fabric. Tag each with a layer role, then Run.`);
  } catch (e) { setStatus("Error: " + e.message); }
}
