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


@dataclass
class ReportConfig:
    """报告目录配置"""
    auto_select: bool = True
    default_base: str = "reports"
    real: dict = field(default_factory=lambda: {
        "base": "reports/real",
        "pre_market": "reports/real/pre_market",
        "intra_day": "reports/real/intra_day",
        "post_market": "reports/real/post_market"
    })
    paper: dict = field(default_factory=lambda: {
        "base": "reports/paper",
        "pre_market": "reports/paper/pre_market",
        "intra_day": "reports/paper/intra_day",
        "post_market": "reports/paper/post_market"
    })
@dataclass
class SymbolWatchConfig:
    """个股 Watch 配置"""
    strategies: list = field(default_factory=list)
    cooldown_minutes: int = 15


@dataclass
class WatchConfig:
    """Watch 守护进程配置"""
    symbols: dict = field(default_factory=dict)  # symbol -> SymbolWatchConfig
    poll_interval: int = 5
    indicator_refresh_minutes: int = 30
    strategy_dir: str = "strategy/strategies"
    real_cooldown_multiplier: float = 4.0

    @property
    def symbol_list(self) -> list[str]:
        return list(self.symbols.keys())


@dataclass
class RiskConfig:
    """TFSA 风控引擎配置"""
    enabled: bool = True
    fail_closed: bool = False       # true: 风控不可用时拒绝交易（实盘必须 true）
    approval_required: bool = False  # true: 信号写入审批队列，用户 approve 后才提交 IBKR
    position_limit_pct: float = 20.0
    max_order_value_pct: float = 50.0  # 单笔订单价值 ≤ 账户净值的百分比
    max_trades_per_year: int = 80
    forbid_short_sell: bool = True
    forbid_day_trading: bool = True


@dataclass
class IBKRConfig:
    """IBKR 完整配置"""
    market_data_source: str = "auto"  # "auto", "ibkr", "yfinance"
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    reports: ReportConfig = field(default_factory=ReportConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    risk_engine: RiskConfig = field(default_factory=RiskConfig)

    @classmethod
    def from_yaml(cls, config_path: str) -> "IBKRConfig":
        """
        从YAML文件加载配置
        """
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
        gateway_data = ibkr_data.get("gateway", {})
        
        # 处理client_id随机值
        # 0 表示自动分配（随机 100-999），同 "random" / None
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
        
        # 加载报告配置
        reports_data = ibkr_data.get("reports", {})
        reports = ReportConfig(
            auto_select=reports_data.get("auto_select", True),
            default_base=reports_data.get("default_base", "reports"),
            real=reports_data.get("real", {
                "base": "reports/real",
                "pre_market": "reports/real/pre_market",
                "intra_day": "reports/real/intra_day",
                "post_market": "reports/real/post_market"
            }),
            paper=reports_data.get("paper", {
                "base": "reports/paper",
                "pre_market": "reports/paper/pre_market",
                "intra_day": "reports/paper/intra_day",
                "post_market": "reports/paper/post_market"
            })
        )
        
        # 加载 watch 守护进程配置
        watch_data = ibkr_data.get("watch", {})
        symbols_raw = watch_data.get("symbols", {"F": {"strategies": ["f_dip_buy.yaml", "f_bounce_sell.yaml"]}})
        default_strategies = watch_data.get("strategy_files", ["f_dip_buy.yaml", "f_bounce_sell.yaml"])
        if isinstance(symbols_raw, list):
            symbols_raw = {s.upper(): SymbolWatchConfig(strategies=list(default_strategies)) for s in symbols_raw}
        elif isinstance(symbols_raw, dict):
            symbols_raw = {
                sym.upper(): SymbolWatchConfig(
                    strategies=cfg.get("strategies", []),
                    cooldown_minutes=cfg.get("cooldown_minutes", 15),
                )
                for sym, cfg in symbols_raw.items()
            }
        else:
            raise TypeError(f"watch.symbols 必须是 list 或 dict，当前: {type(symbols_raw)}")
        watch = WatchConfig(
            symbols=symbols_raw,
            poll_interval=watch_data.get("poll_interval", 5),
            indicator_refresh_minutes=watch_data.get("indicator_refresh_minutes", 30),
            strategy_dir=watch_data.get("strategy_dir", "strategy/strategies"),
            real_cooldown_multiplier=watch_data.get("real_cooldown_multiplier", 4.0),
        )
        
        # 加载风控引擎配置
        risk_data = ibkr_data.get("risk_engine", {})
        risk_engine = RiskConfig(
            enabled=risk_data.get("enabled", True),
            fail_closed=risk_data.get("fail_closed", False),
            approval_required=risk_data.get("approval_required", False),
            position_limit_pct=risk_data.get("position_limit_pct", 20.0),
            max_order_value_pct=risk_data.get("max_order_value_pct", 50.0),
            max_trades_per_year=risk_data.get("max_trades_per_year", 80),
            forbid_short_sell=risk_data.get("forbid_short_sell", True),
            forbid_day_trading=risk_data.get("forbid_day_trading", True),
        )
        
        return cls(market_data_source=market_data_source, gateway=gateway, reports=reports, watch=watch, risk_engine=risk_engine)


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
    """
    加载IBKR配置
    
    Args:
        config_path: 配置文件路径，默认为 config/ibkr.yaml
        
    Returns:
        IBKRConfig: 配置对象
    """
    if config_path is None:
        # 默认配置路径
        project_root = Path(__file__).parent.parent
        config_path = project_root / "config" / "ibkr.yaml"
    
    return IBKRConfig.from_yaml(str(config_path))
