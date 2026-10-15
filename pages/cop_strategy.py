from datetime import date
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.auth import require_auth, has_permission
from cop import load, logic as L, export as X

NAVY, GOLD, GREEN, RED, GREY = "#0D68A3", "#C1A02E", "#2E8449", "#DE201B", "#9CA3AF"
FOCUS_EDIT_COLS = ["Owner", "Reason", "Target", "Next step"]


@st.cache_data(show_spinner=False, max_entries=8)
def _sheets(content: bytes, name: str):
    return load.list_sheets(content, name)


@st.cache_data(show_spinner=False, max_entries=8)
def _read(content: bytes, name: str, sheet):
    return load.read_year(content, name, sheet)


@st.cache_data(show_spinner=False, max_entries=4)
def _names(data: dict) -> list[str]:
    """Customer names after merging spelling variants (for the 'Mark as key account' picker)."""
    merged, _ = L.standardize_names(data)
    return sorted(pd.unique(pd.concat([d["customer"] for d in merged.values()])), key=str.lower)


@st.cache_data(show_spinner=False, max_entries=4)
def _run(data: dict, settings: L.Settings):
    return L.run(data, settings)


def _fmt_k(v):
    return f"{v:,.0f}" if pd.notna(v) else "—"


def _upload_screen():
    """4 slots: year dropdown + file (+ sheet). Returns {year: (df, check, label)}."""
    cur = date.today().year
    years = list(range(cur - 3, cur + 1))
    st.markdown("**Upload one file per year** (the files have no year in them - pick it from the dropdown).")
    cols = st.columns(4)
    picked = {}
    for i, col in enumerate(cols):
        with col:
            y = st.selectbox(f"Slot {i + 1} · year", ["—"] + years, index=i + 1, key=f"cop_y{i}")
            f = st.file_uploader(f"Slot {i + 1} · file", type=["xlsx", "csv"], key=f"cop_f{i}",
                                 label_visibility="collapsed")
            if f is None or y == "—":
                continue
            sheet = None
            names = _sheets(f.getvalue(), f.name)
            if len(names) > 1:
                default = names.index(str(y)) if str(y) in names else 0
                sheet = st.selectbox("Sheet", names, index=default, key=f"cop_s{i}")
            picked.setdefault(y, []).append((f, sheet))
    dupes = [y for y, v in picked.items() if len(v) > 1]
    if dupes:
        st.error(f"The same year is chosen in more than one slot: {dupes}. Each year can be used once.")
        st.stop()
    out = {}
    for y, [(f, sheet)] in picked.items():
        try:
            df, chk = _read(f.getvalue(), f.name, sheet)
        except Exception as e:
            st.error(f"**{y}** ({f.name}): {e}")
            st.stop()
        out[y] = (df, chk, f"{f.name}" + (f" · sheet '{sheet}'" if sheet else ""))
    return out


