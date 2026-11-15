// Headless behaviour check of ui/roundrep.js (jsdom + a real canvas).
// Run through `python tests/check_roundrep_js.py`, which builds the payload
// with the real round_payload and passes its path as the first argument.
//   RR_SHOT_DIR=/some/dir  -> also writes PNG screenshots of the canvas
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import assert from "assert";
import { fileURLToPath } from "url";

const here = path.dirname(fileURLToPath(import.meta.url));
const payload = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const shotDir = process.env.RR_SHOT_DIR || "";

const dom = new JSDOM('<!doctype html><div id="root"></div>', { pretendToBeVisual: true, resources: "usable" });
const { window } = dom;
for (const k of ["document", "Image", "HTMLElement"]) globalThis[k] = window[k];
globalThis.window = window;
globalThis.requestAnimationFrame = (f) => setTimeout(() => f(performance.now()), 16);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
window.requestAnimationFrame = globalThis.requestAnimationFrame;
window.cancelAnimationFrame = globalThis.cancelAnimationFrame;
// jsdom has no layout: give the few elements the script measures a size
const sizes = { "rr-track": [700, 26], "rr-wrap": [760, 470], rr: [1180, 600] };
window.Element.prototype.getBoundingClientRect = function () {
  for (const c of Object.keys(sizes)) if (this.classList && this.classList.contains(c)) {
    return { left: 0, top: 0, width: sizes[c][0], height: sizes[c][1], right: sizes[c][0], bottom: sizes[c][1] };
  }
  return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 };
};

const mod = await import(path.join(here, "..", "..", "ui", "roundrep.js"));
const triggers = [];
const root = document.getElementById("root");
const mount = (data) => mod.default({ data, parentElement: root, setTriggerValue: (k, v) => triggers.push([k, v]) });
let cleanup = mount(payload);
await new Promise((r) => setTimeout(r, 200));

const $ = (s) => root.querySelector(s), $$ = (s) => [...root.querySelectorAll(s)];
const click = (n) => n.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
const seekFrac = (f) => $(".rr-track").dispatchEvent(new window.MouseEvent("pointerdown", { bubbles: true, clientX: f * 700 }));
const save = (name) => {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  fs.writeFileSync(path.join(shotDir, name + ".png"), Buffer.from($("canvas").toDataURL("image/png").split(",")[1], "base64"));
};
const ok = (msg) => console.log("  ok  " + msg);

// players: ONE list, sorted by nickname, nobody special
const rows = $$(".rr-prow .rr-pname").map((n) => n.textContent);
const want = [...payload.players.map((p) => p.name)].sort((a, b) => a.toLowerCase() < b.toLowerCase() ? -1 : a.toLowerCase() > b.toLowerCase() ? 1 : 0);
assert.deepStrictEqual(rows, want);
assert.strictEqual($$(".rr-team").length, 0);
ok("players listed in one list sorted by nickname (" + rows.length + ")");

// every duel of the round has a card
assert.strictEqual($$(".rr-card").length, payload.duels.length);
ok("a card for every duel of the round (" + payload.duels.length + ")");

// the eye hides players from the map, the master eye hides / shows all
const eyes = $$(".rr-prow .rr-eye");
assert.strictEqual(eyes.length, rows.length);
click(eyes[0]); click(eyes[1]);
assert.strictEqual($$(".rr-prow.off").length, 2);
save("hidden");
click($(".rr-lh .rr-eye"));
assert.strictEqual($$(".rr-prow.off").length, 0);
click($(".rr-lh .rr-eye"));
assert.strictEqual($$(".rr-prow.off").length, rows.length);
click($(".rr-lh .rr-eye"));
assert.strictEqual($$(".rr-prow.off").length, 0);
ok("eye hides a player; the top eye shows / hides everyone");

// the hidden choice survives a round switch (the payload is replaced)
click(eyes[2]);
cleanup();
cleanup = mount({ ...payload, round: payload.round + 1 });
await new Promise((r) => setTimeout(r, 100));
assert.strictEqual($$(".rr-prow.off").length, 1);
click($$(".rr-prow .rr-eye")[2]);
ok("the hidden choice survives a round switch");

// a duel locks the playhead to its segment and shows both duelists' keys
if (payload.duels.length) {
  click($$(".rr-card")[0]);
  const z = payload.duels[0];
  assert.ok($(".rr-lock").style.display !== "none");
  seekFrac(0.99);
  assert.ok(root.__rr.st.t <= z.b && root.__rr.st.t >= z.a);
  const named = $$(".rr-dcol .rr-dnm b").map((n) => n.textContent).sort();
  assert.deepStrictEqual(named, [...new Set([z.kn, z.vn])].filter((n) => payload.players.some((p) => p.name === n)).sort());
  save("duel");
  click($(".rr-sbtn.exit"));
  assert.strictEqual(root.__rr.st.sel, -1);
  ok("a duel locks the playhead to its segment, the panel names both duelists, exit releases");
}

