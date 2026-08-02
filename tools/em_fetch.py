#!/usr/bin/env python3
"""em_fetch.py — 东财 F10 三大报表取数器（iFinD 不可用时的降级路径，见 data-contract.md 兜底流程）。

拉取 MAINFINADATA（主要指标）+ GBALANCE + GCASHFLOW + GINCOME（年报），
按 data-contract.md 快照 schema 生成 data/snapshots/<ticker>.json。

用法:
    python tools/em_fetch.py 900948.SH --name 伊泰B股 --years 2019-2025

原始返回留档 data/raw/<ticker>_em_<table>.json。价格/汇率/股本不入快照（需独立核验后人工补 listings）。
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
DC_WEB = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DC_SEC = "https://datacenter.eastmoney.com/securities/api/data/v1/get"


def get(url: str, retries: int = 3, timeout: int = 20) -> dict:
    import random
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(0.6 * (2 ** i) + random.random() * 0.4)
    raise last


def fetch_table(report: str, secucode: str, years: list[str], base: str = DC_SEC,
                extra_filter: str = "") -> list[dict]:
    """按年报报告期逐行拉取（该接口 REPORT_TYPE 过滤 + 排序取前 N 年）。"""
    filt = urllib.parse.quote(f'(SECUCODE="{secucode}")(REPORT_TYPE="年报")' + extra_filter, safe="()")
    url = (f"{base}?reportName={report}&columns=ALL"
           f"&filter={filt}&pageNumber=1&pageSize={len(years) + 2}"
           "&sortTypes=-1&sortColumns=REPORT_DATE&source=HSF10&client=PC")
    rows = ((get(url).get("result") or {}).get("data")) or []
    return [r for r in rows if r.get("REPORT_DATE", "")[:4] in years]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")  # 形如 900948.SH
    ap.add_argument("--name", default="")
    ap.add_argument("--years", default="2019-2025")
    args = ap.parse_args()

    y0, y1 = args.years.split("-")
    years = [str(y) for y in range(int(y0), int(y1) + 1)]
    secucode = args.ticker.upper()
    code = secucode.split(".")[0]

    # 1) 主要指标（datacenter-web，按代码过滤，年报）
    filt = urllib.parse.quote(f'(SECURITY_CODE="{code}")(REPORT_TYPE="年报")', safe="()")
    url = (f"{DC_WEB}?sortColumns=REPORT_DATE&sortTypes=-1&pageSize={len(years) + 2}"
           "&reportName=RPT_F10_FINANCE_MAINFINADATA&columns=ALL&filter=" + filt)
    main_rows = [r for r in (((get(url).get("result") or {}).get("data")) or [])
                 if r.get("REPORT_DATE", "")[:4] in years]
    time.sleep(0.3)
    # 2) 三大报表（datacenter securities）
    bs_rows = fetch_table("RPT_F10_FINANCE_GBALANCE", secucode, years)
    time.sleep(0.3)
    cf_rows = fetch_table("RPT_F10_FINANCE_GCASHFLOW", secucode, years)
    time.sleep(0.3)
    is_rows = fetch_table("RPT_F10_FINANCE_GINCOME", secucode, years)

    raw_dir = REPO / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for tag, rows in (("main", main_rows), ("bs", bs_rows), ("cf", cf_rows), ("is", is_rows)):
        (raw_dir / f"{secucode}_em_{tag}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    by_y = lambda rows: {r["REPORT_DATE"][:4]: r for r in rows}
    M, B, C, I = map(by_y, (main_rows, bs_rows, cf_rows, is_rows))

    snap = {
        "ticker": secucode, "name": args.name, "currency": "CNY",
        "source": "eastmoney_f10（iFinD不可用时的降级路径，见data-contract兜底）",
        "as_of": str(__import__("datetime").date.today()),
        "share_capital": None,  # 待人工核验后补
        "listings": [], "years": {},
    }
    prev_rev = prev_np = prev_nd = None
    for y in years:
        m, b, c, i = M.get(y), B.get(y), C.get(y), I.get(y)
        if not (m and b and c):
            print(f"# WARN {y}: main={bool(m)} bs={bool(b)} cf={bool(c)} is={bool(i)}", file=sys.stderr)
            continue
        rev = m.get("TOTALOPERATEREVE")
        np_p = m.get("PARENTNETPROFIT")
        nd = m.get("KCFJCXSYJLR")
        ocf = c.get("NETCASH_OPERATE")
        capex = c.get("CONSTRUCT_LONG_ASSET")
        da = sum(v or 0 for v in (c.get("FA_IR_DEPR"), c.get("IA_AMORTIZE"),
                                  c.get("LPE_AMORTIZE"), c.get("USERIGHT_ASSET_AMORTIZE")))
        eq = b.get("TOTAL_PARENT_EQUITY")
        ta = b.get("TOTAL_ASSETS")
        tl = b.get("TOTAL_LIABILITIES")
        yr = {
            "total_assets": ta, "total_liab": tl, "equity_parent": eq,
            "contract_liab": b.get("CONTRACT_LIAB"), "goodwill": b.get("GOODWILL") or 0,
            "monetary_funds": b.get("MONETARYFUNDS"), "inventory": b.get("INVENTORY"),
            "revenue": rev, "cogs": (rev - m["MLR"]) if rev and m.get("MLR") else (i or {}).get("OPERATE_COST"),
            "np_parent": np_p, "np_deducted": nd,
            "rd_expense": (i or {}).get("RESEARCH_EXPENSE"),
            "sales_expense": (i or {}).get("SALE_EXPENSE"),
            "ocf": ocf, "capex": capex,
            "distributions": c.get("ASSIGN_DIVIDEND_PORFIT"),
            "gross_margin": (m.get("XSMLL") or 0) / 100,
            "roe_jq": (m.get("ROEJQ") or 0) / 100,       # 加权ROE（东财披露口径）
            "roe_kc_jq": (m.get("ROEKCJQ") or 0) / 100,  # 扣非加权ROE
            "xsjll": (m.get("XSJLL") or 0) / 100,
            "eps": m.get("EPSJB"), "bps": m.get("BPS"),
            "ocf_np": (ocf / np_p) if ocf and np_p else None,
            "ocf_np_deducted": (ocf / nd) if ocf and nd else None,
            "capex_rev": (capex / rev) if capex and rev else None,
            "debt_ratio": (tl / ta) if tl and ta else None,
            "da": da,
            "owner_earnings": (np_p + da - capex) if np_p and capex is not None else None,
            "owner_earnings_deducted": (nd + da - capex) if nd and capex is not None else None,
            "rev_growth": (rev / prev_rev - 1) if rev and prev_rev else None,
            "np_growth": (np_p / prev_np - 1) if np_p and prev_np else None,
            "np_deducted_growth": (nd / prev_nd - 1) if nd and prev_nd else None,
        }
        snap["years"][y] = yr
        prev_rev, prev_np, prev_nd = rev or prev_rev, np_p or prev_np, nd or prev_nd

    # ROE（归母/平均权益，与既有快照口径一致）
    ys = sorted(snap["years"])
    for a, b_ in zip([None] + ys[:-1], ys):
        eq_e = snap["years"][b_]["equity_parent"]
        eq_s = snap["years"][a]["equity_parent"] if a else None
        np_p = snap["years"][b_]["np_parent"]
        if np_p and eq_e:
            avg = (eq_s + eq_e) / 2 if eq_s else eq_e
            snap["years"][b_]["roe"] = np_p / avg
        else:
            snap["years"][b_]["roe"] = None

    out = REPO / "data" / "snapshots" / f"{secucode}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"快照 -> {out}  年份: {list(snap['years'])}")
    for y in ys:
        r = snap["years"][y]
        f = lambda v: f"{v/1e8:.1f}" if isinstance(v, (int, float)) else "—"
        print(f"  {y}: 营收{f(r['revenue'])}亿 归母{f(r['np_parent'])}亿 扣非{f(r['np_deducted'])}亿 "
              f"毛利{r['gross_margin']:.1%} ROE{(r['roe'] or 0):.1%} OCF{f(r['ocf'])}亿 capex{f(r['capex'])}亿 "
              f"负债率{(r['debt_ratio'] or 0):.1%}")


if __name__ == "__main__":
    main()
