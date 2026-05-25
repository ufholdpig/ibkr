"""
市场数据提供者 — 统一数据源接口（IBKR / yfinance），计算技术指标

Phase 3: 数据计算层
目标: 填充 MarketData 所有技术指标字段 (rsi_14, ma_20, ma_50, volume_avg_20d, change_*_pct)
"""

from typing import List, Dict, Optional

from src.core.client import IBKRClient
from src.core.models import Bar
from src.core.strategy import MarketData
from src.core.logger import get_logger

logger = get_logger(__name__)


class MarketDataProvider:
    """市场数据提供者：统一 IBKR / yfinance 数据源，计算技术指标，补全 MarketData

    data_source 取值:
      - "auto":   优先 IBKR，失败自动回退 yfinance
      - "ibkr":   仅 IBKR（需要市场数据订阅）
      - "yfinance": 仅 yfinance（免费，适合模拟盘）
    """

    def __init__(self, client: IBKRClient, data_source: str = "auto"):
        self.client = client
        self.data_source = data_source

    # ── 基础行情（实时价格 + 成交量） ──────────────────────────────

    def fetch_basic(self, symbols: List[str]) -> Dict[str, MarketData]:
        """获取基础行情，按 data_source 路由"""
        if self.data_source in ("auto", "ibkr"):
            try:
                return self._fetch_basic_ibkr(symbols)
            except Exception as e:
                if self.data_source == "ibkr":
                    raise
                logger.warning(f"IBKR 基础行情失败: {e}, 回退 yfinance")

        if self.data_source in ("auto", "yfinance"):
            return self._fetch_basic_yfinance(symbols)

        return {}

    def _fetch_basic_ibkr(self, symbols: List[str]) -> Dict[str, MarketData]:
        raw = self.client.get_market_data(symbols)
        result = {}
        for symbol in symbols:
            data = raw.get(symbol, {})
            result[symbol] = MarketData(
                symbol=symbol,
                price=data.get("price", 0.0),
                volume=data.get("size", 0),
            )
        return result

    def _fetch_basic_yfinance(self, symbols: List[str]) -> Dict[str, MarketData]:
        import yfinance as yf
        result = {}
        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1d", interval="1m")
                if hist.empty:
                    logger.warning(f"yfinance [{symbol}] 返回空数据")
                    continue
                price = float(hist["Close"].iloc[-1])
                volume = int(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0
                result[symbol] = MarketData(symbol=symbol, price=price, volume=volume)
            except Exception as e:
                logger.warning(f"yfinance [{symbol}] 获取失败: {e}")
        return result

    # ── 历史 K 线 ────────────────────────────────────────────────

    def fetch_historical(self, symbol: str, days: int = 60) -> List[Bar]:
        """获取历史日 K 线，按 data_source 路由"""
        if self.data_source in ("auto", "ibkr"):
            try:
                return self._fetch_historical_ibkr(symbol, days)
            except Exception as e:
                if self.data_source == "ibkr":
                    raise
                logger.warning(f"IBKR [{symbol}] 历史数据失败: {e}, 回退 yfinance")

        if self.data_source in ("auto", "yfinance"):
            return self._fetch_historical_yfinance(symbol, days)

        return []

    def _fetch_historical_ibkr(self, symbol: str, days: int) -> List[Bar]:
        return self.client.get_historical_data(symbol, days=days, bar_size="1 day")

    def _fetch_historical_yfinance(self, symbol: str, days: int) -> List[Bar]:
        import yfinance as yf
        df = yf.download(symbol, period=f"{days}d", interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            logger.warning(f"yfinance [{symbol}] 下载历史数据为空")
            return []

        if hasattr(df.columns, 'names') and df.columns.names == ['Price', 'Ticker']:
            single = df.xs(symbol, level=1, axis=1)
        else:
            single = df

        bars = []
        for i in range(len(single)):
            bars.append(Bar(
                time=str(single.index[i].date()),
                open=float(single["Open"].iloc[i]),
                high=float(single["High"].iloc[i]),
                low=float(single["Low"].iloc[i]),
                close=float(single["Close"].iloc[i]),
                volume=int(single["Volume"].iloc[i]),
            ))
        return bars

    # ── 技术指标计算 ──────────────────────────────────────────────

    def compute_rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
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
                avg_gain = (avg_gain * (period - 1)) / period
                avg_loss = (avg_loss * (period - 1) - diff) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    def compute_sma(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def compute_indicators(self, bars: List[Bar]) -> Dict[str, Optional[float]]:
        closes = [b.close for b in bars]
        volumes = [float(b.volume) for b in bars]

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
            "ma_20": self.compute_sma(closes, 20),
            "ma_50": self.compute_sma(closes, 50),
            "rsi_14": self.compute_rsi(closes, 14),
            "volume_avg_20d": self.compute_sma([float(v) for v in volumes], 20),
            "change_1d_pct": change_1d,
            "change_5d_pct": change_5d,
            "change_20d_pct": change_20d,
        }

    def enrich(self, market_data_list: List[MarketData]) -> List[MarketData]:
        """批量补全 MarketData 的技术指标字段"""
        for md in market_data_list:
            try:
                bars = self.fetch_historical(md.symbol, days=60)
                if not bars or len(bars) < 2:
                    logger.warning(f"{md.symbol}: 历史数据不足 ({len(bars)} bars), 跳过指标计算")
                    continue

                indicators = self.compute_indicators(bars)
                for field, value in indicators.items():
                    if value is not None:
                        setattr(md, field, value)

                if md.rsi_14 is not None:
                    logger.info(
                        f"{md.symbol}: MA20={md.ma_20:.2f}, MA50={md.ma_50:.2f}, "
                        f"RSI14={md.rsi_14:.1f}"
                    )
                elif md.ma_20 is not None:
                    logger.info(f"{md.symbol}: MA20={md.ma_20:.2f}")
                else:
                    logger.info(f"{md.symbol}: indicators computed")

            except Exception as e:
                logger.warning(f"{md.symbol}: 历史数据获取失败 — {e}")

        return market_data_list
