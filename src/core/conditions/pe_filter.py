"""市盈率过滤条件"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("pe_filter")
class PEFilterEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None or md.pe_ratio is None:
            return False
        op = node.operator or "<"
        threshold = node.threshold or 20
        return self.compare(md.pe_ratio, op, threshold)
