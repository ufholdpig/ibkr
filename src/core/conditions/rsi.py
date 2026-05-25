"""RSI条件"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("rsi")
class RSIEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None or md.rsi_14 is None:
            return False
        op = node.operator or "<"
        threshold = node.threshold or 30
        return self.compare(md.rsi_14, op, threshold)
