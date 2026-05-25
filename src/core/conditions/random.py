"""随机条件（用于测试/模拟）"""

import random

from .base import ConditionEvaluator, ConditionContext
from . import register


@register("random")
class RandomEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        prob = node.threshold if node.threshold is not None else 0.5
        return random.random() < prob
