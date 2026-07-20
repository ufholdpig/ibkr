# Strong Accumulation 策略设计方案

**创建时间**: 2026-07-18
**更新时间**: 2026-07-20（Section 12/13 历史遗留清理 + 市值修复记录）
**状态**: ✅ Phase 1 已完成，核心功能验证通过（候选池报告、Section 11.4 BUY 信号、持仓市值）
**目标**: 2-6个月周期挖掘强势股，在启动前进场

---

## 一、现状分析

### 1.1 过去一个月执行回顾 (2026/6/18 - 7/17)

| 指标 | 数值 |
|------|------|
| 信号总数 | 1,458 |
| BUY成交 | 317笔 ($357K) |
| SELL成交 | 2笔 |
| 失败订单 | 587笔 |
| 当前持仓 | DLR(100股) + VRT(65股) + NVDA(10股) |

#### 主要问题

1. **SELL失败率极高** — 403 FAILED + 180 UNKNOWN，仅4笔成功（因无持仓可卖）
2. **无平仓交易** — 所有持仓`is_closed=0`，止盈/止损从未触发
3. **买入过于频繁** — DLR连续9个交易日每天20笔（共870股），策略变成"定期定额"
4. **缺乏变现机制** — 买了$357K但无任何收益实现

### 1.2 趋势策略(trend_entry)执行情况

| 标的 | 策略 | 买入笔数 | 买入股数 | 总金额 | 当前状态 |
|------|------|---------|---------|--------|---------|
| GOOGL | 趋势入场v1 | 5笔 | 50股 | $18,393 | 50股(去向不明) |
| TFC | 趋势入场v2 | 26笔 | 260股 | $12,792 | 260股(去向不明) |

**问题**: 买了310股但从未变现，performance文件显示持仓为0（数据不一致或手动平仓）

### 1.3 现有trend_entry策略的设计矛盾

| 条件 | 意图 | 与强势股的冲突 |
|------|------|----------------|
| `ma_spread < 5%` | MA间距窄（早期趋势） | 强势股MA50远超MA200，spread=7-23% |
| `retrace_breakout` | 价格回撤MA50后反弹 | 强势股从不回撤到MA50，偏离>20% |
| `consolidation_breakout` | 横盘整理后突破 | 强势股价格远在MA50之上，从不整理 |

**结论**: 350+标的扫描，0个完全满足6个条件。策略设计与强势股特性相悖。

---

## 二、新策略设计

### 2.1 核心理念

> "在主力吸筹期进场，等爆发后持有"

**核心逻辑**:
- 传统trend_entry: 找"横盘整理后突破" → 弱势股才整理
- 新策略: 找"蓄力完成即将启动" → 强势股启动前信号

### 2.2 标的筛选条件

#### 硬性条件 (必须满足)

| 条件 | 阈值 | 说明 |
|------|------|------|
| 日均成交额 | > $5000万 | 流动性保障 |
| 市值 | > $50亿 | 规避小盘股 |
| 上市时间 | > 2年 | 避免新股 |
| 黑名单 | 不在列表 | 规避高风险标的 |

#### 技术面条件 (满足≥4个)

| 条件 | 阈值 | 意图 |
|------|------|------|
| MA200 slope | ±3° | 主力吸筹期（不涨不跌） |
| 价格距MA200 | >10% | 已脱离成本区 |
| 90日低点 | 高于180日前低点 | 底部抬高（拒绝新低） |
| 20日均线 | 向上且>50日均线 | 短期趋势向上 |
| RSI | 40-70 | 未超买有空间 |
| 近5日均量 | >90日均量×1.3 | 资金关注度提升 |
| 52周位置 | 30%-80% | 非高位非低位，有空间 |

#### 加分项 (满足越多越好)

- 行业龙头地位
- 机构持仓增加
- 近期有分析师上调评级
- 处于热门赛道（AI/云计算/金融科技/半导体）

### 2.3 建仓策略

```
阶段1: 首次信号
  条件: 技术面满足≥4个条件
  动作: 买入 10% 目标仓位（`default_position_size_pct`，见 Section 11.6）

阶段2: 回调加仓
  条件: 价格回撤5%且不破MA50
  动作: 加仓 10% 目标仓位

阶段3: 突破加仓
  条件: 创20日新高且成交量>2倍
  动作: 加仓 10% 目标仓位
```

**单标的最大仓位**: 30%（由 `universe_selector.opening.default_position_size_pct` 控制，初期保守）
> ⚠️ 文档旧版写"单标的100%"为过时描述，实际使用 Section 11.6 的配置值

