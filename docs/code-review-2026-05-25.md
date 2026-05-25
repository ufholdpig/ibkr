# 代码审查记录 — 强趋势入场重构

> 审查日期：2026-05-25
> 对比文档：`docs/strong_trend_entry-deepseek-v4-flash.md`（设计） / `docs/strong_trend_entry-refactoring-log.md`（实施日志）
> 审查范围：src/core/ 下策略引擎、条件求值器、订单执行、watch daemon 相关代码
> 操作模式：只读评估，未修改任何代码

---

## 一、整体评价

代码质量高，架构清晰，与设计文档高度一致。约 85% 的功能已正确落地，剩余问题集中在"信号产生 → 序列化 → 执行"的管线衔接上。

---

## 二、正确实现的部分

| 模块 | 状态 | 关键观察 |
|------|------|----------|
| **MarketData 字段** (strategy.py) | ✅ | 9 个趋势字段全部到位，含 `ma_200`, `ma_50_slope`, `ma_200_slope`, `ma_spread_ratio`, `is_consolidating`, `consolidation_days`, `volume_ratio`, `breakout_detected`, `retrace_to_ma50` |
| **compute_indicators()** (market_data.py) | ✅ | SMA slope 线性回归 + atan 角度转换；consolidation 检测回扫逻辑；volume_ratio/retrace_to_ma50 计算 |
| **6 个 Condition Evaluator** (conditions/) | ✅ | `ma_stack`, `sma_slope`, `ma_spread`, `consolidation_breakout`, `retrace_breakout`, `fib_time` — 全部遵循 `@register` + stateless 模式 |
| **volume_spike 参数化** (conditions/volume_spike.py) | ✅ | `node.multiplier` 已支持参数化阈值，向下兼容默认 2.0x |
| **StrategyTemplateEngine** (strategy.py) | ✅ | 模版文件缓存 + `{symbol}` 占位符 YAML dump/load 安全替换 + 异常路径返回 None |
| **StrategyFactory 双路径加载** (strategy.py) | ✅ | Path 1 模板展开 + Path 2 策略文件直载 + watch_symbols 为空时向后兼容全部加载 |
| **TradingSignal v3 字段** (strategy.py) | ✅ | dataclass 上定义了 `entry_delay_days`, `stop_loss_type`, `stop_loss_pct`, `take_profit_pct`, `trailing_stop_pct`, `oco_group_id` 共 6 个字段 |
| **place_bracket_order** (orders.py) | ✅ | OCO parent (LMT BUY, transmit=False) + child STP (transmit=False) + child LMT SELL (transmit=True) — 结构符合 IBKR bracket 规范 |
| **PendingSignalStore** (pending_signals.py) | ✅ | JSON 文件持久化 + `_execute_after` 到期检查 + `get_ready_signals` 自动标记已消费 + `cleanup` 过期清理 |
| **5 个 YAML 模版** (templates/) | ✅ | `trend_entry.yaml` (宽松 7 条件) / `trend_entry_strict.yaml` (严格 9 条件) / `dip_buy.yaml` / `ma_buy.yaml` / `bounce_sell.yaml` |
| **删除 24 个重复 per-symbol 文件** | ✅ | 从 `{8 symbols} * {3 strategies}` 合并为 templates/ 下 3 个通用模版 |
| **配置更新** (config/ibkr.yaml, config/config.py) | ✅ | `SymbolWatchConfig.templates` 字段 + `WatchConfig.template_dir` + per-symbol 独立指定模版列表 |
| **SignalGenerator 接入双路径** (signal.py) | ✅ | 构造 `watch_symbols_dict` 传给 StrategyFactory |
| **WatchDaemon 接入双路径** (watch_daemon.py) | ✅ | 构造 `watch_symbols_dict` 传给 StrategyFactory |
| **30 个单元测试** (tests/test_trend_conditions.py) | ✅ | 覆盖全部 7 个新 evaluator 的边界/正常/异常情况 |

---

## 三、发现的问题

### 3.1 🔴 信号序列化丢失 v3 字段

**文件**：`src/core/signal.py:23-47` — `convert_signal_to_dict()`

