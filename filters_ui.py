"""
The global filter bar: URL state, presets, the summary row, the popover.

load_url_state() runs before the first widget (a shared link restores
language / player / filters into session_state); render(ctx) is the
summary line (chips + counts + Reset) with every control behind an
st.popover trigger, plus the URL sync. The chip/reset callbacks are
factories (_on_preset_change, _clear_rounds, _reset_filters) that close
over this run's n_rounds / halves.

The controls carry persist_state="session" (they render only inside the
match view and must survive the other pages); the popover keeps its
widgets mounted while it is closed, so no keep-alive is needed anywhere.
"""

import base64
import json

import streamlit as st

import core
import state
from ui.format import fmt_result

# The whole view (language, player, filters) is packed into ?f=<b64 json>
# so a filtered tab can be bookmarked, restored or sent to a teammate.
_URL_KEYS = (
    ("lang", "lang"),
    ("me", "me"),
    ("weapons", "flt_weapons"),
    ("classes", "flt_classes"),
    ("rounds", "flt_rounds"),
    ("preset", "flt_preset"),
    ("side", "flt_side"),
    ("res", "flt_res"),
    ("dist", "flt_dist"),
    ("page", state.PAGE),
)


def _url_pack(d: dict) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def _url_unpack(s: str) -> dict | None:
    try:
        pad = "=" * (-len(s) % 4)
        d = json.loads(base64.urlsafe_b64decode(s + pad).decode())
        return d if isinstance(d, dict) else None
    except ValueError:
        # binascii.Error (bad base64), UnicodeDecodeError and JSONDecodeError
        # are all ValueError subclasses: a bad link must not break the app
        return None


def load_url_state() -> None:
    """Restore a shared ?f= view into session_state (once per session)."""
    if "url_state_loaded" not in st.session_state:
        st.session_state["url_state_loaded"] = True
        saved = _url_unpack(st.query_params.get("f", ""))
        if saved:
            for k, sk in _URL_KEYS:
                if saved.get(k) is not None:
                    st.session_state[sk] = saved[k]


# distance bands reuse the stop-quality thresholds: close is inside
# SAFE_DIST, long is beyond STRICT_DIST
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
    # data-driven halves (MR15 / overtime lengths) when sides are known;
    # [] falls back to the MR12 assumption everywhere below
    halves = core.halves_from_sides(demo.sides, n_rounds)
    # which controls a tab needs: the panel renders above the tabs, but the
    # tab is already known from session_state (the segmented control only
    # re-binds it on the next run)
    tab_now = state.get_match_tab()
    show_result = tab_now == "duels"
    # the replay shows the whole round, so none of the analysis filters apply
    show_panel = tab_now != "replay"
    return demo_weapons, present_classes, n_rounds, has_sides, halves, show_result, show_panel


def _preset_options(n_rounds, halves) -> list[str]:
    """Presets this match can offer (sides decide, else MR12 heuristics)."""
    # a preset is offered only when the match has that part: with
    # sides, halves decide; without them the MR12 heuristics on round count
    # (Custom needs the range slider, i.e. at least two rounds)
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


def _migrate_filter_state(preset_opts, n_rounds):
    """Old links / old session keys keep working: the res multiselect of
    the pre-redesign panel maps to the segmented values, unknown values
    reset to their default, and the round range is clamped to the demo
    (st.slider raises StreamlitInvalidMinMaxError when min == max, which
    would kill the whole app on a one-round demo)."""
    # res arrives as a multiselect list ["kill","death"] -> both/kill/death
    _res = st.session_state.get("flt_res")
    if isinstance(_res, (list, tuple, set)):
        st.session_state["flt_res"] = (
            "kill"
            if "kill" in _res and "death" not in _res
            else "death" if "death" in _res and "kill" not in _res else "both"
        )
    elif _res not in ("both", "kill", "death"):
        st.session_state["flt_res"] = "both"
    # anything unknown resets to "all"
    if st.session_state.get("flt_dist") not in _DIST_KEYS:
        st.session_state["flt_dist"] = "all"
    # an old link could carry preset=all with a slider-moved range: that
    # state maps to Custom (only when the link carried a range - a fresh
    # session has no flt_rounds yet)
    if (
        st.session_state.get("flt_preset", "all") == "all"
        and st.session_state.get("flt_rounds") is not None
        and n_rounds >= 2
        and tuple(st.session_state["flt_rounds"]) != (1, n_rounds)
    ):
        st.session_state["flt_preset"] = "custom"
    if st.session_state.get("flt_preset") not in preset_opts:
        st.session_state["flt_preset"] = "all"
    if st.session_state.get("flt_side") not in ("all", "T", "CT"):
        st.session_state["flt_side"] = "all"
    # no `value=` below: the clamp always puts the (normalized) range into
    # session_state, and passing both a default and the session-state
    # value makes Streamlit log a warning.
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
    # a manual range is the Custom preset (falling back to "all"
    # would hide that a range is active)
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
            (fmt_result(st.session_state["flt_res"]), _clear("flt_flt_res", "both"))
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
        # bounds come from the preset itself, so a URL-restored preset works
        # even though its on_change callback never fired
        rv = core.round_bounds(n_rounds, preset, halves or None)
        if rv != (1, n_rounds):
            round_kwargs["rounds"] = rv
    return round_kwargs


