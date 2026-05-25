# NVDA 趋势入场信号评估

> 数据日期：2026-05-22 收盘
> 评估模版：`trend_entry.yaml`（宽松版）/ `trend_entry_strict.yaml`（严格版）

---

## 一、当前市场数据

| 指标 | 值 |
|------|-----|
| 收盘价 | $215.33 |
| MA20 | $214.75 |
| MA50 | $196.81 |
| MA200 | $187.02 |
| RSI14 | 53.7 |
| MA50 斜率 | 11.92° |
| MA200 斜率 | 14.04° |
| MA 间距比 | 4.55% |
| 成交量 | 168,346,300 |
| 20日均量 | 162,604,985 |
| 量比 | 1.04x |
| 距 MA50 偏离 | +8.60% |
| 横盘天数 | 0 天 |
| 日涨幅 | -1.90% |

---

## 二、逐条条件检查

| # | 条件 | 要求 | 当前值 | 结果 |
|---|------|------|--------|------|
| 1 | 多头排列 | Price > MA50 > MA200 | $215 > $197 > $187 | ✅ |
| 2 | MA50 斜率 | 5° ~ 45° | 11.92° | ✅ |
| 3 | MA200 斜率 | > 5° | 14.04° | ✅ |
| 4 | MA 间距 | < 5% | 4.55% | ✅ |
| 5 | 横盘突破 | 横走 ≥5天 + 突破区间高点 | 0天，无突破 | ❌ |
| 6 | 回撤反弹 | 距 MA50 ≤3% + 收阳反弹 | 偏离 8.6% | ❌ |
| 7 | 斐波那契时间 | 横盘天数匹配 Fib 数 ±2 | N/A | ❌ |
| 8 | 放量确认 | 量比 ≥ 1.3x | 1.04x | ❌ |
| 9 | RSI > 45 | > 45 | 53.7 | ✅ |

---

## 三、信号触发结论

| 模版 | 条件组合 | 满足 | 结果 |
|------|----------|------|------|
| `trend_entry.yaml` | c1 AND c2 AND c4 AND c8 AND c9 AND (c5 OR c6) | 4/6 | **不触发** |
| `trend_entry_strict.yaml` | c1 AND c2 AND c3 AND c4 AND ((c5 AND c7) OR c6) AND c8 AND c9 | 5/7 | **不触发** |

### 阻塞条件

1. **条件 5/6（形态缺失）**：价格当前 $215，远离 MA50($197) 约 8.6%。既不在横盘区间内，也不是刚回撤到 MA50 附近。不满足任何入场形态。

2. **条件 8（成交量平淡）**：当日 1.68 亿股，仅为 20 日均量的 1.04 倍。需要至少 2.11 亿股（1.3 倍均量）才算放量确认。

---

## 四、什么走势会触发信号？

### 场景 A：回撤至 MA50 后放量反弹（最可能，2-3 周）

> NVDA 从当前 $215 回调至 MA50 ±3% 区间（$191 ~ $203），在该区间停留后某日放量（≥2.11 亿股）收阳反弹站上 MA50。

这是典型的「趋势中回踩均线买入点」。触发条件 6（回撤反弹）+ 条件 8（放量）。

**触发价格区间：$191 ~ $203**
**所需成交量：≥ 211,386,480**

### 场景 B：横盘收敛后突破（需更久，4-5 周）

> NVDA 从 $215 缓慢回落至 MA50 附近，在 $191 ~ $203 区间横走 5-8 天形成平台，然后某日放量突破平台高点。

如果横盘天数恰好 5/8/13 天（±2 天容差），还能额外触发严格版的斐波那契条件。

**触发条件：横走 ≥5 天 + 突破日放量 ≥1.3x**

### 场景 C：急跌后 V 型反转（概率低，但可能 1-2 天内发生）

> 某日利空（如财报不及预期、AI 监管消息）导致 NVDA 急跌至 $197 附近，次日即刻放量反弹。

一天内同时满足条件 6（回撤）+ 条件 8（放量），瞬间触发信号。

---

## 五、概率估计

| 时间窗口 | 宽松版触发概率 | 严格版触发概率 | 关键假设 |
|----------|--------------|--------------|---------|
| 本周内 | ~5% | ~2% | 需急跌 V 反 |
| 2 周内 | ~20% | ~10% | 温和回调至 MA50 |
| 1 个月内 | ~45% | ~25% | 正常的趋势回调周期 |
| 2 个月内 | ~65% | ~40% | 几乎必然有一次回调 |

### 关键变量

- **NVDA 是否会回调到 MA50**：当前价格高出 MA50 约 9%。如果继续强势上涨不回撤，信号永远不会触发。这正是趋势跟踪策略的设计意图：**不追高，只在回调确认后入场**。

- **MA50 的追赶速度**：即使价格不跌，MA50 每天在上升（当前斜率 11.9°）。如果 NVDA 在 $210-$220 横走 2-3 周，MA50 会逐渐追上来，可能形成条件 5 的横盘形态。

