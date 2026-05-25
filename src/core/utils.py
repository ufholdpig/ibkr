"""IBKR 核心工具函数

提供时间格式化、ReqID 生成、回调等待等通用功能。
"""

import threading
import time
from datetime import datetime
from typing import Optional

from src.core.exceptions import QueryTimeoutError
from src.core.logger import get_logger

logger = get_logger(__name__)


# 全局 ReqID 计数器（线程安全）
_req_id_counter = -1000
_req_id_lock = threading.Lock()


def generate_req_id(prefix: str = "") -> int:
    """生成唯一的请求 ID (ReqID)
    
    IBKR API 要求每个请求有唯一的 ReqID。
    负数用于查询类请求，避免与订单 ID (正数) 冲突。
    
    Args:
        prefix: 可选前缀，用于日志区分（如 "EXEC", "ORDER"）
    
    Returns:
        唯一的负整数 ReqID
    """
    global _req_id_counter
    with _req_id_lock:
        _req_id_counter -= 1
        req_id = _req_id_counter
        if prefix:
            logger.debug(f"🔢 生成 ReqID: {req_id} ({prefix})")
        else:
            logger.debug(f"🔢 生成 ReqID: {req_id}")
        return req_id


def format_datetime_ibkr(dt: Optional[datetime] = None) -> str:
    """格式化为 IBKR API 所需的时间格式
    
    IBKR 要求时间格式为: 'yyyymmdd-hh:mm:ss' (无时区)
    
    Args:
        dt: datetime 对象，默认为当前时间
    
    Returns:
        格式化后的字符串，如 "20260423-153000"
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y%m%d-%H:%M:%S")


def format_date_ibkr(dt: Optional[datetime] = None) -> str:
    """格式化为 IBKR API 所需的日期格式
    
    IBKR 要求日期格式为: 'yyyymmdd'
    
    Args:
        dt: datetime 对象，默认为当前日期
    
    Returns:
        格式化后的字符串，如 "20260423"
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y%m%d")


def wait_for_event(event: threading.Event, timeout: int, operation: str = "操作") -> None:
    """等待回调事件完成
    
    封装 threading.Event.wait()，超时后抛出 QueryTimeoutError。
    
    Args:
        event: 回调完成事件
        timeout: 超时秒数
        operation: 操作名称，用于错误消息
    
    Raises:
        QueryTimeoutError: 如果超时未收到回调
    """
    logger.info(f"⏳ 等待 {operation} 回调 (超时 {timeout}s)...")
    if not event.wait(timeout=timeout):
        logger.warning(f"⚠️ {operation} 查询超时 (等待 {timeout}s)")
        raise QueryTimeoutError(
            f"{operation} 查询超时，未收到回调",
            {"timeout": timeout, "operation": operation}
        )
    logger.info(f"✅ {operation} 回调已收到")


def sleep_safe(duration: float) -> None:
    """安全休眠
    
    封装 time.sleep()，处理中断异常。
    
    Args:
        duration: 休眠秒数
    """
    try:
        time.sleep(duration)
    except InterruptedError:
        logger.debug("⏸️ 休眠被中断")


def parse_ibkr_time(time_str: str) -> str:
    """解析 IBKR 时间字符串为可读格式
    
    IBKR 返回的时间通常是 Unix 时间戳 (秒) 或 "yyyymmdd-hh:mm:ss" 格式。
    此函数尝试将其转换为 "YYYY-MM-DD HH:MM:SS" 格式。
    
    Args:
        time_str: IBKR 时间字符串
        
    Returns:
        格式化后的时间字符串
    """
    if not time_str:
        return ""
    
    try:
        # 尝试解析为 Unix 时间戳 (整数或浮点数字符串)
        timestamp = float(time_str)
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    
    try:
        # 尝试解析为 "yyyymmdd-hh:mm:ss"
        dt = datetime.strptime(time_str, "%Y%m%d-%H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    
# 如果解析失败，返回原始字符串
    return time_str


def get_today_start_end() -> tuple:
    """获取今日开始和结束时间 (IBKR 格式)
    
    用于 ExecutionFilter 的时间范围过滤。
    IBKR 要求时间格式为: 'yyyymmdd-hh:mm:ss'
    
    Returns:
        (start_time, end_time) 元组，格式为 ("20260424-00:00:00", "20260424-23:59:59")
    """
    today = datetime.now().strftime("%Y%m%d")
    start_time = f"{today}-00:00:00"
    end_time = f"{today}-23:59:59"
    return start_time, end_time
