# trend_entry 策略触发概率评估

**评估时间**: 2026-05-26 19:05 ET  
**数据源**: yfinance (1年日线)  
**策略条件**: ma_stack + sma_slope(>5%) + ma_spread(<0.05) + volume_spike(>1.3x) + RSI(>45) + (consolidation_breakout OR retrace_breakout)

---

## 各标的评估

### BCE — 67% 🟡 中概率（最可能触发）

| 条件 | 结果 | 数值 |
|:----|:----:|:----:|
| ma_stack Price>MA50>MA200 | ✅ | $24.80 > $24.43 > $23.85 |
| sma_slope MA50 > 5% | ❌ | -0.04% |
| ma_spread < 0.05 | ✅ | 0.0243 |
| volume_spike > 1.3x | ❌ | 0.92x |
| RSI > 45 | ✅ | 62.0 |
| consolidation_breakout | ✅ | range=5.76% |
| retrace_breakout | ✅ | dip=3.31% |

**评价**: MA排列完美，横盘整理+回撤突破形态均已满足。**缺量能放大**和**MA50陡峭度**。一旦放量即触发。

### AAPL — 50% 🟡 中概率

| 条件 | 结果 | 数值 |
|:----|:----:|:----:|
| ma_stack Price>MA50>MA200 | ✅ | $308.33 > $271.53 > $261.53 |
| sma_slope MA50 > 5% | ❌ | 0.43% |
| ma_spread < 0.05 | ✅ | 0.0382 |
| volume_spike > 1.3x | ❌ | 0.96x |
| RSI > 45 | ✅ | 87.7 |
| consolidation_breakout | ❌ | range=15.51% |
| retrace_breakout | ❌ | dip=3.19% |

**评价**: MA排列健康，但RSI=87.7已进入超买区，横盘振幅太大(15.5%)无法满足突破条件。需等待回调后再突破。

### COST — 33% 🟢 低概率

| 条件 | 结果 | 数值 |
|:----|:----:|:----:|
| ma_stack Price>MA50>MA200 | ❌ | $1002.93 < $1006.93 |
| sma_slope MA50 > 5% | ❌ | -0.01% |
| ma_spread < 0.05 | ❌ | 0.0543 |
| volume_spike > 1.3x | ✅ | 1.35x |
| RSI > 45 | ✅ | 47.1 |
| consolidation_breakout | ❌ | range=10.25% |
| retrace_breakout | ❌ | dip=6.04% |

### BAC — 33% 🟢 低概率

| 条件 | 结果 | 数值 |
|:----|:----:|:----:|
| ma_stack Price>MA50>MA200 | ❌ | MA50 < MA200（接近死亡交叉） |
| sma_slope MA50 > 5% | ❌ | 0.22% |
| ma_spread < 0.05 | ✅ | 0.0094 |
| volume_spike > 1.3x | ❌ | 0.59x |
| RSI > 45 | ❌ | 43.6 |
| consolidation_breakout | ❌ | range=7.70% |
| retrace_breakout | ✅ | dip=5.41% |

### NVDA — 33% 🟢 低概率

| 条件 | 结果 | 数值 |
|:----|:----:|:----:|
| ma_stack Price>MA50>MA200 | ✅ | $214.86 > $197.50 > $187.19 |
| sma_slope MA50 > 5% | ❌ | 0.35% |
| ma_spread < 0.05 | ❌ | 0.0551 |
| volume_spike > 1.3x | ❌ | 1.12x |
| RSI > 45 | ✅ | 63.9 |
| consolidation_breakout | ❌ | range=19.97% |
| retrace_breakout | ❌ | dip=8.66% |

### BA — 33% 🟢 低概率

| 条件 | 结果 | 数值 |
|:----|:----:|:----:|
| ma_stack Price>MA50>MA200 | ❌ | MA50 < MA200（空头排列） |
| sma_slope MA50 > 5% | ❌ | 0.08% |
| ma_spread < 0.05 | ✅ | 0.0061 |
| volume_spike > 1.3x | ❌ | 0.87x |
| RSI > 45 | ✅ | 45.0 |
| consolidation_breakout | ❌ | range=11.90% |
| retrace_breakout | ❌ | dip=10.64% |

---

