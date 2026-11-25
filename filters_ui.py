"""The global filter bar: URL state, presets, the summary row, the popover.

The controls render only inside the match view, so they carry persist_state="session"."""

import streamlit as st

import core
import i18n
import state
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
    demo_weapons = sorted(shots["weapon"].astype(str).unique())
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
    # no `value=` below: Streamlit warns about a default next to a session value
    if n_rounds >= 2:
        rv = st.session_state.get("flt_rounds") or (1, n_rounds)
        try:
            lo_r = max(1, min(int(rv[0]), n_rounds))
            hi_r = max(lo_r, min(int(rv[1]), n_rounds))
            st.session_state["flt_rounds"] = (lo_r, hi_r)
        except (TypeError, ValueError, IndexError):
            st.session_state["flt_rounds"] = (1, n_rounds)


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


def _on_preset_change(n_rounds, halves):
    """Segmented-control on_change: park the preset's round range."""

    def _apply_preset():
        p = st.session_state.get("flt_preset", "all")
        if p == "custom":
            return  # the slider owns the range; keep whatever it holds
        if p == "pistols":
            st.session_state["flt_rounds"] = (1, max(n_rounds, 1))
        else:
            st.session_state["flt_rounds"] = core.round_bounds(
                n_rounds, p, halves or None
            )
    return _apply_preset


def _slider_custom():
    st.session_state["flt_preset"] = "custom"


def _clear(key, value):
    """A chip click clears one filter; on_click runs before widgets."""

    def _fn():
        st.session_state[key] = value

    return _fn


def _clear_rounds(n_rounds):
    def _clr_rounds():
        st.session_state["flt_rounds"] = (1, n_rounds)
        st.session_state["flt_preset"] = "all"

    return _clr_rounds


def _reset_filters(n_rounds):
    def _do_reset():
        st.session_state["flt_weapons"] = []
        st.session_state["flt_classes"] = []
        st.session_state["flt_res"] = "both"
        st.session_state["flt_side"] = "all"
        st.session_state["flt_dist"] = "all"
        st.session_state["flt_rounds"] = (1, n_rounds)
        st.session_state["flt_preset"] = "all"

    return _do_reset


def _dist_label(t, v: str) -> str:
    return t("all") if v == "all" else t(f"flt_dist_{v}")


def _active_chips(t, has_sides, show_result, n_rounds, halves):
    """One dismissible chip per active filter: (label, clear callback)."""
    # one dismissible chip per active filter: (label, clear callback)
    _chips = []
    if st.session_state.get("flt_weapons"):
        _chips.append(
            (", ".join(st.session_state["flt_weapons"]), _clear("flt_weapons", []))
        )
    if st.session_state.get("flt_classes"):
        _chips.append(
            (
                ", ".join(t(f"wc_{c}") for c in st.session_state["flt_classes"]),
                _clear("flt_classes", []),
            )
        )
    if has_sides and st.session_state.get("flt_side") != "all":
        _chips.append((st.session_state["flt_side"], _clear("flt_side", "all")))
    if st.session_state.get("flt_preset", "all") == "custom":
        _rv = tuple(st.session_state.get("flt_rounds") or ())
        _chips.append(
            (
                t("flt_custom") + (f" {_rv[0]}–{_rv[1]}" if len(_rv) == 2 else ""),
                _clear_rounds(n_rounds),
            )
        )
    elif st.session_state.get("flt_preset", "all") != "all":
        _chips.append(
            (
                _preset_label(t, st.session_state["flt_preset"], n_rounds, halves),
                _clear_rounds(n_rounds),
            )
        )
    if st.session_state.get("flt_dist", "all") != "all":
        _chips.append(
            (_dist_label(t, st.session_state["flt_dist"]), _clear("flt_dist", "all"))
        )
    if show_result and st.session_state.get("flt_res", "both") != "both":
        _chips.append(
            (fmt_result(st.session_state["flt_res"]), _clear("flt_res", "both"))
        )
    return _chips


def _round_kwargs(preset, n_rounds, halves) -> dict:
    """The round selection a preset means ({} when it covers everything)."""
    round_kwargs: dict = {}
    if preset == "pistols":
        round_kwargs["rounds_set"] = core.pistol_rounds(n_rounds, halves or None)
    elif preset == "custom" and n_rounds >= 2:
        rv = tuple(st.session_state.get("flt_rounds") or (1, n_rounds))
        if rv != (1, n_rounds):
            round_kwargs["rounds"] = rv
    elif preset in ("first", "second", "ot"):
        # from the preset itself: a URL-restored one never fired its on_change
        rv = core.round_bounds(n_rounds, preset, halves or None)
        if rv != (1, n_rounds):
            round_kwargs["rounds"] = rv
    return round_kwargs


def _resolve_filters(ctx, preset, n_rounds, halves, has_sides, show_result):
    """core.Filters from the session state, applied BEFORE the panel renders: the summary
    line needs the counts."""
    shots, duels_all, demo = ctx.shots, ctx.duels_all, ctx.demo
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
        shots, duels_all, demo.hurts, filters
    )
    # the kill/death filter applies only where its control is
    res_sel = set()
    if show_result and st.session_state.get("flt_res", "both") != "both":
        res_sel = {st.session_state["flt_res"]}
    return filters, shots_f, duels_f, hurts_f, res_sel


