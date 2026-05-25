"""回撤突破条件: 股价回撤至MA50附近后反弹突破"""

from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("retrace_breakout")
class RetraceBreakoutEvaluator(ConditionEvaluator):
    """Detect price retracing to MA50 (+/-3%) and then breaking out upward.

    Uses pre-computed retrace_to_ma50 field and checks for positive price action.

    YAML usage:
        - type: retrace_breakout
          threshold: 3.0     # proximity threshold % (default 3%)
    """

    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False

        if md.retrace_to_ma50 is None or md.ma_50 is None:
            return False

        if not md.retrace_to_ma50:
            return False

        # Price must be above MA50 (bouncing up from retrace zone)
        if context.market_price <= md.ma_50:
            return False

        # Confirm with positive daily momentum
        if md.change_1d_pct is not None and md.change_1d_pct > 0:
            return True

        return False
