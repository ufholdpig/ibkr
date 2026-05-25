"""余额检查条件（占位 — Phase 4接入真实账户余额）"""

from .base import ConditionEvaluator, ConditionContext
from . import register


@register("balance_check")
class BalanceCheckEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        return True
