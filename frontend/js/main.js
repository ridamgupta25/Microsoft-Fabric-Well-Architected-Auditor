// Application entry point: bootstraps config, defines the Run action, and wires
// every DOM event to the imported handlers. Loaded as an ES module from
// index.html, so it runs after the DOM is parsed.

import { state, PILLAR_HELP } from "./core/state.js";
import { setStatus } from "./core/utils.js";
import { getConfig, postRun } from "./core/api.js";
import { showLoading, hideLoading } from "./ui/loading.js";
import "./ui/game.js";  // optional mini-game plug-in — delete this line to remove the game
import {
  loadWorkspaces, combinedWs, selectAllWs, addWorkspace, loadLiveWorkspaces,
} from "./features/workspaces.js";
import { toggleAuth, signIn, signInAz, diagnose } from "./features/auth.js";
import { render } from "./features/results.js";

/** Fetch server config, build the pillar checkboxes, then load workspaces. */
async function loadConfig() {
  state.CONFIG = await getConfig();
  if (state.CONFIG.auth) {
    document.getElementById("tenantId").value = state.CONFIG.auth.tenant_id || "";
    document.getElementById("clientId").value = state.CONFIG.auth.client_id || "";
  }
  const pc = document.getElementById("pillars");
  pc.innerHTML = "";
  state.CONFIG.pillars.forEach(p => {
    const phase2 = p === "Performance Efficiency";
    pc.insertAdjacentHTML("beforeend",
      `<label class="checkitem"><input type="checkbox" class="pillarChk" value="${p}" ${phase2 ? "" : "checked"}>
       <span>${p}<div class="meta">${PILLAR_HELP[p] || ""}</div></span></label>`);
  });
  toggleAuth();
  await loadWorkspaces();
}

const selectedPillars = () => [...document.querySelectorAll(".pillarChk:checked")].map(c => c.value);

/** Validate selections, run the audit, and render the scorecard. */
async function runAudit() {
  const mode = document.getElementById("mode").value;
  const pillars = selectedPillars();
  const specs = combinedWs().filter(w => state.selected.has(w.id))
    .map(w => ({ id: w.id, role: state.roleById[w.id] || w.role || "Mixed", name: w.name }));
  if (!specs.length) { setStatus("Pick at least one workspace."); return; }
  if (!pillars.length) { setStatus("Pick at least one pillar."); return; }
  if (mode === "live" && !state.authSession) { setStatus("Sign in first (live mode)."); return; }
  const btn = document.getElementById("run");
  btn.disabled = true; setStatus("Running checks…");
  showLoading();
  try {
    const data = await postRun({
      project: state.CONFIG.project, mode, pillars, workspaces: specs, auth_session: state.authSession,
    });
    if (data.error) setStatus("Error: " + data.error);
    else { render(data); setStatus("Done."); }
  } catch (e) { setStatus("Error: " + e.message); }
  finally { hideLoading(); btn.disabled = false; }
}

// ---- Wire up the DOM ----
const _layout = () => document.querySelector(".layout");
document.getElementById("mode").addEventListener("change", () => { toggleAuth(); loadWorkspaces(); });
document.getElementById("signIn").addEventListener("click", signIn);
document.getElementById("signInAz").addEventListener("click", signInAz);
document.getElementById("wsAdd").addEventListener("click", addWorkspace);
document.getElementById("wsSelAll").addEventListener("click", () => selectAllWs(true));
document.getElementById("wsDeselAll").addEventListener("click", () => selectAllWs(false));
document.getElementById("loadWs").addEventListener("click", loadLiveWorkspaces);
document.getElementById("diagBtn").addEventListener("click", diagnose);
document.getElementById("wsAddId").addEventListener("keydown", e => { if (e.key === "Enter") addWorkspace(); });
document.getElementById("run").addEventListener("click", runAudit);
document.getElementById("navToggle").addEventListener("click", () => _layout().classList.toggle("collapsed"));
document.querySelectorAll(".rail .railbtn").forEach(b => b.addEventListener("click", () => {
  _layout().classList.remove("collapsed");
  if (b.id === "railRun") { runAudit(); return; }
  const t = document.getElementById(b.dataset.target);
  if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
}));

loadConfig();
