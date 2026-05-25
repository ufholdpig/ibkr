"""SMA斜率角度条件: 检查均线斜率是否在指定角度范围内"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("sma_slope")
class SMASlopeEvaluator(ConditionEvaluator):
    """Check if SMA slope angle is within a range.

    YAML usage:
        - type: sma_slope
          period: 50          # which SMA (50 or 200)
          threshold: 5        # min angle (degrees)
          multiplier: 45      # max angle (degrees), reusing multiplier field
          operator: ">"       # slope must be positive (default)
    """

    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False

        period = getattr(node, "period", None) or 50
        if period == 200:
            slope = md.ma_200_slope
        else:
            slope = md.ma_50_slope

        if slope is None:
            return False

        min_angle = getattr(node, "threshold", None)
        max_angle = getattr(node, "multiplier", None)
        op = getattr(node, "operator", None) or ">"

        if min_angle is None:
            min_angle = 0.0
        if max_angle is None:
            max_angle = 90.0

        if op == ">":
            return min_angle <= slope <= max_angle
        elif op == "<":
            return -max_angle <= slope <= -min_angle
        return min_angle <= slope <= max_angle
