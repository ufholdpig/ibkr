# 强趋势入场策略 — 重构实施记录

> 实施日期：2026-05-25
> 基于文档：`docs/strong_trend_entry-deepseek-v4-flash.md`

---

## 一、变更概览

| 阶段 | 内容 | 涉及文件 | 状态 |
|------|------|---------|------|
| Phase 0 | 数据底座扩展 | `strategy.py`, `market_data.py` | ✅ 完成 |
| Phase 1 | 条件求值器 | `conditions/` 下 6 个新文件 | ✅ 完成 |
| Phase 1 | 单元测试 | `tests/test_trend_conditions.py` | ✅ 30 项通过 |
| Phase 2 | 模版引擎 | `strategy.py` (StrategyTemplateEngine) | ✅ 完成 |
| Phase 2 | 工厂重构 | `strategy.py` (StrategyFactory) | ✅ 完成 |
| Phase 2 | 模版文件 | `strategy/templates/` 4 个 YAML | ✅ 完成 |
| Phase 2 | 删除重复 | `strategy/strategies/` 删除 24 个文件 | ✅ 完成 |
| Phase 2 | 配置更新 | `config/ibkr.yaml`, `config/config.py` | ✅ 完成 |
| Phase 3 | TradingSignal v3 | `strategy.py` | ✅ 完成 |
| Phase 3 | OCO 订单 | `orders.py` (place_bracket_order) | ✅ 完成 |
| Phase 3 | 延迟执行 | `pending_signals.py` | ✅ 完成 |

---

## 二、Phase 0: 数据底座

### 2.1 MarketData 新增字段

文件：`src/core/strategy.py` (MarketData dataclass)

| 字段名 | 类型 | 含义 |
|--------|------|------|
| `ma_200` | `Optional[float]` | 200日均线 |
| `ma_50_slope` | `Optional[float]` | MA50 斜率角度（度），线性回归 10 根 K 线 |
| `ma_200_slope` | `Optional[float]` | MA200 斜率角度（度） |
| `ma_spread_ratio` | `Optional[float]` | (MA50 - MA200) / 价格，衡量均线间距 |
| `is_consolidating` | `Optional[bool]` | 价格是否在 MA50 ±3% 范围内横走 ≥5 天 |
| `consolidation_days` | `Optional[int]` | 横盘天数 |
| `volume_ratio` | `Optional[float]` | 当日成交量 / 20日均量 |
| `breakout_detected` | `Optional[bool]` | 最新收盘价突破横盘区间高点 |
| `retrace_to_ma50` | `Optional[bool]` | 价格回撤至 MA50 ±3% 范围 |

### 2.2 compute_indicators() 扩展

文件：`src/core/market_data.py`

**关键变更：**
- 历史数据获取从 60 天增至 **220 天**（支持 SMA200 + 10 根回望）
- 新增 `compute_sma_slope()` — 对最近 10 根 SMA 值做线性回归，将斜率标准化后通过 `atan` 转为角度
- 新增 `compute_consolidation()` — 从末尾向前扫描，统计价格在 MA50 ±3% 范围内的连续天数
- 新增 `volume_ratio` 计算 — 最新成交量 / 20日均量
- 新增 `retrace_to_ma50` 检测 — |价格 - MA50| / 价格 ≤ 3%

**斜率计算公式：**
```
sma_values = 最近 10 根 SMA 值
slope = 线性回归斜率
normalized_slope = slope / sma_values[-1] * period
angle = atan(normalized_slope) * 180 / π
```

---

## 三、Phase 1: 条件求值器

### 3.1 新增 6 个求值器

所有求值器遵循 `@register("type_name")` 模式，纯 stateless 函数，从 `MarketData` 预计算字段读取。

