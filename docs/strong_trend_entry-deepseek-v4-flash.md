# 强趋势入场策略 — 老大建议分析 & 落地规划

> 分析基于 `docs/PROJECT_OVERVIEW.md`（系统现状） + `tmp/ibkr_models_from_老大.txt`（专家建议）
> 模型：deepseek-v4-flash | 最后更新：2026-05-22

---

## 一、老大建议的核心内容

### 1.1 建议①：指数期货

在 IBKR 添加 YM（道指 E-mini）、MES（标普 E-mini）、MNQ（纳指 E-mini）期货合约。指数涨跌一点，YM/MES 盈亏 $5，MNQ 盈亏 $2，保证金仅需几千刀。流动性极好，适合自动交易。

### 1.2 建议②：Watchlist 机制

自动交易系统不应无差别扫描大量股票。应维护一个股票池（Watchlist），根据市场热点手工更新，系统只检索池内标的，降低算力消耗、聚焦关注品种。

### 1.3 建议③：订单追踪（部分成交处理）

下单 100 股可能只成交 30 股，剩余是继续等还是 Cancel？止损单急跌跳空时如果不能全部成交，需特别修改价格。系统需要有 `fill_policy`（IOC / GTC / CANCEL_REMAINING）。

### 1.4 建议④：强趋势入场策略（12 条条件）

这是最核心的建议——一套完整的趋势跟踪入场/出场规则：

| # | 条件 | 类型 |
|---|------|------|
| 1 | Price > SMA50 > SMA200 | 均线排列 |
| 2 | SMA50 斜率向上（>0°，<45°，建议 25°） | 均线角度 |
| 3 | SMA200 由走平转上（>5°）或已向上 | 均线角度 |
| 4 | SMA50 与 SMA200 间距 < 历史均值 1/4 | 均线间距 |
| 5 | 股价横走近 SMA50（±3%），突破平台向上 | 形态识别 |
| 6 | 股价回撤近 SMA50（±3%），突破趋势线向上 | 形态识别 |
| 7 | 调整时间符合斐波拉契数（5/8/13/21/34 天 ±1-2） | 时间序列 |
| 8 | 突破量 > 均量 1.3 倍 | 成交量确认 |
| 9 | 突破日 RSI > 45 | 动能确认 |
| 10 | 买入点 = 突破当日或次日 | 执行时机 |
| 11 | 止损 = 前平台低点 / SMA50（允许 -3% 误差） | 风险控制 |
| 12 | 初始止盈 20% / 高位回撤 > 7% 退出 | 出场策略 |

---

## 二、现有系统对比分析

### 2.0 核心洞察

**老大 的核心判断**：当前系统使用的是**均值回复策略**（低买高卖：RSI 超卖买入、超买卖出、均线回归），而他给的是**趋势跟踪策略**（强者恒强：Price > SMA50 > SMA200 多头发散、突破入场）。这是两种完全不同的交易哲学——

| 维度 | 均值回复（当前） | 趋势跟踪（老大建议） |
|------|----------------|-------------------|
| 哲学 | 跌多了会涨，涨多了会跌 | 强者恒强，弱者恒弱 |
| 入场 | RSI 超卖、价格跌破均线 | 均线多头排列、突破确认 |
| 出场 | RSI 超买、急涨卖出 | 高位回撤或固定止盈 |
| 信号频率 | 较高（震荡市反复触发） | 较低（只在趋势启动时） |
| 适合行情 | 震荡市 | 趋势市 |
| AI 适配性 | 信号短促，对执行延迟敏感 | 信号持久，滑点可预测，更适合自动化 |

**老大给的趋势跟踪策略，恰好补上了系统在趋势行情中的短板。两者并存，震荡市靠均值回复，趋势市靠趋势跟踪，才是完整的策略体系。**

### 2.1 系统现状概览

当前系统已实现的策略体系：

