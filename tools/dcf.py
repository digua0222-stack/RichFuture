#!/usr/bin/env python3
"""dcf.py — 股东盈余毛估估估值（第 3 道门）。

两阶段 10 年折现 + 永续，输出内在价值区间与敏感性网格。
原则（段永平）：折现是思维方式不是计算器；用保守假设，区间下沿再打折。

用法:
    python tools/dcf.py <ticker> --g1 0.08 --g2 0.05 [--gterm 0.03] [--discount 0.09]
                        [--base latest|avg3] [--mos 0.65]

假设应来自分析判断并留档于 data/assumptions/<ticker>.json（可用 --save-assumptions 保存）。
"""
import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def dcf_value(base: float, g1: float, g2: float, gterm: float, r: float, years1=5, years2=5) -> float:
    """两阶段 + 永续的股东盈余折现值。"""
    pv = 0.0
    cf = base
    for t in range(1, years1 + years2 + 1):
        cf *= (1 + g1) if t <= years1 else (1 + g2)
        pv += cf / (1 + r) ** t
    tv = cf * (1 + gterm) / (r - gterm)
    pv += tv / (1 + r) ** (years1 + years2)
    return pv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--g1", type=float, required=True, help="第1-5年增速")
    ap.add_argument("--g2", type=float, required=True, help="第6-10年增速")
    ap.add_argument("--gterm", type=float, default=0.03)
    ap.add_argument("--discount", type=float, default=0.09, help="折现率=机会成本")
    ap.add_argument("--base", choices=["latest", "avg3"], default="latest")
    ap.add_argument("--base-override", type=float, default=None,
                    help="直接指定基准股东盈余（元），用于一次性收益等非经常年份的人工修正")
    ap.add_argument("--mos", type=float, default=0.65, help="安全边际系数 0.6-0.7")
    ap.add_argument("--save-assumptions", action="store_true")
    args = ap.parse_args()

    snap = json.loads((REPO / "data" / "snapshots" / f"{args.ticker}.json").read_text(encoding="utf-8"))
    years = snap["years"]
    oes = [(y, years[y]["owner_earnings"]) for y in sorted(years) if years[y].get("owner_earnings")]
    if not oes:
        raise SystemExit("无股东盈余数据")
    if args.base_override is not None:
        base = args.base_override
    else:
        base = oes[-1][1] if args.base == "latest" else sum(v for _, v in oes[-3:]) / min(3, len(oes))
    shares = snap.get("share_capital") or (oes and years[oes[-1][0]].get("avg_shares"))

    iv = dcf_value(base, args.g1, args.g2, args.gterm, args.discount)
    per_share = iv / shares if shares else None

    # 敏感性：g1 ±2pp, 折现率 ±1pp
    grid = []
    for dr in (-0.01, 0, 0.01):
        row = []
        for dg in (-0.02, 0, 0.02):
            r = args.discount + dr
            g = args.g1 + dg
            if r - args.gterm <= 0.005:
                row.append(None)
                continue
            v = dcf_value(base, g, min(args.g2, g), args.gterm, r)
            row.append(v / shares if shares else None)
        grid.append(row)

    listings = []
    fx = (snap.get("fx_to_report_currency") or {}).get("rate")
    rep_ccy = snap.get("currency") or "CNY"
    for lt in snap.get("listings", []):
        px = lt.get("last_close")
        if px is None or per_share is None:
            continue
        lt_ccy = lt.get("currency")
        # 仅当上市货币明确标注且与报表币种不同时才折算
        px_cny = px * fx if (lt_ccy and lt_ccy != rep_ccy and fx) else px
        listings.append({
            "ticker": lt["ticker"], "price": px, "price_ccy": lt_ccy or rep_ccy,
            "price_date": lt.get("date"),
            "price_in_report_ccy": round(px_cny, 2),
            "price_vs_iv_low": round(px_cny / (grid[2][0] or per_share), 3),
            "price_vs_iv_mid": round(px_cny / per_share, 3),
            "buy_threshold_mos": round(per_share * args.mos, 2),
            "undervalued_at_mos": px_cny <= per_share * args.mos,
        })

    result = {
        "ticker": snap["ticker"], "name": snap.get("name"),
        "currency": snap.get("currency") or "CNY",
        "base_owner_earnings": round(base, 0),
        "base_source": (f"人工修正基准（剔除一次性损益，参考 {oes[-1][0]}={oes[-1][1]/1e8:.1f}亿）"
                        if args.base_override is not None
                        else f"{args.base} ({oes[-1][0]}={oes[-1][1]/1e8:.1f}亿)"),
        "share_capital": shares,
        "assumptions": {"g1": args.g1, "g2": args.g2, "gterm": args.gterm,
                        "discount": args.discount, "mos": args.mos},
        "intrinsic_value_total": round(iv, 0),
        "intrinsic_value_per_share": round(per_share, 2) if per_share else None,
        "sensitivity_per_share": {
            "rows_discount": [args.discount - 0.01, args.discount, args.discount + 0.01],
            "cols_g1": [args.g1 - 0.02, args.g1, args.g1 + 0.02],
            "grid": [[round(x, 2) if x else None for x in row] for row in grid],
        },
        "iv_range_per_share": [round(grid[2][0], 2), round(grid[0][2], 2)] if shares else None,
        "listings": listings,
        "verdict": ("严重低估" if any(l["undervalued_at_mos"] for l in listings)
                    else "未达到安全边际"),
        "note": "区间下沿=折现率+1pp且g1-2pp；上沿=折现率-1pp且g1+2pp",
    }

    out = REPO / "data" / "derived" / f"{args.ticker}_valuation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.save_assumptions:
        (REPO / "data" / "assumptions").mkdir(exist_ok=True)
        (REPO / "data" / "assumptions" / f"{args.ticker}.json").write_text(
            json.dumps(result["assumptions"] | {"base": args.base}, indent=2), encoding="utf-8")

    r = result
    print(f"\n== {r['name']} ({r['ticker']}) 毛估估估值 ==")
    print(f"  基准股东盈余: {r['base_owner_earnings']/1e8:.1f}亿 ({r['base_source']})")
    a = r["assumptions"]
    print(f"  假设: g1={a['g1']:.0%} g2={a['g2']:.0%} 永续={a['gterm']:.0%} 折现={a['discount']:.0%} 安全边际={a['mos']}")
    print(f"  内在价值/股: {r['intrinsic_value_per_share']} {r['currency']}  区间: {r['iv_range_per_share']}")
    print(f"  敏感性(每股, 行=折现率, 列=g1): {r['sensitivity_per_share']['grid']}")
    for l in r["listings"]:
        flag = "🟢 达到安全边际" if l["undervalued_at_mos"] else "🔴 未达安全边际"
        print(f"  [{l['ticker']}] 现价 {l['price']}（{l['price_date']}）≈ {l['price_in_report_ccy']} {r['currency']}"
              f" | 价格/中枢IV={l['price_vs_iv_mid']:.0%} | 买入线(IV×{a['mos']})={l['buy_threshold_mos']} | {flag}")
    print(f"\n  结论: {r['verdict']}  (机器可读 -> {out})")


if __name__ == "__main__":
    main()
