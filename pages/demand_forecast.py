import io

import numpy as np
import pandas as pd
import streamlit as st

from utils.auth import require_auth, has_permission
from forecast import load, clean, calendar as cal, classify as cl, models as md, charts, export

WINDOWS = {"12 months": 12, "6 months": 6, "3 months": 3}
MAX_HORIZON = 24


# ── Cached steps (nothing is stored anywhere - cache lives in memory only) ──
@st.cache_data(show_spinner=False, max_entries=10)
def _read(content: bytes, name: str):
    return load.read_invoice_file(content, name)


@st.cache_data(show_spinner=False, max_entries=3)
def _prepare(frames: list[pd.DataFrame]):
    full = load.combine(frames)
    pi = clean.period_info(full)                      # analysis dates from ALL lines in the files
    kept, excluded = clean.exclude_salesmen(full)     # e-shop lines out BEFORE any analysis
    kept, merged = clean.standardize_customers(kept)
    return kept, excluded, merged, pi


@st.cache_data(show_spinner=False, max_entries=3)
def _ft_mix(df: pd.DataFrame, cutoff: pd.Timestamp):
    return md.ft_mix(df, cutoff, 12)


@st.cache_data(show_spinner=False, max_entries=3)
def _forecast(mt: pd.DataFrame, eff_hist: pd.Series, eff_future: pd.Series):
    return md.run_forecasts(mt, eff_hist, eff_future)


