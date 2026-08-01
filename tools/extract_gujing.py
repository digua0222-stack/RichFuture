# -*- coding: utf-8 -*-
"""从 iFinD 原始 CSV 提取古井贡酒多年关键指标（仅事实数据，无分析结论）"""
import csv

RAW = "/Users/zijuezhang/CodeBuddy/RichFuture/data/raw"
T = "200596.SZ"

def load(path):
    try:
        with open(path, newline='', encoding='utf-8-sig') as f:
            rows = list(csv.reader(f))
    except FileNotFoundError:
        return {}
    if len(rows) < 2:
        return {}
    return dict(zip(rows[0], rows[1]))

def num(d, key):
    try:
        return float(d.get(key, ""))
    except (ValueError, TypeError):
        return None

YEARS = [2019, 2020, 2021, 2022, 2023, 2024, 2025]
rows = []
for y in YEARS:
    isd, bsd, cfd = load(f"{RAW}/{T}_{y}_is.csv"), load(f"{RAW}/{T}_{y}_bs.csv"), load(f"{RAW}/{T}_{y}_cf.csv")
    if not isd:
        print(f"{y}: 无数据")
        continue
    g = lambda d, k: num(d, k) or 0
    rev = g(isd, 'ths_operating_total_revenue_stock')
    cost = g(isd, 'ths_operating_cost_stock') or g(isd, 'ths_operating_cost_2_stock')
    np_par = g(isd, 'ths_np_atoopc_stock')
    sf = g(isd, 'ths_sales_fee_stock')
    ocf = g(cfd, 'ths_ncf_from_oa_stock')
    capex = g(cfd, 'ths_cash_paid_for_assets_stock')
    ta, tl = g(bsd, 'ths_total_assets_stock'), g(bsd, 'ths_total_liab_stock')
    eq = g(bsd, 'ths_total_equity_atoopc_stock') or g(bsd, 'ths_eq_atoopc_stock')
    contract = g(bsd, 'ths_contract_liabilities_stock') or g(bsd, 'ths_contract_liab_stock')
    gw = g(bsd, 'ths_goodwill_stock')
    money = g(bsd, 'ths_currency_fund_stock')
    dep = g(cfd, 'ths_depreciation_etc_stock')
    amort = g(cfd, 'ths_intangible_assets_amortized_stock')
    rou = g(cfd, 'ths_rou_depreciation_stock')
    rows.append(dict(y=y, rev=rev, cost=cost, np=np_par, sf=sf, ocf=ocf, capex=capex,
                     ta=ta, tl=tl, eq=eq, contract=contract, gw=gw, money=money,
                     da=dep + amort + rou))

print(f"{'年份':<6}{'营收(亿)':>9}{'毛利率%':>8}{'归母(亿)':>9}{'销售费用(亿)':>11}{'OCF(亿)':>9}{'capex(亿)':>10}{'D&A(亿)':>9}")
for r in rows:
    gm = (r['rev'] - r['cost']) / r['rev'] * 100 if r['rev'] else 0
    print(f"{r['y']:<6}{r['rev']/1e8:>9.2f}{gm:>8.2f}{r['np']/1e8:>9.2f}{r['sf']/1e8:>11.2f}{r['ocf']/1e8:>9.2f}{r['capex']/1e8:>10.2f}{r['da']/1e8:>9.2f}")

print()
print(f"{'年份':<6}{'总资产':>8}{'负债率%':>8}{'归母权益':>9}{'ROE%':>7}{'OCF/NP%':>9}{'capex/营收%':>11}{'合同负债':>9}{'商誉':>7}{'货币资金':>9}")
prev_eq = None
for r in rows:
    roe = r['np'] / ((r['eq'] + prev_eq) / 2) * 100 if prev_eq else r['np'] / r['eq'] * 100
    print(f"{r['y']:<6}{r['ta']/1e8:>8.1f}{r['tl']/r['ta']*100:>8.2f}{r['eq']/1e8:>9.2f}{roe:>7.2f}{r['ocf']/r['np']*100:>9.1f}{r['capex']/r['rev']*100:>11.2f}{r['contract']/1e8:>9.2f}{r['gw']/1e8:>7.2f}{r['money']/1e8:>9.2f}")
    prev_eq = r['eq']

print("\n同比：")
prev = None
for r in rows:
    if prev:
        print(f"  {r['y']}: 营收 {(r['rev']/prev['rev']-1)*100:+.2f}%  归母 {(r['np']/prev['np']-1)*100:+.2f}%")
    prev = r

info = load(f"{RAW}/{T}_info.csv")
print("\n总股本:", info.get('ths_total_shares_stock'), " 简称:", info.get('ths_stock_short_name_stock'), " 控股股东:", info.get('ths_controlling_holder_stock'), info.get('ths_controlling_holder_held_ratio_stock'))

import subprocess
print("\n价格尾部:")
print(subprocess.run(['tail', '-6', f'{RAW}/{T}_price.csv'], capture_output=True, text=True).stdout)