**问题**：TradingSignal v3 字段 `stop_loss_type`, `stop_loss_pct`, `take_profit_pct`, `trailing_stop_pct`, `oco_group_id`, `entry_delay_days` 完全没有被序列化到 dict 中。

```python
# 当前返回的 dict 只包含 v1/v2 字段
{
    "strategy_name": ...,
    "market_regime": ...,
    "processed": False,
    "timestamp": ...,
    # 缺少所有 v3 字段
}
```

**影响链路**：`Template YAML → TradingSignal → convert_signal_to_dict → signal JSON → execute()`
止损止盈/OCO 参数在 YAML 解析后即丢失，永远不会到达执行层。

**建议**：在 dict 中加入所有 v3 字段（default="" 或 0）。

---

### 3.2 🔴 `_create_signal` 未读取 oco_group_id 和 entry_delay_days

**文件**：`src/core/strategy.py:625-674` — `YAMLTemplateStrategy._create_signal()`

**问题**：`_create_signal` 从 `self.risk_config` 读取了 `stop_loss_type`, `stop_loss_pct`, `take_profit_pct`, `trailing_stop_pct`，但**没有读取** `entry_delay_days` 和 `oco_group_id`。

```python
# 当前读取的字段
risk = self.risk_config
stop_loss_type = risk.get("stop_loss_type", "")
stop_loss_pct = risk.get("stop_loss_pct", 0.0)
take_profit_pct = risk.get("take_profit_pct", 0.0)
trailing_stop_pct = risk.get("trailing_stop_pct", 0.0)
# 缺少: entry_delay_days, oco_group_id
```

**影响**：TradingSignal 对象构造时没有传入这两个值，即使 `convert_signal_to_dict` 修复了也仍然为空。

**建议**：补充读取 `entry_delay_days` 和 `oco_group_id` 并传入构造函数。

---

### 3.3 🟠 Watch Daemon 未接入 PendingSignalStore

**文件**：`src/trading/watch_daemon.py:644-677`

**问题**：主循环中处理 signals 时，对所有信号一视同仁直接 `convert_signal_to_dict` 并写入 signal JSON 文件。没有任何代码检查 `signal.entry_delay_days > 0` 并走 `PendingSignalStore` 路径。

```python
# 当前行为：所有信号统一写入
pending_signal_dicts = []
for signal in signals:
    ...
    signal_dict = convert_signal_to_dict(signal)
    pending_signal_dicts.append(signal_dict)
self._batch_submit_orders(pending_signal_dicts)

# 缺少的分支：
# if signal.entry_delay_days > 0:
#     store = PendingSignalStore()
#     store.add(signal_dict, delay_days=signal.entry_delay_days)
#     continue
```

**影响**：条件 10（买入点 = 突破当日或次日）的延迟执行机制未生效。

**建议**：在主循环中添加 PendingSignalStore 分支 + 在 daemon 启动时调用 `get_ready_signals()` 处理到期信号。

---

### 3.4 🟠 Watch Daemon 未使用 place_bracket_order

**文件**：`src/trading/watch_daemon.py:433-527` — `_batch_submit_orders()`

**问题**：即使 TradingSignal 携带了止损止盈信息，`_batch_submit_orders` 只是将信号序列化为 JSON 写入 signal 文件，然后调用 `execute()`。`execute()` 内部使用 `place_order()` 而非 `place_bracket_order()`，OCO 止盈止损从未被执行。

**影响**：条件 11（动态止损）和条件 12（止盈/回撤退出）完全不在执行管线中。

**建议**：从 execute() 到 place_order 之间增加分支判断：当 signal 携带 stop_loss 或 take_profit 参数时，调用 `place_bracket_order` 替代 `place_order`。

---

### 3.5 🟠 regime_detector 未传入 StrategyFactory

**文件**：`src/trading/watch_daemon.py:137-143`

**问题**：构造 StrategyFactory 时没有传入 `regime_detector` 参数：

