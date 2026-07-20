"""52周位置条件: 价格处于过去52周区间的30%-80%

用于检测标的是否处于"有空间但非高位"的状态。

YAML usage:
    - type: position_52w
      operator: ">="
      threshold: 0.30   # 下限30%
    - type: position_52w
      operator: "<="
      threshold: 0.80   # 上限80%
"""
from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("position_52w")
class Position52wEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False

        # 支持直接从 market_data 获取预计算值
        # universe_selector 写入 position_52w_pass 字段
        position_52w_pass = getattr(md, "position_52w_pass", None)
        if position_52w_pass is not None:
            return bool(position_52w_pass)

        # 备用: 用 market_data 的 price_52w_high/low 计算
        high = getattr(md, "price_52w_high", None)
        low = getattr(md, "price_52w_low", None)
        if high is not None and low is not None and high > low:
            price = context.market_price
            position = (price - low) / (high - low)
            threshold = node.threshold or 0.5
            op = node.operator or ">="
            return self.compare(position, op, threshold)

        return False