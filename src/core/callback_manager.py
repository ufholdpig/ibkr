"""IBKR 回调上下文管理器

提供统一的回调注册/恢复机制，避免每个模块重复编写保存/恢复回调的代码。
"""

import threading
from typing import Callable, Dict, Optional, Any


class CallbackContext:
    """回调上下文管理器
    
    用法:
        with CallbackContext(api_client) as ctx:
            ctx.register('orderStatus', my_order_status_handler)
            ctx.register('error', my_error_handler)
            # 业务逻辑
        # 自动恢复原有回调
    """
    
    def __init__(self, api_client: Any):
        self.api_client = api_client
        self._originals: Dict[str, Any] = {}
    
    def register(self, callback_name: str, handler: Callable) -> None:
        """注册回调，覆盖原有回调
        
        Args:
            callback_name: 回调属性名 (如 'orderStatus', 'openOrder', 'error')
            handler: 回调处理函数
        """
        original = getattr(self.api_client, callback_name, None)
        self._originals[callback_name] = original
        setattr(self.api_client, callback_name, handler)
    
    def restore(self) -> None:
        """恢复所有原始回调"""
        for name, original in self._originals.items():
            if original is not None:
                setattr(self.api_client, name, original)
            else:
                try:
                    delattr(self.api_client, name)
                except AttributeError:
                    pass
        self._originals.clear()
    
    def __enter__(self) -> 'CallbackContext':
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.restore()
        return False


class EventCollector:
    """通用事件收集器
    
    用于收集 API 回调返回的数据，支持超时等待。
    """
    
    def __init__(self):
        self.done_event: threading.Event = threading.Event()
        self.data: list = []
        self.error: Optional[str] = None
        self._handlers: Dict[str, Callable] = {}
    
    def add_handler(self, name: str, handler: Callable) -> None:
        self._handlers[name] = handler
    
    def error_handler(self, req_id: int, error_code: int, error_string: str):
        if error_code in (2104, 2106, 2107, 2158, 2159):
            return
        self.error = f"{error_code}: {error_string}"
        self.done_event.set()
    
    def wait(self, timeout: float = 30) -> bool:
        return self.done_event.wait(timeout=timeout)
    
    def reset(self) -> None:
        self.done_event.clear()
        self.data.clear()
        self.error = None