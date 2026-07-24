// Loading overlay controller.
//
// Shows the overlay (spinner + rotating tips + progress bar) while an audit
// runs. A "distraction" plug-in (e.g. the mini-game) may register itself to
// render inside the overlay's mount point. If no plug-in is registered, the
// overlay still works perfectly — so the game can be removed with no impact.

let distraction = null;   // { start(mountEl), stop() } | null

/** Register a distraction plug-in (called by game.js). */
export function registerDistraction(plugin) {
  distraction = plugin;
}

const LOAD_TIPS = [
  "Reading workspace inventory…",
  "Checking role assignments & guest access…",
  "Scanning pipelines for retry & failure paths…",
  "Sniffing out hardcoded secrets…",
  "Verifying each layer is properly separated…",
  "Scoring against the Well-Architected rubric…",
  "Assembling your pillar scorecard…",
];
let loadTipTimer = null, loadShownAt = 0;

/** Show the overlay, rotate tips, and start the distraction if one exists. */
export function showLoading() {
  document.getElementById("loadingOverlay").classList.add("show");
  loadShownAt = performance.now();
  let i = 0;
  document.getElementById("loadTip").textContent = LOAD_TIPS[0];
  loadTipTimer = setInterval(() => {
    i = (i + 1) % LOAD_TIPS.length;
    document.getElementById("loadTip").textContent = LOAD_TIPS[i];
  }, 1700);
  if (distraction) {
    try { distraction.start(document.getElementById("loadingExtra")); } catch { /* ignore */ }
  }
}

/** Hide the overlay (after a brief minimum so it never just flickers). */
export function hideLoading() {
  const finish = () => {
    document.getElementById("loadingOverlay").classList.remove("show");
    if (loadTipTimer) { clearInterval(loadTipTimer); loadTipTimer = null; }
    if (distraction) { try { distraction.stop(); } catch { /* ignore */ } }
  };
  const remaining = 900 - (performance.now() - loadShownAt);
  if (remaining > 0) setTimeout(finish, remaining); else finish();
}
