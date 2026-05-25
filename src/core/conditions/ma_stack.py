"""均线多头排列条件: Price > MA50 > MA200"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("ma_stack")
class MAStackEvaluator(ConditionEvaluator):
    """Check bullish MA alignment: price > ma_50 > ma_200.

    YAML usage:
        - type: ma_stack
          operator: ">"   # bullish (default), "<" for bearish
    """

    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False
        if md.ma_50 is None or md.ma_200 is None:
            return False

        price = context.market_price
        op = getattr(node, "operator", None) or ">"

        if op == ">":
            return price > md.ma_50 > md.ma_200
        elif op == "<":
            return price < md.ma_50 < md.ma_200
        return False