### 2.4 风控参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 止损 | MA50下方5% | 跌破走人 |
| 止盈 | 移动止损 | 最高点回撤10%后止盈 |
| 最大持仓周期 | 6个月 | 超时强制平仓 |
| 最大持仓数 | 1只 | 专注单一标的 |

### 2.5 持有周期

| 阶段 | 周期 | 目标 |
|------|------|------|
| 蓄力期 | 2-8周 | 观察、积累仓位 |
| 爆发期 | 4-16周 | 持有至止盈触发 |
| 总计 | 2-6个月 | 完成一轮交易 |

---

## 三、标的池动态管理

### 3.1 两阶段管理拓扑

```
┌─────────────────────────────────────────────────────────┐
│  阶段1: 空仓期 — 潜力股筛选                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │  筛选维度:                                        │   │
│  │  1. 行业前景 (AI/云计算/半导体/金融科技...)        │   │
│  │  2. 技术面蓄力形态 (MA200走平、底部抬高、缩量)       │   │
│  │  3. 基本面信号 (营收增长、机构持仓增加)             │   │
│  │  4. 市场环境 (不受系统性风险)                      │   │
│  │                                                  │   │
│  │  标的池上限: 15只                                  │   │
│  │  监控频率: 每日                                    │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                          ↓ 触发建仓信号
┌─────────────────────────────────────────────────────────┐
│  阶段2: 建仓期 — 持仓监控为主                            │
│  ┌─────────────────────────────────────────────────┐   │
│  │  规则:                                            │   │
│  │  1. 建仓后，该标的保留在监控列表                   │   │
│  │  2. 不再新增标的进入标的池（专注持仓）              │   │
│  │  3. 其他标的仍可被观察，但不下单                    │   │
│  │  4. 平仓后 → 清空池 → 重新进入阶段1                │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 3.2 标的池刷新机制

```
每日盘后任务:
  ↓
获取全市场股票列表 (~350只)
  ↓
应用硬性过滤器 → ~100只
  ↓
计算技术面得分 → ~30只
  ↓
基本面验证 → ~20只
  ↓
排序取top15 → 标的池
  ↓
对比现有标的池
  ├─ 新标的进入观察
  ├─ 差标的重排或移出
  └─ 已有标的保留
```

### 3.3 动态调整规则

```
每周五收盘后评估:

情况1: 标的A已有建仓信号但未建仓
       → 继续观察，不出池

情况2: 标的B技术面恶化（不满足≥4个条件）
       → 降级到"观察名单"，不优先考虑

情况3: 标的C出现更优机会（条件更好）
       → 优先排序靠前

情况4: 标的D达到建仓条件且排在前列
       → 触发建仓 → 进入阶段2

情况5: 标的E超过6个月未触发信号
       → 移出标的池，重新筛选替换
```

---

## 四、执行时机与评估范围

### 4.1 核心设计原则

> **执行时机决定评估范围，信号生成职责按时间分离**

|| 时间窗口 | scope | 评估范围 | 信号生成 | 职责 |
|---------|-------|---------|---------|---------|
|| 盘前（<9:30 ET） | full | 全部24只板块龙头股 | ✅ → signals_pre_market | 扫描+刷新top10+信号 |
|| 盘中（9:30-16:00 ET） | pool_only | 候选池(top10) | ❌ | 仅评估，WatchDaemon负责信号 |
|| 盘后（≥16:00 ET） | full | 全部24只板块龙头股 | ✅ → signals_pre_market | 扫描+刷新top10+信号 |

**信号职责划分（关键）**：
- `universe-refresh scope=full`：盘后批量扫描 → 刷新top10 → 生成持仓管理信号(BUY/SELL) → T+1执行
- `universe-refresh scope=pool_only`：仅评估池内标的(re-rank)，不生成信号，报告仅供观察
- `WatchDaemon`（盘中wake模式）：实时监控 `templates.strong_accumulation` → conditions评估 → signals_intra_day → 当日执行

### 4.2 两部分刷新动作

|| 刷新部分 | 说明 | 执行条件 | 信号生成 |
|---------|------|---------|---------|
|| **池内标的再审核 (type-a)** | 对当前候选池内标的重新打分 | 始终执行 | ❌（仅报告） |
|| **池外标的评估 (type-b)** | 对全部24只板块龙头股做完整评估 | 仅 scope=full（盘前/盘后） | ✅ → signals_pre_market |

**scope=full 与 scope=pool_only 的本质区别**：前者发现新标的并刷新top10，后者仅在已有池内re-rank。

**资源开销预估**：

- 候选池标的（top10）：~10 次 API 请求，约 15 秒
- 全量板块龙头（24只）：~24 次 API 请求，约 30-60 秒

### 4.3 实现逻辑

```python
def determine_scope() -> str:
    """根据执行时间决定评估范围"""
    et = get_current_et_time()
    if et.hour < 9 or et.hour >= 16:
        return "full"       # 盘前/盘后：全量扫描24只板块龙头股 + 生成信号
    else:
        return "pool_only" # 盘中：仅扫描候选池(top10) + 不生成信号
