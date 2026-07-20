"""成交量突增条件

支持两种 lookback 模式:
- lookback: 20 (默认) → 今日成交量 / volume_avg_20d
- lookback: 90 → 近5日均量 / volume_avg_90d (strong_accumulation 专用)

YAML usage:
    - type: volume_spike
      multiplier: 1.3    # volume_ratio >= 1.3

    - type: volume_spike
      lookback: 90       # 使用90日均量模式
      multiplier: 1.3     # volume_ratio_90d >= 1.3
"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("volume_spike")
class VolumeSpikeEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False

        mult = node.multiplier or 2.0
        lookback = getattr(node, "lookback", None) or 20

        # 90日均量模式 (strong_accumulation: 近5日均量 vs 90日均量)
        if lookback == 90:
            ratio = getattr(md, "volume_ratio_90d", None)
            if ratio is not None:
                return ratio >= mult
            # fallback: compute from avg fields
            avg_5d = getattr(md, "volume_avg_5d", None)
            avg_90d = getattr(md, "volume_avg_90d", None)
            if avg_5d is not None and avg_90d and avg_90d > 0:
                return (avg_5d / avg_90d) >= mult
            return False

        # 20日均量模式 (默认)
        if getattr(md, "volume_ratio", None) is not None:
            return md.volume_ratio >= mult

        if getattr(md, "volume_avg_20d", None) is None or md.volume_avg_20d == 0:
            return False
        return getattr(md, "volume", 0) >= md.volume_avg_20d * mult