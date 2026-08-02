#!/usr/bin/env python3
"""b_share_screen.py — B股三步筛选（净利率≥10% / 负债率≤50% / 近3财年连续分红）。

数据源：新浪 hs_b 节点（B股清单，GBK）+ 东方财富 datacenter（RPT_F10_FINANCE_MAINFINADATA
年报净利率/负债率按报告期批量拉取；RPT_SHAREBONUS_DET 分红逐只拉取）。
仅拉取事实，不做估值判断。

用法:
    python .agents/skills/b-share-screen/scripts/b_share_screen.py [--years 2023,2024,2025]

输出: Markdown 三步筛选表（stdout）。接口不可达时退出码 2 并提示改用 iFinD/联网核验。
注意: datacenter 接口对并发敏感（>1 线程易触发 400），分红段为串行 + 0.15s 间隔。
"""
import argparse
import json
import random
import sys
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def get(url: str, timeout: int = 15, retries: int = 3) -> dict:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 限流/断连：退避重试
            last = e
            time.sleep(0.6 * (2 ** i) + random.random() * 0.4)
    raise last


def list_b_shares() -> list[dict]:
    """全部B股清单。主用新浪 hs_b 节点（GBK），备用于东财 push2。剔除无成交（退市/停牌）。"""
    try:
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "Market_Center.getHQNodeData?page=1&num=100&sort=symbol&asc=1&node=hs_b&_s_r_a=init")
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("gbk", errors="replace")
        out = []
        for it in json.loads(raw):
            if float(it.get("trade") or 0) <= 0:  # 退市/长期停牌
                continue
            sym = it["symbol"]  # sh900901 / sz200596
            out.append({"code": it["code"], "name": it["name"],
                        "mkt": "SH" if sym.startswith("sh") else "SZ"})
        if out:
            return out
    except Exception:
        pass  # 落入东财备选
    out = []
    for fs, mkt in (("m:0+t:7", "SZ"), ("m:1+t:8", "SH")):
        url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&po=1&np=1"
               "&fltt=2&invt=2&fields=f12,f14,f2&fs=" + fs)
        data = get(url)
        for it in (data.get("data") or {}).get("diff") or []:
            if it.get("f2") in (None, "-"):
                continue
            out.append({"code": it["f12"], "name": it["f14"], "mkt": mkt})
    return out


def fin_main_batch(report_date: str) -> dict:
    """按报告期批量拉全市场年报净利率/负债率 → {code: {XSJLL, ZCFZL}}（B股本地筛）。"""
    filt = urllib.parse.quote(f'(REPORT_TYPE="年报")(REPORT_DATE=\'{report_date}\')', safe="()")
    out, page = {}, 1
    while True:
        url = (f"{DC}?sortColumns=SECURITY_CODE&sortTypes=1&pageSize=500&pageNumber={page}"
               "&reportName=RPT_F10_FINANCE_MAINFINADATA"
               "&columns=SECURITY_CODE,REPORT_DATE,XSJLL,ZCFZL"
               f"&filter={filt}")
        data = (get(url).get("result") or {})
        rows = data.get("data") or []
        for r in rows:
            out[r["SECURITY_CODE"]] = r
        if page >= (data.get("pages") or 1):
            break
        page += 1
        time.sleep(0.2)
    return out


def dividends_batch(since: str = "2023-01-01") -> dict:
    """按公告日期范围批量拉全市场分红 → {code: {财年: [方案描述]}}（B股本地筛）。
    财年归属取 REPORT_DATE 年份（中期/年度/特别分红均归入所属财年）。"""
    filt = urllib.parse.quote(f"(NOTICE_DATE>='{since}')", safe="()")
    out, page, pages = {}, 1, 1
    while page <= pages:
        url = (f"{DC}?sortColumns=NOTICE_DATE&sortTypes=-1&pageSize=500&pageNumber={page}"
               "&reportName=RPT_SHAREBONUS_DET"
               "&columns=SECURITY_CODE,REPORT_DATE,IMPL_PLAN_PROFILE,PRETAX_BONUS_RMB,ASSIGN_PROGRESS"
               f"&filter={filt}")
        data = (get(url).get("result") or {})
        pages = data.get("pages") or 1
        for r in data.get("data") or []:
            prof = r.get("IMPL_PLAN_PROFILE") or ""
            ry = (r.get("REPORT_DATE") or "")[:4]
            if ry and "派" in prof:
                out.setdefault(r["SECURITY_CODE"], {}).setdefault(ry, []).append(prof)
        page += 1
        time.sleep(0.15)
    return out


def div_sina(code: str) -> dict:
    """新浪分红配股页 fallback（覆盖纯B股/B+H等东财未收录标的）→ {除息年份: [方案]}。
    口径：按除权除息日历年（与东财按REPORT_DATE财年略有差异，筛选布尔判定够用）。"""
    import io
    import pandas as pd
    url = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/{code}.phtml"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("gbk", errors="replace")
    out = {}
    for t in pd.read_html(io.StringIO(html)):
        cols = " ".join(str(c) for c in t.columns)
        if "派息" not in cols or "除权除息日" not in cols:
            continue
        # 摊平多级表头
        t.columns = ["公告日期", "送股", "转增", "派息", "进度", "除息日", "登记日", "红股上市", "详细"][: len(t.columns)]
        for _, row in t.iterrows():
            try:
                cash = float(row.get("派息") or 0)
            except (TypeError, ValueError):
                continue
            if cash <= 0:
                continue
            ex = str(row.get("除息日") or "")
            ann = str(row.get("公告日期") or "")
            # 财年归属以公告日期为准：公告月<=7 → 上一财年（年度/前三季度分红）；>=8 → 当财年（中期分红）
            if ann[:4].isdigit():
                y, m = int(ann[:4]), int(ann[5:7])
                fy = str(y - 1) if m <= 7 else str(y)
                tag = f"除息{ex[:10]}" if ex[:4].isdigit() else f"宣告{ann[:10]},未除息"
                out.setdefault(fy, []).append(f"10派{cash:g}元({tag})")
        break
    return out


