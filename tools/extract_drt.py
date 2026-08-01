# -*- coding: utf-8 -*-
"""从 iFinD/Yahoo 原始 CSV 提取达仁堂多年关键指标（仅事实数据，无分析结论）"""
import csv, os, glob

RAW = "/Users/zijuezhang/CodeBuddy/RichFuture/data/raw"

def load(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return {}
    hdr, val = rows[0], rows[1]
    return dict(zip(hdr, val))

def num(d, key):
    v = d.get(key, "")
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

YEARS = [2019, 2020, 2021, 2022, 2023, 2024, 2025]
print(f"{'年份':<6}{'营收(亿)':>10}{'营业成本(亿)':>12}{'毛利率%':>9}{'归母净利(亿)':>12}{'扣非?:':>8}{'经营现金流(亿)':>14}{'购建资产(亿)':>12}{'分配股利(亿)':>12}")
rows = []
for y in YEARS:
    isd = load(f"{RAW}/600329.SH_{y}_is.csv")
    bsd = load(f"{RAW}/600329.SH_{y}_bs.csv")
    cfd = load(f"{RAW}/600329.SH_{y}_cf.csv")
    if not isd:
        continue
    rev = num(isd, 'ths_operating_total_revenue_stock')
    cost = num(isd, 'ths_operating_cost_stock') or num(isd, 'ths_operating_cost_2_stock')
    np_par = num(isd, 'ths_np_atoopc_stock')
    inv_inc = num(isd, 'ths_invest_income_stock')
    sales_fee = num(isd, 'ths_sales_fee_stock')
    ocf = num(cfd, 'ths_ncf_from_oa_stock')
    capex = num(cfd, 'ths_cash_paid_for_assets_stock')
    dist = num(cfd, 'ths_cash_paid_for_pd_stock')
    ta = num(bsd, 'ths_total_assets_stock')
    tl = num(bsd, 'ths_total_liab_stock')
    eq_par = num(bsd, 'ths_total_equity_atoopc_stock') or num(bsd, 'ths_eq_atoopc_stock')
    contract = num(bsd, 'ths_contract_liabilities_stock') or num(bsd, 'ths_contract_liab_stock')
    goodwill = num(bsd, 'ths_goodwill_stock')
    money = num(bsd, 'ths_currency_fund_stock')
    gm = (rev - cost) / rev * 100 if rev and cost else None
    rows.append(dict(y=y, rev=rev, cost=cost, gm=gm, np=np_par, inv=inv_inc, sf=sales_fee,
                     ocf=ocf, capex=capex, dist=dist, ta=ta, tl=tl, eq=eq_par,
                     contract=contract, gw=goodwill, money=money))
    print(f"{y:<6}{rev/1e8:>10.2f}{(cost or 0)/1e8:>12.2f}{(gm or 0):>9.2f}{(np_par or 0)/1e8:>12.2f}{'':>8}{(ocf or 0)/1e8:>14.2f}{(capex or 0)/1e8:>12.2f}{(dist or 0)/1e8:>12.2f}")

print()
print(f"{'年份':<6}{'总资产(亿)':>10}{'总负债(亿)':>10}{'负债率%':>9}{'归母权益(亿)':>12}{'ROE%':>8}{'OCF/归母%':>10}{'capex/营收%':>12}{'投资收益(亿)':>12}{'销售费用(亿)':>12}{'合同负债(亿)':>12}{'商誉(亿)':>9}{'货币资金(亿)':>11}")
prev_eq = None
for r in rows:
    roe = r['np'] / ((r['eq'] + prev_eq) / 2) * 100 if r['np'] and r['eq'] and prev_eq else (r['np'] / r['eq'] * 100 if r['np'] and r['eq'] else None)
    print(f"{r['y']:<6}{(r['ta'] or 0)/1e8:>10.2f}{(r['tl'] or 0)/1e8:>10.2f}{(r['tl']/r['ta']*100 if r['tl'] and r['ta'] else 0):>9.2f}{(r['eq'] or 0)/1e8:>12.2f}{(roe or 0):>8.2f}{(r['ocf']/r['np']*100 if r['ocf'] and r['np'] else 0):>10.2f}{(r['capex']/r['rev']*100 if r['capex'] and r['rev'] else 0):>12.2f}{(r['inv'] or 0)/1e8:>12.2f}{(r['sf'] or 0)/1e8:>12.2f}{(r['contract'] or 0)/1e8:>12.2f}{(r['gw'] or 0)/1e8:>9.2f}{(r['money'] or 0)/1e8:>11.2f}")
    prev_eq = r['eq']

print()
print("营收/归母 同比（注意口径：2024-11 剥离医药商业）:")
prev_rev = prev_np = None
for r in rows:
    rg = (r['rev']/prev_rev-1)*100 if prev_rev and r['rev'] else None
    ng = (r['np']/prev_np-1)*100 if prev_np and r['np'] else None
    print(f"  {r['y']}: 营收同比 {('%+.2f%%' % rg) if rg is not None else '—'}  归母同比 {('%+.2f%%' % ng) if ng is not None else '—'}")
    prev_rev, prev_np = r['rev'], r['np']

# D&A from cash flow statement (补充资料列在最后)
print()
print("2025 现金流表尾部字段（折旧摊销等）:")
cfd = load(f"{RAW}/600329.SH_2025_cf.csv")
for k, v in cfd.items():
    if v and any(s in k for s in ['deprec', 'amort', 'da_', '_da', 'fixed_asset_dep', 'intangible']):
        print(f"  {k} = {v}")

# info
info = load(f"{RAW}/600329.SH_info.csv")
print()
print("总股本:", info.get('ths_total_shares_stock'), " 注册资本:", info.get('ths_reg_capital_stock'))
print("控股股东:", info.get('ths_controlling_holder_stock'), info.get('ths_controlling_holder_held_ratio_stock'))
