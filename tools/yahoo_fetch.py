#!/usr/bin/env python3
"""yahoo_fetch.py — 从 Yahoo Finance 拉取三大报表，生成标准化公司快照 JSON。

用于 iFinD 不覆盖或数据异常的市场（HK、SGX、US）。Yahoo 年报仅覆盖最近 4 个财年。

用法:
    python tools/yahoo_fetch.py <ticker> [--name 公司名] [--currency CNY] [--fx HKDCNY=X]

输出:
    data/raw/<ticker>_yf_<is|bs|cf|info|price|fx>.csv
    data/snapshots/<ticker>.json（与 ifind_fetch.py 同一 schema）
"""
import argparse
import csv
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
YF_TOOL = Path(
    r"H:\KimiData\daimon-share\daimon\runtime\kimi-code\home\plugins\managed\yahoo_finance\scripts\yahoo_finance_tool.py"
)

IS_MAP = {
    "Total Revenue": "revenue",
    "Cost Of Revenue": "cogs",
    "Gross Profit": "gross_profit",
    "Net Income Common Stockholders": "np_parent",
    "Net Income": "np_total",
    "Basic Average Shares": "avg_shares",
    "Reconciled Depreciation": "da",
}
BS_MAP = {
    "Total Assets": "total_assets",
    "Total Liabilities Net Minority Interest": "total_liab",
    "Stockholders Equity": "equity_parent",
    "Common Stock Equity": "equity_common",
    "Goodwill": "goodwill",
    "Current Deferred Revenue": "contract_liab",
    "Cash And Cash Equivalents": "cash",
}
CF_MAP = {
    "Operating Cash Flow": "ocf",
    "Capital Expenditure": "capex_raw",          # 负值
    "Cash Dividends Paid": "distributions_raw",  # 负值
    "Depreciation And Amortization": "da_cf",
    "Repurchase Of Capital Stock": "buyback_raw",  # 负值
}


def yf_call(api: str, params: dict) -> None:
    cmd = ["python3", str(YF_TOOL), "call", "--api-name", api,
           "--params-json", json.dumps(params, ensure_ascii=False)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"Yahoo call failed: {api} {params.get('ticker')}\n{r.stdout[-500:]}\n{r.stderr[-300:]}")


def read_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def num(v):
    try:
        return float(v) if v not in (None, "", "--") else None
    except (TypeError, ValueError):
        return None


def safe_div(a, b):
    return a / b if a is not None and b not in (None, 0) else None


def fetch_stmt(ticker: str, ftype: str, tag: str, fmap: dict) -> dict:
    """返回 {year: {field: value}}"""
    raw = REPO / "data" / "raw" / f"{ticker}_yf_{tag}.csv"
    if not raw.exists():
        yf_call("get_financial_statement", {
            "ticker": ticker, "financial_type": ftype,
            "file_path": str(raw).replace("\\", "/")})
    out = {}
    for row in read_rows(raw):
        year = (row.get("date") or "")[:4]
        if not year:
            continue
        rec = {}
        for src, key in fmap.items():
            rec[key] = num(row.get(src))
        out[year] = rec
    return out


def derive(years: dict) -> dict:
    out = {}
    ys = sorted(years)
    for i, y in enumerate(ys):
        d = dict(years[y])
        prev = years[ys[i - 1]] if i > 0 else {}
        d["capex"] = abs(d.pop("capex_raw")) if d.get("capex_raw") is not None else None
        d["distributions"] = abs(d.pop("distributions_raw")) if d.get("distributions_raw") is not None else None
        d["buyback"] = abs(d.pop("buyback_raw")) if d.get("buyback_raw") is not None else None
        if d.get("da") is None:
            d["da"] = d.get("da_cf")
        d.pop("da_cf", None)
        d["gross_margin"] = safe_div(d.get("gross_profit"), d.get("revenue"))
        eq = d.get("equity_parent") or d.get("equity_common")
        prev_eq = prev.get("equity_parent") or prev.get("equity_common")
        avg_eq = (eq + prev_eq) / 2 if eq is not None and prev_eq is not None else eq
        d["roe"] = safe_div(d.get("np_parent"), avg_eq)
        d["ocf_np"] = safe_div(d.get("ocf"), d.get("np_parent"))
        d["capex_rev"] = safe_div(d.get("capex"), d.get("revenue"))
        d["debt_ratio"] = safe_div(d.get("total_liab"), d.get("total_assets"))
        d["owner_earnings"] = (
            d["np_parent"] + (d["da"] or 0) - (d["capex"] or 0)
            if d.get("np_parent") is not None else None)
        d["rev_growth"] = safe_div(
            (d["revenue"] - prev["revenue"]) if d.get("revenue") is not None and prev.get("revenue") else None,
            prev.get("revenue"))
        d["np_growth"] = safe_div(
            (d["np_parent"] - prev["np_parent"]) if d.get("np_parent") is not None and prev.get("np_parent") else None,
            prev.get("np_parent"))
        out[y] = d
    return out


def fetch_price(ticker: str, tag: str = "price") -> dict:
    raw = REPO / "data" / "raw" / f"{ticker}_yf_{tag}.csv"
    yf_call("get_historical_stock_prices", {
        "ticker": ticker, "period": "5d", "interval": "1d",
        "file_path": str(raw).replace("\\", "/")})
    rows = read_rows(raw)
    if not rows:
        return {}
    last = rows[-1]
    return {"ticker": ticker,
            "last_close": num(last.get("Close")) or num(last.get("close")),
            "date": last.get("Date") or last.get("date") or ""}


def fetch_info(ticker: str) -> dict:
    raw = REPO / "data" / "raw" / f"{ticker}_yf_info.csv"
    yf_call("get_stock_info", {"ticker": ticker,
                               "file_path": str(raw).replace("\\", "/")})
    rows = read_rows(raw)
    info = {}
    if rows:
        r = rows[0]
        for k in ("currency", "financialCurrency", "sharesOutstanding", "marketCap",
                  "longName", "sector", "industry", "country", "returnOnEquity"):
            if k in r and r[k] not in ("", None):
                info[k] = r[k]
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--name", default="")
    ap.add_argument("--currency", default="", help="报表币种（不填则用 info.financialCurrency）")
    ap.add_argument("--fx", default="", help="报表币种←价格币种 的汇率代码，如 HKDCNY=X")
    args = ap.parse_args()

    years = {}
    for ftype, tag, fmap in (("income_stmt", "is", IS_MAP),
                             ("balance_sheet", "bs", BS_MAP),
                             ("cashflow", "cf", CF_MAP)):
        print(f"[fetch] {args.ticker} {tag}", file=sys.stderr)
        for year, rec in fetch_stmt(args.ticker, ftype, tag, fmap).items():
            years.setdefault(year, {}).update({k: v for k, v in rec.items() if v is not None})

    info = fetch_info(args.ticker)
    snap = {
        "ticker": args.ticker,
        "name": args.name or info.get("longName", ""),
        "currency": args.currency or info.get("financialCurrency", ""),
        "industry": info.get("industry", ""),
        "source": "yahoo_finance",
        "as_of": str(date.today()),
        "share_capital": num(info.get("sharesOutstanding")),
        "listings": [fetch_price(args.ticker)],
        "years": derive(years),
        "note": "yahoo 年报仅覆盖最近 4 个财年",
    }
    if args.fx:
        fx = fetch_price(args.fx, tag="fx")
        snap["fx_to_report_currency"] = {"pair": args.fx, "rate": fx.get("last_close"),
                                         "date": fx.get("date")}
    out = REPO / "data" / "snapshots" / f"{args.ticker}.json"
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] snapshot -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
