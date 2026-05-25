"""
IBKR 策略引擎 — YAML 模板驱动
"""

import logging
import random
from datetime import datetime
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent

logger = logging.getLogger(__name__)


class SignalAction(Enum):
    """信号动作"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    REBALANCE = "REBALANCE"


@dataclass
class MarketData:
    """市场数据模型"""
    symbol: str
    price: float
    volume: int
    # --- 基本面 ---
    pe_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None
    market_cap: Optional[float] = None
    eps_ttm: Optional[float] = None
    # --- 价格区间 ---
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    price_20d_ago: Optional[float] = None
    # --- 技术指标 ---
    ma_20: Optional[float] = None
    ma_50: Optional[float] = None
    ma_200: Optional[float] = None
    rsi_14: Optional[float] = None
    volume_avg_20d: Optional[float] = None
    # --- 趋势跟踪指标 ---
    ma_50_slope: Optional[float] = None
    ma_200_slope: Optional[float] = None
    ma_200_slope_prev: Optional[float] = None
    ma_spread_ratio: Optional[float] = None
    is_consolidating: Optional[bool] = None
    consolidation_days: Optional[int] = None
    volume_ratio: Optional[float] = None
    breakout_detected: Optional[bool] = None
    retrace_to_ma50: Optional[bool] = None
    days_from_high: Optional[int] = None
    # --- 衍生涨跌幅 ---
    change_1d_pct: Optional[float] = None
    change_5d_pct: Optional[float] = None
    change_20d_pct: Optional[float] = None
    # --- 元信息 ---
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class TradingSignal:
    """交易信号

    v2新增字段(均有默认值，完全向后兼容):
    - strategy_id: 策略ID(来自YAML的strategy_id字段)
    - signal_id: 信号唯一标识(UUID)
    - signal_price: 信号生成时的市场价格
    - weight: 策略权重(0-2)，影响冲突解决
    - market_regime: 信号生成时的市场状态(BULL/BEAR/SIDEWAYS)
    """
    strategy_name: str
    symbol: str
    action: SignalAction
    quantity: int
    target_price: Optional[float] = None
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""
    confidence: float = 0.0  # 置信度 0-1
    priority: int = 0  # 策略优先级
    timestamp: Optional[datetime] = None
    # --- v2新增字段 ---
    strategy_id: str = ""
    signal_id: str = ""
    signal_price: float = 0.0
    weight: float = 1.0
    market_regime: str = ""
    # --- v3新增字段: 趋势跟踪执行管线 ---
    entry_delay_days: int = 0
    stop_loss_type: str = ""
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    oco_group_id: str = ""

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
        if not self.signal_id:
            import uuid
            self.signal_id = str(uuid.uuid4())[:8]

    def is_allowed(self) -> bool:
        """检查信号是否有效"""
        return self.action != SignalAction.HOLD and self.quantity > 0


@dataclass
class ConditionNode:
    """条件节点 — 支持嵌套 AND/OR 组合

    叶子节点: type + params (field, threshold, period, etc.)
    非叶子节点: operator=AND/OR, rules=[子节点...]

    向后兼容: 旧的 flat list conditions = [item, ...]
    会被解析为 ConditionNode(operator=AND, rules=[...])
    """
    operator: Optional[str] = None   # AND / OR  (非叶子)
    type: Optional[str] = None       # 条件类型 (叶子)
    rules: List["ConditionNode"] = field(default_factory=list)

    # 叶子节点参数
    symbol: Optional[str] = None
    field: Optional[str] = None
    threshold: Optional[float] = None
    threshold_ratio: Optional[float] = None
    period: Optional[int] = None
    multiplier: Optional[float] = None
    min_balance: Optional[float] = None
    mode: Optional[str] = None
    flat_threshold: Optional[float] = None


def _parse_condition_tree(raw) -> ConditionNode:
    """将 YAML conditions 字段解析为条件树

    - None / 缺失 → always (无条件)
    - list → AND 组合
    - dict → 递归解析
    """
    if raw is None:
        return ConditionNode(type="always")

    if isinstance(raw, list):
        return ConditionNode(
            operator="AND",
            rules=[_parse_condition_tree(item) for item in raw],
        )

    if isinstance(raw, dict):
        if "rules" in raw:
            return ConditionNode(
                operator=raw.get("operator", "AND"),
                rules=[_parse_condition_tree(r) for r in raw.get("rules", [])],
            )
        return ConditionNode(
            type=raw.get("type"),
            symbol=raw.get("symbol"),
            field=raw.get("field"),
            operator=raw.get("operator"),
            threshold=raw.get("threshold"),
            threshold_ratio=raw.get("threshold_ratio"),
            period=raw.get("period"),
            multiplier=raw.get("multiplier"),
            min_balance=raw.get("min_balance"),
            mode=raw.get("mode"),
            flat_threshold=raw.get("flat_threshold"),
        )

    return ConditionNode(type="always")


def _eval_condition(node: ConditionNode, symbol: str, market_price: float,
                    avg_cost: float, market_data: List[MarketData] = None) -> bool:
    """递归求值条件树

    Args:
        node: 条件节点
        symbol: 当前评估的标的
        market_price: 当前市价
        avg_cost: 平均成本
        market_data: 市场数据列表（含技术指标）

    Returns:
        条件是否满足
    """
    if node.type is not None:
        return _eval_leaf(node, symbol, market_price, avg_cost, market_data)

    if node.operator == "AND":
        return all(
            _eval_condition(r, symbol, market_price, avg_cost, market_data)
            for r in node.rules
        )

    if node.operator == "OR":
        return any(
            _eval_condition(r, symbol, market_price, avg_cost, market_data)
            for r in node.rules
        )

    return False


def _eval_leaf(node: ConditionNode, symbol: str, market_price: float,
               avg_cost: float, market_data: List[MarketData] = None) -> bool:
    """叶子节点求值 — 纯注册式引擎"""
    from src.core.conditions import evaluate, get_registry
    from src.core.conditions.base import ConditionContext
    if node.type not in get_registry():
        logger.error("未知条件类型: %s，可用: %s", node.type, list(get_registry().keys()))
        return False
    ctx = ConditionContext(symbol, market_price, avg_cost, market_data)
    return evaluate(node.type, node, ctx)






def _collect_target_symbols(conditions_raw, action_config: dict) -> set:
    """收集需要评估的标的集合

    从 conditions 的 symbol 字段 + action.ticker 中收集。
    策略引擎不依赖持仓，无 ticker 时返回空集（由调用方保证 target_symbols）。
    """
    symbols = set()

    ticker = action_config.get("ticker", "")
    if ticker:
        symbols.add(ticker)

    if isinstance(conditions_raw, list):
        for cond in conditions_raw:
            s = cond.get("symbol") if isinstance(cond, dict) else None
            if s:
                symbols.add(s)

    return symbols


class StrategyTemplateEngine:
    """Template engine: loads YAML templates and expands {symbol} placeholders.

    Templates live in strategy/templates/ and contain {symbol} placeholders
    that get replaced with actual symbol names to produce strategy instances.
    """

    def __init__(self, template_dir: Path = None):
        if template_dir is None:
            template_dir = PROJECT_ROOT / "strategy" / "templates"
        elif isinstance(template_dir, str):
            template_dir = Path(template_dir)
        self.template_dir = template_dir
        self.logger = logging.getLogger("StrategyTemplateEngine")
        self._cache: Dict[str, dict] = {}

    def _load_template(self, template_name: str) -> Optional[dict]:
        """Load and cache a raw template YAML."""
        if template_name in self._cache:
            return self._cache[template_name]

        filename = f"{template_name}.yaml"
        filepath = self.template_dir / filename
        if not filepath.exists():
            self.logger.error(f"模版文件不存在: {filepath}")
            return None

        try:
            with open(filepath, "r") as f:
                raw = yaml.safe_load(f)
            if raw:
                self._cache[template_name] = raw
            return raw
        except Exception as e:
            self.logger.error(f"模版加载失败 {filename}: {e}")
            return None

    def expand(self, template_name: str, symbol: str) -> Optional[dict]:
        """Expand a template for a specific symbol by replacing {symbol} placeholders."""
        raw = self._load_template(template_name)
        if raw is None:
            return None

        yaml_str = yaml.dump(raw, default_flow_style=False, allow_unicode=True)
        expanded_str = yaml_str.replace("{symbol}", symbol)
        try:
            return yaml.safe_load(expanded_str)
        except Exception as e:
            self.logger.error(f"模版展开失败 {template_name} / {symbol}: {e}")
            return None

    def expand_all(self, symbol: str, template_names: List[str]) -> List[dict]:
        """Expand multiple templates for one symbol."""
        results = []
        for name in template_names:
            config = self.expand(name, symbol)
            if config:
                results.append(config)
        return results


class StrategyFactory:
    """策略工厂：动态加载 YAML 策略模板，按模板声明的 signal_factors 自动获取数据

    watch_templates format: {template_name: [symbols]}
    Each template is expanded for each bound symbol via StrategyTemplateEngine.
    """
    def __init__(self, regime_detector=None, client=None,
                 market_data_source: str = "auto", template_dir: str = None,
                 watch_templates: Dict = None):
        self.yaml_strategies: List["YAMLTemplateStrategy"] = []
        self.regime_detector = regime_detector
        self.client = client
        self.market_data_source = market_data_source
        self.watch_templates = watch_templates or {}
        self.logger = logging.getLogger("StrategyFactory")

        if template_dir is None:
            self.template_engine = StrategyTemplateEngine()
        else:
            self.template_engine = StrategyTemplateEngine(Path(template_dir))

        self.logger.info("加载全部策略...")
        self.load_all()

    def load_all(self):
        """Expand all templates for their bound symbols.

        Format: {template_name: [symbol1, symbol2, ...]}
        """
        templates_loaded = 0
        for template_name, symbols in self.watch_templates.items():
            if not isinstance(symbols, list):
                continue
            for symbol in symbols:
                config = self.template_engine.expand(template_name, symbol)
                if config:
                    strategy = YAMLTemplateStrategy(config)
                    self.yaml_strategies.append(strategy)
                    templates_loaded += 1

        if templates_loaded > 0:
            self.logger.info(f"从模版展开 {templates_loaded} 个策略实例")

    def _load_yaml_file(self, filepath: Path):
        """加载单个 YAML 策略文件"""
        try:
            with open(filepath, "r") as f:
                config = yaml.safe_load(f)
            
            if not config:
                return
            
            enabled = config.get("enabled", True)
            if not enabled:
                self.logger.debug(f"跳过禁用策略：{filepath.stem}")
                return
            
            strategy = YAMLTemplateStrategy(config)
            self.yaml_strategies.append(strategy)
            self.logger.info(f"已加载策略：{config.get('strategy_id')} ({config.get('name')})")
            
        except Exception as e:
            self.logger.error(f"加载策略失败 {filepath.name}: {e}")

    def analyze(self, target_symbols: Optional[Set[str]] = None,
                is_paper: bool = True) -> List[TradingSignal]:
        """分析策略模板，按 signal_factors 声明获取数据，返回信号

        signal_factors 决定策略需要哪些数据因子：
        - "market_data": 需要市场行情（价格、RSI、涨跌幅等）
        - []: 不依赖任何数据因子（纯配置驱动）
        将来可扩展: "macro", "sentiment", "sector_rotation" 等

        Args:
            target_symbols: 可选，指定要评估的标的集合。不传时由各策略从 ticker 推导。
            is_paper: 是否为模拟盘（影响实盘卖空限制）
        """
        # 1. 如果数据源强制要求 IBKR 但没有 client，跳过
        if self.client is None and self.market_data_source == "ibkr":
            self.logger.warning("StrategyFactory.client 未设置且数据源=ibkr，跳过分析")
            return []
        if self.client is None and self.market_data_source != "yfinance":
            self.logger.info("StrategyFactory.client 未设置，使用 yfinance 回退获取数据")

        # 2. 收集需要市场数据的策略所关注的标的
        market_data_symbols = set()
        for ys in self.yaml_strategies:
            if "market_data" in ys.signal_factors:
                syms = target_symbols or _collect_target_symbols(
                    ys.raw_conditions, ys.action_config
                )
                market_data_symbols.update(syms)

        # 3. 仅当有策略声明 market_data 时才获取
        data = {}
        if market_data_symbols:
            data["market_data"] = self._fetch_market_data(
                market_data_symbols or target_symbols
            )
        else:
            data["market_data"] = []

        # 4. 检测市场状态
        regime_str = ""
        market_data = data.get("market_data", [])
        if self.regime_detector and market_data:
            try:
                snapshot = self.regime_detector.detect(market_data=market_data)
                regime_str = snapshot.regime.value
                self.logger.info("当前市场状态: %s", regime_str)
            except Exception as e:
                self.logger.warning(f"市场状态检测失败: {e}")

        # 5. 执行 YAML 模板策略
        all_signals = []
        for yaml_strategy in self.yaml_strategies:
            signals = yaml_strategy.evaluate(data, is_paper=is_paper,
                                             target_symbols=target_symbols)
            for sig in signals:
                sig.priority = yaml_strategy.priority
                sig.strategy_id = yaml_strategy.strategy_id
                sig.market_regime = regime_str
                regime_multiplier = yaml_strategy.regime_weights.get(regime_str, 1.0) if regime_str else 1.0
                sig.weight = yaml_strategy.weight * regime_multiplier
            all_signals.extend(signals)

        return self._resolve_conflicts(all_signals)

    def _fetch_market_data(self, target_symbols) -> list:
        """获取市场数据和技术指标

        策略引擎仅依赖 target_symbols，不回退到持仓。
        """
        symbols = list(target_symbols) if target_symbols else []

        if not symbols:
            return []

        from src.core.market_data import MarketDataProvider
        provider = MarketDataProvider(self.client, data_source=self.market_data_source)
        mds = provider.fetch_basic(symbols)
        market_data_list = [md for md in mds.values() if md and md.price > 0]
        if market_data_list:
            provider.enrich(market_data_list)
        return market_data_list
    
    def _resolve_conflicts(self, signals: List[TradingSignal]) -> List[TradingSignal]:
        """解决信号冲突：加权决策替代简单去重

        同方向信号合并权重，保留最高weight*confidence信号；
        反向信号(BUY vs SELL)按综合评分决定方向。
        """
        if not signals:
            return []
        
        symbol_signals = {}
        for sig in signals:
            symbol_signals.setdefault(sig.symbol, []).append(sig)
        
        result = []
        for symbol, sigs in symbol_signals.items():
            if len(sigs) == 1:
                result.append(sigs[0])
                continue

            buy_sigs = [s for s in sigs if s.action == SignalAction.BUY]
            sell_sigs = [s for s in sigs if s.action == SignalAction.SELL]

            for group in [buy_sigs, sell_sigs]:
                if not group:
                    continue
                group.sort(key=lambda s: getattr(s, 'weight', 1.0) * getattr(s, 'confidence', 0.5), reverse=True)
                winner = group[0]
                merged = [s.strategy_name for s in group[1:]]
                if merged:
                    winner.reason = f"{winner.reason} [合并自: {', '.join(merged)}]"
                result.append(winner)

            if buy_sigs and sell_sigs:
                buy_score = sum(getattr(s, 'weight', 1.0) * getattr(s, 'confidence', 0.5) for s in buy_sigs)
                sell_score = sum(getattr(s, 'weight', 1.0) * getattr(s, 'confidence', 0.5) for s in sell_sigs)
                if buy_score >= sell_score:
                    result = [s for s in result if not (s.symbol == symbol and s.action == SignalAction.SELL)]
                    self.logger.info(f"加权冲突解决 [{symbol}]: BUY({buy_score:.2f}) >= SELL({sell_score:.2f})，保留买入")
                else:
                    result = [s for s in result if not (s.symbol == symbol and s.action == SignalAction.BUY)]
                    self.logger.info(f"加权冲突解决 [{symbol}]: SELL({sell_score:.2f}) > BUY({buy_score:.2f})，保留卖出")
        
        return result


class YAMLTemplateStrategy:
    """YAML 模板策略：根据 YAML 配置动态生成信号"""
    
    def __init__(self, config: dict):
        self.config = config
        self.strategy_id = config.get("strategy_id", "UNKNOWN")
        self.name = config.get("name", "Unnamed")
        self.description = config.get("description", "")
        self.priority = config.get("priority", 10)
        self.weight = config.get("weight", 1.0)
        self.state = config.get("state", "ACTIVE")
        self.regime_weights = config.get("regime_weights", {})
        self.exclude_symbols = config.get("exclude_symbols", [])
        self.signal_factors = set(config.get("signal_factors", []))
        self.raw_conditions = config.get("conditions")
        self.condition_tree = _parse_condition_tree(self.raw_conditions)
        self.action_config = config.get("action", {})
        self.risk_config = self.action_config.get("risk", {})
        self.logger = logging.getLogger(f"YAMLStrategy.{self.strategy_id}")
    
    def evaluate(self, data: dict,
                 is_paper: bool = False,
                 target_symbols: Optional[Set[str]] = None) -> List[TradingSignal]:
        """评估条件，生成信号

        策略引擎只看市场数据，不关心账户状态。
        SELL 信号照常生成，持仓检查由执行层负责。
        price_vs_cost 条件中 avg_cost 默认为 0，由执行层补充。

        Args:
            data: 数据字典，键为信号因子名
            target_symbols: 可选，指定要评估的标的集合。
                           不传时由 _collect_target_symbols 从条件/ticker 推导。
        """
        signals = []
        market_data = data.get("market_data", [])

        # 数据获取失败由 analyze() 统一处理，signal_factors 仅作声明式依赖

        # 收集目标标的：策略自身ticker与外部约束取交集
        # - 有ticker的策略(如VRT_DIP_BUY): 只评估自身ticker ∩ 外部约束
        # - 无ticker的通用策略(如stop_loss): 对全部外部target_symbols评估
        strategy_own_symbols = _collect_target_symbols(
            self.raw_conditions, self.action_config
        )
        if target_symbols is not None:
            external = set(target_symbols)
            if strategy_own_symbols:
                # 有明确ticker: 取交集，策略只评估自己定义的标的
                target_symbols = external & strategy_own_symbols
            else:
                # 通用策略(无ticker): 对全部外部标的评估
                target_symbols = external
        else:
            target_symbols = strategy_own_symbols
 
        # 排除指定标的（如 STOP_LOSS 排除 F）
        if self.exclude_symbols:
            target_symbols = {s for s in target_symbols if s not in self.exclude_symbols}
            if not target_symbols:
                return signals
 
        for symbol in target_symbols:
            avg_cost = 0  # 策略引擎不持有持仓数据，avg_cost 由执行层补充
            market_price = self._get_market_price(market_data, symbol)
 
            if market_price <= 0:
                logger.error(f"S-09: market_price={market_price} <= 0 for {symbol}, 跳过信号生成")
                continue
 
            # 检查条件树
            if not _eval_condition(self.condition_tree, symbol, market_price, avg_cost, market_data):
                continue
 
            # SELL 信号照常生成，不检查持仓（持仓检查由执行层负责）
            signal = self._create_signal(symbol, market_price, is_paper=is_paper)
            if signal:
                signals.append(signal)
                self.logger.info(f"生成信号: {signal.symbol} {signal.action.value} x{signal.quantity}")
        
        return signals
    
    def _get_market_price(self, market_data: List[MarketData], symbol: str) -> float:
        """获取市价"""
        for md in market_data:
            if md.symbol == symbol:
                return md.price
        return 0.0
    
    def _create_signal(self, symbol: str, market_price: float, is_paper: bool = False) -> TradingSignal:
        """创建交易信号

        策略引擎不持有持仓数据：
        - SELL quantity="ALL" 用 -1 标记，由执行层替换为实际持仓量
        - 不做实盘卖空检查（执行层职责）
        """
        action_type = self.action_config.get("type", "LIMIT_BUY")
       
        if action_type == "LIMIT_BUY" or action_type == "MARKET_BUY":
            action = SignalAction.BUY
            quantity = self.action_config.get("quantity", 10)
            price_offset = self.action_config.get("price_offset", -0.02)
            limit_price = market_price * (1 + price_offset)
        elif action_type == "MARKET_SELL":
            action = SignalAction.SELL

            raw_qty = self.action_config.get("quantity", -1)
            if isinstance(raw_qty, str):
                raw_qty = -1 if raw_qty.upper() == "ALL" else int(raw_qty)

            # -1 表示"全部持仓"，由执行层替换为实际数量
            quantity = raw_qty if raw_qty > 0 else -1
            limit_price = None
        else:
            action = SignalAction.HOLD
            quantity = 0
            limit_price = None

        reason = f"{self.name}: 市价 ${market_price:.2f}"

        # Extract risk/execution config from action block
        risk = self.risk_config
        stop_loss_type = risk.get("stop_loss_type", "")
        stop_loss_pct = risk.get("stop_loss_pct", 0.0)
        take_profit_pct = risk.get("take_profit_pct", 0.0)
        trailing_stop_pct = risk.get("trailing_stop_pct", 0.0)

        # entry_delay_days lives at action level, not in risk
        entry_delay_days = int(self.action_config.get("entry_delay_days", 0))

        # Auto-generate OCO group ID when bracket params are present
        oco_group_id = ""
        if stop_loss_pct > 0 or take_profit_pct > 0:
            import hashlib
            raw = f"{self.strategy_id}_{symbol}_{datetime.now().strftime('%Y%m%d')}"
            oco_group_id = hashlib.md5(raw.encode()).hexdigest()[:8]

        return TradingSignal(
            strategy_name=self.name,
            symbol=symbol,
            action=action,
            quantity=quantity,
            target_price=limit_price,
            reason=reason,
            stop_loss_type=stop_loss_type,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_pct=trailing_stop_pct,
            entry_delay_days=entry_delay_days,
            oco_group_id=oco_group_id,
        )