```
策略来源分两类:
  template/: 通用策略模版（可复用于任意标的，{symbol} 占位符）
  strategies/: 特殊策略（只针对特定标的，不通用）
  两者通过 config.watch.symbols.{symbol}.templates[] / strategies[] 引用

每种策略文件（不论位置）结构一致:
  ├── signal_factors: 声明数据需求
  ├── conditions: 条件树 (AND/OR + 原子条件)
  │   └── type: rsi / change_pct / price_vs_ma / ...
  │       → conditions/*.py @register 求值器
  └── action: 订单生成参数
        → _create_signal() → TradingSignal

StrategyFactory (全局单例)
  └── analyze(target_symbols, position_map)
        → 逐策略 evaluate() → _resolve_conflicts()
        → List[TradingSignal]

Watch Daemon (5s 轮询)
  └── 绕过 StrategyFactory，自有 _load_strategies / _build_signal_dict
        → 架构债务，需 Phase B 重构
```

### 2.2 老大建议①：指数期货 — 影响评估

| 维度 | 现状 | 需要支持 |
|------|------|---------|
| 合约类型 | 只支持 STK（股票/ETF） | 需支持 FUT（期货） |
| Contract 定义 | symbol + exchange + currency | 额外需要 productId、multiplier、到期日 |
| 保证金 | 无 | 需 SPAN 保证金检查 |
| 订单类型 | MKT / LMT / STP | 期货兼容，无新增需求 |

**结论**：新资产类别，改动范围大（5-7 天）。与现有策略体系无冲突，可独立实现。**暂缓**。

### 2.3 老大建议②：Watchlist — 影响评估

| 维度 | 现状 | 目标 |
|------|------|------|
| 标的来源 | 32 个 per-symbol YAML 文件推导 | `config.watch.symbols` 统一管控 |
| 新增标的 | 手动复制 3 个 YAML 文件 + 改配置 | `watchlist add` 一行命令 |
| 标的范围 | 硬编码 8 个 | 任意数量，CLI 动态管理 |

**结论**：与策略体系重构合并解决。**纳入整体重构**。

### 2.4 老大建议③：部分成交处理 — 影响评估

| 维度 | 现状 | 目标 |
|------|------|------|
| 部分成交 | `_handle_executed_order` 读 filled_qty 做绩效，无处理策略 | 支持 IOC / GTC / CANCEL_REMAINING |
| Order 模型 | 有 remaining 字段但从未使用 | fill_policy 驱动剩余处理 |
| 止损跳空 | 无处理 | 自动改价或取消 |

**结论**：Action 管线的独立增强，影响范围可控。**并行推进**。

### 2.5 老大建议④：强趋势入场 — 影响评估

这是最重磅的建议，分三层分析：

#### Layer 1: Condition Evaluator（9 个）

| # | 条件 | 现状 | 需要新增 | 难度 | 价值 |
|---|------|------|---------|------|------|
| 1 | `Price > SMA50 > SMA200` | ❌ 无 SMA200，无级联检查 | `ma_stack` evaluator + SMA200 计算 | 低 | 极高 |
| 2 | `SMA50 斜率 0°~45°` | ❌ 无斜率概念 | `sma_slope` evaluator（线性回归角度） | 中 | 高 |
| 3 | `SMA200 走平转上 >5°` | ❌ 同 2 | 复用 `sma_slope`，不同 period 参数 | 低 | 中 |
| 4 | `SMA50/SMA200 间距 < 均值 1/4` | ❌ 无间距概念 | `ma_spread` evaluator（需回测历史均值） | 中 | 高 |
| 5 | `横盘 ±3% → 突破` | ❌ 无模式识别 | `consolidation_breakout` evaluator | 中 | 极高 |
| 6 | `回撤 ±3% → 突破趋势线` | ❌ 无模式识别 | `retrace_breakout` evaluator | 中 | 极高 |
| 7 | `斐波拉契时间序列` | ❌ 无时间概念 | `fib_time` evaluator | 低 | 中 |
| 8 | `突破量 > 均量 1.3x` | ⚠️ 已有 volume_spike，硬编码 2.0x | 改造为参数化 multiplier | 极低 | 高 |
| 9 | `突破日 RSI > 45` | ✅ RSI 条件已实现 | 仅调整 YAML threshold 参数 | 无需改动 | — |