## 总结

| 标的 | 概率 | 状态 |
|:----|:----:|:-----|
| **BCE** | **67%** 🟡 | 最接近触发，只需放量 |
| AAPL | 50% 🟡 | MA好但超买+无突破形态 |
| COST | 33% 🟢 | MA排列破坏 |
| BAC | 33% 🟢 | 接近死亡交叉 |
| NVDA | 33% 🟢 | 价差过大 |
| BA | 33% 🟢 | 空头排列 |

**核心瓶颈**: 近一月横盘震荡导致均线走平，所有标的均卡在 `sma_slope > 5%` 和突破形态两个条件。**BCE 是短期内最可能触发 trend_entry 信号的标的。**

---

## AI/芯片/算力板块评估

**评估时间**: 2026-05-26 19:05 ET  
**数据源**: yfinance (1年日线)  
**注意**: 使用实际代码逻辑重新计算（ma_spread=`(MA50-MA200)/price`，sma_slope=`线性回归→atan→角度°`，consolidation=`价格在MA50±3%内连续≥5天`，retrace=`最新价在MA50±3%内`）

### 各标的评分

| 标的 | 现价 | spread | slope° | 得分 | stack | slope | spread | vol | RSI | breakout |
|:----|:----:|:------:|:------:|:----:|:----:|:-----:|:------:|:---:|:---:|:--------:|
| **CRDO** | $221.64 | 3.73% | 25.1° | **5/6 ⚠️** | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **NVDA** | $214.86 | 4.80% | 11.5° | **4/6 🟡** | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| MRVL | $208.26 | 20.84% | 34.5° | 4/6 🟡 | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| MU | $895.88 | 22.55% | 33.1° | 4/6 🟡 | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| ASML | $1632.03 | 16.02% | 7.3° | 4/6 🟡 | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ |
| AMD | $503.89 | 15.25% | 38.0° | 3/6 🟡 | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| ARM | $321.22 | 11.88% | 34.9° | 3/6 🟡 | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| TSM | $412.32 | 13.81% | 8.5° | 3/6 🟡 | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| AMAT | $454.89 | 22.62% | 12.2° | 3/6 🟡 | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| KLAC | $2011.39 | 18.48% | 13.2° | 3/6 🟡 | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| ANET | $158.01 | 4.85% | 3.5° | 3/6 🟡 | ✅ | ❌ | ✅ | ❌ | ✅ | ✅ |
| AVGO | $422.01 | 6.98% | 12.8° | 3/6 🟡 | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| QCOM | $248.82 | -0.39% | 24.9° | 3/6 🟡 | ❌ | ✅ | ✅ | ❌ | ✅ | ❌ |

### 关键发现

**CRDO（美股光互联龙头）得分 5/6，唯一接近触发的AI标的。** 仅缺突破形态——但原因不是形态未形成，而是它的价格远高于MA50（+45%），无法满足「价格在MA50±3%内」的 consolidation/retrace 条件。这意味着它**趋势太强了以至于触发不了这个模版的入场信号。**

**NVDA 得分 4/6**，spread=4.80% 实际通过条件（代码用 `(MA50-MA200)/price` 而非 `/MA200`），MA50斜率11.5°也在范围内。缺量能放大和突破形态。

**ASML 和 ANET** 是唯二通过 breakout 条件的AI股，但各自缺其他条件（ASML缺量能+spread，ANET缺斜率）。

### 矛盾分析

当前 `trend_entry` 的条件组合存在设计矛盾：

| 条件 | 意图 | 与强势股的冲突 |
|:-----|:-----|:--------------|
| `ma_stack` Price > MA50 > MA200 | 确认上升趋势 | ✅ 强势股完全满足 |
| `sma_slope` 5-45° | 均线陡峭度适中 | ✅ AI股普遍10-38°，通过 |
| **`ma_spread` < 0.05** | MA间距窄（早期趋势） | ❌ **强势股MA50远超MA200**，spread=7-23% |
| **`consolidation_breakout`** | 横盘整理后突破 | ❌ **强势股价格远在MA50之上**（+30-50%），从不整理 |
| **`retrace_breakout`** | 回撤MA50后反弹 | ❌ **强势股不回撤到MA50**（偏离>20%），条件永不满足 |

