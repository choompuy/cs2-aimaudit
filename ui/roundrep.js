// 2D replay front end (Streamlit components v2). The Python side
// (ui/roundrep.py: round_payload) builds the whole model; this file only
// interpolates, draws and animates. No Streamlit rerun happens while
// playing - the one trigger is a click on the rounds bar ("rnd").
//
// Per-browser state that must survive a round switch (the payload is
// replaced, this function runs again) lives on root.__rr: which players are
// hidden from the map, the players panel, the speed and the zoom / pan.
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const d = data;
  const root = parentElement;
  const S = root.__rr || (root.__rr = {});
  if (typeof S.cleanup === "function") { try { S.cleanup(); } catch (e) { /* ignore */ } }
  S.hidden = S.hidden || {};
  if (S.panel == null) S.panel = true;
  if (S.dpanel == null) S.dpanel = true;
  if (!S.speed) S.speed = 1;
  S.view = S.view || [];

  const disposers = [];
  const cleanup = () => {
    while (disposers.length) { try { disposers.pop()(); } catch (e) { /* ignore */ } }
  };
  S.cleanup = cleanup;

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const fmtT = (sec) => {
    sec = Math.max(0, Math.round(sec));
    return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0");
  };
  const lab = d.lab || {};
  const C = d.col || {};
  const P = d.players || [];
  // glyphs of how a round ended / what the bomb did - the FALLBACK when the
  // icon of a badge is missing; every use also carries a title / text label
  const WHY_GLYPH = { bomb: "\u{1F4A5}", defuse: "\u2702", elim: "\u2620", time: "\u23F1", surrender: "\u2691" };
  const BOMB_GLYPH = { plant: "\u{1F4A3}", defuse: "\u2702", boom: "\u{1F4A5}" };
  // the icons of the same things (ui/icons.py): silhouettes in the CS2
  // kill-feed style, delivered in d.icons for exactly this round
  const WHY_ICON = { bomb: "bomb_explode", defuse: "defuse", elim: "skull", time: "hourglass" };
  const BOMB_ICON = { plant: "bomb", defuse: "defuse", boom: "bomb_explode" };
  // how the round ended, as a reason key: the demo's own when it maps to an
  // icon, else read it off the bomb story (explosion / defuse) or the wipe -
  // never a bare flag where the fight or the bomb already says what happened
  const endWhy = (why) => {
    if (WHY_ICON[why]) return why;
    const bs = d.bomb || [];
    for (let i = bs.length - 1; i >= 0; i--) {
      if (bs[i][1] === "boom") return "bomb";
      if (bs[i][1] === "defuse") return "defuse";
    }
    return "elim";
  };
  const IC = d.icons || {};
  const WLAB = d.wlab || {};
  const wl = (w) => (w ? (WLAB[w] || w) : "");
  const THR = typeof d.thr === "number" ? d.thr : null;
  const GRENADES = ["hegrenade", "flashbang", "smokegrenade", "molotov", "incgrenade", "decoy"];

  // an icon in the DOM: the SVG is never inlined - only its data URI, as
  // the alpha MASK of a colour box (fill inside the file never matters:
  // grenades, the bomb, the defuse kit and the timer carry no white fill
  // and would stay black otherwise)
  const iconEl = (key, title, h, color) => {
    const m = IC[key];
    if (!m) return null;
    const s = el("span", "rr-ico");
    s.style.setProperty("--ico", 'url("' + m[0] + '")');
    s.style.setProperty("--ar", String(m[1] && m[2] ? m[1] / m[2] : 1));
    if (h) s.style.setProperty("--h", h + "px");
    if (color) s.style.setProperty("--icol", color);
    if (title) {
      s.title = title;
      s.setAttribute("aria-label", title);
      s.setAttribute("role", "img");
    }
    return s;
  };
  // the icon if there is one, else the plain text (the no-icon fallback)
  const icoOrText = (key, text, title, h, color) =>
    iconEl(key, title, h, color) || el("span", null, text);
  const whyIco = (why, title) => {
    if (why && WHY_ICON[why] && IC[WHY_ICON[why]]) return iconEl(WHY_ICON[why], title, 13);
    if (why && WHY_GLYPH[why]) return el("i", "rr-wg", WHY_GLYPH[why]);
    return null;
  };

  let host = root.querySelector(".rr-host");
  if (!host) { host = el("div", "rr-host"); root.appendChild(host); }
  host.replaceChildren();
  const box = el("div", "rr");
  host.appendChild(box);
  // team colours + round-chip backgrounds as custom properties for the DOM
  for (const [ck, v] of [["ct", C.ct], ["t", C.t], ["ctb", C.ctb], ["tb", C.ttb], ["acc", C.acc], ["warn", C.warn]]) {
    if (v) box.style.setProperty("--rr-" + ck, v);
  }
  const FF = (root.isConnected && window.getComputedStyle
    ? window.getComputedStyle(box).fontFamily : "") || "system-ui, sans-serif";

  const grid = el("div", "rr-grid");
  const left = el("div", "rr-left");
  const main = el("div", "rr-main");
  const right = el("div", "rr-right");
  grid.append(main);
  box.appendChild(grid);
  const outEl = el("div", "rr-out");
  const wrap = el("div", "rr-wrap");
  const cvs = document.createElement("canvas");
  // the player / duel panels float OVER the map: transparent chrome, the
  // cards keep their own surface
  wrap.append(cvs, left, right);
  // slim edge tabs bring a hidden panel back (the transport buttons do too)
  const ptab = el("button", "rr-tab rr-tab-l", "\u203A");
  const dtab = el("button", "rr-tab rr-tab-r", "\u2039");
  ptab.type = "button"; dtab.type = "button";
  ptab.title = lab.players_show || "show";
  dtab.title = lab.duels_show || "show";
  ptab.setAttribute("aria-label", ptab.title);
  dtab.setAttribute("aria-label", dtab.title);
  ptab.addEventListener("click", () => { S.panel = true; applyPanel(); layout(); draw(); });
  dtab.addEventListener("click", () => { S.dpanel = true; applyPanel(); draw(); });
  wrap.append(ptab, dtab);
  const ctl = el("div", "rr-ctl");
  const tl = el("div", "rr-tl");
  const roundsEl = el("div", "rr-rounds");
  main.append(outEl, wrap, ctl, tl, roundsEl);
  const duelWrap = el("div", "rr-duelp hid");
  box.appendChild(duelWrap);

  // ---------- state ---------------------------------------------------------
  const SPEEDS = [0.1, 0.25, 0.5, 1, 2, 4];
  let playing = false;
  let speed = SPEEDS.indexOf(S.speed) >= 0 ? S.speed : 1;
  let sel = (typeof d.sel === "number") ? d.sel : -1;
  let t = Math.min(Math.max(d.at, d.t0), d.t1);
  let scrubbing = false;

  const duelAt = (i) => ((i >= 0 && i < d.duels.length) ? d.duels[i] : null);
  const winStart = () => { const z = duelAt(sel); return z ? z.a : d.t0; };
  const winEnd = () => { const z = duelAt(sel); return z ? z.b : d.t1; };
  const clampT = (x) => Math.min(Math.max(x, winStart()), winEnd());
  const sideCol = (side) => (side === "CT" ? C.ct : side === "T" ? C.t : C.text2);
  const sideCls = (side) => (side === "CT" ? "ct" : side === "T" ? "t" : "");
  const visible = (p) => !!p && !S.hidden[p.sid];
  const spdTxt = (s) => s + "x";
  const money = (v) => "$" + Math.round(v).toLocaleString("en-US").replace(/,/g, "\u2009");

  // ---------- slowly changing state of a player: [[tick, ...value]] runs ----------------------
  function runAt(runs, tick) {
    if (!runs || !runs.length) return null;
    let r = runs[0];
    for (const x of runs) { if (x[0] <= tick) r = x; else break; }
    return r;
  }
  const valAt = (runs, tick, dflt) => { const r = runAt(runs, tick); return r ? r[1] : dflt; };
  function armorKey(a, h) {
    if (h) return a > 50 ? "armor_helmet" : "armor_half_helmet";
    if (a <= 0) return "armor_none";
    return a > 50 ? "armor_full" : "armor_half";
  }
  const countKeys = (keys) => { const c = {}; for (const k of keys) c[k] = (c[k] || 0) + 1; return c; };

  // ---------- round result: who won and HOW -----------------------------------------------
  function buildOutcome() {
    outEl.replaceChildren();
    const o = d.out || {};
    outEl.className = "rr-out" + (o.win === "CT" ? " wct" : o.win === "T" ? " wt" : " unk");
    const head = el("div", "rr-out1");
    head.appendChild(el("span", "rr-badge", (lab.round || "R") + " " + d.round));
    if (o.win) {
      head.appendChild(el("b", "rr-owin", o.win === "CT" ? (lab.won_ct || "CT") : (lab.won_t || "T")));
      const ewhy = endWhy(o.why);
      const w = el("span", "rr-why");
      const wg = whyIco(ewhy, lab["why_" + ewhy] || ewhy);
      if (wg) w.appendChild(wg);
      w.appendChild(document.createTextNode(" " + (lab["why_" + ewhy] || ewhy)));
      head.appendChild(w);
    } else {
      head.appendChild(el("span", "rr-owin dim", lab.out_unknown || ""));
    }
    if (o.win && o.inferred) {
      const inf = el("span", "rr-inf", lab.out_inferred || "");
      inf.title = lab.out_inferred || "";
      head.appendChild(inf);
    }
    if (o.end != null) {
      const ot = el("span", "rr-outt");
      const ti = iconEl("timer", lab.time || "time", 11);
      if (ti) ot.appendChild(ti);
      else ot.appendChild(document.createTextNode("\u23F1 "));
      ot.appendChild(document.createTextNode(fmtT((o.end - d.t0) / d.tr)));
      head.appendChild(ot);
    }
    outEl.appendChild(head);
    // the bomb story of the round (plant / defuse / explosion with the time and the player)
    const parts = [];
    for (const b of d.bomb || []) {
      const key = b[1];
      if (!BOMB_GLYPH[key]) continue;
      // the explosion's player is only the planter: not worth repeating
      const who = key !== "boom" && b[2] >= 0 && P[b[2]] ? P[b[2]].name : "";
      const text = (lab["b_" + key] || key) + " " + fmtT((b[0] - d.t0) / d.tr) + (who ? " \u00B7 " + who : "");
      parts.push(icoOrText(BOMB_ICON[key], BOMB_GLYPH[key] + " " + text, text, 13));
    }
    if (parts.length) {
      const o2 = el("div", "rr-out2");
      parts.forEach((p, i) => {
        if (i) o2.appendChild(document.createTextNode("   "));
        o2.appendChild(p);
      });
      outEl.appendChild(o2);
    }
  }

  // ---------- controls --------------------------------------------------------------------
  const btn = (cls, ch, title, fn) => {
    const b = el("button", "rr-sbtn " + (cls || ""), ch);
    b.type = "button";
    if (title) { b.title = title; b.setAttribute("aria-label", title); }
    b.addEventListener("click", fn);
    return b;
  };

  // playback: ONE place owns the rAF loop, so the play button cannot get out
  // of sync with the animation
  let raf = null, last = 0;
  function loop(ts) {
    raf = null;
    const dt = last ? Math.min(0.1, (ts - last) / 1000) : 0;
    last = ts;
    if (!playing) { last = 0; return; }
    t = clampT(t + dt * d.tr * speed);
    if (t >= winEnd() - 0.5) { t = winEnd(); setPlaying(false); }
    draw();
    if (playing) raf = requestAnimationFrame(loop);
  }
  function setPlaying(on) {
    playing = on;
    if (on) { if (raf == null) raf = requestAnimationFrame(loop); }
    else {
      if (raf != null) { cancelAnimationFrame(raf); raf = null; }
      last = 0;
    }
    sync();
  }
  disposers.push(() => { playing = false; if (raf != null) cancelAnimationFrame(raf); raf = null; });

  const roundIdx = () => d.rounds.findIndex((r) => r[0] === d.round);
  const gotoRound = (delta) => {
    const i = roundIdx();
    const nx = d.rounds[i + delta];
    if (i >= 0 && nx) setTriggerValue("rnd", nx[0]);
  };

  const playBtn = btn("", "\u25B6", lab.play || "play", () => {
    if (!playing && t >= winEnd() - 0.5) t = winStart();
    setPlaying(!playing);
  });
  const rPrev = btn("", "\u00AB", lab.round_prev || "previous round", () => gotoRound(-1));
  const rNext = btn("", "\u00BB", lab.round_next || "next round", () => gotoRound(1));
  const dPrev = btn("", "\u2039", lab.duel_prev || "previous duel", () => jumpDuel(-1));
  const dNext = btn("", "\u203A", lab.duel_next || "next duel", () => jumpDuel(1));
  const exitBtn = btn("exit", "\u2715", lab.exit || "exit duel", () => exitDuel());

  // speed: ONE button showing the current speed; a menu with 0.1x..4x opens on
  // hover (mouse) or on click / tap / Enter (touch, keyboard), Escape closes it
  const spdWrap = el("div", "rr-spd");
  const spdBtn = el("button", "rr-sbtn spd");
  spdBtn.type = "button";
  spdBtn.setAttribute("aria-haspopup", "menu");
  spdBtn.setAttribute("aria-expanded", "false");
  const spdMenu = el("div", "rr-spdmenu");
  spdMenu.setAttribute("role", "menu");
  spdMenu.hidden = true;
  const spdItems = SPEEDS.map((s) => {
    const b = el("button", "rr-spdi", spdTxt(s));
    b.type = "button";
    b.setAttribute("role", "menuitemradio");
    b.addEventListener("click", () => { setSpeed(s); openMenu(false); try { spdBtn.focus(); } catch (err) { /* ignore */ } });
    spdMenu.appendChild(b);
    return b;
  });
  spdWrap.append(spdBtn, spdMenu);
  let menuOpen = false, menuPinned = false, menuTimer = null;
  function openMenu(on, pin) {
    if (menuTimer) { clearTimeout(menuTimer); menuTimer = null; }
    menuOpen = on;
    menuPinned = on ? !!pin : false;
    spdMenu.hidden = !on;
    spdBtn.setAttribute("aria-expanded", on ? "true" : "false");
  }
  function setSpeed(s) {
    speed = s; S.speed = s;
    sync();
  }
  const stepSpeed = (dir) => {
    const i = SPEEDS.indexOf(speed);
    const j = Math.max(0, Math.min(SPEEDS.length - 1, (i < 0 ? SPEEDS.indexOf(1) : i) + dir));
    setSpeed(SPEEDS[j]);
  };
  // a click PINS the menu open (a hover alone closes it when the pointer leaves);
  // a click on the button while it is pinned closes it
  spdBtn.addEventListener("click", () => {
    if (menuOpen && menuPinned) openMenu(false);
    else openMenu(true, true);
  });
  spdWrap.addEventListener("mouseenter", () => { if (!menuOpen) openMenu(true, false); else if (menuTimer) { clearTimeout(menuTimer); menuTimer = null; } });
  spdWrap.addEventListener("mouseleave", () => {
    if (menuPinned) return;
    if (menuTimer) clearTimeout(menuTimer);
    menuTimer = setTimeout(() => openMenu(false), 250);
  });
  spdWrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { openMenu(false); spdBtn.focus(); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      openMenu(true, true);
      const root2 = spdWrap.getRootNode();
      const act = root2 && root2.activeElement ? root2.activeElement : document.activeElement;
      const k = spdItems.indexOf(act);
      const nx = e.key === "ArrowDown" ? k + 1 : k - 1;
      spdItems[(nx + spdItems.length) % spdItems.length].focus();
    }
  });
  // close on a press OUTSIDE the menu. The player lives in a shadow root, so a
  // listener on the document sees the event retargeted to the shadow HOST:
  // `contains(e.target)` would call every press - on the menu items too -
  // "outside" and close the menu before the click lands. composedPath() holds
  // the real elements.
  const outside = (e) => {
    if (!menuOpen) return;
    const path = typeof e.composedPath === "function" ? e.composedPath() : [];
    const inside = path.length ? path.indexOf(spdWrap) >= 0 : spdWrap.contains(e.target);
    if (!inside) openMenu(false);
  };
  document.addEventListener("pointerdown", outside, true);
  disposers.push(() => { document.removeEventListener("pointerdown", outside, true); if (menuTimer) clearTimeout(menuTimer); });

  const plBtn = btn("", lab.players || "players", lab.players_hide || "hide", () => {
    S.panel = !S.panel; applyPanel(); layout(); draw();
  });
  const dpBtn = btn("", lab.duels || "duels", lab.duels_hide || "hide", () => {
    S.dpanel = !S.dpanel; applyPanel(); draw();
  });
  const resetBtn = btn("", "\u21BB", lab.view_reset || "reset view", () => resetView());
  const timeLab = el("span", "rr-time");
  const lockChip = el("span", "rr-lock", lab.locked || "");
  ctl.append(
    playBtn,
    btn("", "\u23EE", lab.restart || "restart", () => { setPlaying(false); t = winStart(); sync(); draw(); }),
    spdWrap,
    el("i", "rr-sep"), dPrev, dNext, exitBtn,
    el("i", "rr-sep"), rPrev, rNext,
    el("i", "rr-sep"), plBtn, dpBtn, resetBtn,
    timeLab, lockChip,
  );

  function jumpDuel(dir) {
    if (!d.duels.length) return;
    let best = null;
    d.duels.forEach((z, k) => {
      const dt = dir > 0 ? z.a - t : t - z.b;
      if (dt > 0 && (best === null || dt < best.dt)) best = { dt: dt, i: k };
    });
    if (best === null) best = { i: dir > 0 ? 0 : d.duels.length - 1 };
    selectDuel(best.i);
  }
  function selectDuel(i) {
    sel = i;
    const z = duelAt(sel);
    if (z && (t < z.a || t > z.b)) t = z.a;
    t = clampT(t);
    sync(); draw();
  }
  function exitDuel() { sel = -1; sync(); draw(); }
  const toggleDuel = (i) => { if (sel === i) exitDuel(); else selectDuel(i); };

  // ---------- timeline ---------------------------------------------------------------------
  const span = Math.max(1, d.t1 - d.t0);
  const pct = (tick) => (tick - d.t0) / span;
  const sideOf = (i) => (i >= 0 && P[i] ? P[i].side : "?");
  // a row above the track: bomb plant / defuse / explosion and the end of the round
  const bm = el("div", "rr-bm");
  const addBm = (tick, glyph, title, iconKey) => {
    if (tick == null || tick < d.t0 || tick > d.t1) return;
    const m = el("span", "rr-bmk");
    const ic = iconKey ? iconEl(iconKey, title, 12) : null;
    if (ic) m.appendChild(ic);
    else if (glyph) m.textContent = glyph;
    else return;
    m.style.left = (pct(tick) * 100) + "%";
    m.title = title;
    bm.appendChild(m);
  };
  for (const b of d.bomb || []) {
    if (!BOMB_GLYPH[b[1]]) continue;
    const who = b[1] !== "boom" && b[2] >= 0 && P[b[2]] ? " \u00B7 " + P[b[2]].name : "";
    addBm(b[0], BOMB_GLYPH[b[1]], (lab["b_" + b[1]] || b[1]) + " " + fmtT((b[0] - d.t0) / d.tr) + who, BOMB_ICON[b[1]]);
  }
  if (d.out && d.out.end != null && d.out.win) {
    const ewhy = endWhy(d.out.why);
    addBm(d.out.end, WHY_GLYPH[d.out.why] || WHY_GLYPH[ewhy] || "\u2691",
      (d.out.win === "CT" ? (lab.won_ct || "CT") : (lab.won_t || "T")) + " \u00B7 " + (lab["why_" + ewhy] || ewhy),
      WHY_ICON[ewhy]);
  }
  const track = el("div", "rr-track");
  track.tabIndex = 0;
  track.setAttribute("role", "slider");
  track.setAttribute("aria-label", lab.time || "time");
  tl.append(bm, track);
  const killTitle = (k) => {
    const a = k[1] >= 0 ? P[k[1]] : null, v = k[2] >= 0 ? P[k[2]] : null;
    let s = (a ? a.name : "?") + " > " + (v ? v.name : "?");
    if (k[3]) s += " [" + k[3] + "]";
    if (k[4]) s += " \u00B7 " + (lab.hs || "HS");
    return s;
  };
  for (const k of d.kills) {
    const m = el("div", "rr-mark " + sideCls(sideOf(k[1])));
    m.style.left = (pct(k[0]) * 100) + "%";
    m.title = killTitle(k);
    track.appendChild(m);
  }
  const segs = d.duels.map((z) => {
    const s = el("div", "rr-seg");
    s.style.left = (pct(z.a) * 100) + "%";
    s.style.width = Math.max(0.5, (pct(z.b) - pct(z.a)) * 100) + "%";
    track.appendChild(s);
    return s;
  });
  const dimA = el("div", "rr-dim"), dimB = el("div", "rr-dim");
  const ph = el("div", "rr-ph");
  track.append(dimA, dimB, ph);

  const tickAt = (ev) => {
    const r = track.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (ev.clientX - r.left) / Math.max(1, r.width)));
    return Math.round(d.t0 + f * span);
  };
  track.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { track.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    scrubbing = true;
    if (playing) setPlaying(false);
    seek(tickAt(e));
  });
  track.addEventListener("pointermove", (e) => { if (scrubbing) seek(tickAt(e)); });
  track.addEventListener("pointerup", () => { scrubbing = false; });
  track.addEventListener("pointercancel", () => { scrubbing = false; });
  // keys on the focused timeline: <- / -> = ONE sample (a few ticks, for
  // frame-by-frame aim study), Shift = 1 s, Home / End, Space = play, [ ] = speed
  track.addEventListener("keydown", (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const k = e.key;
    if (k === " " || k === "Spacebar") { e.preventDefault(); playBtn.click(); return; }
    if (k === "[" || k === "]") { e.preventDefault(); stepSpeed(k === "[" ? -1 : 1); return; }
    if (k !== "ArrowLeft" && k !== "ArrowRight" && k !== "Home" && k !== "End") return;
    e.preventDefault();
    if (playing) setPlaying(false);
    const st = e.shiftKey ? d.tr : d.step;
    if (k === "Home") seek(winStart());
    else if (k === "End") seek(winEnd());
    else seek(t + (k === "ArrowLeft" ? -st : st));
  });
  function seek(x) { t = clampT(x); draw(); }

  // ---------- rounds row (below the timeline): winner colour + how it ended ------------------
  function buildRounds() {
    roundsEl.replaceChildren();
    if (lab.rounds) roundsEl.setAttribute("aria-label", lab.rounds);
    for (const [n, w, why0] of d.rounds) {
      // the CURRENT round may carry no recorded reason: endWhy() reads it
      // off this round's bomb story instead of leaving a bare flag
      const why = w
        ? (WHY_ICON[why0] || WHY_GLYPH[why0] ? why0 : (n === d.round ? endWhy(why0) : "elim"))
        : why0;
      const b = el("button",
        "rr-rd" + (w === "CT" ? " wct" : w === "T" ? " wt" : "") + (n === d.round ? " cur" : ""));
      b.type = "button";
      b.appendChild(document.createTextNode(String(n)));
      // the end-reason slot under the number: the icon, the glyph fallback,
      // or an empty holder so undecided rounds keep the same chip height
      const slot = el("span", "rr-rgslot");
      if (why && WHY_GLYPH[why]) {
        slot.appendChild(
          WHY_ICON[why] && IC[WHY_ICON[why]]
            ? iconEl(WHY_ICON[why], lab["why_" + why] || why, 10)
            : el("i", "rr-rg", WHY_GLYPH[why]));
      }
      b.appendChild(slot);
      let tip = (lab.round || "R") + " " + n;
      if (w === "CT") tip += " \u00B7 " + (lab.won_ct || "");
      else if (w === "T") tip += " \u00B7 " + (lab.won_t || "");
      if (why) tip += " \u00B7 " + (lab["why_" + why] || "");
      b.title = tip;
      b.setAttribute("aria-label", tip);
      b.addEventListener("click", () => { if (n !== d.round) setTriggerValue("rnd", n); });
      roundsEl.appendChild(b);
    }
    const cur = roundsEl.querySelector(".rr-rd.cur");
    if (cur) {
      roundsEl.scrollLeft = Math.max(0, cur.offsetLeft - (roundsEl.clientWidth - cur.offsetWidth) / 2);
    }
  }

  // ---------- right: EVERY duel of the round ---------------------------------------------------
  const cardEls = [];
  function buildCards() {
    right.replaceChildren();
    const rh = el("div", "rr-rh");
    rh.appendChild(el("span", "rr-lt",
      (lab.duels || "duels") + (d.duels.length ? " \u00B7 " + d.duels.length : "")));
    // the panel's own collapse button (the transport toggle brings it back)
    const colR = el("button", "rr-eye rr-col", "\u2013");
    colR.type = "button";
    colR.title = lab.duels_hide || "hide";
    colR.setAttribute("aria-label", colR.title);
    colR.addEventListener("click", () => { S.dpanel = false; applyPanel(); draw(); });
    rh.appendChild(colR);
    right.appendChild(rh);
    if (!d.duels.length) {
      right.appendChild(el("div", "rr-noduels", lab.no_duels || ""));
      return;
    }
    d.duels.forEach((z, i) => {
      const c = el("button", "rr-card");
      c.type = "button";
      const top = el("div", "rr-ch");
      const kn = el("span", "rr-kn", z.kn);
      const vn = el("span", "rr-vn", z.vn);
      kn.style.color = sideCol(sideOf(z.ki));
      vn.style.color = sideCol(sideOf(z.vi));
      top.append(kn, el("span", "rr-ar", "\u25B8"), vn, el("span", "rr-ctm2", fmtT((z.tk - d.t0) / d.tr)));
      // the meta line: the weapon icon (tooltip = name), the headshot icon
      // and the modifier icons, then the numbers; text only without icons
      const meta = el("div", "rr-cmeta");
      const wIco = iconEl(z.w, wl(z.w), 11);
      if (wIco) meta.appendChild(wIco);
      else if (z.w) meta.appendChild(el("span", null, z.w));
      if (z.hs) {
        const hsIco = iconEl("headshot", lab.hs || "HS", 11);
        meta.appendChild(hsIco || el("span", null, lab.hs || "HS"));
      }
      for (const [flag, key, text] of [
        [z.wb, "wallbang", lab.wallbang || "wallbang"],
        [z.ns, "noscope", lab.noscope || "noscope"],
        [z.sm, "smoke_kill", lab.smoke_kill || "through smoke"],
        [z.bl, "flashed_kill", lab.flashed_kill || "flashed"],
      ]) {
        if (!flag) continue;
        const f = iconEl(key, text, 11);
        if (f) meta.appendChild(f);
        else meta.appendChild(el("span", null, text));
      }
      const nums = [(z.dist != null ? z.dist + "u" : ""), (z.dur != null ? z.dur + "s" : "")]
        .filter(Boolean).join(" \u00B7 ");
      if (nums) meta.appendChild(el("span", null, nums));
      const tipBits = [wl(z.w) || z.w, z.hs ? (lab.hs || "HS") : "",
        z.wb ? (lab.wallbang || "") : "", z.ns ? (lab.noscope || "") : "",
        z.sm ? (lab.smoke_kill || "") : "", z.bl ? (lab.flashed_kill || "") : "", nums]
        .filter(Boolean).join(" \u00B7 ");
      c.title = z.kn + " > " + z.vn + (tipBits ? " \u00B7 " + tipBits : "");
      c.setAttribute("aria-label", c.title);
      c.append(top, meta);
      c.addEventListener("click", () => toggleDuel(i));
      right.appendChild(c);
      cardEls.push(c);
    });
  }

  // ---------- left: players, one list sorted by nickname, an eye each -----------------------------
  // Lucide (ISC License, see THIRD_PARTY_LICENSES.md) - static markup, no
  // data inside, so the innerHTML here is safe by construction
  const EYE_ON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49"/><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/><path d="m2 2 20 20"/></svg>';
  // Lucide again (ISC): the kills / deaths marks of the card, static markup
  const SVG_O = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
  const ICO_KILLS = SVG_O + '<circle cx="12" cy="12" r="9"/><path d="M22 12h-4M6 12H2M12 6V2M12 22v-4"/></svg>';
  const ICO_SKULL = SVG_O + '<path d="m12.5 17-.5-1-.5 1h1z"/><path d="M15 22a1 1 0 0 0 1-1v-1a2 2 0 0 0 1.56-3.25 8 8 0 1 0-11.12 0A2 2 0 0 0 8 20v1a1 1 0 0 0 1 1z"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="12" r="1"/></svg>';
  const svgSpan = (markup, cls) => { const i = el("span", cls); i.innerHTML = markup; return i; };
  const lrows = [];
  const sideHeads = {};  // side -> the count span of its group header
  let masterEye = null;
  function setEye(b, on, title) {
    b.innerHTML = on ? EYE_ON : EYE_OFF;  // static markup, no data inside
    b.title = title;
    b.setAttribute("aria-label", title);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  }
  // one count of the K/D line: [mark] number; iconKey swaps the drawn mark
  // for the icon set's silhouette (the deaths skull is the game's own)
  function kdCell(markup, cls, title, iconKey) {
    const w = el("span", "rr-pk " + cls);
    w.title = title;
    const n = el("b", null, "0");
    const ic = iconKey ? iconEl(iconKey, title, 13) : null;
    w.append(ic || svgSpan(markup, "rr-pki"), n);
    return { w: w, n: n };
  }
  function buildLeft() {
    left.replaceChildren();
    lrows.length = 0;
    const head = el("div", "rr-lh");
    head.appendChild(el("span", "rr-lt", lab.players || "players"));
    masterEye = el("button", "rr-eye");
    masterEye.type = "button";
    masterEye.addEventListener("click", () => {
      const allOn = P.every(visible);
      for (const p of P) { if (allOn) S.hidden[p.sid] = true; else delete S.hidden[p.sid]; }
      applyHidden(); draw();
    });
    head.appendChild(masterEye);
    // the panel's own collapse button (the transport toggle brings it back)
    const colL = el("button", "rr-eye rr-col", "\u2013");
    colL.type = "button";
    colL.title = lab.players_hide || "hide";
    colL.setAttribute("aria-label", colL.title);
    colL.addEventListener("click", () => { S.panel = false; applyPanel(); layout(); draw(); });
    head.appendChild(colL);
    left.appendChild(head);
    let lastSide = null;
    P.forEach((p, pi) => {
      if (p.side !== lastSide) {
        // a group header per side; the players inside are sorted by nickname
        lastSide = p.side;
        const th2 = el("div", "rr-th " + sideCls(p.side));
        const cntEl = el("span", null, (p.side === "CT" ? "CT" : p.side === "T" ? "T" : "?") + " \u00B7 0");
        const cashEl = el("span", "rr-th-m", "");
        th2.append(cntEl, cashEl);
        sideHeads[p.side] = { l: cntEl, m: cashEl };
        left.appendChild(th2);
      }
      // a spectator-style card, no photo, three lines:
      //   eye | name + status .......... weapon armor HP
      //       | kills deaths ............ utility kit bomb
      //       | money speed
      const row = el("div", "rr-prow " + sideCls(p.side));
      const eye = el("button", "rr-eye");
      eye.type = "button";
      eye.addEventListener("click", () => {
        if (S.hidden[p.sid]) delete S.hidden[p.sid]; else S.hidden[p.sid] = true;
        applyHidden(); draw();
      });
      const nameCell = el("span", "rr-pn");
      const nm = el("span", "rr-pname", p.name);
      nm.title = p.name;
      const ind = el("span", "rr-pind");
      nameCell.append(nm, ind);
      const kdLine = el("span", "rr-pkd");
      const kk = kdCell(ICO_KILLS, "rr-pkk", lab.pt_kills || "Kills");
      const dk = kdCell(ICO_SKULL, "rr-pdk", lab.pt_deaths || "Deaths", "skull");
      kdLine.append(kk.w, dk.w);
      const wc = el("span", "rr-pw");
      const ac = el("span", "rr-pa");
      const hc = el("span", "rr-php", "");
      const l1 = el("div", "rr-pl1");
      l1.append(el("span", "rr-pl-l", null), el("span", "rr-pl-r"));
      l1.firstChild.append(nameCell);
      // armor sits right before the HP number
      l1.lastChild.append(wc, ac, hc);
      const util = el("span", "rr-putil");
      const bc = el("span", "rr-pbm", "");
      const l2 = el("div", "rr-pl2");
      l2.append(el("span", "rr-pl-l", null), el("span", "rr-pl-r"));
      l2.firstChild.append(kdLine);
      // utility, then the kit and the bomb on the same spot at the right end
      l2.lastChild.append(util, bc);
      const mc = el("span", "rr-pm", "");
      const sp = el("span", "rr-psp");
      const spv = el("b", "rr-pspv", "");
      sp.append(spv);
      const l3 = el("div", "rr-pl3");
      l3.append(el("span", "rr-pl-l", null), el("span", "rr-pl-r"));
      l3.firstChild.append(mc, sp);
      const lines = el("div", "rr-pl");
      lines.append(l1, l2, l3);
      // the HP as the card's background: the team colour fills the card from the left
      // up to hp% (a soft tint, the text stays readable)
      const bar = el("span", "rr-pbar");
      const barf = el("i");
      bar.appendChild(barf);
      row.append(bar, eye, lines);
      left.appendChild(row);
      lrows.push({ p: p, pi: pi, row: row, eye: eye, wc: wc, ac: ac, hc: hc, mc: mc, ind: ind,
        util: util, spv: spv, bc: bc, kn: kk.n, dn: dk.n, bar: bar, barf: barf,
        wk: null, ak: null, us: null, is: null, hk: null, kv: -1, dv: -1 });
    });
    applyHidden();
  }
  function applyHidden() {
    for (const lr of lrows) {
      const on = visible(lr.p);
      lr.row.classList.toggle("off", !on);
      setEye(lr.eye, on, (on ? (lab.eye_hide || "Hide from the map") : (lab.eye_show || "Show on the map")) + ": " + lr.p.name);
    }
    if (masterEye) {
      const allOn = P.every(visible);
      setEye(masterEye, allOn, allOn ? (lab.eye_none || "Hide all") : (lab.eye_all || "Show all"));
    }
  }
  function updateLeft() {
    const sums = { CT: { mo: 0, n: 0, mon: false }, T: { mo: 0, n: 0, mon: false } };
    const aliveBySide = {};
    // match kills / deaths LIVE: what was before the round + the round's own
    // events up to the playhead (an enemy kill counts, a team kill / suicide not)
    const kdLive = P.map((p) => [(p.kd || [0, 0])[0], (p.kd || [0, 0])[1]]);
    for (const k of d.kills) {
      if (k[0] > t) continue;
      const ki = k[1], vi = k[2];
      if (vi >= 0 && kdLive[vi]) kdLive[vi][1] += 1;
      if (ki >= 0 && vi >= 0 && ki !== vi && kdLive[ki] && P[ki].side !== P[vi].side) kdLive[ki][0] += 1;
    }
    for (const lr of lrows) {
      const p = lr.p;
      const s = state(p, t);
      const alive = s ? s.al : false;
      aliveBySide[p.side] = (aliveBySide[p.side] || 0) + (alive ? 1 : 0);
      lr.row.classList.toggle("dead", !alive);
      const kd = kdLive[lr.pi];
      if (lr.kv !== kd[0]) { lr.kv = kd[0]; lr.kn.textContent = String(kd[0]); }
      if (lr.dv !== kd[1]) { lr.dv = kd[1]; lr.dn.textContent = String(kd[1]); }
      const hp = s && s.hp != null ? s.hp : null;
      // weapon in hands: icon with the localized tooltip, plain text without icons
      const w = alive ? weaponAt(p, t) : "";
      if (lr.wk !== w) {
        lr.wk = w; lr.wc.replaceChildren();
        if (w) {
          const wIco = iconEl(w, wl(w), 16);
          if (wIco) lr.wc.appendChild(wIco);
          else lr.wc.appendChild(el("span", "rr-wtxt", wl(w) || w));
        }
        lr.wc.title = w ? wl(w) : "";
      }
      // armor (+ helmet): the silhouette only - no number, and nothing at
      // zero armor (an empty armor_none icon just adds noise)
      const ar = runAt(p.ar, t);
      const armor = ar ? ar[1] : 0, helm = ar ? ar[2] : 0;
      const ak = ar && alive && armor > 0 ? armorKey(armor, helm) + ":" + armor : "";
      if (lr.ak !== ak) {
        lr.ak = ak; lr.ac.replaceChildren(); lr.ac.title = "";
        if (ak) {
          const title = (lab.pt_armor || "armor") + " " + armor + (helm ? " + " + (lab.pt_helmet || "helmet") : "");
          const aIco = iconEl(armorKey(armor, helm), title, 15);
          lr.ac.title = title;
          if (aIco) lr.ac.appendChild(aIco);
        }
      }
      // hp: the big number; a dead player gets the skull instead
      if (!alive) {
        if (lr.hk !== "dead") {
          lr.hk = "dead";
          // the skull of the icon set; the drawn one when the set is not installed
          lr.hc.replaceChildren(iconEl("skull", lab.dead || "dead", 16) || svgSpan(ICO_SKULL, "rr-pdead"));
          lr.hc.title = lab.dead || "dead";
        }
        lr.hc.className = "rr-php dead";
        lr.barf.style.width = "0%";
      } else {
        lr.hk = "hp";
        lr.hc.textContent = hp == null ? "" : String(hp);
        lr.hc.className = "rr-php";
        lr.hc.title = (lab.pt_hp || "hp") + " " + (hp == null ? "" : hp);
        lr.barf.style.width = (hp == null ? 0 : Math.max(0, Math.min(100, hp))) + "%";
      }
      // money (the gear value is not shown on the card)
      const mo = valAt(p.mo, t, null);
      lr.mc.textContent = mo == null ? "" : money(mo);
      lr.mc.title = mo == null ? "" : (lab.pt_money_t || "money") + " " + money(mo);
      // utility: grenades and the kit; status by the name, the bomb by the kit
      const inv = alive ? ((runAt(p.inv, t) || [0, []])[1]) : [];
      const kit = alive && valAt(p.kit, t, 0) === 1;
      const defusing = alive && valAt(p.dg, t, 0) === 1;
      const us = inv.filter((k) => GRENADES.indexOf(k) >= 0).sort().join(",") + "|" + (kit ? "k" : "");
      if (lr.us !== us) {
        lr.us = us; lr.util.replaceChildren();
        const cnt = countKeys(inv);
        for (const g of GRENADES) {
          for (let n = 0; n < (cnt[g] || 0); n++) {
            const gIco = iconEl(g, wl(g), 15);
            if (gIco) lr.util.appendChild(gIco);
            else lr.util.append(el("span", "rr-wtxt", wl(g) || g));
          }
        }
        if (kit) {
          const kIco = iconEl("defuse", lab.pt_kit || "defuse kit", 15);
          if (kIco) lr.util.appendChild(kIco);
        }
      }
      const is = (inv.indexOf("c4") >= 0 ? "c" : "") + (defusing ? "d" : "") + (s && s.fl > 0.2 ? "b" : "");
      if (lr.is !== is) {
        lr.is = is; lr.ind.replaceChildren(); lr.bc.replaceChildren();
        // the bomb sits right of the kit in the utility row; defusing /
        // flashed stay by the name
        if (is.indexOf("c") >= 0) { const ic = iconEl("c4", lab.pt_bomb || "carries the bomb", 14, C.warn); if (ic) lr.bc.appendChild(ic); }
        if (is.indexOf("d") >= 0) { const ic = iconEl("defuse", lab.pt_defusing || "defusing", 14, C.acc); if (ic) lr.ind.appendChild(ic); }
        if (is.indexOf("b") >= 0) { const ic = iconEl("blind", lab.pt_blind || "flashed", 14); if (ic) lr.ind.appendChild(ic); }
      }
      // live speed: just the digits and the unit, no label
      const v = alive ? speedAt(p, t) : null;
      lr.spv.textContent = v == null ? "" : Math.round(v) + " " + (lab.u_s || "u/s");
      const sm = sums[p.side];
      if (sm) { sm.n += 1; if (mo != null) { sm.mo += mo; sm.mon = true; } }
    }
    for (const side in sideHeads) {
      const h = sideHeads[side];
      const sm = sums[side];
      const mon = sm && sm.mon;
      h.l.textContent = (side === "CT" ? "CT" : side === "T" ? "T" : side) + " \u00B7 " + (aliveBySide[side] || 0);
      h.m.textContent = mon ? money(sm.mo) : "";
      h.m.title = mon ? (lab.pt_money_t || "money") + " " + money(sm.mo) : "";
    }
  }
  function applyPanel() {
    grid.classList.toggle("nolf", !S.panel);
    grid.classList.toggle("nord", !S.dpanel);
    plBtn.classList.toggle("on", !!S.panel);
    dpBtn.classList.toggle("on", !!S.dpanel);
    plBtn.title = S.panel ? (lab.players_hide || "hide") : (lab.players_show || "show");
    plBtn.setAttribute("aria-label", plBtn.title);
    dpBtn.title = S.dpanel ? (lab.duels_hide || "hide") : (lab.duels_show || "show");
    dpBtn.setAttribute("aria-label", dpBtn.title);
  }

  // ---------- key panels (one per player; moved into the duel panel) ----------------------------------
  const keyPanels = {};  // player index -> { el, boxes, p }
  if (d.keys && d.keys.length) {
    P.forEach((p, pi) => {
      if (!p.kb) return;
      const kp = el("div", "rr-kp");
      const boxes = d.keys.map((k) => {
        const b = el("span", "rr-k", lab["k_" + k] || k);
        b.title = k;
        kp.appendChild(b);
        return b;
      });
      keyPanels[pi] = { el: kp, boxes: boxes, p: p };
    });
  }

  // ---------- the duel panel: live aim numbers + chart + keys of BOTH duelists ----------------------------
  let dp = null;  // { sel, cols: [...] } of the selected duel
  const sampleIdx = (tick) => Math.round((tick - d.t0) / d.step);
  function telAt(tel, who, key, tick) {
    if (!tel) return null;
    const arr = tel[who] && tel[who][key];
    if (!arr) return null;
    const j = sampleIdx(tick) - tel.i0;
    return (j >= 0 && j < arr.length) ? arr[j] : null;
  }
  const fmt1 = (v) => (v == null ? "\u2014" : String(v));
  function buildDuelPanel() {
    duelWrap.replaceChildren();
    dp = null;
    const z = duelAt(sel);
    if (!z) { duelWrap.classList.add("hid"); return; }
    dp = { sel: sel, cols: [] };
    const tel = z.tel || null;
    const sides = [
      { who: "k", pi: z.ki, name: z.kn, role: lab.role_killer || "killer", sp: z.ksp, er: z.ker, sh: z.ksh, ft: z.kft },
      { who: "v", pi: z.vi, name: z.vn, role: lab.role_victim || "victim", sp: z.vsp, er: z.ver, sh: z.vsh, ft: z.vft },
    ];
    // one speed scale for both charts, so the two duelists are comparable
    let vmax = Math.max(250, THR != null ? THR * 3 : 0);
    if (tel) for (const w of ["k", "v"]) for (const v of tel[w].sp) if (v != null && v > vmax) vmax = v;
    vmax = Math.ceil(vmax / 50) * 50;
    const head = el("div", "rr-dph");
    head.appendChild(el("span", "rr-dpt", lab.duel_panel || "Duel aim"));
    if (z.first) {
      const fl = { killer: lab.first_killer, victim: lab.first_victim, simultaneous: lab.first_sim };
      if (fl[z.first]) head.appendChild(el("span", "rr-dpf", fl[z.first]));
    }
    if (z.dist != null) head.appendChild(el("span", "rr-dpf", (lab.tel_dist || "distance") + " " + z.dist + " u"));
    // the duel's weapon: the silhouette between the duelists' names, its
    // localized name as the tooltip (text only when icons are missing)
    const wh = el("span", "rr-dpw");
    const wIco = iconEl(z.w, wl(z.w), 12);
    if (wIco) wh.appendChild(wIco);
    else if (z.w) wh.appendChild(document.createTextNode(wl(z.w) || z.w));
    wh.title = wl(z.w) || z.w || "";
    if (z.w) head.appendChild(wh);
    duelWrap.appendChild(head);
    const cols = el("div", "rr-dpc");
    duelWrap.appendChild(cols);
    for (const sd of sides) {
      const p = sd.pi >= 0 ? P[sd.pi] : null;
      const col = el("div", "rr-dcol");
      const nm = el("div", "rr-dnm");
      const nspan = el("b", null, sd.name);
      nspan.style.color = sideCol(p ? p.side : "?");
      nm.append(nspan, el("span", "rr-drole", sd.role));
      col.appendChild(nm);
      const stats = el("div", "rr-dst");
      const cell = (key, label) => {
        const c = el("div", "rr-dcell");
        const v = el("b", "rr-dv", "\u2014");
        c.append(el("span", "rr-dl", label), v);
        stats.appendChild(c);
        return v;
      };
      const live = {
        sp: cell("sp", lab.tel_speed || "speed"),
        tn: cell("tn", lab.tel_turn || "turn"),
        er: cell("er", lab.tel_err || "aim error"),
      };
      col.appendChild(stats);
      // what the duels table measured at THIS player's first shot
      const fb = [];
      if (sd.ft != null) fb.push((lab.tel_first || "first shot") + " " + fmtT((sd.ft - d.t0) / d.tr));
      if (sd.sp != null) fb.push(sd.sp + " " + (lab.u_s || "u/s") + (THR != null ? " (" + (sd.sp > THR ? (lab.tel_moving || "moving") : (lab.tel_standing || "standing")) + ")" : ""));
      if (sd.er != null) fb.push(sd.er + "\u00B0");
      if (sd.sh != null) fb.push((lab.tel_shots || "shots") + ": " + sd.sh);
      if (fb.length) col.appendChild(el("div", "rr-dfs", fb.join(" \u00B7 ")));
      let cv = null, cx = null;
      if (tel) {
        cv = document.createElement("canvas");
        cv.className = "rr-dcv";
        col.appendChild(cv);
        cx = cv.getContext("2d");
        const seekFrom = (e) => {
          const r = cv.getBoundingClientRect();
          const f = Math.max(0, Math.min(1, (e.clientX - r.left) / Math.max(1, r.width)));
          seek(Math.round(z.a + f * (z.b - z.a)));
        };
        let down = false;
        cv.addEventListener("pointerdown", (e) => {
          e.preventDefault(); down = true; if (playing) setPlaying(false);
          try { cv.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
          seekFrom(e);
        });
        cv.addEventListener("pointermove", (e) => { if (down) seekFrom(e); });
        cv.addEventListener("pointerup", () => { down = false; });
        cv.addEventListener("pointercancel", () => { down = false; });
      }
      const kp = sd.pi >= 0 ? keyPanels[sd.pi] : null;
      if (kp) { kp.el.classList.add("on"); col.appendChild(kp.el); }
      cols.appendChild(col);
      dp.cols.push({ side: sd, live: live, cv: cv, cx: cx, kp: kp, vmax: vmax, p: p });
    }
    // legend of the chart lines
    if (tel) {
      const lg = el("div", "rr-dleg");
      const mk = (cls, col, text) => { const s = el("span"); const i = el("i", cls); i.style.background = col; s.append(i, " " + text); return s; };
      lg.append(mk("rr-legline", C.acc, lab.tel_leg_speed || "speed"),
        mk("rr-legline", C.warn, lab.tel_leg_err || "aim error"));
      lg.append(mk("rr-legline", C.text, lab.tel_leg_shot || "shot"));
      if (THR != null) lg.append(mk("rr-legdash", C.text2, (lab.tel_leg_thr || "moving threshold") + " " + Math.round(THR)));
      duelWrap.appendChild(lg);
    }
    duelWrap.classList.remove("hid");
    sizeCharts();
  }
  function sizeCharts() {
    if (!dp) return;
    const k = DPR();
    for (const c of dp.cols) {
      if (!c.cv) continue;
      const w = Math.max(160, Math.round(c.cv.getBoundingClientRect().width || 300));
      c.w = w; c.h = 92;
      c.cv.width = Math.round(w * k); c.cv.height = Math.round(c.h * k);
      c.cv.style.height = c.h + "px";
      c.cx.setTransform(k, 0, 0, k, 0, 0);
    }
  }
  const ERR_MAX = 30;
  function drawDuelPanel() {
    if (!dp) return;
    const z = duelAt(dp.sel);
    if (!z) return;
    const tel = z.tel || null;
    for (const c of dp.cols) {
      const who = c.side.who;
      // live numbers at the playhead (from the telemetry of the duel)
      const sp = telAt(tel, who, "sp", t), tn = telAt(tel, who, "tn", t), er = telAt(tel, who, "er", t);
      c.live.sp.textContent = sp == null ? "\u2014" : sp + " " + (lab.u_s || "u/s");
      c.live.tn.textContent = tn == null ? "\u2014" : tn + "\u00B0/s";
      c.live.er.textContent = er == null ? "\u2014" : er + "\u00B0";
      if (!c.cx || !tel) continue;
      const cx = c.cx, W2 = c.w, H2 = c.h, padB = 12, hh = H2 - padB;
      cx.clearRect(0, 0, W2, H2);
      cx.fillStyle = rgba(C.text2, 0.07);
      cx.fillRect(0, 0, W2, hh);
      const xOf = (tick) => ((tick - z.a) / Math.max(1, z.b - z.a)) * (W2 - 2) + 1;
      const ySp = (v) => hh - Math.min(1, v / c.vmax) * (hh - 3) - 1;
      const yEr = (v) => hh - Math.min(1, v / ERR_MAX) * (hh - 3) - 1;
      const line = (arr, yf, col) => {
        cx.strokeStyle = col; cx.lineWidth = 1.6; cx.beginPath();
        let pen = false;
        arr.forEach((v, j) => {
          if (v == null) { pen = false; return; }
          const tick = d.t0 + (tel.i0 + j) * d.step;
          const x = xOf(tick), y = yf(v);
          if (!pen) { cx.moveTo(x, y); pen = true; } else cx.lineTo(x, y);
        });
        cx.stroke();
      };
      line(tel[who].er, yEr, C.warn);
      line(tel[who].sp, ySp, C.acc);
      // the moving threshold: shots above it are the moving shots
      if (THR != null && THR <= c.vmax) {
        cx.setLineDash([4, 3]); cx.strokeStyle = rgba(C.text2, 0.8); cx.lineWidth = 1;
        cx.beginPath(); cx.moveTo(0, ySp(THR)); cx.lineTo(W2, ySp(THR)); cx.stroke();
        cx.setLineDash([]);
      }
      // this player's shots (ticks along the bottom), the first shot taller
      if (c.p) {
        const pi = P.indexOf(c.p);
        cx.strokeStyle = C.text; cx.lineWidth = 1;
        for (const sh of d.shots) {
          if (sh[1] !== pi || sh[0] < z.a || sh[0] > z.b) continue;
          const x = Math.round(xOf(sh[0])) + 0.5;
          cx.beginPath(); cx.moveTo(x, hh); cx.lineTo(x, hh + 7); cx.stroke();
        }
      }
      if (c.side.ft != null && c.side.ft >= z.a && c.side.ft <= z.b) {
        const x = Math.round(xOf(c.side.ft)) + 0.5;
        cx.strokeStyle = C.good; cx.lineWidth = 1.5;
        cx.beginPath(); cx.moveTo(x, 0); cx.lineTo(x, hh + 10); cx.stroke();
      }
      // the kill
      const xk = Math.round(xOf(z.tk)) + 0.5;
      cx.strokeStyle = C.dead; cx.lineWidth = 1.5;
      cx.beginPath(); cx.moveTo(xk, 0); cx.lineTo(xk, hh); cx.stroke();
      // playhead
      const xp = xOf(t);
      cx.strokeStyle = C.acc; cx.lineWidth = 2;
      cx.beginPath(); cx.moveTo(xp, 0); cx.lineTo(xp, H2); cx.stroke();
      // axis hints
      cx.font = "9px " + FF; cx.fillStyle = C.text2; cx.textAlign = "left";
      cx.fillText(Math.round(c.vmax) + " " + (lab.u_s || "u/s"), 3, 9);
      cx.textAlign = "right";
      cx.fillText(ERR_MAX + "\u00B0", W2 - 3, 9);
    }
    for (const c of dp.cols) {
      if (!c.kp) continue;
      const j = sampleIdx(t);
      const mask = c.kp.p.kb ? (c.kp.p.kb[j] || 0) : 0;
      c.kp.boxes.forEach((b, bit) => b.classList.toggle("on", ((mask >> bit) & 1) === 1));
    }
  }

  // ---------- radar geometry + zoom / pan --------------------------------------------------------------------
  const DPR = () => Math.max(1, window.devicePixelRatio || 1);
  const ctx = cvs.getContext("2d");
  const imgs = [];
  const twoPanels = !!(d.radar && d.radar.lo);
  const loadImg = (uri, i) => {
    const im = new Image();
    imgs[i] = im;
    im.addEventListener("load", () => draw());
    if (uri) im.src = uri;
  };
  if (d.radar) { loadImg(d.radar.png, 0); if (twoPanels) loadImg(d.radar.lo, 1); }
  let W = 600, H = 420, panels = [], lastW = 0, lastBW = 0;

  function layout() {
    // the single-column layout depends on the WIDTH OF THE WHOLE PLAYER, not
    // on the canvas (the canvas shrinks and grows with the columns: measuring
    // it would flip the layout back and forth at the threshold)
    const bw = box.getBoundingClientRect().width;
    lastBW = bw;
    box.classList.toggle("narrow", bw > 0 && bw < 780);
    const r = wrap.getBoundingClientRect();
    W = Math.max(240, r.width);
    lastW = W;
    H = Math.max(280, Math.min(560, Math.round(W * 0.62)));
    const k = DPR();
    cvs.width = Math.round(W * k); cvs.height = Math.round(H * k);
    cvs.style.height = H + "px";
    ctx.setTransform(k, 0, 0, k, 0, 0);
    // zoom/pan survive a relayout AND a round switch: only the base placement is recomputed
    const mk = (i, ox, oy, fit) => {
      const v = S.view[i] || (S.view[i] = { z: 1, dx: 0, dy: 0 });
      panels[i] = { ox: ox, oy: oy, fit: fit, v: v };
    };
    if (d.radar) {
      const n = twoPanels ? 2 : 1;
      const pw = W / n;
      for (let i = 0; i < n; i++) {
        const fit = Math.min(pw, H) / d.radar.size;
        mk(i, i * pw + (pw - d.radar.size * fit) / 2, (H - d.radar.size * fit) / 2, fit);
      }
    } else if (d.gridb) {
      const g = d.gridb;
      const fit = Math.min(W / (g.maxx - g.minx), H / (g.maxy - g.miny));
      mk(0, (W - (g.maxx - g.minx) * fit) / 2, (H - (g.maxy - g.miny) * fit) / 2, fit);
    }
    sizeCharts();
  }
  function toPx(level, x, y) {
    const p = panels[level] || panels[0] || { ox: 0, oy: 0, fit: 1, v: { z: 1, dx: 0, dy: 0 } };
    if (d.radar) {
      const kk = d.radar.size / 1024;
      const u = (x - d.radar.pos_x) / d.radar.scale * kk;
      const v = (d.radar.pos_y - y) / d.radar.scale * kk;
      return [p.ox + p.v.dx + u * p.fit * p.v.z, p.oy + p.v.dy + v * p.fit * p.v.z];
    }
    const g = d.gridb;
    return [p.ox + p.v.dx + (x - g.minx) * p.fit * p.v.z, p.oy + p.v.dy + (g.maxy - y) * p.fit * p.v.z];
  }
  function resetView() {
    for (const p of panels) { if (p) { p.v.z = 1; p.v.dx = 0; p.v.dy = 0; } }
    draw();
  }
  const panelAt = (mx) => (twoPanels ? (mx < W / 2 ? 0 : 1) : 0);

  // wheel = zoom (around the cursor), LMB drag = pan, dblclick = reset
  cvs.addEventListener("wheel", (e) => {
    e.preventDefault();
    const p = panels[panelAt(e.offsetX)];
    if (!p) return;
    const z2 = Math.max(0.6, Math.min(14, p.v.z * (e.deltaY < 0 ? 1.18 : 1 / 1.18)));
    const kk = z2 / p.v.z;
    const ax = e.offsetX - p.ox, ay = e.offsetY - p.oy;
    p.v.dx = ax - (ax - p.v.dx) * kk;
    p.v.dy = ay - (ay - p.v.dy) * kk;
    p.v.z = z2;
    draw();
  }, { passive: false });
  let pan = null;
  cvs.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    pan = { x: e.offsetX, y: e.offsetY, i: panelAt(e.offsetX) };
    try { cvs.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    wrap.classList.add("dragging");
  });
  cvs.addEventListener("pointermove", (e) => {
    if (!pan) return;
    const p = panels[pan.i];
    if (p) { p.v.dx += e.offsetX - pan.x; p.v.dy += e.offsetY - pan.y; }
    pan.x = e.offsetX; pan.y = e.offsetY;
    draw();
  });
  const endPan = () => { pan = null; wrap.classList.remove("dragging"); };
  cvs.addEventListener("pointerup", endPan);
  cvs.addEventListener("pointercancel", endPan);
  cvs.addEventListener("dblclick", resetView);

  // ---------- the canvas hover tooltip: name / weapon / hp of a plate ----
  // (a canvas has no title attribute: the hit-test draws a floating tip
  // that names the weapon icon over the player's plate)
  const tip = el("div", "rr-tip");
  tip.hidden = true;
  wrap.appendChild(tip);
  const plateAt = (mx, my) => {
    // alive players were pushed last: they win the hit-test on an overlap
    for (let i = plates.length - 1; i >= 0; i--) {
      const b = plates[i];
      if (Math.abs(mx - b.x) <= b.w / 2 + 2 && my >= b.y - 13 && my <= b.y + b.h + 2) return b;
    }
    return null;
  };
  let tipFor = "";
  cvs.addEventListener("pointermove", (e) => {
    const b = plateAt(e.offsetX, e.offsetY);
    if (!b) {
      if (!tip.hidden) { tip.hidden = true; tip.textContent = ""; }
      return;
    }
    const lines = [b.p.name];
    if (!b.al) lines.push(lab.dead || "dead");
    else {
      if (b.w) lines.push(wl(b.w) || b.w);
      if (b.hp != null) lines.push("HP " + b.hp);
    }
    const txt = lines.join(" \u00B7 ");
    if (txt !== tipFor) {
      tipFor = txt;
      tip.textContent = txt;
      tip.setAttribute("aria-label", txt);
    }
    tip.hidden = false;
    const r = wrap.getBoundingClientRect();
    tip.style.left = Math.min(e.offsetX + 12, Math.max(4, r.width - 170)) + "px";
    tip.style.top = Math.max(2, e.offsetY - 26) + "px";
  });
  cvs.addEventListener("pointerleave", () => { tip.hidden = true; tipFor = ""; });

  // ---------- interpolated player state -------------------------------------------------------------------------
  function state(p, tick) {
    const n = p.x.length;
    if (!n) return null;
    const f = (tick - d.t0) / d.step;
    let i0 = Math.floor(f);
    const i1 = Math.max(0, Math.min(i0 + 1, n - 1));
    i0 = Math.max(0, Math.min(i0, n - 1));
    const fr = Math.max(0, Math.min(1, f - i0));
    const num = (a, b) => (a == null || b == null ? (a == null ? b : a) : a + (b - a) * fr);
    const yA = p.yaw[i0], yB = p.yaw[i1];
    let yaw = num(yA, yB);
    if (yA != null && yB != null) yaw = yA + (((yB - yA + 540) % 360) - 180) * fr;  // shortest arc
    const al = p.al ? !!p.al[i0] : true;
    return {
      x: num(p.x[i0], p.x[i1]), y: num(p.y[i0], p.y[i1]), yaw: yaw,
      hp: p.hp ? p.hp[i0] : null, al: al,
      fl: p.fl ? (p.fl[i0] || 0) : 0,
      lvl: p.lvl ? (p.lvl[i0] ? 1 : 0) : 0,
    };
  }
  function weaponAt(p, tick) {
    if (!p.w || !p.w.length) return "";
    let w = p.w[0][1];
    for (const ch of p.w) { if (ch[0] <= tick) w = ch[1]; else break; }
    return w || "";
  }
  // horizontal speed (u/s) from the positions around the playhead: a delta
  // over +-2 samples (~0.2 s) smooths the one-unit rounding of the positions,
  // and the shown value snaps to 5 u/s - the number holds still instead of
  // flickering with every sample. null for a dead player, a teleport or a
  // sample outside the round
  function speedAt(p, tick) {
    const n = p.x.length;
    if (n < 3) return null;
    const i = Math.max(0, Math.min(n - 1, Math.round((tick - d.t0) / d.step)));
    const a = Math.max(0, i - 2), b = Math.min(n - 1, i + 2);
    if (b === a || p.x[a] == null || p.x[b] == null || p.y[a] == null || p.y[b] == null) return null;
    if (p.al && !p.al[i]) return null;
    const v = Math.hypot(p.x[b] - p.x[a], p.y[b] - p.y[a]) / (((b - a) * d.step) / d.tr);
    return v > 600 ? null : Math.round(v / 5) * 5;
  }

  // ---------- drawing --------------------------------------------------------------------------------------------------
  const rgba = (hex, a) => {
    const h = (hex || "").replace("#", "");
    if (h.length !== 6) return "transparent";
    const n = parseInt(h, 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  };
  const hpCol = (hp) => (hp < 30 ? C.bad : hp < 70 ? (C.warn || C.bad) : C.good);

  // ---------- weapon icons on the canvas --------------------------------------------------
  // The silhouettes are TINTED through the alpha mask, never through the
  // file's own fill (grenades carry none and would stay black): the icon is
  // drawn into a throwaway canvas, then `source-in` with the team colour
  // keeps exactly the painted pixels and recolours them.
  const iconImgs = {};   // key -> HTMLImageElement (loading promise cached)
  const iconTinted = {}; // "key|color" -> canvas of the tinted silhouette
  const loadIconImg = (key) => {
    const m = IC[key];
    if (!m) return Promise.resolve(null);
    if (iconImgs[key]) return iconImgs[key];
    iconImgs[key] = new Promise((res) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => res(null);
      im.src = m[0];
    });
    return iconImgs[key];
  };
  function tintedIcon(key, color, h) {
    if (!IC[key] || !color) return null;
    const ck = key + "|" + color + "|" + h;
    if (iconTinted[ck]) return iconTinted[ck];
    const m = IC[key];
    const ar = (m[1] && m[2]) ? m[1] / m[2] : 1;
    const w = Math.max(1, Math.round(h * ar));
    const cv = document.createElement("canvas");
    cv.width = w; cv.height = h;
    return (iconTinted[ck] = { c: cv, x: cv.getContext("2d"), w: w, h: h, ready: false, im: null });
  }
  // the icon cache is filled while the radar images load; draw() runs again
  // on every Image.onload, so the icons pop in with the same mechanism
  let iconsLoading = false;
  function loadIcons() {
    if (iconsLoading) return;
    iconsLoading = true;
    Object.keys(IC).forEach((key) => { loadIconImg(key); });
  }
  loadIcons();
  function drawIcon(key, x, y, color, h) {
    // draws the silhouette centered on (x, y); returns true when painted
    const t = tintedIcon(key, color, h);
    if (!t) return false;
    if (!t.ready) {
      // not tinted yet: tint as soon as the source image has loaded
      if (!t.im) {
        loadIconImg(key).then((im) => {
          if (!im) { t.ready = true; return; }
          t.im = im;
          t.x.drawImage(im, 0, 0, t.w, t.h);
          t.x.globalCompositeOperation = "source-in";
          t.x.fillStyle = color;
          t.x.fillRect(0, 0, t.w, t.h);
          t.x.globalCompositeOperation = "source-over";
          t.ready = true;
          draw();
        });
      }
      return false;
    }
    if (!t.im) return false;
    ctx.drawImage(t.c, Math.round(x - t.w / 2), Math.round(y - t.h / 2));
    return true;
  }

  function drawBg() {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = C.bg || "transparent";
    ctx.fillRect(0, 0, W, H);
    const n = twoPanels ? 2 : 1;
    for (let i = 0; i < n; i++) {
      const p = panels[i];
      if (!p) continue;
      if (d.radar && imgs[i] && imgs[i].complete && imgs[i].naturalWidth) {
        const sz = d.radar.size * p.fit * p.v.z;
        ctx.drawImage(imgs[i], p.ox + p.v.dx, p.oy + p.v.dy, sz, sz);
      } else if (!d.radar && d.gridb) {
        ctx.strokeStyle = C.grid;
        ctx.lineWidth = 1;
        const g = d.gridb, stepU = 500;
        for (let gx = Math.ceil(g.minx / stepU) * stepU; gx <= g.maxx; gx += stepU) {
          const a = toPx(0, gx, g.miny), b = toPx(0, gx, g.maxy);
          ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        }
        for (let gy = Math.ceil(g.miny / stepU) * stepU; gy <= g.maxy; gy += stepU) {
          const a = toPx(0, g.minx, gy), b = toPx(0, g.maxx, gy);
          ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        }
      }
      if (twoPanels && lab["lvl" + i]) {
        ctx.fillStyle = C.text;
        ctx.font = "600 11px " + FF;
        ctx.textAlign = "left";
        ctx.fillText(lab["lvl" + i], p.ox + p.v.dx + 8, p.oy + p.v.dy + 16);
      }
    }
  }

  // text with a thin halo so names stay readable on top of the radar art
  function halo(text, x, y) {
    ctx.lineWidth = 3;
    ctx.strokeStyle = rgba(C.bg, 0.85);
    ctx.strokeText(text, x, y);
    ctx.fillText(text, x, y);
  }

  // a rounded rect path (the name plate of a player on the map)
  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  function drawTracers() {
    const fade = 0.22 * d.tr;
    for (const sh of d.shots) {
      const tk = sh[0], p = sh[1] >= 0 ? P[sh[1]] : null;
      if (!visible(p) || tk > t || t - tk > fade) continue;
      const s = state(p, tk);
      if (!s || s.x == null) continue;
      const a = (s.yaw == null ? 0 : s.yaw) * Math.PI / 180;
      const a0 = toPx(s.lvl, s.x, s.y);
      const b0 = toPx(s.lvl, s.x + Math.cos(a) * 700, s.y + Math.sin(a) * 700);
      ctx.globalAlpha = 0.65 * (1 - (t - tk) / fade);
      ctx.strokeStyle = sideCol(p.side);
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(a0[0], a0[1]); ctx.lineTo(b0[0], b0[1]); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  function drawPlayers() {
    const R = Math.max(5, Math.min(7.5, W / 110));  // small dots: the map stays readable
    ctx.textAlign = "center";
    plates = [];
    const z = duelAt(sel);
    const ord = [];
    P.forEach((p, pi) => {
      if (!visible(p)) return;
      const s = state(p, t);
      if (!s || s.x == null || s.y == null) return;
      ord.push({ p: p, pi: pi, s: s });
    });
    // z-order on the map: the dead sink to the bottom (the earlier the death,
    // the lower they lie); the living keep the LEFT TABLE's order - the top
    // row of the list paints last, on top
    ord.sort((A, B) => {
      const ad = !A.s.al, bd = !B.s.al;
      if (ad !== bd) return ad ? -1 : 1;
      if (ad) return deathTick[A.pi] - deathTick[B.pi];
      return B.pi - A.pi;
    });
    for (const it of ord) {
      const p = it.p, s = it.s;
      {
        const q = toPx(s.lvl, s.x, s.y), px = q[0], py = q[1];
        const col = sideCol(p.side);
        if (s.al) {
        if (s.fl > 0.2) {
          ctx.beginPath(); ctx.arc(px, py, R + 3, 0, Math.PI * 2);
          ctx.strokeStyle = rgba(C.flash, Math.min(0.9, 0.3 + s.fl / 4));
          ctx.lineWidth = 2.5; ctx.stroke();
        }
        // view direction: only the arrowhead, close to the dot (no shaft)
        const a = (s.yaw == null ? 0 : s.yaw) * Math.PI / 180;
        const dx = Math.cos(a), dy = -Math.sin(a), ox = -dy, oy = dx;
        const hx = px + dx * (R + 4), hy = py + dy * (R + 4);
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.moveTo(hx, hy);
        ctx.lineTo(hx - dx * 4 + ox * 2.8, hy - dy * 4 + oy * 2.8);
        ctx.lineTo(hx - dx * 4 - ox * 2.8, hy - dy * 4 - oy * 2.8);
        ctx.closePath(); ctx.fill();
        ctx.beginPath(); ctx.arc(px, py, R, 0, Math.PI * 2);
        ctx.fillStyle = col; ctx.fill();
        // the two duelists of the selected duel get an accent ring
        if (z && (P[z.ki] === p || P[z.vi] === p)) {
          ctx.beginPath(); ctx.arc(px, py, R + 6.5, 0, Math.PI * 2);
          ctx.strokeStyle = C.acc; ctx.lineWidth = 1.6; ctx.stroke();
        }
        // HP bar under the dot; the NAME sits above it in a dark rounded
        // plate in the team colour, and the weapon SILHOUETTE sits on top
        // of the plate (kill-feed style, tinted by the same alpha mask)
        if (s.hp != null && s.hp > 0) {
          const bw = 16, bx = px - bw / 2, by = py + R + 3;
          ctx.fillStyle = rgba(C.bg, 0.75);
          ctx.fillRect(bx - 0.5, by - 0.5, bw + 1, 4);
          ctx.fillStyle = hpCol(s.hp);
          ctx.fillRect(bx, by, bw * Math.min(1, s.hp / 100), 3);
        }
        const inv = (runAt(p.inv, t) || [0, []])[1];
        const w = weaponAt(p, t);
        ctx.font = "600 10px " + FF;
        const nmW = ctx.measureText(p.name).width;
        const plateW = nmW + 10, plateH = PLATE_H, plateY = py - R - 8 - plateH;
        // the plate: team colour at low alpha over the dark surface
        const bc = p.side === "CT" ? C.ctb : p.side === "T" ? C.ttb : C.bg;
        if (bc) {
          ctx.fillStyle = bc;
          roundRect(ctx, px - plateW / 2, plateY, plateW, plateH, 4);
          ctx.fill();
        }
        ctx.fillStyle = C.text;
        ctx.fillText(p.name, px, plateY + 10.5);
        // the weapon icon above the plate (fallback: the old text under
        // the dot, so a missing icon set changes nothing semantically)
        const ICO_H = 11;  // ~10-12 px, like the spectator HUD
        const wy = plateY - ICO_H / 2 - 1;
        // the bomb rides RIGHT of the weapon icon on the same row; without a
        // weapon icon it falls back to the status marks by the dot
        let c4x = 0;
        if (w && IC[w]) {
          const wm = IC[w], ar = wm[1] && wm[2] ? wm[1] / wm[2] : 1;
          c4x = px + ICO_H * ar / 2 + 5;
        }
        if (w && !drawIcon(w, px, wy, col, ICO_H)) {
          ctx.font = "9px " + FF; ctx.fillStyle = C.text2;
          halo(w, px, py + R + 14);
        }
        if (c4x && inv.indexOf("c4") >= 0) drawIcon("c4", c4x, wy, C.warn, 9);
        // small status marks right of the dot: the defuse kit while
        // defusing, the flash (the bomb is by the weapon when there is one)
        let sx = px + R + 4;
        const mark = (key, color, hh) => {
          if (drawIcon(key, sx, py, color || C.text, hh || 9)) sx += 11;
        };
        if (inv.indexOf("c4") >= 0 && !c4x) mark("c4", C.warn);
        if (valAt(p.dg, t, 0) === 1) mark("defuse", C.acc);
        if (s.fl > 0.2) mark("blind", C.text);
        plates.push({ x: px, y: plateY, w: plateW, h: plateH, p: p, w: w, hp: s.hp, al: true });
      } else {
        // grey skull (the icon set's silhouette, tinted like the weapons);
        // the old cross stays as the no-icon fallback
        const dc = C.text2 || C.dead;
        if (!drawIcon("skull", px, py, dc, Math.round(R * 2.1))) {
          ctx.strokeStyle = dc; ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(px - R * 0.8, py - R * 0.8); ctx.lineTo(px + R * 0.8, py + R * 0.8);
          ctx.moveTo(px + R * 0.8, py - R * 0.8); ctx.lineTo(px - R * 0.8, py + R * 0.8);
          ctx.stroke();
        }
        ctx.font = "600 10px " + FF; ctx.fillStyle = dc;
        halo(p.name, px, py - R - 5);
        plates.push({ x: px, y: py - R - 15, w: plateWFor(p), h: PLATE_H, p: p, w: "", hp: s.hp, al: false });
      }
      }
    }
  }
  // the name plates of the current frame, for the canvas hover tooltip
  // (the canvas has no DOM title: the hit-test draws its own floating tip)
  let plates = [];
  const PLATE_H = 14;
  // the first tick each player dies (Infinity = alive the whole round,
  // -Infinity = dead from the start): the stacking order of the corpses
  const deathTick = P.map((p) => {
    const al = p.al;
    if (!al || !al.length) return Infinity;
    for (let i = 1; i < al.length; i++) if (al[i - 1] && !al[i]) return d.t0 + i * d.step;
    return al[0] ? Infinity : -Infinity;
  });
  function plateWFor(p) {
    ctx.font = "600 10px " + FF;
    return ctx.measureText(p.name).width + 10;
  }

  function drawImpacts() {
    const fadeHit = 0.45 * d.tr, fadeKill = 1.2 * d.tr;
    for (const h of d.hits) {
      const tk = h[0], v = h[2] >= 0 ? P[h[2]] : null;
      if (!visible(v) || tk > t || t - tk > fadeHit) continue;
      const s = state(v, tk);
      if (!s || s.x == null) continue;
      const q = toPx(s.lvl, s.x, s.y), px = q[0], py = q[1];
      const k = 1 - (t - tk) / fadeHit, r = 6 + 5 * k;
      ctx.globalAlpha = k; ctx.strokeStyle = C.hit; ctx.lineWidth = 2;
      ctx.beginPath();  // 4-point star
      ctx.moveTo(px, py - r); ctx.lineTo(px + r * 0.35, py - r * 0.35);
      ctx.lineTo(px + r, py); ctx.lineTo(px + r * 0.35, py + r * 0.35);
      ctx.lineTo(px, py + r); ctx.lineTo(px - r * 0.35, py + r * 0.35);
      ctx.lineTo(px - r, py); ctx.lineTo(px - r * 0.35, py - r * 0.35);
      ctx.closePath(); ctx.stroke();
    }
    for (const k of d.kills) {
      const tk = k[0], v = k[2] >= 0 ? P[k[2]] : null;
      if (!visible(v) || tk > t || t - tk > fadeKill) continue;
      const s = state(v, tk);
      if (!s || s.x == null) continue;
      const q = toPx(s.lvl, s.x, s.y), px = q[0], py = q[1];
      ctx.globalAlpha = 1 - (t - tk) / fadeKill;
      ctx.strokeStyle = C.dead; ctx.lineWidth = 2.5;
      const r = 9;
      ctx.beginPath();
      ctx.moveTo(px - r, py - r); ctx.lineTo(px + r, py + r);
      ctx.moveTo(px + r, py - r); ctx.lineTo(px - r, py + r);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  // ---------- legend + howto ------------------------------------------------------------------------------------------------
  const leg = el("div", "rr-legend");
  const mark = (cls, col, text) => {
    const s = el("span"); const i = el("i", cls);
    i.style.background = col; s.append(i, " " + text);
    return s;
  };
  const deadLeg = el("span");
  deadLeg.append(iconEl("skull", lab.dead || "dead", 10) || document.createTextNode("\u2715 "), document.createTextNode(" " + (lab.dead || "dead")));
  leg.append(
    mark("rr-dot", C.ct, "CT"), mark("rr-dot", C.t, "T"),
    deadLeg,
    el("span", null, "\u2014 " + (lab.shot || "shot")),
    el("span", null, "\u2726 " + (lab.hit || "hit")),
    mark("rr-legline", C.ct, lab.leg_ct || "CT kill"),
    mark("rr-legline", C.t, lab.leg_t || "T kill"),
  );
  box.appendChild(leg);
  if (lab.howto) box.appendChild(el("div", "rr-howto", lab.howto));

  // ---------- sync / draw ------------------------------------------------------------------------------------------------------------
  let builtSel = null;
  function sync() {
    playBtn.textContent = playing ? "\u23F8" : "\u25B6";
    playBtn.title = playing ? (lab.pause || "pause") : (lab.play || "play");
    playBtn.setAttribute("aria-label", playBtn.title);
    spdBtn.textContent = spdTxt(speed) + " \u25BE";
    spdBtn.title = (lab.speed || "speed") + " " + spdTxt(speed);
    spdBtn.setAttribute("aria-label", spdBtn.title);
    spdItems.forEach((b, i) => {
      const on = SPEEDS[i] === speed;
      b.classList.toggle("on", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
    const z = duelAt(sel);
    exitBtn.style.display = z ? "" : "none";
    lockChip.style.display = z ? "" : "none";
    if (z) lockChip.textContent = (lab.locked || "") + ": " + z.kn + " \u25B8 " + z.vn;
    const ri = roundIdx();
    rPrev.disabled = ri <= 0;
    rNext.disabled = ri < 0 || ri >= d.rounds.length - 1;
    segs.forEach((s, i) => s.classList.toggle("sel", sel === i));
    cardEls.forEach((c, i) => c.classList.toggle("cur", sel === i));
    if (z) {
      const aP = pct(z.a) * 100, bP = pct(z.b) * 100;
      dimA.style.display = ""; dimB.style.display = "";
      dimA.style.left = "0%"; dimA.style.width = Math.max(0, aP) + "%";
      dimB.style.left = bP + "%"; dimB.style.width = Math.max(0, 100 - bP) + "%";
    } else {
      dimA.style.display = "none"; dimB.style.display = "none";
    }
    track.setAttribute("aria-valuemin", z ? z.a : d.t0);
    track.setAttribute("aria-valuemax", z ? z.b : d.t1);
    if (builtSel !== sel) { builtSel = sel; buildDuelPanel(); }
  }

  function draw() {
    drawBg();
    drawTracers();
    drawPlayers();
    drawImpacts();
    drawDuelPanel();
    updateLeft();
    cardEls.forEach((c, i) => c.classList.toggle("up", !!(d.duels[i] && d.duels[i].b > t)));
    timeLab.textContent = "\u23F1 " + fmtT((t - d.t0) / d.tr) + " / " + fmtT((winEnd() - d.t0) / d.tr);
    ph.style.left = (Math.max(0, Math.min(1, pct(t))) * 100) + "%";
    track.setAttribute("aria-valuenow", Math.round(t));
    S.st = { t: t, playing: playing, sel: sel, speed: speed, hidden: Object.keys(S.hidden), z: panels.map((p) => (p ? p.v.z : 1)) };
  }

  // ---------- init ------------------------------------------------------------------------------------------------------------------------
  buildOutcome();
  layout();
  applyPanel();
  buildLeft();
  buildCards();
  buildRounds();
  sync();
  draw();
  // re-layout only on a real width change: the canvas height itself depends
  // on W, and reacting to our own height edits would loop the observer
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => {
      const bw = box.getBoundingClientRect().width, w = wrap.getBoundingClientRect().width;
      if (Math.abs(bw - lastBW) < 1 && Math.abs(w - lastW) < 1) return;
      layout(); draw();
    });
    ro.observe(box);
    ro.observe(wrap);
    disposers.push(() => ro.disconnect());
  }
  return cleanup;
}
