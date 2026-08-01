# 数据契约与工具流（工程化）

门禁分析的工具链。目标：任何一只股票，按本文件流程 5 分钟内产出门禁结论 + 估值意见；也可循环 ticker 列表穷举某个系列。

## 目录

- 数据源路由
- 快照 schema
- 标准流程（单只）
- 批量穷举流程
- 已知数据陷阱

## 数据源路由

| 市场 | 基本面（三大报表） | 价格 | 工具 |
|---|---|---|---|
| A 股 / B 股（.SH/.SZ/.BJ） | iFinD（6+ 年） | iFinD | `tools/ifind_fetch.py` |
| 港股（.HK） | Yahoo Finance（iFinD 港股报表为陈旧数据，禁用） | Yahoo | `tools/yahoo_fetch.py` |
| 美股 / 新加坡（.US/.SI 等） | Yahoo Finance | Yahoo | `tools/yahoo_fetch.py` |
| 双重上市（如达仁堂 600329.SH + T14.SI） | 用覆盖最好的一个市场取基本面（同一公司报表相同） | 各市场分别取价，记入 `listings[]` | ifind 取基本面 + yahoo 补境外价格 |

汇率：价格币种 ≠ 报表币种时，用 Yahoo 外汇代码（`HKDCNY=X`、`USDCNY=X` 等）取最新价写入 `fx_to_report_currency`，并在 `listings[]` 每项标注 `currency`。dcf.py 只对标注币种与报表币种不同的上市项做折算。

## 快照 schema（data/snapshots/<ticker>.json）

```json
{
  "ticker": "600329.SH",           // 基本面所属市场的代码
  "name": "达仁堂",
  "currency": "CNY",               // 报表币种
  "industry": "",
  "source": "ifind | yahoo_finance",
  "as_of": "2026-08-01",
  "share_capital": 770158076,      // 总股本（股）
  "listings": [                     // 各上市地价格
    {"ticker": "600329.SH", "last_close": 38.29, "date": "...", "currency": "CNY"},
    {"ticker": "T14.SI", "last_close": 2.79, "date": "...", "currency": "USD"}
  ],
  "fx_to_report_currency": {"pair": "USDCNY=X", "rate": 6.75, "date": "..."},
  "years": {
    "2024": {
      "revenue": 0, "cogs": 0, "gross_margin": 0.0,
      "np_parent": 0, "equity_parent": 0, "roe": 0.0,
      "ocf": 0, "ocf_np": 0.0, "capex": 0, "capex_rev": 0.0,
      "total_assets": 0, "total_liab": 0, "debt_ratio": 0.0,
      "contract_liab": 0, "goodwill": 0, "da": 0,
      "owner_earnings": 0,          // np_parent + da - capex
      "distributions": 0,           // 分配股利利润或偿付利息（含利息，分红上界）
      "rev_growth": 0.0, "np_growth": 0.0
    }
  }
}
```

## 标准流程（单只）

```bash
# 1. 取数 → 快照（A/B股）
python tools/ifind_fetch.py 600329.SH --name 达仁堂 --years 2019-2024 --listings 600329.SH
#    或（HK/境外）
python tools/yahoo_fetch.py 0700.HK --name 腾讯控股 --fx HKDCNY=X

# 2. 第 0/1 道门 → reports/<ticker>_gate01.json
python tools/gate_screen.py 600329.SH          # 高杠杆行业加 --high-leverage

# 3. 第 2 道门（LLM 陪审团，按 references/qualitative-jury.md 执行，人工/agent 判断）

# 4. 第 3 道门 → reports/<ticker>_valuation.json（假设同步留档 data/assumptions/）
python tools/dcf.py 600329.SH --base avg3 --g1 0.05 --g2 0.03 --mos 0.65 --save-assumptions
#    一次性损益年份用 --base-override <元> 人工修正基准
```

## 批量穷举流程

