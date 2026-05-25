"""回测引擎 — 参数变更的验证门禁 (Phase 4 D39-D41)

职责:
1. 加载历史K线数据, 按日回放
2. 复用条件引擎 (_eval_condition) 评估策略信号
3. 模拟交易含滑点(0.05%)和手续费($0.005/股)
4. 产出 BacktestResult (与 StrategyResult 同维度)
5. compare() 对比 baseline vs proposed 参数

设计约束:
- 使用真实历史数据 (从 IBKR 或 yfinance 获取)
- 与策略引擎共享条件求值逻辑
- 不依赖真实 IBKR 连接 (可离线运行)
"""

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from src.core.strategy import (
 MarketData, _parse_condition_tree, _eval_condition, _collect_target_symbols,
)
from src.core.conditions import _find_market_data
from src.core.models import Bar, BacktestResult, BacktestTrade, ComparisonResult
from src.core.market_data import MarketDataProvider

logger = logging.getLogger("BacktestEngine")


class BacktestEngine:
    def __init__(self, config: dict = None):
        cfg = config or {}
        self.slippage_pct = cfg.get("slippage_pct", 0.05)
        self.fee_per_share = cfg.get("fee_per_share", 0.005)
        self.logger = logger

    def run(
        self,
        strategy_config: dict,
        historical_data: Dict[str, List[Bar]],
        start_date: str = "",
        end_date: str = "",
    ) -> BacktestResult:
        """运行单策略回测

        Args:
            strategy_config: YAML策略dict (同strategy/strategies/*.yaml格式)
            historical_data: {symbol: [Bar]} — 每个标的日K线(时间升序)
            start_date: 回测开始日期 "YYYY-MM-DD", 空=数据起点
            end_date: 回测结束日期 "YYYY-MM-DD", 空=数据终点

        Returns:
            BacktestResult 回测结果
        """
        strategy_id = strategy_config.get("strategy_id", "unknown")
        action_cfg = strategy_config.get("action", {})
        condition_tree = _parse_condition_tree(strategy_config.get("conditions"))

        if not historical_data:
            self.logger.warning("无历史数据, 跳过回测: %s", strategy_id)
            return BacktestResult(strategy_id=strategy_id)

        # 构建按日对齐的多标的数据视图 {date: {symbol: Bar}}
        daily_data = self._align_daily_data(historical_data, start_date, end_date)
        if not daily_data:
            return BacktestResult(strategy_id=strategy_id)

        dates = sorted(daily_data.keys())

        trades: List[BacktestTrade] = []
        open_trades: Dict[str, BacktestTrade] = {}
        equity_curve: List[float] = [10000.0]  # 初始虚拟资金
        equity = 10000.0
        peak = 10000.0
        max_dd = 0.0

        # 按日回放
        for i, date in enumerate(dates):
            bars = daily_data[date]
            symbols = list(bars.keys())

            # 计算当日 market_data (含技术指标)
            market_data_list = []
            for sym in symbols:
                sym_bars = historical_data.get(sym, [])
                cutoff = self._find_bar_index(sym_bars, date)
                if cutoff < 2:
                    continue
                window = sym_bars[: cutoff + 1]
                indicators = self._compute_indicators_static(window)
                price = bars[sym].close
                md = MarketData(
                    symbol=sym,
                    price=price,
                    volume=bars[sym].volume,
                    ma_20=indicators.get("ma_20"),
                    ma_50=indicators.get("ma_50"),
                    rsi_14=indicators.get("rsi_14"),
                    volume_avg_20d=indicators.get("volume_avg_20d"),
                    change_1d_pct=indicators.get("change_1d_pct"),
                    change_5d_pct=indicators.get("change_5d_pct"),
                    change_20d_pct=indicators.get("change_20d_pct"),
                )
                market_data_list.append(md)

            if not market_data_list:
                continue

            # 检查是否需要平仓: 已有开仓 -> 遍历条件看是否触发平仓
            for sym in list(open_trades.keys()):
                md = _find_market_data(market_data_list, sym)
                if md is None:
                    continue
                action_type = action_cfg.get("type", "")
                is_sell_strategy = "SELL" in action_type
                condition_met = _eval_condition(
                    condition_tree, sym, md.price, 0.0, market_data_list
                )
                if condition_met and is_sell_strategy:
                    self._close_trade(open_trades, sym, md.price, date, trades)

            # 检查是否需要开仓: 遍历策略目标标的
            target_symbols = _collect_target_symbols(
                strategy_config.get("conditions"), action_cfg, {}
            )
            for sym in target_symbols:
                if sym in open_trades:
                    continue
                md = _find_market_data(market_data_list, sym)
                if md is None:
                    continue
                condition_met = _eval_condition(
                    condition_tree, sym, md.price, 0.0, market_data_list
                )
                if condition_met:
                    is_buy = "BUY" in action_cfg.get("type", "")
                    if is_buy:
                        self._open_trade(open_trades, sym, md.price, date, strategy_id, condition_tree)

            # 更新权益曲线
            total_position_value = sum(
                t.entry_price * t.quantity for t in open_trades.values()
            )
            equity = 10000.0 + total_position_value + sum(
                t.pnl_amount for t in trades if t.is_closed
            )
            equity_curve.append(equity)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # 平仓所有未平仓头寸(以最后价格)
        last_bars = daily_data[dates[-1]]
        for sym, trade in list(open_trades.items()):
            last_price = last_bars.get(sym, last_bars[list(last_bars.keys())[0]]).close
            self._close_trade(open_trades, sym, last_price, dates[-1], trades)

        # 计算绩效指标
        return self._compute_results(strategy_id, trades, equity_curve, max_dd)

    def compare(
        self,
        baseline_config: dict,
        proposed_config: dict,
        historical_data: Dict[str, List[Bar]],
        period: str = "180d",
    ) -> ComparisonResult:
        """对比回测：当前参数 vs 建议参数

        Args:
            baseline_config: 当前策略配置
            proposed_config: 建议的新策略配置
            historical_data: 历史数据
            period: 回测周期 (如 "180d", "1y", "all")

        Returns:
            ComparisonResult 对比结果
        """
        baseline = self.run(baseline_config, historical_data)
        proposed = self.run(proposed_config, historical_data)

        pnl_improvement = proposed.total_pnl_pct - baseline.total_pnl_pct
        risk_change = proposed.max_drawdown_pct - baseline.max_drawdown_pct

        if pnl_improvement > 0 and risk_change <= 0:
            recommendation = "ADOPT"
        elif pnl_improvement > 0 and risk_change > 0:
            recommendation = "REVIEW"
        else:
            recommendation = "REJECT"

        return ComparisonResult(
            baseline=baseline,
            proposed=proposed,
            pnl_improvement=round(pnl_improvement, 2),
            risk_change=round(risk_change, 2),
            recommendation=recommendation,
        )

    # =========================================================================
    # 内部方法
    # =========================================================================

    @staticmethod
    def _align_daily_data(
        historical_data: Dict[str, List[Bar]],
        start_date: str = "",
        end_date: str = "",
    ) -> Dict[str, Dict[str, Bar]]:
        """将多标的历史K线按日期对齐"""
        if not historical_data:
            return {}

        # 收集所有日期
        all_dates: Dict[str, dict] = {}
        for symbol, bars in historical_data.items():
            for bar in bars:
                d = bar.time[:10]
                if d not in all_dates:
                    all_dates[d] = {}
                all_dates[d][symbol] = bar

        if start_date:
            all_dates = {d: v for d, v in all_dates.items() if d >= start_date}
        if end_date:
            all_dates = {d: v for d, v in all_dates.items() if d <= end_date}

        return dict(sorted(all_dates.items()))

    @staticmethod
    def _find_bar_index(bars: List[Bar], date: str) -> int:
        """在bars列表中查找指定日期(含)的最后一个索引"""
        for i in range(len(bars) - 1, -1, -1):
            if bars[i].time[:10] <= date:
                return i
        return -1

    def _open_trade(
        self,
        open_trades: Dict[str, BacktestTrade],
        symbol: str,
        price: float,
        date: str,
        strategy_id: str,
        condition_tree,
    ):
        """开仓模拟: 含滑点"""
        fill_price = round(price * (1 + self.slippage_pct / 100), 2)
        quantity = 10  # 固定数量, 简化
        trade = BacktestTrade(
            symbol=symbol,
            action="BUY",
            quantity=quantity,
            entry_date=date,
            entry_price=fill_price,
            reason=f"backtest:{strategy_id}",
        )
        open_trades[symbol] = trade
        self.logger.debug("回测开仓: %s @ %.2f on %s", symbol, fill_price, date)

    def _close_trade(
        self,
        open_trades: Dict[str, BacktestTrade],
        symbol: str,
        price: float,
        date: str,
        trades: List[BacktestTrade],
    ):
        """平仓模拟: 含滑点"""
        trade = open_trades.pop(symbol, None)
        if trade is None:
            return
        fill_price = round(price * (1 - self.slippage_pct / 100), 2)
        trade.exit_date = date
        trade.exit_price = fill_price
        trade.pnl_pct = round((fill_price - trade.entry_price) / trade.entry_price * 100, 2)
        trade.pnl_amount = round(trade.pnl_pct / 100 * trade.entry_price * trade.quantity, 2)
        trade.is_closed = True
        trade.is_winner = trade.pnl_pct > 0
        trades.append(trade)
        self.logger.debug("回测平仓: %s @ %.2f on %s PnL=%.2f%%", symbol, fill_price, date, trade.pnl_pct)

    @staticmethod
    def _compute_indicators_static(bars: List[Bar]) -> Dict[str, Optional[float]]:
        """从K线列表计算技术指标 (纯函数, 不依赖 MarketDataProvider)"""
        closes = [b.close for b in bars]
        volumes = [float(b.volume) for b in bars]

        def sma(values, period):
            return sum(values[-period:]) / period if len(values) >= period else None

        def rsi(closes, period=14):
            if len(closes) < period + 1:
                return None
            avg_gain = 0.0
            avg_loss = 0.0
            for i in range(1, period + 1):
                diff = closes[i] - closes[i - 1]
                if diff > 0:
                    avg_gain += diff
                else:
                    avg_loss -= diff
            avg_gain /= period
            avg_loss /= period
            for i in range(period + 1, len(closes)):
                diff = closes[i] - closes[i - 1]
                if diff > 0:
                    avg_gain = (avg_gain * (period - 1) + diff) / period
                    avg_loss = (avg_loss * (period - 1)) / period
                else:
                    avg_loss = (avg_loss * (period - 1) - diff) / period
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return 100.0 - 100.0 / (1.0 + rs)

        change_1d = None
        if len(closes) >= 2:
            change_1d = (closes[-1] - closes[-2]) / closes[-2] * 100
        change_5d = None
        if len(closes) >= 6:
            change_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
        change_20d = None
        if len(closes) >= 21:
            change_20d = (closes[-1] - closes[-21]) / closes[-21] * 100

        return {
            "ma_20": sma(closes, 20),
            "ma_50": sma(closes, 50),
            "rsi_14": rsi(closes, 14),
            "volume_avg_20d": sma([float(v) for v in volumes], 20),
            "change_1d_pct": change_1d,
            "change_5d_pct": change_5d,
            "change_20d_pct": change_20d,
        }

    def _compute_results(
        self,
        strategy_id: str,
        trades: List[BacktestTrade],
        equity_curve: List[float],
        max_drawdown_pct: float,
    ) -> BacktestResult:
        """从交易列表计算绩效汇总"""
        closed = [t for t in trades if t.is_closed]
        total = len(closed)
        wins = len([t for t in closed if t.is_winner])
        losses = len([t for t in closed if not t.is_winner])

        win_rate = wins / total if total > 0 else 0.0
        pnl_pcts = [t.pnl_pct for t in closed]
        total_pnl = sum(pnl_pcts) if pnl_pcts else 0.0
        avg_pnl = sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0.0

        sharpe = self._calc_sharpe(equity_curve)

        return BacktestResult(
            strategy_id=strategy_id,
            total_trades=total,
            win_count=wins,
            loss_count=losses,
            win_rate=round(win_rate, 4),
            total_pnl_pct=round(total_pnl, 2),
            avg_pnl_pct=round(avg_pnl, 2),
            max_drawdown_pct=round(max_drawdown_pct, 2),
            sharpe_ratio=round(sharpe, 2),
            trades=trades,
            equity_curve=[round(e, 2) for e in equity_curve],
        )

    @staticmethod
    def _calc_sharpe(equity_curve: List[float], risk_free: float = 0.02) -> float:
        """计算夏普比率 (日收益)"""
        if len(equity_curve) < 2:
            return 0.0
        returns = []
        for i in range(1, len(equity_curve)):
            r = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            returns.append(r)
        if not returns:
            return 0.0
        avg_ret = sum(returns) / len(returns)
        var = sum((r - avg_ret) ** 2 for r in returns) / len(returns)
        std = var ** 0.5
        if std == 0:
            return 0.0
        daily_rf = risk_free / 252
        return (avg_ret - daily_rf) / std * (252 ** 0.5)
