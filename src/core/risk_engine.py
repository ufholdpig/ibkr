"""
风控模块 (Risk Engine)

功能:
1. RiskEngine: 执行 TFSA 合规检查
2. 仓位限制检查 (单持仓≤20%)
3. 交易频率检查 (<80 次/年)
4. 禁止当日冲销 (Day Trading)
5. 禁止卖空 (Short Selling)
6. 账户类型验证 (Paper 绕过 / TFSA 强制执行)

设计原则:
- 所有检查前置，失败直接拒绝
- 明确错误原因，便于用户理解
- 配置化参数，无需修改代码
- Paper 账户 (DU 前缀) 日志警告但不强制拦截
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from pathlib import Path

from config.config import RiskConfig, get_instrument_registry
from src.core.logger import get_logger
from src.core.paths import get_path, get_data_mode

logger = get_logger(__name__)


class RiskDecision:
    """风险决策结果"""
    ALLOWED = "ALLOWED"
    REJECTED = "REJECTED"
    WARNING = "WARNING"


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    decision: str
    rule_name: str
    message: str
    details: Dict = None

    def is_allowed(self) -> bool:
        return self.decision == RiskDecision.ALLOWED

    def is_rejected(self) -> bool:
        return self.decision == RiskDecision.REJECTED


class RiskEngine:
    """
    风控引擎：执行所有 TFSA 合规检查

    检查项:
    1. 仓位上限检查 (单持仓≤N%)
    2. 交易频率检查 (<N 次/年)
    3. 禁止当日冲销
    4. 禁止卖空 (Short Selling)

    Paper 账户 (DU 前缀) 自动绕过强制拦截，仅日志警告。
    """

    def __init__(self, config: RiskConfig = None):
        self.config = config or RiskConfig()
        self.logger = get_logger("RiskEngine")
        self._account_id: str | None = None
        self._is_paper: bool = True
        self._yearly_trades: List[datetime] = []
        self._daily_trades: List[datetime] = []
        self._trades_loaded: bool = False
        # v2: _daily_trades持久化路径
        self._trade_state_path: Path = get_path("data") / get_data_mode() / "risk_daily_trades.json"
        self._current_positions: Dict[str, float] = {}

    def set_account_info(self, account_id: str, net_liquidation: float,
                         positions: Dict[str, float]):
        self._account_id = account_id
        self._is_paper = account_id.startswith("DU") if account_id else True
        self._net_liquidation = net_liquidation
        self._current_positions = positions or {}
        if self._is_paper:
            self.logger.info("Paper 账户 (%s) — TFSA 风控仅记录不拦截", account_id)
        else:
            self.logger.info("实盘账户 (%s) — TFSA 风控全量生效", account_id)

    def load_trade_history(self, order_dir: Path | None = None):
        if self._trades_loaded:
            return
        if order_dir is None:
            order_dir = get_path("orders")
        if not order_dir.exists():
            self._trades_loaded = True
            return
        current_year = datetime.now().year
        trades: List[dict] = []
        for f in sorted(order_dir.glob("order_*.json")):
            try:
                data = json.loads(f.read_text())
            except Exception:
                continue
            for entry in (data.get("orders_intra_day", []) +
                          data.get("orders_pre_market", [])):
                ts = entry.get("generated", "")
                try:
                    dt = datetime.fromisoformat(ts)
                except (ValueError, TypeError):
                    continue
                if dt.year == current_year:
                    sig = entry.get("signal", {})
                    trades.append({
                        "time": dt,
                        "symbol": sig.get("symbol", ""),
                        "action": sig.get("action", ""),
                    })
        self._yearly_trades = trades
        today = datetime.now().date()
        self._daily_trades = [t for t in trades if t["time"].date() == today]
        # v2: 补充从持久化JSON加载(RE-02)，防止重启归零
        persisted = self._load_daily_trades()
        if persisted and len(persisted) > len(self._daily_trades):
            self._daily_trades = persisted
            self.logger.info("从持久化补充 %d 笔今日交易", len(persisted))
        self._trades_loaded = True
        self.logger.info("已加载 %d 笔年内交易 (%d 笔今日)", len(self._yearly_trades), len(self._daily_trades))

    def _get_notional_value(self, symbol: str, quantity: int, price: float) -> float:
        """计算名义价值 = quantity * price * multiplier"""
        registry = get_instrument_registry()
        spec = registry.get(symbol)
        return quantity * price * spec.notional_multiplier

    def _is_futures_symbol(self, symbol: str) -> bool:
        registry = get_instrument_registry()
        return registry.get(symbol).is_futures

    def precheck_order(self, symbol: str, action: str, quantity: int,
                       price: float = 0.0,
                       positions_map: Dict[str, float] | None = None,
                       net_liquidation: float = 0.0) -> List[RiskCheckResult]:
        results = []

        if self.config.tfsa_limitation:
            is_fut = self._is_futures_symbol(symbol)
            if not is_fut:
                results.append(self._check_short_sell(action))
                results.append(self._check_yearly_trades())
                results.append(self._check_day_trading(symbol, action))

        pos = positions_map or self._current_positions
        if self.config.position_limit_pct > 0 and action == "BUY" and price > 0:
            nl = net_liquidation or self._net_liquidation
            results.append(self._check_position_limit(symbol, quantity, price, pos, nl))

        if self.config.max_order_value_pct > 0 and price > 0:
            nl = net_liquidation or self._net_liquidation
            results.append(self._check_order_value(symbol, action, quantity, price, nl))

        all_passed = all(r.is_allowed() for r in results)
        if self._is_paper:
            if not all_passed:
                rejected = [r for r in results if r.is_rejected()]
                for r in rejected:
                    self.logger.warning("[Paper 绕过] %s: %s", r.rule_name, r.message)
                return [RiskCheckResult(RiskDecision.ALLOWED, "PAPER_BYPASS",
                                        f"Paper 账户绕过 TFSA 检查 ({len(rejected)} 项)")]
            return results

        if all_passed:
            self.logger.info("风控检查通过: %s %s x%s", action, symbol, quantity)
        else:
            rejected = [r for r in results if r.is_rejected()]
            for r in rejected:
                self.logger.warning("风控拦截: %s — %s", r.rule_name, r.message)
        return results

    def _check_short_sell(self, action: str) -> RiskCheckResult:
        if action == "SELL":
            return RiskCheckResult(
                decision=RiskDecision.ALLOWED,
                rule_name="SHORT_SELL",
                message="允许卖出（需检查持仓，非裸卖空）"
            )
        return RiskCheckResult(
            decision=RiskDecision.ALLOWED,
            rule_name="SHORT_SELL",
            message="买入操作，不涉及卖空"
        )

    def _check_order_value(self, symbol: str, action: str, quantity: int, price: float,
                           net_liquidation: float) -> RiskCheckResult:
        if net_liquidation <= 0:
            return RiskCheckResult(
                decision=RiskDecision.WARNING,
                rule_name="ORDER_VALUE_LIMIT",
                message=f"净值 {net_liquidation} 无法计算订单价值占比",
            )
        order_value = self._get_notional_value(symbol, quantity, price)
        max_value = net_liquidation * (self.config.max_order_value_pct / 100.0)
        if order_value > max_value:
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                rule_name="ORDER_VALUE_LIMIT",
                message=f"订单价值超限: ${order_value:.2f} > 上限 ${max_value:.2f} "
                        f"({self.config.max_order_value_pct:.0f}% of ${net_liquidation:.2f})",
                details={"symbol": symbol, "order_value": order_value,
                         "max_value": max_value, "net_liquidation": net_liquidation}
            )
        return RiskCheckResult(
            decision=RiskDecision.ALLOWED,
            rule_name="ORDER_VALUE_LIMIT",
            message=f"订单价值合规: ${order_value:.2f} ≤ ${max_value:.2f}"
        )

    def _check_position_limit(self, symbol: str, quantity: int, price: float,
                              positions_map: Dict[str, float],
                              net_liquidation: float) -> RiskCheckResult:
        if net_liquidation <= 0:
            return RiskCheckResult(
                decision=RiskDecision.WARNING,
                rule_name="POSITION_LIMIT",
                message=f"净值 {net_liquidation} 无法计算仓位占比",
            )
        registry = get_instrument_registry()
        mult = registry.get(symbol).notional_multiplier
        trade_value = quantity * price * mult
        current_value = positions_map.get(symbol, 0) * price * mult
        new_value = current_value + trade_value
        new_pct = new_value / net_liquidation
        max_pct = self.config.position_limit_pct / 100.0
        if new_pct > max_pct:
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                rule_name="POSITION_LIMIT",
                message=f"仓位超限: {symbol} {new_pct:.1%} > 上限 {max_pct:.1%} "
                        f"(持仓 ${new_value:.2f} / 净值 ${net_liquidation:.2f})",
                details={"symbol": symbol, "current_pct": round(new_pct, 4),
                         "max_pct": max_pct, "net_liquidation": net_liquidation,
                         "new_value": new_value}
            )
        return RiskCheckResult(
            decision=RiskDecision.ALLOWED,
            rule_name="POSITION_LIMIT",
            message=f"仓位合规: {symbol} {new_pct:.1%} ≤ {max_pct:.1%}"
        )

    def _check_yearly_trades(self) -> RiskCheckResult:
        if not self._trades_loaded:
            self.load_trade_history()
        count = len(self._yearly_trades)
        limit = 80  # TFSA: CRA 商业收入门槛
        if count >= limit:
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                rule_name="YEARLY_TRADE_LIMIT",
                message=f"年度交易超限: {count} >= {limit} (CRA 商业收入风险)",
                details={"count": count, "limit": limit}
            )
        return RiskCheckResult(
            decision=RiskDecision.ALLOWED,
            rule_name="YEARLY_TRADE_LIMIT",
            message=f"年度交易合规: {count} < {limit}"
        )

    def _check_day_trading(self, symbol: str, action: str) -> RiskCheckResult:
        if action != "BUY":
            return RiskCheckResult(
                decision=RiskDecision.ALLOWED,
                rule_name="DAY_TRADING",
                message="卖出操作，不涉及当日冲销检查"
            )
        today = datetime.now().date()
        sells_today = [t for t in self._daily_trades
                       if t.get("symbol") == symbol and t.get("action") == "SELL"]
        if sells_today:
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                rule_name="DAY_TRADING",
                message=f"禁止当日冲销: 今日已卖出 {symbol}，不允许同日买入",
                details={"symbol": symbol, "sells_today": len(sells_today)}
            )
        return RiskCheckResult(
            decision=RiskDecision.ALLOWED,
            rule_name="DAY_TRADING",
            message=f"无当日冲销风险 ({symbol})"
        )

    def record_trade(self, symbol: str, action: str, quantity: int, price: float,
                     strategy_id: str = ""):
        """记录交易到内存+持久化

        v2修复(RE-01): 增加strategy_id参数，记录交易归属策略
        v2修复(RE-02): _daily_trades持久化到JSON，重启不归零
        """
        now = datetime.now()
        record = {
            "time": now.isoformat(),
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "price": price,
            "strategy_id": strategy_id,
        }
        self._yearly_trades.append(record)
        self._daily_trades.append(record)
        # 更新持仓映射
        if action == "BUY":
            self._current_positions[symbol] = self._current_positions.get(symbol, 0) + quantity
        else:
            current = self._current_positions.get(symbol, 0)
            self._current_positions[symbol] = max(0, current - quantity)
        # v2: 持久化今日交易到JSON
        self._save_daily_trades()
        self.logger.info("记录交易: %s %s x%s @ %.2f (strategy=%s)",
                         action, symbol, quantity, price, strategy_id or "unknown")

    def _save_daily_trades(self):
        """持久化今日交易记录到JSON文件(RE-02)"""
        try:
            self._trade_state_path.parent.mkdir(parents=True, exist_ok=True)
            today_str = datetime.now().strftime("%Y-%m-%d")
            records = []
            for t in self._daily_trades:
                r = dict(t) if isinstance(t, dict) else {"time": t["time"], "symbol": t.get("symbol", ""), "action": t.get("action", "")}
                if not isinstance(r.get("time"), str):
                    r["time"] = r["time"].isoformat() if hasattr(r["time"], "isoformat") else str(r["time"])
                records.append(r)
            data = {"date": today_str, "trades": records}
            self._trade_state_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            self.logger.warning("持久化每日交易记录失败: %s", e)

    def _load_daily_trades(self):
        """从JSON文件加载今日交易记录(RE-02)"""
        if not self._trade_state_path.exists():
            return []
        try:
            data = json.loads(self._trade_state_path.read_text())
            saved_date = data.get("date", "")
            today_str = datetime.now().strftime("%Y-%m-%d")
            if saved_date != today_str:
                return []
            trades = []
            for r in data.get("trades", []):
                ts = r.get("time", "")
                try:
                    dt = datetime.fromisoformat(ts)
                except (ValueError, TypeError):
                    continue
                trades.append({"time": dt, "symbol": r.get("symbol", ""),
                               "action": r.get("action", "")})
            return trades
        except Exception as e:
            self.logger.warning("加载每日交易记录失败: %s", e)
            return []