**关键洞察**：条件 #5 和 #6（横盘突破 / 回撤突破）价值最高——它们定义了趋势跟踪的核心入场时机。条件 #1（均线排列）和 #8（放量确认）次之，作为过滤条件极大提高胜率。

这些 evaluator 全部遵循现有 `@register("type_name")` + `evaluate(node, context)` 模式，**纯增量**，不改动现有系统。

#### Layer 2: Action 管线（3 个）

| # | 条件 | 现状 | 需要新增 | 难度 | 价值 |
|---|------|------|---------|------|------|
| 10 | 买入点 = 突破当日/次日 | ⚠️ 无延迟执行概念 | TradingSignal `entry_delay` + pending_signals 持久化 | 中 | 高 |
| 11 | 止损 = 平台低/SMA50(-3%) | ❌ 无动态止损 | OCO 订单 (BUY + STP) + 动态价格计算 | 高 | 极高 |
| 12 | 止盈 20% / 高位回撤 > 7% 退出 | ❌（F14 规划中） | OCO 订单 (BUY + SELL LMT) + 回撤跟踪，可回测参数 5%/7%/10%/15%/20% | 高 | 极高 |

**关键洞察**：条件 #11 和 #12 定义了完整的"入场→止损→止盈"闭环。没有它们，趋势跟踪只有"买"没有"卖"和"保护"。这恰好补上了当前系统最大的缺失——只有止盈止损的机械规则，没有动态风险控制。

这部分需要结构性重构：`place_order` 支持 OCO parent-child。

#### Layer 3: 策略体系重构

建议④触发了对当前策略体系的根本性反思——当前 24 个 per-symbol 文件（8 标的 × 3 类型：dip_buy/ma_buy/bounce_sell）逻辑完全重复，应合并为 3 个通用模版放入 `templates/`。但 `strategies/` 目录保留，存放每个标的的特殊策略（如 `nvda_force_buy`），它们逻辑独特，不值得/无法模版化。

分两条线改造：
- `templates/`：通用模版（dip_buy, ma_buy, bounce_sell, trend_entry, stop_loss），带 `{symbol}` 占位符
- `strategies/`：特殊策略（如 `nvda_force_buy.yaml`），不带占位符，只供特定标的使用
- `config.watch.symbols.{symbol}` 通过 `templates[]` 和 `strategies[]` 分别引用

### 2.6 需要结构性重构的 3 件事

| # | 项目 | 根因 | 范围 |
|---|------|------|------|
| R1 | **Watch Daemon Phase B** | daemon 绕过 StrategyFactory，自有策略加载/信号构建 | daemon 重写信号生成部分 |
| R2 | **Action 管线扩展** | place_order 不支持 OCO、entry_delay、dynamic stop | orders.py + TradingSignal + daemon |
| R3 | **策略体系三层重构** | 24 个重复 per-symbol 文件（8×3 可模版化），需合并为 3 个模版；`strategies/` 保留给特殊策略 | StrategyTemplateEngine + config + 目录重新分工 |

### 2.7 四建议的价值评估

| 建议 | 工作量 | 价值 | 对系统的提升 | 优先级 |
|------|--------|------|------------|--------|
| ① 指数期货 | 5-7 天 | 高 | 新资产类别，流动性极佳 | **P2** |
| ② Watchlist | 0.3 天 | 高 | 降低管理成本，聚焦关注品种 | **P1**（配合模版体系） |
| ③ 部分成交 | 1 天 | 高 | 实盘安全的关键拼图 | **P1** |
| ④ 强趋势入场 | 10-15 天（含体系重构） | 极高 | 全新策略维度，补上趋势跟踪短板 | **P0** |

**优先级排序依据**：
- **P0**：建议④价值最高、改动最大、影响最深，需优先启动
- **P1**：建议②和③是基础设施增强，可并行推进
- **P2**：建议①是独立新资产类别，策略体系稳定后再考虑

