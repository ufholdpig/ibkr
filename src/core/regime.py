"""MarketRegimeDetector — 市场状态检测

判断逻辑:
- BULL: SPX > MA200 且 VIX < 20
- BEAR: SPX < MA200 且 VIX > 25
- SIDEWAYS: 其他(默认，最保守)

Phase 3 D25 交付物
"""

import logging
from datetime import datetime
from typing import List, Optional

from src.core.models import MarketRegime, MarketSnapshot
from src.core.strategy import MarketData

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """市场状态检测器

    支持两种数据源:
    1. 显式传入 spx_price, spx_ma200, vix
    2. 从 market_data 列表中自动查找 SPX/SPY 数据(MA200用MA50代理)
    """

    SPX_CANDIDATES = ["SPX", "SPY", "IVV", "VOO"]

    def __init__(self):
        self._last_snapshot: Optional[MarketSnapshot] = None

    @property
    def last_snapshot(self) -> Optional[MarketSnapshot]:
        return self._last_snapshot

    def detect(
        self,
        spx_price: Optional[float] = None,
        spx_ma200: Optional[float] = None,
        vix: Optional[float] = None,
        market_data: Optional[List[MarketData]] = None,
    ) -> MarketSnapshot:
        if market_data:
            spx_md = self._find_spx(market_data)
            if spx_md and spx_price is None:
                spx_price = spx_md.price
            if spx_md and spx_ma200 is None and spx_md.ma_50 is not None:
                spx_ma200 = spx_md.ma_50
            vix_md = self._find_vix(market_data)
            if vix_md and vix is None:
                vix = vix_md.price

        spx_price = spx_price or 0.0
        spx_ma200 = spx_ma200 or 0.0
        vix = vix or 0.0

        spx_vs_ma200_pct = 0.0
        if spx_ma200 > 0:
            spx_vs_ma200_pct = (spx_price - spx_ma200) / spx_ma200 * 100.0

        if spx_ma200 > 0 and spx_price > spx_ma200 and vix < 20:
            regime = MarketRegime.BULL
        elif spx_ma200 > 0 and spx_price < spx_ma200 and vix > 25:
            regime = MarketRegime.BEAR
        else:
            regime = MarketRegime.SIDEWAYS

        snapshot = MarketSnapshot(
            regime=regime,
            spx_price=spx_price,
            spx_vs_ma200_pct=round(spx_vs_ma200_pct, 2),
            vix=vix,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._last_snapshot = snapshot
        logger.info(
            "市场状态检测: %s (SPX=%.0f, MA200≈%.0f, VIX=%.1f, ΔMA=%.1f%%)",
            regime.value, spx_price, spx_ma200, vix, spx_vs_ma200_pct,
        )
        return snapshot

    def _find_spx(self, market_data: List[MarketData]) -> Optional[MarketData]:
        for md in market_data:
            if md.symbol.upper() in self.SPX_CANDIDATES:
                return md
        return None

    def _find_vix(self, market_data: List[MarketData]) -> Optional[MarketData]:
        for md in market_data:
            sym = md.symbol.upper().replace("^", "")
            if sym in ("VIX",):
                return md
        return None
