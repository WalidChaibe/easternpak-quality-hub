"""
Quote-to-close workbook. Every KPI is a live formula, so each number can be traced:
  Quote lines / Order lines  - the raw lines used (values)
  Items                      - one row per Fact #: Count, AVG, dates, Sales Qty, Status are FORMULAS
                               over Quote lines / Order lines and the Parameters sheet
  Summary + breakdowns       - COUNTIFS / SUMIFS over Items
Changing the order cutoff or buffer on the Parameters sheet recalculates everything.
Pure functions - no Streamlit calls.
"""
import io
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

BLUE = PatternFill("solid", fgColor="0D68A3")
WHITE_BOLD = Font(bold=True, color="FFFFFF")
BOLD = Font(bold=True)
TITLE = Font(bold=True, size=14, color="0D68A3")
PARAM_FILL = PatternFill("solid", fgColor="FFF8E1")
MT, PCT, DATE, INT = "#,##0.00", "0.0%", "DD MMM YYYY", "#,##0"

P_START, P_END, P_CUTOFF, P_BUFFER = "Parameters!$B$2", "Parameters!$B$3", "Parameters!$B$4", "Parameters!$B$5"


def _crit(text: str) -> str:
    """Excel criteria literal: escape wildcards (* ? ~) and quotes, so 'Nova ... Co)*' matches only itself."""
    t = str(text).replace("~", "~~").replace("*", "~*").replace("?", "~?").replace('"', '""')
    return f'"{t}"'


def _header(ws, row, headers, widths=None):
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=j, value=h)
        c.fill, c.font = BLUE, WHITE_BOLD
        c.alignment = Alignment(wrap_text=True, vertical="center")
    if widths:
        for j, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(j)].width = w


def _fmt(ws, col, fmt, r1, r2):
    for r in range(r1, r2 + 1):
        ws.cell(row=r, column=col).number_format = fmt


