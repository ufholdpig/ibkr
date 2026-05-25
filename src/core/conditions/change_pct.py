"""涨跌幅条件"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("change_pct")
class ChangePctEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False
        days = node.period or 1
        change = getattr(md, f"change_{days}d_pct", None)
        if change is None:
            return False
        op = node.operator or "<"
        threshold = node.threshold or 0.0
        return self.compare(change, op, threshold)
