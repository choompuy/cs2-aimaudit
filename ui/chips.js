export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement;
  // S.sel is the live selection: several quick clicks before the rerun
  // arrives must all count, so it is re-synced only when the server's
  // selection actually changed
  const S = root.__aa || (root.__aa = { sig: null, sel: null });
  const sig = (data.sel || []).join("\n");
  if (S.sig !== sig || !S.sel) {
    S.sig = sig;
    S.sel = new Set(data.sel || []);
  }

  let host = root.querySelector(".aa-chips-host");
  if (!host) {
    host = el("div", "aa-chips-host");
    root.appendChild(host);
  }
  const wrap = el("div", "aa-chips");
  wrap.setAttribute("role", "group");
  if (data.label) wrap.setAttribute("aria-label", data.label);

  data.items.forEach((it) => {
    if (it.gap) wrap.appendChild(el("span", "aa-gap"));
    const on = S.sel.has(it.id);
    const ico = it.ico && data.icons && data.icons[it.ico];
    const b = btn(
      "aa-c" + (on ? " on" : "") + (it.tone ? " tone-" + it.tone : "") + (ico ? " has-ico" : ""),
      ico ? null : it.text,
      it.tip,
      () => {
        if (S.sel.has(it.id)) S.sel.delete(it.id);
        else S.sel.add(it.id);
        b.classList.toggle("on");
        b.setAttribute("aria-pressed", S.sel.has(it.id) ? "true" : "false");
        setTriggerValue("sel", data.items.map((x) => x.id).filter((id) => S.sel.has(id)).join("\n"));
      }
    );
    b.setAttribute("aria-pressed", on ? "true" : "false");
    if (ico) {
      const im = el("span", "ic");
      im.style.setProperty("--ico", 'url("' + ico[0] + '")');
      im.style.setProperty("--ar", String(ico[1] && ico[2] ? (ico[1] / ico[2]) : 3));
      b.appendChild(im);
    }
    wrap.appendChild(b);
  });
  host.replaceChildren(wrap);
}
