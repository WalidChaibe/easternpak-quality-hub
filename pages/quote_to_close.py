import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.auth import require_auth, has_permission
from forecast.clean import EXCLUDED_SALESMEN
from q2c import load, analysis as an, export

BREAKDOWNS = {
    "Area": "area",
    "Product group (Box Style Group)": "product_group",
    "Salesman": "salesman",
    "Latest quote order type": "latest_order_type",
    "Number of quotations sent": "quote_bucket",
}
FILL_BLANK = ["customer", "area", "product_group", "salesman", "latest_order_type"]


@st.cache_data(show_spinner=False, max_entries=4)
def _rfq(content: bytes, name: str):
    return load.read_rfq(content, name)


@st.cache_data(show_spinner=False, max_entries=4)
def _sc(content: bytes, name: str):
    return load.read_sc(content, name)


def _exclude(df: pd.DataFrame):
    names = df["salesman"].str.upper().str.strip()
    mask = names.isin(EXCLUDED_SALESMEN.keys())
    return df[~mask].copy(), df[mask].copy()


def _fill_blank(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = df[c].replace("", "(blank)")
    return df


def _pct(v):
    return f"{v:.1%}" if pd.notna(v) else "—"


def _table_config():
    return {
        "quotes": st.column_config.NumberColumn("FTs quoted", format="%d"),
        "closed": st.column_config.NumberColumn("Closed", format="%d"),
        "lost": st.column_config.NumberColumn("Lost", format="%d"),
        "close_rate": st.column_config.NumberColumn("Close rate", format="%.1%"),
        "qty_quoted": st.column_config.NumberColumn("Qty quoted (MT)", format="%,.1f"),
        "qty_sold": st.column_config.NumberColumn("Qty sold (MT)", format="%,.1f"),
        "fill_rate": st.column_config.NumberColumn("Fill rate", format="%.1%"),
    }


def _compare_table(ma: dict, mb: dict) -> pd.DataFrame:
    lab_a = f"A: {ma['period_start']:%d %b} – {ma['period_end']:%d %b %Y}"
    lab_b = f"B: {mb['period_start']:%d %b} – {mb['period_end']:%d %b %Y}"
    rows = []
    for key, label, kind in an.COMPARE_ROWS:
        va, vb = ma[key], mb[key]
        ok = pd.notna(va) and pd.notna(vb)
        if kind == "pct":
            fa, fb, diff = _pct(va), _pct(vb), (f"{(vb - va) * 100:+.1f} pts" if ok else "—")
        elif kind == "mt":
            fa, fb, diff = f"{va:,.0f}", f"{vb:,.0f}", f"{vb - va:+,.0f}"
        elif kind == "dec":
            fa = f"{va:.2f}" if pd.notna(va) else "—"
            fb = f"{vb:.2f}" if pd.notna(vb) else "—"
            diff = f"{vb - va:+.2f}" if ok else "—"
        else:
            fa, fb, diff = f"{va:,}", f"{vb:,}", f"{vb - va:+,}"
        rows.append({"Metric": label, lab_a: fa, lab_b: fb, "B − A": diff})
    rows.append({"Metric": "Orders counted up to", lab_a: f"{ma['order_cutoff']:%d %b %Y}",
                 lab_b: f"{mb['order_cutoff']:%d %b %Y}", "B − A": ""})
    rows.append({"Metric": "Period complete?", lab_a: "Yes" if ma["complete"] else "No ⚠️",
                 lab_b: "Yes" if mb["complete"] else "No ⚠️", "B − A": ""})
    return pd.DataFrame(rows)


def show():
    require_auth()
    if not has_permission("quote2close"):
        st.error("You don't have access to this page.")
        st.stop()

    st.title("📑 Quote to Close")
    c1, c2 = st.columns(2)
    rfq_file = c1.file_uploader("1 · RFQ (quotation) export", type=["xls", "xlsx"])
    sc_file = c2.file_uploader("2 · Sales order (SC) export", type=["xls", "xlsx"])
    st.caption("Files are processed in memory only. Nothing is saved to the database.")
    if not (rfq_file and sc_file):
        st.info("Upload the RFQ export and the sales order (SC) export to begin.")
        st.stop()

    try:
        quotes_all, rfq_check = _rfq(rfq_file.getvalue(), rfq_file.name)
        orders_all, sc_check = _sc(sc_file.getvalue(), sc_file.name)
    except Exception as e:
        st.error(f"Could not read the files: {e}")
        st.stop()

    quotes_kept, quotes_excl = _exclude(quotes_all)
    orders_kept, orders_excl = _exclude(orders_all)
    quotes_pack, quotes_waste = an.split_waste(quotes_kept)

    # ── Sidebar: days allowed, period, waste, filters ──
    q_min, q_max = quotes_all["quote_day"].min().date(), quotes_all["quote_day"].max().date()
    st.sidebar.header("Period")
    buffer_days = st.sidebar.number_input(
        "Days allowed for orders after the period", min_value=0, max_value=90, value=an.BUFFER_DAYS, step=1,
        help="Orders entered up to period end + this many days are counted. 20 days = 90% of orders (2026 data).")
    d_start, d_end = an.default_period(orders_kept, int(buffer_days))
    period = st.sidebar.date_input(
        "Quote period", value=(max(d_start.date(), q_min), max(min(d_end.date(), q_max), q_min)),
        min_value=q_min, max_value=q_max,
        help="Default: the latest month that already has its full order window in the SC file.")
    if not isinstance(period, tuple) or len(period) != 2:
        st.info("Pick both a start and an end date for the quote period.")
        st.stop()
    include_waste = st.sidebar.toggle("Include waste sales quotes in KPIs", value=False,
                                      help="'Open Waste Order' quotes (scrap cartons, plastic, steel). "
                                           "Off = as in the COO's analysis.")
    p = an.make_params(period[0], period[1], int(buffer_days), orders_kept)

    quotes_used = quotes_kept if include_waste else quotes_pack
    items_all = _fill_blank(an.build_items(quotes_used, orders_kept, p), FILL_BLANK)

    st.sidebar.header("Filters")
    areas = st.sidebar.multiselect("Area", sorted(items_all["area"].unique()))
    salesmen = st.sidebar.multiselect("Salesman", sorted(items_all["salesman"].unique()))
    items = items_all
    if areas:
        items = items[items["area"].isin(areas)]
    if salesmen:
        items = items[items["salesman"].isin(salesmen)]
    filtered = bool(areas or salesmen)

    period_text = f"Quotes {p.period_start:%d %b %Y} – {p.period_end:%d %b %Y} · orders up to {p.order_cutoff:%d %b %Y}"
    st.caption(f"**{period_text}** (period end + {p.buffer_days} days)" + (" · **filtered**" if filtered else ""))
    if not p.complete:
        st.warning(f"**Incomplete period:** orders should be counted up to "
                   f"{p.period_end + pd.Timedelta(days=p.buffer_days):%d %b %Y}, but the SC file only reaches "
                   f"{p.last_order_day:%d %b %Y}. Some FTs counted as Lost may still order - upload a newer SC "
                   "export or pick an earlier period.")
    if items.empty:
        st.warning("No quotes in this period / filter.")
        st.stop()

    s = an.summary(items)
    tab_sum, tab_cmp, tab_break, tab_lost, tab_items, tab_check = st.tabs(
        ["📊 Summary", "📈 Compare periods", "🧩 Breakdowns", "❌ Lost opportunities", "📋 Items",
         "🧾 Data check & how calculated"])

    # ════════ Summary ════════
    with tab_sum:
        a, b, c, d_ = st.columns(4)
        a.metric("FTs quoted", f"{s['items']:,}", help="Unique FTs (Fact #) quoted in the period.")
        b.metric("Closed", f"{s['closed']:,}", help="At least one order in the order window.")
        c.metric("Lost", f"{s['lost']:,}", help="No order in the order window.")
        d_.metric("Quote-to-close ratio", _pct(s["close_rate"]), help="Closed ÷ FTs quoted.")
        a, b, c, d_ = st.columns(4)
        a.metric("Quoted volume", f"{s['quoted_mt']:,.0f} MT", help="Sum of each FT's average MT per quote.")
        b.metric("… of which closed FTs", f"{s['quoted_closed_mt']:,.0f} MT")
        c.metric("… of which lost FTs", f"{s['lost_mt']:,.0f} MT", help="Lost quoted volume: offered, never ordered.")
        d_.metric("Sales volume", f"{s['sales_mt']:,.0f} MT", help="Sum of ALL order lines in the order window.")
        a, b, c, d_ = st.columns(4)
        a.metric("Volume fill rate", _pct(s["fill_rate"]), help="Sales ÷ total quoted volume.")
        b.metric("Sold ÷ quoted, closed FTs", _pct(s["closed_sold_ratio"]),
                 help="Sales ÷ quoted volume of the closed FTs. Above 100% = more ordered than quoted (repeat releases).")
        c.metric("Median days to first order",
                 f"{s['median_days_to_order']:.0f}" if pd.notna(s["median_days_to_order"]) else "—")

        st.subheader("Quotation effort")
        a, b, c, d_ = st.columns(4)
        a.metric("Avg quotes sent — closed", f"{s['avg_quotes_closed']:.2f}" if pd.notna(s["avg_quotes_closed"]) else "—")
        b.metric("Avg quotes sent — lost", f"{s['avg_quotes_lost']:.2f}" if pd.notna(s["avg_quotes_lost"]) else "—")
        c.metric("Lost, quoted once", f"{s['lost_once']:,}")
        d_.metric("Lost, quoted 2+ times", f"{s['lost_repeated']:,}",
                  f"{_pct(s['lost_repeated_share'])} of lost", delta_color="off")

        w = quotes_waste[(quotes_waste["quote_day"] >= p.period_start) & (quotes_waste["quote_day"] <= p.period_end)]
        if len(w):
            st.caption(f"**Waste sales quotes** (Order Type 'Open Waste Order') in this period: {len(w)} lines · "
                       f"{w['mt'].sum():,.1f} MT · " + ("**included** in the KPIs above." if include_waste
                                                        else "**not** in the KPIs above (toggle in the sidebar)."))

    # ════════ Compare periods ════════
    with tab_cmp:
        mode = st.radio("Compare", ["Two periods", "Monthly trend", "Quarterly trend"], horizontal=True)
        st.caption(f"Every period uses the same rule: its quotes, orders up to period end + {p.buffer_days} days. "
                   "The area / salesman filters and the waste toggle apply here too.")
        quotes_cmp = _fill_blank(quotes_used.copy(), ["area", "salesman"])
        if areas:
            quotes_cmp = quotes_cmp[quotes_cmp["area"].isin(areas)]
        if salesmen:
            quotes_cmp = quotes_cmp[quotes_cmp["salesman"].isin(salesmen)]

        if quotes_cmp.empty:
            st.info("No quotes for this filter.")
        elif mode == "Two periods":
            quarters = an.calendar_periods(quotes_all, "Q")
            qa = quarters[0]
            qb = quarters[1] if len(quarters) > 1 else qa
            c1, c2 = st.columns(2)
            pa = c1.date_input("Period A (quotes)", value=(qa[1].date(), min(qa[2].date(), q_max)),
                               min_value=q_min, max_value=q_max)
            pb = c2.date_input("Period B (quotes)", value=(qb[1].date(), min(qb[2].date(), q_max)),
                               min_value=q_min, max_value=q_max)
            if not (isinstance(pa, tuple) and len(pa) == 2 and isinstance(pb, tuple) and len(pb) == 2):
                st.info("Pick a start and an end date for both periods.")
            else:
                ma = an.period_metrics(quotes_cmp, orders_kept, pa[0], pa[1], p.buffer_days)
                mb = an.period_metrics(quotes_cmp, orders_kept, pb[0], pb[1], p.buffer_days)
                st.dataframe(_compare_table(ma, mb), hide_index=True, use_container_width=True)
                if not (ma["complete"] and mb["complete"]):
                    st.warning("A period marked ⚠️ doesn't have its full order window in the SC file yet - its Lost "
                               "count may still fall. Compare complete periods only, or upload a newer SC export.")
        else:
            freq = "M" if mode == "Monthly trend" else "Q"
            t = an.trend(quotes_cmp, orders_kept, freq, p.buffer_days)
            done = t[t["complete"]]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=done["period"], y=done["close_rate"] * 100, name="Quote-to-close ratio %",
                                     mode="lines+markers", line=dict(color="#0D68A3", width=2.5)))
            fig.add_trace(go.Scatter(x=done["period"], y=done["fill_rate"] * 100, name="Volume fill rate %",
                                     mode="lines+markers", line=dict(color="#C1A02E", width=2.5)))
            fig.update_layout(height=380, plot_bgcolor="white", hovermode="x unified", yaxis_title="%",
                              legend=dict(orientation="h", y=1.1, x=0), margin=dict(l=10, r=10, t=40, b=10))
            fig.update_yaxes(gridcolor="#EEEEEE", rangemode="tozero")
            st.plotly_chart(fig, use_container_width=True)
            show_t = t.assign(complete=t["complete"].map({True: "Yes", False: "No ⚠️"}),
                              order_cutoff=t["order_cutoff"].dt.date)
            st.dataframe(
                show_t[["period", "complete", "order_cutoff", "items", "closed", "lost", "close_rate", "quoted_mt",
                        "quoted_closed_mt", "lost_mt", "sales_mt", "fill_rate", "closed_sold_ratio"]],
                hide_index=True, use_container_width=True,
                column_config={"period": "Period", "complete": "Complete?", "order_cutoff": "Orders up to",
                               "items": st.column_config.NumberColumn("FTs quoted", format="%d"),
                               "closed": st.column_config.NumberColumn("Closed", format="%d"),
                               "lost": st.column_config.NumberColumn("Lost", format="%d"),
                               "close_rate": st.column_config.NumberColumn("Close rate", format="%.1%"),
                               "quoted_mt": st.column_config.NumberColumn("Quoted MT", format="%,.0f"),
                               "quoted_closed_mt": st.column_config.NumberColumn("… closed FTs", format="%,.0f"),
                               "lost_mt": st.column_config.NumberColumn("… lost FTs", format="%,.0f"),
                               "sales_mt": st.column_config.NumberColumn("Sales MT", format="%,.0f"),
                               "fill_rate": st.column_config.NumberColumn("Fill rate", format="%.1%"),
                               "closed_sold_ratio": st.column_config.NumberColumn("Sold ÷ quoted, closed",
                                                                                  format="%.1%")})
            if (~t["complete"]).any():
                st.caption("Periods marked ⚠️ don't have their full order window in the SC file yet; they are "
                           "left out of the chart.")

    # ════════ Breakdowns ════════
    with tab_break:
        dim_label = st.radio("Break down by", list(BREAKDOWNS), horizontal=True)
        t = an.breakdown(items, BREAKDOWNS[dim_label]).rename(columns={BREAKDOWNS[dim_label]: dim_label})
        t[dim_label] = t[dim_label].astype(str)
        st.dataframe(t, hide_index=True, use_container_width=True, column_config=_table_config())

    # ════════ Lost opportunities ════════
    with tab_lost:
        st.subheader(f"Top {an.TOP_LOST} customers by lost quoted volume")
        st.dataframe(an.top_lost(items), hide_index=True, use_container_width=True,
                     column_config={"customer": "Customer",
                                    "lost_quotes": st.column_config.NumberColumn("# Lost FTs", format="%d"),
                                    "lost_mt": st.column_config.NumberColumn("Lost quoted volume (MT)", format="%,.1f")})
        rep = an.repeated_no_order(items)
        st.subheader(f"Quoted 2+ times, still no order — {len(rep)} FTs")
        st.dataframe(rep, hide_index=True, use_container_width=True,
                     column_config={"area": "Area", "customer": "Customer", "item": "FT",
                                    "product_group": "Product group",
                                    "count": st.column_config.NumberColumn("# Quotations sent", format="%d"),
                                    "avg_mt": st.column_config.NumberColumn("Avg Qty/Quote (MT)", format="%,.2f"),
                                    "salesman": "Salesman",
                                    "last_quote": st.column_config.DateColumn("Last quotation", format="DD MMM YYYY")})

    # ════════ Items ════════
    with tab_items:
        pick = st.multiselect("Status", an.STATUSES, default=[], placeholder="All statuses")
        view = items if not pick else items[items["status"].isin(pick)]
        cols = ["item", "customer", "area", "product_group", "salesman", "latest_order_type", "count", "avg_mt",
                "first_quote", "last_quote", "last_quote_no", "sales_mt", "order_lines", "orders", "status",
                "var_mt", "days_to_first_order"]
        st.dataframe(view[cols].sort_values("avg_mt", ascending=False), hide_index=True, use_container_width=True,
                     column_config={"item": "FT", "customer": "Customer", "area": "Area",
                                    "product_group": "Product group", "salesman": "Salesman",
                                    "latest_order_type": "Latest order type",
                                    "count": st.column_config.NumberColumn("Count", format="%d"),
                                    "avg_mt": st.column_config.NumberColumn("AVG Qty/Quote", format="%,.2f"),
                                    "first_quote": st.column_config.DateColumn("First quote", format="DD MMM YYYY"),
                                    "last_quote": st.column_config.DateColumn("Last quote", format="DD MMM YYYY"),
                                    "last_quote_no": "Last quote #",
                                    "sales_mt": st.column_config.NumberColumn("Sales Qty", format="%,.2f"),
                                    "order_lines": st.column_config.NumberColumn("Order lines", format="%d"),
                                    "orders": st.column_config.NumberColumn("Orders (SC #)", format="%d"),
                                    "status": "Status",
                                    "var_mt": st.column_config.NumberColumn("Var", format="%+,.2f"),
                                    "days_to_first_order": st.column_config.NumberColumn("Days to 1st order",
                                                                                         format="%d")})

    # ════════ Data check & how calculated ════════
    with tab_check:
        st.subheader("Files")
        files = pd.DataFrame([{
            "File": f.name, "Lines": f.lines, "Footer line count": f.footer_count,
            "Lines match footer": "✅" if f.count_match else ("❌" if f.count_match is False else "—"),
            "MT": f.mt, "Footer MT": f.footer_mt,
            "MT matches footer": "✅" if f.mt_match else ("❌" if f.mt_match is False else "—"),
            "From": f.date_min.date(), "To": f.date_max.date(),
        } for f in (rfq_check, sc_check)])
        st.dataframe(files, hide_index=True, use_container_width=True,
                     column_config={"MT": st.column_config.NumberColumn(format="%,.2f"),
                                    "Footer MT": st.column_config.NumberColumn(format="%,.2f")})
        if (files["Lines match footer"] == "❌").any() or (files["MT matches footer"] == "❌").any():
            st.error("A file doesn't add up to its own footer - check that export before using the results.")

        if len(quotes_excl) or len(orders_excl):
            st.markdown(f"**Excluded before analysis** "
                        f"({', '.join(f'{k} - {v}' for k, v in EXCLUDED_SALESMEN.items())}): "
                        f"{len(quotes_excl):,} quote lines ({quotes_excl['mt'].sum():,.1f} MT) and "
                        f"{len(orders_excl):,} order lines ({orders_excl['mt'].sum():,.1f} MT).")

        st.subheader("How every number is calculated")
        st.markdown(f"""
| Number | How it is calculated | Value |
|---|---|---|
| **FT** | One Fact # quoted between {p.period_start:%d %b %Y} and {p.period_end:%d %b %Y} | {s['items']:,} |
| **Count** | Number of quote lines for the FT in the period | |
| **AVG Qty/Quote** | Average MT of the FT's quote lines = its quoted volume | |
| **Order window** | From the FT's first quote date up to period end + {p.buffer_days} days = {p.order_cutoff:%d %b %Y} | |
| **Sales Qty** | **Sum** of the MT of **all** order lines (SC export) for the FT in its order window | |
| **Closed** | Sales Qty > 0 (at least one order) | {s['closed']:,} |
| **Lost** | Sales Qty = 0 (no order) | {s['lost']:,} |
| **Quote-to-close ratio** | Closed ÷ FTs quoted = {s['closed']:,} ÷ {s['items']:,} | {_pct(s['close_rate'])} |
| **Quoted volume** | Sum of AVG Qty/Quote = closed FTs {s['quoted_closed_mt']:,.2f} + lost FTs {s['lost_mt']:,.2f} | {s['quoted_mt']:,.2f} MT |
| **Lost quoted volume** | Quoted volume of the lost FTs: offered, never ordered | {s['lost_mt']:,.2f} MT |
| **Sales volume** | Sum of Sales Qty | {s['sales_mt']:,.2f} MT |
| **Volume fill rate** | Sales ÷ total quoted = {s['sales_mt']:,.2f} ÷ {s['quoted_mt']:,.2f} | {_pct(s['fill_rate'])} |
| **Sold ÷ quoted, closed FTs** | Sales ÷ quoted volume of closed FTs = {s['sales_mt']:,.2f} ÷ {s['quoted_closed_mt']:,.2f} | {_pct(s['closed_sold_ratio'])} |
""")
        st.markdown("""
- **Why 20 days:** on 2026 data, 80.1% of orders are entered within 7 days of the quote, 87.8% within 15,
  **90.0% within 20** and 92.7% within 30.
- **Complete period:** the SC file reaches period end + 20 days. Otherwise the period is flagged - some Lost FTs
  may still order.
- **Waste sales quotes** ('Open Waste Order') are kept out of the KPIs unless the sidebar toggle is on, as in the
  COO's analysis. They are counted and shown, never deleted.
- **Area and Product group** come from the FT's first quote line; **Salesman** and **Latest order type** from its
  most recent quote line. Product group is the export's *Box Style Group* (the COO's "Building block" is not in
  either export).
- **Fill rate above 100%** is normal: one quote can lead to several orders (repeat releases).
- The Excel workbook contains the same calculations as **live formulas**: change *Days allowed for orders* on its
  *Parameters* sheet and every number recalculates.
""")

    # ── Export ──
    ql = quotes_used[(quotes_used["quote_day"] >= p.period_start) & (quotes_used["quote_day"] <= p.period_end)]
    ql = _fill_blank(ql[ql["item"].isin(items["item"])].copy(), ["area", "product_group", "salesman", "customer"])
    ol = orders_kept[orders_kept["item"].isin(items["item"])]
    wl = quotes_waste[(quotes_waste["quote_day"] >= p.period_start) & (quotes_waste["quote_day"] <= p.period_end)]
    note = ""
    if len(quotes_excl) or len(orders_excl):
        note = (f"Excluded before analysis: {', '.join(EXCLUDED_SALESMEN)} (e-shop) - {len(quotes_excl)} quote lines, "
                f"{len(orders_excl)} order lines.")
    if filtered:
        note += f"  Filtered: area = {areas or 'all'}; salesman = {salesmen or 'all'}."
    st.download_button(
        "⬇️ Quote-to-close workbook (.xlsx)",
        export.build_workbook(items, ql, ol, wl.iloc[0:0] if include_waste else wl, p, period_text,
                              f"Source: {rfq_file.name} + {sc_file.name}", note),
        file_name=f"quote_to_close_{p.period_start:%Y%m%d}_{p.period_end:%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
