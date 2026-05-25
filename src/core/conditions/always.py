"""always条件 — 始终满足"""

from .base import ConditionEvaluator, ConditionContext
from . import register


@register("always")
class AlwaysEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        return True