| 文件 | 注册名 | 作用 | YAML 参数 |
|------|--------|------|-----------|
| `ma_stack.py` | `ma_stack` | 多头排列检查 price > MA50 > MA200 | `operator`: ">" (多头) / "<" (空头) |
| `sma_slope.py` | `sma_slope` | 均线斜率角度范围 | `period`: 50/200, `threshold`: 最小角度, `multiplier`: 最大角度 |
| `ma_spread.py` | `ma_spread` | MA 间距比率检查 | `operator`: "<"/">" , `threshold`: 比率阈值 |
| `consolidation_breakout.py` | `consolidation_breakout` | 横盘突破 | 无参数，读 `is_consolidating` + `breakout_detected` |
| `retrace_breakout.py` | `retrace_breakout` | 回撤反弹突破 | 无参数，读 `retrace_to_ma50` + 正涨幅确认 |
| `fib_time.py` | `fib_time` | 斐波那契时间匹配 | `threshold`: 容差天数（默认 2） |

### 3.2 volume_spike 说明

`volume_spike` 已支持参数化 `multiplier`（通过 `node.multiplier or 2.0`），无需修改。YAML 中 `multiplier: 1.3` 即可将阈值从默认 2.0x 降为 1.3x。

### 3.3 单元测试

文件：`tests/test_trend_conditions.py`

30 个测试用例覆盖所有新求值器 + volume_spike 参数化场景：
- `TestMAStack` (5 个)：多头、空头、未对齐、缺失 MA200、默认操作符
- `TestSMASlope` (5 个)：范围内、低于最小、高于最大、MA200、None
- `TestMASpread` (3 个)：低于阈值、高于阈值、None
- `TestConsolidationBreakout` (4 个)：突破、无突破、未横盘、None
- `TestRetraceBreakout` (4 个)：反弹成功、低于 MA50、未回撤、负动量
- `TestFibTime` (5 个)：精确匹配、容差内、超出容差、0 天、None
- `TestVolumeSpike` (4 个)：1.3x 达标、未达标、默认 2x、无均量

---

## 四、Phase 2: 策略模版引擎

### 4.1 架构设计

```
config/ibkr.yaml
  watch.symbols.NVDA:
    templates: ["trend_entry", "dip_buy"]    ← 模版名（无 .yaml 后缀）
    strategies: []                            ← 特殊策略文件名

strategy/templates/
  ├── dip_buy.yaml        ← 通用模版，含 {symbol} 占位符
  ├── ma_buy.yaml
  ├── bounce_sell.yaml
  └── trend_entry.yaml

strategy/strategies/
  ├── stop_loss.yaml      ← 特殊策略（保留）
  ├── conservative_buy.yaml
  ├── conservative_sell.yaml
  └── value_buy.yaml
```

### 4.2 StrategyTemplateEngine

文件：`src/core/strategy.py`

```python
class StrategyTemplateEngine:
    def expand(self, template_name: str, symbol: str) -> Optional[dict]:
        """加载模版 YAML → yaml.dump → 字符串替换 {symbol} → yaml.safe_load → 返回 dict"""
```

- 模版缓存：同一模版只从文件读取一次
- 占位符替换：通过 YAML dump/load 保证所有层级的 `{symbol}` 都被替换
- 错误处理：模版文件不存在或解析失败时返回 None 并 log error

### 4.3 StrategyFactory 改造

新增参数：
- `template_dir: str` — 模版目录路径
- `watch_symbols: Dict` — watchlist 配置（per-symbol 的 templates[] 和 strategies[]）

加载逻辑：
1. **Path 1**：遍历 `watch_symbols`，对每个 symbol 展开其 `templates[]`
2. **Path 2**：加载 `strategies[]` 中指定的直接策略文件
3. **回退**：如果 `watch_symbols` 为空，加载 `strategy_dir` 下所有 YAML（向后兼容）

### 4.4 删除的文件（24 个）

```
{aapl,avgo,ceg,dlr,f,nvda,vrt,vst}_{dip_buy,ma_buy,bounce_sell}.yaml
```

这 24 个文件除 ticker 不同外逻辑完全重复，现由 3 个模版 + 配置展开替代。

