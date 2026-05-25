"""never条件 — 始终不满足"""

from .base import ConditionEvaluator, ConditionContext
from . import register


@register("never")
class NeverEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        return False
