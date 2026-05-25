"""策略学习引擎 — 自我进化系统核心

职责:
1. analyze_strategy() — 分析单策略绩效，产出变更建议
2. analyze_by_type() — 同类型跨策略分析
3. _weight_heuristic() — 权重调整启发式
4. _lifecycle_heuristic() — 生命周期转换启发式

Phase 3 D27-D34 交付物

约束:
- 所有变更必须先提交审批(ApprovalItem)，不能直接修改YAML
- 小样本(默认<5笔)跳过分析，避免过拟合噪声
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.core.models import (
    ApprovalItem,
    ApprovalStatus,
    StrategyPerformance,
    StrategyResult,
    StrategyState,
)
from src.core.paths import get_path, ensure_dir
from src.core.performance import PerformanceTracker

logger = logging.getLogger(__name__)


class StrategyLearner:
    """策略学习引擎

    分析策略绩效数据，生成权重调整/生命周期转换等变更建议，
    所有变更通过 ApprovalQueue 提交审批。
    """

    def __init__(
        self,
        tracker: Optional[PerformanceTracker] = None,
        data_dir: Optional[Path] = None,
    ):
        if data_dir is None:
            data_dir = get_path("learning")
        self._data_dir = Path(data_dir)
        ensure_dir(path=self._data_dir)
        self._tracker = tracker or PerformanceTracker()
        self._queue: "ApprovalQueue" = None
        logger.info("StrategyLearner 初始化, 数据目录: %s", self._data_dir)

    @property
    def queue(self) -> "ApprovalQueue":
        if self._queue is None:
            self._queue = ApprovalQueue(data_dir=self._data_dir)
        return self._queue

    def analyze_strategy(
        self,
        strategy_id: str,
        strategy_config: Optional[dict] = None,
    ) -> List[ApprovalItem]:
        """分析单策略绩效，产出变更建议

        Args:
            strategy_id: 策略ID
            strategy_config: 策略当前配置(含weight/state等)，用于提取current_value

        Returns:
            变更建议列表 (每个建议对应一个 ApprovalItem)
        """
        perf = self._tracker.get_performance(strategy_id)
        if not perf.sample_size_sufficient:
            logger.info(
                "策略 %s 样本不足(%d笔, 需要≥%d)，跳过分析",
                strategy_id, perf.total_closed, self._tracker.get_performance.__defaults__[0] if hasattr(self._tracker.get_performance, '__defaults__') else 5,
            )
            return []

        items: List[ApprovalItem] = []

        weight_item = self._weight_heuristic(perf, strategy_config)
        if weight_item:
            items.append(weight_item)

        lifecycle_item = self._lifecycle_heuristic(perf, strategy_config)
        if lifecycle_item:
            items.append(lifecycle_item)

        return items

    def analyze_by_type(
        self,
        strategy_type: str,
        strategy_configs: Dict[str, dict],
    ) -> Dict[str, List[ApprovalItem]]:
        """同类型跨策略分析

        比较同一类型(如 dip_buy)下所有策略的绩效，
        识别表现最差/最好的策略，产出批量建议。

        Args:
            strategy_type: 策略类型 (如 dip_buy, ma_buy)
            strategy_configs: {strategy_id: config} 映射

        Returns:
            {strategy_id: [ApprovalItem]} 映射
        """
        results: Dict[str, List[ApprovalItem]] = {}
        type_perfs: List[Tuple[str, StrategyPerformance]] = []

        for sid, config in strategy_configs.items():
            perf = self._tracker.get_performance(sid)
            if perf.sample_size_sufficient:
                type_perfs.append((sid, perf))

        if len(type_perfs) < 2:
            logger.info(
                "类型 %s 可比较策略不足(%d个)，跳过跨策略分析",
                strategy_type, len(type_perfs),
            )
            return results

        type_perfs.sort(key=lambda x: x[1].win_rate, reverse=True)
        best_sid, best_perf = type_perfs[0]
        worst_sid, worst_perf = type_perfs[-1]

        logger.info(
            "跨策略分析 [%s]: 最佳=%s(胜率%.1f%%) 最差=%s(胜率%.1f%%)",
            strategy_type, best_sid, best_perf.win_rate * 100,
            worst_sid, worst_perf.win_rate * 100,
        )

        for sid, config in strategy_configs.items():
            items = self.analyze_strategy(sid, config)
            if items:
                results[sid] = items

        return results

    def _weight_heuristic(
        self,
        perf: StrategyPerformance,
        config: Optional[dict] = None,
    ) -> Optional[ApprovalItem]:
        """权重调整启发式

        基于绩效调整策略权重:
        - win_rate > 65% 且 样本>10 → 建议权重+0.2 (上限2.0)
        - win_rate < 40% 且 样本>10 → 建议权重-0.2 (下限0.1)
        - 其余情况不调整

        Returns:
            ApprovalItem 或 None (无需调整)
        """
        if config is None:
            return None

        current_weight = config.get("weight", 1.0)
        win_rate = perf.win_rate
        total = perf.total_closed

        if win_rate > 0.65 and total >= 10 and current_weight < 2.0:
            proposed = min(round(current_weight + 0.2, 1), 2.0)
            reason = (
                f"胜率{win_rate*100:.0f}% > 65% (样本{total}笔)，"
                f"建议上调权重 {current_weight} → {proposed}"
            )
        elif win_rate < 0.40 and total >= 10 and current_weight > 0.1:
            proposed = max(round(current_weight - 0.2, 1), 0.1)
            reason = (
                f"胜率{win_rate*100:.0f}% < 40% (样本{total}笔)，"
                f"建议下调权重 {current_weight} → {proposed}"
            )
        else:
            return None

        return ApprovalItem(
            item_id=f"weight_{perf.strategy_id}_{uuid.uuid4().hex[:6]}",
            strategy_id=perf.strategy_id,
            change_type="WEIGHT_ADJUST",
            current_value=current_weight,
            proposed_value=proposed,
            reason=reason,
            confidence=min(round(win_rate, 2), 0.9),
            evidence={"win_rate": win_rate, "total_closed": total},
        )

    def _lifecycle_heuristic(
        self,
        perf: StrategyPerformance,
        config: Optional[dict] = None,
    ) -> Optional[ApprovalItem]:
        """生命周期转换启发式

        基于绩效判断是否需要状态转换:
        - win_rate > 70% 且 样本>15 → 建议升级 ACTIVE → HIGH_CONV
        - 连续亏损>5笔 → 建议降级 ACTIVE → UNDER_REVIEW
        - max_drawdown > 20% → 建议降级 ACTIVE → UNDER_REVIEW

        Returns:
            ApprovalItem 或 None (无需转换)
        """
        if config is None:
            return None

        current_state = config.get("state", "ACTIVE")
        win_rate = perf.win_rate
        total = perf.total_closed
        max_dd = perf.max_drawdown_pct
        consecutive_losses = perf.max_consecutive_losses

        if current_state == "ACTIVE" and win_rate > 0.70 and total >= 15:
            proposed = StrategyState.HIGH_CONV.value
            reason = (
                f"胜率{win_rate*100:.0f}% > 70% (样本{total}笔)，"
                f"建议升级 {current_state} → {proposed}"
            )
            confidence = min(round(win_rate, 2), 0.85)
        elif current_state in ("ACTIVE", "HIGH_CONV") and consecutive_losses >= 5:
            proposed = StrategyState.UNDER_REVIEW.value
            reason = (
                f"连续亏损{consecutive_losses}笔 ≥ 5，"
                f"建议降级 {current_state} → {proposed}"
            )
            confidence = 0.7
        elif current_state in ("ACTIVE", "HIGH_CONV") and max_dd > 20.0:
            proposed = StrategyState.UNDER_REVIEW.value
            reason = (
                f"最大回撤{max_dd:.1f}% > 20%，"
                f"建议降级 {current_state} → {proposed}"
            )
            confidence = 0.6
        else:
            return None

        return ApprovalItem(
            item_id=f"lifecycle_{perf.strategy_id}_{uuid.uuid4().hex[:6]}",
            strategy_id=perf.strategy_id,
            change_type="LIFECYCLE_TRANSITION",
            current_value=current_state,
            proposed_value=proposed,
            reason=reason,
            confidence=confidence,
            evidence={
                "win_rate": win_rate,
                "total_closed": total,
                "max_drawdown_pct": max_dd,
                "max_consecutive_losses": consecutive_losses,
            },
        )


class ApprovalQueue:
    """审批队列 — 持久化 + 本地摘要 + 微信通知

    约束(C1-C7):
    - C1: 所有变更必须先提交审批
    - C2: 审批通过后才能应用
    - C3: 超时自动REJECT(默认72h)
    - C4: 通知失败不阻塞
    - C5: 每次通知合并多条(减少打扰)
    - C6: 冷却期不重复通知
    - C7: 重试最多3次
    """

    DEFAULT_TTL_HOURS = 72
    NOTIFICATION_COOLDOWN_MINUTES = 60
    MAX_RETRIES = 3

    def __init__(self, data_dir: Path, backtest_engine=None):
        self._data_dir = Path(data_dir)
        self._backtest_engine = backtest_engine
        ensure_dir(path=self._data_dir)
        self._items_file = self._data_dir / "approval_queue.json"
        self._items: List[ApprovalItem] = []
        self._load()
        logger.info(
            "ApprovalQueue 初始化, 队列中有 %d 个待处理项", len(self._items)
        )

    @property
    def pending_items(self) -> List[ApprovalItem]:
        self._purge_expired()
        return [i for i in self._items if i.status == "PENDING"]

    def submit(self, item: ApprovalItem) -> None:
        """提交审批项 (C1)"""
        if not item.expires_at:
            expires = datetime.now() + timedelta(hours=self.DEFAULT_TTL_HOURS)
            item.expires_at = expires.strftime("%Y-%m-%d %H:%M:%S")
        item.status = "PENDING"
        self._items.append(item)
        self._save()
        logger.info("提交审批: %s (%s)", item.item_id, item.change_type)

    def approve(self, item_id: str, run_backtest: bool = True) -> bool:
        """批准审批项 (C2)

        Args:
            item_id: 审批项ID
            run_backtest: 是否在批准前运行回测验证 (需要配置 backtest_engine)

        Returns:
            是否成功批准
        """
        for item in self._items:
            if item.item_id == item_id and item.status == "PENDING":
                # 可选: 回测验证门禁 (Phase 4 D44)
                if run_backtest and self._backtest_engine is not None:
                    self._run_backtest_gate(item)

                item.status = ApprovalStatus.APPROVED.value
                item.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._save()
                logger.info("审批通过: %s", item_id)
                return True
        logger.warning("审批项不存在或已处理: %s", item_id)
        return False

    def _run_backtest_gate(self, item: ApprovalItem):
        """回测门禁: 在批准前验证变更的预期效果"""
        try:
            from src.core.backtesting import BacktestEngine
            engine = self._backtest_engine

            # 加载原策略配置
            from src.core.paths import get_path
            import yaml
            strategy_dir = get_path("data").parent / "strategy" / "templates"
            yaml_file = None
            for f in strategy_dir.glob("*.yaml"):
                cfg = yaml.safe_load(f.read_text())
                if cfg and cfg.get("strategy_id") == item.strategy_id:
                    yaml_file = f
                    break
            if yaml_file is None:
                return

            config = yaml.safe_load(yaml_file.read_text())
            proposed = dict(config)

            # 应用提议变更
            if item.change_type == "WEIGHT_ADJUST":
                proposed["weight"] = item.proposed_value
            elif item.change_type == "LIFECYCLE_TRANSITION":
                proposed["state"] = item.proposed_value

            # 获取历史数据 (从 IBKR 或 yfinance)
            from src.core.market_data import MarketDataProvider
            from src.core.client import IBKRClient
            from config.config import load_config
            historical_data = {}
            target_symbols = set()
            for cond in (config.get("conditions") or []):
                s = cond.get("symbol") if isinstance(cond, dict) else None
                if s:
                    target_symbols.add(s)
            ticker = config.get("action", {}).get("ticker", "")
            if ticker:
                target_symbols.add(ticker)

            for sym in target_symbols:
                try:
                    client = IBKRClient(load_config())
                    client.connect()
                    bars = client.get_historical_data(sym, days=180)
                    if bars:
                        historical_data[sym] = bars
                    client.disconnect()
                except Exception:
                    try:
                        import yfinance as yf
                        df = yf.download(sym, period="6mo", progress=False)
                        if not df.empty:
                            historical_data[sym] = []
                            for i in range(len(df)):
                                row = df.iloc[i]
                                from src.core.models import Bar
                                historical_data[sym].append(Bar(
                                    time=str(df.index[i].date()),
                                    open=float(row["Open"]),
                                    high=float(row["High"]),
                                    low=float(row["Low"]),
                                    close=float(row["Close"]),
                                    volume=int(row["Volume"]),
                                ))
                    except Exception:
                        continue

            if not historical_data:
                return

            comparison = engine.compare(config, proposed, historical_data)
            logger.info(
                "回测门禁 [%s]: baseline=%.2f%% proposed=%.2f%% 改进=%.2f%% 推荐=%s",
                item.strategy_id,
                comparison.baseline.total_pnl_pct if comparison.baseline else 0,
                comparison.proposed.total_pnl_pct if comparison.proposed else 0,
                comparison.pnl_improvement,
                comparison.recommendation,
            )
            item.evidence["backtest_result"] = {
                "baseline_pnl": comparison.baseline.total_pnl_pct if comparison.baseline else 0,
                "proposed_pnl": comparison.proposed.total_pnl_pct if comparison.proposed else 0,
                "improvement": comparison.pnl_improvement,
                "recommendation": comparison.recommendation,
            }
        except Exception as e:
            logger.warning("回测门禁异常 (不阻塞审批): %s", e)

    def reject(self, item_id: str) -> bool:
        """拒绝审批项"""
        for item in self._items:
            if item.item_id == item_id and item.status == "PENDING":
                item.status = ApprovalStatus.REJECTED.value
                item.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._save()
                logger.info("审批拒绝: %s", item_id)
                return True
        logger.warning("审批项不存在或已处理: %s", item_id)
        return False

    def get_pending_summary(self) -> str:
        """生成本地摘要文本 (C5)"""
        pending = self.pending_items
        if not pending:
            return "✅ 暂无待审批变更"

        lines = ["📋 策略进化审批队列", "=" * 30]
        for item in pending:
            expires = item.expires_at or "N/A"
            lines.append(
                f"  [{item.item_id}] {item.change_type}"
                f"\n    策略: {item.strategy_id}"
                f"\n    变更: {item.current_value} → {item.proposed_value}"
                f"\n    原因: {item.reason}"
                f"\n    置信度: {item.confidence:.0%}"
                f"\n    过期: {expires}"
                f"\n"
            )
        return "\n".join(lines)

    def notify_pending(self, wechat_bot=None) -> None:
        """发送微信通知 (C4, C5, C6, C7)

        Args:
            wechat_bot: 可选的微信通知函数, 接受 str 消息参数
        """
        pending = self.pending_items
        if not pending:
            return

        now = datetime.now()
        need_notify = []
        for item in pending:
            if item.notification_status == "NOT_SENT":
                need_notify.append(item)
            elif item.notification_status == "FAILED" and item.notification_attempts < self.MAX_RETRIES:
                need_notify.append(item)

        if not need_notify:
            return

        summary = self.get_pending_summary()
        if wechat_bot:
            try:
                wechat_bot(summary)
                for item in need_notify:
                    item.notification_status = "SENT"
                    item.notification_attempts += 1
                self._save()
                logger.info(
                    "微信通知已发送: %d 条待审批项", len(need_notify)
                )
            except Exception as e:
                logger.error("微信通知失败: %s", e)
                for item in need_notify:
                    item.notification_status = "FAILED"
                    item.notification_attempts += 1
                self._save()
        else:
            logger.info("跳过微信通知(bot未配置), 本地摘要:\n%s", summary)

    def _purge_expired(self) -> None:
        """处理过期项 (C3)"""
        now = datetime.now()
        changed = False
        for item in self._items:
            if item.status != "PENDING" or not item.expires_at:
                continue
            try:
                expires = datetime.strptime(
                    item.expires_at, "%Y-%m-%d %H:%M:%S"
                )
                if now >= expires:
                    item.status = ApprovalStatus.EXPIRED.value
                    item.resolved_at = now.strftime("%Y-%m-%d %H:%M:%S")
                    changed = True
                    logger.info("审批过期自动REJECT: %s", item.item_id)
            except (ValueError, TypeError):
                pass
        if changed:
            self._save()

    def _load(self) -> None:
        if self._items_file.exists():
            try:
                data = json.loads(self._items_file.read_text(encoding="utf-8"))
                self._items = [ApprovalItem(**d) for d in data]
            except Exception as e:
                logger.warning("加载审批队列失败: %s", e)
                self._items = []

    def _save(self) -> None:
        self._items_file.write_text(
            json.dumps(
                [i.to_dict() for i in self._items],
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