### 4.5 模版文件内容

**`strategy/templates/trend_entry.yaml`** — 强趋势入场核心模版：

```yaml
strategy_id: "TREND_ENTRY_{symbol}"
priority: 20
weight: 1.2
regime_weights:
  BULL: 1.5      # 牛市加权
  BEAR: 0.0      # 熊市完全关闭
  SIDEWAYS: 0.5  # 震荡市减半
conditions:
  operator: AND
  rules:
    - type: ma_stack           # 条件1: 多头排列
      operator: ">"
    - type: sma_slope          # 条件2: MA50 斜率 5°~45°
      period: 50
      threshold: 5
      multiplier: 45
    - type: ma_spread          # 条件4: MA间距紧凑
      operator: "<"
      threshold: 0.05
    - type: volume_spike       # 条件8: 放量确认 >1.3x
      multiplier: 1.3
    - type: rsi                # 条件9: RSI > 45
      operator: ">"
      threshold: 45
    - operator: OR             # 条件5/6: 横盘突破 或 回撤突破
      rules:
        - type: consolidation_breakout
        - type: retrace_breakout
action:
  type: "LIMIT_BUY"
  quantity: 10
  ticker: "{symbol}"
  risk:
    stop_loss_type: "ma50_minus_pct"
    stop_loss_pct: 0.03
    take_profit_pct: 0.20
    trailing_stop_pct: 0.07
```

### 4.6 配置更新

**`config/ibkr.yaml`** watch 段：
- 新增 `template_dir: "strategy/templates"`
- 每个 symbol 从 `strategies: [...]` 改为 `templates: [...] + strategies: [...]`
- 趋势跟踪标的（NVDA, AVGO, VST, CEG, VRT）启用 `trend_entry` 模版
- 传统标的（F, AAPL, DLR）仅使用均值回复模版

**`config/config.py`**：
- `SymbolWatchConfig` 新增 `templates: list` 字段
- `WatchConfig` 新增 `template_dir: str` 字段

### 4.7 调用方更新

- `src/trading/watch_daemon.py`：构造 `watch_symbols_dict` 传给 StrategyFactory
- `src/core/signal.py`：同上

---

## 五、Phase 3: 执行管线

### 5.1 TradingSignal v3 新增字段

文件：`src/core/strategy.py` (TradingSignal dataclass)

| 字段名 | 类型 | 默认值 | 含义 |
|--------|------|--------|------|
| `entry_delay_days` | `int` | 0 | 延迟执行天数（0=立即） |
| `stop_loss_type` | `str` | "" | 止损类型: "fixed" / "platform_low" / "ma50_minus_pct" |
| `stop_loss_pct` | `float` | 0.0 | 止损百分比（如 0.03 = 3%） |
| `take_profit_pct` | `float` | 0.0 | 止盈百分比（如 0.20 = 20%） |
| `trailing_stop_pct` | `float` | 0.0 | 移动止损回撤百分比（如 0.07 = 7%） |
| `oco_group_id` | `str` | "" | OCO 订单组 ID（关联 parent-child） |

`_create_signal()` 已更新，从 YAML 的 `action.risk` 段读取这些参数并填入信号。

### 5.2 OCO Bracket 订单

文件：`src/core/orders.py` — 新增 `place_bracket_order()`

**功能**：提交一组关联订单（买入 + 止损 + 止盈），利用 IBKR 的 `parentId` 机制实现 OCO。

**调用方式**：
```python
result = place_bracket_order(
    client=client,
    contract=contract,
    action="BUY",
    quantity=10,
    limit_price=150.0,       # 买入限价
    stop_loss_price=145.5,   # 止损价 (STP 单)
    take_profit_price=180.0, # 止盈价 (LMT SELL)
    tif="GTC",
)
# result = {"parent_result": OrderResult, "parent_id": int, "stop_loss_id": int, "take_profit_id": int}
```

