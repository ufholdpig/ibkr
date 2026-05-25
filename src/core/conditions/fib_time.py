"""斐波那契时间序列条件: 调整天数是否匹配斐波那契数

支持两种模式:
- mode: "consolidation" (默认) — 横盘天数匹配斐波那契
- mode: "pullback" — 从高点回调天数匹配斐波那契
"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data

FIB_NUMBERS = [5, 8, 13, 21, 34, 55]


@register("fib_time")
class FibTimeEvaluator(ConditionEvaluator):
    """Check if time duration matches a Fibonacci number (+/- tolerance).

    YAML usage:
        - type: fib_time
          mode: "consolidation"  # or "pullback" (default: consolidation)
          threshold: 2           # tolerance in days (default: 2)
    """

    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False

        mode = getattr(node, "mode", None) or "consolidation"
        tolerance = int(getattr(node, "threshold", None) or 2)

        if mode == "pullback":
            days = getattr(md, "days_from_high", None)
        else:
            days = md.consolidation_days

        if days is None or days <= 0:
            return False

        return any(abs(days - fib) <= tolerance for fib in FIB_NUMBERS)