```python
self.factory = StrategyFactory(
    config_dir=config.watch.strategy_dir,
    client=self._ibkr_client,
    market_data_source=config.market_data_source,
    template_dir=config.watch.template_dir,
    watch_symbols=watch_symbols_dict,
    # 缺少 regime_detector=...
)
```

**影响**：`StrategyFactory.analyze()` 中 `self.regime_detector` 为 None，regime 检测被跳过，`regime_weights` 永远不会被应用。趋势模版的 `BULL: 1.5, BEAR: 0.0, SIDEWAYS: 0.5` 形同虚设。

**建议**：在 daemon 初始化时创建 RegimeDetector 并传入 factory；或者在 factory 内部延迟初始化。

---

### 3.6 🟡 consolidation 的 MA50 序列计算脆弱

**文件**：`src/core/market_data.py:259-268`

**问题**：生成滚动 MA50 序列的索引计算难以推导且边界易出错：

```python
for i in range(min(60, len(closes) - 49)):
    end = len(closes) - 59 + 49 + i if len(closes) >= 110 else 50 + i
    end = min(end, len(closes))
    start = end - 50
    if start >= 0:
        ma_50_series.append(sum(closes[start:end]) / 50)
```

`len(closes) - 59 + 49 + i` 数学上等价于 `len(closes) - 10 + i`，但逻辑不直观。当 `len(closes)` 刚好在 110 左右时，两个分支的拼接可能产生重复或遗漏。

**建议**：重写为清晰的滑动窗口循环：
```python
ma_50_series = []
start_idx = max(0, len(closes) - 60 - 50)
for i in range(start_idx + 50, len(closes)):
    ma_50_series.append(sum(closes[i-50:i]) / 50)
```

---

### 3.7 🟡 retrace_breakout 的"突破趋势线"实现简化

**文件**：`src/core/conditions/retrace_breakout.py:36`

**问题**：条件 6 原文是"股价回撤近 SMA50（±3%），突破趋势线向上"。当前实现只检查了位置（price > ma_50）+ 动量（change_1d_pct > 0），没有真正的**趋势线**概念（连接两个回撤低点的斜线）。

```python
if context.market_price <= md.ma_50:
    return False
if md.change_1d_pct is not None and md.change_1d_pct > 0:
    return True
```

**影响**：这只是回撤后反弹，不是趋势线突破。误报率可能偏高（一个随机的小阳线就能触发）。

**建议**：当前可作为简化版使用，但文档应注明差异。后续可增加真正的趋势线计算（MarketData 预计算 `trendline_breakout` 字段）。

---

### 3.8 🟡 consolidation_breakout 仅触发单日

**文件**：`src/core/market_data.py:221`

**问题**：突破判定逻辑是：
```python
breakout_detected = closes[-1] > consolidation_high and len(closes) >= 2 and closes[-2] <= consolidation_high
```
要求"今日收盘 > 横盘区间高点"**且**"昨日收盘 ≤ 横盘区间高点"。这意味着突破只被检测到一次，第二天就会变 false。

**影响**：与条件 10（突破当日或次日买入）一致，但信号窗口极窄。如果 daemon 轮询间隔较长或在盘后才运行，会错过信号。

**建议**：这是设计决定，不是 bug。需要注意 daemon 检查频率是否足够捕捉突破日。

---

## 四、未实施的关联项

以下列在 Plan / Refactoring Log 中但代码层面无体现：

| 项目 | 优先级 | 说明 |
|------|--------|------|
| Watch Daemon 集成 PendingSignalStore | P0 | 见 3.3 |
| OCO 实盘接入 execute() | P0 | 见 3.4 |
| 回测验证 | P0 | 参数网格搜索未进行 |
| 斐波那契条件优化（从"高点回调天数"） | P2 | 当前只检查横盘天数 |
| 条件3 MA200 走平转上（增加方向变化检测）| P2 | 当前 sma_slope 只检查角度范围 |

---

## 五、修复优先级建议