```

> **注意**：scope=pool_only 时 WatchDaemon 全权负责盘中信号生成，两者时间上互斥（WatchDaemon sleep 时才执行 universe-refresh）

---

## 五、信号与订单链路

### 5.1 整体流程

```
universe-refresh scope=full（盘前/盘后 ≥16:00 或 <9:30 ET）
    │
    ├─ 确定评估范围：扫描全部24只板块龙头股
    │
    ├─ 获取市场数据
    │
    ├─ 评估候选标的，生成 PositionReview[]
    │
    ├─ 决定 PoolAction（8种）
    │
    ├─ 写入信号（仅非 HOLD action，scope=full 时）
    │       │
    │       └─ 复用 watch_daemon 链路
    │           ├─ 写入 signal_{YYYYMMDD_next}.json
    │           │   └─ section = "signals_pre_market"（盘后执行）
    │           │
    │           └─ 调用 pre_market.execute()
    │               ├─ 读取 signals_pre_market[]
    │               ├─ process_signals() → 订单
    │               ├─ 提交至 IBKR（pre-submit 状态）
    │               └─ 标记 processed=true
    │
    └─ IBKR 持有订单，9:30am EST 自动成交（T+1）

universe-refresh scope=pool_only（盘中 9:30-16:00 ET）
    │
    ├─ 确定评估范围：仅扫描候选池(top10)
    │
    ├─ 获取市场数据
    │
    ├─ 评估候选标的，生成 PositionReview[]
    │
    └─ 仅输出报告，不生成信号
        （WatchDaemon 全权负责盘中 signals_intra_day）

WatchDaemon（盘中 wake 模式）
    │
    ├─ 监控 templates.strong_accumulation (top10)
    │
    ├─ StrategyFactory 评估 strong_accumulation.yaml conditions
    │       （7个技术面，满足≥4个）
    │
    ├─ 条件满足 → 写入 signals_intra_day
    │       └─ 调用 intra_day.execute()
    │           ├─ 读取 signals_intra_day[]
    │           ├─ process_signals() → 订单
    │           └─ 提交至 IBKR（当日执行）
```

### 5.2 8种持仓决策映射

| PoolAction | 信号 action | 触发条件 |
|------------|-------------|---------|
| ADD | BUY | 已有持仓，需加仓 |
| OPEN | BUY | 无持仓，建议建仓 |
| HOLD | — | 不操作 |
| SKIP | — | 不操作 |
| REDUCE | SELL | 减仓（止盈/减分/不在池） |
| CLOSE | SELL | 清仓（止损/不在池） |

### 5.3 复用现有链路的关键

**任何触发源共享同一出口**：

| 触发源 | 写入位置 | execute() 调用 | 后续流程 |
|--------|---------|---------------|---------|
| `universe-refresh`（盘后 18:00） | signals_pre_market | `pre_market.execute()` | 订单→IBKR→次日开盘 |
| `universe-refresh`（盘中 14:00 人工） | signals_intra_day | `intra_day.execute()` | 同上，开盘可成交 |
| watch daemon 轮询信号 | 同上逻辑 | 同上 | 同上 |

**一个统一出口，三种触发方式共享同一结果处理链**（通知、绩效记录、冷却期标记等）。

---

## 六、持仓决策逻辑（8种）

### 6.1 决策矩阵

| 持仓状态 | 标的在池中 | 标的不在池中 | 得分变化 |
|---------|-----------|-------------|---------|
| **已有持仓** | HOLD（持有多日）或 ADD（刚进入池） | REDUCE（减半仓）或 CLOSE（清仓） | - |
| **无持仓** | OPEN（满足条件）或 SKIP（不满足） | 不评估 | - |

### 6.2 具体决策规则

```
对于每个标的：

