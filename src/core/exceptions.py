"""IBKR 统一异常类

所有 IBKR 相关操作抛出的异常统一从此模块导入。

设计原则:
- 所有异常继承自 IBKRClientError
- 异常消息包含足够的上下文信息
- 异常可被上层捕获并转换为友好的错误提示
"""


class IBKRClientError(Exception):
    """IBKR 客户端错误基类
    
    所有 IBKR 相关异常都应继承此类。
    """
    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
    
    def __str__(self):
        if self.details:
            details_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} ({details_str})"
        return self.message


class NotConnectedError(IBKRClientError):
    """未连接到 IB Gateway 错误"""
    pass


class ConnectionTimeoutError(IBKRClientError):
    """连接超时错误"""
    pass


class QueryTimeoutError(IBKRClientError):
    """查询超时错误
    
    当 API 请求在指定时间内未收到回调时抛出。
    """
    pass


class OrderSubmitError(IBKRClientError):
    """订单提交错误"""
    pass


class OrderCancelError(IBKRClientError):
    """订单取消错误"""
    pass


class ContractNotFoundError(IBKRClientError):
    """合约未找到错误"""
    pass


class InvalidOrderError(IBKRClientError):
    """无效订单错误
    
    订单参数不符合 IBKR 要求（如数量为 0、价格为负等）。
    """
    pass


class AccountError(IBKRClientError):
 """账户相关错误
 
 如账户未授权、账户状态异常等。
 """
 pass


class AccountInfoError(AccountError):
 """账户信息解析错误
 
 当无法解析账户摘要、余额或持仓数据时抛出。
 """
 pass


class MarketDataError(IBKRClientError):
    """市场数据错误
    
    如订阅失败、数据延迟等。
    """
    pass


class RateLimitError(IBKRClientError):
    """请求频率限制错误
    
    当超过 IBKR API 的请求频率限制时抛出。
    """
    pass


class AuthenticationError(IBKRClientError):
    """认证错误
    
    如登录失败、会话过期等。
    """
    pass


class ConfigurationError(IBKRClientError):
    """配置错误
    
    如配置文件缺失、参数无效等。
    """
    pass


def raise_ibkr_error(error_type: type, message: str, **kwargs):
    """辅助函数：根据错误类型抛出对应的异常
    
    Args:
        error_type: 异常类型（如 NotConnectedError）
        message: 错误消息
        **kwargs: 额外的上下文信息
    
    Example:
        raise_ibkr_error(NotConnectedError, "连接失败", host="127.0.0.1", port=4002)
    """
    raise error_type(message, kwargs)
