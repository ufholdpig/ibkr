"""成交量突增条件"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("volume_spike")
class VolumeSpikeEvaluator(ConditionEvaluator):
    """Check if volume exceeds average by a multiplier.

    Prefers pre-computed volume_ratio field; falls back to raw calculation.

    YAML usage:
        - type: volume_spike
          multiplier: 1.3    # default 2.0
    """

    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False

        mult = node.multiplier or 2.0

        if getattr(md, "volume_ratio", None) is not None:
            return md.volume_ratio >= mult

        if md.volume_avg_20d is None or md.volume_avg_20d == 0:
            return False
        return md.volume >= md.volume_avg_20d * mult