def build_workbook(items: pd.DataFrame, quote_lines: pd.DataFrame, order_lines: pd.DataFrame,
                   waste_lines: pd.DataFrame, params, title_period: str, files_note: str,
                   recon: pd.DataFrame | None = None, excluded_note: str = "") -> bytes:
    wb = Workbook()
    n = len(items)
    last = n + 1                                   # last data row on Items

    # ── Parameters ──
    wp = wb.active
    wp.title = "Parameters"
    rows = [("Parameter", "Value", "Meaning"),
            ("Quote period start", params.period_start.to_pydatetime(), "Quotes issued on or after this date (Quote lines sheet already filtered)"),
            ("Quote period end", params.period_end.to_pydatetime(), "Quotes issued on or before this date"),
            ("Order cutoff", params.order_cutoff.to_pydatetime(), "Orders entered up to this date are counted (editable)"),
            ("Buffer days", params.buffer_days, "Quotes newer than this before the cutoff, with no order yet, are Open (editable)")]
    for i, r in enumerate(rows, start=1):
        for j, v in enumerate(r, start=1):
            wp.cell(row=i, column=j, value=v)
    _header(wp, 1, rows[0], [22, 16, 90])
    for r in range(2, 5):
        wp.cell(row=r, column=2).number_format = DATE
    for r in (4, 5):
        wp.cell(row=r, column=2).fill = PARAM_FILL

    # ── Quote lines ──
    wq = wb.create_sheet("Quote lines")
    qcols = ["Quote #", "Item (Fact #)", "Customer", "Order type", "Quote date", "Salesman", "MT", "Area", "Product group"]
    _header(wq, 1, qcols, [12, 12, 40, 16, 13, 22, 10, 18, 18])
    for rec in quote_lines.itertuples(index=False):
        wq.append([rec.quote_no, rec.item, rec.customer, rec.order_type, rec.quote_day.to_pydatetime(),
                   rec.salesman, float(rec.mt), rec.area, rec.product_group])
    _fmt(wq, 5, DATE, 2, len(quote_lines) + 1)
    _fmt(wq, 7, MT, 2, len(quote_lines) + 1)
    wq.freeze_panes = "A2"
    wq.auto_filter.ref = f"A1:I{len(quote_lines) + 1}"

    # ── Order lines ──
    wo = wb.create_sheet("Order lines")
    ocols = ["Item (Fact #)", "SC #", "Order ID", "Order type", "Status", "Order date", "MT", "Customer"]
    _header(wo, 1, ocols, [12, 12, 18, 16, 20, 13, 10, 40])
    for rec in order_lines.itertuples(index=False):
        wo.append([rec.item, rec.sc_no, rec.order_id, rec.order_type, rec.status,
                   rec.order_day.to_pydatetime(), float(rec.mt), rec.customer])
    _fmt(wo, 6, DATE, 2, len(order_lines) + 1)
    _fmt(wo, 7, MT, 2, len(order_lines) + 1)
    wo.freeze_panes = "A2"
    wo.auto_filter.ref = f"A1:H{len(order_lines) + 1}"

    # ── Items (formulas) ──
    wi = wb.create_sheet("Items")
    icols = ["Item (Fact #)", "Customer", "Area", "Product group", "Salesman", "Latest order type", "Product",
             "Count (quotes)", "AVG Qty/Quote (MT)", "First quote", "Last quote", "Last quote #",
             "Sales Qty (MT)", "Order lines", "Status", "Var (MT)", "Quotes sent"]
    _header(wi, 1, icols, [12, 40, 18, 18, 22, 16, 30, 9, 12, 13, 13, 12, 12, 9, 9, 11, 10])
    QL, OL = "'Quote lines'", "'Order lines'"
    for r, rec in enumerate(items.itertuples(index=False), start=2):
        wi.append([
            rec.item, rec.customer, rec.area, rec.product_group, rec.salesman, rec.latest_order_type, rec.product,
            f"=COUNTIFS({QL}!$B:$B,$A{r})",
            f"=AVERAGEIFS({QL}!$G:$G,{QL}!$B:$B,$A{r})",
            f"=_xlfn.MINIFS({QL}!$E:$E,{QL}!$B:$B,$A{r})",
            f"=_xlfn.MAXIFS({QL}!$E:$E,{QL}!$B:$B,$A{r})",
            rec.last_quote_no,
            f'=SUMIFS({OL}!$G:$G,{OL}!$A:$A,$A{r},{OL}!$F:$F,">="&$J{r},{OL}!$F:$F,"<="&{P_CUTOFF})',
            f'=COUNTIFS({OL}!$A:$A,$A{r},{OL}!$F:$F,">="&$J{r},{OL}!$F:$F,"<="&{P_CUTOFF})',
            f'=IF($M{r}>0,"Closed",IF($K{r}>{P_CUTOFF}-{P_BUFFER},"Open","Lost"))',
            f"=$M{r}-$I{r}",
            f'=IF($H{r}>=5,"5 or more",TEXT($H{r},"0"))',
        ])
    for col, fmt in ((9, MT), (10, DATE), (11, DATE), (13, MT), (16, MT)):
        _fmt(wi, col, fmt, 2, last)
    wi.freeze_panes = "B2"
    wi.auto_filter.ref = f"A1:Q{last}"
    I = "Items"

    # ── Summary ──
    ws = wb.create_sheet("Summary", 0)
    ws["A1"], ws["A1"].font = "Eastern Pak — RFQ Quote-to-Close Analysis", TITLE
    ws["A2"] = f"{title_period}  |  {files_note}"
    ws["A3"] = "Every value below is a formula - see the 'How calculated' sheet for each definition."
    _header(ws, 5, ["Key metric", "Value", "", "Quotation effort", "Value"], [46, 16, 3, 44, 14])
    left = [
        ("Total items quoted (line items)", f"=COUNTA({I}!$A:$A)-1", INT),
        ("Closed (Sales Qty > 0)", f'=COUNTIF({I}!$O:$O,"Closed")', INT),
        ("Lost (no order after the buffer)", f'=COUNTIF({I}!$O:$O,"Lost")', INT),
        ("Open (quoted < buffer days before cutoff, no order yet)", f'=COUNTIF({I}!$O:$O,"Open")', INT),
        ("Quote-to-close ratio (Closed ÷ (Closed + Lost))", "=IFERROR(B7/(B7+B8),0)", PCT),
        ("Total quoted volume (sum of AVG Qty/Quote)", f"=SUM({I}!$I:$I)", MT),
        ("Total sales volume (orders from first quote to cutoff)", f"=SUM({I}!$M:$M)", MT),
        ("Volume fill rate (Sales ÷ Quoted)", "=IFERROR(B12/B11,0)", PCT),
        ("Lost quoted volume", f'=SUMIFS({I}!$I:$I,{I}!$O:$O,"Lost")', MT),
        ("Open quoted volume", f'=SUMIFS({I}!$I:$I,{I}!$O:$O,"Open")', MT),
    ]
    right = [
        ("Avg # quotations sent — closed items", f'=IFERROR(AVERAGEIFS({I}!$H:$H,{I}!$O:$O,"Closed"),0)', "0.00"),
        ("Avg # quotations sent — lost items", f'=IFERROR(AVERAGEIFS({I}!$H:$H,{I}!$O:$O,"Lost"),0)', "0.00"),
        ("Lost items quoted only once", f'=COUNTIFS({I}!$O:$O,"Lost",{I}!$H:$H,1)', INT),
        ("Lost items quoted 2+ times, still no order", f'=COUNTIFS({I}!$O:$O,"Lost",{I}!$H:$H,">=2")', INT),
        ("  … as % of all lost items", "=IFERROR(E9/B8,0)", PCT),
    ]
    for i, (label, f, fmt) in enumerate(left, start=6):
        ws.cell(row=i, column=1, value=label)
        c = ws.cell(row=i, column=2, value=f)
        c.number_format = fmt
    for i, (label, f, fmt) in enumerate(right, start=6):
        ws.cell(row=i, column=4, value=label)
        c = ws.cell(row=i, column=5, value=f)
        c.number_format = fmt
    ws["A17"], ws["A17"].font = "Waste sales quotes (Order Type 'Open Waste Order') - NOT in the KPIs above", BOLD
    ws["A18"], ws["B18"] = "Quote lines", "=COUNTA('Waste quotes'!$A:$A)-1"
    ws["A19"], ws["B19"] = "Quoted MT", "=SUM('Waste quotes'!$G:$G)"
    ws["B19"].number_format = MT
    if excluded_note:
        ws["A21"] = excluded_note

    # ── Breakdown sheets (COO layout) ──
    def breakdown(name, title, col_letter, categories):
        b = wb.create_sheet(name)
        b["A1"], b["A1"].font = title, TITLE
        _header(b, 3, [name.replace("By ", ""), "Quotes", "Closed", "Lost", "Open", "Close rate %",
                       "Qty quoted (MT)", "Qty sold (MT)", "Fill rate %"], [40, 9, 9, 9, 9, 12, 15, 15, 11])
        col = f"{I}!${col_letter}:${col_letter}"
        r = 4
        for cat in categories:
            cr = _crit(cat)
            b.cell(row=r, column=1, value=str(cat))
            b.cell(row=r, column=2, value=f"=COUNTIFS({col},{cr})")
            b.cell(row=r, column=3, value=f'=COUNTIFS({col},{cr},{I}!$O:$O,"Closed")')
            b.cell(row=r, column=4, value=f'=COUNTIFS({col},{cr},{I}!$O:$O,"Lost")')
            b.cell(row=r, column=5, value=f'=COUNTIFS({col},{cr},{I}!$O:$O,"Open")')
            b.cell(row=r, column=6, value=f'=IFERROR(C{r}/(C{r}+D{r}),"")')
            b.cell(row=r, column=7, value=f"=SUMIFS({I}!$I:$I,{col},{cr})")
            b.cell(row=r, column=8, value=f"=SUMIFS({I}!$M:$M,{col},{cr})")
            b.cell(row=r, column=9, value=f'=IFERROR(H{r}/G{r},"")')
            r += 1
        b.cell(row=r, column=1, value="TOTAL").font = BOLD
        for j, L in zip(range(2, 9), "BCDEFGH"):
            if L == "F":
                b.cell(row=r, column=6, value=f'=IFERROR(C{r}/(C{r}+D{r}),"")')
            else:
                b.cell(row=r, column=j, value=f"=SUM({L}4:{L}{r - 1})")
        b.cell(row=r, column=9, value=f'=IFERROR(H{r}/G{r},"")')
        for rr in range(4, r + 1):
            b.cell(row=rr, column=6).number_format = PCT
            b.cell(row=rr, column=9).number_format = PCT
            b.cell(row=rr, column=7).number_format = MT
            b.cell(row=rr, column=8).number_format = MT
            for j in range(1, 10):
                if rr == r:
                    b.cell(row=rr, column=j).font = BOLD
        b.freeze_panes = "A4"

    order = lambda col: items.groupby(col)["avg_mt"].sum().sort_values(ascending=False).index.tolist()
    breakdown("By Area", "Quote-to-Close by Area", "C", order("area"))
    breakdown("By Product Group", "Quote-to-Close by Product Group (export field 'Box Style Group')", "D", order("product_group"))
    breakdown("By Salesman", "Quote-to-Close by Salesman", "E", order("salesman"))
    breakdown("By Order Type", "Quote-to-Close by Latest Quote Order Type", "F", order("latest_order_type"))
    breakdown("By Quotation Count", "Quote-to-Close by Number of Quotations Sent", "Q", ["1", "2", "3", "4", "5 or more"])

    # ── Top lost ──
    tl = wb.create_sheet("Top Lost Opportunities")
    tl["A1"], tl["A1"].font = "Top 15 Customers by Lost Quoted Volume", TITLE
    tl["A2"] = "Items with Status = Lost (no order from first quote to cutoff, older than the buffer)."
    _header(tl, 3, ["Customer", "# Lost items", "Lost quoted volume (MT)"], [55, 13, 22])
    lost = items[items["status"] == "Lost"].groupby("customer")["avg_mt"].sum().sort_values(ascending=False).head(15)
    for r, cust in enumerate(lost.index, start=4):
        cr = _crit(cust)
        tl.cell(row=r, column=1, value=cust)
        tl.cell(row=r, column=2, value=f'=COUNTIFS({I}!$B:$B,{cr},{I}!$O:$O,"Lost")')
        tl.cell(row=r, column=3, value=f'=SUMIFS({I}!$I:$I,{I}!$B:$B,{cr},{I}!$O:$O,"Lost")').number_format = MT

    # ── Repeated quotes, no order ──
    rq = wb.create_sheet("Repeated Quotes - No Order")
    rep = items[(items["status"] == "Lost") & (items["count"] >= 2)].sort_values(["count", "avg_mt"], ascending=False)
    rq["A1"], rq["A1"].font = "Quoted 2+ Times, Still No Order", TITLE
    rq["A2"] = (f"{len(rep)} items (list as of the export). To refresh after changing Parameters: "
                "filter Items on Status = Lost and Count >= 2.")
    _header(rq, 3, ["Area", "Customer", "Item", "Product group", "# Quotations sent", "Avg Qty/Quote (MT)",
                    "Salesman", "Last quotation date"], [18, 45, 12, 18, 12, 14, 22, 14])
    for r, rec in enumerate(rep.itertuples(index=False), start=4):
        rq.append([rec.area, rec.customer, rec.item, rec.product_group, int(rec.count), float(rec.avg_mt),
                   rec.salesman, rec.last_quote.to_pydatetime()])
        rq.cell(row=r, column=6).number_format = MT
        rq.cell(row=r, column=8).number_format = DATE

    # ── Waste quotes ──
    ww = wb.create_sheet("Waste quotes")
    _header(ww, 1, qcols, [12, 12, 40, 16, 13, 22, 10, 18, 18])
    for rec in waste_lines.itertuples(index=False):
        ww.append([rec.quote_no, rec.item, rec.customer, rec.order_type, rec.quote_day.to_pydatetime(),
                   rec.salesman, float(rec.mt), rec.area, rec.product_group])
    _fmt(ww, 5, DATE, 2, len(waste_lines) + 1)
    _fmt(ww, 7, MT, 2, len(waste_lines) + 1)

    # ── Reconciliation (optional) ──
    if recon is not None and len(recon):
        rc = wb.create_sheet("Reconciliation")
        rc["A1"], rc["A1"].font = "Reconciliation with the earlier analysis", TITLE
        rc["A2"] = ("'Equals one order line' = the earlier Sales Qty is exactly the MT of ONE order line of that item "
                    "(a lookup returning the first match instead of a sum).")
        _header(rc, 3, ["Item", "Customer", "Earlier Sales Qty", "Corrected Sales Qty", "Difference (MT)",
                        "Equals one order line", "Earlier closed", "Corrected status"], [12, 45, 14, 16, 14, 14, 12, 14])
        for r, rec in enumerate(recon.itertuples(index=False), start=4):
            rc.append([rec.item, rec.customer if isinstance(rec.customer, str) else "",
                       float(rec.earlier_sales),
                       f'=IFERROR(INDEX({I}!$M:$M,MATCH($A{r},{I}!$A:$A,0)),"not in period")',
                       f'=IFERROR(D{r}-C{r},"")',
                       "Yes" if rec.equals_one_order_line else "",
                       "Yes" if rec.earlier_closed else "No",
                       f'=IFERROR(INDEX({I}!$O:$O,MATCH($A{r},{I}!$A:$A,0)),"not in period")'])
            for j in (3, 4, 5):
                rc.cell(row=r, column=j).number_format = MT
        rc.freeze_panes = "A4"

    # ── How calculated ──
    hc = wb.create_sheet("How calculated", 1)
    hc["A1"], hc["A1"].font = "How every number is calculated", TITLE
    _header(hc, 3, ["Number", "Definition", "Formula", "Value"], [36, 70, 55, 14])
    defs = [
        ("Item", "One Fact # quoted in the period (packaging quotes only).", "Quote lines, one row per Fact #", None),
        ("Count (quotes)", "Number of quote lines for the item in the period.", "COUNTIFS(Quote lines Item = item)", None),
        ("AVG Qty/Quote", "Average MT of those quote lines = the item's quoted volume.", "AVERAGEIFS(Quote lines MT, Item = item)", None),
        ("Sales Qty", "SUM of the MT of ALL order lines for the item, entered from its first quote date up to the order cutoff.",
         "SUMIFS(Order lines MT, Item = item, Order date >= First quote, Order date <= Cutoff)", None),
        ("Closed", "Sales Qty > 0.", "Status = Closed", "=Summary!B7"),
        ("Open", "No order yet, and the last quote is less than 'Buffer days' before the cutoff - too early to call lost.",
         "Status = Open", "=Summary!B9"),
        ("Lost", "No order, and the last quote is at least 'Buffer days' before the cutoff.", "Status = Lost", "=Summary!B8"),
        ("Quote-to-close ratio", "Closed ÷ (Closed + Lost). Open items are excluded until they are old enough.",
         "Summary!B7 / (Summary!B7 + Summary!B8)", "=Summary!B10"),
        ("Total quoted volume", "Sum of AVG Qty/Quote over all items.", "SUM(Items AVG Qty/Quote)", "=Summary!B11"),
        ("Total sales volume", "Sum of Sales Qty over all items.", "SUM(Items Sales Qty)", "=Summary!B12"),
        ("Volume fill rate", "Sales ÷ Quoted. Can exceed 100%: one quote can lead to several orders (repeat releases).",
         "Summary!B12 / Summary!B11", "=Summary!B13"),
        ("Buffer days", "Measured on 2026 data: 90.0% of orders are entered within 20 days of the quote.",
         "Parameters!B5", "=Parameters!B5"),
        ("Waste sales quotes", "Order Type 'Open Waste Order' (scrap cartons, plastic, steel) - not packaging demand, "
         "kept out of the KPIs and listed on 'Waste quotes'.", "Waste quotes sheet", "=Summary!B19"),
        ("Area / Product group / Salesman", "Area and Product group from the first quote line of the item; Salesman and "
         "Latest order type from its most recent quote line. 'Product group' is the export's Box Style Group.", "", None),
    ]
    for r, (a, b_, c, d) in enumerate(defs, start=4):
        hc.cell(row=r, column=1, value=a).font = BOLD
        hc.cell(row=r, column=2, value=b_).alignment = Alignment(wrap_text=True, vertical="top")
        hc.cell(row=r, column=3, value=c).alignment = Alignment(wrap_text=True, vertical="top")
        if d:
            cell = hc.cell(row=r, column=4, value=d)
            cell.number_format = PCT if a in ("Quote-to-close ratio", "Volume fill rate") else (MT if "volume" in a.lower() or "Waste" in a else INT)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