def screen(years: list[str]) -> str:
    shares = list_b_shares()
    if not shares:
        print("ERROR: B股清单为空（接口不可达），改用 iFinD/联网核验", file=sys.stderr)
        sys.exit(2)
    # 财务：按报告期批量（3 次请求 + 分页）
    fin_by_year = {}
    for y in years:
        try:
            fin_by_year[y] = fin_main_batch(f"{y}-12-31")
        except Exception as e:
            print(f"# WARN 财务批量 {y}: {e}", file=sys.stderr)
            fin_by_year[y] = {}
        time.sleep(0.3)

    # 分红：按公告日期批量（约 36 页）
    try:
        div_by_code = dividends_batch(since=f"{years[0]}-01-01")
    except Exception as e:
        print(f"# WARN 分红批量: {e}", file=sys.stderr)
        div_by_code = {}

    passed, edge, rej_fin, rej_div = [], [], [], []
    for s in shares:
        code = s["code"]
        div_rec = {y: list(v) for y, v in (div_by_code.get(code) or {}).items()}
        src = "东财"
        if not all(y in div_rec for y in years):
            # 东财未覆盖（B股分红挂A股代码或纯B股）→ 新浪 fallback，除息月<8归上财年、>=8归当财年
            try:
                time.sleep(0.15)
                sina = div_sina(code)
            except Exception as e:
                print(f"# WARN {code} {s['name']} 新浪分红: {e}", file=sys.stderr)
                sina = {}
            for fy, plans in sina.items():
                for p in plans:
                    if p not in div_rec.setdefault(fy, []):
                        div_rec[fy].append(p)
            src = "东财+新浪" if div_rec else "新浪"
        div_years = set(div_rec)
        jll = {y: (fin_by_year[y].get(code) or {}).get("XSJLL") for y in years}
        zfz = {y: (fin_by_year[y].get(code) or {}).get("ZCFZL") for y in years}
        has_all = all(v is not None for v in jll.values()) and all(v is not None for v in zfz.values())
        if not all(y in div_years for y in years):
            rej_div.append((s, sorted(set(years) - div_years)))
            continue
        if not has_all:
            rej_fin.append((s, jll, zfz, "数据缺失"))
            continue
        latest_jll, latest_zfz = jll[years[-1]], zfz[years[-1]]
        ever_breach = any(v < 10 for v in jll.values()) or any(v > 50 for v in zfz.values())
        div_latest = "；".join(div_rec.get(years[-1], []))[:60]
        if latest_jll >= 10 and latest_zfz <= 50:
            (edge if ever_breach else passed).append((s, jll, zfz, div_latest))
        else:
            rej_fin.append((s, jll, zfz, "最新年破线" if not ever_breach else "多年破线"))

    def fmt3(d, unit="%"):
        return " / ".join(f"{d[y]:.1f}{unit}" if d.get(y) is not None else "—" for y in years)

    L = [f"# B股三步筛选结果（财年窗口 {'/'.join(years)}）",
         f"\n扫描 {len(shares)} 只 B 股 → 通过 {len(passed)} + 边缘 {len(edge)}；"
         f"剔除：财务 {len(rej_fin)}、分红 {len(rej_div)}\n",
         "## 通过名单\n", "| 标的 | 代码 | 净利率 | 负债率 | 最新财年分红方案 | 判定 |", "|---|---|---|---|---|---|"]
    for s, jll, zfz, dv in passed:
        L.append(f"| {s['name']} | {s['code']}.{s['mkt']} | {fmt3(jll)} | {fmt3(zfz)} | {dv} | ✅ 通过 |")
    for s, jll, zfz, dv in edge:
        L.append(f"| {s['name']} | {s['code']}.{s['mkt']} | {fmt3(jll)} | {fmt3(zfz)} | {dv} | ⚠️ 边缘（近年有破线） |")
    L += ["\n## 剔除A：分红达标但财务不达标\n", "| 标的 | 代码 | 净利率 | 负债率 | 原因 |", "|---|---|---|---|---|"]
    for s, jll, zfz, why in rej_fin:
        L.append(f"| {s['name']} | {s['code']}.{s['mkt']} | {fmt3(jll)} | {fmt3(zfz)} | {why} |")
    L += ["\n## 剔除B：分红不达标\n", "| 标的 | 代码 | 断分红财年 |", "|---|---|---|"]
    for s, miss in rej_div:
        L.append(f"| {s['name']} | {s['code']}.{s['mkt']} | {', '.join(miss)} |")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default=None, help="逗号分隔，默认=最近三个完整财年")
    args = ap.parse_args()
    if args.years:
        years = args.years.split(",")
    else:
        import datetime
        y = datetime.date.today().year - 1  # 最近完整财年（年报4月底前披露）
        if datetime.date.today().month <= 4:
            y -= 1  # 4月30日前上一年年报可能未出齐
        years = [str(y - 2), str(y - 1), str(y)]
    print(screen(years))


if __name__ == "__main__":
    main()
