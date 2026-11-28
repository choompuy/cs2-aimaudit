// A smooth mannequin, front view: the player faces you, so their left side is on your
// right. One solid shape per hit zone; the neck is too rare in demos (~0.2% of hits) to
// get one. Drawn in this order.
const HB_SHAPES = {
  right_leg: "M49,184 Q64,192 76,198 Q80,201 79,210 L78,250 Q77,258 77,266 Q79,284 75,302 L73,322 Q75,330 78,335 Q79,340 74,340 L57,340 Q51,340 53,335 Q57,329 59,322 L57,302 Q52,284 54,266 Q55,258 55,250 L51,214 Q48,198 49,184 Z",
  left_leg: "M111,184 Q96,192 84,198 Q80,201 81,210 L82,250 Q83,258 83,266 Q81,284 85,302 L87,322 Q85,330 82,335 Q81,340 86,340 L103,340 Q109,340 107,335 Q103,329 101,322 L103,302 Q108,284 106,266 Q105,258 105,250 L109,214 Q112,198 111,184 Z",
  stomach: "M54,137 L106,137 Q105,150 107,160 Q111,172 111,182 Q100,189 88,195 Q80,199 72,195 Q60,189 49,182 Q49,172 53,160 Q55,150 54,137 Z",
  chest: "M68,56 Q80,60 92,56 Q106,58 113,63 Q119,67 118,76 Q117,94 113,108 Q110,122 108,134 L52,134 Q50,122 47,108 Q43,94 42,76 Q41,67 47,63 Q54,58 68,56 Z",
  right_arm: "M42,60 Q28,60 25,74 Q22,86 22,100 L22,130 Q20,146 20,160 L20,178 Q17,188 19,197 Q22,205 28,204 Q35,202 35,194 L35,180 Q37,164 38,148 L39,132 Q40,116 40,100 L41,84 Q42,72 47,65 Z",
  left_arm: "M118,60 Q132,60 135,74 Q138,86 138,100 L138,130 Q140,146 140,160 L140,178 Q143,188 141,197 Q138,205 132,204 Q125,202 125,194 L125,180 Q123,164 122,148 L121,132 Q120,116 120,100 L119,84 Q118,72 113,65 Z",
  head: "M80,4 C92,4 99,14 99,27 C99,41 91,52 80,52 C69,52 61,41 61,27 C61,14 68,4 80,4 Z",
};
const HB_NS = "http://www.w3.org/2000/svg";

export default function (component) {
  const { data, parentElement } = component;
  let host = parentElement.querySelector(".aa-hb-host");
  if (!host) {
    host = el("div", "aa-hb-host");
    parentElement.appendChild(host);
  }
  const previews = data.previews || {};
  host.replaceChildren(card(data, data.view || {}));
  // a weapon row under the pointer (ui/table.js) shows that weapon until it leaves
  if (parentElement.__aaHb) window.removeEventListener("aa-hover", parentElement.__aaHb);
  parentElement.__aaHb = (e) => {
    const d = e.detail || {};
    if (!data.ch || d.ch !== data.ch) return;
    const v = d.k != null && previews[d.k] ? previews[d.k] : data.view || {};
    host.replaceChildren(card(data, v, d.k != null && !!previews[d.k]));
  };
  window.addEventListener("aa-hover", parentElement.__aaHb);
}

function card(data, view, preview) {
  const regions = view.regions || [];
  const total = regions.reduce((a, r) => a + (r.h || 0), 0);
  const max = regions.reduce((a, r) => Math.max(a, r.h || 0), 0);
  const byKey = Object.fromEntries(regions.map((r) => [r.k, r]));
  // share of the busiest region -> how far the fill leans to the accent
  const fillOf = (h) => {
    if (!h || !max) return "var(--surface-3)";
    const p = Math.round(22 + 78 * (h / max));
    return "color-mix(in srgb, var(--accent) " + p + "%, var(--surface-3))";
  };
  const pctOf = (h) => (total ? ((h / total) * 100).toFixed(1) + "%" : "0%");

  const wrap = el("div", "aa-hb" + (preview ? " preview" : ""));
  const head = el("div", "aa-hb-head");
  head.append(el("div", "aa-hb-title", data.title || ""));
  if (view.sub) head.append(el("div", "aa-hb-sub", view.sub));
  wrap.appendChild(head);

  const stats = el("div", "aa-hb-stats");
  (view.stats || []).forEach(([label, value]) => {
    const c = el("div", "aa-hb-stat");
    c.append(el("div", "aa-hb-sv", value), el("div", "aa-hb-sl", label));
    stats.appendChild(c);
  });

  const fig = el("div", "aa-hb-fig");
  const svg = document.createElementNS(HB_NS, "svg");
  svg.setAttribute("viewBox", "16 2 128 342");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", data.title || "");
  Object.entries(HB_SHAPES).forEach(([k, d]) => {
    const r = byKey[k];
    const p = document.createElementNS(HB_NS, "path");
    p.setAttribute("d", d);
    p.setAttribute("class", "aa-hb-part" + (r && r.h ? " hit" : ""));
    p.dataset.k = k;
    p.style.fill = fillOf(r ? r.h : 0);
    if (r) {
      const tt = document.createElementNS(HB_NS, "title");
      tt.textContent = r.n + ": " + r.h + " · " + r.d + " · " + pctOf(r.h);
      p.appendChild(tt);
    }
    svg.appendChild(p);
  });
  fig.appendChild(svg);

  const side = el("div", "aa-hb-side");
  side.appendChild(stats);
  const tbl = el("table", "aa-hb-tbl");
  const thead = el("tr");
  (data.cols || []).forEach((c, i) => thead.appendChild(el("th", i ? "num" : "", c)));
  tbl.appendChild(thead);
  regions.forEach((r) => {
    const tr = el("tr", r.h ? "" : "zero");
    tr.dataset.k = r.k;
    const name = el("td", "aa-hb-rn");
    const dot = el("span", "aa-hb-dot");
    dot.style.background = fillOf(r.h);
    name.append(dot, document.createTextNode(r.n));
    tr.append(name, el("td", "num", String(r.h)), el("td", "num", String(r.d)), el("td", "num pct", pctOf(r.h)));
    tbl.appendChild(tr);
  });
  side.appendChild(tbl);

  const bodyRow = el("div", "aa-hb-body");
  bodyRow.append(fig, side);
  wrap.appendChild(bodyRow);

  // hovering a region or its row lights up both
  const mark = (k, on) =>
    wrap.querySelectorAll('[data-k="' + k + '"]').forEach((n) => n.classList.toggle("hl", on));
  wrap.querySelectorAll("[data-k]").forEach((n) => {
    n.addEventListener("mouseenter", () => mark(n.dataset.k, true));
    n.addEventListener("mouseleave", () => mark(n.dataset.k, false));
  });
  return wrap;
}
