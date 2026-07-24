// OPTIONAL "Catch the Data" mini-game — a loading-overlay distraction plug-in.
//
// It builds its OWN DOM into the overlay mount and registers itself with the
// loader. To remove the game entirely:
//   1. delete this file, and
//   2. delete the single `import "./game.js";` line in main.js.
// The loading overlay keeps working (spinner + tips + progress) with no game.

import { registerDistraction } from "./loading.js";

const Game = (() => {
  let canvas, ctx, raf = 0, running = false, last = 0, mount = null;
  let paddleX = 0.5, items = [], score = 0, best = 0, elapsed = 0, spawnAcc = 0;
  const GOOD = ["✅", "🔒", "⚙️", "📊", "🧮", "🗂️"];

  /** Build the game's markup inside the given overlay mount element. */
  function buildDom(mountEl) {
    mount = mountEl;
    mount.innerHTML = `
      <div class="gamewrap">
        <div class="gamebar">
          <span>🎮 Sit back &amp; play — <b>Catch the Data</b></span>
          <span>Score <b id="gScore">0</b> · Best <b id="gBest">0</b></span>
        </div>
        <canvas id="gameCanvas" width="460" height="240"></canvas>
        <div class="gamehint">Move your mouse / finger to catch ✅🔒⚙️📊 · dodge 💣 hardcoded secrets</div>
      </div>`;
  }

  function setScore(v) {
    score = Math.max(0, v);
    document.getElementById("gScore").textContent = score;
    if (score > best) best = score;
    document.getElementById("gBest").textContent = best;
  }
  function move(clientX) {
    const rect = canvas.getBoundingClientRect();
    paddleX = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  }
  const onMouse = e => move(e.clientX);
  const onTouch = e => { if (e.touches[0]) { move(e.touches[0].clientX); e.preventDefault(); } };
  function spawn() {
    items.push({
      x: 0.06 + Math.random() * 0.88, y: -0.05,
      vy: 0.28 + Math.random() * 0.18 + elapsed / 60,   // units/sec, ramps up
      good: Math.random() >= 0.18,
    });
    items[items.length - 1].ch = items[items.length - 1].good
      ? GOOD[(Math.random() * GOOD.length) | 0] : "💣";
  }
  function frame(now) {
    if (!running) return;
    const dt = Math.min(0.05, (now - last) / 1000 || 0); last = now;
    elapsed += dt;
    const W = canvas.width, H = canvas.height;
    ctx.fillStyle = "#0b1631"; ctx.fillRect(0, 0, W, H);
    spawnAcc += dt;
    const interval = Math.max(0.35, 0.85 - elapsed / 40);
    if (spawnAcc >= interval) { spawnAcc = 0; spawn(); }
    const pw = 68, ph = 15, px = paddleX * (W - pw), py = H - 26;
    ctx.font = "22px system-ui, 'Segoe UI Emoji', 'Apple Color Emoji'";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const it of items) {
      it.y += it.vy * dt;
      const ix = it.x * W, iy = it.y * H;
      if (!it.hit && iy >= py - 14 && iy <= py + ph + 8 && ix >= px - 8 && ix <= px + pw + 8) {
        it.hit = true; setScore(score + (it.good ? 1 : -1));
      }
      if (!it.hit) ctx.fillText(it.ch, ix, iy);
    }
    items = items.filter(it => !it.hit && it.y < 1.12);
    // paddle (a little OneLake bucket)
    ctx.fillStyle = "#2f6fed";
    ctx.beginPath();
    const r = 8;
    ctx.moveTo(px + r, py); ctx.arcTo(px + pw, py, px + pw, py + ph, r);
    ctx.arcTo(px + pw, py + ph, px, py + ph, r); ctx.arcTo(px, py + ph, px, py, r);
    ctx.arcTo(px, py, px + pw, py, r); ctx.closePath(); ctx.fill();
    ctx.font = "17px system-ui, 'Segoe UI Emoji', 'Apple Color Emoji'";
    ctx.fillText("🪣", px + pw / 2, py + ph / 2);
    raf = requestAnimationFrame(frame);
  }

  /** Distraction API: start rendering into the overlay mount. */
  function start(mountEl) {
    buildDom(mountEl);
    canvas = document.getElementById("gameCanvas");
    ctx = canvas.getContext("2d");
    items = []; elapsed = 0; spawnAcc = 0; last = performance.now(); setScore(0);
    running = true;
    canvas.addEventListener("mousemove", onMouse);
    canvas.addEventListener("touchmove", onTouch, { passive: false });
    raf = requestAnimationFrame(frame);
  }

  /** Distraction API: stop and clean up. */
  function stop() {
    running = false; cancelAnimationFrame(raf);
    if (canvas) { canvas.removeEventListener("mousemove", onMouse); canvas.removeEventListener("touchmove", onTouch); }
    if (mount) mount.innerHTML = "";
  }

  return { start, stop };
})();

registerDistraction(Game);
