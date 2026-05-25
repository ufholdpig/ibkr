"""策略绩效追踪器 -- 闭环数据管道

PerformanceTracker 负责策略执行结果的写入、读取和聚合计算，
是自我学习系统的数据基础。

持久化方案: JSON文件 (与 signal_*.json / order_*.json 一致)
存储格式: data/<mode>/performances/performance_YYYYMMDD.json
每日一个文件，按 strategy_id 分组，记录列表存放该策略当日所有结果。
每条记录第一个字段为 signal_time。

官方参考:
- 设计文档: docs/strategy/strategy_design_v2-glm-5.1.md Section 6.2
- 数据模型: src/core/models.py (StrategyResult, StrategyPerformance)
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from src.core.models import StrategyResult, StrategyPerformance, MarketRegime
from src.core.paths import get_path, get_performance_file, ensure_dir, get_trading_date

logger = logging.getLogger("PerformanceTracker")


class PerformanceTracker:
    """策略绩效追踪器

    职责:
    1. record_result() -- 记录单次策略执行结果
    2. get_results() -- 读取指定策略的近期执行结果
    3. get_performance() -- 聚合计算策略绩效 (供 StrategyLearner 使用)
    4. update_fill() -- 订单成交后更新成交价和滑点
    5. close_position() -- 持仓平仓后关闭记录

    数据目录: data/<mode>/performances/
    文件命名: performance_YYYYMMDD.json
    文件结构: { strategy_id: [ { signal_time, result_id, ... }, ... ], ... }
    """

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is not None:
            self._data_dir = Path(data_dir)
        else:
            self._data_dir = get_path("performances")
        ensure_dir(path=self._data_dir)
        logger.info(f"PerformanceTracker 初始化, 数据目录: {self._data_dir}")

    # =========================================================================
    # 写入
    # =========================================================================

    def record_result(self, result: StrategyResult) -> Path:
        """记录单次策略执行结果

        按 strategy_id 分组写入当日 performance_YYYYMMDD.json 文件。

        Args:
            result: StrategyResult 实例

        Returns:
            写入的文件路径
        """
        if not result.result_id:
            raise ValueError("StrategyResult.result_id 不能为空")
        if not result.strategy_id:
            raise ValueError("StrategyResult.strategy_id 不能为空")

        filepath = Path(get_performance_file())
        ensure_dir(path=filepath.parent)

        data = self._load_day_file(filepath)

        sid = result.strategy_id
        if sid not in data:
            data[sid] = []
        data[sid].append(result.to_dict())

        self._save_day_file(filepath, data)

        kind = "反事实" if result.is_shadow else "实盘"
        logger.info(
            "记录策略结果 [%s]: %s/%s %s %s @ %.2f",
            kind, result.strategy_id, result.result_id,
            result.action, result.symbol, result.signal_price,
        )
        return filepath

    def record_shadow_trade(self, result: StrategyResult) -> Path:
        """记录反事实交易 (被风控拦截但仍追踪后验收益, Phase 3 D36)

        与 record_result 共享同一数据目录和持久化格式，
        区别仅在于 is_shadow=True 标记。
        """
        result.is_shadow = True
        return self.record_result(result)

    def update_fill(
        self,
        result_id: str,
        strategy_id: str,
        actual_fill_price: float,
    ) -> bool:
        """订单成交后更新成交价和滑点

        Args:
            result_id: 结果记录ID
            strategy_id: 策略ID
            actual_fill_price: IBKR 实际成交价

        Returns:
            是否成功更新
        """
        result = self._find_result(strategy_id, result_id)
        if result is None:
            logger.warning(f"未找到记录: {strategy_id}/{result_id}")
            return False

        result.actual_fill_price = actual_fill_price
        if result.signal_price > 0:
            result.slippage_pct = (
                (actual_fill_price - result.signal_price) / result.signal_price * 100
            )
        else:
            result.slippage_pct = 0.0

        self._update_result_in_files(strategy_id, result_id, result)
        logger.info(
            f"更新成交价: {strategy_id}/{result_id} "
            f"fill={actual_fill_price} slippage={result.slippage_pct:.3f}%"
        )
        return True

    def close_position(
        self,
        result_id: str,
        strategy_id: str,
        close_price: float,
        realized_pnl: float = 0.0,
        benchmark_return: float = 0.0,
    ) -> bool:
        """持仓平仓后关闭记录

        Args:
            result_id: 结果记录ID
            strategy_id: 策略ID
            close_price: 平仓价格
            realized_pnl: 已实现盈亏金额
            benchmark_return: 同期基准(SPX)收益率

        Returns:
            是否成功关闭
        """
        result = self._find_result(strategy_id, result_id)
        if result is None:
            logger.warning(f"未找到记录: {strategy_id}/{result_id}")
            return False

        result.close_price = close_price
        result.realized_pnl = realized_pnl
        result.close_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result.benchmark_return = benchmark_return
        result.is_closed = True

        if result.actual_fill_price > 0 and result.action == "BUY":
            result.realized_pnl_pct = (
                (close_price - result.actual_fill_price) / result.actual_fill_price * 100
            )
        elif result.actual_fill_price > 0 and result.action == "SELL":
            result.realized_pnl_pct = realized_pnl

        result.alpha = result.realized_pnl_pct - benchmark_return

        result.is_winner = result.realized_pnl_pct > 0

        if result.signal_time:
            try:
                signal_dt = datetime.strptime(result.signal_time, "%Y-%m-%d %H:%M:%S")
                close_dt = datetime.strptime(result.close_time, "%Y-%m-%d %H:%M:%S")
                result.holding_days = (close_dt - signal_dt).days
            except (ValueError, TypeError):
                result.holding_days = 0

        self._update_result_in_files(strategy_id, result_id, result)
        logger.info(
            f"平仓记录: {strategy_id}/{result_id} "
            f"close={close_price} pnl={realized_pnl:+.2f} alpha={result.alpha:+.2f}%"
        )
        return True

    # =========================================================================
    # 读取
    # =========================================================================

    def get_results(self, strategy_id: str, days: int = 90,
                    include_shadow: bool = False) -> List[StrategyResult]:
        """读取指定策略的近期执行结果

        扫描最近 days 天的 performance_YYYYMMDD.json 文件，
        从中提取指定 strategy_id 的记录。

        Args:
            strategy_id: 策略ID
            days: 回溯天数 (默认90天)
            include_shadow: 是否包含反事实交易 (默认False)

        Returns:
            按时间排序的 StrategyResult 列表
        """
        cutoff = datetime.now() - timedelta(days=days)
        results = []

        for filepath in sorted(self._data_dir.glob("performance_*.json")):
            # 从文件名提取日期: performance_YYYYMMDD.json
            date_str = filepath.stem.replace("performance_", "")
            try:
                file_date = datetime.strptime(date_str, "%Y%m%d")
            except ValueError:
                continue
            if file_date < cutoff:
                continue

            data = self._load_day_file(filepath)
            if strategy_id not in data:
                continue

            for rec in data[strategy_id]:
                try:
                    result = StrategyResult(**rec)
                    if not include_shadow and result.is_shadow:
                        continue
                    results.append(result)
                except Exception as e:
                    logger.warning(f"解析记录失败: {filepath.name}/{rec.get('result_id','?')}, 错误: {e}")

        results.sort(key=lambda r: r.signal_time or "")
        return results

    def get_performance(
        self,
        strategy_id: str,
        min_sample_trades: int = 5,
    ) -> StrategyPerformance:
        """聚合计算策略绩效

        由 StrategyLearner.analyze_strategy() 调用,
        产出 StrategyPerformance 供学习引擎决策。

        Args:
            strategy_id: 策略ID
            min_sample_trades: 最小样本门槛 (不足时不调参)

        Returns:
            StrategyPerformance 聚合绩效
        """
        results = self.get_results(strategy_id, days=365)
        closed = [r for r in results if r.is_closed]

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not closed:
            return StrategyPerformance(
                strategy_id=strategy_id,
                total_signals=len(results),
                last_updated=now_str,
                sample_size_sufficient=False,
            )

        total_executed = len([r for r in results if r.actual_fill_price > 0])
        total_closed = len(closed)

        wins = [r for r in closed if r.is_winner]
        losses = [r for r in closed if not r.is_winner]
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / total_closed if total_closed > 0 else 0.0

        pnl_pcts = [r.realized_pnl_pct for r in closed if r.realized_pnl_pct != 0]
        alphas = [r.alpha for r in closed if r.alpha != 0]
        holding_days_list = [r.holding_days for r in closed if r.holding_days > 0]

        avg_pnl_pct = sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0.0
        avg_alpha = sum(alphas) / len(alphas) if alphas else 0.0
        avg_holding_days = (
            sum(holding_days_list) / len(holding_days_list) if holding_days_list else 0.0
        )

        max_drawdown_pct = self._calc_max_drawdown(closed)

        max_consecutive_losses = self._calc_max_consecutive_losses(closed)

        slippages = [r.slippage_pct for r in closed if r.slippage_pct != 0]
        avg_slippage = sum(slippages) / len(slippages) if slippages else 0.0

        bull_win_rate, bear_win_rate, sideways_win_rate = (
            self._calc_regime_win_rates(closed)
        )

        last_30d_win_rate = self._calc_window_win_rate(closed, days=30)
        last_90d_win_rate = self._calc_window_win_rate(closed, days=90)
        last_14d_win_rate = self._calc_window_win_rate(closed, days=14)

        return StrategyPerformance(
            strategy_id=strategy_id,
            total_signals=len(results),
            total_executed=total_executed,
            total_closed=total_closed,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=round(win_rate, 4),
            avg_pnl_pct=round(avg_pnl_pct, 4),
            avg_alpha=round(avg_alpha, 4),
            avg_holding_days=round(avg_holding_days, 1),
            max_drawdown_pct=round(max_drawdown_pct, 4),
            max_consecutive_losses=max_consecutive_losses,
            avg_slippage_pct=round(avg_slippage, 4),
            bull_win_rate=round(bull_win_rate, 4),
            bear_win_rate=round(bear_win_rate, 4),
            sideways_win_rate=round(sideways_win_rate, 4),
            last_30d_win_rate=round(last_30d_win_rate, 4),
            last_90d_win_rate=round(last_90d_win_rate, 4),
            last_14d_win_rate=round(last_14d_win_rate, 4),
            last_updated=now_str,
            sample_size_sufficient=total_closed >= min_sample_trades,
        )

    # =========================================================================
    # 内部方法: 文件级读写
    # =========================================================================

    @staticmethod
    def _load_day_file(filepath: Path) -> Dict[str, List[dict]]:
        """加载一个 performance_YYYYMMDD.json 文件

        Returns:
            { strategy_id: [ record_dict, ... ], ... }
        """
        if not filepath.exists():
            return {}
        try:
            return json.loads(filepath.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"加载绩效文件失败: {filepath.name}, 错误: {e}")
            return {}

    @staticmethod
    def _save_day_file(filepath: Path, data: Dict[str, List[dict]]) -> None:
        """保存一个 performance_YYYYMMDD.json 文件"""
        filepath.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _find_result(self, strategy_id: str, result_id: str) -> Optional[StrategyResult]:
        """在所有日期文件中查找一条记录

        优先从当日文件查找，然后回溯90天。
        """
        # 先查当日
        for filepath in sorted(self._data_dir.glob("performance_*.json"), reverse=True):
            data = self._load_day_file(filepath)
            if strategy_id not in data:
                continue
            for rec in data[strategy_id]:
                if rec.get("result_id") == result_id:
                    try:
                        return StrategyResult(**rec)
                    except Exception:
                        return None
        return None

    def _update_result_in_files(
        self, strategy_id: str, result_id: str, updated: StrategyResult
    ) -> None:
        """在日期文件中更新一条记录 (update_fill / close_position 调用)"""
        for filepath in sorted(self._data_dir.glob("performance_*.json"), reverse=True):
            data = self._load_day_file(filepath)
            if strategy_id not in data:
                continue
            for i, rec in enumerate(data[strategy_id]):
                if rec.get("result_id") == result_id:
                    data[strategy_id][i] = updated.to_dict()
                    self._save_day_file(filepath, data)
                    return

    # =========================================================================
    # 内部方法: 统计计算 (与原版一致)
    # =========================================================================

    @staticmethod
    def _calc_max_drawdown(closed: List[StrategyResult]) -> float:
        """计算最大回撤百分比"""
        if not closed:
            return 0.0

        sorted_results = sorted(closed, key=lambda r: r.close_time or "")
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for r in sorted_results:
            cumulative += r.realized_pnl_pct
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        return max_dd

    @staticmethod
    def _calc_max_consecutive_losses(closed: List[StrategyResult]) -> int:
        """计算最大连续亏损次数"""
        sorted_results = sorted(closed, key=lambda r: r.close_time or "")
        max_streak = 0
        current_streak = 0

        for r in sorted_results:
            if not r.is_winner:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        return max_streak

    @staticmethod
    def _calc_regime_win_rates(
        closed: List[StrategyResult],
    ) -> tuple:
        """按市场状态细分计算胜率"""
        regime_groups: Dict[str, List[StrategyResult]] = {
            "BULL": [],
            "BEAR": [],
            "SIDEWAYS": [],
        }
        for r in closed:
            regime = r.market_regime or "SIDEWAYS"
            if regime in regime_groups:
                regime_groups[regime].append(r)

        rates = []
        for regime_key in ["BULL", "BEAR", "SIDEWAYS"]:
            group = regime_groups[regime_key]
            if group:
                wins = len([r for r in group if r.is_winner])
                rates.append(wins / len(group))
            else:
                rates.append(0.0)

        return tuple(rates)

    @staticmethod
    def _calc_window_win_rate(
        closed: List[StrategyResult], days: int
    ) -> float:
        """计算指定时间窗口内的胜率"""
        cutoff = datetime.now() - timedelta(days=days)
        recent = []
        for r in closed:
            if r.close_time:
                try:
                    close_dt = datetime.strptime(r.close_time, "%Y-%m-%d %H:%M:%S")
                    if close_dt >= cutoff:
                        recent.append(r)
                except ValueError:
                    pass

        if not recent:
            return 0.0

        wins = len([r for r in recent if r.is_winner])
        return wins / len(recent)