| 排序 | 问题 | 文件 | 预计工作量 |
|------|------|------|-----------|
| P0 | 3.1 + 3.2 (v3 字段链路断裂) | signal.py, strategy.py | 小（~15行改动） |
| P0 | 3.4 (bracket order 接入执行) | orders.py, intra_day.py / pre_market.py | 中（~50行） |
| P0 | 3.3 (延迟执行接入 daemon) | watch_daemon.py | 小（~20行） |
| P0 | 3.5 (regime_detector 传入) | watch_daemon.py | 极小（~3行） |
| P1 | 3.6 (consolidation 计算重构) | market_data.py | 小（~10行） |
| P2 | 3.7 (趋势线突破增强) | market_data.py, retrace_breakout.py | 中 |

---

## 六、实盘信号审查 — 2026-05-25 01:44

> 修复 `Dict[str, Optional]` bug + `StrategyFactory.client is None` guard 后，daemon 成功生成 8 个信号。
> 数据源：yfinance（IB Gateway 未运行），时段：01:44 ET（盘前，订单 PreSubmitted 待开盘执行）

### 6.1 生成的信号列表

| 标的 | 方向 | 策略 | 数量 | 策略来源 |
|------|------|------|------|----------|
| F | SELL | 反弹卖出 (bounce_sell) | ALL→跳过（无持仓） | 模版 |
| AAPL | SELL | 反弹卖出 (bounce_sell) | ALL→跳过（无持仓） | 模版 |
| VST | SELL | 反弹卖出 (bounce_sell) | ALL→260（持仓替换） | 模版 |
| CEG | SELL | 反弹卖出 (bounce_sell) | ALL→200（持仓替换） | 模版 |
| NVDA | BUY | 均线买入 (ma_buy) | 5 | 模版 |
| AVGO | BUY | 均线买入 (ma_buy) | 5 | 模版 |
| DLR | BUY | 均线买入 (ma_buy) | 5 | 模版 |
| VRT | BUY | 回调买入 (dip_buy) | 5 | 模版（冲突合并自 ma_buy） |

### 6.2 观察

1. **8 个信号全部来自旧模版（dip_buy / ma_buy / bounce_sell），trend_entry 未触发**。这是预期的——trend_entry 的 7 条件 AND 要求很高，当前市场数据大概率不满足。

2. **bounce_sell 信号占据 4/8**：F、AAPL、VST、CEG 同时触发了 bounce_sell（RSI > 78 或日涨幅 > 3%）——表示这些标的处于短期超买状态。

3. **F 和 AAPL 无持仓 → 正确跳过**：SELL quantity=-1（ALL）在 put_order 层替换无持仓时被跳过。日志：

   ```
   ⚠️ 跳过卖出信号 - 无持仓: AAPL
   ```

4. **VST、CEG 使用实际持仓替换 ALL**：quantity=-1 → `{actual_position}` → 正常提交。日志：
   ```
   SELL quantity=-1 → 替换为实际持仓 260: VST
   ```

5. **所有订单 PreSubmitted**：由于在非交易时段提交（01:44 ET），IBKR 接受但标记为等待开盘：
   ```
   Warning: Your order will not be placed at the exchange until 2026-05-26 09:30:00 US/Eastern.
   ```

6. **VRT 冲突合并正确**：VRT 同时触发 ma_buy（priority=9）和 dip_buy（priority=10），`_resolve_conflicts` 按 weighted score 选择 dip_buy，reason 注明 `[合并自: VRT 均线买入]`。

### 6.3 仍存在的问题

| 问题 | 严重度 | 说明 |
|------|--------|------|
| **market_regime 为空** | 🟠 | 所有 signal 的 `market_regime: ""`，regime_detector 未传入 factory（代码审查 3.5） |
| **signal_price 为 0.0** | 🟡 | `_create_signal` 未填充 `signal_price` 字段，`TradingSignal.signal_price` 默认 0.0。不阻断执行，但影响绩效分析中的滑点计算 |
| **趋势模版零触发** | 🟡 | trend_entry 在当前市场条件下无一触发，需确认参数是否合理（回测验证 P0 未做） |
| **bounce_sell F/AAPL qty=-1 检查** | 🟡 | 盘前无持仓信息的 SELL 信号不会被执行，但信号写入 JSON 文件造成冗余 |
