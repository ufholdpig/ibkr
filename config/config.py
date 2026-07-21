"""
IBKR 配置加载模块

负责从YAML文件加载配置，支持环境变量覆盖
"""

import os
import random
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GatewayConfig:
    """IB Gateway 连接配置"""
    host: str = "127.0.0.1"
    port: int = 4001
    client_id: int = 0  # 运行时随机生成
    timeout: int = 15
    max_retries: int = 3
    retry_delay: int = 2
    account_id: str = ""  # 指定子账号 ID（多账号时必需），空字符串由 TWS 默认决定


# =============================================================================
# StrongAccumulationConfig — 必须放在 WatchConfig 之前（因 WatchConfig 引用了它）
# =============================================================================

@dataclass
class StrongAccumulationConfig:
    """Strong Accumulation 策略配置 — 从 strategy/templates/strong_accumulation.yaml 加载

    sectors: 全量扫描范围（scope=full 时使用）
    candidate_pool: top 10 候选池（scope=pool_only 使用，scope=full 后动态更新）
    universe_selector: 策略参数（从 ibkr.yaml universe_selector 节迁移）
    """
    sectors: dict = field(default_factory=dict)
    candidate_pool: list = field(default_factory=list)

    # 策略参数
    capacity: int = 10
    max_positions: int = 2
    min_positions: int = 1
    required_passing: int = 4
    min_score_threshold: float = 4.0
    take_profit_pct: float = 20.0
    stop_loss_pct: float = -10.0
    reduce_ratio: float = 0.5
    blacklist: list = field(default_factory=list)
    default_position_size_pct: float = 10.0
    top_n: int = 3

    @property
    def all_sector_leaders(self) -> list[str]:
        """从所有 sectors 提取所有龙头股（去重）"""
        symbols = []
        seen = set()
        for sector_cfg in self.sectors.values():
            for sym in sector_cfg.get("leaders", []):
                if sym not in seen:
                    symbols.append(sym)
                    seen.add(sym)
        return symbols

    def get_candidate_pool_for_scope(self, scope: str) -> list[str]:
        """根据 scope 返回标的列表

        scope=full: 返回全量 sectors（所有板块龙头）
        scope=pool_only: 返回 candidate_pool
        """
        if scope == "full":
            return self.all_sector_leaders
        return self.candidate_pool

    def update_candidate_pool(self, top_symbols: list[str]):
        """更新 candidate_pool（scope=full 后调用）"""
        self.candidate_pool = top_symbols

    def to_template_dict(self) -> dict:
        """导出为模板 dict（用于写回 YAML）"""
        return {
            "strategy_id": "strong_accumulation",
            "name": "强势股蓄力池",
            "description": "基于板块龙头扫描的强势股候选池，每日盘后动态更新 top 10",
            "sectors": self.sectors,
            "candidate_pool": self.candidate_pool,
            "universe_selector": {
                "capacity": self.capacity,
                "max_positions": self.max_positions,
                "min_positions": self.min_positions,
                "required_passing": self.required_passing,
                "min_score_threshold": self.min_score_threshold,
                "position_review": {
                    "take_profit_pct": self.take_profit_pct,
                    "stop_loss_pct": self.stop_loss_pct,
                    "reduce_ratio": self.reduce_ratio,
                },
                "blacklist": self.blacklist,
                "opening": {
                    "default_position_size_pct": self.default_position_size_pct,
                    "top_n": self.top_n,
                },
            },
        }


_STRONG_ACC_CONFIG_CACHE: Optional[StrongAccumulationConfig] = None


