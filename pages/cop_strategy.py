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
        min_mt = st.number_input("Minimum MT to classify a customer", value=10.0, step=1.0)
        recent = st.number_input("Recent window for momentum (months)", min_value=1, max_value=12, value=3)
        vol_up = st.number_input("Volume Growing at ≥ (%)", value=5.0, step=1.0) / 100
        vol_down = st.number_input("Volume Declining at ≤ (%)", value=-5.0, step=1.0) / 100
        copt_up = st.number_input("COP/t Up at ≥ ($/t)", value=10.0, step=1.0)
        copt_down = st.number_input("COP/t Down at ≤ ($/t)", value=-10.0, step=1.0)
        min_price = st.number_input("Min price change to measure capture ($/t)", value=20.0, step=1.0)
        cap_real = st.number_input("Real margin gain at capture ≥ (%)", value=75.0, step=5.0) / 100
        cap_part = st.number_input("Partly cost recovery at capture ≥ (%)", value=25.0, step=5.0) / 100
        worth_copt = st.number_input("Worth fighting for: COP/t ≥ ($/t)", value=100.0, step=10.0)
        worth_top = st.number_input("… or inside the top X% of PY COP", value=80.0, step=5.0) / 100
    cost_adj = sb.slider("Cost risk: PY cost/t ±", -20, 20, 0, 1, format="%d%%") / 100

    if "cop_actions" not in st.session_state:
        st.session_state["cop_actions"] = {k: v[2] for k, v in L.STRATEGIES.items()}
    s = L.Settings(month_to=int(month_to), fx=fx, min_mt=min_mt, recent_months=int(recent), vol_up=vol_up,
                   vol_down=vol_down, copt_up=copt_up, copt_down=copt_down, min_price_change=min_price,
                   capture_real=cap_real, capture_partly=cap_part, worth_copt=worth_copt, worth_top_share=worth_top,
                   cost_adjust=cost_adj, actions=dict(st.session_state["cop_actions"]))
    missing = [y for y in years if not (data[y]["m"] <= s.month_to).any()]
    if missing:
        st.error(f"No data in Jan–{load.MONTHS[s.month_to - 1]} for {missing}.")
        st.stop()

    with st.spinner("Calculating…"):
        R = _run(data, s)
    t = R["table"]

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
                 ("Price", b["price"], "relative"), ("Cost / mix", b["cost_mix"], "relative"),
                 ("New / regained", b["new"], "relative"), ("Lost / near-lost", b["lost"], "relative"),
                 ("Low data", b["low_data"], "relative"), ("COP " + str(cy), b["cop_cy"], "total")]
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
        st.caption(f"Check: bridge total − actual {cy} COP = **{b['check']:,.6f}** (must be 0). Volume / price / "
                   "cost-mix are for continuing customers; new, lost and low-data customers each carry their whole ΔCOP.")

    # ════════ Strategies ════════
    with tabs[1]:
        st_tbl = R["strategies"].rename(columns={"strategy": "Strategy", "who": "Who falls here",
                                                 "customers": "Customers", f"mt_{cy}": f"MT {cy}",
                                                 f"cop_{py}": f"COP {py}", f"cop_{cy}": f"COP {cy}", "dcop": "ΔCOP",
                                                 "volume": "of which volume", "price": "of which price",
                                                 "cost_mix": "of which cost/mix", "action": "What to do"})
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
        cols = ["customer", "status", "grow_tag", f"mt_{py}", f"mt_{cy}", "vol_pct", f"copt_{py}", f"copt_{cy}",
                f"cop_{py}", f"cop_{cy}", "dcop", "vol_eff", "price_eff", "cost_eff", "reading", "momentum"]
        st.dataframe(dd[cols].rename(columns=cols_map), hide_index=True, use_container_width=True,
                     column_config=_num_cfg(cols_map, cols))

    # ════════ Price → COP ════════
    with tabs[2]:
        c = t[t["status"] == "Continuing"].dropna(subset=["dprice", "dcopt"])
        cap = 400
        fig = go.Figure()
        for r_, color in zip(L.READINGS, [GREEN, "#4C956C", GOLD, RED, "#457B9D", "#8E5572", "#2E8449", "#BC6C25", GREY]):
            x = c[c["reading"] == r_]
            if x.empty:
                continue
            fig.add_trace(go.Scatter(x=x["dprice"].clip(-cap, cap), y=x["dcopt"].clip(-cap, cap), mode="markers",
                                     name=r_, marker=dict(color=color, size=8, opacity=0.8), text=x["customer"],
                                     hovertemplate="%{text}<br>Price Δ %{x:,.0f} $/t<br>COP/t Δ %{y:,.0f} $/t<extra></extra>"))
        fig.add_trace(go.Scatter(x=[-cap, cap], y=[-cap, cap], mode="lines", name="100% capture",
                                 line=dict(color=NAVY, dash="dash")))
        fig.update_layout(height=520, plot_bgcolor="white", xaxis_title="Price change ($/t)",
                          yaxis_title="COP per ton change ($/t)", legend=dict(orientation="h", y=-0.2),
                          margin=dict(l=10, r=10, t=30, b=10))
        fig.update_xaxes(gridcolor="#EEEEEE", zeroline=True, zerolinecolor=GREY)
        fig.update_yaxes(gridcolor="#EEEEEE", zeroline=True, zerolinecolor=GREY)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Continuing customers, values capped at ±{cap} $/t for readability. On the dashed line, every $1 "
                   "of price became $1 of COP per ton.")
        rd = R["readings"].rename(columns={"reading": "Reading", "customers": "Customers", "avg_dprice": "Avg price Δ $/t",
                                           "avg_dcopt": "Avg COP/t Δ $/t", "dcop": "ΔCOP", "price": "of which price",
                                           "cost_mix": "of which cost/mix", "meaning": "Meaning"})
        st.dataframe(rd, hide_index=True, use_container_width=True, column_config={
            c_: st.column_config.NumberColumn(c_, format="%,.0f") for c_ in rd.columns if c_ not in ("Reading", "Meaning")})
        st.subheader("Discounts that cut margin")
        dm = view[view["reading"] == "Discount cut margin"]
        cols = ["customer", "strategy", f"mt_{cy}", "dprice", "dcopt", f"cop_{py}", f"cop_{cy}", "dcop"]
        st.dataframe(dm[cols].sort_values("dcop").rename(columns=cols_map), hide_index=True, use_container_width=True,
                     column_config=_num_cfg(cols_map, cols))

    # ════════ Trends ════════
    with tabs[3]:
        st.subheader("Multi-year COP trend")
        tr = view[~view["trend"].str.startswith("Not active")].copy()
        tr["COP by year"] = tr[[f"cop_{y}" for y in years]].values.tolist()
        cols = ["customer", "trend", "COP by year"] + [f"cop_{y}" for y in years] + ["strategy"]
        st.dataframe(tr[cols].rename(columns=cols_map), hide_index=True, use_container_width=True,
                     column_config={**_num_cfg(cols_map, cols),
                                    "COP by year": st.column_config.LineChartColumn("COP by year")})
        st.caption(f"Customers with ≥ {s.min_mt:g} MT in every uploaded year. Label = direction of COP over the last "
                   "two changes (Growing ↑↑ · Recovering ↓↑ · Falling back ↑↓ · Declining ↓↓).")
        st.subheader(f"Momentum: last {s.recent_months} months of {cy} vs the same months of {py}")
        mo = view[view["momentum"] != ""]
        cols = ["customer", "momentum", "recent_copt_py", "recent_copt_cy", "strategy"]
        st.dataframe(mo[cols].rename(columns=cols_map), hide_index=True, use_container_width=True,
                     column_config=_num_cfg(cols_map, cols))
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
        st.caption("Top 30 customers by COP at stake (SAR k): Defend = −volume effect · Win back = PY COP · "
                   "Raise price = MT CY × max(0, COP/t PY − COP/t CY) · Grow = positive ΔCOP.")
        fl = L.focus_list(view)
        notes = st.session_state.setdefault("cop_focus_notes", {})
        cols = ["customer", "strategy", "at_stake", f"cop_{py}", f"cop_{cy}", "dcop", "momentum"]
        fe = fl[cols].rename(columns=cols_map).copy()
        for c_ in FOCUS_EDIT_COLS:
            fe[c_] = [notes.get(n, {}).get(c_, "") for n in fl["customer"]]
        edited = st.data_editor(fe, hide_index=True, use_container_width=True,
                                disabled=[cols_map[c_] for c_ in cols], key="cop_focus_ed",
                                column_config=_num_cfg(cols_map, cols))
        for _, r in edited.iterrows():
            notes[r["Customer"]] = {c_: r[c_] for c_ in FOCUS_EDIT_COLS}
        st.caption("Owner / Reason / Target / Next step are kept for this session and included in the Excel download. "
                   "Download to keep them.")

    # ════════ Cost risk ════════
    with tabs[5]:
        rk = R["risk"]
        tot_cy, tot_sc = rk[f"cop_{cy}"].sum(), rk["cop_scenario"].sum()
        a, b_, c_ = st.columns(3)
        a.metric(f"{cy} COP (actual)", _fmt_k(tot_cy))
        b_.metric(f"{cy} COP if cost/t = {py} level {s.cost_adjust:+.0%}", _fmt_k(tot_sc))
        c_.metric("Depends on lower costs", _fmt_k(tot_cy - tot_sc),
                  f"{(tot_cy - tot_sc) / (tot_cy - R['bridge']['cop_py']):.0%} of the {cy} COP gain"
                  if tot_cy != R["bridge"]["cop_py"] else None, delta_color="off")
        st.caption(f"Each customer's {cy} volume and price kept; cost per ton set back to its {py} level "
                   f"{s.cost_adjust:+.0%} (slider in the sidebar). Customers with no {py} volume keep their {cy} COP.")
        rv = rk[rk["customer"].isin(view["customer"])]
        cols = ["customer", "strategy", f"mt_{cy}", f"cost_{py}", f"cost_{cy}", "cost_scenario_t", f"cop_{cy}",
                "cop_scenario", "at_risk", "scenario_basis"]
        rmap = {**cols_map, "cost_scenario_t": "Scenario cost $/t", "cop_scenario": f"{cy} COP in scenario",
                "at_risk": "COP at risk", "scenario_basis": "Basis"}
        st.dataframe(rv[cols].rename(columns=rmap), hide_index=True, use_container_width=True,
                     column_config=_num_cfg(rmap, cols))

    # ════════ Data check ════════
    with tabs[6]:
        st.dataframe(chk, hide_index=True, use_container_width=True)
        st.markdown(f"""
- **Customers analysed:** everyone in the {py} or {cy} file (any month), as in the workbook - **{len(t):,}**.
- **Status:** MT under {s.min_mt:g} in both years → Low data · {py} only → New / regained · {cy} only → Lost / near-lost
  · otherwise Continuing.
- **Worth fighting for:** COP/t ≥ ${s.worth_copt:,.0f}/t ({cy} for continuing, {py} for lost customers) **or** inside
  the top {s.worth_top_share:.0%} of {py} COP (cumulative) - replaces the workbook's margin tier.
- **Momentum** compares the last {s.recent_months} months of {cy} with the same months of {py}.
- **Year KPIs** use every customer in that year's file. (The workbook's 2024 MT and COP only counted customers also
  present in 2025/2026, while its sales counted all - so its 2024 price/t was inconsistent.)
- Rows without a customer name are kept as **'{load.BLANK_CUSTOMER}'**.
""")

    # ── Export ──
    cust = t[list(cols_map)].rename(columns=cols_map)
    focus = L.focus_list(t)[["customer", "strategy", "at_stake", f"cop_{py}", f"cop_{cy}", "dcop"]].rename(columns=cols_map)
    notes = st.session_state.get("cop_focus_notes", {})
    for c_ in FOCUS_EDIT_COLS:
        focus[c_] = [notes.get(n, {}).get(c_, "") for n in focus["Customer"]]
    b = R["bridge"]
    sheets = {
        "KPIs": R["kpis"].rename(columns={"year": "Year", "mt": "MT", "sales": "Gross sales (SAR k)", "cop": "COP (SAR k)",
                                          "price_t": "Price $/t", "cost_t": "Cost $/t", "cop_t": "COP $/t",
                                          "active_customers": "Active customers"}),
        "Bridge": pd.DataFrame({"Step": [f"COP {py}", "Volume effect", "Price effect", "Cost / mix effect",
                                         "New / regained", "Lost / near-lost", "Low data", f"COP {cy}", "Check (must be 0)"],
                                "SAR k": [b["cop_py"], b["volume"], b["price"], b["cost_mix"], b["new"], b["lost"],
                                          b["low_data"], b["cop_cy"], b["check"]]}),
        "Strategies": st_tbl, "Customers": cust, "Price-COP readings": rd, "Focus list": focus,
        "Cost risk": R["risk"].rename(columns=rmap)[[rmap[c_] for c_ in ["customer", "strategy", f"mt_{cy}", f"cost_{py}",
                                                                        f"cost_{cy}", "cost_scenario_t", f"cop_{cy}",
                                                                        "cop_scenario", "at_risk", "scenario_basis"]]],
        "Settings": pd.DataFrame({"Setting": list(vars(s).keys())[:-1],
                                  "Value": [str(v) for k_, v in vars(s).items() if k_ != "actions"]}),
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
