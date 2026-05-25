"""成交量突增条件"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("volume_spike")
class VolumeSpikeEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None or md.volume_avg_20d is None or md.volume_avg_20d == 0:
            return False
        mult = node.multiplier or 2.0
        return md.volume >= md.volume_avg_20d * mult