def show():
    require_auth()
    if not has_permission("cop"):
        st.error("You don't have access to this page.")
        st.stop()
    st.title("💰 Customer COP Strategy")
    st.caption("COP = gross sales − production cost − freight. Files are processed in memory only; nothing is saved.")

    loaded = _upload_screen()
    if len(loaded) < 2:
        st.info("Upload at least **2 years** to start. The latest year uploaded is the current year (CY).")
        st.stop()
    years = sorted(loaded)
    cy, py = years[-1], years[-2]
    data = {y: loaded[y][0] for y in years}

    chk = pd.DataFrame([{"Year": y, "File": loaded[y][2], "Rows": loaded[y][1].rows,
                         "Months found": ", ".join(load.MONTHS[m - 1] for m in loaded[y][1].months),
                         "Notes": " · ".join(loaded[y][1].issues) or "OK"} for y in years])
    with st.expander(f"Files: CY = **{cy}**, PY = **{py}**", expanded=False):
        st.dataframe(chk, hide_index=True, use_container_width=True)
    if py != cy - 1:
        st.warning(f"The prior year is **{py}**, not {cy - 1} ({cy - 1} wasn't uploaded). Comparisons are {cy} vs {py}.")

    # ── Sidebar settings ──
    sb = st.sidebar
    sb.header("Settings")
    cy_months = sorted(data[cy]["m"].unique())
    m_default = L.default_month_to(data[cy], cy, date.today())
    month_to = sb.selectbox("Compare months 1 through", cy_months, index=cy_months.index(m_default),
                            format_func=lambda m: f"Jan – {load.MONTHS[m - 1]}",
                            help="Default: last complete month of CY (the last month in the file is often partial).")
    with sb.expander("Thresholds", expanded=False):
        fx = st.number_input("FX rate (SAR per USD)", value=3.75, step=0.01, format="%.2f")
        min_mt_month = st.number_input("Minimum MT per month to classify a customer", value=10 / 9, step=0.1,
                                       format="%.3f", help="Scales with the months compared: 1.111 x 9 months = 10 MT.")
        min_mt = round(min_mt_month * int(month_to), 6)          # 10/9 x 9 = exactly 10.0
        st.caption(f"= **{min_mt:,.1f} MT** for Jan–{load.MONTHS[int(month_to) - 1]}")
        recent = st.number_input("Recent window for momentum (months)", min_value=1, max_value=12, value=3)
        vol_up = st.number_input("Volume Growing at ≥ (%)", value=5.0, step=1.0) / 100
        vol_down = st.number_input("Volume Declining at ≤ (%)", value=-5.0, step=1.0) / 100
        copt_up = st.number_input("COP/t Up at ≥ ($/t)", value=10.0, step=1.0)
        copt_down = st.number_input("COP/t Down at ≤ ($/t)", value=-10.0, step=1.0)
        min_price = st.number_input("Price counts as changed when it moves ≥ ($/t)", value=20.0, step=1.0)
        cap_real = st.number_input("'Price up, kept as COP' when ≥ … % of the increase is kept", value=75.0,
                                   step=5.0) / 100
        cap_part = st.number_input("'Price up, cost took part' when ≥ … % is kept", value=25.0, step=5.0) / 100
    sb.header("Worth fighting for")
    sb.caption("Worth fighting for = High or Mid margin, **or** a key account.")
    tier_mid = sb.number_input("Mid margin from ($/t)", value=65.0, step=5.0,
                               help="Below this: Low margin. Below $0/t: Loss-making.")
    tier_high = sb.number_input("High margin from ($/t)", value=230.0, step=5.0)
    if tier_high <= tier_mid:
        sb.error("'High margin from' must be above 'Mid margin from'.")
        st.stop()
    key_share = sb.slider("Key accounts = customers making the top … of PY COP", 0, 100, 80, 5, format="%d%%",
                          help="0% = no key accounts (only customers marked by hand below).") / 100
    manual_keys = sb.multiselect("Also mark as key account", _names(data), placeholder="Type a customer name",
                                 help="For strategic customers that are not in the top group. Kept for this session "
                                      "only - nothing is saved.")
    cost_adj = sb.slider("Cost risk: PY cost/t ±", -20, 20, 0, 1, format="%d%%") / 100

    if "cop_actions" not in st.session_state:
        st.session_state["cop_actions"] = {k: v[2] for k, v in L.STRATEGIES.items()}
    s = L.Settings(month_to=int(month_to), fx=fx, min_mt=min_mt, recent_months=int(recent), vol_up=vol_up,
                   vol_down=vol_down, copt_up=copt_up, copt_down=copt_down, min_price_change=min_price,
                   capture_real=cap_real, capture_partly=cap_part, tier_mid=tier_mid, tier_high=tier_high,
                   key_share=key_share, manual_keys=tuple(manual_keys), cost_adjust=cost_adj,
                   actions=dict(st.session_state["cop_actions"]))
    missing = [y for y in years if not (data[y]["m"] <= s.month_to).any()]
    if missing:
        st.error(f"No data in Jan–{load.MONTHS[s.month_to - 1]} for {missing}.")
        st.stop()

    with st.spinner("Calculating…"):
        R = _run(data, s)
    t = R["table"]
    data = R["data"]                      # names merged

    # ── Filters (customer lists only; totals always cover everyone) ──
    sb.header("Filters")
    search = sb.text_input("Customer search", placeholder="part of a name")
    f_strat = sb.multiselect("Strategy", [v[0] for v in L.STRATEGIES.values()])
    f_status = sb.multiselect("Status", L.STATUSES)
    f_read = sb.multiselect("Price → COP reading", list(L.READINGS))
    view = t
    if search:
        view = view[view["customer"].str.contains(search, case=False, regex=False)]
    if f_strat:
        view = view[view["strategy"].isin(f_strat)]
    if f_status:
        view = view[view["status"].isin(f_status)]
    if f_read:
        view = view[view["reading"].isin(f_read)]
    filtered = bool(search or f_strat or f_status or f_read)

    period_lbl = f"Jan–{load.MONTHS[s.month_to - 1]}"
    st.caption(f"**Like-for-like {period_lbl} of each year · {cy} vs {py}** · SAR k for totals, $/t for per-ton"
               + (f" · lists filtered to {len(view)} of {len(t)} customers" if filtered else ""))

    tabs = st.tabs(["📊 Overview", "🧭 Strategies", "💲 Price → COP", "📈 Trends", "🎯 Focus list", "⚠️ Cost risk",
                    "🧾 Data check"])
    cols_map = X.customer_columns(cy, py, years)
    trend_map = {**cols_map, "strategy": f"Strategy ({cy} vs {py})"}

    # ════════ Overview ════════
    with tabs[0]:
        k = R["kpis"].set_index("year")
        rows = {"Invoiced MT": ("mt", "{:,.0f}"), "Gross sales (SAR k)": ("sales", "{:,.0f}"),
                "COP (SAR k)": ("cop", "{:,.0f}"), "Price $/t": ("price_t", "{:,.1f}"),
                "Cost $/t": ("cost_t", "{:,.1f}"), "COP $/t": ("cop_t", "{:,.1f}"),
                "Active customers": ("active_customers", "{:,.0f}")}
        kt = pd.DataFrame({lab: [fmt.format(k.loc[y, key]) for y in years] for lab, (key, fmt) in rows.items()},
                          index=[str(y) for y in years]).T
        kt[f"Δ% {cy} v {py}"] = [f"{k.loc[cy, key] / k.loc[py, key] - 1:+.1%}" if k.loc[py, key] else "—"
                                 for key, _ in rows.values()]
        st.dataframe(kt, use_container_width=True)
        higher = [y for y in years[:-1] if k.loc[y, "cop"] > k.loc[cy, "cop"]]
        if higher:
            st.warning(f"**{cy} COP ({k.loc[cy, 'cop']:,.0f}) is still below "
                       + ", ".join(f"{y} ({k.loc[y, 'cop']:,.0f})" for y in higher) + "** for the same months.")

        b = R["bridge"]
        steps = [("COP " + str(py), b["cop_py"], "absolute"), ("Volume", b["volume"], "relative"),
                 ("Price", b["price"], "relative"), ("Cost", b["cost"], "relative"),
                 ("New customers", b["new"], "relative"), ("Lost customers", b["lost"], "relative"),
                 ("Small customers", b["small"], "relative"), ("COP " + str(cy), b["cop_cy"], "total")]
        fig = go.Figure(go.Waterfall(x=[s_[0] for s_ in steps], y=[s_[1] for s_ in steps],
                                     measure=[s_[2] for s_ in steps], text=[f"{s_[1]:+,.0f}" if s_[2] == "relative"
                                                                           else f"{s_[1]:,.0f}" for s_ in steps],
                                     textposition="outside", increasing=dict(marker_color=GREEN),
                                     decreasing=dict(marker_color=RED), totals=dict(marker_color=NAVY),
                                     connector=dict(line=dict(color=GREY))))
        fig.update_layout(title=dict(text=f"COP bridge {py} → {cy} (SAR k)", x=0, font=dict(color=NAVY)),
                          height=430, plot_bgcolor="white", margin=dict(l=10, r=10, t=50, b=10), showlegend=False)
        fig.update_yaxes(gridcolor="#EEEEEE")
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(f"""
| Bar | What it means | SAR k |
|---|---|---|
| **Volume** | Customers buying more or fewer tons, valued at last year's COP per ton | {b['volume']:+,.0f} |
| **Price** | Change in price per ton × tons sold this year | {b['price']:+,.0f} |
| **Cost** | Change in production + freight cost per ton × tons sold this year (lower cost = positive) | {b['cost']:+,.0f} |
| **New customers** | All COP from customers under {s.min_mt:,.1f} MT last year and above it this year | {b['new']:+,.0f} |
| **Lost customers** | All COP lost from customers above {s.min_mt:,.1f} MT last year and under it this year | {b['lost']:+,.0f} |
| **Small customers** | Customers under {s.min_mt:,.1f} MT in **both** years - too small to split, shown in one bar | {b['small']:+,.0f} |
""")
        st.caption(f"Volume, price and cost are calculated for customers above {s.min_mt:,.1f} MT in both years. "
                   f"Check: all bars add up to the actual {cy} COP (difference = {b['check']:,.6f}).")

    # ════════ Strategies ════════
    with tabs[1]:
        st_tbl = R["strategies"].rename(columns={"strategy": "Strategy", "who": "Who falls here",
                                                 "customers": "Customers", f"mt_{cy}": f"MT {cy}",
                                                 f"cop_{py}": f"COP {py}", f"cop_{cy}": f"COP {cy}", "dcop": "ΔCOP",
                                                 "volume": "of which volume", "price": "of which price",
                                                 "cost": "of which cost", "action": "What to do"})
        st.dataframe(st_tbl, hide_index=True, use_container_width=True, column_config={
            c: st.column_config.NumberColumn(c, format="%,.0f") for c in st_tbl.columns
            if c not in ("#", "Strategy", "Who falls here", "What to do", "Customers")})
        with st.expander("✏️ Edit the action text ('What to do')"):
            ed = st.data_editor(pd.DataFrame([{"#": k_, "Strategy": v[0], "What to do": st.session_state["cop_actions"][k_]}
                                              for k_, v in L.STRATEGIES.items()]),
                                hide_index=True, use_container_width=True, disabled=["#", "Strategy"], key="cop_act_ed")
            new = {int(r["#"]): r["What to do"] for _, r in ed.iterrows()}
            if new != st.session_state["cop_actions"]:
                st.session_state["cop_actions"] = new
                st.rerun()
            st.caption("Edits apply to this session and to the Excel download.")
        st.subheader("Customers by strategy")
        pick = st.selectbox("Strategy", [v[0] for v in L.STRATEGIES.values()])
        dd = view[view["strategy"] == pick]
        cols = ["customer", "status", "margin_tier", "key_reason", "grow_tag", f"mt_{py}", f"mt_{cy}", "vol_pct",
                f"copt_{py}", f"copt_{cy}", f"cop_{py}", f"cop_{cy}", "dcop", "vol_eff", "price_eff", "cost_eff",
                "reading", "momentum"]
        st.dataframe(dd[cols].rename(columns=cols_map), hide_index=True, use_container_width=True,
                     column_config=_num_cfg(cols_map, cols))

    # ════════ Price → COP ════════
    with tabs[2]:
        c = t[t["status"] == "Continuing"].dropna(subset=["dprice", "dcopt"])
        cap = 400
        fig = go.Figure()
        outcome = [("Up", f"COP per ton up (≥ +${s.copt_up:,.0f}/t)", GREEN),
                   ("Flat", "COP per ton about the same", GREY),
                   ("Down", f"COP per ton down (≤ −${-s.copt_down:,.0f}/t)", RED)]
        for move, name, color in outcome:
            x = c[c["copt_move"] == move]
            if x.empty:
                continue
            fig.add_trace(go.Scatter(
                x=x["dprice"].clip(-cap, cap), y=x["dcopt"].clip(-cap, cap), mode="markers", name=name,
                marker=dict(color=color, size=8, opacity=0.8), customdata=x[["customer", "reading"]].values,
                hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>Price Δ %{x:,.0f} $/t"
                              "<br>COP per ton Δ %{y:,.0f} $/t<extra></extra>"))
        fig.add_trace(go.Scatter(x=[-cap, cap], y=[-cap, cap], mode="lines", name="Cost per ton unchanged",
                                 line=dict(color=NAVY, dash="dash")))
        for x_, y_, txt in ((-cap * 0.45, cap * 0.85, "Above the dotted line:<br><b>cost per ton fell</b>"),
                            (cap * 0.70, cap * 0.22, "Below the dotted line:<br><b>cost per ton rose</b>"),
                            (0, -cap * 0.88, "Below zero: <b>COP per ton fell</b>"),
                            (cap * 0.80, cap * 0.95, "Dotted line: every $1 of price<br>became $1 of COP per ton")):
            fig.add_annotation(x=x_, y=y_, text=txt, showarrow=False, font=dict(size=12, color=NAVY),
                               bgcolor="rgba(255,255,255,0.8)")
        fig.update_layout(height=560, plot_bgcolor="white", xaxis_title="Price per ton change ($/t)",
                          yaxis_title="COP per ton change ($/t)", legend=dict(orientation="h", y=-0.18),
                          margin=dict(l=10, r=10, t=30, b=10))
        fig.update_xaxes(gridcolor="#EEEEEE", zeroline=True, zerolinecolor="#6B7280", zerolinewidth=1.5,
                         range=[-cap * 1.05, cap * 1.05])
        fig.update_yaxes(gridcolor="#EEEEEE", zeroline=True, zerolinecolor="#6B7280", zerolinewidth=1.5,
                         range=[-cap * 1.05, cap * 1.05])
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Each dot is a continuing customer, {cy} vs {py}. Hover a dot to see the customer and its reading. "
                   f"Values beyond ±{cap} $/t are shown at the edge. Readings are per customer, so a customer that "
                   "switched to more expensive boxes can show a price change without a real price increase.")
        rd = R["readings"].rename(columns={"reading": "Reading", "customers": "Customers", "avg_dprice": "Avg price Δ $/t",
                                           "avg_dcopt": "Avg COP/t Δ $/t", "dcop": "ΔCOP", "price": "of which price",
                                           "cost": "of which cost", "meaning": "Meaning"})
        st.dataframe(rd, hide_index=True, use_container_width=True, column_config={
            c_: st.column_config.NumberColumn(c_, format="%,.0f") for c_ in rd.columns if c_ not in ("Reading", "Meaning")})
        st.subheader("Price down, COP fell")
        dm = view[view["reading"] == "Price down, COP fell"]
        cols = ["customer", "strategy", f"mt_{cy}", "dprice", "dcopt", f"cop_{py}", f"cop_{cy}", "dcop"]
        st.dataframe(dm[cols].sort_values("dcop").rename(columns=cols_map), hide_index=True, use_container_width=True,
                     column_config=_num_cfg(cols_map, cols))

    # ════════ Trends ════════
    with tabs[3]:
        st.subheader("Multi-year COP trend")
        tr = view[~view["trend"].str.startswith("Not active")].copy()
        tr["COP by year"] = tr[[f"cop_{y}" for y in years]].values.tolist()
        cols = ["customer", "trend", "COP by year"] + [f"cop_{y}" for y in years] + ["strategy"]
        st.dataframe(tr[cols].rename(columns=trend_map), hide_index=True, use_container_width=True,
                     column_config={**_num_cfg(cols_map, cols),
                                    "COP by year": st.column_config.LineChartColumn("COP by year")})
        st.caption(f"Customers with ≥ {s.min_mt:,.1f} MT in every uploaded year. Label = direction of COP over the last "
                   "two changes (Growing ↑↑ · Recovering ↓↑ · Falling back ↑↓ · Declining ↓↓).")
        st.subheader(f"Momentum: last {s.recent_months} months of {cy} vs the same months of {py}")
        mo = view[view["momentum"] != ""]
        cols = ["customer", "momentum", "recent_copt_py", "recent_copt_cy", "strategy"]
        st.dataframe(mo[cols].rename(columns=trend_map), hide_index=True, use_container_width=True,
                     column_config=_num_cfg(cols_map, cols))
        st.caption(f"The strategy column is the same as on the Strategies tab: it compares {cy} with {py} only, so it "
                   "can differ from the multi-year trend.")
        st.subheader("Monthly COP per ton")
        who = st.selectbox("Customer", view["customer"].tolist() or t["customer"].tolist(), help="Type to search.")
        mc = L.monthly_copt(data, who, s.fx)
        fig = go.Figure()
        for y, color in zip(years, [GREY, "#457B9D", GOLD, NAVY][-len(years):]):
            x = mc[mc["year"] == str(y)]
            fig.add_trace(go.Scatter(x=x["month"], y=x["cop_t"], name=str(y), mode="lines+markers",
                                     line=dict(color=color, width=3 if y == cy else 2)))
        fig.update_layout(height=380, plot_bgcolor="white", yaxis_title="COP $/t", hovermode="x unified",
                          title=dict(text=who, x=0, font=dict(color=NAVY, size=14)), margin=dict(l=10, r=10, t=50, b=10),
                          xaxis=dict(categoryorder="array", categoryarray=load.MONTHS))
        fig.update_yaxes(gridcolor="#EEEEEE")
        st.plotly_chart(fig, use_container_width=True)

    # ════════ Focus list ════════
    with tabs[4]:
        st.markdown(f"""
**Top 30 customers by COP at stake** (SAR k): how much COP each one is worth acting on.

| Strategy | COP at stake = |
|---|---|
| **Defend** | the COP lost because they bought fewer tons: tons lost × their {py} COP per ton |
| **Win back** | all the COP they made in {py} |
| **Raise price** | {cy} tons × the COP per ton needed to get back to their {py} level |
| **Grow** | the COP they added vs {py} |

Each row's own calculation is in the column **How it's calculated**.
""")
        fl = L.focus_list(view, cy, py, s.fx)
        notes = st.session_state.setdefault("cop_focus_notes", {})
        cols = ["customer", "strategy", "at_stake", "stake_how", f"cop_{py}", f"cop_{cy}", "dcop", "momentum"]
        fe = fl[cols].rename(columns={**cols_map, "stake_how": "How it's calculated"}).copy()
        for c_ in FOCUS_EDIT_COLS:
            fe[c_] = [notes.get(n, {}).get(c_, "") for n in fl["customer"]]
        edited = st.data_editor(fe, hide_index=True, use_container_width=True,
                                disabled=[{**cols_map, "stake_how": "How it's calculated"}[c_] for c_ in cols],
                                key="cop_focus_ed",
                                column_config=_num_cfg(cols_map, cols))
        for _, r in edited.iterrows():
            notes[r["Customer"]] = {c_: r[c_] for c_ in FOCUS_EDIT_COLS}
        st.caption("Owner / Reason / Target / Next step are kept for this session and included in the Excel download. "
                   "Download to keep them.")

    # ════════ Cost risk ════════
    with tabs[5]:
        rk = R["risk"]
        b = R["bridge"]
        at_risk = rk["at_risk"].sum()
        gain = b["cop_cy"] - b["cop_py"]
        st.markdown(f"""
**Question:** how much of this year's COP depends on costs per ton being lower than last year?

**What-if:** for every continuing customer, keep their {cy} tons and {cy} price, but put their cost per ton back to
their {py} level{f' {s.cost_adjust:+.0%}' if s.cost_adjust else ''} (slider in the sidebar).
""")
        a, b_, c_ = st.columns(3)
        a.metric(f"{cy} COP (actual)", _fmt_k(b["cop_cy"]))
        b_.metric(f"{cy} COP if cost per ton = {py} level" + (f" {s.cost_adjust:+.0%}" if s.cost_adjust else ""),
                  _fmt_k(b["cop_cy"] - at_risk))
        c_.metric("COP that depends on lower cost", _fmt_k(at_risk),
                  f"{at_risk / gain:.0%} of the {cy} COP gain ({gain:,.0f})" if gain else None, delta_color="off")
        ex = rk.iloc[0] if len(rk) else None
        if ex is not None:
            st.caption(f"Example - {ex['customer']}: {ex[f'mt_{cy}']:,.0f} t × (scenario cost "
                       f"{ex['cost_scenario_t']:,.0f} − actual {cy} cost {ex[f'cost_{cy}']:,.0f} $/t) × {s.fx} ÷ 1000 "
                       f"= {ex['at_risk']:,.0f} SAR k of COP at risk. "
                       + (f"At 0% on the slider the total equals the Cost bar of the bridge ({b['cost']:,.0f}). "
                          if not s.cost_adjust else "")
                       + "New, lost and small customers have no reliable last-year cost per ton, so they are not "
                         "part of this what-if.")
        rv = rk[rk["customer"].isin(view["customer"])]
        cols = ["customer", "strategy", f"mt_{cy}", f"cost_{py}", f"cost_{cy}"]
        if s.cost_adjust:
            cols.append("cost_scenario_t")
        cols += [f"cop_{cy}", "cop_scenario", "at_risk"]
        rmap = {**cols_map, "cost_scenario_t": f"Cost $/t in what-if ({py} {s.cost_adjust:+.0%})",
                f"cop_{cy}": f"COP {cy} actual (SAR k)", "cop_scenario": f"COP {cy} if cost back to {py} level",
                "at_risk": "COP that depends on lower cost"}
        st.dataframe(rv[cols].rename(columns=rmap), hide_index=True, use_container_width=True,
                     column_config=_num_cfg(rmap, cols))

    # ════════ Data check ════════
    with tabs[6]:
        st.dataframe(chk, hide_index=True, use_container_width=True)
        kt_ = t[t["key_account"]]
        st.markdown(f"""
- **Customers analysed:** everyone in the {py} or {cy} file (any month), as in the workbook - **{len(t):,}**.
- **Small customer threshold:** {min_mt_month:.3f} MT per month × {s.month_to} months = **{s.min_mt:,.1f} MT**.
- **Status:** under {s.min_mt:,.1f} MT in both years → Small customer · under it in {py} only → New / regained · under
  it in {cy} only → Lost / near-lost · otherwise Continuing.
- **Margin tier** (COP per ton; {cy} for continuing customers, {py} for lost ones): High ≥ ${s.tier_high:,.0f} ·
  Mid ${s.tier_mid:,.0f}–{s.tier_high:,.0f} · Low $0–{s.tier_mid:,.0f} · Loss-making < $0.
- **Key accounts** ({len(kt_):,}): the customers that together make {s.key_share:.0%} of all positive {py} COP
  ({int((kt_['key_reason'] != 'Marked by PM').sum())}), plus any marked by hand for this session
  ({int((kt_['key_reason'] == 'Marked by PM').sum())}).
  A key account is worth fighting for even at a low margin.
- **Worth fighting for** = High or Mid margin, **or** a key account.
- **Momentum** compares the last {s.recent_months} months of {cy} with the same months of {py}.
- **Year KPIs** use every customer in that year's file. (The workbook's 2024 MT and COP only counted customers also
  present in 2025/2026, while its sales counted all - so its 2024 price/t was inconsistent.)
- Rows without a customer name are kept as **'{load.BLANK_CUSTOMER}'**.
- **Duplicate names are always merged** (list below).
""")
        st.subheader("Customer names treated as one customer")
        if R["merges"].empty:
            st.caption("No name variants found.")
        else:
            st.caption("Same name after ignoring case, spaces and punctuation, or the same English name with an added "
                       "Arabic name. Shown under the spelling used most in the latest year.")
            st.dataframe(R["merges"].rename(columns={"shown_as": "Shown as", "names_merged": "Names merged"}),
                         hide_index=True, use_container_width=True)

    # ── Export ──
    cust = t[list(cols_map)].rename(columns=cols_map)
    focus = L.focus_list(t, cy, py, s.fx)[["customer", "strategy", "at_stake", "stake_how", f"cop_{py}", f"cop_{cy}",
                                           "dcop"]].rename(columns={**cols_map, "stake_how": "How it's calculated"})
    notes = st.session_state.get("cop_focus_notes", {})
    for c_ in FOCUS_EDIT_COLS:
        focus[c_] = [notes.get(n, {}).get(c_, "") for n in focus["Customer"]]
    b = R["bridge"]
    sheets = {
        "KPIs": R["kpis"].rename(columns={"year": "Year", "mt": "MT", "sales": "Gross sales (SAR k)", "cop": "COP (SAR k)",
                                          "price_t": "Price $/t", "cost_t": "Cost $/t", "cop_t": "COP $/t",
                                          "active_customers": "Active customers"}),
        "Bridge": pd.DataFrame({"Step": [f"COP {py}", "Volume", "Price", "Cost", "New customers", "Lost customers",
                                         "Small customers", f"COP {cy}", "Check (must be 0)"],
                                "SAR k": [b["cop_py"], b["volume"], b["price"], b["cost"], b["new"], b["lost"],
                                          b["small"], b["cop_cy"], b["check"]]}),
        "Strategies": st_tbl, "Customers": cust, "Price-COP readings": rd, "Focus list": focus,
        "Merged names": R["merges"].rename(columns={"shown_as": "Shown as", "names_merged": "Names merged"}),
        "Cost risk": R["risk"].rename(columns={**cols_map, "cost_scenario_t": f"Cost $/t in what-if ({py} {s.cost_adjust:+.0%})",
                                               f"cop_{cy}": f"COP {cy} actual (SAR k)",
                                               "cop_scenario": f"COP {cy} if cost back to {py} level",
                                               "at_risk": "COP that depends on lower cost"}),
        "Settings": pd.DataFrame([{"Setting": k_, "Value": str(v)} for k_, v in vars(s).items() if k_ != "actions"]),
    }
    st.download_button("⬇️ Download all tables (.xlsx)", X.build_workbook(sheets),
                       file_name=f"cop_strategy_{cy}_vs_{py}_{period_lbl.replace('–', '-')}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _num_cfg(cols_map: dict, cols: list) -> dict:
    """Number formats: % columns, $/t columns (no decimals), SAR k / MT columns (1 decimal)."""
    cfg = {}
    for c in cols:
        lab = cols_map.get(c, c)
        if c in ("vol_pct", "capture", "share_cy_cop"):
            cfg[lab] = st.column_config.NumberColumn(lab, format="%.1%")
        elif c.startswith(("price_", "cost_", "copt_", "recent_copt")) or c in ("dprice", "dcopt", "dcost"):
            cfg[lab] = st.column_config.NumberColumn(lab, format="%,.0f")
        elif c.startswith(("mt_", "cop_", "vol_eff", "price_eff", "cost_eff", "at_stake", "at_risk")) or c == "dcop":
            cfg[lab] = st.column_config.NumberColumn(lab, format="%,.1f")
    return cfg