def load_strong_accumulation_config() -> StrongAccumulationConfig:
    """加载 StrongAccumulation 配置（带缓存）"""
    global _STRONG_ACC_CONFIG_CACHE
    if _STRONG_ACC_CONFIG_CACHE is not None:
        return _STRONG_ACC_CONFIG_CACHE

    from pathlib import Path
    project_root = Path(__file__).parent.parent
    template_path = project_root / "strategy" / "templates" / "strong_accumulation.yaml"

    if not template_path.exists():
        _STRONG_ACC_CONFIG_CACHE = StrongAccumulationConfig()
        return _STRONG_ACC_CONFIG_CACHE

    with open(template_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    univ = data.get("universe_selector", {})
    pos_rev = univ.get("position_review", {})
    opening = univ.get("opening", {})

    cfg = StrongAccumulationConfig(
        sectors=data.get("sectors", {}),
        candidate_pool=data.get("candidate_pool", []),
        capacity=univ.get("capacity", 10),
        max_positions=univ.get("max_positions", 2),
        min_positions=univ.get("min_positions", 1),
        required_passing=univ.get("required_passing", 4),
        min_score_threshold=univ.get("min_score_threshold", 4.0),
        take_profit_pct=pos_rev.get("take_profit_pct", 20.0),
        stop_loss_pct=pos_rev.get("stop_loss_pct", -10.0),
        reduce_ratio=pos_rev.get("reduce_ratio", 0.5),
        blacklist=univ.get("blacklist", []),
        default_position_size_pct=opening.get("default_position_size_pct", 10.0),
        top_n=opening.get("top_n", 3),
    )

    _STRONG_ACC_CONFIG_CACHE = cfg
    return cfg


def save_strong_accumulation_config(cfg: StrongAccumulationConfig):
    """保存 StrongAccumulation 配置到 YAML 文件（scope=full 后写回 top 10）"""
    from pathlib import Path
    import datetime
    project_root = Path(__file__).parent.parent
    template_path = project_root / "strategy" / "templates" / "strong_accumulation.yaml"

    with open(template_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    data["candidate_pool"] = cfg.candidate_pool
    data["_last_refresh"] = datetime.datetime.now().isoformat()

    with open(template_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    global _STRONG_ACC_CONFIG_CACHE
    _STRONG_ACC_CONFIG_CACHE = None


# =============================================================================
# 其他 Config 类
# =============================================================================

@dataclass
class WatchConfig:
    """Watch 守护进程配置 — 以模版为中心

    templates: {template_name: [symbols]} — 每个模版绑定适用的标的列表
    cooldown_minutes: {symbol: minutes, "default": minutes} — 标的冷却时间
    strong_accumulation: StrongAccumulationConfig — 强势股蓄力池配置
    """
    templates: dict = field(default_factory=dict)
    cooldown_minutes: dict = field(default_factory=lambda: {"default": 20})
    poll_interval: int = 5
    indicator_refresh_minutes: int = 30
    template_dir: str = "strategy/templates"
    real_cooldown_multiplier: float = 4.0
    strong_accumulation: Optional[StrongAccumulationConfig] = None

    @property
    def symbol_list(self) -> list[str]:
        """从所有模版绑定推导唯一标的列表"""
        symbols = set()
        for syms in self.templates.values():
            symbols.update(syms)
        return sorted(symbols)

    def get_cooldown(self, symbol: str) -> int:
        """获取标的的冷却时间，未配置则用 default"""
        return self.cooldown_minutes.get(symbol, self.cooldown_minutes.get("default", 20))


@dataclass
class RiskConfig:
    """风控引擎配置

    tfsa_limitation=true  硬编码 TFSA 规则（年交易 ≤80, 禁止做空, 禁止日内交易）
    tfsa_limitation=false 保证金/普通账户，不施加 TFSA 限制
    """
    enabled: bool = True
    fail_closed: bool = False       # true: 风控不可用时拒绝交易（实盘必须 true）
    position_limit_pct: float = 20.0
    max_order_value_pct: float = 50.0  # 单笔订单价值 ≤ 账户净值的百分比
    tfsa_limitation: bool = True


@dataclass
class UniverseSelectorConfig:
    """候选池选择器配置"""
    enabled: bool = True
    capacity: int = 10               # 盘后刷新后保留 top N
    max_positions: int = 2
    min_positions: int = 1
    required_passing: int = 4
    min_score_threshold: float = 4.0
    take_profit_pct: float = 20.0
    stop_loss_pct: float = -10.0
    reduce_ratio: float = 0.5
    blacklist: list = field(default_factory=list)
    default_position_size_pct: float = 10.0
    top_n: int = 3
    oco_enabled: bool = True          # true: 建仓后自动挂止损+止盈 OCO 订单


@dataclass
class InstrumentSpec:
    """合约规格 — 统一描述股票/期货的交易所元数据"""
    symbol: str
    sec_type: str = "STK"
    exchange: str = "SMART"
    currency: str = "USD"
    multiplier: int = 1
    trading_class: str = ""
    front_month: str = ""
    roll_rule: str = ""
    yfinance_symbol: str = ""

    @property
    def is_futures(self) -> bool:
        return self.sec_type == "FUT"

    @property
    def notional_multiplier(self) -> int:
        return self.multiplier


class InstrumentRegistry:
    """合约注册表 — 从 instruments.yaml 加载，未注册 symbol 返回 STK 默认值"""

    def __init__(self, config_path: str = None):
        self._specs: dict = {}
        if config_path:
            self._load(config_path)

    def _load(self, path: str):
        p = Path(path)
        if not p.exists():
            return
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or "instruments" not in data:
            return
        for sym, cfg in data["instruments"].items():
            self._specs[sym.upper()] = InstrumentSpec(
                symbol=sym.upper(),
                sec_type=cfg.get("sec_type", "STK"),
                exchange=cfg.get("exchange", "SMART"),
                currency=cfg.get("currency", "USD"),
                multiplier=int(cfg.get("multiplier", 1)),
                trading_class=cfg.get("trading_class", ""),
                front_month=str(cfg.get("front_month", "")),
                roll_rule=cfg.get("roll_rule", ""),
                yfinance_symbol=cfg.get("yfinance_symbol", ""),
            )

    def get(self, symbol: str) -> InstrumentSpec:
        """查询合约规格，不存在则返回 STK 默认值"""
        return self._specs.get(symbol.upper(), InstrumentSpec(symbol=symbol.upper()))

    @property
    def futures_symbols(self) -> list:
        return [s for s, spec in self._specs.items() if spec.is_futures]

    @property
    def all_symbols(self) -> list:
        return list(self._specs.keys())


_INSTRUMENT_REGISTRY: Optional[InstrumentRegistry] = None


def get_instrument_registry() -> InstrumentRegistry:
    """获取全局合约注册表（单例，延迟加载）"""
    global _INSTRUMENT_REGISTRY
    if _INSTRUMENT_REGISTRY is None:
        project_root = Path(__file__).parent.parent
        instruments_path = project_root / "config" / "instruments.yaml"
        _INSTRUMENT_REGISTRY = InstrumentRegistry(str(instruments_path))
    return _INSTRUMENT_REGISTRY


@dataclass
class IBKRConfig:
    """IBKR 完整配置"""
    market_data_source: str = "auto"  # "auto", "ibkr", "yfinance"
    approval_required: bool = False   # true: 信号写入审批队列，用户 approve 后才提交 IBKR
    allow_short_selling: bool = False  # true=允许做空，false=Long Only（禁止主动做空）
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    risk_engine: RiskConfig = field(default_factory=RiskConfig)
    universe_selector: UniverseSelectorConfig = field(default_factory=UniverseSelectorConfig)

    @classmethod
    def from_yaml(cls, config_path: str) -> "IBKRConfig":
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or "ibkr" not in data:
            raise ValueError("配置文件格式错误：缺少ibkr节点")

        ibkr_data = data["ibkr"]
        market_data_source = ibkr_data.get("market_data_source", "auto")
        if market_data_source not in ("auto", "ibkr", "yfinance"):
            raise ValueError(f"market_data_source 必须是 'auto', 'ibkr' 或 'yfinance'，当前值: {market_data_source}")
        approval_required = ibkr_data.get("approval_required", False)
        allow_short_selling = ibkr_data.get("trading", {}).get("allow_short_selling", False)
        gateway_data = ibkr_data.get("gateway", {})

        client_id = gateway_data.get("client_id", "random")
        if client_id == "random" or client_id is None or client_id == 0:
            client_id = random.randint(100, 999)
        else:
            client_id = int(client_id)

        account_id = os.getenv("IBKR_ACCOUNT_ID", gateway_data.get("account_id", ""))
        gateway = GatewayConfig(
            host=os.getenv("IBKR_HOST", gateway_data.get("host", "127.0.0.1")),
            port=int(os.getenv("IBKR_PORT", gateway_data.get("port", 4001))),
            client_id=client_id,
            timeout=int(os.getenv("IBKR_TIMEOUT", gateway_data.get("timeout", 15))),
            max_retries=int(os.getenv("IBKR_MAX_RETRIES", gateway_data.get("max_retries", 3))),
            retry_delay=int(os.getenv("IBKR_RETRY_DELAY", gateway_data.get("retry_delay", 2))),
            account_id=account_id,
        )
        strong_acc_cfg = load_strong_accumulation_config()

        watch_data = ibkr_data.get("watch", {})
        templates_raw = watch_data.get("templates", {})
        cooldown_raw = watch_data.get("cooldown_minutes", {"default": 20})

        watch = WatchConfig(
            templates=templates_raw,
            cooldown_minutes=cooldown_raw,
            poll_interval=watch_data.get("poll_interval", 5),
            indicator_refresh_minutes=watch_data.get("indicator_refresh_minutes", 30),
            template_dir=watch_data.get("template_dir", "strategy/templates"),
            real_cooldown_multiplier=watch_data.get("real_cooldown_multiplier", 4.0),
            strong_accumulation=strong_acc_cfg,
        )

        risk_data = ibkr_data.get("risk_engine", {})
        risk_engine = RiskConfig(
            enabled=risk_data.get("enabled", True),
            fail_closed=risk_data.get("fail_closed", False),
            position_limit_pct=risk_data.get("position_limit_pct", 20.0),
            max_order_value_pct=risk_data.get("max_order_value_pct", 50.0),
            tfsa_limitation=risk_data.get("tfsa_limitation", True),
        )

        universe_selector = UniverseSelectorConfig(
            enabled=True,
            capacity=strong_acc_cfg.capacity,
            max_positions=strong_acc_cfg.max_positions,
            min_positions=strong_acc_cfg.min_positions,
            required_passing=strong_acc_cfg.required_passing,
            min_score_threshold=strong_acc_cfg.min_score_threshold,
            take_profit_pct=strong_acc_cfg.take_profit_pct,
            stop_loss_pct=strong_acc_cfg.stop_loss_pct,
            reduce_ratio=strong_acc_cfg.reduce_ratio,
            blacklist=strong_acc_cfg.blacklist,
            default_position_size_pct=strong_acc_cfg.default_position_size_pct,
            top_n=strong_acc_cfg.top_n,
        )

        return cls(market_data_source=market_data_source, approval_required=approval_required,
                  allow_short_selling=allow_short_selling,
                  gateway=gateway, watch=watch, risk_engine=risk_engine,
                  universe_selector=universe_selector)


# =============================================================================
# 自适应学习配置 (Phase 3 D35)
# =============================================================================

@dataclass
class LearningConfig:
    """自适应学习引擎配置"""
    enabled: bool = False
    min_sample_trades: int = 5
    analysis_interval_hours: int = 24
    cross_strategy_learning: bool = False
    strategy_type_map: dict = field(default_factory=dict)


@dataclass
class ShadowTradingConfig:
    """反事实分析配置"""
    enabled: bool = False
    track_days: int = 30
    include_in_report: bool = True


@dataclass
class AdaptiveConfig:
    """自适应进化系统完整配置"""
    learning: LearningConfig = field(default_factory=LearningConfig)
    shadow_trading: ShadowTradingConfig = field(default_factory=ShadowTradingConfig)

    @classmethod
    def from_yaml(cls, config_path: str) -> "AdaptiveConfig":
        path = Path(config_path)
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data:
            return cls()
        learning_data = data.get("learning", {})
        shadow_data = data.get("shadow_trading", {})
        return cls(
            learning=LearningConfig(
                enabled=learning_data.get("enabled", False),
                min_sample_trades=learning_data.get("min_sample_trades", 5),
                analysis_interval_hours=learning_data.get("analysis_interval_hours", 24),
                cross_strategy_learning=learning_data.get("cross_strategy_learning", False),
                strategy_type_map=learning_data.get("strategy_type_map", {}),
            ),
            shadow_trading=ShadowTradingConfig(
                enabled=shadow_data.get("enabled", False),
                track_days=shadow_data.get("track_days", 30),
                include_in_report=shadow_data.get("include_in_report", True),
            ),
        )


_ADAPTIVE_CONFIG_CACHE: Optional[AdaptiveConfig] = None


def load_adaptive_config() -> AdaptiveConfig:
    """加载自适应进化配置 (带缓存)"""
    global _ADAPTIVE_CONFIG_CACHE
    if _ADAPTIVE_CONFIG_CACHE is None:
        project_root = Path(__file__).parent.parent
        config_path = project_root / "config" / "adaptive.yaml"
        _ADAPTIVE_CONFIG_CACHE = AdaptiveConfig.from_yaml(str(config_path))
    return _ADAPTIVE_CONFIG_CACHE


def load_config(config_path: Optional[str] = None) -> IBKRConfig:
    if config_path is None:
        project_root = Path(__file__).parent.parent
        config_path = project_root / "config" / "ibkr.yaml"

    return IBKRConfig.from_yaml(str(config_path))