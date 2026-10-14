"""
Checks cop/logic.py against the PM's COP workbook (2025 vs 2026, Jan-Sep).
The workbook is company data and is NOT in the repo: point COP_TEST_XLSX at a local copy to run, e.g.
    COP_TEST_XLSX="C:/path/2026 COP month.xlsx" pytest tests/test_cop_logic.py
"""
import os
import pytest
from cop import load, logic as L

XLSX = os.environ.get("COP_TEST_XLSX")
pytestmark = pytest.mark.skipif(not XLSX or not os.path.exists(XLSX), reason="set COP_TEST_XLSX to the workbook")


@pytest.fixture(scope="module")
def result():
    content = open(XLSX, "rb").read()
    data = {int(y): load.read_year(content, "w.xlsx", y)[0] for y in ("2024", "2025", "2026")}
    return L.run(data, L.Settings(month_to=9))


def test_cop_totals(result):
    assert round(result["bridge"]["cop_py"]) == 40538
    assert round(result["bridge"]["cop_cy"]) == 51852


def test_bridge(result):
    b = result["bridge"]
    assert round(b["volume"]) == -3431
    assert round(b["price"]) == 5961
    assert round(b["cost_mix"]) == 8590
    assert round(b["new"]) == 2170
    assert round(b["lost"]) == -2309
    assert round(b["low_data"]) == 332
    assert abs(b["check"]) < 1e-6


def test_customer_count(result):
    assert len(result["table"]) == 653      # incl. one '(no customer name)' customer, as in the workbook


def test_rule_tree_reproduces_workbook_with_old_tier(result):
    t = result["table"]
    counts = L.strategy_excel_tier(t, 2026, 2025).value_counts().sort_index().to_dict()
    assert counts == {1: 87, 2: 16, 3: 21, 4: 77, 5: 37, 6: 47, 7: 37, 8: 52, 9: 279}


def test_one_strategy_per_customer(result):
    assert result["table"]["strategy_no"].between(1, 9).all()
