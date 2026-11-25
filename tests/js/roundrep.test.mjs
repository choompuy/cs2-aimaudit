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

// the component is common.js + roundrep.js in one module, like ui/component.py builds it
const asset = (name) => fs.readFileSync(path.join(here, "..", "..", "ui", name), "utf8");
const mod = await import("data:text/javascript;base64," + Buffer.from(asset("common.js") + asset("roundrep.js")).toString("base64"));
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

// players: split into CT / T groups, sorted by nickname inside a team, nobody special
const rows = $$(".rr-prow .rr-pname").map((n) => n.textContent);
const cmp = (a, b) => (a.toLowerCase() < b.toLowerCase() ? -1 : a.toLowerCase() > b.toLowerCase() ? 1 : 0);
const rank = { CT: 0, T: 1 };
const want = [...payload.players]
  .sort((a, b) => (rank[a.side] ?? 2) - (rank[b.side] ?? 2) || cmp(a.name, b.name))
  .map((p) => p.name);
assert.deepStrictEqual(rows, want);
assert.deepStrictEqual(payload.players.map((p) => p.name), want);   // the payload already has this order
const heads = $$(".rr-th").map((n) => n.textContent.replace(/\s+/g, " ").trim());
assert.strictEqual(heads.length, 2);
assert.ok(heads[0].startsWith("CT") && heads[1].startsWith("T"));
// each team header sits right before the first player of that team
const order = $$(".rr-left .rr-th, .rr-left .rr-prow")
  .map((n) => (n.classList.contains("rr-th") ? "H" : "P"))
  .join("");
assert.ok(/^HP+HP+$/.test(order), order);
assert.strictEqual($$(".rr-team").length, 0);
ok("players split into CT / T, sorted by nickname inside a team (" + rows.length + ")");

// the player card: kills / deaths of the MATCH (before the round + live), money, HP, no photo
const card = (name) => $$(".rr-prow").find((r) => r.querySelector(".rr-pname").textContent === name);
const kdOf = (name) => [Number(card(name).querySelector(".rr-pkk b").textContent), Number(card(name).querySelector(".rr-pdk b").textContent)];
const pByName = (name) => payload.players.find((p) => p.name === name);
assert.strictEqual($$(".rr-prow img, .rr-prow canvas").length, 0);
assert.strictEqual($$(".rr-prow .rr-pbar").length, rows.length);
assert.ok(card("zeta").querySelector(".rr-pm").textContent.startsWith("$"));
assert.deepStrictEqual(kdOf("zeta"), pByName("zeta").kd);
assert.deepStrictEqual(kdOf("zeta"), [7, 12]);
// the HP is the card background: the fill is as wide as the HP, empty for the dead
const fillOf = (name) => card(name).querySelector(".rr-pbar i").style.width;
assert.strictEqual(fillOf("Charlie"), "100%");
seekFrac(0.99);
await new Promise((r) => setTimeout(r, 60));
assert.deepStrictEqual(kdOf("zeta"), [8, 12]);      // killed Alpha in the round
assert.deepStrictEqual(kdOf("Alpha"), [3, 5]);      // ... and Alpha died
assert.deepStrictEqual(kdOf("foxtrot"), [10, 10]);  // died to Echo
assert.deepStrictEqual(kdOf("Echo"), [1, 0]);
assert.strictEqual(fillOf("Alpha"), "0%");
assert.ok(card("Alpha").classList.contains("dead") && card("Alpha").querySelector(".rr-php.dead"));
assert.ok(!card("Charlie").classList.contains("dead") && /^\d+$/.test(card("Charlie").querySelector(".rr-php").textContent));
seekFrac(0);
await new Promise((r) => setTimeout(r, 60));
assert.deepStrictEqual(kdOf("zeta"), [7, 12]);
ok("player cards: kills / deaths of the match, live; a dead player's HP is the skull");

// nothing in a card appears or disappears: every slot is in every card,
// empty or not
const SLOTS = [".rr-pw", ".rr-pa", ".rr-php", ".rr-putil", ".rr-pbm", ".rr-pind", ".rr-pm", ".rr-psp", ".rr-pkd", ".rr-pbar i"];
for (const r of $$(".rr-prow")) {
  for (const c of SLOTS) assert.ok(r.querySelector(c), "slot " + c);
  assert.strictEqual(r.querySelectorAll(".rr-pl1, .rr-pl2, .rr-pl3").length, 3);
}
assert.strictEqual($$(".rr-pg").length, 2);   // one group per team
ok("fixed slots: every card has all of them");

