#!/usr/bin/env python3
"""gate_screen.py — 价值投资门禁第 0/1 道门自动判定（三档制）。

读取 data/snapshots/<ticker>.json，按 value-investing-gate skill 的硬指标逐档检查：
  S 档 · 林园档（永续复利型）：毛利率≥50%/ROE连续5年≥15%/营收连续3年>10% 等（最苛刻）
  A 档 · 巴菲特档（稳健价值型）：毛利率≥30%/ROE均值≥15%且近3年≥12%/营收低增长允许
  B 档 · 周期档（中值回归型）：逐年达标→周期均值达标（窗口≥5年且含下行年），ΣOCF/ΣNP≥100%
判定顺序 S→A→B，取最高通过档；三档全 FAIL = REJECT@GATE1。
输出人类可读表格 + 机器可读 JSON（data/derived/<ticker>_gate01.json）。

用法:
    python tools/gate_screen.py <ticker> [--high-leverage]   # 高杠杆行业用 --high-leverage 标记
    python tools/gate_screen.py <ticker> [--cyclical]        # 人工确认强周期行业后启用 B 档判定

判定值: PASS / FAIL / INSUFFICIENT（数据不足，按 skill 规则视为不通过）
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PASS, FAIL, INSUF, REVIEW = "PASS", "FAIL", "INSUFFICIENT", "REVIEW"

TIER_NAMES = {"S": "S档 · 林园档（永续复利型）",
              "A": "A档 · 巴菲特档（稳健价值型）",
              "B": "B档 · 周期档（中值回归型）"}


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


def window(years: dict, key: str, n: int = None):
    """取有数据的年份窗口 {year: value}，n=None 为全部。"""
    ys = sorted(y for y in years if years[y].get(key) is not None)
    if n:
        ys = ys[-n:]
    return {y: years[y][key] for y in ys}


def cagr(first: float, last: float, n_years: int):
    if first is None or last is None or first <= 0 or n_years < 1:
        return None
    return (last / first) ** (1 / n_years) - 1


def check(snap: dict, high_leverage: bool, cyclical: bool = False) -> dict:
    years = snap["years"]
    checks = []

    def add(gate, item, verdict, detail, threshold, tier=None):
        checks.append({"gate": gate, "tier": tier, "item": item, "verdict": verdict,
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

    gate0_fail = any(c["verdict"] in (FAIL, INSUF) for c in checks)

    # ---------- 第 1 道 · S 档（林园档，原严格版） ----------
    y_gm, gm = latest(years, "gross_margin")
    gm_ok = gm is not None and gm >= 0.5
    gm_years = sorted(y for y in years if years[y].get("gross_margin") is not None)[-4:]
    declines = [f"{a}->{b}({(years[a]['gross_margin']-years[b]['gross_margin'])*100:.1f}pp)"
                for a, b in zip(gm_years, gm_years[1:])
                if years[b]["gross_margin"] < years[a]["gross_margin"] - 0.005]
    v = INSUF if gm is None else (PASS if gm_ok and not declines else FAIL)
    add(1, "毛利率>=50%且连续3年不下滑", v,
        f"最新({y_gm}): {gm:.1%}; 下滑区间: {declines or '无'}" if gm else "无数据",
        ">=50%, 3年不下滑(容忍0.5pp)", tier="S")

    v, detail = consec(years, "roe", 5, lambda x: x >= 0.15)
    add(1, "ROE连续5年>=15%", v,
        {y: f"{x:.1%}" for y, x in detail.items()}, ">=15% x 5年", tier="S")

    # 现金流覆盖（S/A 同标准）：近3年均值>=80%，且无单年<50%
    ocf_years = sorted(y for y in years if years[y].get("ocf_np") is not None)[-3:]
    ocf3_verdict, ocf3_detail = None, None
    if len(ocf_years) < 3:
        ocf3_verdict, ocf3_detail = INSUF, f"仅{len(ocf_years)}年数据"
    else:
        vals = {y: years[y]["ocf_np"] for y in ocf_years}
        avg = sum(vals.values()) / 3
        deep = [y for y, x in vals.items() if x < 0.5]
        ocf3_verdict = PASS if (avg >= 0.8 and not deep) else FAIL
        ocf3_detail = f"均值={avg:.0%}; 各年: {{{', '.join(f'{y}:{x:.0%}' for y, x in vals.items())}}}"
    add(1, "经营现金流/净利润>=80%(近3年)", ocf3_verdict, ocf3_detail,
        "均值>=80%且无单年<50%", tier="S")

    y_cx, cx = latest(years, "capex_rev")
    add(1, "资本开支/营收<10%", INSUF if cx is None else (PASS if cx < 0.10 else FAIL),
        f"最新({y_cx}): {cx:.1%}" if cx else "无数据", "<10%", tier="S")

    y_dr, dr = latest(years, "debt_ratio")
    add(1, "资产负债率<50%", INSUF if dr is None else (PASS if dr < 0.5 else FAIL),
        f"最新({y_dr}): {dr:.1%}" if dr else "无数据", "<50%", tier="S")

    v, detail = consec(years, "rev_growth", 3, lambda x: x > 0.10)
    add(1, "营收连续3年>10%增长", v,
        {y: f"{x:.1%}" for y, x in detail.items()}, ">10% x 3年", tier="S")

    v, detail = consec(years, "np_growth", 3, lambda x: x > 0)
    add(1, "净利润连续3年正增长", v,
        {y: f"{x:.1%}" for y, x in detail.items()}, ">0 x 3年", tier="S")

    y_dv, dv = latest(years, "distributions")
    add(1, "有稳定分红记录", INSUF if dv is None else (PASS if dv > 0 else FAIL),
        f"最新({y_dv}): 分配 {dv/1e8:.1f}亿（含利息）" if dv else "无数据", ">0", tier="S")

    cl = {y: years[y]["contract_liab"] for y in sorted(years) if years[y].get("contract_liab")}
    add(1, "预收款/合同负债（参考项）", PASS if cl else INSUF,
        {y: f"{v/1e8:.1f}亿" for y, v in cl.items()}, "越多越好", tier="S")

    # ---------- 第 1 道 · A 档（巴菲特档） ----------
    # 毛利率 >=30%，且近5年累计降幅 <=5pp
    gm5 = window(years, "gross_margin", 5)
    if gm is None or len(gm5) < 2:
        add(1, "毛利率>=30%且5年降幅<=5pp", INSUF if gm is None else REVIEW,
            f"最新({y_gm}): {gm:.1%}" if gm else "无数据", ">=30%, 5年降幅<=5pp", tier="A")
    else:
        ys5 = sorted(gm5)
        drop5 = gm5[ys5[0]] - gm5[ys5[-1]]
        v = PASS if (gm >= 0.30 and drop5 <= 0.05) else FAIL
        add(1, "毛利率>=30%且5年降幅<=5pp", v,
            f"最新({y_gm}): {gm:.1%}; {ys5[0]}->{ys5[-1]} 降幅 {drop5*100:.1f}pp",
            ">=30%, 5年降幅<=5pp", tier="A")

    # ROE 近5年均值>=15% 且最近3年每年>=12%
    roe5 = window(years, "roe", 5)
    roe3 = window(years, "roe", 3)
    if len(roe5) < 5 or len(roe3) < 3:
        add(1, "ROE均值>=15%(5年)且近3年>=12%", INSUF,
            f"仅{len(roe5)}年数据", "均值>=15% & 每年>=12%", tier="A")
    else:
        avg5 = sum(roe5.values()) / 5
        ok3 = all(x >= 0.12 for x in roe3.values())
        v = PASS if (avg5 >= 0.15 and ok3) else FAIL
        add(1, "ROE均值>=15%(5年)且近3年>=12%", v,
            f"5年均值={avg5:.1%}; 近3年: {{{', '.join(f'{y}:{x:.1%}' for y, x in roe3.items())}}}",
            "均值>=15% & 每年>=12%", tier="A")

    add(1, "经营现金流/净利润>=80%(近3年，同S档)", ocf3_verdict, ocf3_detail,
        "均值>=80%且无单年<50%", tier="A")

    add(1, "资本开支/营收<12%", INSUF if cx is None else (PASS if cx < 0.12 else FAIL),
        f"最新({y_cx}): {cx:.1%}" if cx else "无数据", "<12%", tier="A")

    add(1, "资产负债率<55%", INSUF if dr is None else (PASS if dr < 0.55 else FAIL),
        f"最新({y_dr}): {dr:.1%}" if dr else "无数据", "<55%", tier="A")

    # 营收：连续3年>0 且近5年 CAGR>=3%
    v_pos, det_pos = consec(years, "rev_growth", 3, lambda x: x > 0)
    rev5 = window(years, "revenue", 6)  # 6 个点才能算 5 年 CAGR；不足则用全部
    ys_r = sorted(rev5)
    cg = cagr(rev5[ys_r[0]], rev5[ys_r[-1]], len(ys_r) - 1) if len(ys_r) >= 2 else None
    if v_pos == INSUF or cg is None:
        v = INSUF
    else:
        v = PASS if (v_pos == PASS and cg >= 0.03) else FAIL
    add(1, "营收3年>0且5年CAGR>=3%", v,
        f"近3年: {{{', '.join(f'{y}:{x:.1%}' for y, x in det_pos.items())}}}; "
        f"CAGR({ys_r[0]}->{ys_r[-1]})={cg:.1%}" if cg is not None else "无数据",
        ">0 x 3年 & CAGR>=3%", tier="A")

    # 净利：连续3年无亏损（np_parent>0）且近5年 CAGR>=0
    np3 = window(years, "np_parent", 3)
    np5 = window(years, "np_parent", 6)
    ys_n = sorted(np5)
    cg_np = cagr(np5[ys_n[0]], np5[ys_n[-1]], len(ys_n) - 1) if len(ys_n) >= 2 else None
    if len(np3) < 3:
        v = INSUF
    elif cg_np is None:
        # 基数年净利为负时 CAGR 不可算 → 用"窗口末年为正 + 亏损年<=1"降级判定
        loss_n = [y for y in ys_n if np5[y] < 0]
        v = PASS if (all(x > 0 for x in np3.values()) and len(loss_n) <= 1) else FAIL
    else:
        v = PASS if (all(x > 0 for x in np3.values()) and cg_np >= 0) else FAIL
    np3_str = f"近3年归母(亿): {{{', '.join(f'{y}:{x/1e8:.1f}' for y, x in np3.items())}}}"
    if cg_np is not None:
        np_detail = f"{np3_str}; CAGR({ys_n[0]}->{ys_n[-1]})={cg_np:.1%}"
    elif len(ys_n) >= 2:
        np_detail = (f"{np3_str}; 基数年({ys_n[0]})归母{np5[ys_n[0]]/1e8:.1f}亿为负→CAGR不可算，"
                     f"降级判定: 窗口末年{np5[ys_n[-1]]/1e8:.1f}亿, 亏损年{[y for y in ys_n if np5[y] < 0] or '无'}")
    else:
        np_detail = "无数据"
    add(1, "净利3年无亏损且5年CAGR>=0", v, np_detail,
        "无亏损 x 3年 & CAGR>=0（基数为负时降级: 末年为正且亏损<=1年）", tier="A")

    add(1, "有分红记录", INSUF if dv is None else (PASS if dv > 0 else FAIL),
        f"最新({y_dv}): 分配 {dv/1e8:.1f}亿" if dv else "无数据", ">0", tier="A")

    # ---------- 第 1 道 · B 档（周期档：周期均值达标；仅人工确认强周期标的 --cyclical 启用） ----------
    if not cyclical:
        add(1, "B档（周期档）判定未启用", INSUF,
            "非人工确认的强周期标的；确认强周期行业后加 --cyclical 重跑",
            "--cyclical 启用", tier="B")
    else:
        all_years = sorted(years)
        down_years = [y for y in all_years
                      if (years[y].get("rev_growth") is not None and years[y]["rev_growth"] < 0)
                      or (years[y].get("np_growth") is not None and years[y]["np_growth"] < 0)]
        window_ok = len(all_years) >= 5 and len(down_years) >= 1
        add(1, "周期窗口>=5年且含下行年", PASS if window_ok else INSUF,
            f"窗口 {all_years[0]}-{all_years[-1]} 共{len(all_years)}年; 下行年: {down_years or '无'}",
            ">=5年 & >=1个下行年", tier="B")

        # 毛利率：周期均值>=25% 且 CV<=0.25
        gms_all = window(years, "gross_margin")
        if len(gms_all) < 3:
            add(1, "毛利率周期均值>=25%且CV<=0.25", INSUF, f"仅{len(gms_all)}年数据",
                "均值>=25% & CV<=0.25", tier="B")
        else:
            mean_gm = sum(gms_all.values()) / len(gms_all)
            cv_gm = (sum((g - mean_gm) ** 2 for g in gms_all.values()) / len(gms_all)) ** 0.5 / mean_gm if mean_gm else None
            v = PASS if (mean_gm >= 0.25 and cv_gm is not None and cv_gm <= 0.25) else FAIL
            add(1, "毛利率周期均值>=25%且CV<=0.25", v,
                f"均值={mean_gm:.1%}（{len(gms_all)}年）; CV={cv_gm:.3f}",
                "均值>=25% & CV<=0.25", tier="B")

        # ROE：周期均值>=10% 且亏损年<=1
        roe_all = window(years, "roe")
        loss_years = [y for y in all_years if (years[y].get("np_parent") or 0) < 0]
        if len(roe_all) < 5:
            add(1, "ROE周期均值>=10%且亏损年<=1", INSUF, f"仅{len(roe_all)}年数据",
                "均值>=10% & 亏损<=1年", tier="B")
        else:
            mean_roe = sum(roe_all.values()) / len(roe_all)
            v = PASS if (mean_roe >= 0.10 and len(loss_years) <= 1) else FAIL
            add(1, "ROE周期均值>=10%且亏损年<=1", v,
                f"均值={mean_roe:.1%}（{len(roe_all)}年）; 亏损年: {loss_years or '无'}",
                "均值>=10% & 亏损<=1年", tier="B")

        # ΣOCF / ΣNP（窗口总额比）>=100%
        sum_ocf = sum(years[y].get("ocf") or 0 for y in all_years)
        sum_np = sum(years[y].get("np_parent") or 0 for y in all_years)
        if sum_np <= 0:
            add(1, "Σ经营现金流/Σ净利润>=100%", FAIL if window_ok else INSUF,
                f"ΣOCF={sum_ocf/1e8:.1f}亿, ΣNP={sum_np/1e8:.1f}亿（窗口净利非正）",
                "总额比>=100%", tier="B")
        else:
            ratio = sum_ocf / sum_np
            add(1, "Σ经营现金流/Σ净利润>=100%", PASS if ratio >= 1.0 else FAIL,
                f"ΣOCF={sum_ocf/1e8:.1f}亿 / ΣNP={sum_np/1e8:.1f}亿 = {ratio:.0%}（{len(all_years)}年）",
                "总额比>=100%", tier="B")

        # capex：周期均值<10% 且单年峰<=20%
        cap_all = window(years, "capex_rev")
        if len(cap_all) < 5:
            add(1, "capex/营收均值<10%且单年峰<=20%", INSUF, f"仅{len(cap_all)}年数据",
                "均值<10% & 峰值<=20%", tier="B")
        else:
            mean_cap = sum(cap_all.values()) / len(cap_all)
            peak_y = max(cap_all, key=cap_all.get)
            peak = cap_all[peak_y]
            v = PASS if (mean_cap < 0.10 and peak <= 0.20) else FAIL
            add(1, "capex/营收均值<10%且单年峰<=20%", v,
                f"均值={mean_cap:.1%}; 峰值({peak_y})={peak:.1%}",
                "均值<10% & 峰值<=20%", tier="B")

        add(1, "资产负债率<60%", INSUF if dr is None else (PASS if dr < 0.60 else FAIL),
            f"最新({y_dr}): {dr:.1%}" if dr else "无数据", "<60%", tier="B")

        # 营收最大单年跌幅 >-50%
        drops_b = [(y, years[y]["rev_growth"]) for y in all_years
                   if years[y].get("rev_growth") is not None and years[y]["rev_growth"] < 0]
        worst_b = min((g for _, g in drops_b), default=None)
        has_rev_growth = any(years[y].get("rev_growth") is not None for y in all_years)
        add(1, "营收最大单年跌幅>-50%", INSUF if not has_rev_growth else (PASS if (worst_b is None or worst_b > -0.50) else FAIL),
            f"最大跌幅: {worst_b:.1%}" if worst_b is not None else "无负增长年份",
            ">-50%", tier="B")

        # 净利亏损年<=1（与 ROE 项共用 loss_years，单列便于人工复核）
        add(1, "窗口内亏损年<=1", INSUF if len(all_years) < 5 else (PASS if len(loss_years) <= 1 else FAIL),
            f"亏损年: {loss_years or '无'}", "<=1个", tier="B")

        # 窗口内有分红记录
        dv_all = {y: years[y]["distributions"] for y in all_years if (years[y].get("distributions") or 0) > 0}
        add(1, "窗口内有分红记录", PASS if dv_all else FAIL,
            f"分红年份: {sorted(dv_all) or '无'}", "任一年>0", tier="B")

    # ---------- 第 2.5 道：永续性财务代理（各档共用参考项） ----------
    gms = [years[y]["gross_margin"] for y in sorted(years) if years[y].get("gross_margin") is not None]
    if len(gms) >= 3:
        mean = sum(gms) / len(gms)
        cv = (sum((g - mean) ** 2 for g in gms) / len(gms)) ** 0.5 / mean if mean else None
        v = PASS if cv <= 0.08 else ("REVIEW" if cv <= 0.15 else FAIL)
        add(2.5, "毛利率变异系数CV（稳定性）", v, f"CV={cv:.3f}（{len(gms)}年）", "<=0.08 / 0.08-0.15 / >0.15")
    else:
        add(2.5, "毛利率变异系数CV（稳定性）", INSUF, f"仅{len(gms)}年数据", "需>=3年")

    drops = [(y, years[y]["rev_growth"]) for y in sorted(years)
             if years[y].get("rev_growth") is not None and years[y]["rev_growth"] < 0]
    worst = min((g for _, g in drops), default=None)
    v = (FAIL if worst <= -0.30 else REVIEW if worst <= -0.10 else PASS) if worst is not None else PASS
    add(2.5, "营收最大单年跌幅", v,
        f"最大跌幅: {worst:.1%}" if worst is not None else "无负增长年份",
        ">-10% PASS / -10%~-30% REVIEW / <=-30% FAIL")

    rein = {}
    for y in sorted(years):
        cap, rd, rev = years[y].get("capex"), years[y].get("rd_expense"), years[y].get("revenue")
        if rev and cap is not None:
            rein[y] = (cap + (rd or 0)) / rev
    if rein:
        recent = list(rein.values())[-3:]
        avg3 = sum(recent) / len(recent)
        v = PASS if avg3 <= 0.10 else ("REVIEW" if avg3 <= 0.15 else FAIL)
        add(2.5, "维持性再投入强度(capex+研发)/营收", v,
            f"近3年均值={avg3:.1%}；各年: {{{', '.join(f'{y}:{v:.0%}' for y, v in rein.items())}}}",
            "<=10% / 10-15% / >15%")
    else:
        add(2.5, "维持性再投入强度(capex+研发)/营收", INSUF, "无数据", "<=10%")

    # ---------- 档位汇总 ----------
    def tier_verdict(t):
        tc = [c for c in checks if c["gate"] == 1 and c["tier"] == t]
        n_bad = sum(1 for c in tc if c["verdict"] in (FAIL, INSUF))
        return ("PASS" if n_bad == 0 else "FAIL"), n_bad

    tiers = {}
    for t in ("S", "A", "B"):
        tv, n_bad = tier_verdict(t)
        fails = [c["item"] for c in checks
                 if c["gate"] == 1 and c["tier"] == t and c["verdict"] in (FAIL, INSUF)]
        tiers[t] = {"name": TIER_NAMES[t], "verdict": tv, "n_fail": n_bad, "fail_items": fails}
    if not cyclical:
        tiers["B"]["verdict"] = "SKIP"
        tiers["B"]["note"] = "B档仅对人工确认的强周期标的启用（--cyclical）"

    highest = next((t for t in ("S", "A", "B") if tiers[t]["verdict"] == "PASS"), None)
    reviews = [c for c in checks if c["verdict"] == "REVIEW"]

    if gate0_fail:
        verdict = "REJECT@GATE0"
    elif highest:
        verdict = f"PASS_TIER_{highest}"
    else:
        verdict = "REJECT@GATE1"
    if reviews:
        verdict += f" | 永续性复核: {len(reviews)}项REVIEW"

    return {"ticker": snap["ticker"], "name": snap.get("name"), "verdict": verdict,
            "highest_pass_tier": highest, "tiers": tiers,
            "checks": checks, "source": snap.get("source"), "as_of": snap.get("as_of")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--high-leverage", action="store_true")
    ap.add_argument("--cyclical", action="store_true",
                    help="人工确认为强周期行业后启用 B 档（周期中值回归型）判定")
    args = ap.parse_args()

    snap = json.loads((REPO / "data" / "snapshots" / f"{args.ticker}.json").read_text(encoding="utf-8"))
    result = check(snap, args.high_leverage, cyclical=args.cyclical)

    out = REPO / "data" / "derived" / f"{args.ticker}_gate01.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    mark = {"PASS": "✅", "FAIL": "❌", "INSUFFICIENT": "⚠️ ", "REVIEW": "🔶"}
    print(f"\n== {result['name']} ({result['ticker']}) 门禁结论: {result['verdict']} ==  [数据源: {result['source']}, {result['as_of']}]")

    sections = [("第 0 道 · 一票否决", lambda c: c["gate"] == 0)]
    for t in ("S", "A", "B"):
        tv = result["tiers"][t]["verdict"]
        sections.append((f"第 1 道 · {TIER_NAMES[t]} —— {tv}", lambda c, t=t: c["gate"] == 1 and c["tier"] == t))
    sections.append(("第 2.5 道 · 永续性财务代理（共用参考）", lambda c: c["gate"] == 2.5))

    for title, pred in sections:
        print(f"\n  ── {title} ──")
        for c in [c for c in result["checks"] if pred(c)]:
            print(f"  {mark[c['verdict']]} {c['verdict']:<12} {c['item']:<30} | {c['detail']}")

    print(f"\n  (机器可读结果 -> {out})")


if __name__ == "__main__":
    main()
