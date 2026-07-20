"""价格vs移动平均线条件

支持两种模式：
1. 绝对值比较: price > MA (operator + threshold)
2. 百分比比较: (price - MA) / MA > pct_threshold

YAML usage:
    - type: price_vs_ma
      period: 200
      operator: ">"
      threshold: 0          # 绝对值：price > MA+threshold

    - type: price_vs_ma
      period: 200
      pct_threshold: 10      # 百分比：price 超过 MA 10%以上
"""

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

        price = context.market_price

        # 百分比模式: (price - MA) / MA > pct_threshold
        pct_threshold = getattr(node, "pct_threshold", None)
        if pct_threshold is not None:
            pct_above = (price - ma) / ma * 100.0
            return pct_above > pct_threshold

        # 绝对值模式
        op = node.operator or ">"
        threshold = node.threshold or 0.0
        return self.compare(price, op, ma + threshold)