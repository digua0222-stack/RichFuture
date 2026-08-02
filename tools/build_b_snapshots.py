#!/usr/bin/env python3
"""build_b_snapshots.py — 为沪B三标的（伊泰B股/宝信B/鄂资B股）从东财F10报表接口取数并落盘快照。

口径（与 data/snapshots/200429.SZ.json 对齐）:
  revenue=TOTAL_OPERATE_INCOME, cogs=OPERATE_COST, np_parent=PARENT_NETPROFIT,
  ocf=NETCASH_OPERATE, capex=CONSTRUCT_LONG_ASSET,
  da=FA_IR_DEPR+IA_AMORTIZE+LPE_AMORTIZE, distributions=ASSIGN_DIVIDEND_PORFIT,
  equity=PARENT_EQUITY_BALANCE, ta=TOTAL_ASSETS, tl=TOTAL_LIABILITIES,
  contract_liab=CONTRACT_LIAB, goodwill=GOODWILL, rd=RESEARCH_EXPENSE
"""
import json
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAP = REPO / "data" / "snapshots"

BASE = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis"
YEARS = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]

STOCKS = [
    {
        "ticker": "900948.SH", "name": "伊泰B股", "industry": "煤炭",
        "share_capital": 2929267782,
        "listings": [{"ticker": "900948.SH", "last_close": 2.879, "date": "2026-07-31", "currency": "USD"}],
    },
    {
        "ticker": "900926.SH", "name": "宝信B", "industry": "IT服务(宝钢系)",
        "share_capital": 2857904137,
        "listings": [{"ticker": "900926.SH", "last_close": 0.892, "date": "2026-07-31", "currency": "USD"}],
    },
    {
        "ticker": "900936.SH", "name": "鄂资B股", "industry": "化工/煤电",
        "share_capital": 2798776254,
        "listings": [{"ticker": "900936.SH", "last_close": 1.173, "date": "2026-07-31", "currency": "USD"}],
    },
]

def fetch(api, code, date):
    url = f"{BASE}/{api}?companyType=4&reportDateType=0&reportType=1&dates={date}&code={code}"
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["data"][0] if d.get("data") else {}

def code_of(ticker):
    if ticker.endswith(".SH"):
        return "SH" + ticker[:-3]
    if ticker.endswith(".SZ"):
        return "SZ" + ticker[:-3]
    return ticker

def build(stk):
    code = code_of(stk["ticker"])
    years = {}
    for date in YEARS:
        y = date[:4]
        lrb = fetch("lrbAjaxNew", code, date)
        zcf = fetch("zcfzbAjaxNew", code, date)
        xjl = fetch("xjllbAjaxNew", code, date)
        if not (lrb and zcf and xjl):
            print(f"  {y}: 数据缺失，跳过")
            continue
        rev = lrb.get("TOTAL_OPERATE_INCOME")
        cogs = lrb.get("OPERATE_COST")
        np_ = lrb.get("PARENT_NETPROFIT")
        rd = lrb.get("RESEARCH_EXPENSE")
        ta = zcf.get("TOTAL_ASSETS")
        tl = zcf.get("TOTAL_LIABILITIES")
        eq = zcf.get("TOTAL_PARENT_EQUITY")
        cl = zcf.get("CONTRACT_LIAB")
        gw = zcf.get("GOODWILL")
        ocf = xjl.get("NETCASH_OPERATE")
        capex = xjl.get("CONSTRUCT_LONG_ASSET")
        da = sum(x for x in [xjl.get("FA_IR_DEPR"), xjl.get("IA_AMORTIZE"), xjl.get("LPE_AMORTIZE")] if x) or None
        dist = xjl.get("ASSIGN_DIVIDEND_PORFIT")
        d = {
            "revenue": rev, "cogs": cogs, "np_parent": np_, "rd_expense": rd,
            "equity_parent": eq, "total_assets": ta, "total_liab": tl,
            "contract_liab": cl, "goodwill": gw,
            "ocf": ocf, "capex": capex, "da": da, "distributions": dist,
        }
        d["gross_margin"] = (rev - cogs) / rev if (rev and cogs is not None) else None
        d["debt_ratio"] = tl / ta if (ta and tl is not None) else None
        d["owner_earnings"] = (np_ + da - capex) if (np_ is not None and da is not None and capex is not None) else None
        d["ocf_np"] = ocf / np_ if (ocf is not None and np_) else None
        d["capex_rev"] = capex / rev if (rev and capex is not None) else None
        d["roe"] = np_ / eq if (np_ is not None and eq) else None
        years[y] = d

    ys = sorted(years)
    for i, y in enumerate(ys):
        if i == 0:
            years[y]["rev_growth"] = None
            years[y]["np_growth"] = None
        else:
            p = ys[i - 1]
            if years[p]["revenue"]:
                years[y]["rev_growth"] = (years[y]["revenue"] - years[p]["revenue"]) / years[p]["revenue"]
            if years[p]["np_parent"]:
                years[y]["np_growth"] = (years[y]["np_parent"] - years[p]["np_parent"]) / years[p]["np_parent"]

    snap = {
        "ticker": stk["ticker"], "name": stk["name"], "industry": stk["industry"],
        "currency": "CNY", "source": "东财F10报表(2021-2025年报)", "as_of": "2026-08-02",
        "share_capital": stk["share_capital"],
        "listings": stk["listings"],
        "fx_to_report_currency": {"pair": "USDCNY=X", "rate": 7.15, "date": "2026-07-31"},
        "years": years,
    }
    out = SNAP / f"{stk['ticker']}.json"
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{stk['ticker']} saved. years: {ys}")

if __name__ == "__main__":
    SNAP.mkdir(exist_ok=True)
    for stk in STOCKS:
        build(stk)
