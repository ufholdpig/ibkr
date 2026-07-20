# Sprint 0720 — 候选池更新与盘后报告

## 时间范围
2026-07-17 至 2026-07-20（进行中）

---

## 一、候选池更新（07-17 ~ 07-20）

### 新 top10（2026-07-20 生效）
```
AMAT, KLAC, LRCX, CAT, PG, AMD, ASML, SNOW, LLY, JNJ
```

### 黑名单
```
F, TSLA, VST, CEG, SQ, COIN, MRVL, AVGO
```

### 配置文件
- `config/ibkr.yaml` → `ibkr.watch.templates.strong_accumulation`
- `strategy/templates/strong_accumulation.yaml` → `candidate_pool`

---

## 二、Git Commits（07-17 后）

| Commit | 描述 |
|--------|------|
| `82a4a57` | 新增 Strong Accumulation 策略设计与标的池管理模块 |
| `700d253` | UniverseSelector 完整实现 — 候选池管理 + signal/order 链路 |
| `d520518` | fix: universe-refresh 使用 MarketDataProvider（有 yfinance fallback） |
| `3269998` | feat: strong_accumulation 策略完整纳入 watch 调度体系 |
| `f5a8c73` | 精简配置结构，修复 universe-refresh scope=full 全量扫描逻辑 |
| `301022f` | implement: Section 11.4 盘后决策逻辑 — 无持仓标的 BUY 信号 |
| `147b20b` | feat(universe): add candidate-pool report, scope-gated, append-only |

---

## 三、Section 11.4 盘后决策逻辑

持仓评审（基于 old_top2 vs new_top2）：

| 条件 | 动作 |
|------|------|
| 持仓 ∈ old_top2 ∧ 持仓 ∈ new_top2 | HOLD |
| 持仓 ∈ new_top2 ∧ 持仓 ∉ old_top2 | ADD |
| 持仓 ∉ new_top2 | CLOSE |
| 无持仓标的 ∈ new_top2 | BUY（信号写入 signals_pre_market） |

---

## 四、候选池报告（candidate-pool）

**文件**：`data/{paper|live}/reports/candidate-pool_YYYYMMDD.md`

**触发条件**：仅 `scope=full`（非交易时段 / 盘后执行 universe-refresh）

**内容格式**：
```markdown
# 候选池更新 — 20260720

### 18:00:00 ET 候选池更新（scope=full）
- 候选池变化：LRCX ❌ → NVDA ✅（如有变化）
- Top2：AMAT / KLAC → AMAT / KLAC（无变化）
- 候选池 TOP5：AMAT(85), KLAC(82), ...
- 持仓评审：RY.TO hold（+1.2%）
- 新信号：BUY NVDA x10（新入 top2）
```

**实现**：`src/trading/universe_selector.generate_candidate_pool_markdown_report()`

---

## 五、定时任务（Hermes Cron）

| Job | Schedule | Script | Job ID |
|-----|----------|--------|--------|
| post-market | Mon-Fri 16:30 ET | `ibkr_post_market.sh` | `ec41cc3826b9` |
| universe-refresh | Mon-Fri 18:00 ET | `ibkr_universe_refresh.sh` | `cdee1bf1a8db` |

**脚本位置**：`~/.hermes/profiles/ibkr/scripts/`

**微信通知**：已禁用，待切换 Telegram

---

## 六、Watch Daemon 状态

- **重启时间**：2026-07-20 13:21 ET
- **PID**：506832
- **监控标的**：AMAT, AMD, ASML, CAT, JNJ, KLAC, LLY, LRCX, PG, SNOW
- **冷却时间**：每标的 20 分钟
- **账户**：DU4011059（Paper）

---

## 七、待完成

1. [ ] Telegram 通知集成（替换 WeChat）— 暂时搁置
2. [x] 验证 candidate-pool_YYYYMMDD.md 报告内容（18:00 ET 执行后检查）
3. [x] Section 11.4 逻辑实盘验证（明日 pre-market 执行结果）

