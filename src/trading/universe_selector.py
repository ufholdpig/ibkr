"""
候选池动态管理器 (UniverseSelector)

功能：
1. 每日盘后扫描，更新候选池
2. 评估持仓标的，决定加仓/减仓/平仓
3. 无持仓时，从候选池选择最优标的

集成：
- 由 post_market.py 在生成盘后报告前调用
- 输出调仓建议信号，供 WatchDaemon 执行

设计文档：docs/strong_accumulation-design.md
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Set

from src.core.strategy import MarketData

logger = logging.getLogger(__name__)


class PoolAction(Enum):
    """池状态评估动作"""
    HOLD = "hold"           # 持有，无动作
    ADD = "add"             # 加仓
    REDUCE = "reduce"       # 减仓
    CLOSE = "close"        # 平仓
    OPEN = "open"           # 开仓（新标的）
    SKIP = "skip"           # 跳过（不在池中）


@dataclass
class Candidate:
    """候选标的"""
    symbol: str
    score: float = 0.0
    
    # 评分细节
    ma200_slope_pass: bool = False      # MA200走平 (±3°)
    price_above_ma200_pass: bool = False  # 价格距MA200 >10%
    bottom_raised_pass: bool = False   # 底部抬高
    trend_up_pass: bool = False        # 短期趋势向上
    rsi_pass: bool = False             # RSI 40-70
    volume_pass: bool = False          # 量能放大 >1.3x
    position_52w_pass: bool = False    # 52周位置 30-80%
    
    # 原始指标值
    price: float = 0.0
    ma200_slope: float = 0.0
    price_deviation: float = 0.0
    rsi: float = 0.0
    volume_ratio: float = 0.0
    
    # 通过条件计数
    passing_count: int = 0
    
    def calc_score(self) -> float:
        """计算总得分"""
        weights = {
            "ma200_slope": 1.0,
            "price_above_ma200": 1.5,
            "bottom_raised": 1.5,
            "trend_up": 1.0,
            "rsi": 1.0,
            "volume": 1.5,
            "position_52w": 1.0,
        }
        
        self.passing_count = sum([
            self.ma200_slope_pass,
            self.price_above_ma200_pass,
            self.bottom_raised_pass,
            self.trend_up_pass,
            self.rsi_pass,
            self.volume_pass,
            self.position_52w_pass,
        ])
        
        self.score = sum([
            self.ma200_slope_pass * weights["ma200_slope"],
            self.price_above_ma200_pass * weights["price_above_ma200"],
            self.bottom_raised_pass * weights["bottom_raised"],
            self.trend_up_pass * weights["trend_up"],
            self.rsi_pass * weights["rsi"],
            self.volume_pass * weights["volume"],
            self.position_52w_pass * weights["position_52w"],
        ])
        
        return self.score
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": self.score,
            "passing_count": self.passing_count,
            "price": self.price,
            "ma200_slope": self.ma200_slope,
            "price_deviation": self.price_deviation,
            "rsi": self.rsi,
            "volume_ratio": self.volume_ratio,
        }


@dataclass
class PositionReview:
    """持仓评审结果"""
    symbol: str
    action: PoolAction
    reason: str
    
    # 标的池状态
    in_pool: bool = False
    pool_rank: int = 0
    pool_score: float = 0.0
    
    # 当前持仓状态
    current_qty: float = 0.0
    current_price: float = 0.0
    avg_cost: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    
    # 建议动作
    suggested_qty_change: float = 0.0  # 正=买入，负=卖出
    suggested_reason: str = ""


@dataclass
class UniverseSelectorReport:
    """候选池评估报告"""
    generated: str
    report_date: str
    
    # 候选池状态
    candidate_pool: List[Candidate] = field(default_factory=list)
    candidate_count: int = 0
    
    # 持仓评审结果
    position_reviews: List[PositionReview] = field(default_factory=list)
    
    # 建仓建议（当无持仓时）
    opening_suggestions: List[Candidate] = field(default_factory=list)
    
    # 汇总
    actions_summary: Dict[str, int] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "generated": self.generated,
            "report_date": self.report_date,
            "candidate_count": self.candidate_count,
            "candidates": [c.to_dict() for c in self.candidate_pool],
            "position_reviews": [
                {
                    "symbol": r.symbol,
                    "action": r.action.value,
                    "reason": r.reason,
                    "in_pool": r.in_pool,
                    "suggested_qty_change": r.suggested_qty_change,
                    "unrealized_pnl": r.unrealized_pnl,
                    "unrealized_pnl_pct": r.unrealized_pnl_pct,
                }
                for r in self.position_reviews
            ],
            "opening_suggestions": [c.to_dict() for c in self.opening_suggestions],
            "actions_summary": self.actions_summary,
        }


class UniverseSelector:
    """候选池动态管理器
    
    每日盘后调用流程：
    1. refresh() — 获取候选池（从手动配置或动态筛选）
    2. evaluate_positions() — 评审当前持仓
    3. suggest_openings() — 无持仓时建议建仓标的
    4. generate_report() — 生成评估报告
    """
    
    def __init__(self, candidate_symbols: List[str] = None, config: dict = None):
        """初始化
        
        Args:
            candidate_symbols: 手动配置的候选标的列表（初始方案）
            config: 策略配置（从 ibkr.yaml 读取）
        """
        cfg = config or {}
        
        # 通过条件阈值
        self.REQUIRED_PASSING: int = cfg.get("required_passing", 4)
        self.MIN_SCORE_THRESHOLD: float = cfg.get("min_score_threshold", 4.0)
        
        # 持仓数量限制
        self.MAX_POSITIONS: int = cfg.get("max_positions", 2)
        self.MIN_POSITIONS: int = cfg.get("min_positions", 1)
        
        # 持仓评估参数
        review_cfg = cfg.get("position_review", {})
        self.TAKE_PROFIT_PCT: float = review_cfg.get("take_profit_pct", 20.0)
        self.STOP_LOSS_PCT: float = review_cfg.get("stop_loss_pct", -10.0)
        self.REDUCE_RATIO: float = review_cfg.get("reduce_ratio", 0.5)
        
        # 建仓参数
        opening_cfg = cfg.get("opening", {})
        self.DEFAULT_POSITION_SIZE_PCT: float = opening_cfg.get("default_position_size_pct", 10.0)
        self.TOP_N: int = opening_cfg.get("top_n", 3)
        
        # 候选池容量（盘后刷新后保留 top N）
        self.CAPACITY: int = cfg.get("capacity", 10)
        
        self._candidate_symbols: Set[str] = set(candidate_symbols or [])
        self.candidates: List[Candidate] = []
        self._top_n_for_save: List[str] = []  # 总是保存得分最高的 top N（含未通过评审的）
        self.last_refresh: Optional[datetime] = None
        
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info(
            f"UniverseSelector 初始化: 候选标的={len(self._candidate_symbols)}, "
            f"容量={self.CAPACITY}, 持仓限制={self.MIN_POSITIONS}-{self.MAX_POSITIONS}, "
            f"入围条件≥{self.REQUIRED_PASSING}, 得分≥{self.MIN_SCORE_THRESHOLD}"
        )
    
    @property
    def top2(self) -> List[str]:
        """当前排名前2的标的（用于 top2 决策框架）"""
        return [c.symbol for c in self.candidates[:2]]
    
    @property
    def candidate_symbols(self) -> Set[str]:
        """当前候选标的集合"""
        return self._candidate_symbols.copy()
    
    def set_candidate_pool(self, symbols: List[str]):
        """手动设置候选池"""
        self._candidate_symbols = set(symbols)
        self.logger.info(f"更新候选池: {len(symbols)} 只")
    
    def add_candidate(self, symbol: str):
        """添加单个候选标的"""
        self._candidate_symbols.add(symbol)
    
    def remove_candidate(self, symbol: str):
        """移除候选标的"""
        self._candidate_symbols.discard(symbol)
    
    def refresh(self, market_data_map: Dict[str, MarketData],
                historical_data: Dict[str, List] = None,
                symbols: List[str] = None) -> List[Candidate]:
        """刷新候选池

        Args:
            market_data_map: 市场数据 (symbol -> MarketData)
            historical_data: 历史K线数据 (symbol -> List[Bar])
            symbols: 可选，覆盖默认的 _candidate_symbols（用于 scope=full 全量扫描）
        """
        self.logger.debug(f"刷新候选池 START，候选标的数: {len(self._candidate_symbols)}")

        candidates = []
        hist_data = historical_data or {}

        # 优先使用传入的 symbols，否则用内部的 _candidate_symbols
        symbols_to_evaluate = symbols if symbols is not None else list(self._candidate_symbols)

        for symbol in symbols_to_evaluate:
            data = market_data_map.get(symbol)
            if not data:
                continue
            
            candidate = self._evaluate_candidate(symbol, data, hist_data.get(symbol, []))
            if candidate:
                candidates.append(candidate)
        
        # 按得分排序
        candidates.sort(key=lambda x: x.score, reverse=True)

        # 保存所有评估过的标的（用于持仓判断 + YAML写回）
        self._evaluated_candidates = candidates

        # 过滤：只保留满足条件的（用于评审报告）
        self.candidates = [
            c for c in candidates
            if c.passing_count >= self.REQUIRED_PASSING and c.score >= self.MIN_SCORE_THRESHOLD
        ]

        # 应用容量限制（取 top N）— 基于所有评估过的标的写回YAML
        # 确保即使0只通过评审，top N 仍被保存（供次日观察）
        top_n = candidates[:self.CAPACITY] if self.CAPACITY else candidates
        self._top_n_for_save = [c.symbol for c in top_n]
        
        self.last_refresh = datetime.now()
        
        self.logger.info(f"候选池刷新完成: {len(self.candidates)} 只通过评审")
        for i, c in enumerate(self.candidates[:5]):
            self.logger.debug(f"  #{i+1} {c.symbol}: score={c.score:.1f}, pass={c.passing_count}/7")
        
        self.logger.debug(f"刷新候选池 END，通过评审 {len(self.candidates)} 只，评估 {len(self._evaluated_candidates)} 只")
        return self.candidates
    
    def _evaluate_candidate(self, symbol: str, data: MarketData, 
                            bars: List) -> Optional[Candidate]:
        """评估单个候选标的"""
        c = Candidate(symbol=symbol, price=data.price)
        
        # 1. MA200 slope (±3°内)
        if data.ma_200_slope is not None:
            c.ma200_slope = data.ma_200_slope
            c.ma200_slope_pass = abs(data.ma_200_slope) <= 3.0
        
        # 2. 价格距MA200 (>10%)
        if data.ma_200 and data.ma_200 > 0:
            c.price_deviation = (data.price - data.ma_200) / data.ma_200
            c.price_above_ma200_pass = c.price_deviation > 0.10
        
        # 3. 底部抬高（90日低点 > 180日前低点）
        if len(bars) >= 180:
            low_90d = min(b.low for b in bars[-90:])
            low_180d_ago = min(b.low for b in bars[-180:-90])
            c.bottom_raised_pass = low_90d > low_180d_ago
        
        # 4. 短期趋势向上（MA20 > MA50 且 MA50向上）
        if data.ma_20 and data.ma_50 and data.ma_20 > data.ma_50:
            if data.ma_50_slope and data.ma_50_slope > 0:
                c.trend_up_pass = True
        
        # 5. RSI健康 (40-70)
        if data.rsi_14 is not None:
            c.rsi = data.rsi_14
            c.rsi_pass = 40 <= data.rsi_14 <= 70
        
        # 6. 量能放大（近5日 > 90日 × 1.3）
        if data.volume_ratio is not None:
            c.volume_ratio = data.volume_ratio
            c.volume_pass = data.volume_ratio >= 1.3
        
        # 7. 52周位置 (30%-80%)
        if data.high_52w and data.low_52w:
            range_52w = data.high_52w - data.low_52w
            if range_52w > 0:
                position = (data.price - data.low_52w) / range_52w
                c.position_52w_pass = 0.30 <= position <= 0.80
        
        c.calc_score()
        return c
    
    def evaluate_positions(self, positions: List[dict], 
                            scope: str = "post_market",
                            old_top2: List[str] = None) -> List[PositionReview]:
        """评审当前持仓（Top2 决策框架）
        
        Args:
            positions: 持仓列表，每项包含 symbol/qty/avg_cost/market_price
            scope: "intra_day"（盘中）或 "post_market"（盘后）
            old_top2: 旧池的前2名（刷新前的 ibkr.yaml top2）
        
        Returns:
            持仓评审结果列表
        """
        self.logger.info(f"评审持仓，{len(positions)} 个标的（scope={scope}）")
        
        if old_top2 is None:
            old_top2 = []
        new_top2 = self.top2
        
        self.logger.info(f"Top2 变化: {old_top2} → {new_top2}")
        
        # 候选池：排序后通过阈值的（用于建仓决策）
        pool_symbols = {c.symbol for c in self.candidates}
        # 评估过的标的：所有经过评估的候选（用于持仓判断：是否曾经通过评估）
        qualified_symbols = {c.symbol for c in getattr(self, '_evaluated_candidates', [])}
        pool_rank_map = {c.symbol: i+1 for i, c in enumerate(self.candidates)}
        
        reviews = []
        for pos in positions:
            symbol = pos.get("symbol", "")
            qty = pos.get("quantity", 0) or pos.get("qty", 0)
            avg_cost = pos.get("avg_cost", 0) or pos.get("average_cost", 0)
            market_price = pos.get("market_price", 0) or pos.get("price", 0)
            
            if qty <= 0:
                continue
            
            in_pool = symbol in qualified_symbols
            in_new_top2 = symbol in new_top2
            in_old_top2 = symbol in old_top2
            pool_rank = pool_rank_map.get(symbol, 0)
            
            # 计算盈亏
            unrealized_pnl = (market_price - avg_cost) * qty
            unrealized_pnl_pct = (market_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
            
            review = PositionReview(
                symbol=symbol,
                action=PoolAction.HOLD,  # 默认持有
                reason="默认持有",
                in_pool=in_pool,
                pool_rank=pool_rank,
                pool_score=0,
                current_qty=qty,
                current_price=market_price,
                avg_cost=avg_cost,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
            )
            
            # Top2 决策逻辑
            if in_new_top2:
                if in_old_top2:
                    # 一直在 top2 内：持有
                    review.action = PoolAction.HOLD
                    review.reason = f"Top2 内（排名#{pool_rank}），持有"
                    review.suggested_qty_change = 0
                    review.suggested_reason = "top2 稳定"
                else:
                    # 新入 top2：加仓
                    review.action = PoolAction.ADD
                    review.reason = f"新入 Top2（排名#{pool_rank}），加仓"
                    review.suggested_qty_change = int(qty * 0.5)  # 加半仓
                    review.suggested_reason = "新入 top2"
            else:
                # 退出 top2
                if not in_pool:
                    # 完全不在池中（盘后）：清仓
                    review.action = PoolAction.CLOSE
                    review.reason = f"标的已不在候选池（排名#{pool_rank}），触发平仓"
                    review.suggested_qty_change = -qty
                    review.suggested_reason = "不在候选池内"
                else:
                    # 还在池内但不在 top2
                    if scope == "intra_day":
                        # 盘中：减仓
                        review.action = PoolAction.REDUCE
                        review.reason = f"退出 Top2（排名#{pool_rank}），适度减仓"
                        review.suggested_qty_change = -int(qty * self.REDUCE_RATIO)
                        review.suggested_reason = "退出 top2（盘中）"
                    else:
                        # 盘后：清仓
                        review.action = PoolAction.CLOSE
                        review.reason = f"退出 Top2（排名#{pool_rank}），触发平仓"
                        review.suggested_qty_change = -qty
                        review.suggested_reason = "退出 top2（盘后）"
            
            reviews.append(review)
            self.logger.info(f"  {symbol}: {review.action.value} - {review.reason}")
        
        return reviews
    
    def suggest_openings(self, account_balance: float = 0) -> List[Candidate]:
        """建议建仓标的（当无持仓时）
        
        Args:
            account_balance: 账户余额（用于计算仓位）
        
        Returns:
            建仓建议列表（按得分排序，取前 TOP_N）
        """
        suggestions = self.candidates[:self.TOP_N]
        
        self.logger.info(f"建仓建议(共{len(suggestions)}只): {[c.symbol for c in suggestions]}")
        for c in suggestions:
            position_value = account_balance * (self.DEFAULT_POSITION_SIZE_PCT / 100.0) if account_balance > 0 else 10000
            shares = int(position_value / c.price / 100) * 100  # 取整百股
            self.logger.info(f"  {c.symbol}: 建议买入{shares}股 @ ${c.price:.2f}")
        
        return suggestions
    
    def generate_report(self, report_date: str, positions: List[dict] = None) -> UniverseSelectorReport:
        """生成候选池评估报告
        
        Args:
            report_date: 报告日期 YYYYMMDD
            positions: 当前持仓列表
        
        Returns:
            评估报告
        """
        report = UniverseSelectorReport(
            generated=datetime.now().isoformat(),
            report_date=report_date,
            candidate_pool=self.candidates,
            candidate_count=len(self.candidates),
        )
        
        # 持仓评审
        if positions:
            report.position_reviews = self.evaluate_positions(positions)
        else:
            report.position_reviews = []
        
        # 建仓建议（如果无持仓）
        if not positions or all(p.get("quantity", 0) <= 0 for p in positions):
            report.opening_suggestions = self.suggest_openings()
        
        # 汇总动作
        summary: Dict[str, int] = {}
        for review in report.position_reviews:
            action = review.action.value
            summary[action] = summary.get(action, 0) + 1
        report.actions_summary = summary
        
        return report


# ============================================================
# 手动配置的候选池
# ============================================================

def create_universe_selector() -> UniverseSelector:
    """创建标的池选择器（从 strong_accumulation.yaml 读取）

    配置来源：strategy/templates/strong_accumulation.yaml
    - candidate_pool: 初始候选池（scope=pool_only 使用，scope=full 后动态更新）
    - blacklist: 永不纳入的标的
    - 策略参数（capacity/max_positions/required_passing 等）
    """
    from config.config import load_strong_accumulation_config

    sa_config = load_strong_accumulation_config()

    # 候选池（过滤黑名单）
    candidate_pool = sa_config.candidate_pool or []
    blacklist = set(sa_config.blacklist or [])
    candidates = [s for s in candidate_pool if s not in blacklist]

    # 构建参数字典（与 UniverseSelector.__init__ 期望格式一致）
    strategy_cfg = {
        "required_passing": sa_config.required_passing,
        "min_score_threshold": sa_config.min_score_threshold,
        "max_positions": sa_config.max_positions,
        "min_positions": sa_config.min_positions,
        "position_review": {
            "take_profit_pct": sa_config.take_profit_pct,
            "stop_loss_pct": sa_config.stop_loss_pct,
            "reduce_ratio": sa_config.reduce_ratio,
        },
        "opening": {
            "default_position_size_pct": sa_config.default_position_size_pct,
            "top_n": sa_config.top_n,
        },
    }

    selector = UniverseSelector(candidate_symbols=candidates, config=strategy_cfg)

    # 设置候选池容量（从配置读取）
    selector.CAPACITY = sa_config.capacity

    return selector