// the screen decides how much of a card is shown: height -> density, width -> stacked panels
const screen = async (wrapW, boxW, vh) => {
  sizes["rr-wrap"] = [wrapW, 470];
  sizes.rr = [boxW, 600];
  window.innerHeight = vh;
  window.dispatchEvent(new window.Event("resize"));
  await new Promise((r) => setTimeout(r, 80));
};
const where = (name) => (card(name).querySelector(".rr-pm").closest(".rr-pl3") ? "pl3" : card(name).querySelector(".rr-pm").closest(".rr-pl2") ? "pl2" : "?");
await screen(1100, 1180, 1000);   // wide and tall: all three lines
assert.strictEqual($(".rr-left").dataset.dens, "full");
assert.strictEqual(where("zeta"), "pl3");
assert.ok(!$(".rr").classList.contains("narrow"));
await screen(1100, 1180, 800);    // a shorter screen: two lines, the money moves up behind the K/D
assert.strictEqual($(".rr-left").dataset.dens, "cmp");
assert.strictEqual(where("zeta"), "pl2");
await screen(1100, 1180, 600);    // a short one: the name line only
assert.strictEqual($(".rr-left").dataset.dens, "mini");
await screen(700, 700, 800);      // narrow: the panels stack under the map, a card is whole
assert.ok($(".rr").classList.contains("narrow"));
assert.strictEqual($(".rr-left").dataset.dens, "full");
assert.strictEqual(where("zeta"), "pl3");
await screen(1100, 1180, 1000);
assert.strictEqual($(".rr-left").dataset.dens, "full");
assert.strictEqual(where("zeta"), "pl3");
assert.deepStrictEqual(kdOf("zeta"), [7, 12]);   // a relayout keeps the live numbers
await screen(760, 1180, 768);
ok("density follows the screen: full / cmp / mini by height, stacked panels under 900px");

// a card and the map point at the same player; the Names toggle hides the labels of the map
{
  const row = card("zeta");
  row.dispatchEvent(new window.MouseEvent("mouseenter"));
  assert.deepStrictEqual($$(".rr-prow.hov .rr-pname").map((n) => n.textContent), ["zeta"]);
  row.dispatchEvent(new window.MouseEvent("mouseleave"));
  assert.strictEqual($$(".rr-prow.hov").length, 0);
  const names = $(".rr-grp.vw .rr-sbtn");
  assert.ok(names.classList.contains("on") && root.__rr.labels === true);
  click(names);
  assert.ok(!names.classList.contains("on") && root.__rr.labels === false);
  click(names);
  assert.strictEqual(root.__rr.labels, true);
  ok("hover links a card with the map; the Names toggle");
}

// blinded: the demo holds the WHOLE length of the flash, what is left is counted
// from its start; the card shows the seconds and a wash that runs out
{
  const pi = payload.players.findIndex((p) => p.name === "Charlie");
  const n = payload.players[pi].x.length, from = 10, secs = 2;
  const held = Math.ceil(secs * 1.4 * payload.tr / payload.step);   // the demo holds it through the fade
  const fl = Array.from({ length: n }, (_, i) => (i >= from && i < from + held ? secs : 0));
  cleanup();
  const at = (s) => payload.t0 + from * payload.step + s * payload.tr;
  const mountAt = async (tick) => {
    cleanup = mount({ ...payload, at: tick, sel: -1, players: payload.players.map((p, i) => (i === pi ? { ...p, fl } : p)) });
    await new Promise((r) => setTimeout(r, 60));
  };
  const look = () => [card("Charlie").classList.contains("blind"), card("Charlie").querySelector(".rr-pfc b").textContent,
    Number(card("Charlie").querySelector(".rr-pfl i").style.getPropertyValue("--k"))];
  await mountAt(at(-0.2));
  assert.strictEqual(look()[0], false);
  await mountAt(at(0.5));
  assert.deepStrictEqual(look(), [true, "1.5", 0.75]);
  cleanup();
  await mountAt(at(1.5));
  assert.deepStrictEqual(look(), [true, "0.5", 0.25]);
  cleanup();
  await mountAt(at(2.3));   // still held by the demo, but over
  assert.strictEqual(look()[0], false);
  cleanup();
  cleanup = mount(payload);
  await new Promise((r) => setTimeout(r, 100));
  ok("blinded: the seconds left and the wash run out by the flash's own length");
}

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
  assert.ok($(".rr-lock").style.visibility !== "hidden" && !$(".rr-sbtn.exit").disabled);
  seekFrac(0.99);
  assert.ok(root.__rr.st.t <= z.b && root.__rr.st.t >= z.a);
  const named = $$(".rr-dcol .rr-dnm b").map((n) => n.textContent).sort();
  assert.deepStrictEqual(named, [...new Set([z.kn, z.vn])].filter((n) => payload.players.some((p) => p.name === n)).sort());
  save("duel");
  click($(".rr-sbtn.exit"));
  assert.strictEqual(root.__rr.st.sel, -1);
  // hidden, not removed: the controls to the right of the exit button do not move
  assert.ok($(".rr-lock").style.visibility === "hidden" && $(".rr-sbtn.exit").style.visibility === "hidden");
  assert.ok($(".rr-ctl").contains($(".rr-lock")) && $(".rr-ctl").contains($(".rr-sbtn.exit")));
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

