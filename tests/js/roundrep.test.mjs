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
  const named = $$(".rr-kp.on .rr-kname").map((n) => n.textContent).sort();
  assert.deepStrictEqual(named, [...new Set([z.kn, z.vn])].filter((n) => payload.players.some((p) => p.name === n)).sort());
  save("duel");
  click($(".rr-sbtn.exit"));
  assert.strictEqual(root.__rr.st.sel, -1);
  ok("a duel locks the playhead to its segment, both duelists' keys, exit releases");
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

// playback never reruns Streamlit
triggers.length = 0;
click($$(".rr-sbtn")[0]);  // play
await new Promise((r) => setTimeout(r, 120));
assert.strictEqual(root.__rr.st.playing, true);
assert.ok(root.__rr.st.t > payload.at);
click($$(".rr-sbtn")[0]);  // pause
assert.strictEqual(triggers.length, 0);
ok("playback runs in the browser (no Streamlit trigger)");

cleanup();
console.log("JS OK");
process.exit(0);
