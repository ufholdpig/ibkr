"""条件引擎注册表 — 加文件即生效"""

import logging
from typing import Dict, Type, List

from .base import ConditionEvaluator, ConditionContext

_REGISTRY: Dict[str, Type[ConditionEvaluator]] = {}


def register(condition_type: str):
    """装饰器：注册条件求值器"""
    def decorator(cls: Type[ConditionEvaluator]):
        _REGISTRY[condition_type] = cls
        return cls
    return decorator


def evaluate(condition_type: str, node, context: ConditionContext) -> bool:
    """统一求值入口"""
    evaluator_cls = _REGISTRY.get(condition_type)
    if evaluator_cls is None:
        logging.getLogger("ConditionEngine").warning(
            f"未知条件类型: {condition_type}, 可用: {list(_REGISTRY.keys())}"
        )
        return False
    return evaluator_cls().evaluate(node, context)


def get_registry() -> Dict[str, Type[ConditionEvaluator]]:
    """返回当前注册表(调试/审计用)"""
    return dict(_REGISTRY)


def _find_market_data(market_data: List, symbol: str):
    """从 market_data 列表查找指定标的的数据"""
    for md in market_data:
        if hasattr(md, "symbol") and md.symbol == symbol:
            return md
        if isinstance(md, dict) and md.get("symbol") == symbol:
            return md
    return None


import importlib
import pkgutil

for _, name, _ in pkgutil.iter_modules(__path__):
    if name not in ("base",):
        importlib.import_module(f".{name}", __package__)
