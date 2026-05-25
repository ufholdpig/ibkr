"""MA200 走平转上检测: 斜率从负/平变为正向"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("ma_slope_turn")
class MASlopeTurnEvaluator(ConditionEvaluator):
    """Detect MA slope direction change from negative/flat to positive.

    Checks that:
    1. Current slope > flat_threshold (default 1.0 degree)
    2. Previous slope <= flat_threshold (was flat or negative)

    YAML usage:
        - type: ma_slope_turn
          period: 200            # MA period (default: 200)
          flat_threshold: 1.0    # degrees below which slope is "flat" (default: 1.0)
    """

    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False

        period = int(getattr(node, "period", None) or 200)
        flat_threshold = float(getattr(node, "flat_threshold", None) or 1.0)

        if period == 200:
            current_slope = getattr(md, "ma_200_slope", None)
            prev_slope = getattr(md, "ma_200_slope_prev", None)
        elif period == 50:
            current_slope = getattr(md, "ma_50_slope", None)
            prev_slope = None
        else:
            return False

        if current_slope is None or prev_slope is None:
            return False

        return current_slope > flat_threshold and prev_slope <= flat_threshold
