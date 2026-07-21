# Sprint 0721 — 风控基建 + Universe-Selector 完善

**日期**: 2026-07-21
**目标**: 完善风控体系，为实盘部署扫清障碍

---

## 一、背景

SOUL.md v2.0 已将账户类型切换为 Margin Account，但仍保留"禁止做空"的硬约束。当前代码仅有被动兜底（`SELL + 无持仓 → skip`），缺少主动的做空方向校验。需要从**信号级别**拦截做空行为，并通过配置开关可控。

---

## 二、待完成

1. [ ] **方向校验（禁做空）** — `allow_short_selling: false` 开关，信号层拦截
2. [ ] **止损/止盈 OCO 订单** — 每个持仓绑定止损+止盈单
3. [ ] **Telegram 通知集成** — 替换 WeChat
4. [ ] **日熔断机制** — 日亏 ≥3% 停止当日交易
5. [ ] **DLR 频率优化** — 评估/改进交易频率

---

## 三、已验证

| 功能 | 验证日期 | 说明 |
|------|---------|------|
| 候选池报告 | 07-20 18:00+ | top10 更新，AMAT/KLAC BUY 信号 |
| Section 11.4 BUY 信号 | 07-20 | T+1 执行，5 个订单全部 SUBMITTED |
| 持仓市值计算 | 07-20 | MarketDataProvider+yfinance，重复日志修复 |
| 空头仓位平仓 | 07-20 收盘后 | MSFT/NVDA/GOOGL BUY to Cover，全部 SUBMITTED |
| approval_required | ✅ | 配置开关，order-list/approve/reject 已实现 |

---

## 四、执行记录

### 4.1 方向校验（禁做空）— ✅ 已完成（当前提交）

**目标**: 在信号层（而非执行层）拦截做空行为

**配置**:
```yaml
# ibkr.yaml
trading:
  allow_short_selling: false  # true=允许做空，false=Long Only（禁止主动做空）
```

**核心逻辑**:
```python
# Long Only: 叠加信号后持仓不得为负（不能变为空头）
# 公式: new_pos = pos + qty（BUY时加，SELL时减），new_pos < 0 → 禁止
if action == "SELL":
    new_pos = pos - qty
elif action == "BUY":
    new_pos = pos + qty
if new_pos < 0:
    return False, f"Long Only: {symbol} 持仓 {pos} 股，{action} {qty} 股后变为空头 ({new_pos} 股)，禁止"
```

**行为** (`allow_short_selling: false`):

| 持仓 pos | Signal | qty | new_pos | 结果 |
|---------|--------|-----|---------|------|
| 多头 100 | SELL | 50 | 50 | ✅ 减仓 |
| 多头 100 | SELL | 100 | 0 | ✅ 清仓 |
| 多头 100 | SELL | 101 | -1 | ❌ 多转空，禁止 |
| 空头 -50 | SELL | 30 | -80 | ❌ 增加空头，禁止 |
| 无持仓 0 | SELL | 10 | -10 | ❌ 建空头，禁止 |
| 多头 100 | BUY | 50 | 150 | ✅ 做多建仓 |
| 空头 -50 | BUY | 30 | -20 | ❌ 加空，禁止 |
| 空头 -50 | BUY | 50 | 0 | ✅ 平空仓 |
| 空头 -50 | BUY | 80 | 30 | ✅ 做多（反向建仓） |
| 无持仓 0 | BUY | 10 | 10 | ✅ 建仓 |

**文件改动**:
- `config/ibkr.yaml` — 新增 `trading.allow_short_selling: false`
- `config/config.py` — `IBKRConfig` 新增 `allow_short_selling` 字段 + `from_yaml` 解析
- `src/trading/put_order.py` — 新增 `_check_long_only_mode()` 函数，信号预处理阶段调用

---

### 4.2 止损/止盈 OCO 订单 — ✅ 已完成

**目标**: 每个持仓绑定止损+止盈 OCO 单（触发一个自动取消另一个）

**OCO 用途（概念确认）**:
- 建仓后挂两个保护单：**止损单**（价格跌到某点卖，控制最大亏损）和**止盈单**（价格涨到某点卖，锁定利润）
- 两个价格拉开，触发任意一个，另一个自动取消 → "One-Cancels-Other"

**订单类型说明**:
- 正股建仓：MKT（**市价单**，立即成交）→ 当前使用
- OCO 保护单：LMT（**限价单**，条件触发）→ 当前使用

**正股 OCO vs 期权对冲**:

| | 正股 OCO | 期权（Options）|
|---|---|---|
| 本质 | 卖出股票止损/止盈 | 买 Put（保护性）/ 卖 Call（Covered Call）|
| 优点 | 简单，IBKR 原生支持 | 效率高，用更少资金保护更大仓位 |
| 缺点 | 需要占用等额资金 | 复杂（行权价、到期日、IV 等）|
| 适用 | 趋势跟踪（当前场景）| 有保护需求或收入增强 |

**配置**（`UniverseSelectorConfig`，默认值）:
```python
oco_enabled=True        # 开启 OCO
stop_loss_pct=-10.0    # 亏损 10% 触发止损
take_profit_pct=20.0   # 盈利 20% 触发止盈
```

**实现**（`build_and_submit_order`，建仓成功后）:
```python
# 用已有 place_bracket_order() 创建 bracket order
# parent=LMT + children=STP(止损) + LMT(止盈)，tif=GTC
sl_price = fill_price * (1 + stop_loss_pct / 100)
tp_price = fill_price * (1 + take_profit_pct / 100)
place_bracket_order(client, contract, action, quantity,
                    limit_price=fill_price,
                    stop_loss_price=sl_price,
                    take_profit_price=tp_price,
                    tif="GTC", timeout=15)
```

**已知问题 — 初始实现的 bug（已修复见 commit）**:
- 问题：主单用 MKT 成交后，`place_bracket_order` 会再下一笔 LMT 父单（重复建仓）
- 修复：主单改为 `order_type="LMT"`，与 bracket parent 合并为原子操作
- 影响：今天手动下的 5 个仓位无 OCO（代码是收盘后 deploy 的）；今晚盘后触发建仓时可验证

**文件改动**:
- `config/config.py` — `UniverseSelectorConfig` 新增 `oco_enabled=True`
- `src/trading/put_order.py` — 建仓成功后调用 `place_bracket_order`
