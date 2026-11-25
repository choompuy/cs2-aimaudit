export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement;
  // per-instance state: the keydown listener is added once and reads the
  // latest payload through S; S.cur tracks the selection optimistically so
  // several quick key presses before a rerun still all count
  const S = root.__aa || (root.__aa = { data: null, cur: 0, onKey: null });
  S.data = data;
  S.cur = data.sel;


  function go(i) {
    const n = S.data.n;
    if (!n) return;
    i = ((i % n) + n) % n;
    if (i !== S.cur) {
      S.cur = i;
      setTriggerValue("sel", i);
    }
  }

  function step(d) { go(S.cur + d); }

  function rstep(d) {
    const rs = S.data.rstarts;
    if (!rs.length) return;
    let g = rs.length - 1;
    for (let k = 0; k < rs.length; k++) {
      if (rs[k] <= S.cur) g = k;
      else break;
    }
    go(rs[(g + d + rs.length) % rs.length]);
  }

  function onKey(e) {
    // the strip is gone (page switched): remove the listener for good
    if (!root.isConnected) {
      document.removeEventListener("keydown", S.onKey);
      S.onKey = null;
      return;
    }
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const k = e.key;
    if (k !== "ArrowLeft" && k !== "ArrowRight" && k !== "Home" && k !== "End") return;
    const d = S.data;
    if (!d || !d.n) return;
    // yield inside anything that owns its arrow keys: text inputs,
    // selectboxes (combobox/listbox), data grids, editable areas
    const ae = document.activeElement;
    if (ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" ||
               ae.tagName === "SELECT" || ae.isContentEditable)) return;
    if (ae && ae.closest && ae.closest(
      '[role="combobox"],[role="listbox"],[role="grid"],[role="spinbutton"],[contenteditable]'
    )) return;
    e.preventDefault();
    if (k === "Home") { go(0); return; }
    if (k === "End") { go(d.n - 1); return; }
    if (e.shiftKey) { rstep(k === "ArrowLeft" ? -1 : 1); return; }
    step(k === "ArrowLeft" ? -1 : 1);
  }
  if (!S.onKey) {
    S.onKey = onKey;
    document.addEventListener("keydown", onKey);
  }

  // parentElement carries Streamlit's injected <style> (and anything
  // else it owns): never replaceChildren the root itself - only the
  // div this component owns, exactly like ui/table.py does
  let host = root.querySelector(".aa-strip-host");
  if (!host) {
    host = el("div", "aa-strip-host");
    root.appendChild(host);
  }
  const strip = el("div", "aa-strip");
  strip.setAttribute("role", "group");
  if (data.lab.duel) strip.setAttribute("aria-label", data.lab.duel);

  strip.appendChild(btn("aa-sbtn rr", "\u00AB", data.lab.rprev, () => rstep(-1)));
  strip.appendChild(btn("aa-sbtn pp", "\u25C0", data.lab.prev, () => step(-1)));
  const track = el("div", "aa-track");
  data.groups.forEach((g) => {
    const cur = g.items.some((it) => it.i === data.sel);
    track.appendChild(btn("aa-rd" + (cur ? " cur" : ""),
      g.r == null ? "?" : String(g.r), g.tip, () => go(g.items[0].i)));
    g.items.forEach((it) => {
      track.appendChild(btn(
        "aa-chip " + (it.w === "kill" ? "k" : "d") + (it.i === data.sel ? " cur" : ""),
        it.w === "kill" ? "\u2713" : "\u2715", it.tip, () => go(it.i)));
    });
  });
  strip.appendChild(track);
  strip.appendChild(btn("aa-sbtn nn", "\u25B6", data.lab.next, () => step(1)));
  strip.appendChild(btn("aa-sbtn ff", "\u00BB", data.lab.rnext, () => rstep(1)));
  const pos = el("div", "aa-pos", data.n ? (data.sel + 1) + " / " + data.n : "");
  if (data.lab.first) pos.title = data.lab.first;
  strip.appendChild(pos);
  host.replaceChildren(strip);

  // keep the selected chip centered in the track without scrolling the
  // page (scrollIntoView would drag every scrollable ancestor along)
  const cur = track.querySelector(".aa-chip.cur");
  if (cur) {
    track.scrollLeft = Math.max(
      0, cur.offsetLeft - (track.clientWidth - cur.offsetWidth) / 2
    );
  }
}