```bash
for t in 600519.SH 000858.SZ 600329.SH; do
  python tools/ifind_fetch.py $t --years 2019-2024
  python tools/gate_screen.py $t
done
# 通过第 1 道门的标的，再逐个做第 2/3 道门。
# reports/*_valuation.json 中 verdict=="严重低估" 的即最终候选。
```

## 已知数据陷阱

- **iFinD 港股三大报表是陈旧数据**（约 2014 年），港股基本面一律走 Yahoo。
- Yahoo 年报只有最近 4 个财年；5 年 ROE 等检查会报 INSUFFICIENT，按规则视为不通过或标注数据不足。
- `distributions` 含偿付利息，是分红的上界；利息大的公司需人工复核。
- 归母净利润含一次性损益（出售股权、公允价值变动）时，估值基准必须 `--base-override` 人工修正（如达仁堂 2024、腾讯投资损益年份）。
- iFinD 同一 API 的 `statement=all` 只保留最后一张表，必须 bs/is/cf 分开取（ifind_fetch.py 已处理）。
- **跨期口径陷阱**：业务剥离 / 并购 / 口径重述年份（如达仁堂 2025 剥离医药商业板块、2024 年 11 月完成），禁止对口径变化年份直接同比营收 / 费用率 / 毛利率 / 净利率。先重建可比口径（同口径重述 / 剔除剥离板块）再对比，输出时必须标注口径标签（`含商业混合口径` / `纯工业口径`）。强判断词（失控 / 背离 / 恶化）必须绑定口径标签。详见 [anti-misjudgment.md](anti-misjudgment.md) M1。
- **计量单位陷阱**：销量 / 产量 / 库存的计量单位（盒 / 粒 / 吨 / 规格 / 包装）跨期变化时（如达仁堂 2025 速效救心丸 120 粒 → 200/240/300 粒规格调整），"包装单位"不等于"消费量"，必须换算统一单位后再做量价拆分，禁止直接同比盒数。详见 [anti-misjudgment.md](anti-misjudgment.md) M2。
- 双重上市折价：同股同权，B 股 / S 股 / H 股价格折让不改变公司内在价值，只改变买入入口——估值对公司、价格比价对上市地。
- **境外价格币种必须核对行情源**：B/S/H 股的计价货币（CNY/USD/HKD/SGD 等）禁止凭印象标注，必须从行情源（富途/老虎/交易所）确认。反例：达仁堂 S 股 T14.SI 计价货币为 **SGD**（2.79 新元），曾被误标为 USD——币种标错直接导致估值差 1.9 倍（SGD/CNY≈5.28 vs USD/CNY≈6.75 尚可，但换算基准错）。

## 工具链降级兜底流程（脚本不可用时的取数路径）

`tools/ifind_fetch.py` / `tools/yahoo_fetch.py` 依赖 iFinD / Yahoo 内部工具，且其脚本路径硬编码为 Windows（`H:\KimiData\...`），在 macOS 上不可用；Yahoo 公开 API 会因缺少 cookie/crumb 触发限流（`Too Many Requests`）。此时按以下顺序降级取数，**禁止直接引用早前分析结论替代**：

1. **价格**：权威行情页（富途 futunn.com、老虎 laohu8.com）web 抓取——含现价、币种、市值、PE、股本、52 周区间；汇率用央行中间价（chinamoney.com.cn）或 exchange-rates.org
2. **基本面**：web 搜索年报核心数据（营收/归母/扣非/经营现金流/分红/ROE/毛利率），交叉验证 ≥2 个独立来源（如东方财富 + 新浪研报点评），标注 `[Source: 来源, 日期]`
3. **口径**：剥离/一次性收益年份按 M1 处理，估值基准用扣非 + `--base-override` 逻辑（手工等价）
4. **落盘**：将手工快照写入 `data/snapshots/<ticker>.json`（含 listings 价格与币种），供 gate_screen.py / dcf.py 复用