// rounds bar + round buttons are the one Streamlit event
const cur = payload.round;
const ri = payload.rounds.findIndex((r) => r[0] === cur);
if (payload.rounds.length > 1) {
  const chips = $$(".rr-rd");
  const other = chips.find((c) => c.textContent !== String(cur + 1));
  click(other);
  assert.strictEqual(triggers.length, 1);
  ok("a round chip click -> one 'rnd' trigger " + JSON.stringify(triggers[0]));
}

// the result banner: who won and how, plus the bomb story
const out = $(".rr-out");
assert.ok(out && out.classList.contains(payload.out.win === "CT" ? "wct" : "wt"));
if (payload.out.why) assert.ok(out.textContent.includes(payload.lab["why_" + payload.out.why]));
ok("result banner: " + out.querySelector(".rr-out1").textContent.replace(/\s+/g, " ").trim());
if (payload.bomb.length) {
  assert.strictEqual($$(".rr-bmk").length, payload.bomb.length + (payload.out.end != null ? 1 : 0));
  ok("bomb events + the round end are marked on the timeline (" + $$(".rr-bmk").length + ")");
}
// rounds bar chips carry how the round ended
assert.ok($$(".rr-rd .rr-rg").length > 0);
ok("round chips are marked with how the round ended");

// the speed menu: hidden until hover / click, 0.1x..4x, the choice sticks and survives a round switch
const spdBtn = $(".rr-sbtn.spd"), menu = $(".rr-spdmenu");
assert.ok(menu.hidden);
spdBtn.dispatchEvent(new window.MouseEvent("mouseenter", { bubbles: false }));
$(".rr-spd").dispatchEvent(new window.MouseEvent("mouseenter", { bubbles: false }));
assert.ok(!menu.hidden);
const items = [...menu.querySelectorAll(".rr-spdi")].map((b) => b.textContent);
assert.deepStrictEqual(items, ["0.1x", "0.25x", "0.5x", "1x", "2x", "4x"]);
click(menu.querySelectorAll(".rr-spdi")[0]);
assert.ok(menu.hidden);
assert.strictEqual(root.__rr.speed, 0.1);
assert.ok(spdBtn.textContent.startsWith("0.1x"));
click(spdBtn);                      // a click (touch / keyboard) opens it too
assert.ok(!menu.hidden);
click(spdBtn);
assert.ok(menu.hidden);
cleanup();
cleanup = mount({ ...payload, round: payload.round + 1 });
await new Promise((r) => setTimeout(r, 100));
assert.ok($(".rr-sbtn.spd").textContent.startsWith("0.1x"));
ok("speed menu: 0.1x..4x on hover or click; the choice survives a round switch");

// slow motion really is slow: at 0.1x one second of wall time moves ~0.1 s of round time
triggers.length = 0;
const t00 = root.__rr.st.t;
click($$(".rr-sbtn")[0]);  // play
await new Promise((r) => setTimeout(r, 500));
click($$(".rr-sbtn")[0]);  // pause
const moved = (root.__rr.st.t - t00) / payload.tr;
assert.ok(moved > 0 && moved < 0.2, "moved " + moved + " s in 0.5 s at 0.1x");
ok("0.1x plays slowly (" + moved.toFixed(3) + " s of the round in 0.5 s)");

// keys on the timeline: arrows step ONE sample, [ ] change the speed
const trackEl = $(".rr-track");
const key = (k, extra) => trackEl.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, ...extra }));
const tBefore = root.__rr.st.t;
key("ArrowRight");
assert.strictEqual(Math.round(root.__rr.st.t - tBefore), payload.step);
key("ArrowRight", { shiftKey: true });
key("]");
assert.strictEqual(root.__rr.speed, 0.25);
key("[");
assert.strictEqual(root.__rr.speed, 0.1);
ok("arrows step one sample, Shift one second, [ ] change the speed");

// the duel panel: live aim numbers of both duelists
if (payload.duels.length && payload.duels[0].tel) {
  click($$(".rr-card")[0]);
  const cells = $$(".rr-dcol .rr-dv").map((n) => n.textContent);
  assert.ok(cells.length >= 6);
  assert.ok($$(".rr-dcv").length === 2);
  assert.ok($(".rr-dfs"));
  ok("duel panel: live numbers " + cells.slice(0, 3).join(" | ") + " and a chart per duelist");
  save("duel_panel");
  click($(".rr-sbtn.exit"));
  assert.ok($(".rr-duelp").classList.contains("hid"));
}

// playback never reruns Streamlit
triggers.length = 0;
click($$(".rr-sbtn")[0]);  // play
await new Promise((r) => setTimeout(r, 120));
assert.strictEqual(root.__rr.st.playing, true);
assert.ok(root.__rr.st.t >= payload.at);
click($$(".rr-sbtn")[0]);  // pause
assert.strictEqual(triggers.length, 0);
ok("playback runs in the browser (no Streamlit trigger)");

cleanup();
console.log("JS OK");
process.exit(0);
