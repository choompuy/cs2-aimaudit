// Shared by table.js, chips.js, meters.js and roundrep.js: ui/component.py puts this file
// in front of each of them, so everything here is in their scope.
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const btn = (cls, text, title, fn) => {
  const b = el("button", cls, text);
  b.type = "button";
  if (title) { b.title = title; b.setAttribute("aria-label", title); }
  b.addEventListener("click", fn);
  return b;
};
const hexOf = (h) => { const v = parseInt(String(h || "").replace("#", ""), 16); return [(v >> 16) & 255, (v >> 8) & 255, v & 255]; };
const mixHex = (a, b, k) => {
  const p = hexOf(a), q = hexOf(b);
  return "#" + p.map((v, i) => Math.round(v * k + q[i] * (1 - k)).toString(16).padStart(2, "0")).join("");
};
const rgba = (hex, a) => (/^#?[0-9a-f]{6}$/i.test(hex || "") ? "rgba(" + hexOf(hex).join(",") + "," + a + ")" : "transparent");
// quality 0..1 -> one of three colours, never a gradient: below the norm (q < 0.6,
// the "ok" point of a [bad, ok, good] grade) is bad, at the "good" mark or past it is
// good, everything between keeps the plain text colour
const gradeLevel = (q) => (q < 0.6 ? "bad" : q >= 1 ? "good" : "ok");
const gradeColor = (q) => ({ bad: "var(--tier-bad)", good: "var(--tier-good)", ok: "var(--text-primary)" })[gradeLevel(q)];