`ma_spread` 和 `consolidation/retrace` 两个条件本质上要求**价格靠近MA50、MA50靠近MA200**，这与AI强势股「价格远高于MA50、MA50远高于MA200」的现实相悖。

---

## 参数调整建议

### 方案A：为强势趋势股增加 OR 分支（推荐）

在原有 `AND` 逻辑下新增第二个入场路径，专门处理强势趋势股的继续上涨形态：

```yaml
conditions:
  operator: AND
  rules:
    # 路径一：早期趋势入场（保留现有逻辑）
    - operator: OR
      rules:
        - operator: AND
          rules:
            - type: ma_stack
              operator: ">"
            - type: sma_slope
              period: 50
              threshold: 5
              multiplier: 45
              operator: ">"
            - type: ma_spread
              operator: "<"
              threshold: 0.05
            - type: volume_spike
              multiplier: 1.3
            - type: rsi
              operator: ">"
              threshold: 45
            - operator: OR
              rules:
                - type: consolidation_breakout
                - type: retrace_breakout
        # 路径二：强势趋势延续入场（新增）
        - operator: AND
          rules:
            - type: ma_stack
              operator: ">"
            - type: sma_slope
              period: 50
              threshold: 5
              multiplier: 45
              operator: ">"
            - type: volume_spike
              multiplier: 1.3
            - type: rsi
              operator: ">"
              threshold: 45
            - type: close_above_ma20  # 价格在MA20之上（短期强势）
```

**优点**: 保留对早期趋势的严格筛选，同时覆盖已处于强势趋势的标的。  
**缺点**: 需要新增 `close_above_ma20` 条件实现。

### 方案B：仅调整已有参数（最简单）

不改代码，只调 YAML 参数：

| 参数 | 当前值 | 建议值 | 理由 |
|:-----|:------:|:------:|:-----|
| `ma_spread` threshold | 0.05 | **0.08** | 允许MA50高出MA20达8%（如AVGO 6.98%即可通过） |
| `sma_slope` multiplier | 45 | **60** | 允许更陡的均线斜率（如AMD 38°已接近上限） |

**效果**: 放宽后 CRDO(3.73%)、NVDA(4.80%)、ANET(4.85%)、AVGO(6.98%) 通过 spread；但 **consolidation/retrace 条件仍是硬瓶颈**，不改代码无法绕过。

### 方案C：修改回撤阈值（需改代码，推荐）

修改 `market_data.py` 第291行的 `deviation_pct <= 3.0`，将 retrace 阈值从 3% 放宽：

```python
# 当前（硬编码 3%）
retrace_to_ma50 = deviation_pct <= 3.0

# 改为读取 YAML 参数或提高到 8-10%
# 散户线 8%: 允许价格在MA50±8%内视为"回撤"
threshold = getattr(node, 'threshold', 8.0)
retrace_to_ma50 = deviation_pct <= threshold
```

同时将 `retrace_breakout` evaluator 的 YAML 参数打通，使其能接收 `threshold` 参数。

**优点**: 保留严格趋势筛选的同时，允许强势股的回调入场。  
**效果**: CRDO（偏离45%）仍不满足，但类似 ANET（偏离1.5%）这类正常回撤的标的可通过。

### 方案D：针对AI板块创建独立模板

复制 trend_entry.yaml 为 trend_entry_ai.yaml，单独配置 AI 板块的宽松参数：

```yaml
# strategy/templates/trend_entry_ai.yaml
conditions:
  operator: AND
  rules:
    - type: ma_stack
      operator: ">"
    - type: sma_slope
      period: 50
      threshold: 5
      multiplier: 60        # 放松到60°
      operator: ">"
    - type: ma_spread
      operator: "<"
      threshold: 0.25       # 放松到25%（覆盖大多数AI股）
    - type: rsi
      operator: ">"
      threshold: 45
    - type: volume_spike
      multiplier: 1.3
```

**效果**: 不需要复杂的 OR 逻辑，独立调参。**CRDO 5/6 → 6/6 通过**，NVDA 4/6 → 5/6 仍卡量能。

---

## 全市场扫描（2026-05-26 收盘后）

