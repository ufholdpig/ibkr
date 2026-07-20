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

1. [ ] Telegram 通知集成（替换 WeChat）
2. [ ] 验证 candidate-pool_YYYYMMDD.md 报告内容（18:00 ET 执行后检查）
3. [ ] Section 11.4 逻辑实盘验证（明日 pre-market 执行结果）

---

## 八、相关文档

- `src/trading/universe_selector.py` — 候选池选择器（含 generate_candidate_pool_markdown_report）
- `skills/ibclient-all-in-one/ibclient.py` — universe-refresh 命令入口
- `docs/strong_accumulation-design.md` — 策略设计文档