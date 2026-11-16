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
  // glyphs of how a round ended / what the bomb did (icons never stand alone:
  // every use also carries a title / text label)
  const WHY_GLYPH = { bomb: "\u{1F4A5}", defuse: "\u2702", elim: "\u2620", time: "\u23F1", surrender: "\u2691" };
  const BOMB_GLYPH = { plant: "\u{1F4A3}", defuse: "\u2702", boom: "\u{1F4A5}" };

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
  grid.append(left, main, right);
  box.appendChild(grid);
  const outEl = el("div", "rr-out");
  const wrap = el("div", "rr-wrap");
  const cvs = document.createElement("canvas");
  const feed = el("div", "rr-feed");
  wrap.append(cvs, feed);
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

  // ---------- round result: who won and HOW -----------------------------------------------
  function buildOutcome() {
    outEl.replaceChildren();
    const o = d.out || {};
    outEl.className = "rr-out" + (o.win === "CT" ? " wct" : o.win === "T" ? " wt" : " unk");
    const head = el("div", "rr-out1");
    head.appendChild(el("span", "rr-badge", (lab.round || "R") + " " + d.round));
    if (o.win) {
      head.appendChild(el("b", "rr-owin", o.win === "CT" ? (lab.won_ct || "CT") : (lab.won_t || "T")));
      if (o.why) {
        const w = el("span", "rr-why");
        w.append(el("i", "rr-wg", WHY_GLYPH[o.why] || "\u00B7"), " " + (lab["why_" + o.why] || o.why));
        head.appendChild(w);
      }
    } else {
      head.appendChild(el("span", "rr-owin dim", lab.out_unknown || ""));
    }
    if (o.win && o.inferred) {
      const inf = el("span", "rr-inf", lab.out_inferred || "");
      inf.title = lab.out_inferred || "";
      head.appendChild(inf);
    }
    if (o.end != null) head.appendChild(el("span", "rr-outt", "\u23F1 " + fmtT((o.end - d.t0) / d.tr)));
    outEl.appendChild(head);
    // the bomb story of the round (plant / defuse / explosion with the time and the player)
    const parts = [];
    for (const b of d.bomb || []) {
      const key = b[1];
      if (!BOMB_GLYPH[key]) continue;
      // the explosion's player is only the planter: not worth repeating
      const who = key !== "boom" && b[2] >= 0 && P[b[2]] ? P[b[2]].name : "";
      parts.push(BOMB_GLYPH[key] + " " + (lab["b_" + key] || key) + " " + fmtT((b[0] - d.t0) / d.tr) + (who ? " \u00B7 " + who : ""));
    }
    if (parts.length) outEl.appendChild(el("div", "rr-out2", parts.join("   ")));
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
  const resetBtn = btn("", "\u21BB", lab.view_reset || "reset view", () => resetView());
  const timeLab = el("span", "rr-time");
  const lockChip = el("span", "rr-lock", lab.locked || "");
  ctl.append(
    playBtn,
    btn("", "\u23EE", lab.restart || "restart", () => { setPlaying(false); t = winStart(); sync(); draw(); }),
    spdWrap,
    el("i", "rr-sep"), dPrev, dNext, exitBtn,
    el("i", "rr-sep"), rPrev, rNext,
    el("i", "rr-sep"), plBtn, resetBtn,
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
  const addBm = (tick, glyph, title) => {
    if (tick == null || tick < d.t0 || tick > d.t1) return;
    const m = el("span", "rr-bmk", glyph);
    m.style.left = (pct(tick) * 100) + "%";
    m.title = title;
    bm.appendChild(m);
  };
  for (const b of d.bomb || []) {
    if (!BOMB_GLYPH[b[1]]) continue;
    const who = b[1] !== "boom" && b[2] >= 0 && P[b[2]] ? " \u00B7 " + P[b[2]].name : "";
    addBm(b[0], BOMB_GLYPH[b[1]], (lab["b_" + b[1]] || b[1]) + " " + fmtT((b[0] - d.t0) / d.tr) + who);
  }
  if (d.out && d.out.end != null && d.out.win) {
    addBm(d.out.end, WHY_GLYPH[d.out.why] || "\u2691",
      (d.out.win === "CT" ? (lab.won_ct || "CT") : (lab.won_t || "T")) + (d.out.why ? " \u00B7 " + (lab["why_" + d.out.why] || "") : ""));
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
    for (const [n, w, why] of d.rounds) {
      const b = el("button",
        "rr-rd" + (w === "CT" ? " wct" : w === "T" ? " wt" : "") + (n === d.round ? " cur" : ""));
      b.type = "button";
      b.appendChild(document.createTextNode(String(n)));
      if (why && WHY_GLYPH[why]) b.appendChild(el("i", "rr-rg", WHY_GLYPH[why]));
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
    right.appendChild(el("div", "rr-rh",
      (lab.duels || "duels") + (d.duels.length ? " \u00B7 " + d.duels.length : "")));
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
      const bits = [z.w];
      if (z.hs) bits.push(lab.hs || "HS");
      if (z.dist != null) bits.push(z.dist + "u");
      if (z.dur != null) bits.push(z.dur + "s");
      const meta = el("div", "rr-cmeta", bits.filter(Boolean).join(" \u00B7 "));
      c.title = z.kn + " > " + z.vn + " \u00B7 " + bits.filter(Boolean).join(" \u00B7 ");
      c.setAttribute("aria-label", c.title);
      c.append(top, meta);
      c.addEventListener("click", () => toggleDuel(i));
      right.appendChild(c);
      cardEls.push(c);
    });
  }

  // ---------- left: players, one list sorted by nickname, an eye each -----------------------------
  const EYE_ON = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 19c-7 0-11-7-11-7a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 5c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
  const lrows = [];
  let masterEye = null;
  function setEye(b, on, title) {
    b.innerHTML = on ? EYE_ON : EYE_OFF;  // static markup, no data inside
    b.title = title;
    b.setAttribute("aria-label", title);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  }
  function buildLeft() {
    left.replaceChildren();
    const head = el("div", "rr-lh");
    head.appendChild(el("span", "rr-lt", (lab.players || "players") + " \u00B7 " + P.length));
    masterEye = el("button", "rr-eye");
    masterEye.type = "button";
    masterEye.addEventListener("click", () => {
      const allOn = P.every(visible);
      for (const p of P) { if (allOn) S.hidden[p.sid] = true; else delete S.hidden[p.sid]; }
      applyHidden(); draw();
    });
    head.appendChild(masterEye);
    left.appendChild(head);
    let lastSide = null;
    for (const p of P) {
      if (p.side !== lastSide) {
        // a group header per side; the players inside are sorted by nickname
        lastSide = p.side;
        const cnt = P.filter((q) => q.side === p.side).length;
        const th = el("div", "rr-th " + sideCls(p.side));
        th.appendChild(el("i", "rr-sdot " + sideCls(p.side)));
        th.appendChild(el("span", null, (p.side === "CT" ? "CT" : p.side === "T" ? "T" : "?") + " \u00B7 " + cnt));
        left.appendChild(th);
      }
      const row = el("div", "rr-prow");
      const eye = el("button", "rr-eye");
      eye.type = "button";
      eye.addEventListener("click", () => {
        if (S.hidden[p.sid]) delete S.hidden[p.sid]; else S.hidden[p.sid] = true;
        applyHidden(); draw();
      });
      const dot = el("i", "rr-sdot " + sideCls(p.side));
      const body = el("div", "rr-pbody");
      const nm = el("span", "rr-pname", p.name);
      nm.title = p.name;
      const sub = el("span", "rr-psub");
      const wp = el("span", "rr-pw", "");
      const sp = el("span", "rr-psp", "");
      sub.append(wp, sp);
      body.append(nm, sub);
      const hp = el("span", "rr-php", "");
      row.append(eye, dot, body, hp);
      left.appendChild(row);
      lrows.push({ p: p, row: row, eye: eye, wp: wp, sp: sp, hp: hp });
    }
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
    for (const lr of lrows) {
      const s = state(lr.p, t);
      const alive = s ? s.al : false;
      lr.row.classList.toggle("dead", !alive);
      const hp = s && s.hp != null ? s.hp : null;
      if (!alive) {
        lr.hp.textContent = "\u2715";
        lr.hp.className = "rr-php";
      } else {
        lr.hp.textContent = hp == null ? "" : String(hp);
        lr.hp.className = "rr-php" + (hp != null && hp > 0 ? (hp < 30 ? " low" : hp < 70 ? " mid" : "") : "");
      }
      const w = weaponAt(lr.p, t);
      lr.wp.textContent = alive ? w : "";
      lr.wp.title = w;
      const v = alive ? speedAt(lr.p, t) : null;
      lr.sp.textContent = v == null ? "" : Math.round(v) + " " + (lab.u_s || "u/s");
    }
  }
  function applyPanel() {
    grid.classList.toggle("nolf", !S.panel);
    plBtn.classList.toggle("on", !!S.panel);
    plBtn.title = S.panel ? (lab.players_hide || "hide") : (lab.players_show || "show");
    plBtn.setAttribute("aria-label", plBtn.title);
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
    let vmax = 250;
    if (tel) for (const w of ["k", "v"]) for (const v of tel[w].sp) if (v != null && v > vmax) vmax = v;
    vmax = Math.ceil(vmax / 50) * 50;
    const head = el("div", "rr-dph");
    head.appendChild(el("span", "rr-dpt", lab.duel_panel || "Duel aim"));
    if (z.first) {
      const fl = { killer: lab.first_killer, victim: lab.first_victim, simultaneous: lab.first_sim };
      if (fl[z.first]) head.appendChild(el("span", "rr-dpf", fl[z.first]));
    }
    if (z.dist != null) head.appendChild(el("span", "rr-dpf", (lab.tel_dist || "distance") + " " + z.dist + " u"));
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
      if (sd.sp != null) fb.push(sd.sp + " " + (lab.u_s || "u/s"));
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
    const z = duelAt(sel);
    for (const p of P) {
      if (!visible(p)) continue;
      const s = state(p, t);
      if (!s || s.x == null || s.y == null) continue;
      const q = toPx(s.lvl, s.x, s.y), px = q[0], py = q[1];
      const col = sideCol(p.side);
      if (s.al) {
        if (s.fl > 0.2) {
          ctx.beginPath(); ctx.arc(px, py, R + 3, 0, Math.PI * 2);
          ctx.strokeStyle = rgba(C.flash, Math.min(0.9, 0.3 + s.fl / 4));
          ctx.lineWidth = 2.5; ctx.stroke();
        }
        // view direction: a short arrow
        const a = (s.yaw == null ? 0 : s.yaw) * Math.PI / 180;
        const dx = Math.cos(a), dy = -Math.sin(a), ox = -dy, oy = dx;
        ctx.strokeStyle = col; ctx.lineWidth = 1.8;
        ctx.beginPath();
        ctx.moveTo(px + dx * R, py + dy * R); ctx.lineTo(px + dx * (R + 6), py + dy * (R + 6));
        ctx.stroke();
        const hx = px + dx * (R + 9), hy = py + dy * (R + 9);
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.moveTo(hx, hy);
        ctx.lineTo(hx - dx * 4 + ox * 2.8, hy - dy * 4 + oy * 2.8);
        ctx.lineTo(hx - dx * 4 - ox * 2.8, hy - dy * 4 - oy * 2.8);
        ctx.closePath(); ctx.fill();
        ctx.beginPath(); ctx.arc(px, py, R, 0, Math.PI * 2);
        ctx.fillStyle = col; ctx.fill();
        ctx.lineWidth = 1; ctx.strokeStyle = rgba(C.bg, 0.9); ctx.stroke();
        // the two duelists of the selected duel get an accent ring
        if (z && (P[z.ki] === p || P[z.vi] === p)) {
          ctx.beginPath(); ctx.arc(px, py, R + 6.5, 0, Math.PI * 2);
          ctx.strokeStyle = C.acc; ctx.lineWidth = 1.6; ctx.stroke();
        }
        // HP bar + weapon under the dot
        if (s.hp != null && s.hp > 0) {
          const bw = 16, bx = px - bw / 2, by = py + R + 3;
          ctx.fillStyle = rgba(C.bg, 0.75);
          ctx.fillRect(bx - 0.5, by - 0.5, bw + 1, 4);
          ctx.fillStyle = hpCol(s.hp);
          ctx.fillRect(bx, by, bw * Math.min(1, s.hp / 100), 3);
        }
        const w = weaponAt(p, t);
        if (w) {
          ctx.font = "9px " + FF; ctx.fillStyle = C.text2;
          halo(w, px, py + R + 14);
        }
        ctx.font = "600 10px " + FF; ctx.fillStyle = C.text;
        halo(p.name, px, py - R - 5);
      } else {
        ctx.strokeStyle = C.dead || C.text2; ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(px - R * 0.8, py - R * 0.8); ctx.lineTo(px + R * 0.8, py + R * 0.8);
        ctx.moveTo(px + R * 0.8, py - R * 0.8); ctx.lineTo(px - R * 0.8, py + R * 0.8);
        ctx.stroke();
        ctx.font = "600 10px " + FF; ctx.fillStyle = C.dead || C.text2;
        halo(p.name, px, py - R - 5);
      }
    }
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

  function drawFeed() {
    feed.replaceChildren();
    for (const k of d.kills.filter((x) => x[0] <= t).slice(-5).reverse()) {
      const a = k[1] >= 0 ? P[k[1]] : null, v = k[2] >= 0 ? P[k[2]] : null;
      const row = el("div", "rr-fe");
      const an = el("b", null, a ? a.name : "?");
      const vn = el("b", null, v ? v.name : "?");
      an.style.color = a ? sideCol(a.side) : "";
      vn.style.color = v ? sideCol(v.side) : "";
      row.append(an, el("span", "rr-w", (k[3] ? "[" + k[3] + "]" : "") + (k[4] ? " \u25CE" : "")), vn);
      feed.appendChild(row);
    }
  }

  // ---------- legend + howto ------------------------------------------------------------------------------------------------
  const leg = el("div", "rr-legend");
  const mark = (cls, col, text) => {
    const s = el("span"); const i = el("i", cls);
    i.style.background = col; s.append(i, " " + text);
    return s;
  };
  leg.append(
    mark("rr-dot", C.ct, "CT"), mark("rr-dot", C.t, "T"),
    el("span", null, "\u2715 " + (lab.dead || "dead")),
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
    drawFeed();
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
