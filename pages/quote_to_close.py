import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.auth import require_auth, has_permission
from q2c import load, analysis as an, export

KIND_FMT = {"int": "{:,.0f}", "mt": "{:,.2f}", "pct": "{:.1%}", "val": "{:,.0f}"}


@st.cache_data(show_spinner=False, max_entries=4)
def _rfq(content: bytes, name: str):
    return load.read_rfq(content, name)


@st.cache_data(show_spinner=False, max_entries=4)
def _inv(content: bytes, name: str):
    return load.read_invoices(content, name)


def _pct(v):
    return f"{v:.1%}" if pd.notna(v) else "—"


def _fmt(v, kind):
    return KIND_FMT[kind].format(v) if pd.notna(v) else "—"


def _summary_table(m: dict) -> pd.DataFrame:
    return pd.DataFrame([{"Key Metric": label, "Value": _fmt(m[k], kind), "% of total": _pct(m[pk]) if pk else ""}
                         for label, k, pk, kind in an.SUMMARY_ROWS])


def _compare_table(ma: dict, mb: dict) -> pd.DataFrame:
    lab_a = f"A: quotes {ma['quote_start']:%d %b} – {ma['quote_end']:%d %b %Y}"
    lab_b = f"B: quotes {mb['quote_start']:%d %b} – {mb['quote_end']:%d %b %Y}"
    rows = []
    for label, k, pk, kind in an.SUMMARY_ROWS:
        va, vb = ma[k], mb[k]
        if kind == "pct":
            diff = f"{(vb - va) * 100:+.1f} pts" if pd.notna(va) and pd.notna(vb) else "—"
        elif kind in ("int", "val"):
            diff = f"{vb - va:+,.0f}"
        else:
            diff = f"{vb - va:+,.2f}"
        rows.append({"Key Metric": label, lab_a: _fmt(va, kind) + (f"  ({_pct(ma[pk])})" if pk else ""),
                     lab_b: _fmt(vb, kind) + (f"  ({_pct(mb[pk])})" if pk else ""), "B − A": diff})
    rows.append({"Key Metric": "Sales window (invoice dates)",
                 lab_a: f"{ma['sales_start']:%d %b} – {ma['sales_end']:%d %b %Y}",
                 lab_b: f"{mb['sales_start']:%d %b} – {mb['sales_end']:%d %b %Y}", "B − A": ""})
    rows.append({"Key Metric": "Sales window complete?", lab_a: "Yes" if ma["complete"] else "No ⚠️",
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
    inv_files = c2.file_uploader("2 · Invoice Detail exports (one file per year)", type=["xls", "xlsx"],
                                 accept_multiple_files=True)
    st.caption("Files are processed in memory only. Nothing is saved to the database.")
    if not (rfq_file and inv_files):
        st.info("Upload the RFQ export and the Invoice Detail export(s) to begin. Sales = invoiced MT "
                "(\"$1 earned = $1 invoiced\").")
        st.stop()
    try:
        quotes, rfq_check = _rfq(rfq_file.getvalue(), rfq_file.name)
        inv_read = [_inv(f.getvalue(), f.name) for f in inv_files]
    except Exception as e:
        st.error(f"Could not read the files: {e}")
        st.stop()
    inv_checks = [c for _, c in inv_read]
    overlaps = load.overlapping_files(inv_checks)
    if overlaps:
        st.error("These invoice files cover overlapping dates, so invoices would be counted twice: "
                 + "; ".join(f"**{a}** and **{b}**" for a, b in overlaps) + ". Remove one of each pair.")
        st.stop()
    sales = pd.concat([d for d, _ in inv_read], ignore_index=True)

    # ── Sidebar: quote period, sales window, filters ──
    q_min, q_max = quotes["quote_day"].min().date(), quotes["quote_day"].max().date()
    st.sidebar.header("Quote period")
    days = st.sidebar.number_input("Days added after the quote period for sales", min_value=0, max_value=120,
                                   value=an.BUFFER_DAYS, step=1,
                                   help="Sets the default sales window: quote period start → quote period end + "
                                        "these days. Share of first invoices within N days of the quote "
                                        "(2026 data): 20 days 79.6% · 30 days 89.3% · 35 days 91.8%.")
    d_start, d_end = an.default_quote_period(quotes, sales, int(days))
    qp = st.sidebar.date_input("Quotes issued", value=(d_start.date(), d_end.date()), min_value=q_min,
                               max_value=q_max)
    if not isinstance(qp, tuple) or len(qp) != 2:
        st.info("Pick both a start and an end date for the quote period.")
        st.stop()
    s_def = an.default_sales_window(qp[0], qp[1], int(days))
    st.sidebar.header("Sales window")
    sp = st.sidebar.date_input("Invoiced (invoice date)", value=(s_def[0].date(), s_def[1].date()),
                               help="Sales Qty = sum of invoiced MT with an invoice date in this window. "
                                    "Default: quote period start → quote period end + the days above.")
    if not isinstance(sp, tuple) or len(sp) != 2:
        st.info("Pick both a start and an end date for the sales window.")
        st.stop()
    p = an.make_params(qp[0], qp[1], sp[0], sp[1], sales)

    table_all = an.build_table(quotes, sales, p)
    st.sidebar.header("Filters")
    areas = st.sidebar.multiselect("Area", sorted(table_all["area"].unique()))
    table = table_all[table_all["area"].isin(areas)] if areas else table_all

    st.caption(f"**Quotes {p.quote_start:%d %b %Y} – {p.quote_end:%d %b %Y} · invoices "
               f"{p.sales_start:%d %b %Y} – {p.sales_end:%d %b %Y}**" + (" · **filtered**" if areas else ""))
    if not p.complete:
        st.warning(f"**Sales window not complete:** it ends {p.sales_end:%d %b %Y}, but the invoice files only reach "
                   f"{p.export_day:%d %b %Y}. Invoices after that date are missing - some Lost quotations may still "
                   "close. Upload a newer invoice file or pick an earlier period.")
    if table.empty:
        st.warning("No quotations in this period / filter.")
        st.stop()

    m = an.summary(table)
    tabs = st.tabs(["📊 Summary", "📋 Quote to close", "👥 Pivot (by customer)", "🗺️ By area",
                    "📈 Compare periods", "🧾 Data check & how calculated"])

    # ════════ Summary ════════
    with tabs[0]:
        a, b, c, d = st.columns(4)
        a.metric("Total quotations", f"{m['total']:,}")
        b.metric("Closed (>= 1 order)", f"{m['closed']:,}")
        c.metric("Lost (zero sales)", f"{m['lost']:,}")
        d.metric("Quote-to-close ratio (by count)", _pct(m["ratio"]))
        st.dataframe(_summary_table(m), hide_index=True, use_container_width=True)

    # ════════ Quote to close ════════
    with tabs[1]:
        pick = st.multiselect("Status", an.STATUSES, default=[], placeholder="All")
        view = table if not pick else table[table["status"].isin(pick)]
        st.dataframe(view[["area", "customer", "item", "count", "quoted_mt", "avg_mt", "sales_mt", "last_quote_no",
                           "last_quote", "status", "invoice_lines", "sales_value"]],
                     hide_index=True, use_container_width=True,
                     column_config={"area": "Area", "customer": "Customer", "item": "Fact #",
                                    "count": st.column_config.NumberColumn("Count", format="%d"),
                                    "quoted_mt": st.column_config.NumberColumn("Quoted Qty", format="%,.2f"),
                                    "avg_mt": st.column_config.NumberColumn("AVG MT / Quote", format="%,.2f"),
                                    "sales_mt": st.column_config.NumberColumn("Sales Qty", format="%,.2f"),
                                    "last_quote_no": "Last quote #",
                                    "last_quote": st.column_config.DateColumn("Date", format="DD MMM YYYY"),
                                    "status": "Status",
                                    "invoice_lines": st.column_config.NumberColumn("Invoice lines in window",
                                                                                   format="%d"),
                                    "sales_value": st.column_config.NumberColumn("Invoiced value", format="%,.0f")})

    pv_cfg = {"count": st.column_config.NumberColumn("Sum of Count", format="%d"),
              "quoted_mt": st.column_config.NumberColumn("Sum of Quoted Qty", format="%,.2f"),
              "avg_mt": st.column_config.NumberColumn("Sum of AVG MT / Quote", format="%,.2f"),
              "sales_mt": st.column_config.NumberColumn("Sum of Sales Qty", format="%,.2f"),
              "fill_rate": st.column_config.NumberColumn("Fill rate (Sales / Quoted)", format="%.1%"),
              "items": st.column_config.NumberColumn("Quotations", format="%d"),
              "closed": st.column_config.NumberColumn("Closed", format="%d"),
              "sales_value": st.column_config.NumberColumn("Sum of Invoiced value", format="%,.0f")}
    pv_c = an.pivot(table, "customer")
    pv_a = an.pivot(table, "area")
    with tabs[2]:
        st.caption("As the PM's pivot: customers sorted by fill rate (Sales ÷ Quoted), highest first.")
        st.dataframe(pv_c[["customer", "count", "quoted_mt", "avg_mt", "sales_mt", "fill_rate", "sales_value", "items",
                           "closed"]],
                     hide_index=True, use_container_width=True, column_config={"customer": "Customer", **pv_cfg})
    with tabs[3]:
        st.dataframe(pv_a[["area", "count", "quoted_mt", "avg_mt", "sales_mt", "fill_rate", "sales_value", "items",
                           "closed"]],
                     hide_index=True, use_container_width=True, column_config={"area": "Area", **pv_cfg})

    # ════════ Compare periods ════════
    with tabs[4]:
        c0, c1 = st.columns([2, 1])
        mode = c0.radio("Compare", ["Two periods", "Monthly trend", "Quarterly trend"], horizontal=True)
        view_by = c1.selectbox("Compare by", ["Total", "Area", "Salesman", "Customer"])
        by = {"Area": "area", "Salesman": "salesman", "Customer": "customer_key"}.get(view_by)
        full_cmp, _ = an.period_table(quotes, sales, quotes["quote_day"].min(), quotes["quote_day"].max(), int(days))
        names = an.customer_names(full_cmp)
        label_of = (lambda v: names.get(v, v)) if by == "customer_key" else (lambda v: v)
        st.caption(f"Same method for every period: its quotations, invoices from the period start to the period "
                   f"end + {int(days)} days. Salesman = salesman on the Fact #'s latest quote line. "
                   "The sidebar area filter does not apply here - use 'Compare by'.")

        if mode == "Two periods":
            qs = an.calendar_periods(quotes, "Q")
            qa, qb = qs[0], (qs[1] if len(qs) > 1 else qs[0])
            c1, c2 = st.columns(2)
            pa = c1.date_input("Period A (quotes)", value=(qa[1].date(), min(qa[2].date(), q_max)),
                               min_value=q_min, max_value=q_max)
            pb = c2.date_input("Period B (quotes)", value=(qb[1].date(), min(qb[2].date(), q_max)),
                               min_value=q_min, max_value=q_max)
            if isinstance(pa, tuple) and len(pa) == 2 and isinstance(pb, tuple) and len(pb) == 2:
                ta, par_a = an.period_table(quotes, sales, pa[0], pa[1], int(days))
                tb, par_b = an.period_table(quotes, sales, pb[0], pb[1], int(days))
                if by is None:
                    st.dataframe(_compare_table(an._with_period(an.summary(ta), par_a),
                                                an._with_period(an.summary(tb), par_b)),
                                 hide_index=True, use_container_width=True)
                else:
                    all_label = f"All {view_by.lower()}s (one row each)"
                    options = [all_label] + sorted(set(ta[by]) | set(tb[by]), key=lambda v: str(label_of(v)).lower())
                    pick = st.selectbox(view_by, options, format_func=lambda v: v if v == all_label else label_of(v),
                                        help="Type to search.")
                    if pick == options[0]:
                        g = an.group_compare(ta, tb, by)
                        g[by] = g[by].map(label_of)
                        cfg = {by: view_by}
                        for key, label, kind in an.GROUP_COLUMNS:
                            for side in ("A", "B"):
                                fmt = "%.1%" if kind == "pct" else ("%d" if kind == "int" else "%,.0f")
                                cfg[f"{label} {side}"] = st.column_config.NumberColumn(f"{label} {side}", format=fmt)
                            if kind == "pct":
                                g[f"{label} Δ"] = g[f"{label} Δ"] * 100
                                cfg[f"{label} Δ"] = st.column_config.NumberColumn(f"{label} Δ (pts)", format="%+.1f")
                            else:
                                cfg[f"{label} Δ"] = st.column_config.NumberColumn(
                                    f"{label} Δ", format="%+d" if kind == "int" else "%+,.0f")
                        st.caption(f"A = quotes {par_a.quote_start:%d %b} – {par_a.quote_end:%d %b %Y} · "
                                   f"B = quotes {par_b.quote_start:%d %b} – {par_b.quote_end:%d %b %Y}. "
                                   "Sorted by quoted volume in B.")
                        st.dataframe(g, hide_index=True, use_container_width=True, column_config=cfg)
                    else:
                        st.dataframe(_compare_table(an._with_period(an.summary(ta[ta[by] == pick]), par_a),
                                                    an._with_period(an.summary(tb[tb[by] == pick]), par_b)),
                                     hide_index=True, use_container_width=True)
                if not (par_a.complete and par_b.complete):
                    st.warning("A period's sales window ends after the last invoice date - its numbers may still change.")
            else:
                st.info("Pick a start and an end date for both periods.")
        else:
            value = None
            if by:
                value = st.selectbox(view_by, sorted(full_cmp[by].unique(), key=lambda v: str(label_of(v)).lower()),
                                     format_func=label_of, help="Type to search.")
            t = an.trend(quotes, sales, "M" if mode == "Monthly trend" else "Q", int(days), by, value)
            done = t[t["complete"] & (t["total"] > 0)]
            fig = go.Figure()
            for key, name, color in (("ratio", "Quote-to-close ratio (by count) %", "#0D68A3"),
                                     ("a_closed_pct", "Quotation closed by AVG volume %", "#2E8449"),
                                     ("fill_quoted", "Volume fill rate (Sales / Quoted) %", "#C1A02E")):
                fig.add_trace(go.Scatter(x=done["period"], y=done[key] * 100, name=name, mode="lines+markers",
                                         line=dict(color=color, width=2.5)))
            fig.update_layout(height=380, plot_bgcolor="white", hovermode="x unified", yaxis_title="%",
                              title=dict(text=label_of(value) if value else "All quotations", x=0,
                                         font=dict(size=14, color="#0D68A3")),
                              legend=dict(orientation="h", y=1.12, x=0), margin=dict(l=10, r=10, t=60, b=10))
            fig.update_yaxes(gridcolor="#EEEEEE", rangemode="tozero")
            st.plotly_chart(fig, use_container_width=True)
            show_t = t.assign(complete=t["complete"].map({True: "Yes", False: "No ⚠️"}),
                              sales_end=t["sales_end"].dt.date)
            cols = ["period", "complete", "sales_end", "total", "closed", "lost", "ratio", "quoted", "q_closed",
                    "q_closed_pct", "sales", "fill_quoted", "fill_closed", "avg", "a_closed", "a_closed_pct"]
            st.dataframe(show_t[cols], hide_index=True, use_container_width=True, column_config={
                "period": "Period", "complete": "Complete?", "sales_end": "Invoices up to",
                "total": st.column_config.NumberColumn("Quotations", format="%d"),
                "closed": st.column_config.NumberColumn("Closed", format="%d"),
                "lost": st.column_config.NumberColumn("Lost", format="%d"),
                "ratio": st.column_config.NumberColumn("Ratio (count)", format="%.1%"),
                "quoted": st.column_config.NumberColumn("Quoted vol.", format="%,.0f"),
                "q_closed": st.column_config.NumberColumn("Closed vol.", format="%,.0f"),
                "q_closed_pct": st.column_config.NumberColumn("Closed vol. %", format="%.1%"),
                "sales": st.column_config.NumberColumn("Sales", format="%,.0f"),
                "fill_quoted": st.column_config.NumberColumn("Fill (Sales/Quoted)", format="%.1%"),
                "fill_closed": st.column_config.NumberColumn("Fill (Sales/Closed)", format="%.1%"),
                "avg": st.column_config.NumberColumn("Sum AVG", format="%,.0f"),
                "a_closed": st.column_config.NumberColumn("Closed AVG", format="%,.0f"),
                "a_closed_pct": st.column_config.NumberColumn("Closed AVG %", format="%.1%")})
            if (~t["complete"]).any():
                st.caption("Periods marked ⚠️ have a sales window that ends after the last invoice date; they are left "
                           "out of the chart.")

    # ════════ Data check & how calculated ════════
    with tabs[5]:
        st.subheader("Files")
        files = pd.DataFrame([{
            "File": f.name, "Lines": f.lines, "Footer line count": f.footer_count,
            "Lines match footer": "✅" if f.count_match else ("❌" if f.count_match is False else "—"),
            "MT": f.mt, "Footer MT": f.footer_mt,
            "MT matches footer": "✅" if f.mt_match else ("❌" if f.mt_match is False else "—"),
            "From": f.date_min.date(), "To": f.date_max.date(),
        } for f in [rfq_check] + sorted(inv_checks, key=lambda c: c.date_min)])
        st.dataframe(files, hide_index=True, use_container_width=True,
                     column_config={"MT": st.column_config.NumberColumn(format="%,.2f"),
                                    "Footer MT": st.column_config.NumberColumn(format="%,.2f")})
        if (files["Lines match footer"] == "❌").any() or (files["MT matches footer"] == "❌").any():
            st.error("A file doesn't add up to its own footer - check that export before using the results.")

        st.subheader("How every number is calculated (the PM's method)")
        st.markdown(f"""
| Number | How it is calculated | Value |
|---|---|---|
| **Quotation (line item)** | One Fact # quoted {p.quote_start:%d %b %Y} – {p.quote_end:%d %b %Y}. All order types and salesmen are included | {m['total']:,} |
| **Count** | Number of quote lines of the Fact # | |
| **Quoted Qty** | **Sum** of MT of all its quote lines | |
| **AVG MT / Quote** | Quoted Qty ÷ Count | |
| **Sales Qty** | **Sum** of invoiced MT (Shipped Tons) of the Fact # with invoice date {p.sales_start:%d %b %Y} – {p.sales_end:%d %b %Y}. The invoice files' *Fact Tic NB* is the Fact # | |
| **Invoiced value** | Sum of Invoice Amount of the same invoice lines | {m['sales_value']:,.0f} |
| **Closed / Lost** | Sales Qty > 0 / Sales Qty = 0 | {m['closed']:,} / {m['lost']:,} |
| **Quote-to-Close Ratio (by count)** | Closed ÷ Total = {m['closed']:,} ÷ {m['total']:,} | {_pct(m['ratio'])} |
| **Total Quoted Volume** | Sum of Quoted Qty | {m['quoted']:,.2f} |
| **Quotation closed / lost (by volume)** | Quoted Qty of closed / lost Fact #s | {m['q_closed']:,.2f} ({_pct(m['q_closed_pct'])}) / {m['q_lost']:,.2f} ({_pct(m['q_lost_pct'])}) |
| **Total Sales Volume Realised** | Sum of Sales Qty | {m['sales']:,.2f} |
| **Volume Fill Rate (Sales / Quoted volume)** | {m['sales']:,.2f} ÷ {m['quoted']:,.2f} | {_pct(m['fill_quoted'])} |
| **Volume Fill Rate (Sales / Quotation closed)** | {m['sales']:,.2f} ÷ {m['q_closed']:,.2f} | {_pct(m['fill_closed'])} |
| **Sum of AVG Quoted volume** | Sum of AVG MT / Quote | {m['avg']:,.2f} |
| **Quotation closed / lost (by AVG volume)** | AVG of closed / lost Fact #s | {m['a_closed']:,.2f} ({_pct(m['a_closed_pct'])}) / {m['a_lost']:,.2f} ({_pct(m['a_lost_pct'])}) |
""")
        st.markdown("""
- **Area** = the customer's Area in the quote export, except the customers the PM re-labels (list below).
- **Customer** = the Fact #'s first quote line; **Last quote # / Date** = its latest quote line.
- **Pivot** groups customer names that differ only in upper/lower case into one row, like an Excel pivot.
- The Excel workbook has the same layout as the PM's (Summary, Pivot, Quote to close, Raw data) with every number
  as a **live formula**; change the sales window on its *Parameters* sheet and everything recalculates.
""")
        with st.expander("Customers the PM re-labels to his own Area"):
            st.dataframe(pd.DataFrame(sorted(an.PM_AREA_OVERRIDES.items()), columns=["Customer", "Area"]),
                         hide_index=True, use_container_width=True)
            st.caption("To change this list, edit PM_AREA_OVERRIDES in q2c/analysis.py.")

    # ── Export ──
    ql = quotes[(quotes["quote_day"] >= p.quote_start) & (quotes["quote_day"] <= p.quote_end)
                & quotes["item"].isin(table["item"])]
    sl = sales[sales["item"].isin(table["item"])]
    st.download_button(
        "⬇️ Quote-to-close workbook (.xlsx)",
        export.build_workbook(table, ql, sl, p, pv_c["customer"].iloc[:-1].tolist(),
                              pv_a["area"].iloc[:-1].tolist(),
                              f"Source: {rfq_file.name} + " + ", ".join(f.name for f in inv_files)
                              + (f" · Area = {areas}" if areas else "")),
        file_name=f"quote_to_close_{p.quote_start:%Y%m%d}_{p.quote_end:%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
