export default function (component) {
  const { data, parentElement } = component;
  const root = parentElement;
  let host = root.querySelector(".aa-meters-host");
  if (!host) {
    host = el("div", "aa-meters-host");
    root.appendChild(host);
  }
  const wrap = el("div", "aa-meters");
  if (data.title) wrap.appendChild(el("div", "aa-mt-title", data.title));
  const grid = el("div", "aa-mgrid");
  (data.tiles || []).forEach((tl) => {
    const card = el("div", "aa-m" + (tl.f != null || tl.m != null ? " has-bar" : ""));
    const head = el("div", "aa-m-l", tl.l);
    if (tl.h) {
      const q = el("span", "aa-m-help", "?");
      q.title = tl.h;
      q.setAttribute("aria-label", tl.h);
      head.appendChild(q);
    }
    card.appendChild(head);
    if (tl.w > 1) card.style.gridColumn = "span " + tl.w;
    if (tl.p && tl.p.length) {
      // a group tile: related counts side by side, value over its label
      const row = el("div", "aa-m-parts");
      tl.p.forEach((pt) => {
        const cell = el("div", "aa-m-part");
        cell.append(el("div", "aa-m-pv", pt[1]), el("div", "aa-m-pl", pt[0]));
        row.appendChild(cell);
      });
      card.appendChild(row);
      grid.appendChild(card);
      return;
    }
    const val = el("div", "aa-m-v", tl.v);
    const lvl = tl.q != null ? gradeLevel(tl.q) : null;
    // the value keeps the text colour at "ok": only bad / good are coloured
    const col = lvl && lvl !== "ok" ? gradeColor(tl.q) : null;
    if (col) val.style.color = col;
    card.appendChild(val);
    if (tl.m != null) {
      const bar = el("div", "aa-m-bar");
      const fill = el("div", "aa-m-fill");
      fill.style.width = ((tl.f || 0) * 100).toFixed(1) + "%";
      fill.style.background = col || "var(--text-secondary)";
      bar.appendChild(fill);
      const mk = el("div", "aa-m-mark");
      mk.style.left = (tl.m * 100).toFixed(1) + "%";
      bar.appendChild(mk);
      card.appendChild(bar);
    }
    const foot = [tl.n, tl.s].filter(Boolean).join(" · ");
    if (foot) card.appendChild(el("div", "aa-m-n", foot));
    grid.appendChild(card);
  });
  wrap.appendChild(grid);
  host.replaceChildren(wrap);
}
