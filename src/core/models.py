"""IBKR 核心数据模型

统一所有模块使用的数据结构，确保接口一致性。

官方参考:
- Execution: https://interactivebrokers.github.io/tws-api/execution_data.html
- Order: https://interactivebrokers.github.io/tws-api/order_info.html
- Contract: https://interactivebrokers.github.io/tws-api/contract.html
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime


# =============================================================================
# 账户相关模型
# =============================================================================


@dataclass
class AccountInfo:
    """账户基本信息"""

    account_id: str
    cash_balance: float = 0.0
    buying_power: float = 0.0
    net_liquidation: float = 0.0
    total_securities_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    currency: str = "USD"
    is_paper: bool = True
    positions: List[Position] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "cash_balance": self.cash_balance,
            "buying_power": self.buying_power,
            "net_liquidation": self.net_liquidation,
            "total_securities_value": self.total_securities_value,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "currency": self.currency,
            "positions": [p.to_dict() for p in self.positions],
        }


@dataclass
class AccountSummary:
    """账户摘要信息 (合并账户 ID 与余额，推荐使用的统一接口)"""

    account_id: str
    cash_balance: float = 0.0
    buying_power: float = 0.0
    net_liquidation: float = 0.0
    total_securities_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    excess_liquidity: float = 0.0
    currency: str = "USD"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "cash_balance": self.cash_balance,
            "buying_power": self.buying_power,
            "net_liquidation": self.net_liquidation,
            "total_securities_value": self.total_securities_value,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "excess_liquidity": self.excess_liquidity,
            "currency": self.currency,
        }


@dataclass
class BalanceInfo:
    """账户余额信息"""

    account_id: str
    cash_balance: float = 0.0
    buying_power: float = 0.0
    net_liquidation: float = 0.0
    excess_liquidity: float = 0.0
    initial_margin: float = 0.0
    maintenance_margin: float = 0.0
    currency: str = "USD"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "cash_balance": self.cash_balance,
            "buying_power": self.buying_power,
            "net_liquidation": self.net_liquidation,
            "excess_liquidity": self.excess_liquidity,
            "initial_margin": self.initial_margin,
            "maintenance_margin": self.maintenance_margin,
            "currency": self.currency,
        }


# =============================================================================
# 持仓相关模型
# =============================================================================


@dataclass
class Position:
    """持仓信息"""

    account: str
    symbol: str
    secType: str
    exchange: str
    currency: str
    quantity: float = 0.0
    cost_basis: float = 0.0
    market_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    average_cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account": self.account,
            "symbol": self.symbol,
            "secType": self.secType,
            "exchange": self.exchange,
            "currency": self.currency,
            "quantity": self.quantity,
            "cost_basis": self.cost_basis,
            "market_price": self.market_price,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "average_cost": self.average_cost,
        }


# =============================================================================
# 订单相关模型
# =============================================================================


@dataclass
class Order:
    """订单信息（活跃订单）
 
    注意：接受 'orderId' 或 'order_id' 两种风格，
    与 IBKR API 一致
    """

    # 标识
    order_id: int = 0  # 首选：统一模型内部名
    account: str = ""
    perm_id: int = 0
    client_id: int = 0

    # 合约
    symbol: str = ""
    secType: str = ""
    exchange: str = ""
    currency: str = ""

    # 订单参数
    action: str = ""  # BUY / SELL
    order_type: str = ""  # MKT / LMT / STP
    total_quantity: float = 0.0
    limit_price: float = 0.0
    aux_price: float = 0.0 # 止损价
    tif: str = ""  # DAY / GTC / IOC

    # 状态
    status: str = ""  # PendingSubmit / Submitted / Filled / Cancelled
    filled: float = 0.0
    remaining: float = 0.0
    avg_fill_price: float = 0.0
    last_fill_price: float = 0.0
    last_fill_qty: float = 0.0

    # 引用
    order_ref: str = ""
    parent_id: int = 0
    block_order: bool = False

    # 高级参数
    trail_stop_price: float = 0.0
    lmt_price_offset: float = 0.0
    oca_group: str = ""
    oca_type: int = 0
    
    # 兼容性映射：接受 'orderId' 传参以避免 KeyError
    def __post_init__(self):
        """确保 Order.__init__() 兼容 IBKR 返回的所有字段名
        
        允许通过 'orderId' 传入 order_id 以向下兼容
        """
        if hasattr(self, 'orderId'):
            self.order_id = getattr(self, 'orderId')
            object.__delattr__(self, 'orderId')
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "account": self.account,
            "perm_id": self.perm_id,
            "client_id": self.client_id,
            "symbol": self.symbol,
            "secType": self.secType,
            "exchange": self.exchange,
            "currency": self.currency,
            "action": self.action,
            "order_type": self.order_type,
            "total_quantity": self.total_quantity,
            "limit_price": self.limit_price,
            "aux_price": self.aux_price,
            "tif": self.tif,
            "status": self.status,
            "filled": self.filled,
            "remaining": self.remaining,
            "avg_fill_price": self.avg_fill_price,
            "last_fill_price": self.last_fill_price,
            "last_fill_qty": self.last_fill_qty,
            "order_ref": self.order_ref,
            "parent_id": self.parent_id,
            "block_order": self.block_order,
            "trail_stop_price": self.trail_stop_price,
            "lmt_price_offset": self.lmt_price_offset,
            "oca_group": self.oca_group,
            "oca_type": self.oca_type,
        }


@dataclass
class OrderResult:
    """订单提交结果"""

    success: bool
    order_id: int
    perm_id: int = 0
    status: str = ""
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    error_message: str = ""
    submit_time: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "perm_id": self.perm_id,
            "status": self.status,
            "filled_qty": self.filled_qty,
            "avg_fill_price": self.avg_fill_price,
            "error_message": self.error_message,
            "submit_time": self.submit_time,
        }


# =============================================================================
# 成交记录模型
# =============================================================================


@dataclass
class Execution:
    """成交记录（Execution/Trade）

    接受 IBKR API 返回的字段名（驼峰命名）。
    """

    # 标识
    exec_id: str = ""
    order_id: int = 0
    perm_id: int = 0
    client_id: int = 0
    account: str = ""

    # 合约
    symbol: str = ""
    secType: str = ""
    exchange: str = ""
    currency: str = ""

    # 成交详情
    side: str = ""  # BOT / SLD
    shares: float = 0.0
    price: float = 0.0
    cum_qty: float = 0.0
    avg_price: float = 0.0

    # 时间与清算
    exec_time: str = ""
    liquidation: int = 0

    # 引用
    order_ref: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exec_id": self.exec_id,
            "order_id": self.order_id,
            "perm_id": self.perm_id,
            "client_id": self.client_id,
            "account": self.account,
            "symbol": self.symbol,
            "secType": self.secType,
            "exchange": self.exchange,
            "currency": self.currency,
            "side": self.side,
            "shares": self.shares,
            "price": self.price,
            "cum_qty": self.cum_qty,
            "avg_price": self.avg_price,
            "exec_time": self.exec_time,
            "liquidation": self.liquidation,
            "order_ref": self.order_ref,
        }


# =============================================================================
# 合约相关模型
# =============================================================================


@dataclass
class Contract:
    """合约定义

    注意：属性使用驼峰命名（如 secType）以对齐 IB API，
    避免使用下划线命名（如 sec_type），防止 AttributeError。
    """

    symbol: str = ""
    secType: str = ""
    exchange: str = ""
    currency: str = ""
    primaryExchange: str = ""
    local_symbol: str = ""
    trading_class: str = ""
    multiplier: str = ""
    expiry: str = ""
    strike: float = 0.0
    right: str = ""  # C / P (期权)
    include_expired: bool = False
    secId_type: str = ""
    secId: str = ""
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "secType": self.secType,
            "exchange": self.exchange,
            "currency": self.currency,
            "primaryExchange": self.primaryExchange,
            "local_symbol": self.local_symbol,
            "trading_class": self.trading_class,
            "multiplier": self.multiplier,
            "expiry": self.expiry,
            "strike": self.strike,
            "right": self.right,
            "include_expired": self.include_expired,
            "secId_type": self.secId_type,
            "secId": self.secId,
            "description": self.description,
        }


@dataclass
class ContractDetails:
    """合约详情（包含市场数据等）"""

    contract: Contract
    market_name: str = ""
    min_tick: float = 0.0
    order_types: str = ""
    valid_exchanges: str = ""
    price_magnifier: int = 0
    under_symbol: str = ""
    under_secType: str = ""
    model_code: str = ""
    last_trade_date: str = ""
    contract_id: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract": self.contract.to_dict(),
            "market_name": self.market_name,
            "min_tick": self.min_tick,
            "order_types": self.order_types,
            "valid_exchanges": self.valid_exchanges,
            "price_magnifier": self.price_magnifier,
            "under_symbol": self.under_symbol,
            "under_secType": self.under_secType,
            "model_code": self.model_code,
            "last_trade_date": self.last_trade_date,
            "contract_id": self.contract_id,
        }


# =============================================================================
# 通用查询结果模型
# =============================================================================


@dataclass
class QueryResult:
    """通用查询结果包装"""

    success: bool
    data: Any = None
    error_message: str = ""
    query_time: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    source: str = "ibapi"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error_message": self.error_message,
            "query_time": self.query_time,
            "source": self.source,
        }


@dataclass
class ConnectionResult:
    """连接结果"""

    success: bool
    host: str
    port: int
    client_id: int
    connected_at: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "host": self.host,
            "port": self.port,
            "client_id": self.client_id,
            "connected_at": self.connected_at,
            "error_message": self.error_message,
        }


# =============================================================================
# 历史K线数据模型
# =============================================================================


@dataclass
class Bar:
    """历史K线数据（来自 IBKR reqHistoricalData）"""
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    wap: float = 0.0
    count: int = 0


@dataclass
class HistoricalResult:
    """历史数据查询结果"""
    req_id: int
    bars: List[Bar] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""


@dataclass
class ContractInfo:
    """合约信息（简化的 ContractDetails）"""

    symbol: str
    secType: str
    exchange: str
    currency: str
    local_symbol: str = ""
    trading_class: str = ""
    contract_id: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "secType": self.secType,
            "exchange": self.exchange,
            "currency": self.currency,
            "local_symbol": self.local_symbol,
            "trading_class": self.trading_class,
            "contract_id": self.contract_id,
        }


# =============================================================================
# 策略学习系统数据模型 (Phase 1 D5 — v2.0 自我学习闭环)
# =============================================================================


class MarketRegime(Enum):
    """市场状态枚举

    用于策略的 regime_weights 调整 — 不同市场环境下
    策略权重自适应变化。

    判断逻辑:
    - BULL: SPX > MA200 且 VIX < 20
    - BEAR: SPX < MA200 且 VIX > 25
    - SIDEWAYS: 其他(默认，最保守)
    """
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"


class StrategyState(Enum):
    """策略生命周期状态

    状态机转换:
    DRAFT → ACTIVE → HIGH_CONV (绩效优异)
              ↓               ↑
        UNDER_REVIEW ──(绩效回升)──┘
              ↓
         SUSPENDED → (人工恢复) → ACTIVE
              ↓
          RETIRED (永久禁用，YAML保留供审计)
    """
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    HIGH_CONV = "HIGH_CONV"
    UNDER_REVIEW = "UNDER_REVIEW"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class ApprovalStatus(Enum):
    """审批项状态"""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class MarketSnapshot:
    """市场状态快照 — 每次信号生成时记录

    与信号绑定，用于事后分析"该信号在什么市场环境下产生"。
    由 MarketRegimeDetector.detect() 产出。
    """
    regime: MarketRegime = MarketRegime.SIDEWAYS
    spx_price: float = 0.0
    spx_vs_ma200_pct: float = 0.0
    vix: float = 0.0
    sector_returns: Dict[str, float] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime.value,
            "spx_price": self.spx_price,
            "spx_vs_ma200_pct": self.spx_vs_ma200_pct,
            "vix": self.vix,
            "sector_returns": self.sector_returns,
            "timestamp": self.timestamp,
        }


@dataclass
class StrategyResult:
    """策略执行结果 — 闭环数据基础

    每次策略产生信号并执行后，记录完整执行链路：
    信号价 → 成交价 → 平仓价，支持滑点追踪和绩效分析。

    生命周期:
    1. 信号生成时创建(填写前半部分: result_id ~ signal_price)
    2. 订单成交后更新(actual_fill_price, slippage_pct)
    3. 持仓平仓后关闭(close_price, realized_pnl, is_closed=True)
    """
    # --- 标识 ---
    result_id: str = ""
    strategy_id: str = ""
    signal_id: str = ""
    symbol: str = ""
    action: str = ""

    # --- 价格链（信号价→成交价→平仓价） ---
    signal_price: float = 0.0
    actual_fill_price: float = 0.0
    close_price: float = 0.0
    slippage_pct: float = 0.0

    # --- 绩效指标 ---
    quantity: int = 0
    initial_pnl: float = 0.0
    realized_pnl: float = 0.0
    realized_pnl_pct: float = 0.0
    holding_days: int = 0

    # --- 上下文快照 ---
    signal_time: str = ""
    close_time: str = ""
    benchmark_return: float = 0.0
    alpha: float = 0.0
    market_regime: str = "SIDEWAYS"
    market_conditions: Dict[str, Any] = field(default_factory=dict)

    # --- 标记 ---
    is_winner: bool = False
    is_closed: bool = False
    is_shadow: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_time": self.signal_time,
            "result_id": self.result_id,
            "strategy_id": self.strategy_id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "action": self.action,
            "signal_price": self.signal_price,
            "actual_fill_price": self.actual_fill_price,
            "close_price": self.close_price,
            "slippage_pct": self.slippage_pct,
            "quantity": self.quantity,
            "initial_pnl": self.initial_pnl,
            "realized_pnl": self.realized_pnl,
            "realized_pnl_pct": self.realized_pnl_pct,
            "holding_days": self.holding_days,
            "close_time": self.close_time,
            "benchmark_return": self.benchmark_return,
            "alpha": self.alpha,
            "market_regime": self.market_regime,
            "market_conditions": self.market_conditions,
            "is_winner": self.is_winner,
            "is_closed": self.is_closed,
            "is_shadow": self.is_shadow,
        }


@dataclass
class StrategyPerformance:
    """策略聚合绩效 — 学习引擎的输入

    由 PerformanceTracker.get_performance() 计算产出，
    供 StrategyLearner 分析使用。

    设计约束:
    - sample_size_sufficient 为 False 时不调参(避免小样本偏差)
    - 按市场状态细分胜率(支持 regime_weights 调整决策)
    - 时间窗口细分(30d/90d，支持短期趋势识别)
    """
    strategy_id: str = ""

    # --- 计数 ---
    total_signals: int = 0
    total_executed: int = 0
    total_closed: int = 0

    # --- 胜率 ---
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0

    # --- 收益 ---
    avg_pnl_pct: float = 0.0
    avg_alpha: float = 0.0
    avg_holding_days: float = 0.0

    # --- 风险 ---
    max_drawdown_pct: float = 0.0
    max_consecutive_losses: int = 0

    # --- 滑点 ---
    avg_slippage_pct: float = 0.0

    # --- 市场状态细分胜率 ---
    bull_win_rate: float = 0.0
    bear_win_rate: float = 0.0
    sideways_win_rate: float = 0.0

    # --- 时间窗口细分 ---
    last_30d_win_rate: float = 0.0
    last_90d_win_rate: float = 0.0
    last_14d_win_rate: float = 0.0

    # --- 元数据 ---
    last_updated: str = ""
    sample_size_sufficient: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "total_signals": self.total_signals,
            "total_executed": self.total_executed,
            "total_closed": self.total_closed,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": self.win_rate,
            "avg_pnl_pct": self.avg_pnl_pct,
            "avg_alpha": self.avg_alpha,
            "avg_holding_days": self.avg_holding_days,
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_consecutive_losses": self.max_consecutive_losses,
            "avg_slippage_pct": self.avg_slippage_pct,
            "bull_win_rate": self.bull_win_rate,
            "bear_win_rate": self.bear_win_rate,
            "sideways_win_rate": self.sideways_win_rate,
            "last_30d_win_rate": self.last_30d_win_rate,
            "last_90d_win_rate": self.last_90d_win_rate,
            "last_14d_win_rate": self.last_14d_win_rate,
            "last_updated": self.last_updated,
            "sample_size_sufficient": self.sample_size_sufficient,
        }


@dataclass
class ApprovalItem:
    """学习引擎产出的变更建议 — 必须经用户审批

    安全约束:
    - 所有变更(权重/参数/生命周期)必须先提交审批
    - 审批通过后才能应用到YAML策略配置
    - 超时未审批自动REJECT(默认72h)
    - 通知失败不阻塞(先持久化，后通知)
    """
    item_id: str = ""
    strategy_id: str = ""
    change_type: str = ""
    current_value: Any = None
    proposed_value: Any = None
    reason: str = ""
    confidence: float = 0.0

    # --- 绩效证据 ---
    evidence: Dict[str, Any] = field(default_factory=dict)

    # --- 通知状态 ---
    notification_status: str = "NOT_SENT"
    notification_attempts: int = 0

    # --- 时间 ---
    created_at: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    expires_at: str = ""
    resolved_at: str = ""

    # --- 状态 ---
    status: str = "PENDING"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "strategy_id": self.strategy_id,
            "change_type": self.change_type,
            "current_value": self.current_value,
            "proposed_value": self.proposed_value,
            "reason": self.reason,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "notification_status": self.notification_status,
            "notification_attempts": self.notification_attempts,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "resolved_at": self.resolved_at,
            "status": self.status,
        }


# =============================================================================
# 回测引擎数据模型 (Phase 4 D39-D41)
# =============================================================================


@dataclass
class BacktestTrade:
    """回测中的单笔模拟交易"""
    symbol: str
    action: str  # BUY | SELL
    quantity: int
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    pnl_amount: float = 0.0
    is_closed: bool = False
    is_winner: bool = False
    reason: str = ""


@dataclass
class BacktestResult:
    """单策略回测结果"""
    strategy_id: str
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    avg_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)


@dataclass
class ComparisonResult:
    """对比回测结果 — baseline vs proposed"""
    baseline: Optional[BacktestResult] = None
    proposed: Optional[BacktestResult] = None
    pnl_improvement: float = 0.0
    risk_change: float = 0.0
    recommendation: str = ""  # ADOPT | REJECT | REVIEW