if 有持仓:
    if 不在候选池:
        if 亏损 > stop_loss_pct: CLOSE（止损）
        else: REDUCE（减半仓）
    else:
        if 池内排名上升且 score >= threshold: ADD（加仓）
        else: HOLD（持有）

else（无持仓）:
    if 在候选池:
        if score >= threshold: OPEN（建仓）
        else: SKIP（观察）
    else:
        SKIP（不在池中）
```

### 6.3 信号生成规则

- **ADD / OPEN** → 生成 `BUY` 信号，数量为建议股数
- **REDUCE / CLOSE** → 生成 `SELL` 信号，数量为建议股数
- **HOLD / SKIP** → 不生成信号

---

## 七、系统实现架构

### 7.1 模块结构

```
src/trading/
├── universe_selector.py    # 候选池管理器（已完成）
│   ├── UniverseSelector    # 核心评估类
│   ├── Candidate           # 单标的评估结果
│   ├── PositionReview      # 持仓审核结果（含 action）
│   ├── PoolAction          # 8种决策枚举
│   └── UniverseSelectorReport  # 完整报告
│
├── pre_market.py           # 盘前执行（已存在）
├── intra_day.py            # 盘中执行（已存在）
└── watch_daemon.py         # 实时监控（已存在）
```

### 7.2 UniverseSelector 核心接口

```python
class UniverseSelector:
    def __init__(self, candidates: list, config: dict, market_data_provider, positions: list):
        """candidates: 候选标的列表（来自 ibkr.yaml）"""

    def evaluate(self) -> UniverseSelectorReport:
        """执行完整评估，返回报告"""

    def _evaluate_positions(self) -> list[PositionReview]:
        """评估现有持仓（8种决策）"""

    def _rank_candidates(self) -> list[Candidate]:
        """对候选标的排序"""

    def _decide_actions(self) -> list[PoolAction]:
        """决定每个持仓的 action"""

class PositionReview:
    symbol: str
    action: PoolAction          # 8种决策
    suggested_qty_change: int   # 正=买，负=卖
    suggested_reason: str
    score: float
    current_price: float

class PoolAction(Enum):
    HOLD = "hold"
    ADD = "add"
    REDUCE = "reduce"
    CLOSE = "close"
    OPEN = "open"
    SKIP = "skip"
```

### 7.3 配置文件结构

```yaml
# config/ibkr.yaml

watch:
  candidate_pool:
    - NVDA
    - AVGO
    - MRVL
    # ... 共25只

universe_selector:
  required_passing: 4          # 满足≥4个技术条件
  min_score_threshold: 4.0     # 最低得分
  max_positions: 2             # 最大持仓数（实验期保守）
  take_profit_pct: 20          # 止盈20%
  stop_loss_pct: 10           # 止损10%
  blacklist:
    - TSLA
    - CEG
    - VST
    - F
```

---

## 八、ibclient 命令集成

### 8.1 命令接口

```bash
# 手动执行（任意时刻）
ibclient universe-refresh

# 输出示例
=== Universe Selector Report (2026-07-18) ===
Pool: 25 candidates, 1 positions
Scope: FULL (post-market)

Position Reviews:
  NVDA  ADD     +100  (score=7.2, in_pool_rank=1)
  VRT   HOLD    0     (score=5.1, in_pool_rank=3)
  GOOGL OPEN    +100  (score=6.8, in_pool_rank=2)

Signals Generated: 2 BUY
Written to: signals_pre_market (pre_market execute triggered)
```

### 8.2 执行时机判断

```python
def get_execution_scope() -> str:
    """根据当前时间判断评估范围"""
    et = get_current_et_time()
    if et.hour < 9 or et.hour >= 16:
        return "full"      # 盘前/盘后：评估全部
    else:
        return "pool_only" # 盘中：仅池内标的
