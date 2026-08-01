#!/usr/bin/env python3
"""update_snapshot_gujing.py — 将 200596.SZ 快照补齐为 M1 合规（一次性脚本）。

原则：快照仅含事实与口径标注；分析判断（增速假设等）不入快照。
- owner_earnings 改为扣非口径（np_deducted + D&A - capex，总额 capex 保守口径），
  原归母口径保留为 owner_earnings_parent。
- np_deducted 为年报披露约数（2023-2025 经多源核验）。
"""
import json
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "data" / "snapshots" / "200596.SZ.json"
snap = json.loads(P.read_text(encoding="utf-8"))

snap["currency"] = "CNY"
snap["source"] = "ifind原始报表 + 公司公告/年报 + 多源联网核验"
snap["as_of"] = "2026-08-01"
snap["share_structure"] = {
    "total": 528600000,
    "A股_000596.SZ": 408600000,
    "B股_200596.SZ": 120000000,
    "note": "B股于深交所挂牌、港币报价交易；红利以人民币宣告、按汇率折港币支付——B股本质为人民币资产。"
            "A/B 同股同权（分红方案对两地股东一致）。控股股东安徽古井集团持股51.34%。",
}

# 扣非净利润（年报披露，亿元级约数 → 元）
NP_DEDUCTED = {2023: 4_495_000_000.0, 2024: 5_457_000_000.0, 2025: 3_489_000_000.0}

for y, yr in snap["years"].items():
    yi = int(y)
    # 保留原归母口径股东盈余
    if yr.get("owner_earnings") is not None:
        yr["owner_earnings_parent"] = yr["owner_earnings"]
    nd = NP_DEDUCTED.get(yi)
    if nd is not None:
        yr["np_deducted"] = nd
        # 扣非口径股东盈余 = 扣非 + D&A - capex（总额 capex 保守口径，不区分维持/扩张）
        yr["owner_earnings"] = round(nd + yr["da"] - yr["capex"], 2)
        # 扣非口径 OCF 覆盖
        yr["ocf_np_deducted"] = round(yr["ocf"] / nd, 4)
# 扣非增速
for a, b in [(2023, 2024), (2024, 2025)]:
    snap["years"][str(b)]["np_deducted_growth"] = round(
        NP_DEDUCTED[b] / NP_DEDUCTED[a] - 1, 4)
snap["years"]["2023"]["np_deducted_growth"] = 0.476  # 2022扣非约30.4亿(年报口径)推算，约数

# FY 宣告口径分红（区别于现金流量表支付口径 distributions）
snap["dividends_declared_fy"] = {
    "2023": {"amount": 2_378_700_000.0, "per_10_shares": 45.0, "payout": 0.518,
             "note": "年度10派45（无中期分红）"},
    "2024": {"amount": 3_171_600_000.0, "per_10_shares": 60.0, "payout": 0.5749,
             "note": "中期10派10(2024-12)+年度10派50(2025-06-26除息)"},
    "2025": {"amount": 2_325_840_000.0, "per_10_shares": 44.0, "payout": 0.6553,
             "note": "中期10派10(2026-01)+年度10派34(2026-07-17除息)；公司公告分红率65.53%"},
}

# 修正 A 股价格日期格式
for lt in snap["listings"]:
    if lt["ticker"] == "000596.SZ":
        lt["date"] = "2026-07-31"

snap["post_period"] = {
    "2026Q1": {
        "revenue": 7_446_000_000.0, "revenue_yoy": -0.1859,
        "np_parent": 1_607_000_000.0, "np_yoy": -0.3103,
        "contract_liab": 2_304_796_934.0,
        "contract_liab_vs_yearend": 0.5164,
        "ocf": 1_910_000_000.0, "ocf_yoy": 0.036,
        "note": "2026Q1营收/归母同比仍双位数下滑（Q4低基数上收窄），合同负债较2025年末+51.64%但同比-13.7亿；"
                "经营性现金流同比+3.6%率先转正。",
    }
}

snap["caliber_notes"] = [
    "np_deducted 为年报披露扣非归母净利润约数（2023≈44.95亿 / 2024=54.57亿 / 2025=34.89亿，同比-36.06%）；"
    "2025年扣非已内含黄鹤楼酒业商誉减值约3.1亿元（商誉 5.61亿→2.47亿），扣非口径天然惩罚并购后遗症。",
    "owner_earnings 自本快照版本起改为扣非口径（扣非+D&A-capex，总额capex不区分维持/扩张，保守）；"
    "归母口径留存于 owner_earnings_parent。近3年扣非股东盈余：2023=24.75亿 / 2024=35.69亿 / 2025=25.80亿，avg3≈28.75亿。",
    "distributions 为现金流量表『分配股利利润或偿付利息支付的现金』（含利息、支付口径）；"
    "FY宣告口径见 dividends_declared_fy。",
    "B股(200596.SZ)以港币报价：2026-07-31收盘63.39 HKD，按 HKDCNY=0.8603 折54.53 CNY，B/A比价57.4%。"
    "A股(000596.SZ)同日收盘94.95 CNY。",
    "2025年为政策冲击（最严禁酒令）+行业去库存极端年：营收-20.13%、归母-35.67%，Q4单季归母-4.11亿（亏损）；"
    "2025Q3/Q4营收同比-52%/-47%，2026Q1收窄至-19%。",
    "2025年年份原浆销量-10.4%、吨价-10.0%（量价齐跌）；古20批价485元较2023年高点540元持续下行，"
    "电商一度468元击穿批价——提价权在行业收缩期失效。",
    "行业背景：2025年规上白酒产量354.9万千升(-12.1%)，连续9年下降，较2016年峰值1358.4万千升累计-74%（国家统计局）。",
]

P.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"快照已更新: {P}")
for y in ("2023", "2024", "2025"):
    yr = snap["years"][y]
    print(f"  {y}: 扣非={yr['np_deducted']/1e8:.2f}亿 OE(扣非)={yr['owner_earnings']/1e8:.2f}亿 "
          f"OE(归母)={yr['owner_earnings_parent']/1e8:.2f}亿 ocf/扣非={yr['ocf_np_deducted']:.1%}")
avg3 = sum(snap["years"][y]["owner_earnings"] for y in ("2023", "2024", "2025")) / 3
print(f"  avg3(扣非OE) = {avg3/1e8:.2f}亿")
