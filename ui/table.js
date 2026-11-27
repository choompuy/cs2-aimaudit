export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement;
  const S = root.__aa || (root.__aa = { sig: "", col: -1, dir: 0, top: 0, left: 0 });
  let host = root.querySelector(".aa-host");
  if (!host) {
    host = document.createElement("div");
    host.className = "aa-host";
    root.appendChild(host);
  }
  const sig = data.cols.map((c) => c.k).join("|") + "#" + (data.chk ? "1" : "0");
  if (sig !== S.sig) { S.sig = sig; S.col = -1; S.dir = 0; S.top = 0; S.left = 0; }
  // the checkbox selection: S.checked is the live set, re-synced from the
  // payload whenever the app-side state changed behind our back (a rerun
  // the JS toggle itself triggered lands here with the same set - no reset)
  const checkedSig = (data.checked || []).join("\n");
  if (S.checkedSig !== checkedSig) {
    S.checkedSig = checkedSig;
    S.checked = new Set(data.checked || []);
  } else if (!S.checked) {
    S.checked = new Set(data.checked || []);
  }
  const reportChecked = () => setTriggerValue("checked", Array.from(S.checked).join("\n"));

  const firstDir = (c) => (c.f === "asc" ? 1 : -1);
  function ordered() {
    const rows = data.rows.slice();
    if (S.col < 0) return rows;
    const ci = S.col, dir = S.dir;
    rows.sort((a, b) => {
      const x = a.c[ci][0], y = b.c[ci][0];
      if (x == null && y == null) return a.i - b.i;
      if (x == null) return 1;
      if (y == null) return -1;
      const r = typeof x === "number" && typeof y === "number"
        ? x - y
        : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: "base" });
      return r === 0 ? a.i - b.i : r * dir;
    });
    return rows;
  }

  function onHeader(ci) {
    const c = data.cols[ci];
    if (S.col !== ci) { S.col = ci; S.dir = firstDir(c); }
    else if (S.dir === firstDir(c)) { S.dir = -S.dir; }
    else { S.col = -1; S.dir = 0; }
    draw();
  }

  function draw() {
    const prev = host.querySelector(".aa-wrap");
    if (prev) { S.top = prev.scrollTop; S.left = prev.scrollLeft; }
    const wrap = el("div", "aa-wrap");
    wrap.style.maxHeight = data.maxh + "px";
    if (!data.rows.length) {
      wrap.appendChild(el("div", "aa-empty", data.empty || ""));
      host.replaceChildren(wrap);
      return;
    }
    const table = el("table", "aa-t");
    const trh = el("tr");
    if (data.chk) {
      // the header checkbox toggles every checkable row at once; the
      // dash marks the in-between state
      const th = el("th", "chk");
      const all = el("input");
      all.type = "checkbox";
      const keys = data.rows.filter((r) => r.ck).map((r) => r.k);
      const on = keys.filter((k) => S.checked.has(k)).length;
      all.checked = keys.length > 0 && on === keys.length;
      all.indeterminate = on > 0 && on < keys.length;
      all.addEventListener("click", (e) => e.stopPropagation());
      all.addEventListener("change", () => {
        if (all.checked) keys.forEach((k) => S.checked.add(k));
        else keys.forEach((k) => S.checked.delete(k));
        reportChecked();
        draw();
      });
      th.appendChild(all);
      trh.appendChild(th);
    }
    data.cols.forEach((c, ci) => {
      const th = el("th", [c.b ? "bar" : "", c.s ? "stick" : ""].join(" ").trim());
      th.appendChild(el("span", "lbl", c.l));
      const active = S.col === ci;
      th.appendChild(el("span", "arrow", active && S.dir === 1 ? "\u25B2" : "\u25BC"));
      if (active) th.setAttribute("aria-sort", S.dir === 1 ? "ascending" : "descending");
      if (c.h) th.title = c.h;
      th.tabIndex = 0;
      th.addEventListener("click", () => onHeader(ci));
      th.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onHeader(ci); }
      });
      trh.appendChild(th);
    });
    const thead = el("thead");
    thead.appendChild(trh);
    const tbody = el("tbody");
    ordered().forEach((r) => {
      const cls = [];
      if (r.s) cls.push("stripe", "stripe-" + r.s);
      if (r.i === data.sel) cls.push("sel");
      if (data.click) cls.push("clk");
      const tr = el("tr", cls.join(" "));
      if (data.click) {
        tr.tabIndex = 0;
        const go = () => setTriggerValue("clicked", r.i);
        tr.addEventListener("click", go);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      }
      if (data.chk) {
        const td = el("td", "chk");
        if (r.ck) {
          const box = el("div", "cell chk");
          const cb = el("input");
          cb.type = "checkbox";
          cb.checked = S.checked.has(r.k);
          cb.addEventListener("click", (e) => e.stopPropagation());
          cb.addEventListener("change", () => {
            if (cb.checked) S.checked.add(r.k); else S.checked.delete(r.k);
            reportChecked();
            draw();
          });
          box.appendChild(cb);
          td.appendChild(box);
        }
        tr.appendChild(td);
      }
      r.c.forEach((cell, ci) => {
        const c = data.cols[ci];
        const td = el("td", [c.b ? "bar" : "", c.s ? "stick" : ""].join(" ").trim());
        const box = el("div", "cell tone-" + c.tn);
        if (cell[2] != null && cell[2] > 0.004) {
          const fill = el("div", "bar-fill");
          fill.style.width = (cell[2] * 100).toFixed(1) + "%";
          box.appendChild(fill);
        }
        const ico = c.t === "icon" && cell[0] && data.icons && data.icons[cell[0]];
        if (ico) {
          // icon only: the name is the tooltip (title + aria-label), never
          // visible text next to the silhouette
          const im = el("span", "ic");
          im.style.setProperty("--ico", 'url("' + ico[0] + '")');
          im.style.setProperty("--ar", String(ico[1] && ico[2] ? (ico[1] / ico[2]) : 3));
          im.title = cell[1];
          im.setAttribute("aria-label", cell[1]);
          im.setAttribute("role", "img");
          box.appendChild(im);
        } else if (c.t === "badge" && cell[1] !== "") {
          box.appendChild(el("span", "badge" + (cell[3] ? " tone-" + cell[3] : ""), cell[1]));
        } else {
          const val = el("span", "val", cell[1]);
          if (cell[4] != null) val.style.color = gradeColor(cell[4]);
          box.appendChild(val);
        }
        td.appendChild(box);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(thead);
    table.appendChild(tbody);
    wrap.appendChild(table);
    host.replaceChildren(wrap);
    wrap.scrollTop = S.top;
    wrap.scrollLeft = S.left;
  }
  draw();
}
