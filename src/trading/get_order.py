"""
订单查询模块（trading 层）

提供订单查询功能，封装 IBKR API 的回调等待逻辑：
1. get_opened_orders — 获取活跃订单
2. get_completed_orders — 获取已完成订单
3. get_executed_orders — 获取成交记录

被 PostMarketExecutor 等 trading 模块使用。
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from ibapi.execution import ExecutionFilter

from src.core.client import IBKRClient
from src.core.models import Execution
from src.core.exceptions import (
    NotConnectedError,
    QueryTimeoutError,
    raise_ibkr_error,
)
from src.core.logger import get_logger, create_audit_record
from src.core.utils import generate_req_id, wait_for_event, parse_ibkr_time

logger = get_logger(__name__)


# =============================================================================
# 数据模型
# =============================================================================


@dataclass
class OrderCommon:
    """订单通用字段（用于盘后报告）"""

    symbol: str
    action: str
    quantity: int
    filled_qty: int
    avg_price: float
    perm_id: int
    order_id: int
    status: str
    exec_time: str = ""


@dataclass
class OrderOnline(OrderCommon):
    """在线订单数据，来自 IBKR（用于盘后报告）"""

    order_type: str = "MKT"


# =============================================================================
# 数据收集器
# =============================================================================


class ExecutedOrdersCollector:
    """已执行订单收集器"""

    def __init__(self):
        self.executions: List[Execution] = []
        self.done_event = threading.Event()
        self.error: Optional[str] = None

    def reset(self):
        self.executions = []
        self.done_event.clear()
        self.error = None


# =============================================================================
# 内部转换函数
# =============================================================================


def _ibapi_execution_to_model(execution, contract) -> Execution:
    """将 ibapi.execution.Execution 转换为模型"""
    symbol = getattr(execution, 'symbol', '')
    if not symbol and contract:
        symbol = getattr(contract, 'symbol', '')

    exchange = getattr(execution, 'exchange', '')
    if not exchange and contract:
        exchange = getattr(contract, 'exchange', '')

    currency = getattr(execution, 'currency', '')
    if not currency and contract:
        currency = getattr(contract, 'currency', '')

    secType = getattr(execution, 'secType', '')
    if not secType and contract:
        secType = getattr(contract, 'secType', '')

    return Execution(
        exec_id=getattr(execution, 'execId', ''),
        order_id=getattr(execution, 'orderId', 0),
        perm_id=getattr(execution, 'permId', 0),
        client_id=getattr(execution, 'clientId', 0),
        account=getattr(execution, 'acctNumber', ''),
        symbol=symbol,
        secType=secType,
        exchange=exchange,
        currency=currency,
        side=getattr(execution, 'side', ''),
        shares=float(getattr(execution, 'shares', 0)),
        price=float(getattr(execution, 'price', 0)),
        cum_qty=float(getattr(execution, 'cumQty', 0)),
        avg_price=float(getattr(execution, 'avgPrice', 0)),
        exec_time=parse_ibkr_time(str(getattr(execution, 'time', ''))),
        liquidation=getattr(execution, 'liquidation', 0),
        order_ref=getattr(execution, 'orderRef', ''),
    )


# =============================================================================
# 查询接口
# =============================================================================


def get_opened_orders(client: IBKRClient, timeout: int = 30) -> List[OrderOnline]:
    """获取活跃订单

    使用 reqAllOpenOrders 获取所有活跃订单（跨 API session）。
    相对 reqOpenOrders 只返回当前 session 的订单，reqAllOpenOrders
    可看到 TWS 手动下单以及其他 API 客户端提交的订单。

    Args:
        client: 已连接的 IBKRClient 实例
        timeout: 等待回调的超时秒数

    Returns:
        List[OrderOnline]: 活跃订单列表
    """
    api_client = client.get_api_client()

    if not api_client.connected:
        logger.warning("⚠️ 未连接到 IB Gateway，无法获取活跃订单")
        return []

    done_event = threading.Event()
    orders = []

    # Audit: 记录请求
    create_audit_record(
        operation="get_opened_orders", success=False,
        timeout=timeout,
        request_type="reqAllOpenOrders",
        client_id=api_client.client_id if hasattr(api_client, 'client_id') else None,
        account=api_client._managed_account or "",
    )

    def on_open_order(order_id, contract, order, order_state):
        online_order = OrderOnline(
            symbol=getattr(contract, "symbol", ""),
            action=getattr(order, "action", ""),
            quantity=getattr(order, "totalQuantity", 0),
            filled_qty=getattr(order_state, "filled", 0),
            avg_price=getattr(order_state, "avgFillPrice", 0.0),
            perm_id=getattr(order, "permId", 0),
            order_id=order_id,
            status=getattr(order_state, "status", ""),
            exec_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            order_type=getattr(order, "orderType", "MKT"),
        )
        orders.append(online_order)
        # Audit: 记录完整 response raw data
        create_audit_record(
            operation="get_opened_orders_response", success=True,
            order_id=order_id,
            symbol=getattr(contract, "symbol", ""),
            secType=getattr(contract, "secType", ""),
            exchange=getattr(contract, "exchange", ""),
            currency=getattr(contract, "currency", ""),
            action=getattr(order, "action", ""),
            totalQuantity=getattr(order, "totalQuantity", 0),
            filled=getattr(order_state, "filled", 0),
            avgFillPrice=getattr(order_state, "avgFillPrice", 0.0),
            permId=getattr(order, "permId", 0),
            orderId=getattr(order, "orderId", 0),
            status=getattr(order_state, "status", ""),
            orderType=getattr(order, "orderType", "MKT"),
        )
        logger.debug(f"📋 收到活跃订单 [ID={order_id}]: {online_order.symbol}")

    def on_open_order_end():
        logger.debug("✅ 活跃订单查询结束")
        done_event.set()

    api_client._open_order_callbacks.append(on_open_order)
    api_client._open_order_end_callbacks.append(on_open_order_end)

    logger.info("🔍 开始请求活跃订单 (reqAllOpenOrders)...")
    api_client.reqAllOpenOrders()

    if not done_event.wait(timeout):
        logger.warning(f"⚠️ 活跃订单查询超时 ({timeout}s)")
        create_audit_record(
            operation="get_opened_orders", success=False,
            error=f"查询超时 ({timeout}s)",
        )
        if on_open_order in api_client._open_order_callbacks:
            api_client._open_order_callbacks.remove(on_open_order)
        if on_open_order_end in api_client._open_order_end_callbacks:
            api_client._open_order_end_callbacks.remove(on_open_order_end)
    else:
        if on_open_order in api_client._open_order_callbacks:
            api_client._open_order_callbacks.remove(on_open_order)
        if on_open_order_end in api_client._open_order_end_callbacks:
            api_client._open_order_end_callbacks.remove(on_open_order_end)

    # Audit: 记录响应
    create_audit_record(
        operation="get_opened_orders", success=True,
        order_count=len(orders),
        summary=[{"order_id": o.order_id, "symbol": o.symbol, "action": o.action, "filled": o.filled_qty, "status": o.status} for o in orders],
    )
    logger.info(f"📊 从 IBKR 获取到 {len(orders)} 个活跃订单")
    return orders


def get_completed_orders(client: IBKRClient, timeout: int = 30) -> List[OrderOnline]:
    """获取已完成订单

    注意：由于 IBKR API 限制，reqCompletedOrders 返回的订单字段（orderId/filled/avgFillPrice）全部为 0，
    目前不可用。建议使用 get_executed_orders() 获取详细成交记录。

    使用 reqCompletedOrders 获取今天已完成的订单。

    Args:
        client: 已连接的 IBKRClient 实例
        timeout: 等待回调的超时秒数

    Returns:
        List[OrderOnline]: 已完成订单列表（字段可能全为 0）
    """
    api_client = client.get_api_client()

    if not api_client.connected:
        logger.warning("⚠️ 未连接到 IB Gateway，无法获取已完成订单")
        return []

    done_event = threading.Event()
    orders = []

    # Audit: 记录请求
    create_audit_record(
        operation="get_completed_orders", success=False,
        timeout=timeout,
        request_type="reqCompletedOrders",
        client_id=api_client.client_id if hasattr(api_client, 'client_id') else None,
        account=api_client._managed_account or "",
    )

    def on_completed_order(contract, order, order_state):
        online_order = OrderOnline(
            symbol=getattr(contract, "symbol", ""),
            action=getattr(order, "action", ""),
            quantity=getattr(order, "totalQuantity", 0),
            filled_qty=getattr(order_state, "filled", 0),
            avg_price=getattr(order_state, "avgFillPrice", 0.0),
            perm_id=getattr(order, "permId", 0),
            order_id=getattr(order, "orderId", 0),
            status=getattr(order_state, "status", ""),
            exec_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            order_type=getattr(order, "orderType", "MKT"),
        )
        orders.append(online_order)
        # Audit: 记录完整 response raw data
        create_audit_record(
            operation="get_completed_orders_response", success=True,
            order_id=getattr(order, "orderId", 0),
            symbol=getattr(contract, "symbol", ""),
            secType=getattr(contract, "secType", ""),
            exchange=getattr(contract, "exchange", ""),
            currency=getattr(contract, "currency", ""),
            action=getattr(order, "action", ""),
            totalQuantity=getattr(order, "totalQuantity", 0),
            filled=getattr(order_state, "filled", 0),
            avgFillPrice=getattr(order_state, "avgFillPrice", 0.0),
            permId=getattr(order, "permId", 0),
            orderId=getattr(order, "orderId", 0),
            status=getattr(order_state, "status", ""),
            orderType=getattr(order, "orderType", "MKT"),
        )
        logger.debug(f"📋 收到已完成订单 [ID={online_order.order_id}]: {online_order.symbol}")

    def on_completed_orders_end():
        logger.debug("✅ 已完成订单查询结束")
        done_event.set()

    api_client._completed_order_callbacks.append(on_completed_order)
    api_client._completed_order_end_callbacks.append(on_completed_orders_end)

    logger.info("🔍 开始请求已完成订单 (reqCompletedOrders)...")
    api_client.reqCompletedOrders()

    if not done_event.wait(timeout):
        logger.warning(f"⚠️ 已完成订单查询超时 ({timeout}s)")
        create_audit_record(
            operation="get_completed_orders", success=False,
            error=f"查询超时 ({timeout}s)",
        )
        if on_completed_order in api_client._completed_order_callbacks:
            api_client._completed_order_callbacks.remove(on_completed_order)
        if on_completed_orders_end in api_client._completed_order_end_callbacks:
            api_client._completed_order_end_callbacks.remove(on_completed_orders_end)
    else:
        if on_completed_order in api_client._completed_order_callbacks:
            api_client._completed_order_callbacks.remove(on_completed_order)
        if on_completed_orders_end in api_client._completed_order_end_callbacks:
            api_client._completed_order_end_callbacks.remove(on_completed_orders_end)

    # Audit: 记录响应
    create_audit_record(
        operation="get_completed_orders", success=True,
        order_count=len(orders),
        summary=[{"order_id": o.order_id, "symbol": o.symbol, "action": o.action, "filled": o.filled_qty, "status": o.status} for o in orders],
    )
    logger.info(f"📊 从 IBKR 获取到 {len(orders)} 个已完成订单")
    return orders


def get_executed_orders(
    client: IBKRClient,
    date_str: Optional[str] = None,
    account_id: str = "",
    timeout: int = 30,
) -> List[Execution]:
    """获取已执行订单

    通过 IBKR API 的 reqExecutions 请求。
    默认返回当日的成交记录。

    Args:
        client: 已连接的 IBKRClient 实例
        date_str: 日期字符串 "YYYY-MM-DD" 或 "YYYYMMDD"，默认今日
        account_id: 可选的账户 ID 过滤
        timeout: 等待回调的超时秒数

    Returns:
        List[Execution]: 已执行订单列表
    """
    api_client = client.get_api_client()

    if not api_client.connected:
        raise_ibkr_error(NotConnectedError, "未连接到 IB Gateway")

    collector = ExecutedOrdersCollector()

    # Audit: 记录请求
    create_audit_record(
        operation="get_executed_orders", success=False,
        date_str=date_str, account_id=account_id, timeout=timeout,
        request_type="reqExecutions",
        client_id=api_client.client_id if hasattr(api_client, 'client_id') else None,
        account=api_client._managed_account or "",
    )

    def on_exec_details(req_id: int, contract, execution):
        try:
            o = _ibapi_execution_to_model(execution, contract)
            collector.executions.append(o)
            # Audit: 记录完整 response raw data
            create_audit_record(
                operation="get_executed_orders_response", success=True,
                exec_id=getattr(execution, 'execId', ''),
                order_id=getattr(execution, 'orderId', 0),
                perm_id=getattr(execution, 'permId', 0),
                symbol=getattr(contract, 'symbol', '') or getattr(execution, 'symbol', ''),
                side=getattr(execution, 'side', ''),
                shares=float(getattr(execution, 'shares', 0)),
                price=float(getattr(execution, 'price', 0)),
                exchange=getattr(contract, 'exchange', '') or getattr(execution, 'exchange', ''),
                currency=getattr(contract, 'currency', '') or getattr(execution, 'currency', ''),
                exec_time=parse_ibkr_time(str(getattr(execution, 'time', ''))),
            )
            info = f"📝 收到成交：[{o.perm_id}] {o.symbol} {o.side} {o.shares}|{o.price} |{o.order_id}|{o.exchange}"
            logger.debug(info)
        except Exception as e:
            logger.error(f"❌ 解析成交记录失败：{e}")
            create_audit_record(
                operation="get_executed_orders_response", success=False,
                error=f"解析成交记录失败：{e}",
            )

    def on_exec_details_end(req_id: int):
        logger.info(f"✅ 成交记录查询结束 (ReqID={req_id})")
        collector.done_event.set()

    def on_error(req_id: int, err_code: int, err_string: str):
        collector.error = f"{err_code}: {err_string}"
        logger.warning(f"⚠️ 成交记录查询错误：{err_code} - {err_string}")
        create_audit_record(
            operation="get_executed_orders", success=False,
            error=f"{err_code}: {err_string}",
        )

    orig_exec_details = api_client.execDetails
    orig_exec_details_end = getattr(api_client, 'execDetailsEnd', None)
    orig_error = api_client.error

    api_client.execDetails = on_exec_details
    api_client.execDetailsEnd = on_exec_details_end
    api_client.error = on_error

    filt = ExecutionFilter()
    if account_id:
        filt.acctCode = account_id

    req_id = generate_req_id("EXEC")
    logger.info(f"🔍 开始查询成交记录...")
    api_client.reqExecutions(req_id, filt)

    try:
        wait_for_event(collector.done_event, timeout, "成交记录")
    except QueryTimeoutError:
        if collector.executions:
            logger.warning(f"⚠️ 返回 {len(collector.executions)} 条已收到的成交记录")
            create_audit_record(
                operation="get_executed_orders", success=False,
                error=f"返回 {len(collector.executions)} 条已收到的成交记录",
            )
        else:
            logger.warning("⚠️ 成交记录查询超时 (可能该账户无成交记录)")
            create_audit_record(
                operation="get_executed_orders", success=False,
                error="查询超时 (可能该账户无成交记录)",
            )

    if collector.error and not collector.executions:
        logger.warning(f"⚠️ 成交记录查询出错：{collector.error}")
        create_audit_record(
            operation="get_executed_orders", success=False,
            error=f"查询出错：{collector.error}",
        )

    api_client.execDetails = orig_exec_details
    if orig_exec_details_end:
        api_client.execDetailsEnd = orig_exec_details_end
    api_client.error = orig_error

    # Audit: 记录响应
    create_audit_record(
        operation="get_executed_orders", success=True,
        execution_count=len(collector.executions),
        executions=[(e.perm_id, e.symbol, e.side, e.shares, e.price) for e in collector.executions],
    )
    logger.info(f"✅ 成交记录查询完成，共 {len(collector.executions)} 条")
    return collector.executions
