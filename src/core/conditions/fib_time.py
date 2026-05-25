"""斐波那契时间序列条件: 调整天数是否匹配斐波那契数"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data

FIB_NUMBERS = [5, 8, 13, 21, 34, 55]


@register("fib_time")
class FibTimeEvaluator(ConditionEvaluator):
    """Check if consolidation duration matches a Fibonacci number (+/- tolerance).

    Uses pre-computed consolidation_days from MarketData.

    YAML usage:
        - type: fib_time
          threshold: 2       # tolerance in days (default: 2)
    """

    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None or md.consolidation_days is None:
            return False

        days = md.consolidation_days
        if days <= 0:
            return False

        tolerance = int(getattr(node, "threshold", None) or 2)

        return any(abs(days - fib) <= tolerance for fib in FIB_NUMBERS)