### 2.8 回测验证计划

老大建议中的参数（斐波拉契、斜率角度 25°、间距 1/4 历史均值等）**不能直接用于实盘**，需先用现有 `BacktestEngine` 验证：

| 参数 | 扫描范围 | 评估指标 |
|------|---------|---------|
| SMA50 斜率角度 | [5°, 15°, 25°, 35°, 45°] | 夏普比率、胜率、最大回撤 |
| MA 间距阈值 | [1/8, 1/4, 1/2, 全量] 历史均值 | 信号频率、胜率 |
| 止盈 / 回撤组合 | (20%/5%), (20%/7%), (20%/10%), (15%/7%), (15%/5%) | 盈亏比、收益率 |
| 斐波拉契 vs 固定周期 | [5,8,13,21,34] ±2 vs 等间隔 10/15/20 天 | 命中率、平均收益 |
| 成交量倍数 | [1.0x, 1.3x, 1.5x, 2.0x] | 信号质量 |

回测标的池：NVDA / AVGO / VST / CEG / DLR（当前关注 + AI 赛道），时间范围 2019-2025。

### 2.9 纯增量（不动现有架构）的改进

| # | 项目 | 文件 |
|---|------|------|
| I1 | 7 个新的 Condition Evaluator | `conditions/ma_stack.py`, `sma_slope.py`, `ma_spread.py`, `consolidation_breakout.py`, `retrace_breakout.py`, `fib_time.py`, `volume_spike.py` (改) |
| I2 | MarketData 扩展 9 个字段 | `models.py` + `market_data.py` compute_indicators |
| I3 | 4 个策略模版 YAML | `templates/dip_buy.yaml`, `ma_buy.yaml`, `bounce_sell.yaml`, `trend_entry.yaml` |
| I4 | TradingSignal v3 字段 | `strategy.py` dataclass |

---

## 三、重构/改进路线图

### 3.1 Target Architecture

两条加载路径，互不冲突：

```
Template 路径（strategy/templates/*.yaml）
  ├── 完整策略定义：conditions + action + 所有参数
  ├── {symbol} 占位符
  ├── 参数封装在模版内部，无需外部覆写
  └── 通过 config.watch.symbols.{symbol}.templates[] 引用（仅模版名）

Strategy 路径（strategy/strategies/*.yaml）
  ├── per-symbol 特殊策略，不通用
  ├── 无 {symbol} 占位符，硬编码标的
  ├── 如 nvda_force_buy.yaml、aapl_xxx.yaml
  └── 通过 config.watch.symbols.{symbol}.strategies[] 引用

Watchlist (config.watch.symbols)
  ├── 决定"交易哪些标的"
  ├── 每个标的指定 templates[]（仅模版名）+ strategies[]（文件名）
  └── CLI 管理：watchlist add/remove/list
```

**设计原则**：模版是完整的策略单元，参数写在模版 YAML 内。如需不同参数，创建模版变体（如 `trend_entry_aggressive.yaml`），不要在 config 里覆写。

配置示例：

```yaml
# config/ibkr.yaml
watch:
  poll_interval: 5
  template_dir: "strategy/templates"
  strategy_dir: "strategy/strategies"
  symbols:
    NVDA:
      cooldown_minutes: 30
      templates: ["trend_entry", "dip_buy"]  # 仅模版名
      strategies: ["nvda_force_buy.yaml"]    # 特殊策略
    AVGO:
      cooldown_minutes: 30
      templates: ["trend_entry", "dip_buy"]
      strategies: []
    VST:
      cooldown_minutes: 20
      templates: ["trend_entry_aggressive"]  # 模版变体
      strategies: []
```

### 3.2 执行路线图（15 天）

