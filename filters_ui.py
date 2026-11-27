"""The global filter bar: URL state, presets, the controls.

One compact row: rounds (presets or hand-picked rounds) and weapons (kill-feed icons) sit
in popovers, side and distance inline. The native controls render only inside the match
view, so they carry persist_state="session"; the chip selections (flt_weapons,
flt_rounds) are plain session keys written by the chips' callbacks."""

import streamlit as st

import core
import i18n
import state
from ui import chips, icons
from ui.format import fmt_result

# the shareable view in plain query params; lists are comma-joined
_URL_KEYS = {
    "lang": "lang",
    "me": "me",
    "weapons": "flt_weapons",
    "classes": "flt_classes",
    "rounds": "flt_rounds",
    "preset": "flt_preset",
    "side": "flt_side",
    "res": "flt_res",
    "dist": "flt_dist",
}
_URL_LISTS = ("weapons", "classes", "rounds")


def load_url_state() -> None:
    """Restore a shared view into session_state (once per session)."""
    if "url_state_loaded" in st.session_state:
        return
    st.session_state["url_state_loaded"] = True
    for k, sk in _URL_KEYS.items():
        v = st.query_params.get(k)
        if v and (k != "lang" or v in i18n.LANGS):
            st.session_state[sk] = v.split(",") if k in _URL_LISTS else v


_DIST_KEYS = ("all", "close", "mid", "long")
_DIST_RANGES = {
    "close": (0.0, core.SAFE_DIST),
    "mid": (core.SAFE_DIST, core.STRICT_DIST),
    "long": (core.STRICT_DIST, float("inf")),
}


def _filter_data(ctx):
    """What this demo offers the filter panel (weapons, classes, rounds,
    halves, and which controls the current tab needs)."""
    demo, shots = ctx.demo, ctx.shots
    # the player's own guns first (most shots first), the rest alphabetically
    mine = shots.loc[shots["sid"] == ctx.me, "weapon"].astype(str).value_counts()
    demo_weapons = sorted(
        shots["weapon"].astype(str).unique(), key=lambda w: (-int(mine.get(w, 0)), w)
    )
    present_classes = sorted({core.weapon_class(w) for w in demo_weapons})
    n_rounds = int(shots["round"].max()) if len(shots) else 0
    has_sides = bool(demo.sides)
    # [] falls back to the MR12 assumption everywhere below
    halves = core.halves_from_sides(demo.sides, n_rounds)
    # the panel renders above the tabs, but the tab is already in session_state
    tab_now = state.MATCH_TAB.get()
    show_result = tab_now == "duels"
    # the replay shows the whole round, so none of the analysis filters apply
    show_panel = tab_now != "replay"
    return demo_weapons, present_classes, n_rounds, has_sides, halves, show_result, show_panel


def _preset_options(n_rounds, halves) -> list[str]:
    """Presets this match can offer (sides decide, else MR12 heuristics)."""
    # a preset is offered only when the match has that part (Custom: 2+ rounds)
    preset_opts = ["all"] + (["pistols"] if n_rounds >= 2 else [])
    if len(halves) >= 2:
        preset_opts = ["all", "first", "second"] + preset_opts[1:]
        if len(halves) >= 3:
            preset_opts.insert(3, "ot")
    elif n_rounds >= 2 * core.ROUNDS_PER_HALF + 1:
        preset_opts = ["all", "first", "second", "ot", "pistols"]
    elif n_rounds >= core.ROUNDS_PER_HALF + 1:
        preset_opts = ["all", "first", "second", "pistols"]
    if n_rounds >= 2:
        preset_opts.append("custom")
    return preset_opts


def _drop_stale_selections(demo_weapons, present_classes):
    """Drop selections this demo does not have (from another file or from
    a shared URL of another match)."""
    if "flt_weapons" in st.session_state:
        st.session_state["flt_weapons"] = [
            w for w in st.session_state["flt_weapons"] if w in set(demo_weapons)
        ]
    if "flt_classes" in st.session_state:
        st.session_state["flt_classes"] = [
            c for c in st.session_state["flt_classes"] if c in set(present_classes)
        ]


