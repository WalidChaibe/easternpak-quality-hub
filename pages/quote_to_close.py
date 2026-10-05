import io

import pandas as pd
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


@st.cache_data(show_spinner=False, max_entries=2)
def _earlier(content: bytes):
    raw = pd.read_excel(io.BytesIO(content), sheet_name="Raw Data")
    missing = [c for c in ("Item", "Sales Qty") if c not in raw.columns]
    if missing:
        raise ValueError(f"no 'Raw Data' sheet with columns Item and Sales Qty (missing {missing})")
    return raw


def _exclude(df: pd.DataFrame):
    names = df["salesman"].str.upper().str.strip()
    mask = names.isin(EXCLUDED_SALESMEN.keys())
    return df[~mask].copy(), df[mask].copy()


def _fill_blank(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = df[c].replace("", "(blank)")
    return df


def _table_config():
    return {
        "quotes": st.column_config.NumberColumn("Quotes", format="%d"),
        "closed": st.column_config.NumberColumn("Closed", format="%d"),
        "lost": st.column_config.NumberColumn("Lost", format="%d"),
        "open": st.column_config.NumberColumn("Open", format="%d"),
        "close_rate": st.column_config.NumberColumn("Close rate", format="%.1%"),
        "qty_quoted": st.column_config.NumberColumn("Qty quoted (MT)", format="%,.1f"),
        "qty_sold": st.column_config.NumberColumn("Qty sold (MT)", format="%,.1f"),
        "fill_rate": st.column_config.NumberColumn("Fill rate", format="%.1%"),
    }


def _pct(v):
    return f"{v:.1%}" if pd.notna(v) else "—"


def show():
    require_auth()
    if not has_permission("quote2close"):
        st.error("You don't have access to this page.")
        st.stop()

    st.title("📑 Quote to Close")
    c1, c2, c3 = st.columns(3)
    rfq_file = c1.file_uploader("1 · RFQ (quotation) export", type=["xls", "xlsx"])
    sc_file = c2.file_uploader("2 · Sales order (SC) export", type=["xls", "xlsx"])
    earlier_file = c3.file_uploader("3 · Earlier analysis to compare (optional)", type=["xlsx"],
                                    help="E.g. the COO's workbook - needs a 'Raw Data' sheet with Item and Sales Qty.")
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

    # ── Sidebar: period, cutoff, buffer, filters ──
    d = an.default_params(orders_kept)
    q_min, q_max = quotes_all["quote_day"].min().date(), quotes_all["quote_day"].max().date()
    st.sidebar.header("Period")
    period = st.sidebar.date_input("Quote period",
                                   value=(max(d.period_start.date(), q_min), min(d.period_end.date(), q_max)),
                                   min_value=q_min, max_value=q_max,
                                   help="Default: the month ending 20 days before the last order date.")
    if not isinstance(period, tuple) or len(period) != 2:
        st.info("Pick both a start and an end date for the quote period.")
        st.stop()
    o_max = orders_kept["order_day"].max().date()
    cutoff = st.sidebar.date_input("Count orders up to", value=o_max, min_value=period[1], max_value=o_max,
                                   help="Default: the last order date in the SC export.")
    buffer_days = st.sidebar.number_input("Buffer days", min_value=0, max_value=90, value=an.BUFFER_DAYS, step=1,
                                          help="Quotes newer than this before the cutoff, with no order yet, are Open "
                                               "instead of Lost. 20 days captures 90% of orders (2026 data).")
    include_waste = st.sidebar.toggle("Include waste sales quotes in KPIs", value=False,
                                      help="'Open Waste Order' quotes (scrap cartons, plastic, steel). "
                                           "Off = as in the COO's analysis.")
    p = an.Params(pd.Timestamp(period[0]), pd.Timestamp(period[1]), pd.Timestamp(cutoff), int(buffer_days))

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

    ok, gap = an.buffer_ok(p)
    period_text = f"Quotes {p.period_start:%d %b %Y} – {p.period_end:%d %b %Y} · orders up to {p.order_cutoff:%d %b %Y}"
    st.caption(f"**{period_text}** · buffer {p.buffer_days} days" + (" · **filtered**" if filtered else ""))
    if not ok:
        st.warning(f"The quote period ends only **{gap} days** before the order cutoff (buffer: {p.buffer_days} days). "
                   f"Items last quoted after {p.order_cutoff - pd.Timedelta(days=p.buffer_days):%d %b %Y} with no "
                   "order yet are shown as **Open**, not Lost.")
    if items.empty:
        st.warning("No quotes in this period / filter.")
        st.stop()

    s = an.summary(items)
    tab_sum, tab_break, tab_lost, tab_items, tab_rec, tab_check = st.tabs(
        ["📊 Summary", "🧩 Breakdowns", "❌ Lost opportunities", "📋 Items", "🔁 Reconciliation",
         "🧾 Data check & how calculated"])

    # ════════ Summary ════════
    with tab_sum:
        a, b, c, d_, e = st.columns(5)
        a.metric("Items quoted", f"{s['items']:,}")
        b.metric("Closed", f"{s['closed']:,}")
        c.metric("Lost", f"{s['lost']:,}")
        d_.metric("Open", f"{s['open']:,}", help="Quoted less than the buffer before the cutoff, no order yet.")
        e.metric("Quote-to-close ratio", _pct(s["close_rate"]), help="Closed ÷ (Closed + Lost).")
        a, b, c, d_, e = st.columns(5)
        a.metric("Quoted volume", f"{s['quoted_mt']:,.0f} MT", help="Sum of each item's average MT per quote.")
        b.metric("Sales volume", f"{s['sales_mt']:,.0f} MT", help="Sum of ALL order lines from first quote to cutoff.")
        c.metric("Volume fill rate", _pct(s["fill_rate"]),
                 help="Sales ÷ Quoted. Above 100% = more ordered than one quote's quantity (repeat releases).")
        d_.metric("Lost quoted volume", f"{s['lost_mt']:,.0f} MT")
        e.metric("Median days to first order",
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

    # ════════ Breakdowns ════════
    with tab_break:
        dim_label = st.radio("Break down by", list(BREAKDOWNS), horizontal=True)
        t = an.breakdown(items, BREAKDOWNS[dim_label]).rename(columns={BREAKDOWNS[dim_label]: dim_label})
        t[dim_label] = t[dim_label].astype(str)
        st.dataframe(t, hide_index=True, use_container_width=True, column_config=_table_config())
        if BREAKDOWNS[dim_label] == "quote_bucket":
            st.caption("Close rate rises with more quotations sent: items still being re-quoted usually have live "
                       "customer engagement.")

    # ════════ Lost opportunities ════════
    with tab_lost:
        st.subheader(f"Top {an.TOP_LOST} customers by lost quoted volume")
        st.dataframe(an.top_lost(items), hide_index=True, use_container_width=True,
                     column_config={"customer": "Customer",
                                    "lost_quotes": st.column_config.NumberColumn("# Lost items", format="%d"),
                                    "lost_mt": st.column_config.NumberColumn("Lost quoted volume (MT)", format="%,.1f")})
        rep = an.repeated_no_order(items)
        st.subheader(f"Quoted 2+ times, still no order — {len(rep)} items")
        st.dataframe(rep, hide_index=True, use_container_width=True,
                     column_config={"area": "Area", "customer": "Customer", "item": "Item",
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
                     column_config={"item": "Item", "customer": "Customer", "area": "Area",
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
                                    "days_to_first_order": st.column_config.NumberColumn("Days to 1st order", format="%d")})

    # ════════ Reconciliation ════════
    recon = None
    with tab_rec:
        if not earlier_file:
            st.info("Upload an earlier analysis (e.g. the COO's workbook) to compare it item by item.")
        else:
            try:
                earlier = _earlier(earlier_file.getvalue())
            except Exception as e:
                st.error(f"Could not read the earlier analysis: {e}")
                earlier = None
            if earlier is not None:
                recon = an.reconcile(items_all, earlier, orders_kept)
                found = recon["status"].notna()
                closed_e = recon["earlier_closed"]
                one_line = recon.loc[closed_e, "equals_one_order_line"]
                a, b, c, d_ = st.columns(4)
                a.metric("Earlier items found in this period", f"{int(found.sum()):,} / {len(recon):,}")
                b.metric("Closed / not closed agree",
                         f"{int(((recon['status'] == 'Closed') == closed_e)[found].sum()):,} / {int(found.sum()):,}")
                c.metric("Earlier Sales Qty", f"{recon['earlier_sales'].sum():,.1f} MT")
                d_.metric("Corrected Sales Qty", f"{recon.loc[found, 'sales_mt'].sum():,.1f} MT")
                if len(one_line):
                    st.warning(f"On **{int(one_line.sum()):,} of {len(one_line):,}** items the earlier analysis shows as "
                               "closed, its Sales Qty is exactly the MT of **one single order line** of that item - a "
                               "lookup returning the first match (e.g. VLOOKUP) instead of adding all order lines "
                               "(SUMIFS). The corrected Sales Qty adds every order line.")
                st.dataframe(recon[["item", "customer", "earlier_sales", "sales_mt", "difference_mt",
                                    "equals_one_order_line", "earlier_closed", "status"]]
                             .sort_values("difference_mt", ascending=False),
                             hide_index=True, use_container_width=True,
                             column_config={"item": "Item", "customer": "Customer",
                                            "earlier_sales": st.column_config.NumberColumn("Earlier Sales Qty", format="%,.2f"),
                                            "sales_mt": st.column_config.NumberColumn("Corrected Sales Qty", format="%,.2f"),
                                            "difference_mt": st.column_config.NumberColumn("Difference", format="%+,.2f"),
                                            "equals_one_order_line": "Equals one order line",
                                            "earlier_closed": "Earlier closed", "status": "Corrected status"})
                st.caption("Small differences in items and counts are expected: quotes revised in the system after "
                           "the earlier file was exported. Set the quote period to the earlier analysis' period.")

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
| **Item** | One Fact # quoted between {p.period_start:%d %b %Y} and {p.period_end:%d %b %Y} | {s['items']:,} |
| **Count** | Number of quote lines for the item in the period | |
| **AVG Qty/Quote** | Average MT of the item's quote lines = its quoted volume | |
| **Sales Qty** | **Sum** of the MT of **all** order lines (SC export) for the item, entered from its first quote date up to {p.order_cutoff:%d %b %Y} | |
| **Closed** | Sales Qty > 0 | {s['closed']:,} |
| **Open** | No order yet, last quote after {p.order_cutoff - pd.Timedelta(days=p.buffer_days):%d %b %Y} (less than {p.buffer_days} days before the cutoff) | {s['open']:,} |
| **Lost** | No order, last quote at least {p.buffer_days} days before the cutoff | {s['lost']:,} |
| **Quote-to-close ratio** | Closed ÷ (Closed + Lost) = {s['closed']:,} ÷ {s['closed'] + s['lost']:,} | {_pct(s['close_rate'])} |
| **Quoted volume** | Sum of AVG Qty/Quote | {s['quoted_mt']:,.2f} MT |
| **Sales volume** | Sum of Sales Qty | {s['sales_mt']:,.2f} MT |
| **Volume fill rate** | Sales ÷ Quoted = {s['sales_mt']:,.2f} ÷ {s['quoted_mt']:,.2f} | {_pct(s['fill_rate'])} |
| **Lost quoted volume** | Sum of AVG Qty/Quote where Status = Lost | {s['lost_mt']:,.2f} MT |
""")
        st.markdown("""
- **Why 20 buffer days:** on 2026 data, 80.1% of orders are entered within 7 days of the quote, 87.8% within 15,
  **90.0% within 20** and 92.7% within 30.
- **Waste sales quotes** ('Open Waste Order') are kept out of the KPIs unless the sidebar toggle is on, as in the
  COO's analysis. They are counted and shown, never deleted.
- **Area and Product group** come from the item's first quote line; **Salesman** and **Latest order type** from its
  most recent quote line. Product group is the export's *Box Style Group* (the COO's "Building block" is not in
  either export).
- **Fill rate above 100%** is normal: one quote can lead to several orders (repeat releases).
- The Excel workbook contains the same calculations as **live formulas**: change the order cutoff or buffer on its
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
                              f"Source: {rfq_file.name} + {sc_file.name}", recon, note),
        file_name=f"quote_to_close_{p.period_start:%Y%m%d}_{p.period_end:%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
