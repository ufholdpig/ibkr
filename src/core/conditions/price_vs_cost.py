"""价格vs成本条件"""

from .base import ConditionEvaluator, ConditionContext
from . import register


@register("price_vs_cost")
class PriceVsCostEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        op = node.operator or "<"
        ratio = node.threshold_ratio or 0.9
        threshold = context.avg_cost * ratio
        return self.compare(context.market_price, op, threshold)
