"""横盘突破条件: 股价横走近MA50后突破平台"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("consolidation_breakout")
class ConsolidationBreakoutEvaluator(ConditionEvaluator):
    """Detect consolidation near MA50 followed by upside breakout.

    Requires MarketData pre-computed fields:
    - is_consolidating: price stayed within +/-3% of MA50 for >= 5 days
    - breakout_detected: latest close broke above consolidation high

    YAML usage:
        - type: consolidation_breakout
    """

    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False

        if md.is_consolidating is None or md.breakout_detected is None:
            return False

        return md.is_consolidating and md.breakout_detected
