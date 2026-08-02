#!/usr/bin/env python3
"""lx_valuation.py — 理杏仁式估值分位计算（历史统计+机械区间，严格不预测）

数据源：东方财富数据中心 RPT_VALUEANALYSIS_DET（日频 PE_TTM/PE_LAR/PB_MRQ/PS_TTM/PCF_OCF_TTM/PEG_CAR/市值/收盘，2018 至今）
分位点定义（理杏仁官方）：当前值在过去所有取样点所在位置的百分数（= 低于当前值的取样点占比）。
窗口：3Y / 5Y / 10Y / 全部；区间阈值默认 20%/80%（可用 --lo/--hi 调整）。
用法：python lx_valuation.py --code 600329.SH [--lo 20 --hi 80] [--dividend-ttm 1.802e9] [--out x.json]
口径纪律：分位序列剔除 ≤0 与缺失值（中证口径）；输出全部为历史统计事实，区间标注为机械相对位置，不含任何未来判断。
"""
import argparse, datetime, json, sys, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0"}
METRICS = ["PE_TTM", "PB_MRQ", "PS_TTM", "PCF_OCF_TTM"]

def fetch_all(code):
    rows, page = [], 1
    while True:
        url = ("https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_VALUEANALYSIS_DET"
               f"&columns=ALL&filter=(SECUCODE%3D%22{code}%22)&pageNumber={page}&pageSize=500"
               "&sortTypes=-1&sortColumns=TRADE_DATE&source=HSF10&client=PC")
        req = urllib.request.Request(url, headers=UA)
        d = json.load(urllib.request.urlopen(req, timeout=30))
        data = (d.get("result") or {}).get("data")
        if not data:
            break
        rows += data
        if len(data) < 500:
            break
        page += 1
    if not rows:
        sys.exit(f"无估值历史数据：{code}（接口不可达或代码错误）")
    return rows

def percentile(series, cur):
    vals = [v for v in series if v is not None and v > 0]  # 剔除负值/缺失（中证口径）
    if not vals or cur is None:
        return None, len(vals)
    if cur <= 0:
        return None, len(vals)  # 当前值为负：PE 失真，不出分位
    return round(100 * sum(1 for v in vals if v < cur) / len(vals), 2), len(vals)

def zone(p, lo, hi):
    if p is None:
        return "失真/不可判"
    if p < lo:
        return f"低估区(<{lo}%)"
    if p > hi:
        return f"高估区(>{hi}%)"
    return "中性区"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--lo", type=float, default=20)
    ap.add_argument("--hi", type=float, default=80)
    ap.add_argument("--dividend-ttm", type=float, default=None, help="近12个月分红总额(元)，用于股息率；缺省则不输出")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    rows = fetch_all(a.code)
    cur = rows[0]
    today = datetime.date.fromisoformat(cur["TRADE_DATE"][:10])
    windows = {}
    for label, years in [("3Y", 3), ("5Y", 5), ("10Y", 10), ("ALL", 99)]:
        cut = today - datetime.timedelta(days=years * 365.25)
        win = [r for r in rows if datetime.date.fromisoformat(r["TRADE_DATE"][:10]) >= cut]
        w = {}
        for k in METRICS:
            p, n = percentile([r.get(k) for r in win], cur.get(k))
            w[k] = {"percentile": p, "n_samples": n, "zone": zone(p, a.lo, a.hi)}
        pe_p, pb_p = w["PE_TTM"]["percentile"], w["PB_MRQ"]["percentile"]
        w["估值温度(PE/PB分位均值,社区复合)"] = round((pe_p + pb_p) / 2, 2) if pe_p is not None and pb_p is not None else None
        windows[label] = w

    flags = []
    pe_p10, pb_p10, ps_p10 = (windows["10Y"][k]["percentile"] for k in ("PE_TTM", "PB_MRQ", "PS_TTM"))
    if pe_p10 is not None and ps_p10 is not None and abs(pe_p10 - ps_p10) > 50:
        flags.append(f"PE 分位({pe_p10}%)与 PS 分位({ps_p10}%)背离>50pp：利润与营收的价格错位（利润含非经常项/利润率结构突变），单一指标结论禁止承重")
    if pe_p10 is not None and pb_p10 is not None and abs(pe_p10 - pb_p10) > 50:
        flags.append(f"PE 分位({pe_p10}%)与 PB 分位({pb_p10}%)背离>50pp：净资产收益率结构变化，历史分布代表性存疑")
    if cur.get("PEG_CAR") is not None and cur["PEG_CAR"] < 0:
        flags.append(f"PEG_CAR={round(cur['PEG_CAR'],2)}<0：盈利增速为负，PEG 失真禁用")
    if cur.get("PE_TTM") is not None and cur["PE_TTM"] <= 0:
        flags.append("PE_TTM≤0：亏损期，PE 分位不可用，改用 PB/PS")

    dy = None
    if a.dividend_ttm:
        dy = round(100 * a.dividend_ttm / cur["TOTAL_MARKET_CAP"], 2)

    out = {"meta": {"code": a.code, "name": cur.get("SECURITY_NAME_ABBR"), "as_of": cur["TRADE_DATE"][:10],
                    "source": "东方财富 RPT_VALUEANALYSIS_DET（日频，point-in-time 口径）",
                    "分位点定义": "理杏仁官方：当前值在过去所有取样点所在位置的百分数；序列剔除≤0与缺失",
                    "区间阈值": f"低估<{a.lo}% / 中性 / 高估>{a.hi}%",
                    "承重假设(显式)": "历史分布代表未来分布（遍历性假设）——结构变化时分位失真，区间标注仅为机械相对位置，不构成未来判断"},
           "当前值": {"收盘": cur["CLOSE_PRICE"], "总市值_亿": round(cur["TOTAL_MARKET_CAP"] / 1e8, 1),
                     "PE_TTM": round(cur["PE_TTM"], 2), "PE_LAR": round(cur["PE_LAR"], 2),
                     "PB_MRQ": round(cur["PB_MRQ"], 2), "PS_TTM": round(cur["PS_TTM"], 2),
                     "PCF_OCF_TTM": round(cur["PCF_OCF_TTM"], 2), "PEG_CAR": cur.get("PEG_CAR"),
                     "股息率_TTM%": dy},
           "分位与区间": windows,
           "背离与失真红旗": flags,
           "数据范围": f"{rows[-1]['TRADE_DATE'][:10]} → {rows[0]['TRADE_DATE'][:10]}，{len(rows)} 个交易日"}
    txt = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt)
        print(f"written: {a.out}")
    else:
        print(txt)

if __name__ == "__main__":
    main()
