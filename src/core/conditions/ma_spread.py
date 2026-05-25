"""MA间距条件: MA50与MA200间距是否小于阈值"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("ma_spread")
class MASpreadEvaluator(ConditionEvaluator):
    """Check if MA50-MA200 spread ratio is within threshold.

    ma_spread_ratio = (ma50 - ma200) / price
    Useful for detecting early trend starts where MAs are close together.

    YAML usage:
        - type: ma_spread
          operator: "<"
          threshold: 0.05    # spread ratio < 5% of price
    """

    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None or md.ma_spread_ratio is None:
            return False

        threshold = getattr(node, "threshold", None)
        if threshold is None:
            threshold = 0.05

        op = getattr(node, "operator", None) or "<"
        return self.compare(md.ma_spread_ratio, op, threshold)