def _resolve_filters(ctx, preset, n_rounds, halves, has_sides, show_result):
    """Build core.Filters from the session state and apply it. The filters
    are resolved BEFORE the panel renders: the collapsed summary line
    needs the filtered counts as well."""
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
    # the kill/death filter follows its control: it lives on the duels
    # pages, so it applies there (other pages show all duels)
    res_sel = set()
    if show_result and st.session_state.get("flt_res", "both") != "both":
        res_sel = {st.session_state["flt_res"]}
    return filters, shots_f, duels_f, hurts_f, res_sel


def _card_controls_row1(t, preset_opts, has_sides, show_result, n_rounds, halves):
    """Row 1: the round-range preset (widest slot so its options never
    clip), the side control, the duel result control. Every control
    carries persist_state="session": their values must survive both the
    pages without the panel and the tabs that hide half of them."""
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
    """Row 2: distance, then ONE weapons control - class pills + a More
    popover with the single guns, combined with OR (core.Filters unions
    weapons and classes)."""
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
    """The filter controls (the popover body): two rows of controls, the
    Custom range slider, live counts. The widgets stay mounted while the
    popover is closed - Streamlit runs the body on every rerun - so their
    state survives a close without any keep-alive."""
    _card_controls_row1(t, preset_opts, has_sides, show_result, n_rounds, halves)
    _card_controls_row2(t, demo_weapons, present_classes)
    # the range slider exists only in Custom mode (and only when a
    # range is possible at all); moving it keeps Custom selected
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
    """The one-line summary in the flow: what is active, how much is in
    view, Reset - and every control behind the Edit popover. The active
    filters repeat below as dismissible chips (st.button: st.badge cannot
    be clicked)."""
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


def _sync_filter_url(me, preset, n_rounds):
    """Keep the shareable ?f= in step (defaults drop off the URL)."""
    url_view = {
        "lang": state.get_lang(),
        "me": me,
        "weapons": st.session_state.get("flt_weapons") or [],
        "classes": st.session_state.get("flt_classes") or [],
        "rounds": list(st.session_state.get("flt_rounds") or ()),
        "preset": preset,
        "side": st.session_state.get("flt_side", "all"),
        "res": st.session_state.get("flt_res", "both"),
        "dist": st.session_state.get("flt_dist", "all"),
    }
    non_default = (
        url_view["weapons"]
        or url_view["classes"]
        or url_view["res"] != "both"
        or url_view["dist"] != "all"
        or url_view["side"] != "all"
        or url_view["preset"] != "all"
        or (url_view["rounds"] and tuple(url_view["rounds"]) != (1, n_rounds))
    )
    if non_default:
        st.query_params["f"] = _url_pack(url_view)
    elif "f" in st.query_params:
        del st.query_params["f"]


def render(ctx) -> None:
    """The filter bar: resolve, render, sync the shareable URL.

    A one-line summary (chips + counts + Reset) with every control behind
    the Edit popover: Side / Rounds / Distance segmented controls plus
    weapon class pills; the Result control exists only on the duels pages,
    and the replay tab shows no bar at all. All metrics below are computed
    on the FILTERED frames.
    """
    t = ctx.t
    (demo_weapons, present_classes, n_rounds, has_sides, halves,
     show_result, show_panel) = _filter_data(ctx)

    preset_opts = _preset_options(n_rounds, halves)
    _drop_stale_selections(demo_weapons, present_classes)
    _migrate_filter_state(preset_opts, n_rounds)

    chips = _active_chips(t, has_sides, show_result, n_rounds, halves)

    preset = st.session_state.get("flt_preset", "all")
    filters, shots_f, duels_f, hurts_f, res_sel = _resolve_filters(
        ctx, preset, n_rounds, halves, has_sides, show_result
    )

    # how much data is in view: the one caption shared by the summary
    # row and the popover
    counts = t("flt_sum_counts", shots=len(shots_f), duels=len(duels_f))
    if not filters.empty():
        counts += " " + t("flt_sum_of", shots=len(ctx.shots), duels=len(ctx.duels_all))

    if show_panel:
        _render_summary_row(
            t, chips, counts, demo_weapons, present_classes,
            preset_opts, has_sides, show_result, n_rounds, halves,
        )

    _sync_filter_url(ctx.me, preset, n_rounds)
    ctx.filters = filters
    ctx.shots_f = shots_f
    ctx.duels_f = duels_f
    ctx.hurts_f = hurts_f
    ctx.res_sel = res_sel