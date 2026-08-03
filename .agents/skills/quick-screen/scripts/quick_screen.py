#!/usr/bin/env python3
"""quick_screen.py — 快速筛选（四维参数化：净利率/负债率/现金流/股息率 × 市场或领域）

数据源（东方财富 datacenter 公开接口，全批量、不逐只请求）：
- 标的池+市值/股本/PE/PB/最新收盘价：RPT_VALUEANALYSIS_DET（最新交易日全市场）
- 当月平均股价：候选标的在最新行情日所属自然月内的日收盘价算术平均
- 净利率 XSJLL / 负债率 ZCFZL / 每股经营现金流 MGJYXJJE：RPT_F10_FINANCE_MAINFINADATA（年报）
- 股息率：RPT_SHAREBONUS_DET（目标财年每10股派息合计）×总股本÷最新总市值（近似 TTM，口径见输出注记）

用法示例：
  python quick_screen.py --market b                          # B股四维默认（净利率≥10/负债率≤50/现金流>0/股息率≥3）
  python quick_screen.py --market a --board 中药 --min-dy 2  # A股中药板块，自定义阈值
  python quick_screen.py --market a --min-jll 15 --max-zcfzl 40 --min-ocf 0 --min-dy 4 --years 2023,2024,2025

输出：Markdown 通过名单/边缘名单/剔除清单（stdout）；--out 存文件。接口不可达退出码 2。
纪律：只拉事实并按阈值机械过滤，输出为"待研究候选池"，不作任何贵贱/未来判断。
"""
import argparse, datetime, json, random, sys, time, urllib.parse, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"

def get(url, timeout=20, retries=3):
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

def dc_batch(report, columns, filt, sort="SECURITY_CODE", pause=0.2):
    out, page, pages = [], 1, 1
    fq = urllib.parse.quote(filt, safe="()")
    while page <= pages:
        url = (f"{DC}?sortColumns={sort}&sortTypes=1&pageSize=500&pageNumber={page}"
               f"&reportName={report}&columns={columns}&filter={fq}")
        d = (get(url).get("result") or {})
        pages = d.get("pages") or 1
        out += d.get("data") or []
        page += 1
        time.sleep(pause)
    return out

def latest_trade_date():
    url = (f"{DC}?sortColumns=TRADE_DATE&sortTypes=-1&pageSize=1&pageNumber=1"
           "&reportName=RPT_VALUEANALYSIS_DET&columns=TRADE_DATE")
    d = (get(url).get("result") or {}).get("data") or []
    if not d:
        sys.exit("ERROR: 无法获取最新交易日（接口不可达）")
    return d[0]["TRADE_DATE"][:10]

def universe(date):
    return dc_batch("RPT_VALUEANALYSIS_DET",
                    "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME,TOTAL_MARKET_CAP,TOTAL_SHARES,PE_TTM,PB_MRQ,CLOSE_PRICE",
                    f"(TRADE_DATE='{date}')", sort="SECURITY_CODE")

def month_averages_a(codes, date, chunk_size=80):
    """A股/北证候选月均价：最新行情日所属自然月的日收盘价算术平均。"""
    start = date[:8] + "01"
    values = {}
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i:i + chunk_size]
        quoted = ",".join(f'"{code}"' for code in chunk)
        filt = (f"(SECURITY_CODE in ({quoted}))"
                f"(TRADE_DATE>='{start}')(TRADE_DATE<='{date}')")
        rows = dc_batch("RPT_VALUEANALYSIS_DET",
                        "SECURITY_CODE,TRADE_DATE,CLOSE_PRICE", filt,
                        sort="SECURITY_CODE", pause=0.05)
        for r in rows:
            try:
                close = float(r.get("CLOSE_PRICE"))
            except (TypeError, ValueError):
                continue
            values.setdefault(r["SECURITY_CODE"], []).append(close)
    return {code: sum(closes) / len(closes) for code, closes in values.items() if closes}

