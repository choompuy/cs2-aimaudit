"""
Styling and the client-side player of the round replay component
(ui/roundrep.py): the CSS chrome and the JS player, split into their
own module so the Python payload logic stays reviewable - these are
big literal strings, not prose. Touch this only for visuals.
"""

# ---------------------------------------------------------------- styling
# canvas painting colours arrive through the payload (theme tokens); the
# chrome (buttons, panels, feed) uses CSS variables like ui/strip.py.
# CT/T colours also arrive through the payload and are re-published as
# custom properties (--rr-ct / --rr-t / --rr-ctb / --rr-tb) for the DOM.
_CSS = """
:host { display: block; }
.rr {
  font-family: inherit;
  color: var(--text-primary);
}
.rr-grid {
  display: grid;
  grid-template-columns: minmax(140px, 200px) minmax(280px, 1fr) minmax(165px, 215px);
  gap: 0.6rem;
  align-items: start;
}
.rr-grid.nolf { grid-template-columns: minmax(280px, 1fr) minmax(165px, 215px); }
.rr-grid.nolf .rr-left { display: none; }
.rr.narrow .rr-grid,
.rr.narrow .rr-grid.nolf { grid-template-columns: minmax(0, 1fr); }
.rr.narrow .rr-left { max-height: 11rem; overflow-y: auto; }
.rr.narrow .rr-right { max-height: 14rem; }

/* ---------- left: players panel -------------------------------------- */
.rr-left {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-1);
  padding: 0.35rem 0.45rem 0.45rem;
}
.rr-lh {
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-semibold);
  color: var(--text-secondary);
  letter-spacing: 0.03em;
  padding: 0.05rem 0.1rem 0.25rem;
}
.rr-team {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin: 0.3rem 0.1rem 0.12rem;
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-semibold);
}
.rr-team.ct { color: var(--rr-ct); }
.rr-team.t { color: var(--rr-t); }
.rr-team::after { content: ""; flex: 1 1 auto; height: 1px; background: var(--border-subtle); }
.rr-prow {
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
  padding: 0.1rem 0.25rem;
  border-radius: var(--radius-md);
}
.rr-prow.me {
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  box-shadow: inset 2px 0 0 var(--accent);
}
.rr-pname {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-size-xs);
}
.rr-pw {
  flex: none;
  max-width: 4.4rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
  font-size: var(--font-size-xs);
}
.rr-php {
  flex: none;
  min-width: 1.7rem;
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-semibold);
  color: var(--positive);
}
.rr-php.mid { color: var(--warning); }
.rr-php.low { color: var(--negative); }
.rr-prow.dead { opacity: 0.45; }
.rr-prow.dead .rr-php { color: var(--text-secondary); }

/* ---------- center: radar + controls + timeline + rounds ------------- */
.rr-main { min-width: 0; }
.rr-wrap {
  position: relative;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-1);
  overflow: hidden;
}
.rr canvas { display: block; width: 100%; cursor: grab; }
.rr-wrap.dragging canvas { cursor: grabbing; }
.rr-feed {
  position: absolute;
  top: 0.4rem; right: 0.4rem;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  max-width: 46%;
  pointer-events: none;
}
.rr-fe {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.1rem 0.45rem;
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--surface-2) 82%, transparent);
  border: 1px solid var(--border-subtle);
  font-size: var(--font-size-xs);
  white-space: nowrap;
  overflow: hidden;
}
.rr-fe.mine { border-color: var(--negative); }
.rr-fe b { font-weight: var(--font-weight-semibold); }
.rr-fe .rr-w { color: var(--text-secondary); }
.rr-fe .rr-hs { color: var(--negative); font-weight: var(--font-weight-semibold); }
.rr-ctl {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  padding: 0.35rem 0 0.15rem;
  font-size: var(--font-size-sm);
}
.rr-sbtn {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.7rem;
  height: 1.7rem;
  padding: 0 0.25rem;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--surface-2);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: var(--font-size-sm);
  line-height: 1;
  cursor: pointer;
}
.rr-sbtn:hover { color: var(--accent); border-color: var(--accent); }
.rr-sbtn.on { color: var(--accent); border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent); }
.rr-sbtn.exit { color: var(--negative); }
.rr-sbtn.exit:hover, .rr-sbtn.exit.on { border-color: var(--negative); color: var(--negative); background: color-mix(in srgb, var(--negative) 10%, transparent); }
.rr-sbtn:focus-visible,
.rr-card:focus-visible,
.rr-rd:focus-visible,
.rr-track:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.rr-time {
  font-variant-numeric: tabular-nums;
  color: var(--text-secondary);
  white-space: nowrap;
}
.rr-lock {
  display: inline-flex;
  align-items: center;
  padding: 0.05rem 0.45rem;
  border-radius: var(--radius-pill);
  border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  color: var(--accent);
  font-size: var(--font-size-xs);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 16rem;
}

/* ---------- timeline --------------------------------------------------- */
.rr-tl { padding: 0.2rem 0 0.05rem; }
.rr-track {
  position: relative;
  height: 1.55rem;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-2);
  cursor: pointer;
  touch-action: none;
  overflow: hidden;
}
.rr-mark {
  position: absolute;
  top: 2px; bottom: 2px;
  width: 2px;
  border-radius: 1px;
  background: var(--text-secondary);
}
.rr-mark.ct { background: var(--rr-ct); }
.rr-mark.t { background: var(--rr-t); }
.rr-seg {
  position: absolute;
  bottom: 1px;
  height: 3px;
  border-radius: 2px;
  background: color-mix(in srgb, var(--text-secondary) 45%, transparent);
}
.rr-seg.sel {
  height: 4px;
  background: var(--accent);
}
.rr-dim {
  position: absolute;
  top: 0; bottom: 0;
  background: color-mix(in srgb, var(--bg-app) 68%, transparent);
}
.rr-ph {
  position: absolute;
  top: 0; bottom: 0;
  width: 3px;
  border-radius: 2px;
  background: var(--accent);
  box-shadow: 0 0 0 1px var(--bg-app);
  transform: translateX(-50%);
  pointer-events: none;
}

/* ---------- rounds row ------------------------------------------------- */
.rr-rounds {
  display: flex;
  gap: 0.15rem;
  overflow-x: auto;
  padding: 0.25rem 0 0.05rem;
  scrollbar-width: thin;
}
.rr-rd {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.55rem;
  height: 1.4rem;
  padding: 0 0.3rem;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-2);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-semibold);
  line-height: 1;
  cursor: pointer;
}
.rr-rd:hover { border-color: var(--accent); color: var(--accent); }
.rr-rd.wct {
  background: var(--rr-ctb);
  border-color: color-mix(in srgb, var(--rr-ct) 40%, var(--border-subtle));
  color: var(--rr-ct);
}
.rr-rd.wt {
  background: var(--rr-tb);
  border-color: color-mix(in srgb, var(--rr-t) 40%, var(--border-subtle));
  color: var(--rr-t);
}
.rr-rd.cur { box-shadow: inset 0 0 0 1px var(--accent); border-color: var(--accent); }

/* ---------- right: duel cards ------------------------------------------ */
.rr-right {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-1);
  padding: 0.35rem 0.4rem 0.45rem;
  max-height: 36rem;
  overflow-y: auto;
  scrollbar-width: thin;
}
.rr-rh {
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-semibold);
  color: var(--text-secondary);
  letter-spacing: 0.03em;
  padding: 0.05rem 0.1rem 0.2rem;
}
.rr-noduels {
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  padding: 0.1rem 0.15rem;
}
.rr-card {
  display: block;
  width: 100%;
  text-align: left;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-2);
  padding: 0.22rem 0.45rem 0.25rem;
  cursor: pointer;
  font-family: inherit;
  color: var(--text-primary);
}
.rr-card:hover { border-color: var(--accent); }
.rr-card.up { opacity: 0.45; }
.rr-card.cur {
  border-color: var(--accent);
  outline: 1px solid var(--accent);
  background: var(--surface-selected);
}
.rr-ch { display: flex; align-items: center; gap: 0.35rem; }
.rr-ic { flex: none; font-weight: var(--font-weight-semibold); }
.rr-card.k .rr-ic { color: var(--positive); }
.rr-card.d .rr-ic { color: var(--negative); }
.rr-cnm {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-semibold);
}
.rr-ctm2 {
  flex: none;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  font-size: var(--font-size-xs);
}
.rr-cmeta {
  color: var(--text-secondary);
  font-size: var(--font-size-xs);
  padding-top: 0.08rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ---------- key panels (inside a duel segment only) -------------------- */
.rr-keys {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem 1.2rem;
  padding: 0.35rem 0 0;
  font-size: var(--font-size-sm);
}
.rr-keys.hid { display: none; }
.rr-kp { display: none; align-items: center; gap: 0.3rem; flex-wrap: wrap; }
.rr-kp.on { display: flex; }
.rr-kname { color: var(--text-secondary); max-width: 9rem;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rr-k {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.55rem;
  height: 1.55rem;
  padding: 0 0.2rem;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-2);
  color: var(--text-secondary);
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-semibold);
  line-height: 1;
}
.rr-k.on {
  color: var(--text-inverse);
  background: var(--accent);
  border-color: var(--accent);
}
.rr-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 1rem;
  color: var(--text-secondary);
  font-size: var(--font-size-xs);
  padding-top: 0.3rem;
}
.rr-legend span { display: inline-flex; align-items: center; gap: 0.25rem; }
.rr-dot { width: 0.7rem; height: 0.7rem; border-radius: 50%;
  display: inline-block; flex: none; }
.rr-legline { width: 2px; height: 0.75rem; border-radius: 1px;
  display: inline-block; flex: none; }
"""


