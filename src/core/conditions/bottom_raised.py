"""底部抬高条件: 90日低点 > 180日前(90-180日前)低点

用于检测标的是否在拒绝创新低，
蓄力形态的关键信号之一。

YAML usage:
    - type: bottom_raised
"""
from .base import ConditionEvaluator, ConditionContext
from . import register, _find_market_data


@register("bottom_raised")
class BottomRaisedEvaluator(ConditionEvaluator):
    def evaluate(self, node, context: ConditionContext) -> bool:
        if not context.market_data:
            return False
        md = _find_market_data(context.market_data, context.symbol)
        if md is None:
            return False

        # 支持直接从 market_data 获取预计算值
        # universe_selector 写入 bottom_raised_pass 字段
        bottom_raised = getattr(md, "bottom_raised_pass", None)
        if bottom_raised is not None:
            return bool(bottom_raised)

        # 备用: 直接用价格数据计算
        # 需要 md 有历史数据属性，这里放 false 由 universe_selector 补充计算
        return False