// the round result banner is gone: the round chips + the timeline markers carry it
assert.strictEqual($(".rr-out"), null);
if (payload.bomb.length) {
  assert.strictEqual($$(".rr-bmk").length, payload.bomb.length + (payload.out.end != null ? 1 : 0));
  ok("bomb events + the round end are marked on the timeline (" + $$(".rr-bmk").length + ")");
}
// rounds bar chips carry how the round ended (the icon when the set has
// one, the glyph otherwise)
assert.ok($$(".rr-rd .rr-rg").length > 0 || $$(".rr-rd .rr-ico").length > 0);
ok("round chips are marked with how the round ended");

// the players table: weapon icon, armor icon, hp, money, grenades; teams' economy below
const row0 = $(".rr-prow:not(.dead)");
assert.ok(row0.querySelector(".rr-pw .rr-ico"), "weapon icon");
assert.ok(row0.querySelector(".rr-pa .rr-ico"), "armor icon");
assert.ok(/\$\d/.test(row0.querySelector(".rr-pm").textContent), "money: " + row0.querySelector(".rr-pm").textContent);
assert.ok(row0.querySelectorAll(".rr-putil .rr-ico").length >= 2, "grenades");
assert.ok(!$(".rr-pth"), "no column header row");
assert.ok(!$(".rr-left .rr-sdot"), "no team dots in the players table");
assert.ok(!row0.querySelector(".rr-pnum"), "armor shows no number");
assert.ok(!$(".rr-eco"), "no bottom economy block");
assert.ok(heads.some((h) => /\$\d/.test(h)), "team money sits in the group headers: " + heads.join(" | "));
assert.ok($$(".rr-prow .rr-pbm .rr-ico").length >= 1, "the bomb carrier is marked in the utility row");
// the kit sits in the same slot as the bomb (a CT line has no hole), never among the grenades
assert.ok(card("Charlie").querySelector(".rr-pbm .rr-ico"), "the T bomb carrier: the bomb in the slot");
assert.ok(card("bravo").querySelector(".rr-pbm .rr-ico"), "the CT with a kit: the kit in the same slot");
assert.ok(!card("bravo").querySelector(".rr-putil .rr-ico[title*='kit' i]"), "no kit among the grenades");
assert.strictEqual(card("delta").querySelectorAll(".rr-pbm .rr-ico").length, 0);  // nothing to carry: the slot stays empty
// the bomb and the kit have the colour of the other icons, the money is green
assert.ok(!card("Charlie").querySelector(".rr-pbm .rr-ico").style.getPropertyValue("--icol"));
assert.ok(!card("bravo").querySelector(".rr-pbm .rr-ico").style.getPropertyValue("--icol"));
// every name is on hover only: the weapon icon carries its name in the title
assert.ok(row0.querySelector(".rr-pw .rr-ico").title.length > 0);
ok("players table: weapon, armor, hp, money, grenades, bomb carrier; economy of both teams");