def _clamp_filter_state(preset_opts, n_rounds):
    """Unknown values (a hand-edited link, another demo) reset to their default, the round
    range is clamped (st.slider raises when min == max)."""
    if st.session_state.get("flt_res") not in ("both", "kill", "death"):
        st.session_state["flt_res"] = "both"
    if st.session_state.get("flt_dist") not in _DIST_KEYS:
        st.session_state["flt_dist"] = "all"
    if st.session_state.get("flt_preset") not in preset_opts:
        st.session_state["flt_preset"] = "all"
    if st.session_state.get("flt_side") not in ("all", "T", "CT"):
        st.session_state["flt_side"] = "all"
    # the hand-picked rounds: ints of this match only (a link carries strings)
    picked = set()
    for v in st.session_state.get("flt_rounds") or []:
        try:
            if 1 <= int(v) <= n_rounds:
                picked.add(int(v))
        except (TypeError, ValueError):
            pass
    st.session_state["flt_rounds"] = sorted(picked)


def _preset_label(t, p: str, n_rounds: int, halves) -> str:
    """Preset option label with the actual round numbers of this match."""
    h = halves or []
    if p == "first":
        lo, hi = h[0] if h else (1, core.ROUNDS_PER_HALF)
        return t("fp_first", lo=lo, hi=hi)
    if p == "second":
        lo, hi = (
            h[1]
            if len(h) > 1
            else (core.ROUNDS_PER_HALF + 1, 2 * core.ROUNDS_PER_HALF)
        )
        return t("fp_second", lo=lo, hi=hi)
    if p == "ot":
        lo = h[2][0] if len(h) > 2 else 2 * core.ROUNDS_PER_HALF + 1
        return t("fp_ot", lo=lo)
    if p == "pistols":
        rounds = sorted(core.pistol_rounds(n_rounds, halves or None))
        shown = ", ".join(map(str, rounds[:4])) + ("…" if len(rounds) > 4 else "")
        return t("fp_pistols", rounds=shown)
    if p == "custom":
        return t("flt_custom")
    return t("fp_all")


def _reset_filters(n_rounds):
    def _do_reset():
        st.session_state["flt_weapons"] = []
        st.session_state["flt_classes"] = []
        st.session_state["flt_res"] = "both"
        st.session_state["flt_side"] = "all"
        st.session_state["flt_dist"] = "all"
        st.session_state["flt_rounds"] = []
        st.session_state["flt_preset"] = "all"

    return _do_reset


def _dist_label(t, v: str) -> str:
    return t("flt_dist_any") if v == "all" else t(f"flt_dist_{v}")


def _any_active(has_sides, show_result) -> bool:
    """Is any filter of this tab narrowing the view?"""
    ss = st.session_state
    return bool(
        ss.get("flt_weapons")
        or ss.get("flt_classes")
        or (has_sides and ss.get("flt_side") != "all")
        or ss.get("flt_preset") != "all"
        or ss.get("flt_dist") != "all"
        or (show_result and ss.get("flt_res") != "both")
    )


def _weapons_label(t) -> str:
    """The weapons button names its selection: the one pick, else how many."""
    picks = [t(f"wc_{c}") for c in st.session_state.get("flt_classes") or []]
    picks += [icons.weapon_label(w) for w in st.session_state.get("flt_weapons") or []]
    if not picks:
        return t("flt_weapons")
    return f"{t('flt_weapons')}: {picks[0] if len(picks) == 1 else len(picks)}"


def _rounds_label(t, preset, n_rounds, halves) -> str:
    """The rounds button names its selection: the preset, or the picked rounds."""
    if preset == "custom":
        picked = st.session_state.get("flt_rounds") or []
        if not picked:
            return t("fp_all")
        shown = ", ".join(map(str, picked[:4])) + ("…" if len(picked) > 4 else "")
        return f"{t('flt_rounds')}: {shown}"
    return _preset_label(t, preset, n_rounds, halves)