```
Phase 0 (Day 1-2): 数据底座
  ├─ models.py: MarketData 新增 9 个字段
  ├─ market_data.py: compute_indicators 扩展
  │   ├─ ma_200, ma_slope, ma_spread_ratio
  │   ├─ consolidation 检测, fib_time, volume_ratio
  └─ 单元测试

Phase 1 (Day 3-5): Condition Evaluator
  ├─ 新增 7 个 evaluator（完全遵守 @register 模式）
  ├─ volume_spike.py: multiplier 参数化
  ├─ 集成测试：新条件 + ConditionTree 组合
  └─ 回测验证 12 条件参数（NVDA 2019-2025）

Phase 2 (Day 6-10): 策略体系重构
  ├─ StrategyTemplateEngine（templates 路径 → 绑定 {symbol} → 展开实例）
  ├─ StrategyFactory 改造：支持两条加载路径（templates + strategies）
  ├─ config.py: WatchConfig 保留 strategies[] + 新增 templates[]
  ├─ config/ibkr.yaml: watch 段改写（templates + strategies 并列）
  ├─ strategy/templates/ 新建 4 个模版（从 24 个重复文件中提取通用逻辑）
  ├─ strategy/strategies/ 保留，只删除 24 个重复文件，特殊策略不动
  ├─ Watch Daemon Phase B: 接入 StrategyFactory
  └─ Paper 全链路验证

Phase 3 (Day 11-15): Action 管线
  ├─ TradingSignal v3 扩展
  ├─ orders.py: place_order 支持 OCO
  ├─ watch_daemon: pending_signals 持久化 + 续期
  └─ 沙盒验证：entry_delay / OCO / take_profit
```

### 3.3 文件变动清单

| 文件 | 操作 | 性质 |
|------|------|------|
| `src/core/models.py` — MarketData | 扩展 9 字段 | 增量 |
| `src/core/models.py` — TradingSignal | 扩展 v3 字段 | 增量 |
| `src/core/market_data.py` — compute_indicators | 新增 SMA200/slope/consolidation 等计算 | 增量 |
| `src/core/conditions/ma_stack.py` | **新建** | 增量 |
| `src/core/conditions/sma_slope.py` | **新建** | 增量 |
| `src/core/conditions/ma_spread.py` | **新建** | 增量 |
| `src/core/conditions/consolidation_breakout.py` | **新建** | 增量 |
| `src/core/conditions/retrace_breakout.py` | **新建** | 增量 |
| `src/core/conditions/fib_time.py` | **新建** | 增量 |
| `src/core/conditions/volume_spike.py` | 修改：multiplier 参数化 | 微改 |
| `src/core/strategy.py` — StrategyTemplateEngine | **新建类** | 增量 |
| `src/core/strategy.py` — StrategyFactory | 改造：从 bind_all() 加载 | 重构 |
| `src/core/orders.py` — place_order | 支持 OCO parent-child | 重构 |
| `src/trading/watch_daemon.py` | Phase B 重写信号生成部分 | 重构 |
| `config/ibkr.yaml` | watch 段改写 | 修改 |
| `config/config.py` — WatchConfig | 简化 | 修改 |
| `strategy/strategies/{nvda,avgo,ceg,dlr,vrt,vst,f,aapl}_{dip_buy,ma_buy,bounce_sell}.yaml` (24 个) | **删除**（合并为 templates/ 下的 3 个模版） | 删除 |
| `strategy/strategies/*.yaml` (剩余特殊策略，如 `nvda_force_buy`) | **保留** | 不变 |
| `strategy/templates/dip_buy.yaml` | **新建**（从 8 个 per-symbol dip_buy 提取） | 新建 |
| `strategy/templates/ma_buy.yaml` | **新建**（从 8 个 per-symbol ma_buy 提取） | 新建 |
| `strategy/templates/bounce_sell.yaml` | **新建**（从 8 个 per-symbol bounce_sell 提取） | 新建 |
| `strategy/templates/trend_entry.yaml` | **新建** | 新建 |

### 3.4 与现有 Roadmap 的关系