def _card_controls_row1(t, preset_opts, has_sides, show_result, n_rounds, halves):
    """Row 1: the round preset, the side, the duel result."""
    _r1 = st.columns(
        [3.9] + ([1.0] if has_sides else []) + ([1.4] if show_result else [])
    )
    _c = 0
    with _r1[_c]:
        st.segmented_control(
            t("flt_rounds"),
            preset_opts,
            format_func=lambda p: _preset_label(t, p, n_rounds, halves),
            key="flt_preset",
            on_change=_on_preset_change(n_rounds, halves),
            help=t("flt_rounds_help"),
            persist_state="session",
        )
    _c += 1
    if has_sides:
        with _r1[_c]:
            st.segmented_control(
                t("flt_side"),
                ["all", "T", "CT"],
                format_func=lambda v: t("all") if v == "all" else v,
                key="flt_side",
                help=t("flt_side_help"),
                persist_state="session",
            )
        _c += 1
    if show_result:
        with _r1[_c]:
            st.segmented_control(
                t("flt_res"),
                ["both", "kill", "death"],
                format_func=lambda v: (
                    t("res_both") if v == "both" else fmt_result(v)
                ),
                key="flt_res",
                help=t("flt_res_help"),
                persist_state="session",
            )


def _card_controls_row2(t, demo_weapons, present_classes):
    """Row 2: distance and weapons (class pills OR single guns)."""
    _r2 = st.columns([3.1, 2.2, 0.8], vertical_alignment="bottom")
    with _r2[0]:
        st.segmented_control(
            t("flt_dist"),
            list(_DIST_KEYS),
            format_func=lambda v: _dist_label(t, v),
            key="flt_dist",
            help=t("flt_dist_help"),
            persist_state="session",
        )
    with _r2[1]:
        st.pills(
            t("flt_classes"),
            present_classes,
            selection_mode="multi",
            format_func=lambda c: t(f"wc_{c}"),
            key="flt_classes",
            help=t("flt_classes_help"),
            persist_state="session",
        )
    with _r2[2]:
        with st.popover(t("flt_more"), width="stretch"):
            st.multiselect(
                t("flt_weapons"),
                demo_weapons,
                key="flt_weapons",
                help=t("flt_weapons_help"),
                persist_state="session",
            )


def _render_controls(t, demo_weapons, present_classes,
                     preset_opts, has_sides, show_result, n_rounds, halves,
                     counts):
    """The popover body; it stays mounted while closed, so the widgets keep their state."""
    _card_controls_row1(t, preset_opts, has_sides, show_result, n_rounds, halves)
    _card_controls_row2(t, demo_weapons, present_classes)
    if st.session_state.get("flt_preset") == "custom" and n_rounds >= 2:
        st.slider(
            t("flt_rounds"),
            1,
            n_rounds,
            key="flt_rounds",
            help=t("flt_rounds_help"),
            on_change=_slider_custom,
            persist_state="session",
        )
    # live feedback while editing: the same counts the summary line shows
    st.caption(counts)


def _render_summary_row(t, chips, counts, demo_weapons, present_classes,
                        preset_opts, has_sides, show_result, n_rounds, halves):
    """What is active, how much is in view, Reset, Edit; then the active filters as
    dismissible chips (buttons: st.badge cannot be clicked)."""
    _sum = (
        t("flt_sum", parts=" · ".join(_label for _label, _ in chips))
        if chips
        else t("flt_sum_none")
    )
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.caption(f"{_sum} · {counts}")
        with st.popover(t("flt_edit"), icon=":material/tune:"):
            st.caption(t("flt_title"))
            _render_controls(
                t, demo_weapons, present_classes, preset_opts, has_sides,
                show_result, n_rounds, halves, counts,
            )
        if chips:
            st.button(
                t("flt_reset"),
                on_click=_reset_filters(n_rounds),
                key="flt_reset",
                type="tertiary",
                width="content",
            )
    # one dismissible chip per active filter
    if chips:
        with st.container(
            horizontal=True, gap="small", vertical_alignment="center"
        ):
            for _i, (_label, _fn) in enumerate(chips):
                st.button(
                    f"{_label} ✕",
                    on_click=_fn,
                    key=f"flt_chip_{_i}",
                    width="content",
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
    t = ctx.t
    (demo_weapons, present_classes, n_rounds, has_sides, halves,
     show_result, show_panel) = _filter_data(ctx)

    preset_opts = _preset_options(n_rounds, halves)
    _drop_stale_selections(demo_weapons, present_classes)
    _clamp_filter_state(preset_opts, n_rounds)

    chips = _active_chips(t, has_sides, show_result, n_rounds, halves)

    preset = st.session_state.get("flt_preset", "all")
    filters, shots_f, duels_f, hurts_f, res_sel = _resolve_filters(
        ctx, preset, n_rounds, halves, has_sides, show_result
    )

    counts = t("flt_sum_counts", shots=len(shots_f), duels=len(duels_f))
    if not filters.empty():
        counts += " " + t("flt_sum_of", shots=len(ctx.shots), duels=len(ctx.duels_all))

    if show_panel:
        _render_summary_row(
            t, chips, counts, demo_weapons, present_classes,
            preset_opts, has_sides, show_result, n_rounds, halves,
        )

    _sync_filter_url(ctx.me, preset)
    ctx.filters = filters
    ctx.shots_f = shots_f
    ctx.duels_f = duels_f
    ctx.hurts_f = hurts_f
    ctx.res_sel = res_sel