def _round_kwargs(preset, n_rounds, halves) -> dict:
    """The round selection a preset means ({} when it covers everything)."""
    round_kwargs: dict = {}
    if preset == "pistols":
        round_kwargs["rounds_set"] = core.pistol_rounds(n_rounds, halves or None)
    elif preset == "custom" and n_rounds >= 2:
        picked = set(st.session_state.get("flt_rounds") or [])
        if picked and len(picked) < n_rounds:
            round_kwargs["rounds_set"] = picked
    elif preset in ("first", "second", "ot"):
        # from the preset itself: a URL-restored one never fired its on_change
        rv = core.round_bounds(n_rounds, preset, halves or None)
        if rv != (1, n_rounds):
            round_kwargs["rounds"] = rv
    return round_kwargs


def _resolve_filters(ctx, preset, n_rounds, halves, has_sides, show_result):
    """core.Filters from the session state (the pages below read the filtered frames)."""
    filters = core.Filters(
        weapons=set(st.session_state.get("flt_weapons") or []) or None,
        classes=set(st.session_state.get("flt_classes") or []) or None,
        side=(
            st.session_state.get("flt_side")
            if has_sides and st.session_state.get("flt_side") != "all"
            else None
        ),
        dist=_DIST_RANGES.get(st.session_state.get("flt_dist", "all")),
        **_round_kwargs(preset, n_rounds, halves),
    )
    shots_f, duels_f, hurts_f = core.apply_filters(
        ctx.shots, ctx.duels_all, ctx.hurts, filters
    )
    # the kill/death filter applies only where its control is
    res_sel = set()
    if show_result and st.session_state.get("flt_res", "both") != "both":
        res_sel = {st.session_state["flt_res"]}
    return filters, shots_f, duels_f, hurts_f, res_sel


def _set_weapons(demo_weapons):
    def _cb(picked: set) -> None:
        st.session_state["flt_weapons"] = [w for w in demo_weapons if w in picked]

    return _cb


def _set_rounds(picked: set) -> None:
    st.session_state["flt_rounds"] = sorted(int(r) for r in picked)


def _round_chips(t, demo, me, n_rounds, halves) -> None:
    """Every round of the match as a chip, tinted by the player's side, a divider at
    each halftime."""
    starts = {lo for lo, _ in (halves or [])[1:]}
    items = []
    for r in range(1, n_rounds + 1):
        side = demo.side_at(r, me) if me else "?"
        tone = {"T": "t", "CT": "ct"}.get(side)
        items.append({
            "id": str(r),
            "text": str(r),
            "tip": t("flt_round_tip", n=r, side=side if tone else "?"),
            "tone": tone,
            "gap": r in starts,
        })
    chips.chips(
        items,
        map(str, st.session_state.get("flt_rounds") or []),
        key="flt_rounds_pick",
        on_change=_set_rounds,
        label=t("flt_rounds"),
    )


def _weapon_chips(t, demo_weapons) -> None:
    """The demo's weapons as kill-feed silhouettes (the name is the tooltip)."""
    keys = {w: icons.weapon_key(w) for w in demo_weapons}
    items = [
        {"id": w, "text": icons.weapon_label(w), "tip": icons.weapon_label(w), "ico": keys[w]}
        for w in demo_weapons
    ]
    chips.chips(
        items,
        st.session_state.get("flt_weapons") or [],
        key="flt_weapons_pick",
        on_change=_set_weapons(demo_weapons),
        icons=icons.payload(k for k in keys.values() if k),
        label=t("flt_weapons"),
    )


