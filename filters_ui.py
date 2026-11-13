"""
The global filter bar: URL state, presets, the panel, the chips.

load_url_state() runs before the first widget (a shared link restores
language / player / filters into session_state); render(ctx) is the
sticky filter panel plus the URL sync. The filter callbacks
(_apply_preset, _clr_rounds, ...) are nested inside render(): they close
over this run's n_rounds / halves.
"""

import base64
import json

import streamlit as st

import core
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
    ("page", "page"),
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
    except Exception:  # noqa: BLE001 - a bad link must not break the app
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
            # a link that restores filters opens with the card expanded
            if (
                saved.get("weapons")
                or saved.get("classes")
                or saved.get("rounds")
                or saved.get("preset") not in (None, "all")
                or saved.get("side") not in (None, "all")
                or saved.get("res") not in (None, "both")
                or saved.get("dist") not in (None, "all")
            ):
                st.session_state["flt_open"] = True


def render(ctx) -> None:
    """The filter bar: resolve, render, sync the shareable URL."""
    t = ctx.t
    demo = ctx.demo
    shots = ctx.shots
    duels_all = ctx.duels_all
    me = ctx.me

    # A persistent panel (no popover): Side / Rounds / Distance segmented
    # controls plus weapon class pills; the Result control exists only on
    # the duels pages, and the guide page shows no panel at all. Active
    # filters repeat as dismissible chips with a Reset. All metrics below
    # are computed on the FILTERED frames.
    demo_weapons = sorted(shots["weapon"].astype(str).unique())
    present_classes = sorted({core.weapon_class(w) for w in demo_weapons})
    n_rounds = int(shots["round"].max()) if len(shots) else 0
    has_sides = bool(demo.sides)
    # data-driven halves (MR15 / overtime lengths) when sides are known;
    # [] falls back to the MR12 assumption everywhere below
    halves = core.halves_from_sides(demo.sides, n_rounds)
    # which controls a page needs: the panel renders above the nav, but the
    # page is already known from session_state (the nav widget only
    # re-binds it on the next run)
    page_now = st.session_state.get("page") or "overview"
    show_result = page_now in ("duels", "timeline")
    show_panel = page_now != "guide"
    # distance bands reuse the stop-quality thresholds: close is inside
    # SAFE_DIST, long is beyond STRICT_DIST
    DIST_KEYS = ("all", "close", "mid", "long")
    DIST_RANGES = {
        "close": (0.0, core.SAFE_DIST),
        "mid": (core.SAFE_DIST, core.STRICT_DIST),
        "long": (core.STRICT_DIST, float("inf")),
    }
    # drop selections that do not exist in this demo (e.g. from another file
    # or from a shared URL of another match)
    if "flt_weapons" in st.session_state:
        st.session_state["flt_weapons"] = [
            w for w in st.session_state["flt_weapons"] if w in set(demo_weapons)
        ]
    if "flt_classes" in st.session_state:
        st.session_state["flt_classes"] = [
            c for c in st.session_state["flt_classes"] if c in set(present_classes)
        ]
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
    # ---- old links / old session keys keep working
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
    if st.session_state.get("flt_dist") not in DIST_KEYS:
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
    # st.slider raises StreamlitInvalidMinMaxError when min_value == max_value,
    # which kills the whole app on a one-round demo (or any demo where every
    # shot lands in round 1 because total_rounds_played never arrives). A
    # single round needs no range filter, so the slider starts at 2 rounds.
    if n_rounds >= 2:
        # no `value=`: the clamp block above always puts the (normalized)
        # range into session_state, and passing both a default and the
        # session-state value makes Streamlit log a warning.
        rv = st.session_state.get("flt_rounds") or (1, n_rounds)
        try:
            lo_r = max(1, min(int(rv[0]), n_rounds))
            hi_r = max(lo_r, min(int(rv[1]), n_rounds))
            st.session_state["flt_rounds"] = (lo_r, hi_r)
        except (TypeError, ValueError, IndexError):
            st.session_state["flt_rounds"] = (1, n_rounds)

    def _preset_label(p: str) -> str:
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

    def _slider_custom():
        # a manual range is the Custom preset (falling back to "all"
        # would hide that a range is active)
        st.session_state["flt_preset"] = "custom"

    def _clr(key, value):
        """A chip click clears one filter; on_click runs before widgets."""

        def _fn():
            st.session_state[key] = value

        return _fn

    def _clr_rounds():
        st.session_state["flt_rounds"] = (1, n_rounds)
        st.session_state["flt_preset"] = "all"

    def _reset_filters():
        st.session_state["flt_weapons"] = []
        st.session_state["flt_classes"] = []
        st.session_state["flt_res"] = "both"
        st.session_state["flt_side"] = "all"
        st.session_state["flt_dist"] = "all"
        _clr_rounds()

    def _dist_label(v: str) -> str:
        return t("all") if v == "all" else t(f"flt_dist_{v}")

    # one dismissible chip per active filter: (label, clear callback)
    _chips = []
    if st.session_state.get("flt_weapons"):
        _chips.append(
            (", ".join(st.session_state["flt_weapons"]), _clr("flt_weapons", []))
        )
    if st.session_state.get("flt_classes"):
        _chips.append(
            (
                ", ".join(t(f"wc_{c}") for c in st.session_state["flt_classes"]),
                _clr("flt_classes", []),
            )
        )
    if has_sides and st.session_state.get("flt_side") != "all":
        _chips.append((st.session_state["flt_side"], _clr("flt_side", "all")))
    if st.session_state.get("flt_preset", "all") == "custom":
        _rv = tuple(st.session_state.get("flt_rounds") or ())
        _chips.append(
            (
                t("flt_custom") + (f" {_rv[0]}–{_rv[1]}" if len(_rv) == 2 else ""),
                _clr_rounds,
            )
        )
    elif st.session_state.get("flt_preset", "all") != "all":
        _chips.append((_preset_label(st.session_state["flt_preset"]), _clr_rounds))
    if st.session_state.get("flt_dist", "all") != "all":
        _chips.append(
            (_dist_label(st.session_state["flt_dist"]), _clr("flt_dist", "all"))
        )
    if show_result and st.session_state.get("flt_res", "both") != "both":
        _chips.append(
            (fmt_result(st.session_state["flt_res"]), _clr("flt_res", "both"))
        )
    # Streamlit deletes widget state when the widget is absent from a run:
    # re-assign the keys of the controls this page does not show - and of
    # the filter card while it is collapsed - so neither a page switch nor
    # folding the card loses a selection
    _flt_open = bool(st.session_state.get("flt_open"))
    if not show_result:
        st.session_state["flt_res"] = st.session_state.get("flt_res", "both")
    if not show_panel or not _flt_open:
        for _k, _d in (
            ("flt_weapons", []),
            ("flt_classes", []),
            ("flt_side", "all"),
            ("flt_preset", "all"),
            ("flt_dist", "all"),
            ("flt_res", "both"),
        ):
            st.session_state[_k] = st.session_state.get(_k, _d)
        if n_rounds >= 2:
            st.session_state["flt_rounds"] = st.session_state.get("flt_rounds") or (
                1,
                n_rounds,
            )

    # The filters are resolved BEFORE the panel renders: the collapsed
    # summary line needs the filtered counts as well.
    preset = st.session_state.get("flt_preset", "all")
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
    filters = core.Filters(
        weapons=set(st.session_state.get("flt_weapons") or []) or None,
        classes=set(st.session_state.get("flt_classes") or []) or None,
        side=(
            st.session_state.get("flt_side")
            if has_sides and st.session_state.get("flt_side") != "all"
            else None
        ),
        dist=DIST_RANGES.get(st.session_state.get("flt_dist", "all")),
        **round_kwargs,
    )
    shots_f, duels_f, hurts_f = core.apply_filters(
        shots, duels_all, demo.hurts, filters
    )
    # the kill/death filter follows its control: it lives on the duels
    # pages, so it applies there (other pages show all duels)
    res_sel = set()
    if show_result and st.session_state.get("flt_res", "both") != "both":
        res_sel = {st.session_state["flt_res"]}

    def _flt_show(is_open: bool):
        st.session_state["flt_open"] = is_open

    # how much data is in view: the one caption shared by the collapsed
    # summary row and the open card
    _counts = t("flt_sum_counts", shots=len(shots_f), duels=len(duels_f))
    if not filters.empty():
        _counts += " " + t("flt_sum_of", shots=len(shots), duels=len(duels_all))

    # The sticky bar: the filter summary row and the page tabs stay
    # visible while the page scrolls (position: sticky in
    # theme/overrides.css, scoped to this container's key; on Streamlit
    # builds without .st-key-* classes the rule does not match and
    # the bar scrolls away - no fallback hacks).
    _hdr = st.container(key="hdr_sticky", gap="xsmall")

    if show_panel:
        if _flt_open:
            with st.container(border=True, key="flt_card"):
                with st.container(
                    horizontal=True, gap="small", vertical_alignment="center"
                ):
                    st.caption(t("flt_title"))
                    st.button(
                        t("flt_done"),
                        on_click=_flt_show,
                        args=(False,),
                        key="flt_done",
                        type="tertiary",
                        width="content",
                    )
                # row 1: the round range gets the widest slot so its options never clip
                _r1 = st.columns(
                    [3.9]
                    + ([1.0] if has_sides else [])
                    + ([1.4] if show_result else [])
                )
                _c = 0
                with _r1[_c]:
                    st.segmented_control(
                        t("flt_rounds"),
                        preset_opts,
                        format_func=_preset_label,
                        key="flt_preset",
                        on_change=_apply_preset,
                        help=t("flt_rounds_help"),
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
                        )
                # row 2: distance, then ONE weapons control: class chips + a More
                # popover with the single guns, combined with OR (core.Filters
                # unions weapons and classes)
                _r2 = st.columns([3.1, 2.2, 0.8], vertical_alignment="bottom")
                with _r2[0]:
                    st.segmented_control(
                        t("flt_dist"),
                        list(DIST_KEYS),
                        format_func=_dist_label,
                        key="flt_dist",
                        help=t("flt_dist_help"),
                    )
                with _r2[1]:
                    st.pills(
                        t("flt_classes"),
                        present_classes,
                        selection_mode="multi",
                        format_func=lambda c: t(f"wc_{c}"),
                        key="flt_classes",
                        help=t("flt_classes_help"),
                    )
                with _r2[2]:
                    with st.popover(t("flt_more"), width="stretch"):
                        st.multiselect(
                            t("flt_weapons"),
                            demo_weapons,
                            key="flt_weapons",
                            help=t("flt_weapons_help"),
                        )
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
                    )
                # active filters: compact dismissible chips + a text-style Reset
                if _chips:
                    with st.container(
                        horizontal=True, gap="small", vertical_alignment="center"
                    ):
                        for _i, (_label, _fn) in enumerate(_chips):
                            st.button(
                                f"{_label} ✕",
                                on_click=_fn,
                                key=f"flt_chip_{_i}",
                                width="content",
                            )
                        st.button(
                            t("flt_reset"),
                            on_click=_reset_filters,
                            key="flt_reset",
                            type="tertiary",
                            width="content",
                        )
                # live feedback while editing: the same counts the collapsed
                # summary line shows
                st.caption(_counts)
        else:
            # one line: the active filters, how much is in view, Reset
            _sum = (
                t("flt_sum", parts=" · ".join(_label for _label, _ in _chips))
                if _chips
                else t("flt_sum_none")
            )
            with _hdr:
                with st.container(
                    horizontal=True, gap="small", vertical_alignment="center"
                ):
                    st.caption(f"{_sum} · {_counts}")
                    st.button(
                        t("flt_edit"),
                        on_click=_flt_show,
                        args=(True,),
                        key="flt_edit",
                        type="secondary",
                        width="content",
                    )
                    if _chips:
                        st.button(
                            t("flt_reset"),
                            on_click=_reset_filters,
                            key="flt_reset_sum",
                            type="tertiary",
                            width="content",
                        )

    # keep the URL in sync so the view can be bookmarked / shared
    url_view = {
        "lang": st.session_state.get("lang"),
        "me": me,
        "weapons": st.session_state.get("flt_weapons") or [],
        "classes": st.session_state.get("flt_classes") or [],
        "rounds": list(st.session_state.get("flt_rounds") or ()),
        "preset": preset,
        "side": st.session_state.get("flt_side", "all"),
        "res": st.session_state.get("flt_res", "both"),
        "dist": st.session_state.get("flt_dist", "all"),
        "page": st.session_state.get("page", "overview"),
    }
    non_default = (
        url_view["weapons"]
        or url_view["classes"]
        or url_view["res"] != "both"
        or url_view["dist"] != "all"
        or url_view["side"] != "all"
        or url_view["preset"] != "all"
        or url_view["page"] != "overview"
        or (url_view["rounds"] and tuple(url_view["rounds"]) != (1, n_rounds))
    )
    if non_default:
        st.query_params["f"] = _url_pack(url_view)
    elif "f" in st.query_params:
        del st.query_params["f"]

    ctx.filters = filters
    ctx.shots_f = shots_f
    ctx.duels_f = duels_f
    ctx.hurts_f = hurts_f
    ctx.res_sel = res_sel
    ctx.hdr = _hdr