_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement;
  const S = root.__rr || (root.__rr = {});
  S.data = data;
  const d = data;

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

  let host = root.querySelector(".rr-host");
  if (!host) { host = el("div", "rr-host"); root.appendChild(host); }
  host.replaceChildren();

  const box = el("div", "rr");
  host.appendChild(box);
  // CT/T colours arrive through the payload (theme tokens) - publish them
  // as custom properties for the DOM chrome (cards, round chips, marks)
  for (const ck of ["ct", "t", "ctb", "tb", "acc"]) {
    if (d.col[ck]) box.style.setProperty("--rr-" + ck, d.col[ck]);
  }

  const grid = el("div", "rr-grid");
  box.appendChild(grid);
  const left = el("div", "rr-left");
  grid.appendChild(left);
  const main = el("div", "rr-main");
  grid.appendChild(main);
  const right = el("div", "rr-right");
  grid.appendChild(right);

  const wrap = el("div", "rr-wrap");
  main.appendChild(wrap);
  const cvs = document.createElement("canvas");
  wrap.appendChild(cvs);
  const feed = el("div", "rr-feed");
  wrap.appendChild(feed);
  const ctl = el("div", "rr-ctl");
  main.appendChild(ctl);
  const tl = el("div", "rr-tl");
  main.appendChild(tl);
  const roundsEl = el("div", "rr-rounds");
  main.appendChild(roundsEl);
  const keyWrap = el("div", "rr-keys");
  box.appendChild(keyWrap);

  const P = d.players;

  // ---------- state ------------------------------------------------------
  const SPEEDS = [1, 0.5, 0.25];
  let playing = false, speed = 1;
  let sel = (typeof d.sel === "number") ? d.sel : -1;
  let t = Math.min(Math.max(d.at, d.t0), d.t1);
  let scrubbing = false;

  const duelAt = (i) => ((i >= 0 && i < d.duels.length) ? d.duels[i] : null);
  const winStart = () => { const z = duelAt(sel); return z ? z.a : d.t0; };
  const winEnd = () => { const z = duelAt(sel); return z ? z.b : d.t1; };
  const clampT = (x) => Math.min(Math.max(x, winStart()), winEnd());

  // ---------- controls ---------------------------------------------------
  const btn = (cls, ch, title, fn) => {
    const b = el("button", "rr-sbtn " + (cls || ""), ch);
    b.type = "button";
    if (title) { b.title = title; b.setAttribute("aria-label", title); }
    b.addEventListener("click", fn);
    return b;
  };

  // playback: ONE place owns the rAF loop, so the play button cannot get
  // out of sync with the animation loop again (the old double-listener
  // bug: the button toggled `playing`, a second listener toggled the rAF,
  // and playback only moved while dragging the slider)
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

  const playBtn = btn("", "\\u25B6", d.lab.play || "play", () => {
    if (!playing && t >= winEnd() - 0.5) t = winStart();
    setPlaying(!playing);
  });
  ctl.appendChild(playBtn);
  ctl.appendChild(btn("", "\\u23EE", d.lab.restart || "restart", () => {
    setPlaying(false); t = winStart(); sync(); draw();
  }));
  ctl.appendChild(btn("", "\\u2039", d.lab.duel_prev || "prev duel", () => jumpMark(-1)));
  ctl.appendChild(btn("", "\\u203A", d.lab.duel_next || "next duel", () => jumpMark(1)));
  const exitBtn = btn("exit", "\\u2715", d.lab.exit || "exit duel", () => exitDuel());
  ctl.appendChild(exitBtn);
  const spdBtns = SPEEDS.map((s) => {
    const b = btn("spd", s + "x", (d.lab.speed || "speed") + " " + s + "x", () => {
      speed = s; sync();
    });
    b.dataset.spd = String(s);
    ctl.appendChild(b);
    return b;
  });
  const plBtn = btn("", d.lab.players || "players", d.lab.players_hide || "hide", () => {
    S.hid = !S.hid; applyHid(); layout(); draw();
  });
  ctl.appendChild(plBtn);
  ctl.appendChild(btn("", "\\u21BB", d.lab.view_reset || "reset view", () => resetView()));
  const timeLab = el("span", "rr-time");
  ctl.appendChild(timeLab);
  const lockChip = el("span", "rr-lock", d.lab.locked || "");
  lockChip.title = d.lab.locked || "";
  ctl.appendChild(lockChip);

  function jumpMark(dir) {
    if (!d.duels.length) return;
    let best = null;
    for (let k = 0; k < d.duels.length; k++) {
      const z = d.duels[k];
      const dt = dir > 0 ? z.a - t : t - z.b;
      if (dt > 0 && (best === null || dt < best.dt)) best = { dt: dt, i: k };
    }
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
  function exitDuel() {
    sel = -1;
    sync(); draw();
  }
  function toggleDuel(i) {
    if (sel === i) exitDuel(); else selectDuel(i);
  }

  // ---------- timeline ---------------------------------------------------
  // kill marks (CT blue / T yellow by the killer's side), my duel
  // segments, the selected duel's lock dimmers and the playhead
  const track = el("div", "rr-track");
  track.tabIndex = 0;
  track.setAttribute("role", "slider");
  track.setAttribute("aria-label", d.lab.time || "time");
  tl.appendChild(track);
  const pct = (tick) => (tick - d.t0) / (d.t1 - d.t0);
  const markCls = (ai) => {
    const p = ai >= 0 ? P[ai] : null;
    if (p && p.side === "CT") return "ct";
    if (p && p.side === "T") return "t";
    return "n";
  };
  const killTitle = (k) => {
    const a = k[1] >= 0 ? P[k[1]] : null, v = k[2] >= 0 ? P[k[2]] : null;
    let s = (a ? a.name : "?") + " > " + (v ? v.name : "?");
    if (k[3]) s += " [" + k[3] + "]";
    if (k[4]) s += " \\u00B7 " + (d.lab.hs || "HS");
    return s;
  };
  for (const k of d.kills) {
    const m = el("div", "rr-mark " + markCls(k[1]));
    m.style.left = (pct(k[0]) * 100) + "%";
    m.title = killTitle(k);
    track.appendChild(m);
  }
  const segs = [];
  for (const z of d.duels) {
    const s = el("div", "rr-seg");
    s.style.left = (pct(z.a) * 100) + "%";
    s.style.width = Math.max(0.5, (pct(z.b) - pct(z.a)) * 100) + "%";
    track.appendChild(s);
    segs.push(s);
  }
  const dimA = el("div", "rr-dim"), dimB = el("div", "rr-dim");
  track.append(dimA, dimB);
  const ph = el("div", "rr-ph");
  track.appendChild(ph);

  const tickAt = (ev) => {
    const r = track.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (ev.clientX - r.left) / Math.max(1, r.width)));
    return Math.round(d.t0 + f * (d.t1 - d.t0));
  };
  track.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { track.setPointerCapture(e.pointerId); } catch (err) {}
    scrubbing = true;
    if (playing) setPlaying(false);
    seek(tickAt(e));
  });
  track.addEventListener("pointermove", (e) => { if (scrubbing) seek(tickAt(e)); });
  track.addEventListener("pointerup", () => { scrubbing = false; });
  track.addEventListener("pointercancel", () => { scrubbing = false; });
  track.addEventListener("keydown", (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const k = e.key;
    if (k !== "ArrowLeft" && k !== "ArrowRight" && k !== "Home" && k !== "End") return;
    e.preventDefault();
    const st = (e.shiftKey ? 5 : 1) * d.tr;
    if (k === "Home") seek(winStart());
    else if (k === "End") seek(winEnd());
    else seek(t + (k === "ArrowLeft" ? -st : st));
  });
  function seek(x) { t = clampT(x); draw(); }

  // ---------- rounds row (below the timeline) ----------------------------
  function buildRounds() {
    roundsEl.replaceChildren();
    if (d.lab.rounds) roundsEl.setAttribute("aria-label", d.lab.rounds);
    for (const rn of d.rounds) {
      const n = rn[0], w = rn[1];
      const b = el("button",
        "rr-rd" + (w === "CT" ? " wct" : w === "T" ? " wt" : "") + (n === d.round ? " cur" : ""),
        String(n));
      b.type = "button";
      let tip = (d.lab.round || "R") + " " + n;
      if (w === "CT") tip += " \\u00B7 " + (d.lab.won_ct || "");
      else if (w === "T") tip += " \\u00B7 " + (d.lab.won_t || "");
      b.title = tip;
      b.setAttribute("aria-label", tip);
      b.addEventListener("click", () => { if (n !== d.round) setTriggerValue("rnd", n); });
      roundsEl.appendChild(b);
    }
    const cur = roundsEl.querySelector(".rr-rd.cur");
    if (cur) {
      roundsEl.scrollLeft = Math.max(
        0, cur.offsetLeft - (roundsEl.clientWidth - cur.offsetWidth) / 2
      );
    }
  }

  // ---------- right: duel cards -------------------------------------------
  const cardEls = [];
  function buildCards() {
    right.replaceChildren();
    const h = el("div", "rr-rh",
      d.lab.duels + (d.duels.length ? " \\u00B7 " + d.duels.length : ""));
    right.appendChild(h);
    if (!d.duels.length) {
      right.appendChild(el("div", "rr-noduels", d.lab.no_duels || ""));
      return;
    }
    d.duels.forEach((z, i) => {
      const c = el("button", "rr-card " + (z.res === "kill" ? "k" : "d"));
      c.type = "button";
      const top = el("div", "rr-ch");
      top.appendChild(el("span", "rr-ic", z.res === "kill" ? "\\u2713" : "\\u2715"));
      top.appendChild(el("span", "rr-cnm", z.opp));
      top.appendChild(el("span", "rr-ctm2", fmtT((z.tk - d.t0) / d.tr)));
      let tip = (z.res === "kill" ? d.lab.kill : d.lab.death) + " \\u00B7 " + z.opp;
      if (z.w) tip += " \\u00B7 " + z.w;
      if (z.dur != null) tip += " \\u00B7 " + z.dur + "s";
      c.title = tip;
      c.setAttribute("aria-label", tip);
      const meta = el("div", "rr-cmeta",
        z.w + (z.hs ? " \\u00B7 " + (d.lab.hs || "HS") : "")
        + (z.dur != null ? " \\u00B7 " + z.dur + "s" : ""));
      c.append(top, meta);
      c.addEventListener("click", () => toggleDuel(i));
      right.appendChild(c);
      cardEls.push(c);
    });
  }

  // ---------- left: players panel (hideable) -------------------------------
  const lrows = [];
  function buildLeft() {
    left.replaceChildren();
    left.appendChild(el("div", "rr-lh", d.lab.players || "players"));
    const groups = { CT: [], T: [], other: [] };
    for (const p of P) {
      if (p.side === "CT") groups.CT.push(p);
      else if (p.side === "T") groups.T.push(p);
      else groups.other.push(p);
    }
    const mkGroup = (label, list, cls) => {
      if (!list.length) return;
      if (label) left.appendChild(el("div", "rr-team " + cls, label));
      for (const p of list) {
        const row = el("div", "rr-prow" + (p.role === "me" ? " me" : ""));
        const nm = el("span", "rr-pname", p.name);
        const wp = el("span", "rr-pw", "");
        const hp = el("span", "rr-php", "");
        row.append(nm, wp, hp);
        left.appendChild(row);
        lrows.push({ p: p, row: row, nm: nm, wp: wp, hp: hp });
      }
    };
    mkGroup("CT", groups.CT, "ct");
    mkGroup("T", groups.T, "t");
    mkGroup("", groups.other, "");
  }
  function updateLeft() {
    for (const lr of lrows) {
      const s = state(lr.p, t);
      const alive = s ? s.al : false;
      lr.row.classList.toggle("dead", !alive);
      const hp = s && s.hp != null ? s.hp : null;
      if (!alive) {
        lr.hp.textContent = "\\u2715";
        lr.hp.className = "rr-php";
      } else if (hp != null && hp > 0) {
        lr.hp.textContent = String(hp);
        lr.hp.className = "rr-php" + (hp < 30 ? " low" : hp < 70 ? " mid" : "");
      } else {
        lr.hp.textContent = hp == null ? "" : String(hp);
        lr.hp.className = "rr-php";
      }
      const w = weaponAt(lr.p, t);
      lr.wp.textContent = alive ? w : "";
      lr.wp.title = w;
    }
  }

  // ---------- key panels: only inside a duel segment ----------------------
  const keyPanels = [];
  if (d.kp && d.kp.length && d.keys && d.keys.length) {
    for (const pr of d.kp) {
      const sid = pr[0], caption = pr[1];
      const p = P.find((x) => x.sid === sid);
      if (!p || !p.keys) continue;
      const kp = el("div", "rr-kp");
      kp.appendChild(el("span", "rr-kname", caption));
      const boxes = {};
      for (const k of d.keys) {
        const b = el("span", "rr-k", (d.lab["k_" + k] || k));
        b.title = k;
        kp.appendChild(b);
        boxes[k] = b;
      }
      keyWrap.appendChild(kp);
      keyPanels.push({ sid: sid, el: kp, p: p, boxes: boxes });
    }
  }
  function activePanels() {
    if (sel < 0) return [];
    const z = duelAt(sel);
    if (!z) return [];
    const sids = {};
    if (P[d.myIdx]) sids[P[d.myIdx].sid] = 1;
    if (z.oi >= 0 && P[z.oi]) sids[P[z.oi].sid] = 1;
    return keyPanels.filter((kp) => sids[kp.sid]);
  }

  // ---------- radar geometry + zoom / pan ----------------------------------
  const DPR = () => Math.max(1, window.devicePixelRatio || 1);
  const ctx = cvs.getContext("2d");
  const C = d.col || {};
  const imgs = [];
  const loadImg = (uri, i) => {
    const im = new Image();
    imgs[i] = im;
    if (uri) im.src = uri;
  };
  const twoPanels = !!(d.radar && d.radar.lo);
  if (d.radar) {
    loadImg(d.radar.png, 0);
    if (twoPanels) loadImg(d.radar.lo, 1);
  }
  let W = 600, H = 420, panels = [], lastW = 0;

  function layout() {
    const r = wrap.getBoundingClientRect();
    W = Math.max(240, r.width);
    lastW = W;
    H = Math.max(280, Math.min(560, Math.round(W * 0.62)));
    box.classList.toggle("narrow", W < 720);
    const k = DPR();
    cvs.width = Math.round(W * k); cvs.height = Math.round(H * k);
    cvs.style.height = H + "px";
    ctx.setTransform(k, 0, 0, k, 0, 0);
    // zoom/pan survive a relayout: only the base placement is recomputed
    const mk = (i, ox, oy, fit) => {
      const old = panels[i];
      panels[i] = {
        ox: ox, oy: oy, fit: fit,
        z: old ? old.z : 1, dx: old ? old.dx : 0, dy: old ? old.dy : 0,
      };
    };
    if (d.radar) {
      const n = twoPanels ? 2 : 1;
      const pw = W / n;
      for (let i = 0; i < n; i++) {
        const fit = Math.min(pw, H) / d.radar.size;
        const ox = i * pw + (pw - d.radar.size * fit) / 2;
        const oy = (H - d.radar.size * fit) / 2;
        mk(i, ox, oy, fit);
      }
    } else if (d.gridb) {
      const g = d.gridb;
      const fit = Math.min(W / (g.maxx - g.minx), H / (g.maxy - g.miny));
      mk(0, (W - (g.maxx - g.minx) * fit) / 2, (H - (g.maxy - g.miny) * fit) / 2, fit);
    }
  }

  function toPx(level, x, y) {
    const p = panels[level] || panels[0] || { ox: 0, oy: 0, fit: 1, z: 1, dx: 0, dy: 0 };
    if (d.radar) {
      const kk = d.radar.size / 1024;
      const u = (x - d.radar.pos_x) / d.radar.scale * kk;
      const v = (d.radar.pos_y - y) / d.radar.scale * kk;
      return [p.ox + p.dx + u * p.fit * p.z, p.oy + p.dy + v * p.fit * p.z];
    }
    const g = d.gridb;
    return [p.ox + p.dx + (x - g.minx) * p.fit * p.z,
            p.oy + p.dy + (g.maxy - y) * p.fit * p.z];
  }

  function resetView() {
    for (const p of panels) { if (p) { p.z = 1; p.dx = 0; p.dy = 0; } }
    draw();
  }
  const panelAt = (mx) => (twoPanels ? (mx < W / 2 ? 0 : 1) : 0);

  // wheel = zoom (around the cursor), LMB drag = pan, dblclick = reset
  cvs.addEventListener("wheel", (e) => {
    e.preventDefault();
    const p = panels[panelAt(e.offsetX)];
    if (!p) return;
    const k = e.deltaY < 0 ? 1.18 : 1 / 1.18;
    const z2 = Math.max(0.6, Math.min(14, p.z * k));
    const kk = z2 / p.z;
    const ax = e.offsetX - p.ox, ay = e.offsetY - p.oy;
    p.dx = ax - (ax - p.dx) * kk;
    p.dy = ay - (ay - p.dy) * kk;
    p.z = z2;
    draw();
  }, { passive: false });

  let pan = null;
  cvs.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    pan = { x: e.offsetX, y: e.offsetY, i: panelAt(e.offsetX) };
    try { cvs.setPointerCapture(e.pointerId); } catch (err) {}
    wrap.classList.add("dragging");
  });
  cvs.addEventListener("pointermove", (e) => {
    if (!pan) return;
    const p = panels[pan.i];
    if (p) {
      p.dx += e.offsetX - pan.x;
      p.dy += e.offsetY - pan.y;
    }
    pan.x = e.offsetX; pan.y = e.offsetY;
    draw();
  });
  const endPan = () => { pan = null; wrap.classList.remove("dragging"); };
  cvs.addEventListener("pointerup", endPan);
  cvs.addEventListener("pointercancel", endPan);
  cvs.addEventListener("dblclick", resetView);

  // ---------- interpolated player state ----------------------------------
  function state(p, tick) {
    const n = p.x.length;
    if (!n) return null;
    const f = (tick - d.t0) / d.step;
    let i0 = Math.floor(f), i1 = Math.min(i0 + 1, n - 1);
    i0 = Math.max(0, Math.min(i0, n - 1));
    const fr = Math.max(0, Math.min(1, f - i0));
    const num = (a, b) => (a == null || b == null ? (a == null ? b : a) : a + (b - a) * fr);
    const yawA = p.yaw[i0], yawB = p.yaw[i1];
    let yaw = num(yawA, yawB);
    if (yawA != null && yawB != null) {
      let dY = ((yawB - yawA + 540) % 360) - 180;  // shortest arc
      yaw = yawA + dY * fr;
    }
    return {
      x: num(p.x[i0], p.x[i1]),
      y: num(p.y[i0], p.y[i1]),
      yaw: yaw,
      hp: p.hp ? p.hp[i0] : undefined,
      al: p.al ? !!p.al[i0] : true,
      fl: p.fl ? (p.fl[i0] || 0) : 0,
      lvl: p.lvl ? (p.lvl[i0] ? 1 : 0) : 0,
    };
  }
  function weaponAt(p, tick) {
    if (!p.w) return "";
    let w = p.w.length ? p.w[0][1] : "";
    for (const ch of p.w) { if (ch[0] <= tick) w = ch[1]; else break; }
    return w || "";
  }

  // ---------- drawing ------------------------------------------------------
  const roleCol = (r) => r === "tm" ? C.tm : r === "en" ? C.en : C.me;
  const mixA = (hex, a) => {
    const h = (hex || "").replace("#", "");
    if (h.length !== 6) return "transparent";
    const n = parseInt(h, 16);
    const s = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return "rgb" + "a(" + s + "," + g + "," + b + "," + a + ")";
  };

  function drawPanelBg() {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, W, H);
    const n = twoPanels ? 2 : 1;
    for (let i = 0; i < n; i++) {
      const p = panels[i];
      if (!p) continue;
      if (d.radar && imgs[i] && imgs[i].complete && imgs[i].naturalWidth) {
        ctx.drawImage(imgs[i], p.ox + p.dx, p.oy + p.dy,
          d.radar.size * p.fit * p.z, d.radar.size * p.fit * p.z);
      } else if (!d.radar) {
        ctx.strokeStyle = C.grid;
        ctx.lineWidth = 1;
        const g = d.gridb;
        const stepU = 500;
        for (let gx = Math.ceil(g.minx / stepU) * stepU; gx <= g.maxx; gx += stepU) {
          const a = toPx(0, gx, g.miny), b = toPx(0, gx, g.maxy);
          ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        }
        for (let gy = Math.ceil(g.miny / stepU) * stepU; gy <= g.maxy; gy += stepU) {
          const a = toPx(0, g.minx, gy), b = toPx(0, g.maxx, gy);
          ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        }
      }
      if (twoPanels && d.lab["lvl" + i]) {
        ctx.fillStyle = C.text;
        ctx.font = "600 11px inherit";
        ctx.textAlign = "left";
        ctx.fillText(d.lab["lvl" + i], p.ox + p.dx + 8, p.oy + p.dy + 16);
      }
    }
  }

  function drawPlayers() {
    const rad = Math.max(9, Math.min(14, W / 70));
    ctx.textAlign = "center";
    for (const p of P) {
      const s = state(p, t);
      if (!s || s.x == null || s.y == null) continue;
      const px0 = toPx(s.lvl, s.x, s.y);
      const px = px0[0], py = px0[1];
      const col = roleCol(p.role);
      const alive = s.al;
      ctx.globalAlpha = alive ? 1 : 0.55;
      if (alive) {
        if (s.fl > 0.2) {
          ctx.beginPath();
          ctx.arc(px, py, rad + 4, 0, Math.PI * 2);
          ctx.strokeStyle = mixA(C.flash, Math.min(0.9, 0.3 + s.fl / 4));
          ctx.lineWidth = 3;
          ctx.stroke();
        }
        ctx.beginPath();
        ctx.arc(px, py, rad, 0, Math.PI * 2);
        ctx.fillStyle = col;
        ctx.fill();
        if (p.role === "me") {
          ctx.beginPath();
          ctx.arc(px, py, rad + 2.5, 0, Math.PI * 2);
          ctx.strokeStyle = C.ring;
          ctx.lineWidth = 2;
          ctx.stroke();
        }
        if (s.hp != null && s.hp > 0 && s.hp <= 100) {
          ctx.fillStyle = C.hpText;
          ctx.font = "700 " + Math.round(rad * 0.85) + "px inherit";
          ctx.fillText(String(s.hp), px, py + rad * 0.3);
        }
        const a = (s.yaw == null ? 0 : s.yaw) * Math.PI / 180;
        const dx = Math.cos(a), dy = -Math.sin(a);
        const x0 = px + dx * (rad + 2), y0 = py + dy * (rad + 2);
        const x1 = px + dx * (rad + 11), y1 = py + dy * (rad + 11);
        ctx.strokeStyle = col;
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
        const hx = px + dx * (rad + 14.5), hy = py + dy * (rad + 14.5);
        const ox = -dy, oy = dx;
        ctx.beginPath();
        ctx.moveTo(hx, hy);
        ctx.lineTo(hx - dx * 4 + ox * 3, hy - dy * 4 + oy * 3);
        ctx.lineTo(hx - dx * 4 - ox * 3, hy - dy * 4 - oy * 3);
        ctx.closePath();
        ctx.fillStyle = col;
        ctx.fill();
      } else {
        ctx.strokeStyle = C.dead || "#777";
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.moveTo(px - rad * 0.8, py - rad * 0.8);
        ctx.lineTo(px + rad * 0.8, py + rad * 0.8);
        ctx.moveTo(px + rad * 0.8, py - rad * 0.8);
        ctx.lineTo(px - rad * 0.8, py + rad * 0.8);
        ctx.stroke();
      }
      ctx.font = "600 10px inherit";
      ctx.fillStyle = alive ? C.text : C.dead;
      ctx.fillText(p.name, px, py - rad - 6);
      const w = weaponAt(p, t);
      if (alive && w) {
        ctx.font = "9px inherit";
        ctx.fillStyle = C.text2;
        ctx.fillText(w, px, py + rad + 11);
      }
      ctx.globalAlpha = 1;
    }
  }

  function drawEvents() {
    const tr = d.tr;
    const fadeShot = 0.22 * tr, fadeHit = 0.45 * tr, fadeKill = 1.2 * tr;
    for (const sh of d.shots) {
      const tk = sh[0], pi = sh[1];
      if (pi < 0 || tk > t || t - tk > fadeShot) continue;
      const p = P[pi], s = state(p, tk);
      if (!s || s.x == null) continue;
      const a = (s.yaw == null ? 0 : s.yaw) * Math.PI / 180;
      const mine = p.role === "me";
      const len = 700;  // world units
      const x2 = s.x + Math.cos(a) * len, y2 = s.y + +Math.sin(a) * len;
      const a0 = toPx(s.lvl, s.x, s.y);
      const b0 = toPx(s.lvl, x2, y2);
      const k = 1 - (t - tk) / fadeShot;
      ctx.globalAlpha = (mine ? 0.95 : 0.5) * k;
      ctx.strokeStyle = roleCol(p.role);
      ctx.lineWidth = mine ? 2.2 : 1.4;
      ctx.beginPath(); ctx.moveTo(a0[0], a0[1]); ctx.lineTo(b0[0], b0[1]); ctx.stroke();
      ctx.globalAlpha = 1;
    }
    for (const h of d.hits) {
      const tk = h[0], vi = h[2];
      if (tk > t || t - tk > fadeHit || vi < 0) continue;
      const v = P[vi], s = state(v, tk);
      if (!s || s.x == null) continue;
      const p0 = toPx(s.lvl, s.x, s.y);
      const px = p0[0], py = p0[1];
      const k = 1 - (t - tk) / fadeHit;
      ctx.globalAlpha = k;
      ctx.strokeStyle = C.hit;
      ctx.lineWidth = 2;
      const r = 6 + 5 * k;
      ctx.beginPath();  // 4-point star
      ctx.moveTo(px, py - r); ctx.lineTo(px + r * 0.35, py - r * 0.35);
      ctx.lineTo(px + r, py); ctx.lineTo(px + r * 0.35, py + r * 0.35);
      ctx.lineTo(px, py + r); ctx.lineTo(px - r * 0.35, py + r * 0.35);
      ctx.lineTo(px - r, py); ctx.lineTo(px - r * 0.35, py - r * 0.35);
      ctx.closePath(); ctx.stroke();
      ctx.globalAlpha = 1;
    }
    for (const k of d.kills) {
      const tk = k[0], vi = k[2];
      if (tk > t || vi < 0) continue;
      const v = P[vi], s = state(v, tk);
      if (!s || s.x == null) continue;
      const p0 = toPx(s.lvl, s.x, s.y);
      const px = p0[0], py = p0[1];
      const age = t - tk;
      if (age <= fadeKill) {
        const k2 = 1 - age / fadeKill;
        ctx.globalAlpha = k2;
        ctx.strokeStyle = C.dead;
        ctx.lineWidth = 3;
        const r = 12;
        ctx.beginPath();
        ctx.moveTo(px - r, py - r); ctx.lineTo(px + r, py + r);
        ctx.moveTo(px + r, py - r); ctx.lineTo(px - r, py + r);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    }
    for (const mk of d.marks) {
      const tk = mk[0], res = mk[1];
      if (tk > t || t - tk > tr) continue;
      const p = P[d.myIdx];
      if (!p) break;
      const s = state(p, tk);
      if (!s || s.x == null) continue;
      const p0 = toPx(s.lvl, s.x, s.y);
      const k = 1 - (t - tk) / tr;
      ctx.globalAlpha = k;
      ctx.strokeStyle = res === "kill" ? C.good : C.bad;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(p0[0], p0[1], 16 + 14 * (1 - k), 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }

  function drawFeed() {
    feed.replaceChildren();
    const recent = d.kills.filter((k) => k[0] <= t).slice(-5).reverse();
    for (const k of recent) {
      const tk = k[0], ai = k[1], vi = k[2], w = k[3], hs = k[4];
      const a = ai >= 0 ? P[ai] : null, v = vi >= 0 ? P[vi] : null;
      const row = el("div", "rr-fe" + ((v && v.role === "me") || (a && a.role === "me") ? " mine" : ""));
      const an = el("b");
      an.textContent = a ? a.name : "?";
      an.style.color = a ? roleCol(a.role) : "";
      row.appendChild(an);
      const wEl = el("span", "rr-w");
      wEl.textContent = (w ? "[" + w + "]" : "") + (hs ? " \\u25CE" : "");
      row.appendChild(wEl);
      const vn = el("b");
      vn.textContent = v ? v.name : "?";
      vn.style.color = v ? roleCol(v.role) : "";
      row.appendChild(vn);
      feed.appendChild(row);
    }
  }

  function drawKeys() {
    const act = activePanels();
    for (const kp of keyPanels) {
      if (act.indexOf(kp) < 0) continue;
      const n = kp.p.x.length;
      const j = Math.max(0, Math.min(Math.round((t - d.t0) / d.step), n - 1));
      for (const k of d.keys) {
        const arr = kp.p.keys[k];
        kp.boxes[k].classList.toggle("on", !!(arr && arr[j]));
      }
    }
  }

  // ---------- legend + howto ----------------------------------------------
  const leg = el("div", "rr-legend");
  const dot = (col) => {
    const s = el("span");
    const d2 = el("i", "rr-dot");
    d2.style.background = col;
    s.appendChild(d2);
    return s;
  };
  const line = (col) => {
    const s = el("span");
    const d2 = el("i", "rr-legline");
    d2.style.background = col;
    s.appendChild(d2);
    return s;
  };
  const l1 = dot(C.me); l1.append(" " + (d.lab.you || "you"));
  const l2 = dot(C.tm); l2.append(" " + (d.lab.tm || "teammates"));
  const l3 = dot(C.en); l3.append(" " + (d.lab.en || "enemies"));
  const l4 = el("span", null, "\\u2715 " + (d.lab.dead || "dead"));
  const l5 = el("span", null, "\\u2014 " + (d.lab.shot || "shot"));
  const l6 = el("span", null, "\\u2726 " + (d.lab.hit || "hit"));
  const l7 = line(C.ct); l7.append(" " + (d.lab.leg_ct || "CT kill"));
  const l8 = line(C.t); l8.append(" " + (d.lab.leg_t || "T kill"));
  leg.append(l1, l2, l3, l4, l5, l6, l7, l8);
  box.appendChild(leg);
  if (d.lab.howto) {
    const cap = el("div", "rr-howto");
    cap.style.color = "var(--text-secondary)";
    cap.style.fontSize = "var(--font-size-xs)";
    cap.style.paddingTop = "0.25rem";
    cap.textContent = d.lab.howto;
    box.appendChild(cap);
  }

  // ---------- sync / draw --------------------------------------------------
  function applyHid() {
    grid.classList.toggle("nolf", !!S.hid);
    plBtn.classList.toggle("on", !S.hid);
    plBtn.title = S.hid ? (d.lab.players_show || "show") : (d.lab.players_hide || "hide");
    plBtn.setAttribute("aria-label", plBtn.title);
  }

  function sync() {
    playBtn.textContent = playing ? "\\u23F8" : "\\u25B6";
    playBtn.title = playing ? (d.lab.pause || "pause") : (d.lab.play || "play");
    playBtn.setAttribute("aria-label", playBtn.title);
    spdBtns.forEach((b) => b.classList.toggle("on", Number(b.dataset.spd) === speed));
    const hasDuel = sel >= 0 && !!duelAt(sel);
    exitBtn.style.display = hasDuel ? "" : "none";
    lockChip.style.display = hasDuel ? "" : "none";
    segs.forEach((s, i) => s.classList.toggle("sel", sel === i));
    cardEls.forEach((c, i) => c.classList.toggle("cur", sel === i));
    const z = duelAt(sel);
    if (z) {
      const aP = pct(z.a) * 100, bP = pct(z.b) * 100;
      dimA.style.display = ""; dimB.style.display = "";
      dimA.style.left = "0%";
      dimA.style.width = Math.max(0, aP) + "%";
      dimB.style.left = bP + "%";
      dimB.style.width = Math.max(0, 100 - bP) + "%";
      track.setAttribute("aria-valuemin", z.a);
      track.setAttribute("aria-valuemax", z.b);
    } else {
      dimA.style.display = "none"; dimB.style.display = "none";
      track.setAttribute("aria-valuemin", d.t0);
      track.setAttribute("aria-valuemax", d.t1);
    }
    const act = activePanels();
    keyWrap.classList.toggle("hid", !act.length);
    keyPanels.forEach((kp) => kp.el.classList.toggle("on", act.indexOf(kp) >= 0));
    track.setAttribute("aria-valuenow", Math.round(t));
  }

  function draw() {
    drawPanelBg();
    drawPlayers();
    drawEvents();
    drawFeed();
    drawKeys();
    updateLeft();
    updateCards();
    timeLab.textContent = "\\u23F1 " + fmtT((t - d.t0) / d.tr)
      + " / " + fmtT((winEnd() - d.t0) / d.tr);
    const f = Math.max(0, Math.min(1, (t - d.t0) / (d.t1 - d.t0)));
    ph.style.left = (f * 100) + "%";
    track.setAttribute("aria-valuenow", Math.round(t));
    S.st = {
      t: t, playing: playing, sel: sel,
      z: panels.map((p) => (p ? p.z : 1)),
      dx: panels.map((p) => (p ? Math.round(p.dx) : 0)),
    };
  }
  function updateCards() {
    cardEls.forEach((c, i) => {
      c.classList.toggle("up", !!(d.duels[i] && d.duels[i].b > t));
    });
  }

  // ---------- init ----------------------------------------------------------
  layout();
  applyHid();
  buildLeft();
  buildCards();
  buildRounds();
  sync();
  draw();
  for (const im of imgs) {
    if (im) im.addEventListener("load", () => draw());
  }
  // re-layout only on a real width change: the canvas height itself
  // depends on W, and reacting to our own height edits would loop the
  // ResizeObserver ("loop completed with undelivered notifications")
  const ro = new ResizeObserver(() => {
    const w = wrap.getBoundingClientRect().width;
    if (Math.abs(w - lastW) < 1) return;
    layout();
    draw();
  });
  ro.observe(wrap);
}
"""