| 原 Roadmap 项 | 本计划中的覆盖 | 变化 |
|--------------|---------------|------|
| F14 阶梯止盈 | 条件12：止盈/回撤退出 | 从"规划中"升级，参数具体化(5%/7%/10%/15%/20%) |
| F15 ATR 波动率感知 | — | 不受影响，独立 |
| P0 去随机化 | 模版体系中不再需要 random | 自然消除 |
| P0 重连通知 | — | 不受影响，独立 |
| Phase 8 策略改进 | 全部纳入 Phase 1-3 | 从"待定"变为具体 15 天计划 |

---

## 四、深度讨论区

> 本章记录每次讨论的话题、决策和结论。可据此随时调整第三章的路线图。

### 4.1 讨论 1：12 条条件的分层分类

**话题**：老大给的 12 条条件属于系统的哪个层？

**结论**：
- #1-9 → Condition Evaluator（纯指标检查，stateless）
- #10-12 → Action/Execution Pipeline（不是触发条件，是执行策略）
- 关键在于 #5/#6/#7 需要历史 K 线数据，但通过 MarketData 预计算解决（不引入有状态 evaluator）

### 4.2 讨论 2：Condition Evaluator 设计原则

**话题**：模式识别条件（横盘、回撤、斐波拉契）如何处理历史数据依赖？

**决策**：不走有状态 evaluator。改为在 `compute_indicators()` 中预计算指标 → 放入 `MarketData` → evaluator 从 `ConditionContext.market_data` 字段中读取。evaluator 保持纯函数、stateless、可测试。

```python
# MarketData 新增字段（条件 #5/#6/#7 的数据基础）
class MarketData:
    ...
    ma_200: Optional[float] = None
    ma_50_slope: Optional[float] = None
    ma_200_slope: Optional[float] = None
    ma_spread_ratio: Optional[float] = None
    is_consolidating: Optional[bool] = None
    consolidation_range_pct: Optional[float] = None
    days_since_52w_low: Optional[int] = None
    days_since_52w_high: Optional[int] = None
    volume_ratio: Optional[float] = None
```

### 4.3 讨论 3：Watchlist 配置位置

**话题**：是否需要单独的 `config/watchlist.yaml`？

**决策**：不需要。复用 `config/ibkr.yaml` 中已有的 `watch.symbols` 段。

### 4.4 讨论 4：趋势模版是全局绑定还是 per-symbol 选择？

**话题**：`templates: ["trend_entry"]` 放在哪里？

**决策**：Per-symbol 选择。每个标的独立指定用哪些模版。NVDA 可以用 `trend_entry` + `dip_buy`，AAPL 只用 `dip_buy`，VST 只用 `trend_entry`。option 在 `config.watch.symbols.{symbol}.templates`。

### 4.5 讨论 5：三层架构与参数所有权

**话题**：参数应该放在模版 YAML 还是 config？

**讨论过程**：
- 初版设计：config 中 `templates` 是 dict，key=模版名，value=per-symbol 覆写参数
- 最终结论：参数应当封装在模版 YAML 内部，`ibkr.yaml` 只列模版名

**理由**：
1. `template_dir` 指向了模版文件，参数自然应在模版中
2. 模版是完整的策略单元——逻辑 + 参数，不应拆分到两个地方
3. 如需不同参数，创建模版变体（`trend_entry_aggressive.yaml`），config 选不同模版即可

**最终决策**：

| 层 | 定义 | 存储 | 生命周期 |
|---|------|------|---------|
| **Template** | 完整策略单元：conditions + action + 所有参数 | `strategy/templates/*.yaml` | 写一次，偶尔修改 |
| **Strategy** | Template + symbol = 具体策略实例 | `config.watch.symbols` → 运行时展开 | 每次 daemon 启动时重建 |
| **Watchlist** | 所有 Strategy 的集合，标的列表 | `config.watch.symbols` keys | 手工维护，CLI 管理 |

`ibkr.yaml` 只选模版名，不覆写参数：

```yaml
NVDA:
  templates: ["trend_entry", "dip_buy"]       # 仅模版名
  strategies: ["nvda_force_buy.yaml"]
```