// the panels collapse from their own headers (and come back via the transport)
{
  const grid = $(".rr-grid");
  assert.ok($(".rr-lh .rr-col") && $(".rr-rh .rr-col"), "collapse buttons in both panel headers");
  click($(".rr-lh .rr-col"));
  assert.ok(grid.classList.contains("nolf"), "left panel hidden");
  click($(".rr-rh .rr-col"));
  assert.ok(grid.classList.contains("nord"), "right panel hidden");
  // the slim edge tabs bring a collapsed panel back
  assert.ok($(".rr-tab-l") && $(".rr-tab-r"), "edge tabs exist");
  click($(".rr-tab-l"));
  assert.ok(!grid.classList.contains("nolf") && grid.classList.contains("nord"), "left tab restores only its panel");
  click($(".rr-tab-r"));
  assert.ok(!grid.classList.contains("nord"), "right tab restores its panel");
  // the transport toggles hide / show the panels too
  const toggles = $$(".rr-sbtn").filter((b) => /players|duels/i.test(b.textContent));
  assert.strictEqual(toggles.length, 2, "a transport toggle per panel");
  toggles.forEach(click);
  assert.ok(grid.classList.contains("nolf") && grid.classList.contains("nord"), "transport hides both");
  toggles.forEach(click);
  assert.ok(!grid.classList.contains("nolf") && !grid.classList.contains("nord"), "panels restored");
  ok("panel headers collapse the overlays; the transport toggles bring them back");
}

// the speed menu inside a shadow root (how Streamlit hosts a component): a press on a menu
// item must not be taken for an outside press, and a click on the button after a hover keeps it open
{
  const shost = document.createElement("div");
  document.body.appendChild(shost);
  const sroot = shost.attachShadow({ mode: "open" });
  const sparent = document.createElement("div");
  sroot.appendChild(sparent);
  const sTriggers = [];
  const sCleanup = mod.default({ data: payload, parentElement: sparent, setTriggerValue: (k, v) => sTriggers.push([k, v]) });
  const sq = (sel) => sparent.querySelector(sel);
  const fire = (el, type, init = {}) => el.dispatchEvent(new window.MouseEvent(type, { bubbles: true, composed: true, ...init }));
  const sBtn = sq(".rr-sbtn.spd"), sMenu = sq(".rr-spdmenu"), sWrap = sq(".rr-spd");
  assert.ok(sMenu.hidden);
  // hover opens, then a click on the button PINS it (it used to toggle it shut)
  fire(sWrap, "mouseenter", { bubbles: false });
  assert.ok(!sMenu.hidden);
  fire(sBtn, "pointerdown"); fire(sBtn, "click");
  assert.ok(!sMenu.hidden, "a click on the button after the hover must keep the menu open");
  fire(sWrap, "mouseleave", { bubbles: false });
  await new Promise((r) => setTimeout(r, 400));
  assert.ok(!sMenu.hidden, "a pinned menu stays open when the pointer leaves");
  // the real press sequence on an item: pointerdown, pointerup, click
  const item = sMenu.querySelectorAll(".rr-spdi")[0];   // 0.1x
  fire(item, "pointerdown");
  assert.ok(!sMenu.hidden, "a press ON the menu is not an outside press (shadow DOM retargets the event)");
  fire(item, "pointerup"); fire(item, "click");
  assert.ok(sMenu.hidden);
  assert.ok(sBtn.textContent.startsWith("0.1x"), sBtn.textContent);
  // a press outside closes an open menu
  fire(sBtn, "click");
  assert.ok(!sMenu.hidden);
  fire(document.body, "pointerdown");
  assert.ok(sMenu.hidden);
  sCleanup();
  shost.remove();
  ok("speed menu inside a shadow root: hover opens, click pins, item press selects, outside press closes");
}

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

// the speed value does not flicker: it snaps to 5 u/s (no label, just digits + unit)
for (const sp of $$(".rr-psp")) {
  const txt = (sp.querySelector(".rr-pspv") || sp).textContent.trim();
  if (txt) assert.ok(/^\d+ /.test(txt) && Number(txt.split(" ")[0]) % 5 === 0, "speed snaps to 5: " + txt);
}
// the moving threshold lives only inside the duel panel's shot chart now
click($$(".rr-card")[0]);
assert.ok($$(".rr-legdash").length > 0, "the threshold line legend in the duel panel");
click($(".rr-sbtn.exit"));
ok("moving threshold: dashed line in the duel chart only");

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