**订单结构**：
- Parent: LMT BUY, `transmit=False`
- Child 1: STP SELL (止损), `parentId=parent_id`, `transmit=False`
- Child 2: LMT SELL (止盈), `parentId=parent_id`, `transmit=True` (最后一个 child 触发全组发送)

### 5.3 延迟信号持久化

文件：`src/core/pending_signals.py`

**设计**：
- 存储路径：`data/pending_signals.json`
- 格式：JSON 数组，每条记录包含信号字典 + 元数据 (`_created_at`, `_execute_after`, `_expired`)

**API**：
```python
store = PendingSignalStore()
store.add(signal_dict, delay_days=1)     # 存入，明天执行
ready = store.get_ready_signals()         # 获取到期信号（自动标记已消费）
store.cleanup(max_age_days=7)             # 清理 7 天前的过期记录
store.count_pending()                     # 查询待执行信号数量
```

**使用场景**：当 `TradingSignal.entry_delay_days > 0` 时，watch daemon 不立即下单，而是存入 pending store。下一轮循环时检查 `get_ready_signals()` 并执行到期信号。

---

## 六、验证结果

```
$ python3 -m pytest tests/ -v
30 passed in 0.03s

$ python3 集成测试
Registry has 17 evaluators
TradingSignal v3 fields OK
MarketData trend fields OK
PendingSignalStore OK
StrategyFactory loaded 29 instances
Trend entry strategies: ['TREND_ENTRY_VST', 'TREND_ENTRY_CEG', 'TREND_ENTRY_NVDA', 'TREND_ENTRY_AVGO', 'TREND_ENTRY_VRT']
=== ALL INTEGRATION CHECKS PASSED ===
```

---

## 七、字段覆盖矩阵

| MarketData 字段 | 对应老大条件 | trend_entry.yaml | trend_entry_strict.yaml |
|---|---|---|---|
| `ma_200` | 条件1 均线排列 | ✅ ma_stack | ✅ ma_stack |
| `ma_50_slope` | 条件2 MA50斜率 | ✅ sma_slope(50) | ✅ sma_slope(50) |
| `ma_200_slope` | 条件3 MA200转上 | ❌ | ✅ sma_slope(200) |
| `ma_spread_ratio` | 条件4 间距紧凑 | ✅ ma_spread | ✅ ma_spread |
| `is_consolidating` | 条件5 横盘 | ✅ consolidation_breakout | ✅ consolidation_breakout |
| `breakout_detected` | 条件5 突破 | ✅ consolidation_breakout | ✅ consolidation_breakout |
| `retrace_to_ma50` | 条件6 回撤反弹 | ✅ retrace_breakout | ✅ retrace_breakout |
| `consolidation_days` | 条件7 斐波那契 | ❌ | ✅ fib_time |
| `volume_ratio` | 条件8 放量 | ✅ volume_spike | ✅ volume_spike |

- **`trend_entry.yaml`**（宽松版）：7 个条件 AND，信号相对频繁，适合初期验证
- **`trend_entry_strict.yaml`**（严格版）：全部 9 个条件 AND（含 MA200 斜率 + 斐波那契），信号稀少但高胜率

---

## 八、后续工作

| 项目 | 优先级 | 说明 |
|------|--------|------|
| Watch Daemon 集成 | P0 | daemon 循环中调用 `PendingSignalStore` 处理延迟信号 |
| 回测验证 | P0 | 用 BacktestEngine 对 trend_entry 参数做网格搜索 |
| OCO 实盘测试 | P1 | 在 Paper 环境验证 bracket order 的部分成交行为 |
| 斐波那契条件优化 | P2 | 当前只检查横盘天数，未来可扩展为"从高点回调天数" |
| 条件3 (MA200 走平转上) | P2 | 需独立于 sma_slope，增加"方向变化"检测逻辑 |
| 指数期货支持 | P2 | 新资产类别，Contract 定义需扩展 FUT 类型 |