`trend_entry.yaml` 封装所有参数：

```yaml
strategy_id: "TREND_ENTRY_{symbol}"
quantity: 10
priority: 20
take_profit:
  profit_pct: 0.20
  retrace_pct: 0.07
```

### 4.6 讨论 6：strategies/ 目录的去留

**话题**：`strategy/strategies/` 下的 32 个 per-symbol 文件怎么处理？

**最终决策**：**不全部删除，重新分工**。

| 目录 | 用途 | 文件 |
|------|------|------|
| `strategy/templates/` | 通用模版，可复用于任意标的，带 `{symbol}` 占位符 | dip_buy, ma_buy, bounce_sell, trend_entry（4 个） |
| `strategy/strategies/` | per-symbol 特殊策略，逻辑太独特不值得模版化 | nvda_force_buy, aapl_xxx 等 |

操作步骤：
1. 从 24 个重复文件中提取通用逻辑，合并为 3 个模版放入 `templates/`
2. 删除这 24 个文件
3. `strategies/` 保留，只存放真正的特殊策略
4. 特殊策略不经过 TemplateEngine，直接由 StrategyFactory 按文件名加载

### 4.7 讨论 7：趋势 vs 均值回复共存

**话题**：同一标的 NVDA 既有 dip_buy（RSI<45 买入）又有 trend_entry（多头排列追高），信号打架怎么处理？

**决策**：
1. **Regime 隔离**：`trend_entry` 设 `regime_weights: { BULL: 1.5, BEAR: 0.0, SIDEWAYS: 0.5 }`，熊市自动关闭
2. **权重优先级**：trend_entry priority=20 > dip_buy priority=10，同时触发时趋势优先
3. **方向互斥**：同一标的同一方向（BUY）不重复；相反方向（一个 BUY 一个 SELL）可以共存

### 4.8 与老大面谈的问题清单（2026-05-23）

> 以下问题在 Phase 2 启动前需与老大确认，直接关系到路线图的具体参数和设计决策。

#### 关于 12 条条件的参数来源

1. 斜率 25°、间距均值 1/4、斐波拉契数列——这些数值来自他个人的回测经验，还是通用的技术分析共识？NVDA 和 VST 的参数是否应不同？
2. 条件 #5（横盘突破）和 #6（回撤突破）是 OR 关系——他交易中哪种形态胜率更高？值得分别独立做策略模版吗？

#### 关于趋势与均值回复共存

3. 趋势跟踪和均值回复共用同一标的时，他靠什么机制做决策——regime 检测还是手动切换？同一标的同时产生 BUY 信号（dip_buy 超卖 + trend_entry 突破），在他眼中是冲突还是机会？
4. 他偏好"少量高信噪比"策略（10 个精心设计的模版），还是"广撒网"（50 个模版覆盖所有标的）？这决定模版体系的规模方向。

#### 关于 IBKR 执行层实操

5. OCO 订单（BUY + STP 止损，BUY + LMT SELL 止盈）在 IBKR API 上有没有坑？他实际用的是什么 order type 组合？
6. 部分成交——跳空止损没成交时，他手工处理还是靠算法自动改价？改价逻辑是怎样的（立即改、等 N 秒、按 ATR 偏移）？

#### 关于指数期货（建议①）

7. 期货的保证金计算是否需要每次都查 IBKR，还是可以用固定值预估算？期货 Contract 定义有没有现成的模板可以参考？

**话题**：同一标的 NVDA 既有 dip_buy（RSI<45 买入）又有 trend_entry（多头排列追高），信号打架怎么处理？

**决策**：
1. **Regime 隔离**：`trend_entry` 设 `regime_weights: { BULL: 1.5, BEAR: 0.0, SIDEWAYS: 0.5 }`，熊市自动关闭
2. **权重优先级**：trend_entry priority=20 > dip_buy priority=10，同时触发时趋势优先
3. **方向互斥**：同一标的同一方向（BUY）不重复；相反方向（一个 BUY 一个 SELL）可以共存