```

---

## 九、策略分工

### 9.1 所有策略定位

| 策略 | 标的池 | 监控周期 | 目的 | 状态 |
|------|--------|---------|------|------|
| **strong_accumulation** | 动态top15 | 每日筛选 | 2-6个月中长期 | **实现中** |
| trend_entry | 固定配置 | 持续监控 | 趋势跟踪 | 维护 |
| dip_buy | 固定配置 | 持续监控 | 回调买入实验 | 实验性 |
| ma_buy | 固定配置 | 持续监控 | 均线买入实验 | 实验性 |

### 9.2 关键区别

| 维度 | 新策略 | 其他策略 |
|------|--------|---------|
| 标的池 | 动态，基于全市场筛选 | 固定配置 |
| 监控周期 | 2-6个月 | 持续 |
| 策略重心 | 标的选择+持仓管理 | 信号生成 |
| 建仓后 | 专注持仓，不新增标的 | 继续生成信号 |

---

## 十、风险与限制

### 10.1 系统风险

| 风险 | 应对 |
|------|------|
| 市场系统性下跌 | 止损纪律，MA50下方5% |
| 流动性不足 | 仅选日均成交>$5000万标的 |
| 持仓集中 | 单标的最大仓位控制 |

### 10.2 策略风险

| 风险 | 应对 |
|------|------|
| 标的池筛选偏差 | 定期评估和调整标准 |
| 持有周期过长 | 6个月强制平仓 |
| 频繁换仓 | 建仓后锁定标的池 |

### 10.3 操作风险

| 风险 | 应对 |
|------|------|
| 手动干预 | 保持策略纪律，不轻易干预 |
| 数据延迟 | 使用实时数据源 |
| 情绪化决策 | 所有决策基于规则 |

---

## 十一、Top2 决策框架（2026-07-19 新增）

### 11.1 核心理念

> **排名本身就是信号**：不跨标的比较得分，只看 top2 的变化。

- 候选池按评分排序后，**top2** 是最关键的决策依据
- top2 变化 → 信号生成
- top2 不变 → 持仓稳定

### 11.2 盘中 vs 盘后执行

| 维度 | 盘中（Intra-day） | 盘后（Post-market） |
|------|------------------|-------------------|
| 执行时机 | 9:30-16:00 | 16:00 后 |
| 评估范围 | 仅池内标的 | 全量 ~350 标的 |
| 标的增删 | **不增删**（固定池） | **增删**（取 top N） |
| 可能信号 | hold / reduce / add / close | buy / add / hold / reduce / close |

### 11.3 盘中决策逻辑

```
old_top2 = 旧 ibkr.yaml 的前2名
new_top2 = 新排序后的前2名

对于持仓标的：
    if symbol in new_top2:
        if symbol in old_top2:
            → HOLD（top2 内，排名稳定）
        else:
            → ADD（新入 top2，排名上升）
    else:
        → REDUCE（退出 top2，排名下降）
        （极端反转：三振出局 → CLOSE）
```

### 11.4 盘后决策逻辑

```
old_top2 = 旧 ibkr.yaml 的前2名
new_top2 = 新排序后的前2名

对于持仓标的：
    if symbol in new_top2:
        if symbol in old_top2:
            → HOLD
        else:
            → ADD（新入 top2）
    else:
        → CLOSE（被踢出新池）

对于无持仓标的：
    if symbol in new_top2:
        if new_top2[0] == symbol or new_top2[1] == symbol:
            → BUY（top2 标的建仓）
        else:
            → 无（不在 top2，不建仓）
    else:
        → 无
```

### 11.5 信号类型

|| 信号 | 说明 | 适用场景 |
|---|------|------|---------|
| **buy** | 建仓 | 无持仓 + 新入 top2 |
| **add** | 加仓 | 有持仓 + 新入 top2 |
| **hold** | 持有 | top2 内且无变化 |
| **reduce** | 减仓 | 退出 top2（盘中） |
| **close** | 清仓 | 退出 top2（盘后）/ 三振出局 |

### 11.6 候选池容量

```yaml
candidate_pool:
  capacity: 10    # 盘后刷新后保留 top N（设计文档描述，实际硬编码为 top10）