def _controls(ctx, demo_weapons, present_classes, preset_opts, has_sides, n_rounds, halves,
              active):
    """One row: every control shows its own value, labels live in the tooltips."""
    t = ctx.t
    preset = st.session_state.get("flt_preset", "all")
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        # the popover bodies stay mounted while closed, so the widgets keep their state
        with st.popover(
            _rounds_label(t, preset, n_rounds, halves),
            icon=":material/flag:",
            key="flt_rounds_pop",
            help=t("flt_rounds_help"),
        ):
            st.pills(
                t("flt_rounds"),
                preset_opts,
                format_func=lambda p: _preset_label(t, p, n_rounds, halves),
                key="flt_preset",
                required=True,
                label_visibility="collapsed",
                persist_state="session",
            )
            if preset == "custom" and n_rounds >= 2:
                st.caption(t("flt_custom_hint"))
                _round_chips(t, ctx.demo, ctx.me, n_rounds, halves)
        if has_sides:
            st.segmented_control(
                t("flt_side"),
                ["all", "T", "CT"],
                format_func=lambda v: t("flt_side_both") if v == "all" else v,
                key="flt_side",
                help=t("flt_side_help"),
                required=True,
                label_visibility="collapsed",
                persist_state="session",
            )
        st.selectbox(
            t("flt_dist"),
            list(_DIST_KEYS),
            format_func=lambda v: _dist_label(t, v),
            key="flt_dist",
            help=t("flt_dist_help"),
            label_visibility="collapsed",
            persist_state="session",
            width=190,
        )
        with st.popover(
            _weapons_label(t),
            icon=":material/swords:",
            key="flt_weapons_pop",
            help=t("flt_weapons_help"),
        ):
            st.pills(
                t("flt_classes"),
                present_classes,
                selection_mode="multi",
                format_func=lambda c: t(f"wc_{c}"),
                key="flt_classes",
                help=t("flt_classes_help"),
                persist_state="session",
            )
            st.caption(t("flt_weapons"))
            _weapon_chips(t, demo_weapons)
        # always there: the row must not jump when a filter is set
        st.button(
            t("flt_reset"),
            on_click=_reset_filters(n_rounds),
            key="flt_reset",
            icon=":material/filter_alt_off:",
            type="tertiary",
            disabled=not active,
            width="content",
        )


def result_control(t) -> None:
    """The kill/death filter: the Duels page draws it over its table."""
    st.segmented_control(
        t("flt_res"),
        ["both", "kill", "death"],
        format_func=lambda v: t("res_both") if v == "both" else fmt_result(v),
        key="flt_res",
        label_visibility="collapsed",
        required=True,
        persist_state="session",
    )


def _sync_filter_url(me, preset):
    """Keep the shareable query params in step (defaults drop off the URL)."""
    want = {
        k: ",".join(map(str, v)) if k in _URL_LISTS else v
        for k, sk in _URL_KEYS.items()
        if (v := st.session_state.get(sk)) and v not in ("all", "both")
    }
    if preset != "custom":
        want.pop("rounds", None)
    if want.keys() <= {"lang", "me"}:
        want = {}  # nothing is filtered: a clean URL
    elif me:
        want["me"] = str(me)
    for k in _URL_KEYS:
        if k not in want:
            st.query_params.pop(k, None)
        elif st.query_params.get(k) != want[k]:
            st.query_params[k] = want[k]


def render(ctx) -> None:
    """The filter bar: resolve, render, sync the URL. The replay tab shows none."""
    (demo_weapons, present_classes, n_rounds, has_sides, halves,
     show_result, show_panel) = _filter_data(ctx)

    preset_opts = _preset_options(n_rounds, halves)
    _drop_stale_selections(demo_weapons, present_classes)
    _clamp_filter_state(preset_opts, n_rounds)

    preset = st.session_state.get("flt_preset", "all")
    filters, shots_f, duels_f, hurts_f, res_sel = _resolve_filters(
        ctx, preset, n_rounds, halves, has_sides, show_result
    )

    if show_panel:
        _controls(
            ctx, demo_weapons, present_classes, preset_opts, has_sides, n_rounds, halves,
            _any_active(has_sides, show_result),
        )

    _sync_filter_url(ctx.me, preset)
    ctx.filters = filters
    ctx.shots_f = shots_f
    ctx.duels_f = duels_f
    ctx.hurts_f = hurts_f
    ctx.res_sel = res_sel