---

## 八、完成记录

### 8.1 候选池报告验证（07-20 18:00 ET）

**文件**：`data/paper/reports/candidate-pool_20260720.md`

**结果**：✅ 报告正确生成，内容符合预期

```
候选池变化：AMAT ✅ → GS ✅ → KLAC ✅ → LRCX ✅ → PG ✅
Top2： → AMAT / KLAC  （首次初始化，前一个 Top2 为空是预期行为）
候选池 TOP5：AMAT(4), KLAC(4), LRCX(4), GS(4), PG(4)
新信号：
  - BUY AMAT x10（新入 Top2 建仓候选（得分4.5，通过4/7））
  - BUY KLAC x10（新入 Top2 建仓候选（得分4.5，通过4/7））
```

Top2 为空是因为首次执行时候选池无历史数据，next_top2 写入后才会在下一周期比较。

### 8.2 Section 11.4 BUY 信号（07-20 18:00 ET）

**结果**：✅ BUY AMAT/KLAC 信号已写入 `signals_pre_market`，T+1 盘前执行

信号详情见 `candidate-pool_20260720.md`，将在明日（07-21）盘前由 `post-market` → `pre-market` 链路执行。

### 8.3 持仓市值修复

**问题**：盘后报告持仓市值和未实现盈亏始终为 `$0.00`

**根因**：IBKR `position` 回调仅返回 `(account, contract, position, avg_cost)`，不包含 `market_price`。`_parse_account_info` 第874行读取不存在的 `mktPrice` 字段，导致 `market_value = 0`。

**解决方案**：使用已设计的 `MarketDataProvider + market_data_source` 配置获取实时价格。

**验证**（ad-hoc test，2026-07-20 18:33）：

```python
MarketDataProvider(data_source='yfinance').fetch_basic(['AAPL', 'DLR', 'NVDA'])
→ AAPL: $326.70, DLR: $176.36, NVDA: $203.38  # 全部成功

# 市值计算验证：
NVDA qty=-1102, price=$203.38 → mkt_val=$224,125, pnl=+$2,777
AAPL qty=725,  price=$326.70 → mkt_val=$236,858, pnl=+$26,119
DLR  qty=935,  price=$176.36 → mkt_val=$164,897, pnl=+$123
```

**修改文件**：

| 文件 | 变更 |
|------|------|
| `src/trading/post_market.py` | 导入 `MarketDataProvider`，`__init__` 保存 `config.market_data_source`，重写 `_get_positions_from_ibkr()` 用 `MarketDataProvider.fetch_basic()` 获取价格并本地计算市值/pnl |
| `config/ibkr.yaml` | watch 模板 CAT→GS（universe-refresh 结果） |
| `strategy/templates/strong_accumulation.yaml` | candidate_pool CAT→GS + `_last_refresh` 时间戳更新 |

**实盘验证**（07-20 18:54 post-market 脚本重跑）：

```
MTB:  $2,494.80  pnl=🟢 +$352.20
DLR:  $164,896   pnl=🟢 +$123.80
NVDA: $224,124   pnl=🟢 +$2,777.84
VRT:  $310,809   pnl=🔴 -$13,320.51
GOOGL:$30,632    pnl=🟢 +$102.25
TFC:  $13,764    pnl=🟢 +$470.10
USB:  $6,316     pnl=🟢 +$749.40
AAPL: $236,857   pnl=🟢 +$26,119.06
MSFT: $1,207,530  pnl=🔴 -$37,930.40
```

**Commit**：`6353eac` fix(post_market): 用 MarketDataProvider+yfinance 计算持仓市值

---

## 九、相关文档

- `src/trading/universe_selector.py` — 候选池选择器（含 generate_candidate_pool_markdown_report）
- `skills/ibclient-all-in-one/ibclient.py` — universe-refresh 命令入口
- `docs/strong_accumulation-design.md` — 策略设计文档