"""
市场数据提供者 — 统一数据源接口（IBKR / yfinance），计算技术指标

Phase 3: 数据计算层
目标: 填充 MarketData 所有技术指标字段 (rsi_14, ma_20, ma_50, ma_200, slopes, consolidation, etc.)
"""

import math
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

    def compute_sma_slope(self, closes: List[float], period: int, lookback: int = 10) -> Optional[float]:
        """Compute SMA slope as angle in degrees using linear regression over lookback bars.

        Returns angle in degrees where positive = upward slope.
        Uses normalized slope (relative to price level) to make angles comparable across stocks.
        """
        if len(closes) < period + lookback:
            return None

        sma_values = []
        for i in range(lookback):
            end_idx = len(closes) - lookback + i + 1
            sma_values.append(sum(closes[end_idx - period:end_idx]) / period)

        n = len(sma_values)
        x_mean = (n - 1) / 2.0
        y_mean = sum(sma_values) / n

        numerator = sum((i - x_mean) * (sma_values[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0

        slope = numerator / denominator
        price_level = sma_values[-1] if sma_values[-1] != 0 else 1.0
        normalized_slope = slope / price_level * period
        angle = math.atan(normalized_slope) * 180.0 / math.pi
        return angle

    def compute_consolidation(self, closes: List[float], ma_50_values: List[float],
                              threshold_pct: float = 3.0) -> dict:
        """Detect consolidation: price staying within +/-threshold_pct of MA50.

        Returns dict with is_consolidating, consolidation_days, breakout_detected.
        """
        if not ma_50_values or len(closes) < 2:
            return {"is_consolidating": None, "consolidation_days": None, "breakout_detected": None}

        days_in_range = 0
        consolidation_high = float('-inf')

        for i in range(len(ma_50_values) - 1, -1, -1):
            close_idx = len(closes) - len(ma_50_values) + i
            if close_idx < 0:
                break
            price = closes[close_idx]
            ma = ma_50_values[i]
            if ma == 0:
                break
            deviation = abs(price - ma) / ma * 100
            if deviation <= threshold_pct:
                days_in_range += 1
                consolidation_high = max(consolidation_high, price)
            else:
                break

        is_consolidating = days_in_range >= 5
        breakout_detected = False
        if is_consolidating and consolidation_high > float('-inf'):
            breakout_detected = closes[-1] > consolidation_high and len(closes) >= 2 and closes[-2] <= consolidation_high

        return {
            "is_consolidating": is_consolidating,
            "consolidation_days": days_in_range if is_consolidating else 0,
            "breakout_detected": breakout_detected,
        }

    def compute_indicators(self, bars: List[Bar]) -> dict:
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

        ma_50 = self.compute_sma(closes, 50)
        ma_200 = self.compute_sma(closes, 200)
        volume_avg_20d = self.compute_sma(volumes, 20)

        # Trend-following indicators
        ma_50_slope = self.compute_sma_slope(closes, 50, lookback=10)
        ma_200_slope = self.compute_sma_slope(closes, 200, lookback=10)
        # Previous MA200 slope (shifted back 5 bars) for direction-change detection
        ma_200_slope_prev = None
        if len(closes) >= 215:
            ma_200_slope_prev = self.compute_sma_slope(closes[:-5], 200, lookback=10)

        ma_spread_ratio = None
        if ma_50 is not None and ma_200 is not None and closes[-1] != 0:
            ma_spread_ratio = (ma_50 - ma_200) / closes[-1]

        # Consolidation detection (rolling MA50 for last 60 bars)
        consolidation_info = {"is_consolidating": None, "consolidation_days": None, "breakout_detected": None}
        if len(closes) >= 50:
            ma_50_series = []
            for i in range(50, len(closes)):
                ma_50_series.append(sum(closes[i - 50:i]) / 50)
            # Keep only the last 60 values (aligned with closes[-len(ma_50_series):])
            ma_50_series = ma_50_series[-60:]
            consolidation_info = self.compute_consolidation(closes, ma_50_series)

        # Volume ratio
        volume_ratio = None
        if volume_avg_20d and volume_avg_20d > 0 and volumes:
            volume_ratio = volumes[-1] / volume_avg_20d

        # Retrace to MA50 detection
        retrace_to_ma50 = None
        if ma_50 is not None and closes[-1] != 0:
            deviation_pct = abs(closes[-1] - ma_50) / closes[-1] * 100
            retrace_to_ma50 = deviation_pct <= 3.0

        # Days from recent high (within lookback window)
        days_from_high = None
        if len(closes) >= 5:
            lookback = min(60, len(closes))
            recent_closes = closes[-lookback:]
            high_idx = recent_closes.index(max(recent_closes))
            days_from_high = lookback - 1 - high_idx

        return {
            "ma_20": self.compute_sma(closes, 20),
            "ma_50": ma_50,
            "ma_200": ma_200,
            "rsi_14": self.compute_rsi(closes, 14),
            "volume_avg_20d": volume_avg_20d,
            "change_1d_pct": change_1d,
            "change_5d_pct": change_5d,
            "change_20d_pct": change_20d,
            "ma_50_slope": ma_50_slope,
            "ma_200_slope": ma_200_slope,
            "ma_200_slope_prev": ma_200_slope_prev,
            "ma_spread_ratio": ma_spread_ratio,
            "is_consolidating": consolidation_info["is_consolidating"],
            "consolidation_days": consolidation_info["consolidation_days"],
            "breakout_detected": consolidation_info["breakout_detected"],
            "volume_ratio": volume_ratio,
            "retrace_to_ma50": retrace_to_ma50,
            "days_from_high": days_from_high,
        }

    def enrich(self, market_data_list: List[MarketData]) -> List[MarketData]:
        """批量补全 MarketData 的技术指标字段

        Fetches 220 days of history to support SMA200 + slope lookback calculations.
        """
        for md in market_data_list:
            try:
                bars = self.fetch_historical(md.symbol, days=220)
                if not bars or len(bars) < 2:
                    logger.warning(f"{md.symbol}: 历史数据不足 ({len(bars)} bars), 跳过指标计算")
                    continue

                indicators = self.compute_indicators(bars)
                for attr, value in indicators.items():
                    if value is not None:
                        setattr(md, attr, value)

                if md.rsi_14 is not None and md.ma_50 is not None:
                    slope_str = f", slope50={md.ma_50_slope:.1f}°" if md.ma_50_slope is not None else ""
                    ma200_str = f", MA200={md.ma_200:.2f}" if md.ma_200 is not None else ""
                    logger.info(
                        f"{md.symbol}: MA50={md.ma_50:.2f}{ma200_str}, "
                        f"RSI14={md.rsi_14:.1f}{slope_str}"
                    )
                elif md.ma_20 is not None:
                    logger.info(f"{md.symbol}: MA20={md.ma_20:.2f}")
                else:
                    logger.info(f"{md.symbol}: indicators computed")

            except Exception as e:
                logger.warning(f"{md.symbol}: 历史数据获取失败 — {e}")

        return market_data_list