**扫描范围**: 350+ 只股票（含标普500、AI板块、防御板块、金融、能源等）  
**数据源**: yfinance (1年日线)

### 结果：0/350+ 完全匹配 6/6

当前市况下没有一只股票能同时满足全部 6 个条件。

### 最接近的候选

| 标的 | 得分 | 卡在 | 现价 | MA50 | MA200 | spread | slope° | vol/x | RSI | breakout |
|:----|:----:|:----|-----:|-----:|------:|:------:|:------:|:-----:|:---:|:--------:|
| **ALLY** | **5/6** | 量能 0.86x | $42.74 | $41.61 | $40.91 | 1.63% ✅ | 5.7° ✅ | 0.86x ❌ | 47.6 ✅ | RETRACE ✅ |
| **CRDO** | **5/6** | 突破形态 | $221.64 | $151.92 | $143.65 | 3.73% ✅ | 25.1° ✅ | 1.51x ✅ | 59.0 ✅ | NONE ❌ |

### ALLY 详细评估

| 条件 | 结果 | 数值 |
|:----|:----:|:----:|
| ma_stack Price>MA50>MA200 | ✅ | $42.74 > $41.61 > $40.91 |
| sma_slope MA50 > 5° | ✅ | 5.7° |
| ma_spread < 5% | ✅ | 1.63% |
| **volume_spike > 1.3x** | **❌** | **0.86x** |
| RSI > 45 | ✅ | 47.6 |
| retrace_breakout | ✅ | 距MA50 2.65%，高于MA50，动量向上 |

**评价**: retrace 条件已触发（价格在MA50±3%内且反弹中），6个条件只差放量。一旦某天成交量 > 1.3x 均值（约 363 万股），**立即触发信号**。

最新价距MA50仅 2.65%（$42.74 vs $41.61），52周高 $46.41，目前距高点-7.9%。5/19 曾下探至距MA50仅 0.66%，随后反弹——典型的回踩MA50后弹起的形态。

### CRDO 详细评估

| 条件 | 结果 | 数值 |
|:----|:----:|:----:|
| ma_stack Price>MA50>MA200 | ✅ | $221.64 > $151.92 > $143.65 |
| sma_slope MA50 > 5° | ✅ | 25.1° |
| ma_spread < 5% | ✅ | 3.73% |
| volume_spike > 1.3x | ✅ | 1.51x |
| RSI > 45 | ✅ | 59.0 |
| **breakout** | **❌** | **距MA50 +45%，无法满足consolidation/retrace** |

**评价**: 趋势过强导致触发不了入场信号。价格 $221 远高于 MA50 $152（偏离+45%），consolidation（需在MA50±3%内震荡≥5天）和 retrace（需在MA50±3%内）均不可能。需约 40% 的深调至 ~$155 才能满足条件。

### 瓶颈总结

该扫描印证了之前的判断：

1. **`ma_spread < 5%`** 过滤了约 82% 的候选——多数强势股的 MA50 已远高于 MA200。
2. **`consolidation/retrace (±3%)`** 过滤了 100% 的最终候选——能同时满足 spread 和 stack 的股票（如 ALLY 这类慢涨股）往往也没有量能放大。
3. **6/6 的 OR 组合（consolidation OR retrace）并不意味着容易通过**，两者本质上都要求价格靠近 MA50，与「stack=Price>MA50>MA200」这一趋势确认条件存在隐含矛盾。

### 扩展扫描部分的补充标的（未突破筛选但仍值得关注）

以下标的正在 MA50 附近整理但未被 count 为通过，原因补充：

| 标的 | 距MA50 | 卡在条件 |
|:----|:-----:|:---------|
| ASML | +2.1% (retrace ✅) | spread 16.02% ❌ / volume 0.91x ❌ |
| ANET | +1.5% (retrace ✅) | slope 3.5° ❌ / volume 1.13x ❌ / spread 4.85% ❌ |
| BCE | +1.0% (retrace ✅) | slope -0.04° ❌ / volume 0.92x ❌ |
| CPT | -3.9%（空头排列）| stack ❌ / volume 1.43x ✅ |

这些标的都有各自的突破形态但缺少组合条件中的其他环节，说明 `trend_entry` 的门槛在 5-6 个条件叠加下确实极高。
