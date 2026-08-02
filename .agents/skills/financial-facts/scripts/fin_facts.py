#!/usr/bin/env python3
"""fin_facts.py — 固定标的事实层财务指标计算（数学/会计视角，严格不预测）

输入：data/raw/<code>_em_{is,bs,cf,main}.json（东方财富 F10 导出，年度行，新→旧）
输出：JSON 指标包（stdout 或 --out 文件）：meta / M1-M8 / flags(红旗) / missing_fields
用法：python fin_facts.py --code 600329.SH [--dir data/raw] [--snapshot data/snapshots/600329.SH.json]

纪律（与 SKILL.md 一致）：本脚本只计算已实现数据与其算术关系，输出一律标注"事实"或
"算术延伸"；不做任何外推、不给目标价、不作贵贱判断。缺字段计入 missing_fields，
缺失模块整体标 null，禁止补拍。
"""
import argparse, json, os, sys

def load(base, code, kind):
    p = os.path.join(base, f"{code}_em_{kind}.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))

def g(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None

def div(a, b):
    return round(a / b, 4) if a is not None and b not in (None, 0) else None

def sub(a, b):
    return None if a is None or b is None else a - b

MISSING = set()

def need(row, *keys):
    v = g(row, *keys)
    if v is None:
        MISSING.add("/".join(keys))
    return v

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--dir", default="data/raw")
    ap.add_argument("--snapshot", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    isL, bsL, cfL, mnL = (load(a.dir, a.code, k) for k in ("is", "bs", "cf", "main"))
    if not (isL and bsL and cfL):
        sys.exit(f"缺三表原始文件于 {a.dir}/{a.code}_em_{{is,bs,cf}}.json")
    n = min(len(isL), len(bsL), len(cfL))
    years, M1, M2, M3, M4, M5, M6, M7 = [], [], [], [], [], [], [], []

    prev_bs = None  # 上一年（更旧）资产负债表，供均值口径
    for i in range(n):
        I, B, C = isL[i], bsL[i], cfL[i]
        yr = (I.get("REPORT_DATE_NAME") or I.get("REPORT_DATE", ""))[:4]
        years.append(yr)

        rev   = need(I, "OPERATE_INCOME", "TOTAL_OPERATE_INCOME")
        np_   = need(I, "NETPROFIT")
        pnp   = need(I, "PARENT_NETPROFIT")
        ta    = need(B, "TOTAL_ASSETS")
        te    = need(B, "TOTAL_EQUITY")
        ocf   = need(C, "NETCASH_OPERATE")
        icf   = need(C, "NETCASH_INVEST")
        fcf   = need(C, "NETCASH_FINANCE")
        sale  = need(C, "SALES_SERVICES")
        tp    = need(I, "TOTAL_PROFIT")

        # M1 勾稽守恒（残差越小越自洽；>1% 总资产或 >1% 现金变动额即红旗）
        bs_resid = sub(ta, need(B, "TOTAL_LIAB_EQUITY"))
        cce = need(C, "CCE_ADD")
        end_cce = g(C, "END_CCE")
        cf_resid = None if None in (ocf, icf, fcf, cce) else round(ocf + icf + fcf - cce, 2)
        M1.append({"year": yr,
                   "bs_identity_resid": bs_resid,
                   "cash_identity_resid": cf_resid,
                   "cash_identity_resid注": "残差≈汇率变动对现金的影响（第四项），|残差|≤期末现金0.5% 视为自洽",
                   "_end_cce": end_cce,
                   "收现比_销售收现/收入": div(sale, rev),
                   "净现比_经营现金/净利润": div(ocf, np_)})

        # M2 盈利质量
        M2.append({"year": yr,
                   "应计率_(净利-经营现金)/总资产": div(sub(np_, ocf), ta),
                   "投资收益/利润总额": div(need(I, "INVEST_INCOME"), tp),
                   "扣非/归母": div(need(I, "DEDUCT_PARENT_NETPROFIT"), pnp),
                   "核心利润率_(总收入-总成本)/总收入": div(sub(need(I, "TOTAL_OPERATE_INCOME"), need(I, "TOTAL_OPERATE_COST")), need(I, "TOTAL_OPERATE_INCOME"))})

        # M3 资产质量
        debt = sum(x for x in (need(B, "SHORT_LOAN"), g(B, "LONG_LOAN"), g(B, "BOND_PAYABLE")) if x) or None
        M3.append({"year": yr,
                   "商誉/净资产": div(g(B, "GOODWILL"), te),
                   "应收(票据+账款)/收入": div(g(B, "NOTE_ACCOUNTS_RECE") or g(B, "ACCOUNTS_RECE"), rev),
                   "存货/营业成本": div(g(B, "INVENTORY"), need(I, "OPERATE_COST")),
                   "在建工程/固定资产": div(g(B, "CIP"), g(B, "FIXED_ASSET")),
                   "货币资金": g(B, "MONETARYFUNDS"),
                   "有息负债(短借+长借+应付债)": debt,
                   "存贷双高_货币资金/总资产": div(g(B, "MONETARYFUNDS"), ta),
                   "存贷双高_有息负债/总资产": div(debt, ta)})

        # M4 资本回报（杜邦用均值口径；无上年数据则首年用期末）
        avg_ta = ta if prev_bs is None else (ta + g(prev_bs, "TOTAL_ASSETS")) / 2
        avg_te = te if prev_bs is None else (te + g(prev_bs, "TOTAL_EQUITY")) / 2
        m_row = mnL[i] if mnL and i < len(mnL) else {}
        margin, turn, mult = div(np_, rev), div(rev, avg_ta), div(avg_ta, avg_te)
        dupont = None if None in (margin, turn, mult) else round(margin * turn * mult, 4)
        tax_rate = div(need(I, "INCOME_TAX"), tp)
        nopat = None if None in (tp, tax_rate) else round((tp + (g(I, "FINANCE_EXPENSE") or 0)) * (1 - tax_rate), 2)
        ic = None if None in (debt, te) else debt + te
        M4.append({"year": yr,
                   "ROE加权_披露": g(m_row, "ROEJQ"),
                   "杜邦三分_净利率": margin, "杜邦三分_资产周转": turn, "杜邦三分_权益乘数": mult,
                   "杜邦ROE(三分乘积)": dupont,
                   "ROIC_NOPAT/(有息负债+净资产)": div(nopat, ic)})
        prev_bs = B

        # M5 股东回报（现金流口径：分配股利利润或偿付利息支付的现金，含少数股东与利息，标注 ≠ 宣告分红）
        div_paid = g(C, "ASSIGN_DIVIDEND_PORFIT")
        M5.append({"year": yr,
                   "分红利息支付现金": div_paid,
                   "股权融资_吸收投资收到现金": g(C, "ACCEPT_INVEST_CASH"),
                   "分红支付率(现金流口径/净利润)": div(div_paid, np_)})

        # M6 现金流画像（八型：经营/投资/筹资符号组合）
        def s(x): return "+" if (x or 0) > 0 else "-"
        portrait = {"++-": "扩张失血型", "+++": "全吸型(警惕)", "+--": "奶牛型",
                    "+-+": "蛮牛型", "-+-": "衰退输血型", "-++": "赌徒型",
                    "---": "休克型", "--+": "蛮牛失血型"}.get(s(ocf) + s(icf) + s(fcf), "未定")
        M6.append({"year": yr, "经营/投资/筹资": s(ocf) + s(icf) + s(fcf), "画像": portrait})

        # M7 离群（披露口径 YoY + 结构突变）
        M7.append({"year": yr,
                   "收入YoY": g(I, "OPERATE_INCOME_YOY"),
                   "净利润YoY": g(I, "NETPROFIT_YOY"),
                   "经营现金YoY": g(C, "NETCASH_OPERATE_YOY")})

    # 汇总红旗（事实阈值，不预测）
    flags = []
    if sum(1 for m in M1 if m["净现比_经营现金/净利润"] is not None and m["净现比_经营现金/净利润"] < 0.8) >= 2:
        flags.append("净现比<0.8 连续两年以上（利润现金含量存疑）")
    if any(m["收现比_销售收现/收入"] is not None and m["收现比_销售收现/收入"] < 1 for m in M1):
        flags.append("存在收现比<1 的年份（收入含白条扩张）")
    if any(abs(m["cash_identity_resid"] or 0) > 0.005 * max(1, abs(m["_end_cce"] or 1)) for m in M1):
        flags.append("现金守恒残差超期末现金 0.5%（经营+投资+筹资 ≠ 现金净增加，且非汇率项可解释）")
    if any((m["商誉/净资产"] or 0) > 0.1 for m in M3):
        flags.append("商誉/净资产>10%（减值敞口）")
    if any((m["投资收益/利润总额"] or 0) > 0.2 for m in M2):
        flags.append("投资收益占利润总额>20%（盈利依赖非主业）")
    if any((m["扣非/归母"] or 1) < 0.8 for m in M2):
        flags.append("扣非/归母<0.8（非经常项贡献大）")
    if any((m["存贷双高_货币资金/总资产"] or 0) > 0.3 and (m["存贷双高_有息负债/总资产"] or 0) > 0.3 for m in M3):
        flags.append("存贷双高（货币资金与有息负债同时>总资产30%）")

    # M8 现状快照（可选；白名单精确键搬运披露值，不评贵贱；禁止模糊子串误匹配）
    M8 = None
    if a.snapshot and os.path.exists(a.snapshot):
        snap = json.load(open(a.snapshot, encoding="utf-8"))
        WL = ("ticker", "name", "currency", "as_of", "share_capital", "listings",
              "dividends_declared_fy", "fx_to_report_currency", "caliber_notes",
              "pe_ttm", "pb", "total_market_cap", "总市值", "股息率", "dividend_yield")
        keep = {k: snap[k] for k in WL if k in snap}
        def dig(d, pre=""):
            for k, v in (d.items() if isinstance(d, dict) else []):
                p = f"{pre}.{k}" if pre else k
                if k.lower() in ("pe_ttm", "pb", "pe", "total_market_cap", "dividend_yield", "market_cap"):
                    yield p, v
                elif isinstance(v, dict) and pre.count(".") < 2:
                    yield from dig(v, p)
        keep.update(dict(dig(snap)))
        M8 = {"source": a.snapshot, "披露值搬运": keep}

    out = {"meta": {"code": a.code, "periods": years, "source": f"{a.dir}/{a.code}_em_*.json",
                    "口径": "东方财富 F10 年度合并报表；ROIC 为近似口径(NOPAT≈(利润总额+财务费用)×(1-实际税率))；分红为现金流口径≠宣告口径",
                    "性质": "全部为已实现事实或其算术关系，无任何预测成分"},
           "M1_勾稽守恒": M1, "M2_盈利质量": M2, "M3_资产质量": M3, "M4_资本回报": M4,
           "M5_股东回报": M5, "M6_现金流画像": M6, "M7_离群YoY": M7, "M8_现状快照": M8,
           "flags_红旗": flags, "missing_fields": sorted(MISSING)}
    txt = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt)
        print(f"written: {a.out}")
    else:
        print(txt)

if __name__ == "__main__":
    main()
