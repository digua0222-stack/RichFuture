#!/usr/bin/env python3
"""ifind_fetch.py — 从 iFinD 拉取三大报表，生成标准化公司快照 JSON。

用法:
    python tools/ifind_fetch.py <ticker> [--name 公司名] [--years 2019-2024] [--listings 200596.SZ,000596.SZ]

输出:
    data/raw/<ticker>_<year>_<bs|is|cf>.csv   原始报表（留档）
    data/snapshots/<ticker>.json               标准化快照（gate_screen.py / dcf.py 的输入）

快照 schema 见 .agents/skills/value-investing-gate/references/data-contract.md
"""
import argparse
import csv
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IFIND_TOOL = Path(
    r"H:\KimiData\daimon-share\daimon\runtime\kimi-code\home\plugins\managed\ifind\scripts\ifind_tool.py"
)

# iFinD THS 字段名 → 快照字段名
BS_MAP = {
    "ths_total_assets_stock": "total_assets",
    "ths_total_liab_stock": "total_liab",
    "ths_total_equity_atoopc_stock": "equity_parent",
    "ths_contract_liab_stock": "contract_liab",
    "ths_goodwill_stock": "goodwill",
}
IS_MAP = {
    "ths_operating_total_revenue_stock": "revenue",
    "ths_operating_cost_stock": "cogs",
    "ths_np_atoopc_stock": "np_parent",
    "ths_np_stock": "np_total",
    "ths_rad_cost_sum_stock": "rd_expense",
    "ths_sales_fee_stock": "sales_expense",
}
CF_MAP = {
    "ths_ncf_from_oa_stock": "ocf",
    "ths_cash_paid_for_assets_stock": "capex",
    "ths_cash_paid_of_distribution_stock": "distributions",  # 分配股利、利润或偿付利息支付的现金（含利息，为分红上界）
    "ths_depreciation_etc_stock": "depreciation",
    "ths_intangible_assets_amortized_stock": "amortization",
    "ths_rou_depreciation_stock": "rou_depreciation",
}


def ifind_call(api: str, params: dict) -> None:
    cmd = ["python3", str(IFIND_TOOL), "call", "--api-name", api,
           "--params-json", json.dumps(params, ensure_ascii=False)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"iFinD call failed: {api} {params.get('ticker')}\n{r.stdout[-500:]}\n{r.stderr[-300:]}")


def read_csv(path: Path) -> dict:
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 2:
        return {}
    return dict(zip(rows[0], rows[1]))


def num(v):
    try:
        return float(v) if v not in (None, "", "--") else None
    except (TypeError, ValueError):
        return None


def fetch_year(ticker: str, period: str, year: str) -> dict:
    rec = {}
    for stmt, fmap in (("bs", BS_MAP), ("is", IS_MAP), ("cf", CF_MAP)):
        raw = REPO / "data" / "raw" / f"{ticker}_{year}_{stmt}.csv"
        if not raw.exists():
            ifind_call("ifind_get_financial_statements", {
                "ticker": ticker, "statement": stmt,
                "financial_parameter": period, "file_path": str(raw).replace("\\", "/"),
            })
        row = read_csv(raw)
        for ths, key in fmap.items():
            rec[key] = num(row.get(ths))
    return rec


def safe_div(a, b):
    return a / b if a is not None and b not in (None, 0) else None


def derive(years: dict) -> dict:
    """计算派生指标：毛利率、ROE（平均净资产口径）、增长率、各项比率。"""
    out = {}
    ys = sorted(years)
    for i, y in enumerate(ys):
        d = dict(years[y])
        prev = years[ys[i - 1]] if i > 0 else {}
        d["gross_margin"] = safe_div(
            (d["revenue"] - d["cogs"]) if d.get("revenue") is not None and d.get("cogs") is not None else None,
            d.get("revenue"))
        avg_eq = None
        if d.get("equity_parent") is not None:
            avg_eq = (d["equity_parent"] + prev["equity_parent"]) / 2 if prev.get("equity_parent") else d["equity_parent"]
        d["roe"] = safe_div(d.get("np_parent"), avg_eq)
        d["ocf_np"] = safe_div(d.get("ocf"), d.get("np_parent"))
        d["capex_rev"] = safe_div(d.get("capex"), d.get("revenue"))
        d["debt_ratio"] = safe_div(d.get("total_liab"), d.get("total_assets"))
        d["da"] = sum(x for x in (d.get("depreciation"), d.get("amortization"), d.get("rou_depreciation")) if x) or None
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


def fetch_price(ticker: str) -> dict:
    raw = REPO / "data" / "raw" / f"{ticker}_price.csv"
    ifind_call("ifind_get_price", {
        "ticker": ticker, "start_date": "2026-07-24", "end_date": str(date.today()),
        "adjust": "none", "file_path": str(raw).replace("\\", "/"),
    })
    with open(raw, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return {}
    last = rows[-1]
    close = None
    for k, v in last.items():
        if "close" in k.lower():
            close = num(v)
    return {"ticker": ticker, "last_close": close, "date": last.get("time") or last.get("date") or ""}


def fetch_share_capital(ticker: str) -> float | None:
    raw = REPO / "data" / "raw" / f"{ticker}_info.csv"
    ifind_call("ifind_get_stock_info", {"ticker": ticker, "file_path": str(raw).replace("\\", "/")})
    row = read_csv(raw)
    for k, v in row.items():
        if "total_share" in k or "总股本" in k:
            n = num(v)
            if n:
                return n
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--name", default="")
    ap.add_argument("--years", default="2019-2024")
    ap.add_argument("--listings", default="", help="逗号分隔的上市代码，用于取价（默认=ticker 本身）")
    ap.add_argument("--currency", default="")
    args = ap.parse_args()

    y0, y1 = (int(x) for x in args.years.split("-"))
    years = {}
    for y in range(y0, y1 + 1):
        print(f"[fetch] {args.ticker} {y}", file=sys.stderr)
        years[str(y)] = fetch_year(args.ticker, f"{y}1231", str(y))

    snap = {
        "ticker": args.ticker,
        "name": args.name,
        "currency": args.currency,
        "source": "ifind",
        "as_of": str(date.today()),
        "share_capital": fetch_share_capital(args.ticker),
        "listings": [],
        "years": derive(years),
    }
    for lt in (args.listings.split(",") if args.listings else [args.ticker]):
        lt = lt.strip()
        if lt:
            try:
                snap["listings"].append(fetch_price(lt))
            except Exception as e:
                print(f"[warn] price fetch failed for {lt}: {e}", file=sys.stderr)

    out = REPO / "data" / "snapshots" / f"{args.ticker}.json"
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] snapshot -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
