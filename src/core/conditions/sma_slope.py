"""SMA斜率角度条件: 检查均线斜率是否在指定角度范围内

支持双向范围: slope 在 [threshold, multiplier] 之间 (当 operator=">")
支持负数: threshold 可以是负值，用于检测 [-3, 3]° 走平区间

YAML usage:
    - type: sma_slope
      period: 200
      threshold: -3          # 最小角度 (可以是负数)
      multiplier: 3          # 最大角度
      operator: ">"           # 范围模式: threshold <= slope <= multiplier

    - type: sma_slope
      period: 50
      threshold: 0
      multiplier: 90
      operator: ">"           # 正斜率模式: 0 <= slope <= 90
"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("sma_slope")
class SMASlopeEvaluator(ConditionEvaluator):
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