- **催化剂**：财报（NVDA 通常 5月/8月/11月）、AI 政策新闻、产品发布等事件可能带来放量，同时满足条件 8。

---

## 六、与均值回复策略对比

| 维度 | dip_buy（当前可能触发） | trend_entry（当前不触发） |
|------|----------------------|------------------------|
| 入场逻辑 | RSI<35 或 5日跌>5% | 多头排列 + 形态确认 + 放量 |
| 当前状态 | RSI=53.7，不满足 | 形态不成立 |
| 等待什么 | 急跌到超卖 | 回调到 MA50 后的重新启动 |
| 风险 | 接飞刀（可能越跌越买） | 错过行情（可能一直涨不回调） |

**结论**：当前 NVDA 处于"趋势中但偏离均线"的状态，两种策略都不会触发。这恰好说明系统运作正常 — 不在不确定的位置开仓。

---

## 七、数据获取与计算流程

### 数据源配置

```yaml
# config/ibkr.yaml
ibkr:
  market_data_source: "yfinance"   # 当前使用免费数据源
```

可选值：
- `"yfinance"` — 免费，适合 Paper 测试（当前使用）
- `"ibkr"` — IBKR 市场数据订阅，实盘需要
- `"auto"` — 优先 IBKR，失败自动回退 yfinance

### 完整数据流

```
StrategyFactory.analyze(target_symbols={"NVDA"})
    │
    ▼
_fetch_market_data({"NVDA"})
    │
    ▼
MarketDataProvider(client, data_source="yfinance")
    │
    ├── fetch_basic(["NVDA"])              ← 实时价格 + 成交量
    │     └── yf.Ticker("NVDA").history(period="1d", interval="1m")
    │           → MarketData(price=215.33, volume=168346300)
    │
    └── enrich(market_data_list)           ← 技术指标批量计算
          │
          └── fetch_historical("NVDA", days=220)    ← 获取220天日K线
                └── yf.download("NVDA", period="220d", interval="1d")
                      → 251 根 Bar(time, open, high, low, close, volume)
                            │
                            ▼
                compute_indicators(bars)    ← 全部指标本地计算
                      │
                      ├── MA50   = avg(close[-50:])   → $196.81
                      ├── MA200  = avg(close[-200:])  → $187.02
                      ├── ma_50_slope  = 线性回归(最近10根MA50值) → 11.92°
                      ├── ma_200_slope = 线性回归(最近10根MA200值) → 14.04°
                      ├── ma_spread_ratio = (MA50-MA200)/price → 0.0455
                      ├── is_consolidating = 检查最近N天是否在MA50±3% → False
                      ├── consolidation_days → 0
                      ├── breakout_detected → False
                      ├── volume_ratio = 今日量/20日均量 → 1.04
                      ├── retrace_to_ma50 = |price-MA50|/price ≤ 3% → False
                      ├── rsi_14 → 53.7
                      └── change_1d_pct → -1.90%
```

### 关键计算公式

**$191~$203 区间的来源：**

```python
MA50 = $196.81
回撤区间 = [MA50 × 0.97, MA50 × 1.03]
         = [$196.81 × 0.97, $196.81 × 1.03]
         = [$190.90, $202.71]
```

代码位置：`src/core/market_data.py` → `compute_indicators()`

```python
# retrace_to_ma50 检测
deviation_pct = abs(closes[-1] - ma_50) / closes[-1] * 100
retrace_to_ma50 = deviation_pct <= 3.0   # 3% 阈值硬编码
```

**放量阈值 2.11 亿的来源：**

```python
volume_avg_20d = 162,604,985
放量阈值 = volume_avg_20d × 1.3 = 211,386,480
```

代码位置：`src/core/conditions/volume_spike.py`

```python
# volume_spike evaluator
mult = node.multiplier or 2.0   # YAML 中配置 multiplier: 1.3
return md.volume_ratio >= mult
```

**MA50 斜率 11.92° 的来源：**

```python
# 取最近10根MA50值做线性回归
sma_values = [最近10天每天的MA50]
slope = linear_regression_slope(sma_values)
normalized_slope = slope / sma_values[-1] * 50  # 除以价格水平，乘以周期
angle = atan(normalized_slope) × 180° / π = 11.92°
```

代码位置：`src/core/market_data.py` → `compute_sma_slope(closes, period=50, lookback=10)`

### 为什么取 220 天历史？

```
SMA200 需要 200 根收盘价
斜率计算需要额外 10 根回望 (lookback)
合计最少需要 210 根
取 220 天留余量（交易日 vs 日历日差异、节假日等）
```

### Watch Daemon 调用周期

```yaml
watch:
  poll_interval: 5                    # 每5秒轮询一次
  indicator_refresh_minutes: 30       # 指标每30分钟刷新
```

即：实时价格每 5 秒更新，但历史 K 线和技术指标每 30 分钟重新计算一次（避免频繁调用 yfinance API）。
