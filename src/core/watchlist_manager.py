"""
标的池动态管理器 (WatchlistManager)

功能：
1. 空仓期：从全市场筛选潜力股，构建动态标的池
2. 建仓期：专注持仓监控，不新增标的
3. 平仓后：清空池，重新进入筛选模式

设计文档：docs/strong_accumulation-design.md
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Set

from src.core.strategy import MarketData

logger = logging.getLogger(__name__)


class PoolPhase(Enum):
    """标的池所处阶段"""
    EMPTY = "empty"      # 空仓期：动态筛选潜力股
    HOLDING = "holding"  # 建仓期：专注持仓
    PAUSED = "paused"    # 暂停：等待手动确认


@dataclass
class CandidateScore:
    """候选标的评分"""
    symbol: str
    total_score: float = 0.0
    
    # 技术面各维度得分
    ma200_slope_score: float = 0.0      # MA200走平 (±3°内)
    price_above_ma200_score: float = 0.0  # 价格距MA200 (>10%)
    bottom_raised_score: float = 0.0   # 底部抬高
    trend_up_score: float = 0.0        # 短期趋势向上
    rsi_score: float = 0.0             # RSI健康 (40-70)
    volume_score: float = 0.0          # 量能放大
    position_52w_score: float = 0.0   # 52周位置 (30%-80%)
    
    # 技术指标原始值（用于调试）
    ma200_slope: float = 0.0
    price_deviation: float = 0.0
    rsi: float = 0.0
    volume_ratio: float = 0.0
    
    # 额外属性（不存储）
    passing_conditions: int = 0
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_score": self.total_score,
            "ma200_slope_score": self.ma200_slope_score,
            "price_above_ma200_score": self.price_above_ma200_score,
            "bottom_raised_score": self.bottom_raised_score,
            "trend_up_score": self.trend_up_score,
            "rsi_score": self.rsi_score,
            "volume_score": self.volume_score,
            "position_52w_score": self.position_52w_score,
        }


@dataclass
class WatchlistEntry:
    """标的池条目"""
    symbol: str
    score: CandidateScore
    added_date: datetime
    last_updated: datetime
    signals_triggered: int = 0  # 累计触发信号次数
    in_watchlist: bool = True    # 是否在主标的池（vs观察名单）
    is_holding: bool = False     # 当前是否持仓


class WatchlistManager:
    """标的池动态管理器
    
    两阶段状态机：
    - EMPTY: 空仓期，每日扫描全市场，动态调整标的池
    - HOLDING: 建仓期，专注持仓监控，不新增标的
    - PAUSED: 暂停，等待手动确认
    """
    
    # ========== 配置参数 ==========
    
    # 标的池大小
    MAX_WATCHLIST_SIZE: int = 15
    OBSERVATION_LIST_SIZE: int = 20  # 观察名单上限
    
    # 硬性过滤条件
    MIN_AVG_VOLUME: int = 5_000_000     # 日均成交额 $5000万
    MIN_MARKET_CAP: float = 50_000_000_000  # 市值 $500亿
    MIN_LISTED_YEARS: int = 2           # 上市时间 > 2年
    
    # 技术面通过阈值（满足>=4个条件）
    REQUIRED_PASSING_CONDITIONS: int = 4
    
    # 各维度得分权重
    SCORE_WEIGHTS = {
        "ma200_slope": 1.0,        # MA200走平 (±3°内)
        "price_above_ma200": 1.5,  # 价格距MA200 (>10%)
        "bottom_raised": 1.5,      # 底部抬高
        "trend_up": 1.0,           # 短期趋势向上
        "rsi": 1.0,                # RSI健康
        "volume": 1.5,             # 量能放大
        "position_52w": 1.0,       # 52周位置
    }
    
    # 持仓锁定期间隔（天）
    MAX_HOLDING_DAYS: int = 180  # 6个月强制平仓
    STALE_DAYS_THRESHOLD: int = 120  # 120天无信号，移出标的池
    
    def __init__(self):
        self.phase: PoolPhase = PoolPhase.EMPTY
        self.watchlist: List[WatchlistEntry] = []   # 主标的池 (top 15)
        self.observation_list: List[WatchlistEntry] = []  # 观察名单
        
        self.holding_symbol: Optional[str] = None
        self.holding_since: Optional[datetime] = None
        self.holding_entry: Optional[WatchlistEntry] = None
        
        # 黑名单（永不纳入）
        self.blacklist: Set[str] = set()
        
        self.logger = logging.getLogger(self.__class__.__name__)
    
    # ========== 公共接口 ==========
    
    def add_to_blacklist(self, symbol: str):
        """将标的加入黑名单"""
        self.blacklist.add(symbol)
        self._remove_from_pools(symbol)
        self.logger.info(f"加入黑名单: {symbol}")
    
    def remove_from_blacklist(self, symbol: str):
        """将标的移出黑名单"""
        self.blacklist.discard(symbol)
    
    def on_position_opened(self, symbol: str, avg_cost: float = 0.0):
        """建仓回调"""
        if self.holding_symbol:
            self.logger.warning(f"已有持仓 {self.holding_symbol}，先平仓再开新仓")
            return False
        
        # 查找标的是否在池中
        entry = self._find_entry(symbol)
        if entry:
            entry.is_holding = True
        
        self.holding_symbol = symbol
        self.holding_since = datetime.now()
        self.holding_entry = entry
        self.phase = PoolPhase.HOLDING
        
        self.logger.info(f"建仓: {symbol}，切换到HOLDING模式")
        return True
    
    def on_position_closed(self, symbol: str, pnl: float = 0.0):
        """平仓回调"""
        if self.holding_symbol != symbol:
            self.logger.warning(f"平仓标的 {symbol} 与持仓 {self.holding_symbol} 不匹配")
            return False
        
        self.logger.info(f"平仓: {symbol}, PnL: ${pnl:.2f}")
        
        # 重置状态
        if self.holding_entry:
            self.holding_entry.is_holding = False
        
        self.holding_symbol = None
        self.holding_since = None
        self.holding_entry = None
        self.phase = PoolPhase.EMPTY
        
        # 清空标的池，下次扫描重新筛选
        self.watchlist = []
        self.observation_list = []
        
        return True
    
    def refresh_watchlist(self, market_data_map: Dict[str, MarketData], 
                         historical_data: Dict[str, List]) -> int:
        """刷新标的池
        
        Args:
            market_data_map: 当前市场数据 (symbol -> MarketData)
            historical_data: 历史数据 (symbol -> List[Bar])
        
        Returns:
            纳入标的池的标的数量
        """
        if self.phase == PoolPhase.HOLDING:
            self.logger.debug("HOLDING模式，跳过标的池刷新")
            return 0
        
        self.logger.info(f"开始刷新标的池，候选标的数: {len(market_data_map)}")
        
        # Step 1: 硬性过滤
        candidates = self._apply_hard_filters(market_data_map)
        self.logger.info(f"硬性过滤后: {len(candidates)} 个候选")
        
        if not candidates:
            return 0
        
        # Step 2: 技术面评分
        scored = self._calculate_scores(candidates, market_data_map, historical_data)
        
        # Step 3: 排序并分组
        self._rank_and_assign(scored)
        
        self.logger.info(f"标的池刷新完成: {len(self.watchlist)} 只在池, {len(self.observation_list)} 只在观察")
        
        return len(self.watchlist)
    
    def get_candidates_for_signal(self) -> List[str]:
        """获取可生成信号的标的列表（供策略引擎使用）"""
        if self.phase == PoolPhase.HOLDING and self.holding_symbol:
            # 建仓期：只返回持仓标的
            return [self.holding_symbol]
        
        # 空仓期：返回标的池内所有标的
        return [e.symbol for e in self.watchlist if e.in_watchlist]
    
    def get_watchlist(self) -> List[WatchlistEntry]:
        """获取当前标的池"""
        return self.watchlist.copy()
    
    def get_observation_list(self) -> List[WatchlistEntry]:
        """获取观察名单"""
        return self.observation_list.copy()
    
    def get_status(self) -> dict:
        """获取当前状态摘要"""
        return {
            "phase": self.phase.value,
            "watchlist_size": len(self.watchlist),
            "observation_size": len(self.observation_list),
            "holding_symbol": self.holding_symbol,
            "holding_days": (datetime.now() - self.holding_since).days if self.holding_since else 0,
            "blacklist_size": len(self.blacklist),
        }
    
    # ========== 内部方法 ==========
    
    def _apply_hard_filters(self, market_data_map: Dict[str, MarketData]) -> List[str]:
        """硬性过滤：市值、成交量、流动性等"""
        candidates = []
        
        for symbol, data in market_data_map.items():
            # 黑名单检查
            if symbol in self.blacklist:
                continue
            
            # 成交量过滤（日均成交额 > $5000万）
            # 简化：volume * price > $5000万
            if data.volume > 0:
                daily_volume = data.volume * data.price
                if daily_volume < self.MIN_AVG_VOLUME:
                    continue
            
            # 市值过滤（如果有数据）
            if data.market_cap and data.market_cap < self.MIN_MARKET_CAP:
                continue
            
            candidates.append(symbol)
        
        return candidates
    
    def _calculate_scores(self, candidates: List[str],
                         market_data_map: Dict[str, MarketData],
                         historical_data: Dict[str, List]) -> List[CandidateScore]:
        """计算每个候选标的的技术面得分"""
        results = []
        
        for symbol in candidates:
            data = market_data_map.get(symbol)
            hist = historical_data.get(symbol, [])
            
            if not data:
                continue
            
            score = CandidateScore(symbol=symbol)
            
            # === 各维度评分 ===
            
            # 1. MA200 slope (±3°内 = 1分)
            if data.ma_200_slope is not None:
                score.ma200_slope = data.ma_200_slope
                if abs(data.ma_200_slope) <= 3.0:
                    score.ma200_slope_score = 1.0 * self.SCORE_WEIGHTS["ma200_slope"]
            
            # 2. 价格距MA200 (>10% = 1分)
            if data.ma_200 and data.ma_200 > 0:
                score.price_deviation = (data.price - data.ma_200) / data.ma_200
                if score.price_deviation > 0.10:
                    score.price_above_ma200_score = 1.0 * self.SCORE_WEIGHTS["price_above_ma200"]
            
            # 3. 底部抬高（90日低点 > 180日前低点）
            # 需要历史数据，此处简化处理
            if len(hist) >= 180:
                low_90d = min(b.low for b in hist[-90:])
                low_180d_ago = min(b.low for b in hist[-180:-90]) if len(hist) >= 180 else float('inf')
                if low_90d > low_180d_ago:
                    score.bottom_raised_score = 1.0 * self.SCORE_WEIGHTS["bottom_raised"]
            
            # 4. 短期趋势向上（MA20向上且>MA50）
            if data.ma_20 and data.ma_50 and data.ma_20 > data.ma_50:
                if data.ma_50_slope and data.ma_50_slope > 0:
                    score.trend_up_score = 1.0 * self.SCORE_WEIGHTS["trend_up"]
            
            # 5. RSI健康 (40-70)
            if data.rsi_14 is not None:
                score.rsi = data.rsi_14
                if 40 <= data.rsi_14 <= 70:
                    score.rsi_score = 1.0 * self.SCORE_WEIGHTS["rsi"]
            
            # 6. 量能放大（近5日均量 > 90日均量 × 1.3）
            if data.volume_ratio is not None and data.volume_ratio >= 1.3:
                score.volume_score = 1.0 * self.SCORE_WEIGHTS["volume"]
            
            # 7. 52周位置 (30%-80%)
            if data.high_52w and data.low_52w:
                range_52w = data.high_52w - data.low_52w
                if range_52w > 0:
                    position = (data.price - data.low_52w) / range_52w
                    if 0.30 <= position <= 0.80:
                        score.position_52w_score = 1.0 * self.SCORE_WEIGHTS["position_52w"]
            
            # 计算总分
            score.total_score = (
                score.ma200_slope_score +
                score.price_above_ma200_score +
                score.bottom_raised_score +
                score.trend_up_score +
                score.rsi_score +
                score.volume_score +
                score.position_52w_score
            )
            
            results.append(score)
        
        # 按总分排序
        results.sort(key=lambda x: x.total_score, reverse=True)
        
        return results
    
    def _rank_and_assign(self, scored: List[CandidateScore]):
        """排序并分配到标的池/观察名单"""
        now = datetime.now()
        
        # 统计通过条件数
        for s in scored:
            pass_count = sum([
                s.ma200_slope_score > 0,
                s.price_above_ma200_score > 0,
                s.bottom_raised_score > 0,
                s.trend_up_score > 0,
                s.rsi_score > 0,
                s.volume_score > 0,
                s.position_52w_score > 0,
            ])
            # 扩展得分记录
            s.passing_conditions = pass_count
        
        # 取top15进入标的池
        watchlist = []
        observations = []
        
        for score in scored[:self.MAX_WATCHLIST_SIZE]:
            # 检查是否满足技术面通过条件（>=4个条件）
            pass_count = getattr(score, 'passing_conditions', 0)
            if pass_count >= self.REQUIRED_PASSING_CONDITIONS:
                entry = self._find_or_create_entry(score.symbol, now)
                entry.score = score
                entry.last_updated = now
                watchlist.append(entry)
            else:
                # 不满足条件但仍在观察名单
                if len(observations) < self.OBSERVATION_LIST_SIZE:
                    entry = self._find_or_create_entry(score.symbol, now)
                    entry.score = score
                    entry.last_updated = now
                    entry.in_watchlist = False
                    observations.append(entry)
        
        self.watchlist = watchlist
        self.observation_list = observations
    
    def _find_entry(self, symbol: str) -> Optional[WatchlistEntry]:
        """查找标的条目"""
        for entry in self.watchlist:
            if entry.symbol == symbol:
                return entry
        for entry in self.observation_list:
            if entry.symbol == symbol:
                return entry
        return None
    
    def _find_or_create_entry(self, symbol: str, now: datetime) -> WatchlistEntry:
        """查找或创建标的条目"""
        existing = self._find_entry(symbol)
        if existing:
            return existing
        
        return WatchlistEntry(
            symbol=symbol,
            score=CandidateScore(symbol=symbol),
            added_date=now,
            last_updated=now,
        )
    
    def _remove_from_pools(self, symbol: str):
        """从标的池和观察名单中移除"""
        self.watchlist = [e for e in self.watchlist if e.symbol != symbol]
        self.observation_list = [e for e in self.observation_list if e.symbol != symbol]