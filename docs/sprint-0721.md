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
