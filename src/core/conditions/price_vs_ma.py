"""价格vs移动平均线条件"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("price_vs_ma")
class PriceVsMAEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False
        period = node.period or 20
        ma = getattr(md, f"ma_{period}", None)
        if ma is None or ma == 0:
            return False
        op = node.operator or "<"
        return self.compare(context.market_price, op, ma)
