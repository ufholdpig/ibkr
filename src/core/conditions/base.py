"""条件求值器基类 + 上下文"""

from dataclasses import dataclass
from typing import Optional, List


@dataclass
class ConditionContext:
    """条件求值上下文 — 统一参数传递"""
    symbol: str
    market_price: float
    avg_cost: float
    market_data: Optional[List] = None
    market_regime: Optional[str] = None


class ConditionEvaluator:
    """条件求值器基类"""
    def evaluate(self, node, context: ConditionContext) -> bool:
        raise NotImplementedError

    @staticmethod
    def compare(value: float, operator: str, threshold: float) -> bool:
        """通用比较逻辑 — 消除重复的if/elif"""
        ops = {
            "<": lambda a, b: a < b,
            ">": lambda a, b: a > b,
            "<=": lambda a, b: a <= b,
            ">=": lambda a, b: a >= b,
        }
        return ops.get(operator, lambda a, b: False)(value, threshold)
