#!/usr/bin/env python3
"""gate_screen.py — 价值投资门禁第 0/1 道门自动判定。

读取 data/snapshots/<ticker>.json，按 value-investing-gate skill 的硬指标逐项检查，
输出人类可读表格 + 机器可读 JSON（reports/<ticker>_gate01.json）。

用法:
    python tools/gate_screen.py <ticker> [--high-leverage]   # 高杠杆行业用 --high-leverage 标记

判定值: PASS / FAIL / INSUFFICIENT（数据不足，按 skill 规则视为不通过）
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PASS, FAIL, INSUF = "PASS", "FAIL", "INSUFFICIENT"


def consec(years: dict, key: str, n: int, cond, use_latest: bool = True):
    """最近 n 个有数据的年份是否连续满足 cond(value)。返回 (判定, 明细)。"""
    ys = sorted(y for y in years if years[y].get(key) is not None)
    if use_latest:
        ys = ys[-n:]
    if len(ys) < n:
        return INSUF, {y: years[y][key] for y in ys}
    detail = {y: years[y][key] for y in ys}
    ok = all(cond(years[y][key]) for y in ys)
    return (PASS if ok else FAIL), detail


def latest(years: dict, key: str):
    ys = sorted(y for y in years if years[y].get(key) is not None)
    return (ys[-1], years[ys[-1]][key]) if ys else (None, None)


def check(snap: dict, high_leverage: bool) -> dict:
    years = snap["years"]
    checks = []

    def add(gate, item, verdict, detail, threshold):
        checks.append({"gate": gate, "item": item, "verdict": verdict,
                       "detail": detail, "threshold": threshold})

    # ---------- 第 0 道：一票否决 ----------
    add(0, "高杠杆行业", FAIL if high_leverage else PASS,
        "人工标记" if high_leverage else "非高杠杆行业（默认）", "银行/地产/保险/航空等否决")

    neg_ocf = [y for y in sorted(years) if (years[y].get("ocf") or 0) < 0]
    add(0, "经营现金流连续2年为负", FAIL if len(neg_ocf) >= 2 else PASS,
        f"负值年份: {neg_ocf or '无'}", "否决")

    low_cover = [y for y in sorted(years)
                 if years[y].get("ocf_np") is not None and years[y]["ocf_np"] < 0.5]
    add(0, "现金流长期远低于净利润(<50%)", FAIL if len(low_cover) >= 3 else PASS,
        f"覆盖<50%的年份: {low_cover or '无'}", "否决")

    # ---------- 第 1 道：硬指标（林园严格版） ----------
    y_gm, gm = latest(years, "gross_margin")
    gm_ok = gm is not None and gm >= 0.5
    # 毛利率连续3年不下滑
    gm_years = sorted(y for y in years if years[y].get("gross_margin") is not None)[-4:]
    declines = [f"{a}->{b}" for a, b in zip(gm_years, gm_years[1:])
                if years[b]["gross_margin"] < years[a]["gross_margin"]]
    v = INSUF if gm is None else (PASS if gm_ok and not declines else FAIL)
    add(1, "毛利率>=50%且连续3年不下滑", v,
        f"最新({y_gm}): {gm:.1%}; 下滑区间: {declines or '无'}" if gm else "无数据",
        ">=50%, 3年不下滑")

    v, detail = consec(years, "roe", 5, lambda x: x >= 0.15)
    add(1, "ROE连续5年>=15%", v,
        {y: f"{x:.1%}" for y, x in detail.items()}, ">=15% x 5年")

    v, detail = consec(years, "ocf_np", 3, lambda x: x >= 0.8)
    add(1, "经营现金流/净利润>=80%(近3年)", v,
        {y: f"{x:.0%}" for y, x in detail.items()}, ">=80%")

    y_cx, cx = latest(years, "capex_rev")
    add(1, "资本开支/营收<10%", INSUF if cx is None else (PASS if cx < 0.10 else FAIL),
        f"最新({y_cx}): {cx:.1%}" if cx else "无数据", "<10%")

    y_dr, dr = latest(years, "debt_ratio")
    add(1, "资产负债率<50%", INSUF if dr is None else (PASS if dr < 0.5 else FAIL),
        f"最新({y_dr}): {dr:.1%}" if dr else "无数据", "<50%")

    v, detail = consec(years, "rev_growth", 3, lambda x: x > 0.10)
    add(1, "营收连续3年>10%增长", v,
        {y: f"{x:.1%}" for y, x in detail.items()}, ">10% x 3年")

    v, detail = consec(years, "np_growth", 3, lambda x: x > 0)
    add(1, "净利润连续3年正增长", v,
        {y: f"{x:.1%}" for y, x in detail.items()}, ">0 x 3年")

    y_dv, dv = latest(years, "distributions")
    add(1, "有稳定分红记录", INSUF if dv is None else (PASS if dv > 0 else FAIL),
        f"最新({y_dv}): 分配 {dv/1e8:.1f}亿（含利息）" if dv else "无数据", ">0")

    cl = {y: years[y]["contract_liab"] for y in sorted(years) if years[y].get("contract_liab")}
    add(1, "预收款/合同负债（参考项）", PASS if cl else INSUF,
        {y: f"{v/1e8:.1f}亿" for y, v in cl.items()}, "越多越好")

    n_fail = sum(1 for c in checks if c["verdict"] in (FAIL, INSUF))
    first_fail = next((c for c in checks if c["verdict"] in (FAIL, INSUF)), None)
    verdict = "PASS_GATE01" if n_fail == 0 else f"REJECT@GATE{first_fail['gate']}"
    return {"ticker": snap["ticker"], "name": snap.get("name"), "verdict": verdict,
            "checks": checks, "source": snap.get("source"), "as_of": snap.get("as_of")}


def fmt_pct(s):
    try:
        return f"{float(s):.1%}"
    except (TypeError, ValueError):
        return str(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--high-leverage", action="store_true")
    args = ap.parse_args()

    snap = json.loads((REPO / "data" / "snapshots" / f"{args.ticker}.json").read_text(encoding="utf-8"))
    result = check(snap, args.high_leverage)

    out = REPO / "reports" / f"{args.ticker}_gate01.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n== {result['name']} ({result['ticker']}) 门禁结论: {result['verdict']} ==  [数据源: {result['source']}, {result['as_of']}]")
    for c in result["checks"]:
        mark = {"PASS": "✅", "FAIL": "❌", "INSUFFICIENT": "⚠️ "}[c["verdict"]]
        print(f"  [G{c['gate']}] {mark} {c['verdict']:<12} {c['item']:<28} | {c['detail']}")
    print(f"\n  (机器可读结果 -> {out})")


if __name__ == "__main__":
    main()
