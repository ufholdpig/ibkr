"""均线交叉条件"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("ma_cross")
class MACrossEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None or md.ma_20 is None or md.ma_50 is None:
            return False
        fast_period = node.period or 20
        if fast_period == 20:
            slow_period = 50
        else:
            slow_period = fast_period + 30
        fast = getattr(md, f"ma_{fast_period}", None)
        slow = getattr(md, f"ma_{slow_period}", None)
        if fast is None or slow is None:
            return False
        op = node.operator or ">"
        return self.compare(fast, op, slow)