```

- 盘后执行：评估全量 → 取 top N → 更新候选池
- 盘中执行：不增删标的，只调整排名
- `capacity` 字段在 `strategy/templates/strong_accumulation.yaml` 中定义但未被代码引用，top10 由 `universe_selector.opening.top_n` 控制

### 11.7 信号去重机制

#### 11.7.1 核心原则

> **daily-based**："已经做过的就是做了，向前看，明天是新起点"
> **同一标的 + 同一策略（strategy_id）+ 同一交易日 = 最多一条订单**

#### 11.7.2 去重规则

**在写入新信号前，检查对应交易日 order.json：**

| order 状态 | 新信号动作 |
|-----------|-----------|
| SUBMITTED / FILLED | **跳过**（IBKR 已在处理或已成交，不重复） |
| FAILED / CANCELLED | **允许**（IBKR 未接受，可重试） |
| 无对应 order 记录 | **允许**（首次生成） |

**判断维度**：`同一标的 + 同一 strategy_id + 同一交易日`

#### 11.7.3 覆盖场景

| 场景 | 说明 | 处理 |
|------|------|------|
| 一天内多次运行 | 每次 run 都检查 order.json | SUBMITTED/FILLED 跳过 |
| 盘中再次评估 | 连接 IBKR → `on_order_status` 自动回填 FILLED | 发现 FILLED 跳过 |
| 盘后首次运行 | T+1 order.json 不存在 | 直接写入（无去重） |
| 盘后第二次运行 | T+1 order.json 已有 FILLED | 跳过 |

**BUY 和 SELL 共用同一条去重规则。** SUBMITTED/FILLED 说明当时 IBKR 已接受该信号，无需重复。

#### 11.7.4 为什么用 order.json 而非 signal.json 做去重

| 文件 | 作用 | 去重依据 |
|------|------|---------|
| signal.json | 信号生成记录，不区分 pending/processed | ❌ 不适合 |
| order.json | 信号执行记录，含 status + signal metadata，IBKR 连接时自动回填 FILLED | ✅ 唯一依据 |

#### 11.7.5 signal → order 流程

```
策略引擎生成信号
    ↓
signal_YYYYMMDD.json（立即写入）
    ↓
watch daemon 监听到新信号 → 去重检查 order.json
    ↓
通过 → order_YYYYMMDD.json（立即写入 + 提交 IBKR）
    ↓
IBKR 回填状态（SUBMITTED → FILLED / FAILED）
```

---

## 十二、TODO

- [x] **Top2 决策框架** — ✅ 已完成（2026-07-19），Section 11 详细描述
- [x] **盘中/盘后执行区分** — ✅ 已完成，scope=full/pool_only
- [x] **ibclient universe-refresh 命令** — ✅ 已完成，cron job `cdee1bf1a8db` 每日 18:00 ET 运行
- [x] **post-market 集成** — ✅ 已完成，PostMarketExecutor._get_positions_from_ibkr() 已调用市值计算
- [x] **ibkr.yaml 动态更新** — ✅ 已完成，候选池写入 `strategy/templates/strong_accumulation.yaml`
- [x] **去重机制** — ✅ 已工作，基于 order.json SUBMITTED/FILLED 跳过重复信号
- [x] **watch daemon 整合** — ✅ 已工作（PID 506832，盘前手动唤醒 + 盘后信号执行）
- [ ] **持仓预检** — 提交订单前检查 live 持仓（real account 风控），当前 paper 账户暂不需要

---

## 十三、实现状态

### 已完成 ✅

| 模块 | 文件 | 状态 | 备注 |
|------|------|------|------|
| UniverseSelector + Top2 框架 | `src/trading/universe_selector.py` | ✅ | Section 11.4 BUY 信号已验证（07-20） |
| WatchlistManager | `src/core/watchlist_manager.py` | ✅ | |
| 配置集成 | `config/ibkr.yaml` + `config/config.py` | ✅ | market_data_source 已支持 yfinance |
| ibclient 命令框架 | `skills/ibclient-all-in-one/ibclient.py` | ✅ | universe-refresh 已接入 cron |
| 持仓市值计算 | `src/trading/post_market.py` | ✅ | MarketDataProvider + yfinance（07-20 修复） |
| 候选池报告 | `universe_selector.generate_candidate_pool_markdown_report()` | ✅ | candidate-pool_YYYYMMDD.md 追加报告 |
| ibkr.yaml 动态更新 | `strategy/templates/strong_accumulation.yaml` | ✅ | 盘后 18:00 ET 写入 |

### 进行中 🟡

| 模块 | 说明 | 状态 |
|------|------|------|
| — | 无 | — |

### 待实现

| 模块 | 说明 | 优先级 |
|------|------|--------|
| 持仓预检 | 提交订单前 live 持仓检查（real account 风控） | 🟡 中（paper 暂不需要） |

### 07-20 关键验证记录

- **候选池报告**（18:00 ET）：Top2=AMAT/KLAC，BUY AMAT/KLAC x10 已写入 signals_pre_market
- **市值修复**：post-market 持仓市值/盈亏从 `$0.00` 修正为实时 yfinance 价格
- **Commit**: `6353eac` fix(post_market): 用 MarketDataProvider+yfinance 计算持仓市值

---

**文档版本**: v1.2（Top2 决策框架，2026-07-19）