def _excel(sheets: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
    return buf.getvalue()


def _month_cols(frame: pd.DataFrame) -> pd.DataFrame:
    """Period column names -> 'Oct 2026' strings for display/export."""
    return frame.rename(columns={c: c.strftime("%b %Y") for c in frame.columns if isinstance(c, pd.Period)})


def show():
    require_auth()
    if not has_permission("forecast"):
        st.error("You don't have access to this page.")
        st.stop()

    st.title("📈 Customer Demand Forecast")

    uploaded = st.file_uploader(
        "Upload the Invoice Detail exports - one file per year (.xls / .xlsx)",
        type=["xls", "xlsx"], accept_multiple_files=True,
    )
    st.caption("Files are processed in memory only. Nothing is saved to the database.")
    if not uploaded:
        st.info("Upload at least one Invoice Detail export to begin. For Holt-Winters seasonality, "
                "upload 24+ months (e.g. the last two full years plus the current year).")
        st.stop()

    # ── Load ──
    frames, infos = [], []
    with st.spinner("Reading files…"):
        for f in uploaded:
            try:
                d, info = _read(f.getvalue(), f.name)
            except Exception as e:
                st.error(f"Could not read **{f.name}**: {e}")
                st.stop()
            frames.append(d)
            infos.append(info)

    overlaps = load.find_overlaps(infos)
    if overlaps:
        pairs = "; ".join(f"**{a}** and **{b}**" for a, b in overlaps)
        st.error(f"These files cover overlapping dates, so invoices would be counted twice: {pairs}. "
                 "Remove one of each pair and upload again.")
        st.stop()

    df, excluded, merged_names, pi = _prepare(frames)
    attrs = clean.customer_attributes(df)

    daily = df.groupby("inv_date")["tons"].sum().reindex(pd.date_range(pi.data_start, pi.cutoff), fill_value=0.0)
    hol_w = cal.estimate_holiday_weight(daily)

    # ── Sidebar filters ──
    st.sidebar.header("Filters")
    window_label = st.sidebar.radio("Compare window", list(WINDOWS.keys()), index=0,
                                    help="Window ending on the last invoice date.")
    months_w = WINDOWS[window_label]
    comp_label = st.sidebar.radio(
        "Compare with", list(cl.COMPARISONS.values()), index=0,
        help="Same period last year removes seasonality. Previous period shows recent momentum, "
             "but includes seasonal swings. For 12 months both are the same period.")
    comparison = {v: k for k, v in cl.COMPARISONS.items()}[comp_label]
    with st.sidebar.expander("Status thresholds"):
        stable = st.slider("Stable band (±%)", 0, 30, 10, 1)
        strong = st.slider("Strong growth above (%)", stable + 1, 100, max(25, stable + 1), 1)
        min_mt = st.number_input("Minimum MT for a % status", min_value=0.0, value=5.0, step=1.0,
                                 help="Customers below this in both periods are shown as 'Below threshold'.")
    industries = st.sidebar.multiselect("Industry group", sorted(attrs["industry"].replace("", np.nan).dropna().unique()))
    areas = st.sidebar.multiselect("Area", sorted(attrs["area"].replace("", np.nan).dropna().unique()))

    selected = attrs.index
    if industries:
        selected = selected[attrs.loc[selected, "industry"].isin(industries)]
    if areas:
        selected = selected[attrs.loc[selected, "area"].isin(areas)]
    is_filtered = bool(industries or areas)
    df_sel = df[df["customer"].isin(selected)]

    tab_trend, tab_fc, tab_check = st.tabs(["📊 Customer trends", "📈 Forecast", "🧾 Data check & method"])

    # ════════════════════ Customer trends ════════════════════
    with tab_trend:
        th = cl.Thresholds(stable_band=stable / 100, strong_growth=strong / 100, min_mt=min_mt)
        cls_all = cl.classify(df, attrs, pi.cutoff, months_w, th, comparison)
        a = dict(cls_all.attrs)
        cls_df = cls_all[cls_all["customer"].isin(selected)].copy()
        cls_df.attrs = {}

        if a["p_start"] < pi.data_start:
            st.warning(f"The comparison period starts {a['p_start']:%d %b %Y}, before the earliest invoice "
                       f"uploaded ({pi.data_start:%d %b %Y}). Upload the earlier year for a correct comparison.")

        st.caption(f"**Window:** {a['w_start']:%d %b %Y} – {a['w_end']:%d %b %Y}  ·  "
                   f"**Compared with:** {a['p_start']:%d %b %Y} – {a['p_end']:%d %b %Y} ({comp_label.lower()})")

        if comparison == "previous" and months_w < 12:
            ref = cl.seasonal_reference(df_sel, pi.cutoff, months_w, pi.data_start)
            if ref is None:
                st.warning("Seasonal reference not available: the uploaded files don't go back far enough "
                           "to show how these two periods moved last year.")
            else:
                st.info(f"**Seasonal reference:** last year, these same two periods moved **{ref:+.1%}** "
                        f"({a['w_start'] - pd.DateOffset(years=1):%d %b %Y} – {a['w_end'] - pd.DateOffset(years=1):%d %b %Y} "
                        f"vs {a['p_start'] - pd.DateOffset(years=1):%d %b %Y} – {a['p_end'] - pd.DateOffset(years=1):%d %b %Y}). "
                        "Read each customer's % against this: it is the normal seasonal swing, not a trend.")

        w_mt, p_mt = cls_df["window_mt"].sum(), cls_df["prior_mt"].sum()
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Window MT", f"{w_mt:,.0f}")
        c2.metric(comp_label, f"{p_mt:,.0f}")
        c3.metric("Change", f"{w_mt - p_mt:+,.0f} MT", f"{(w_mt / p_mt - 1):+.1%}" if p_mt else None)
        c4.metric("Customers invoiced in window", f"{(cls_df['window_mt'] > 0).sum():,}")
        c5.metric("New in window / Total loss",
                  f"{(cls_df['status'] == 'New').sum()} / {(cls_df['status'] == 'Total loss').sum()}")

        summary = cl.status_summary(cls_df)
        st.plotly_chart(charts.status_bridge(summary, f"Where the change came from - {window_label}"),
                        use_container_width=True)
        st.dataframe(
            summary, hide_index=True, use_container_width=True,
            column_config={
                "status": "Status",
                "customers": st.column_config.NumberColumn("Customers", format="%d"),
                "window_mt": st.column_config.NumberColumn("Window MT", format="%,.0f"),
                "prior_mt": st.column_config.NumberColumn("Prior MT", format="%,.0f"),
                "change_mt": st.column_config.NumberColumn("Change MT", format="%+,.0f"),
            },
        )

        st.subheader("Customers")
        pick = st.multiselect("Show status", cl.STATUS_ORDER, default=[], placeholder="All statuses")
        table = cls_df if not pick else cls_df[cls_df["status"].isin(pick)]
        table = table.sort_values("change_mt")
        cols = ["customer", "status", "industry", "area", "salesman", "window_mt", "prior_mt",
                "change_mt", "change_pct", "last_invoice"]
        st.dataframe(
            table[cols], hide_index=True, use_container_width=True,
            column_config={
                "customer": "Customer", "status": "Status", "industry": "Industry", "area": "Area",
                "salesman": "Salesman (latest)",
                "window_mt": st.column_config.NumberColumn("Window MT", format="%,.1f"),
                "prior_mt": st.column_config.NumberColumn("Prior MT", format="%,.1f"),
                "change_mt": st.column_config.NumberColumn("Change MT", format="%+,.1f"),
                "change_pct": st.column_config.NumberColumn("Change %", format="%+.1%"),
                "last_invoice": st.column_config.DateColumn("Last invoice", format="DD MMM YYYY"),
            },
        )
        st.download_button(
            "⬇️ Customer trends (.xlsx)",
            _excel({"Summary": summary.assign(status=summary["status"].astype(str)),
                    "Customers": table[cols].assign(status=table["status"].astype(str))}),
            file_name=f"customer_trends_{months_w}m_{comparison}_{pi.cutoff:%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ════════════════════ Forecast ════════════════════
    with tab_fc:
        hist_months = pd.period_range(pi.first_complete_month, pi.last_complete_month, freq="M")
        n_hist = len(hist_months)
        if n_hist < 12:
            st.warning(f"Only {n_hist} complete months uploaded. Trend models need 12+, "
                       "Holt-Winters seasonality needs 24+. Most customers will use a 6-month average.")

        first_fc = pi.cutoff_month if pi.is_partial else pi.last_complete_month + 1
        fc_options = list(pd.period_range(first_fc, first_fc + MAX_HORIZON - 1, freq="M"))
        default_end = pd.Period(f"{first_fc.year + 1}-12", freq="M")
        horizon_end = st.select_slider(
            "Forecast up to", options=fc_options, value=default_end if default_end in fc_options else fc_options[-1],
            format_func=lambda p: p.strftime("%b %Y"),
        )
        fc_months = pd.period_range(first_fc, horizon_end, freq="M")

        gaps = cal.missing_years(hist_months[0].start_time, horizon_end.end_time)
        if gaps:
            st.warning(f"No Eid dates in forecast/calendar.py for {gaps}: those months get no holiday adjustment.")

        eff_all = cal.effective_days(hist_months[0].start_time, horizon_end.end_time, hol_w)
        eff_hist = eff_all.loc[hist_months]
        eff_future = eff_all.loc[fc_months].copy()
        partial_actual_all = None
        if pi.is_partial:
            elapsed = cal.day_weights(pi.cutoff_month.start_time, pi.cutoff, hol_w).sum()
            eff_future.iloc[0] = eff_future.iloc[0] - elapsed          # remaining days only
            partial_actual_all = (df[df["inv_date"].dt.to_period("M") == pi.cutoff_month]
                                  .groupby("customer")["tons"].sum())

        mt_all = md.build_matrix(df, hist_months)
        with st.spinner("Fitting customer models… (first run takes ~20 seconds)"):
            fc_rem, model_info = _forecast(mt_all, eff_hist, eff_future)

        # Full forecast per customer = remaining-days forecast + (partial month) invoiced so far
        fc_full = fc_rem.copy()
        if partial_actual_all is not None:
            fc_full = fc_full.reindex(fc_full.index.union(partial_actual_all.index), fill_value=0.0)
            fc_full[pi.cutoff_month] = fc_full[pi.cutoff_month] + partial_actual_all.reindex(fc_full.index, fill_value=0.0)

        fc_sel = fc_full[fc_full.index.isin(selected)]
        mt_sel = mt_all[mt_all.index.isin(selected)]
        info_sel = model_info[model_info.index.isin(selected)]
        hist_total = mt_sel.sum()
        fc_sum = fc_sel.sum()

        tot_fc, tot_model = md.forecast_total(hist_total, eff_hist, eff_future)
        tot_fc = pd.Series(tot_fc, index=fc_months)
        partial_sel = None
        if pi.is_partial:
            so_far = float(partial_actual_all.reindex(selected, fill_value=0.0).sum())
            tot_fc.iloc[0] += so_far
            partial_sel = (pi.cutoff_month, so_far)
            st.info(f"**{pi.cutoff_month.strftime('%B %Y')} is a partial month.** Its forecast = {so_far:,.0f} MT invoiced "
                    f"up to {pi.cutoff:%d %b} + the forecast for the remaining days. Models are fitted on "
                    f"complete months only ({hist_months[0].strftime('%b %Y')} – {hist_months[-1].strftime('%b %Y')}).")

        acc = md.accuracy_summary(info_sel)
        next12 = fc_sum.iloc[:12].sum()
        budget_year = first_fc.year + 1
        by_months = [m for m in fc_sum.index if m.year == budget_year]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"Next {min(12, len(fc_sum))} months", f"{next12:,.0f} MT")
        c2.metric(f"{budget_year} forecast" + ("" if len(by_months) == 12 else f" ({len(by_months)} mo.)"),
                  f"{fc_sum[by_months].sum():,.0f} MT" if by_months else "—")
        c3.metric("Test error per customer", f"{acc['wape']:.0%}" if pd.notna(acc["wape"]) else "—",
                  help="Volume-weighted average error per customer when the last 6 complete months were "
                       "held back and forecast. Individual customers are noisy; this is expected to be high.")
        c4.metric("Test total bias", f"{acc['bias']:+.1%}" if pd.notna(acc["bias"]) else "—",
                  help="How far the SUM of all customer forecasts was from the actual total in that test. "
                       "This is the number that matters for the budget.")

        st.plotly_chart(
            charts.forecast_chart(hist_total, fc_sum, tot_fc, partial_sel,
                                  "Monthly MT - actual and forecast" + (" (filtered)" if is_filtered else "")),
            use_container_width=True,
        )

        monthly = pd.DataFrame({
            "Month": [m.strftime("%b %Y") for m in fc_months],
            "Forecast MT (sum of customers)": fc_sum.reindex(fc_months).values,
            f"Cross-check MT ({tot_model})": tot_fc.values,
            "Effective days": eff_all.loc[fc_months].values,
        })
        st.dataframe(monthly, hide_index=True, use_container_width=True,
                     column_config={c: st.column_config.NumberColumn(c, format="%,.0f")
                                    for c in monthly.columns if "MT" in c} |
                                   {"Effective days": st.column_config.NumberColumn(format="%.1f")})
        diff = fc_sum.sum() / tot_fc.sum() - 1 if tot_fc.sum() else 0
        if abs(diff) > 0.05:
            st.caption(f"⚠️ The sum of customer forecasts is {diff:+.1%} vs the company-level model. "
                       "Check the largest customers below before using either figure for the budget.")

        mix_all = _ft_mix(df, pi.cutoff)
        mix_sel = mix_all[mix_all["customer"].isin(fc_sel.index)]

        # ── Customer table ──
        st.subheader("Forecast by customer")
        cust = (_month_cols(fc_sel.reindex(columns=fc_months))
                .assign(**{"Total MT": fc_sel.sum(axis=1)})
                .join(info_sel[["model", "history_months", "replaced_by_average", "size_change"]])
                .join(attrs[["industry", "area"]])
                .sort_values("Total MT", ascending=False))
        cust["model"] = cust["model"].fillna("No complete-month history")
        cust = cust.rename_axis("Customer").reset_index()
        lead = ["Customer", "industry", "area", "model", "history_months", "Total MT"]
        cust = cust[lead + [c for c in cust.columns if c not in lead + ["replaced_by_average", "size_change"]]]
        st.dataframe(cust, hide_index=True, use_container_width=True,
                     column_config={c: st.column_config.NumberColumn(c, format="%,.1f")
                                    for c in cust.columns if c not in ("Customer", "industry", "area", "model", "history_months")})

        # ── Drill-down ──
        st.subheader("Customer drill-down")
        options = cust["Customer"].tolist()
        if options:
            who = st.selectbox("Customer", options)
            hist_c = mt_all.loc[who] if who in mt_all.index else pd.Series(0.0, index=hist_months)
            fc_c = fc_full.loc[who].reindex(fc_months) if who in fc_full.index else pd.Series(0.0, index=fc_months)
            st.plotly_chart(charts.customer_chart(hist_c, fc_c, who), use_container_width=True)
            if who in model_info.index:
                r = model_info.loc[who]
                note = f"**Model:** {r['model']} · **History:** {int(r['history_months'])} months"
                if pd.notna(r["test_error_model"]):
                    note += f" · **Test error:** model {r['test_error_model']:.0%}, 6-month average {r['test_error_average']:.0%}"
                if r["replaced_by_average"]:
                    note += " · the 6-month average beat the trend model in the test, so the average is used"
                st.caption(note)
                if r["size_change"]:
                    st.warning("**Size change detected:** this customer's last 6 months are 3x or more, or under a "
                               "third, of the 12 months before. The model still uses the full history, which may no "
                               "longer describe this customer - review this forecast before using it.")
            fts = md.ft_split(mix_all, who, fc_c)
            if fts.empty:
                st.caption("No invoices in the last 12 months - no FT split.")
            else:
                st.caption("FT split = each FT's share of this customer's MT over the last 12 months to the analysis date.")
                fts = _month_cols(fts)
                st.dataframe(fts, hide_index=True, use_container_width=True,
                             column_config={"ft": "FT", "product_name": "Product name",
                                            "share": st.column_config.NumberColumn("Share", format="%.1%"),
                                            "months_invoiced": st.column_config.NumberColumn("Months invoiced (last 12)", format="%d"),
                                            "last_invoiced": st.column_config.DateColumn("Last invoiced", format="DD MMM YYYY")} |
                                           {c: st.column_config.NumberColumn(c, format="%,.1f")
                                            for c in fts.columns if c not in ("ft", "product_name", "share",
                                                                              "months_invoiced", "last_invoiced")})

        # ── Export (live formulas: edit a customer in Excel and its FTs + totals follow) ──
        notes = [
            f"Demand forecast exported from the Quality Hub. Analysis date (last invoice): {pi.cutoff:%d %b %Y}.",
            f"Models fitted on complete months {hist_months[0].strftime('%b %Y')} - {hist_months[-1].strftime('%b %Y')}.",
            "HOW TO ADJUST: change the yellow month cells on the Customers sheet. The FT sheet and the Monthly total "
            "sheet recalculate automatically. 'Model total MT' keeps the original model figure for comparison.",
            "FT sheet: each FT = its share of the customer's MT over the last 12 months x the customer's month on the "
            "Customers sheet. Every FT invoiced in those 12 months is included; check 'Months invoiced' and "
            "'Last invoiced' for one-off FTs (e.g. National Day artwork).",
            "Size change = Yes: the last 6 months are 3x+ or under a third of the 12 months before. The model "
            "still uses the full history - review these customers before using their forecast.",
        ]
        if pi.is_partial:
            notes.append(f"* {pi.cutoff_month.strftime('%b %Y')} is a partial month: its figure = MT invoiced up to "
                         f"{pi.cutoff:%d %b %Y} + the forecast for the remaining days.")
        if len(excluded):
            notes.append("Excluded before analysis: " + "; ".join(
                f"{sm} ({excluded.loc[excluded['salesman'].str.upper().str.strip() == sm, 'tons'].sum():,.1f} MT) - {why}"
                for sm, why in clean.EXCLUDED_SALESMEN.items()))
        if is_filtered:
            notes.append(f"Filtered export: industry = {industries or 'all'}; area = {areas or 'all'}.")
        cust_meta = info_sel[["model", "history_months", "size_change"]].join(attrs[["industry", "area"]], how="outer")
        cust_meta = cust_meta.reindex(fc_sel.index)
        cust_meta["model"] = cust_meta["model"].fillna("No complete-month history")
        cust_meta["size_change"] = cust_meta["size_change"].fillna(False)
        st.download_button(
            "⬇️ Forecast workbook (.xlsx)",
            export.build_workbook(cust_meta, fc_sel.reindex(columns=fc_months), mix_sel, fc_months,
                                  tot_fc, eff_all.loc[fc_months], pi.cutoff_month if pi.is_partial else None, notes),
            file_name=f"demand_forecast_{pi.cutoff:%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ════════════════════ Data check & method ════════════════════
    with tab_check:
        st.subheader("Files")
        files = pd.DataFrame([{
            "File": i.name, "From": i.date_min.date(), "To": i.date_max.date(),
            "Invoice lines": i.rows_kept, "Footer rows removed": i.footer_rows_dropped,
            "MT": i.tons, "Footer total MT": i.footer_tons,
            "Matches footer": "✅" if i.footer_match else ("❌" if i.footer_match is False else "—"),
        } for i in sorted(infos, key=lambda i: i.date_min)])
        st.dataframe(files, hide_index=True, use_container_width=True,
                     column_config={"MT": st.column_config.NumberColumn(format="%,.2f"),
                                    "Footer total MT": st.column_config.NumberColumn(format="%,.2f")})
        if (files["Matches footer"] == "❌").any():
            st.error("A file's invoice lines don't add up to its footer total - check that export before using the results.")

        st.markdown(f"**Analysis date (last invoice):** {pi.cutoff:%d %b %Y} · "
                    f"**Complete months for models:** {pi.first_complete_month.strftime('%b %Y')} – {pi.last_complete_month.strftime('%b %Y')} "
                    f"({len(pd.period_range(pi.first_complete_month, pi.last_complete_month, freq='M'))} months)")

        st.subheader("Excluded before analysis")
        if excluded.empty:
            st.caption("No invoice lines matched the exclusion list.")
        else:
            for sm, why in clean.EXCLUDED_SALESMEN.items():
                ex = excluded[excluded["salesman"].str.upper().str.strip() == sm]
                if ex.empty:
                    continue
                st.markdown(f"**{sm}** - {why}: **{len(ex):,} lines · {ex['tons'].sum():,.1f} MT · "
                            f"{ex['customer_raw'].nunique()} customers**")
                st.dataframe(
                    ex.groupby("customer_raw").agg(lines=("tons", "size"), mt=("tons", "sum"),
                                                   last_invoice=("inv_date", "max"))
                      .sort_values("mt", ascending=False).rename_axis("Customer").reset_index(),
                    hide_index=True, use_container_width=True,
                    column_config={"lines": st.column_config.NumberColumn("Lines", format="%d"),
                                   "mt": st.column_config.NumberColumn("MT", format="%,.2f"),
                                   "last_invoice": st.column_config.DateColumn("Last invoice", format="DD MMM YYYY")})
            st.caption("The exclusion list is in forecast/clean.py (EXCLUDED_SALESMEN).")

        st.subheader("Customer names merged")
        if merged_names.empty:
            st.caption("No customers had more than one spelling.")
        else:
            st.caption("Same name after removing case, spaces and punctuation. Shown under the most recent spelling.")
            st.dataframe(merged_names, hide_index=True, use_container_width=True,
                         column_config={"display_name": "Shown as", "spellings": "Spellings found",
                                        "tons": st.column_config.NumberColumn("MT", format="%,.1f")})

        st.subheader("Eid holiday adjustment")
        st.markdown(f"Invoicing on Eid holiday days runs at **{hol_w:.0%}** of a normal day "
                    "(measured from the uploaded data). Each month's history is divided by its effective days "
                    "and each forecast month is multiplied by its own effective days, so moving Eid dates "
                    "are handled.")
        eid = pd.DataFrame({"Year": list(cal.EID_AL_FITR.keys()),
                            "Eid al-Fitr": list(cal.EID_AL_FITR.values()),
                            "Eid al-Adha": [cal.EID_AL_ADHA.get(y, "") for y in cal.EID_AL_FITR],
                            "Status": ["Tentative" if y >= cal.TENTATIVE_FROM_YEAR else "Actual" for y in cal.EID_AL_FITR]})
        st.dataframe(eid, hide_index=True)

        st.subheader("Models used")
        st.dataframe(model_info["model"].value_counts().rename_axis("Model").reset_index(name="Customers"),
                     hide_index=True)

        with st.expander("Status rules"):
            for status in cl.STATUS_ORDER:
                st.markdown(f"- **{status}:** {cl.STATUS_RULES[status]}")
            st.caption("Rules are checked in this order; the first match wins.")

        with st.expander("Methodology notes"):
            st.markdown("""
- **Forecast level:** each customer's monthly MT. FT forecasts are the customer forecast split by each
  FT's share of the customer's MT over the last 12 months, because most FTs are too short-lived to model.
  Every FT invoiced in those 12 months is included - nothing is dropped.
- **Model per customer:** no orders in the last 12 complete months → 0 · more than 40% empty months in
  the last 24 → TSB (intermittent demand) · under 12 months of history → 6-month average ·
  12–23 months → Holt with damped trend · 24+ months → Holt-Winters with damped trend and 12-month
  seasonality (additive or multiplicative, whichever fits better).
- **Size change (flag for review):** customers whose last 6 months are 3x+ or under a third of the
  12 months before are flagged. Their forecast is NOT changed: tested on Apr-Sep 2026, forcing an
  average for them more than doubled the error, because most kept moving in the same direction.
- **Accuracy test:** each model is refitted without the last 6 complete months and scored on them.
  If a plain 6-month average beats it, the average is used.
- **Partial month:** excluded from model fitting; its forecast is the MT invoiced so far plus the
  forecast for the remaining days.
- **Ramadan:** the data shows invoicing drops at Eid (handled by the holiday adjustment). It does not
  show a consistent pre-Ramadan demand change: the three Ramadans uploaded (2024–2026) all fall in
  February–April, so a Ramadan effect can't be separated from normal seasonality, which Holt-Winters
  already captures. No extra Ramadan uplift is applied.
- **Returns / credit notes:** not in this export, so the history is gross invoiced MT.
- **Trend windows** are date-based, ending on the last invoice date. "Same period last year" compares
  with the same dates one year earlier (no seasonality). "Previous period" compares with the period
  right before the window (recent momentum) and shows last year's swing between the same two periods
  as a seasonal reference.
- **Excluded lines:** salespeople in the exclusion list (forecast/clean.py) are removed before any
  analysis; they are listed above with their MT.
            """)
