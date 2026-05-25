"""月度进化报告 — 策略学习系统自动总结 (Phase 4 D49)

每月首周六由 cron job 触发, 生成 Markdown 报告,
总结过去一个月的策略绩效、学习引擎活动、回测验证结果。

输出: data/reports/evolution_{YYYYMM}.md
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from src.core.paths import get_path, ensure_dir
from src.core.performance import PerformanceTracker
from src.core.learning import ApprovalQueue

logger = logging.getLogger("EvolutionReport")


class EvolutionReport:
    """月度进化报告生成器"""

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = get_path("reports")
        self._data_dir = Path(data_dir)
        ensure_dir(path=self._data_dir)
        self.tracker = PerformanceTracker()
        self.logger = logger

    def generate(self, year: int = None, month: int = None) -> str:
        """生成月度进化报告

        Args:
            year: 年份 (默认当前月)
            month: 月份 (默认当前月)

        Returns:
            Markdown 报告内容
        """
        now = datetime.now()
        if year is None:
            year = now.year
        if month is None:
            month = now.month

        report_month = f"{year}-{month:02d}"
        lines = [
            f"# IBKR 策略进化月报 — {report_month}",
            f"",
            f"**生成时间**: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**数据范围**: 过去 90 天",
            f"",
            f"---",
            f"",
        ]

        # 1. 策略绩效概览
        lines.append("## 1. 策略绩效概览")
        lines.append("")
        lines.append("| 策略ID | 总信号 | 已执行 | 已平仓 | 胜率 | 平均收益 | 最大回撤 |")
        lines.append("|--------|--------|--------|--------|------|----------|----------|")

        strategy_ids = self._discover_strategies()
        if not strategy_ids:
            lines.append("| (无交易记录) | - | - | - | - | - | - |")
        else:
            for sid in sorted(strategy_ids):
                perf = self.tracker.get_performance(sid)
                if perf.total_signals > 0:
                    lines.append(
                        f"| {sid} | {perf.total_signals} | {perf.total_executed} "
                        f"| {perf.total_closed} | {perf.win_rate:.1%} "
                        f"| {perf.avg_pnl_pct:+.2f}% | {perf.max_drawdown_pct:.1f}% |"
                    )

        lines.append("")

        # 2. 市场状态统计
        lines.append("## 2. 市场状态分布")
        lines.append("")
        results = []
        for sid in strategy_ids:
            results.extend(self.tracker.get_results(sid, days=90))

        regimes: Dict[str, int] = {"BULL": 0, "BEAR": 0, "SIDEWAYS": 0}
        for r in results:
            regime = r.market_regime or "SIDEWAYS"
            regimes[regime] = regimes.get(regime, 0) + 1

        total = sum(regimes.values()) or 1
        for regime, count in sorted(regimes.items()):
            pct = count / total * 100
            lines.append(f"- **{regime}**: {count} 信号 ({pct:.0f}%)")

        lines.append("")

        # 3. 学习引擎活动
        lines.append("## 3. 学习引擎活动")
        lines.append("")
        try:
            queue = ApprovalQueue(data_dir=get_path("learning"))
            all_items = queue._items if hasattr(queue, '_items') else []
            month_items = [i for i in all_items if i.created_at and i.created_at[:7] == report_month]

            if month_items:
                approved = [i for i in month_items if i.status == "APPROVED"]
                rejected = [i for i in month_items if i.status == "REJECTED"]
                pending = [i for i in month_items if i.status == "PENDING"]

                lines.append(f"- **总建议**: {len(month_items)}")
                lines.append(f"- **已批准**: {len(approved)}")
                lines.append(f"- **已拒绝**: {len(rejected)}")
                lines.append(f"- **待审批**: {len(pending)}")
                lines.append("")
                lines.append("| 变更类型 | 策略 | 现状 → 建议 | 状态 |")
                lines.append("|----------|------|-------------|------|")
                for item in month_items:
                    lines.append(
                        f"| {item.change_type} | {item.strategy_id} "
                        f"| {item.current_value} → {item.proposed_value} "
                        f"| {item.status} |"
                    )
            else:
                lines.append("本月无学习引擎活动")
        except Exception as e:
            lines.append(f"(加载审批队列失败: {e})")

        lines.append("")

        # 4. 总结与建议
        lines.append("## 4. 总结与建议")
        lines.append("")

        top_strategies = []
        for sid in strategy_ids:
            perf = self.tracker.get_performance(sid)
            if perf.sample_size_sufficient:
                top_strategies.append((sid, perf.win_rate))
        top_strategies.sort(key=lambda x: x[1], reverse=True)

        if top_strategies:
            best = top_strategies[0]
            lines.append(f"- **最佳策略**: {best[0]} (胜率 {best[1]:.1%})")
            if len(top_strategies) > 1:
                worst = top_strategies[-1]
                lines.append(f"- **最差策略**: {worst[0]} (胜率 {worst[1]:.1%})")

        lines.append("")
        lines.append("---")
        lines.append(f"*报告由 EvolutionReport 自动生成*")

        report = "\n".join(lines)

        # 持久化
        filepath = self._data_dir / f"evolution_{report_month}.md"
        filepath.write_text(report, encoding="utf-8")
        self.logger.info("月度报告已生成: %s", filepath)

        return report

    def _discover_strategies(self) -> List[str]:
        """从 performances 数据目录发现所有有记录的策略

        新格式: performances/performance_YYYYMMDD.json
        文件结构: { strategy_id: [ records... ], ... }
        从所有日期文件中收集 strategy_id。
        """
        perf_dir = get_path("performances")
        if not perf_dir.exists():
            return []
        ids = set()
        for f in perf_dir.glob("performance_*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ids.update(data.keys())
            except Exception:
                continue
        return sorted(ids)
