# Sprint 0722 — Bracket Order + 状态码 + 去重 + 配置修复

**日期**: 2026-07-22
**目标**: 完善 bracket order 执行链路，修复状态码映射和去重逻辑

---

## 一、背景

基于 Sprint 0721 的 OCO bracket order 实现，在实际执行中发现多个问题：
1. 状态码映射不正确，导致订单状态显示 UNKNOWN
2. 去重逻辑不完善，导致重复提交订单
3. 配置参数（max_positions）未正确使用
4. 价格获取逻辑需要简化

---

## 二、已完成

### 2.1 状态码修复 — 直接使用 IBKR 原始状态

**问题**: 之前使用映射逻辑（PreSubmitted → SUBMITTED），但 bracket order 返回的状态可能是 PreSubmitted，导致映射失败显示 UNKNOWN

**修复**: 移除映射逻辑，直接使用 IBKR 原始状态

**IBKR 原始状态**:
- 已提交: `PreSubmitted`, `Submitted`, `PendingSubmit`
- 已成交: `Filled`, `PartiallyFilled`
- 已取消: `Cancelled`
- 被拒绝: `Rejected`
- 已过期: `Expired`

**修改文件**:
- `src/trading/put_order.py` — 直接返回 `place_result.status`
- `src/trading/pre_market.py` — 报告统计使用 IBKR 原始状态
- `skills/ibclient-all-in-one/ibclient.py` — 去重检查使用 IBKR 原始状态

---

### 2.2 去重逻辑修复 — 检查所有活跃订单

**问题**: 之前只检查 `SUBMITTED` 和 `FILLED` 状态，但 bracket order 状态是 `PreSubmitted`，导致重复提交

**修复**: 检查所有活跃订单状态

**去重逻辑**:
```python
ibkr_active_statuses = ("PreSubmitted", "Submitted", "PendingSubmit", "PartiallyFilled", "Filled")
if any(o.get("status") in ibkr_active_statuses for o in existing_orders):
    # 跳过重复信号
```

**修改文件**:
- `skills/ibclient-all-in-one/ibclient.py` — 检查 `PreSubmitted/Submitted/PendingSubmit/PartiallyFilled/Filled`
- `src/core/signal.py` — 同样检查这些状态

---

### 2.3 max_positions 配置修复 — 移除 TOP_N 变量

**问题**: `top2` 属性硬编码返回前 2 个标的，没有使用 `MAX_POSITIONS` 配置

**修复**: 
1. 修改 `top2` 属性使用 `self.MAX_POSITIONS`
2. 移除歧义变量 `TOP_N`，统一使用 `MAX_POSITIONS`

**修改文件**:
- `src/trading/universe_selector.py` — `top2` 属性使用 `self.MAX_POSITIONS`
- `config/config.py` — 移除 `top_n` 配置
- `src/trading/universe_selector.py` — `_top_n_for_save` 重命名为 `_candidates_for_save`

---

### 2.4 价格获取逻辑简化 — oco_enabled 控制 MKT/LMT

**问题**: 价格获取逻辑复杂，有不必要的 `signal.get("price")` 检查

**修复**: 简化逻辑，`oco_enabled` 控制订单类型

**逻辑**:
```python
if not oco_enabled:
    # OCO 关闭：总是 MKT
    order_obj.order_type = "MKT"
else:
    # OCO 开启：尝试 MDP 获取价格
    # 成功 → LMT + bracket order
    # 失败 → 降级 MKT
```

**修改文件**:
- `src/trading/put_order.py` — 简化价格获取逻辑

---

### 2.5 bracket order 重复父单修复

**问题**: 旧代码先 `place_order()` 提交主单，再 `place_bracket_order()` 提交 bracket order（包含父单），导致重复

**修复**: 直接使用 bracket order，移除重复的 `place_order()`

**修改文件**:
- `src/trading/put_order.py` — 直接使用 `place_bracket_order()`

---

## 三、验证结果

### 3.1 bracket order 测试（07-22 00:10）

```
📊 候选池评估报告 — 
   建仓建议: ['AMAT', 'KLAC', 'LRCX', 'CAT']
   跳过重复信号: KLAC, AMAT, LRCX (已有 SUBMITTED 订单)
   ✅ 去重完成：4 → 1（过滤 3 个重复）

📌 信号无 price，用市价 889.969970703125 → limit_price=894.42
📌 OCO: CAT LMT=894.42 SL=804.98 TP=1073.3
✅ Bracket 订单已提交: parent=3, SL=4, TP=5
```

**订单状态**（`get-opened-orders`）:

| PermID | Symbol | Action | Type | Status |
|--------|--------|--------|------|--------|
| 1211088211 | CAT | BUY 10 | LMT | PreSubmitted |
| 1211088212 | CAT | SELL 10 | STP LMT | PreSubmitted |
| 1211088213 | CAT | SELL 10 | LMT | PreSubmitted |

---

### 3.2 去重测试（07-22 01:50）

```
⏭️ 跳过重复信号: AMAT (universe-refresh)，已有活跃订单
⏭️ 跳过重复信号: KLAC (universe-refresh)，已有活跃订单
⏭️ 跳过重复信号: LRCX (universe-refresh)，已有活跃订单
⏭️ 跳过重复信号: CAT (universe-refresh)，已有活跃订单
ℹ️ 所有信号均为重复，跳过写入
✅ 去重完成：4 → 0（过滤 4 个重复）
```

---

### 3.3 max_positions 测试（07-22 01:53）

修改 `max_positions=5` 后，新增 SCHW 标的：

```
📌 MDP 获取市价 99.94000244140625 → limit_price=100.44
📌 OCO: SCHW LMT=100.44 SL=90.4 TP=120.53
✅ SCHW BUY - Status=PreSubmitted, PermID=1211088232
```

**验证**: `max_positions` 配置生效，新增标的成功提交 bracket order ✓

---

## 四、Git Commits

| Commit | 描述 |
|--------|------|
| `dd4466e` | fix: 使用IBKR原始状态码 + 去重逻辑修复 + max_positions配置修复 |
| `fd5a059` | refactor: remove ambiguous top_n, use MAX_POSITIONS consistently |
| `70314fb` | fix: simplify price logic - oco_enabled controls MKT/LMT |
| `321070f` | fix: use symbol consistently in log messages |
| `952265a` | Revert "debug: add status mapping debug logs" |

---

## 五、待完成

1. [ ] **取消重复订单** — IBKR 中有 3 组 CAT bracket order（共 9 个订单），需要取消多余的
2. [ ] **Telegram 通知集成** — 替换 WeChat
3. [ ] **日熔断机制** — 日亏 ≥3% 停止当日交易
4. [ ] **DLR 频率优化** — 评估/改进交易频率

---

## 六、相关文件

- `src/trading/put_order.py` — 订单提交逻辑，状态码处理
- `src/trading/universe_selector.py` — 候选池选择器，max_positions 配置
- `skills/ibclient-all-in-one/ibclient.py` — universe-refresh 命令入口，去重逻辑
- `src/core/signal.py` — 信号生成，去重逻辑
- `config/config.py` — 配置定义
- `strategy/templates/strong_accumulation.yaml` — 策略模板配置
