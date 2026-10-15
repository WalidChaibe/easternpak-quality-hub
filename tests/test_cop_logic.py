"""
Checks cop/logic.py against the PM's COP workbook (2025 vs 2026, Jan-Sep).
The workbook is company data and is NOT in the repo: point COP_TEST_XLSX at a local copy to run, e.g.
    COP_TEST_XLSX="C:/path/2026 COP month.xlsx" pytest tests/test_cop_logic.py
"""
import os
import pytest
from cop import load, logic as L

XLSX = os.environ.get("COP_TEST_XLSX")
needs_file = pytest.mark.skipif(not XLSX or not os.path.exists(XLSX), reason="set COP_TEST_XLSX to the workbook")


@pytest.fixture(scope="module")
def data():
    content = open(XLSX, "rb").read()
    return {int(y): load.read_year(content, "w.xlsx", y)[0] for y in ("2024", "2025", "2026")}


@pytest.fixture(scope="module")
def as_workbook(data):
    """Name merging OFF = exactly the workbook's customer list."""
    return L.run(data, L.Settings(month_to=9, merge_names=False))


@pytest.fixture(scope="module")
def merged(data):
    return L.run(data, L.Settings(month_to=9))


@needs_file
def test_cop_totals(as_workbook):
    assert round(as_workbook["bridge"]["cop_py"]) == 40538
    assert round(as_workbook["bridge"]["cop_cy"]) == 51852


@needs_file
def test_bridge_matches_workbook(as_workbook):
    b = as_workbook["bridge"]
    assert (round(b["volume"]), round(b["price"]), round(b["cost"])) == (-3431, 5961, 8590)
    assert (round(b["new"]), round(b["lost"]), round(b["small"])) == (2170, -2309, 332)
    assert abs(b["check"]) < 1e-6


@needs_file
def test_customer_count(as_workbook, merged):
    assert len(as_workbook["table"]) == 653     # incl. one '(no customer name)' customer, as in the workbook
    assert len(merged["table"]) == 648          # 5 name variants merged in the 2025/2026 universe


@needs_file
def test_rule_tree_reproduces_workbook_with_old_tier(as_workbook):
    counts = L.strategy_excel_tier(as_workbook["table"], 2026, 2025).value_counts().sort_index().to_dict()
    assert counts == {1: 87, 2: 16, 3: 21, 4: 77, 5: 37, 6: 47, 7: 37, 8: 52, 9: 279}


@needs_file
def test_merging_changes_no_total(as_workbook, merged):
    a, b = as_workbook["bridge"], merged["bridge"]
    assert abs(a["cop_py"] - b["cop_py"]) < 1e-6 and abs(a["cop_cy"] - b["cop_cy"]) < 1e-6
    assert abs(b["check"]) < 1e-6


@needs_file
def test_one_strategy_per_customer(merged):
    assert merged["table"]["strategy_no"].between(1, 9).all()


@needs_file
def test_manual_key_account(data):
    s = L.Settings(month_to=9, manual_keys=("Prima International Company",))
    t = L.run(data, s)["table"].set_index("customer")
    assert t.loc["Prima International Company", "key_account"]
    assert t.loc["Prima International Company", "key_reason"] == "Marked by PM"


def test_margin_tier_boundaries():
    s = L.Settings()
    assert [L.margin_tier(x, s) for x in (-0.01, 0, 64.99, 65, 229.99, 230)] == \
           ["Loss-making", "Low", "Low", "Mid", "Mid", "High"]


def test_name_key():
    assert L.name_key("Van Der Hoeven") == L.name_key("Vanderhoeven")
    assert L.name_key("شركة الراشد للاغذية Rashed Bakery") == L.name_key("Rashed Bakery")
    assert L.name_key("شركة الراشد") != L.name_key("شركة التكامل")      # Arabic-only names stay distinct