def month_averages_b(codes, date):
    """B股候选月均价：新浪日线在最新行情日所属自然月内的收盘价算术平均。"""
    start = date[:8] + "01"
    out = {}
    for code in codes:
        symbol = ("sh" if code.startswith("900") else "sz") + code
        url = ("https://quotes.sina.cn/cn/api/json_v2.php/"
               "CN_MarketDataService.getKLineData"
               f"?symbol={symbol}&scale=240&ma=no&datalen=45")
        rows = get(url)
        closes = []
        for r in rows if isinstance(rows, list) else []:
            day = r.get("day") or ""
            if start <= day <= date:
                try:
                    closes.append(float(r.get("close")))
                except (TypeError, ValueError):
                    pass
        if closes:
            out[code] = sum(closes) / len(closes)
        time.sleep(0.12)
    return out

def fx_rates():
    """美元/港币兑人民币（新浪外汇，近似即期）；失败回退常量并 WARN。"""
    import re
    try:
        url = "https://hq.sinajs.cn/list=fx_susdcny,fx_shkdcny"
        req = urllib.request.Request(url, headers={**UA, "Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="replace")
        def rate(sym, default):
            m = re.search(rf'hq_str_{sym}="([^"]*)"', raw)
            if not m:
                raise ValueError(sym)
            return float(m.group(1).split(",")[3])
        return rate("fx_susdcny", 7.2), rate("fx_shkdcny", 0.92)
    except Exception as e:
        print(f"WARN 汇率接口失败，回退常量 USD7.2/HKD0.92: {e}", file=sys.stderr)
        return 7.2, 0.92

def cny_price(code, price, usd, hkd):
    """B股报价币种折算人民币：沪B(900)美元、深B(200)港币；其余原样。"""
    if code.startswith("900"):
        return price * usd
    if code.startswith("200") or code.startswith("201"):
        return price * hkd
    return price

def sina_prices(symbols):
    """新浪批量行情（hq.sinajs.cn，GBK），{code: 最新价}。symbols 形如 sh900948/sz200596。"""
    import re
    out = {}
    for i in range(0, len(symbols), 60):
        chunk = symbols[i:i + 60]
        url = "https://hq.sinajs.cn/list=" + ",".join(chunk)
        req = urllib.request.Request(url, headers={**UA, "Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", errors="replace")
        for m in re.finditer(r'hq_str_(?:sh|sz)(\d{6})="([^"]*)"', raw):
            parts = m.group(2).split(",")
            if len(parts) > 3:
                try:
                    out[m.group(1)] = float(parts[3])
                except ValueError:
                    pass
        time.sleep(0.3)
    return out

def universe_b():
    """B股池（东财估值明细/push2 均不可靠覆盖B股）：清单用新浪 hs_b，最新价用新浪批量行情。
    无板块/市值/PE/PB字段；股息率改按 每股分红÷最新价 计算，不依赖市值。"""
    stocks, symbols = [], []
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "Market_Center.getHQNodeData?page=1&num=100&sort=symbol&asc=1&node=hs_b&_s_r_a=init")
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read().decode("gbk", errors="replace")
    for it in json.loads(raw):
        if float(it.get("trade") or 0) <= 0:  # 退市/长期停牌
            continue
        sym = it["symbol"]
        stocks.append({"SECUCODE": f"{it['code']}.{'SH' if sym.startswith('sh') else 'SZ'}",
                       "SECURITY_CODE": it["code"], "SECURITY_NAME_ABBR": it["name"],
                       "BOARD_NAME": ""})
        symbols.append(sym)
    prices = sina_prices(symbols)
    out = []
    for s in stocks:
        price = prices.get(s["SECURITY_CODE"])
        if not price:
            print(f"WARN {s['SECURITY_CODE']} {s['SECURITY_NAME_ABBR']} 无最新价，出池", file=sys.stderr)
            continue
        s.update({"TOTAL_MARKET_CAP": None, "TOTAL_SHARES": None,
                  "PE_TTM": None, "PB_MRQ": None, "CLOSE_PRICE": price})
        out.append(s)
    return out

def div_sina_fy(code, target_fy):
    """B股分红东财未覆盖时的 fallback：新浪分红页按除息年归属（公告月<8归上财年）→ 每10股派息合计。"""
    import io, re
    try:
        import pandas as pd
        url = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/{code}.phtml"
        req = urllib.request.Request(url, headers=UA)
        html = urllib.request.urlopen(req, timeout=20).read().decode("gbk", errors="replace")
        total = 0.0
        for t in pd.read_html(io.StringIO(html)):
            cols = " ".join(str(c) for c in t.columns)
            if "派息" not in cols or "公告日期" not in cols:
                continue
            t.columns = ["公告日期", "送股", "转增", "派息", "进度", "除息日", "登记日", "红股上市", "详细"][:len(t.columns)]
            for _, row in t.iterrows():
                m = re.search(r"([\d.]+)", str(row.get("派息") or ""))
                ann = str(row.get("公告日期") or "")
                if not m or not ann[:4].isdigit():
                    continue
                yy, mm = int(ann[:4]), int(ann[5:7])
                fy = str(yy - 1) if mm <= 7 else str(yy)
                if fy == target_fy:
                    total += float(m.group(1))
            break
        return total
    except Exception:
        return 0.0

def in_scope(code, market):
    if market == "all": return True
    if market == "b":   return code.startswith(("900", "200"))
    if market == "b-sh": return code.startswith("900")
    if market == "b-sz": return code.startswith("200")
    if market == "a":   return not code.startswith(("900", "200", "4", "8"))
    if market == "a-sh": return code.startswith(("600", "601", "603", "605", "688"))
    if market == "a-sz": return code.startswith(("000", "001", "002", "003", "300", "301", "302"))
    if market == "kcb": return code.startswith("688")
    if market == "cyb": return code.startswith(("300", "301", "302"))
    if market == "bj":  return code.startswith(("4", "8"))
    raise SystemExit(f"未知市场范围: {market}（可选 all/b/b-sh/b-sz/a/a-sh/a-sz/kcb/cyb/bj）")

def fin_batch(report_date):
    rows = dc_batch("RPT_F10_FINANCE_MAINFINADATA", "SECURITY_CODE,XSJLL,ZCFZL,MGJYXJJE",
                    f'(REPORT_TYPE="年报")(REPORT_DATE=\'{report_date}\')')
    return {r["SECURITY_CODE"]: r for r in rows}

def dividends_batch(since):
    rows = dc_batch("RPT_SHAREBONUS_DET", "SECURITY_CODE,REPORT_DATE,PRETAX_BONUS_RMB",
                    f"(NOTICE_DATE>='{since}')", sort="NOTICE_DATE", pause=0.15)
    out = {}
    for r in rows:
        fy = (r.get("REPORT_DATE") or "")[:4]
        try:
            per10 = float(r.get("PRETAX_BONUS_RMB") or 0)
        except (TypeError, ValueError):
            continue
        if fy and per10 > 0:
            out.setdefault(r["SECURITY_CODE"], {}).setdefault(fy, 0.0)
            out[r["SECURITY_CODE"]][fy] += per10
    return out  # {code: {FY: 每10股派息合计(元)}}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="b", help="all/b/b-sh/b-sz/a/a-sh/a-sz/kcb/cyb/bj")
    ap.add_argument("--board", default=None, help="行业板块名包含匹配（如 中药）")
    ap.add_argument("--min-jll", type=float, default=10.0, help="净利率%% ≥")
    ap.add_argument("--max-zcfzl", type=float, default=50.0, help="负债率%% ≤")
    ap.add_argument("--min-ocf", type=float, default=0.0, help="每股经营现金流(元) >")
    ap.add_argument("--min-dy", type=float, default=3.0, help="股息率%% ≥（目标财年分红合计/最新市值）")
    ap.add_argument("--years", default=None, help="逗号分隔财年，默认最近完整财年单年；多年则要求逐年达标（破线年→边缘）")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    today = datetime.date.today()
    y = today.year - 1 if today.month > 4 else today.year - 2
    years = a.years.split(",") if a.years else [str(y)]
    target_fy = years[-1]

    date = latest_trade_date()
    if a.market.startswith("b") and a.market != "bj":
        if a.board:
            print("WARN: B股无板块字段，--board 忽略", file=sys.stderr)
        uni = universe_b()
        date_note = "新浪实时价"
    else:
        uni = [r for r in universe(date)
               if in_scope(r["SECURITY_CODE"], a.market)
               and (a.board is None or a.board in (r.get("BOARD_NAME") or ""))]
        date_note = date
    if not uni:
        sys.exit(f"ERROR: 标的池为空（market={a.market} board={a.board}）")

    fin_by_year, misses = {}, []
    for yy in years:
        try:
            fin_by_year[yy] = fin_batch(f"{yy}-12-31")
        except Exception as e:
            misses.append(f"财务{yy}: {e}")
            fin_by_year[yy] = {}
        time.sleep(0.3)
    try:
        divs = dividends_batch(f"{target_fy}-01-01")
    except Exception as e:
        misses.append(f"分红: {e}")
        divs = {}

    passed, edge, rejected = [], [], []
    usd, hkd = fx_rates()
    for r in uni:
        code, name = r["SECURITY_CODE"], r["SECURITY_NAME_ABBR"]
        price = r.get("CLOSE_PRICE")
        price_cny = cny_price(code, price, usd, hkd) if price else None
        # 四维取值（净利率/负债率/现金流逐年；股息率目标财年）
        jll = {yy: (fin_by_year[yy].get(code) or {}).get("XSJLL") for yy in years}
        zfz = {yy: (fin_by_year[yy].get(code) or {}).get("ZCFZL") for yy in years}
        ocf = {yy: (fin_by_year[yy].get(code) or {}).get("MGJYXJJE") for yy in years}
        per10 = (divs.get(code) or {}).get(target_fy, 0.0)
        if not per10 and a.market.startswith("b") and a.market != "bj":
            time.sleep(0.15)
            per10 = div_sina_fy(code, target_fy)  # B股东财分红未覆盖 → 新浪 fallback
        # 股息率 = 目标财年每股派息(人民币) ÷ 人民币折算最新价（沪B美元×USDCNY、深B港币×HKDCNY）
        dy = round(per10 / 10 / price_cny * 100, 2) if price_cny and per10 else None
        # 逐年判定
        def year_ok(yy):
            j, z, o = jll[yy], zfz[yy], ocf[yy]
            return (j is not None and j >= a.min_jll and z is not None and z <= a.max_zcfzl
                    and o is not None and o > a.min_ocf)
        latest_ok = year_ok(target_fy)
        dy_ok = dy is not None and dy >= a.min_dy
        miss_data = [yy for yy in years if jll[yy] is None or zfz[yy] is None or ocf[yy] is None]
        row = {"name": name, "code": r["SECUCODE"], "ticker": code,
               "board": r.get("BOARD_NAME") or "",
               "jll": jll, "zfz": zfz, "ocf": ocf, "dy": dy,
               "price": price, "month_avg": None,
               "cap": round((r.get("TOTAL_MARKET_CAP") or 0) / 1e8, 1) or None,
               "pe": r.get("PE_TTM"), "pb": r.get("PB_MRQ")}
        if miss_data:
            rejected.append((row, f"数据缺失 {miss_data}"))
        elif latest_ok and dy_ok and all(year_ok(yy) for yy in years):
            passed.append(row)
        elif latest_ok and dy_ok:
            edge.append((row, [yy for yy in years if not year_ok(yy)]))
        else:
            why = []
            if not latest_ok:
                if jll[target_fy] is None or jll[target_fy] < a.min_jll: why.append(f"净利率{jll[target_fy]}")
                if zfz[target_fy] is None or zfz[target_fy] > a.max_zcfzl: why.append(f"负债率{zfz[target_fy]}")
                if ocf[target_fy] is None or ocf[target_fy] <= a.min_ocf: why.append(f"每股现金流{ocf[target_fy]}")
            if not dy_ok: why.append(f"股息率{dy}")
            rejected.append((row, "；".join(why)))

    # 价格观察字段不参与筛选；只为通过/边缘候选取月线，避免对全量剔除池逐只请求。
    candidates = passed + [r for r, _ in edge]
    if candidates:
        tickers = [r["ticker"] for r in candidates]
        try:
            if a.market.startswith("b") and a.market != "bj":
                month_avg = month_averages_b(tickers, date)
            else:
                month_avg = month_averages_a(tickers, date)
        except Exception as e:
            misses.append(f"候选月均价: {e}")
            month_avg = {}
        for r in candidates:
            r["month_avg"] = month_avg.get(r["ticker"])
        missing_avg = [r["ticker"] for r in candidates if r["month_avg"] is None]
        if missing_avg:
            sample = ",".join(missing_avg[:10])
            suffix = f"等{len(missing_avg)}只" if len(missing_avg) > 10 else ""
            misses.append(f"月均价缺失 {sample}{suffix}")

    def f3(d, unit=""):
        return " / ".join(f"{d[yy]:.2f}{unit}" if d.get(yy) is not None else "—" for yy in years)
    def fprice(r, key):
        value = r.get(key)
        if value is None:
            return "—"
        code = r["ticker"]
        if code.startswith("900"):
            return f"{value:.3f} USD"
        if code.startswith(("200", "201")):
            return f"{value:.3f} HKD"
        return f"{value:.2f} CNY"
    def line(r):
        return (f"| {r['name']} | {r['code']} | {r['board']} | {f3(r['jll'],'%')} | {f3(r['zfz'],'%')} "
                f"| {f3(r['ocf'])} | {r['dy'] if r['dy'] is not None else '—'}% "
                f"| {fprice(r, 'price')} | {fprice(r, 'month_avg')} | {r['cap'] or '—'} "
                f"| {round(r['pe'],1) if r['pe'] else '—'} | {round(r['pb'],2) if r['pb'] else '—'} |")
    H = "| 标的 | 代码 | 板块 | 净利率 | 负债率 | 每股经营现金流 | 股息率 | 当前股价 | 当月平均股价 | 市值(亿) | PE | PB |\n|---|---|---|---|---|---|---|---|---|---|---|---|"
    L = [f"# 快速筛选（{a.market}{'/'+a.board if a.board else ''}，财年 {'/'.join(years)}，行情日 {date_note}）",
         f"阈值：净利率≥{a.min_jll}% / 负债率≤{a.max_zcfzl}% / 每股经营现金流>{a.min_ocf}元 / 股息率≥{a.min_dy}%",
         f"扫描 {len(uni)} 只 → 通过 {len(passed)} + 边缘 {len(edge)} + 剔除 {len(rejected)}\n",
         "## 通过名单（候选池，供进一步研究）\n", H]
    L += [line(r) for r in sorted(passed, key=lambda x: -(x["dy"] or 0))]
    if edge:
        L += ["\n## 边缘名单（最新年达标、早年破线）\n", H + "\n| 破线财年 |"]
        L += [line(r) + f" {','.join(bad)} |" for r, bad in edge]
    L += ["\n## 剔除清单（含原因，供复查）\n", "| 标的 | 代码 | 原因 |", "|---|---|---|"]
    L += [f"| {r['name']} | {r['code']} | {why} |" for r, why in rejected[:200]]
    if len(rejected) > 200:
        L.append(f"| … | … | 其余 {len(rejected)-200} 只略 |")
    L += ["", f"口径注记：净利率/负债率/每股经营现金流=东财 F10 年报主指标；股息率=目标财年({target_fy})宣告每10股派息合计(人民币)÷10÷人民币折算最新价（沪B美元×USDCNY≈{usd:.4f}、深B港币×HKDCNY≈{hkd:.4f}，新浪即期；预案未除权亦计入；B股分红东财未覆盖者用新浪除息年归属口径）。当前股价=A股/北证为东财最新交易日收盘价、B股为新浪 hq 最新价；当月平均股价=该最新行情日所属自然月截至该日的日收盘价算术平均，A股/北证来自东财 RPT_VALUEANALYSIS_DET、B股来自新浪日线；价格列保留报价币种（A股/北证 CNY、沪B USD、深B HKD），不参与四维筛选。PE/PB=接口披露值搬运（B股无此字段）。"
          , "纪律：本清单为机械阈值过滤的候选池，不含任何贵贱/未来判断；深度研究衔接 financial-facts → lixinger-valuation → value-investing-gate。"]
    if misses:
        L.append(f"WARN: {'; '.join(misses)}")
    txt = "\n".join(L)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt)
        print(f"written: {a.out}")
    else:
        print(txt)

if __name__ == "__main__":
    main()