// ---------- the icons (payload.icons rides along from the fixture set) ----
if (Object.keys(payload.icons || {}).length) {
  // there is no kill feed anymore
  assert.strictEqual($$(".rr-fe").length, 0, "kill feed removed");

  // duel cards: weapon + headshot + the kill modifiers of duel 1
  const metas = $$(".rr-card .rr-cmeta");
  assert.strictEqual(metas[0].querySelectorAll(".rr-ico").length, 5,
    "duel 1 meta icons: " + metas[0].innerHTML);  // ak47, hs, wallbang, noscope, flashed
  assert.strictEqual(metas[1].querySelectorAll(".rr-ico").length, 1);   // awp only
  ok("duel cards: weapon + headshot + wallbang / noscope / flashed icons on duel 1");

  // the player list carries the live weapon icon; the dead get the skull
  seekFrac(0.9);
  assert.ok($$(".rr-prow .rr-pw .rr-ico").length > 0);
  const dead = $$(".rr-prow.dead");
  assert.ok(dead.length > 0);
  assert.ok(dead[0].querySelector(".rr-php .rr-ico"), "dead player hp cell: " + dead[0].innerHTML);
  // the K/D deaths mark is the icon set's own skull, not a drawn cross
  const dk = $(".rr-prow .rr-pdk");
  assert.ok(dk && dk.querySelector(".rr-ico"), "deaths cell uses the game skull icon");
  ok("player list: live weapon icons; the dead get the skull, K/D uses the game skull");

  // the round chips and the timeline markers
  assert.ok($$(".rr-rd .rr-ico").length > 0, "round chip icons missing");
  assert.ok($$(".rr-bmk .rr-ico").length >= 3, "timeline marker icons missing");
  ok("round chips and bomb timeline markers carry icons");

  // the duel panel names the weapon with its silhouette
  click($$(".rr-card")[0]);
  assert.ok($(".rr-duelp .rr-dpw .rr-ico"), "duel panel weapon icon missing");
  click($(".rr-sbtn.exit"));
  ok("the duel panel shows the duel's weapon icon");

  // no second copy of what is on the screen: no tooltip on the map, none on a duel card
  assert.ok(!$(".rr-tip"));
  assert.ok($$(".rr-card").every((c) => !c.title && c.getAttribute("aria-label")));
  ok("no tooltips that repeat the card");

  // the bomb in hands is the weapon: the bomb slot stays empty, a knife is lower than a rifle
  {
    cleanup();
    const inHands = payload.players.map((p) => (p.name === "Charlie" ? { ...p, w: [[payload.t0, "c4"]] } : p));
    cleanup = mount({ ...payload, at: payload.t0, players: inHands });
    await new Promise((r) => setTimeout(r, 80));
    assert.strictEqual(card("Charlie").querySelectorAll(".rr-pbm .rr-ico").length, 0);
    assert.strictEqual(card("Charlie").querySelectorAll(".rr-pw .rr-ico").length, 1);
    cleanup();
    const wide = { ...payload.icons, knife: ["data:,", 440, 110], ak47: ["data:,", 678, 222] };
    const hOf = (n) => parseFloat(card(n).querySelector(".rr-pw .rr-ico").style.getPropertyValue("--h"));
    cleanup = mount({ ...payload, at: payload.t0, icons: wide, players: payload.players.map((p) => (
      p.name === "Charlie" ? { ...p, w: [[payload.t0, "knife"]] } : p.name === "delta" ? { ...p, w: [[payload.t0, "ak47"]] } : p)) });
    await new Promise((r) => setTimeout(r, 80));
    assert.ok(hOf("Charlie") * 4 < hOf("delta") * 3.05, "a knife is shorter than a rifle: " + hOf("Charlie") + " / " + hOf("delta"));
    ok("the bomb in hands is shown once; icons of one size");
  }
  // the clock counts from the end of the freeze, the lead before it is negative
  {
    cleanup();
    cleanup = mount({ ...payload, at: payload.t0, start: payload.t0 + 2 * payload.tr });
    await new Promise((r) => setTimeout(r, 80));
    assert.strictEqual($(".rr-time b").textContent, "-0:02");
    assert.strictEqual($$(".rr-bm .rr-tm").map((n) => n.textContent).includes("0:00"), true);
    ok("the round clock starts at the end of the freeze");
  }

  // the no-icon fallback: same payload without icons -> plain text everywhere
  cleanup();
  cleanup = mount({ ...payload, icons: {}, wlab: {} });
  await new Promise((r) => setTimeout(r, 120));
  seekFrac(0.95);
  assert.strictEqual($$(".rr-ico").length, 0);
  assert.ok($$(".rr-pw").some((n) => n.textContent.trim() === "ak47"));
  ok("without the icon set everything falls back to text (no .rr-ico at all)");
}

cleanup();
console.log("JS OK");
process.exit(0);
