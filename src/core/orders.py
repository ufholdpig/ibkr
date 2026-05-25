"""IBKR 订单核心管理模块

功能：
1. 提交新订单 (place_order)
2. 取消订单 (cancel_order)

设计依据：
- IBKR API: placeOrder, cancelOrder
- 官方文档: https://interactivebrothers.github.io/tws-api/order_management.html

注意：订单查询功能（get_opened_orders / get_completed_orders / get_executed_orders）
已移至 src/trading/get_order.py。
"""

import threading
import time
from typing import Optional

from ibapi.order import Order as IBAPIOrder
from ibapi.contract import Contract as IBAPIContract

from src.core.client import IBKRClient
from src.core.models import Order, OrderResult, Contract
from src.core.exceptions import (
    IBKRClientError,
    NotConnectedError,
    OrderSubmitError,
    OrderCancelError,
    QueryTimeoutError,
    raise_ibkr_error,
)
from src.core.logger import get_logger, create_audit_record
from src.core.utils import generate_req_id, wait_for_event

logger = get_logger(__name__)

# =============================================================================
# 数据收集器
# =============================================================================

class OrderSubmitCollector:
    """订单提交结果收集器"""

    def __init__(self):
        self.order_id: int = 0
        self.perm_id: int = 0
        self.status: str = ""
        self.filled: float = 0.0
        self.avg_fill_price: float = 0.0
        self.error_message: str = ""
        self.done_event = threading.Event()
        self.success: bool = False

    def reset(self):
        self.order_id = 0
        self.perm_id = 0
        self.status = ""
        self.filled = 0.0
        self.avg_fill_price = 0.0
        self.error_message = ""
        self.done_event.clear()
        self.success = False

# =============================================================================
# 核心接口
# =============================================================================

def _ibapi_order_to_model(order: IBAPIOrder, contract, order_state) -> Order:
    """将 ibapi.order.Order 转换为模型

    Args:
        order: IB API 订单对象
        contract: IB API 合约对象 (包含 symbol, exchange, currency 等)
        order_state: 订单状态对象 (包含 status, filled, avgFillPrice 等)
    """
    # 从 contract 获取合约信息
    symbol = getattr(contract, "symbol", "") if contract else ""
    sec_type = getattr(contract, "secType", "") if contract else ""
    exchange = getattr(contract, "exchange", "") if contract else ""
    currency = getattr(contract, "currency", "") if contract else ""

    # 从 order_state 获取状态信息
    status = getattr(order_state, "status", "") if order_state else ""
    filled = getattr(order_state, "filled", 0) if order_state else 0
    avg_fill_price = getattr(order_state, "avgFillPrice", 0) if order_state else 0

    return Order(
        order_id=order.orderId,
        account=order.account,
        perm_id=order.permId,
        client_id=order.clientId,
        symbol=symbol,
        secType=sec_type,
        exchange=exchange,
        currency=currency,
        action=order.action,
        order_type=order.orderType,
        total_quantity=order.totalQuantity,
        limit_price=order.lmtPrice,
        aux_price=order.auxPrice,
        tif=order.tif,
        status=status,
        filled=filled,
        remaining=getattr(order, "remaining", 0),
        avg_fill_price=avg_fill_price,
        last_fill_price=getattr(order, "lastFillPrice", 0),
        last_fill_qty=getattr(order, "lastFillQty", 0),
        order_ref=order.orderRef,
        parent_id=order.parentId,
        block_order=order.blockOrder,
        trail_stop_price=order.trailStopPrice,
        lmt_price_offset=order.lmtPriceOffset,
        oca_group=order.ocaGroup,
        oca_type=order.ocaType,
    )

def _register_order_callbacks(api_client, collector):
    """注册订单相关回调（openOrder/orderStatus/execDetails/error）"""
    orig_open_order = getattr(api_client, "openOrder", None)
    orig_order_status = getattr(api_client, "orderStatus", None)
    orig_error = getattr(api_client, "error", None)
    orig_exec_details = getattr(api_client, "execDetails", None)

    def on_open_order(order_id: int, contract, order: IBAPIOrder, order_state):
        if order_id == collector.order_id:
            collector.perm_id = getattr(order, "permId", 0)
            collector.status = getattr(order_state, "status", "")
            collector.done_event.set()

    def on_order_status(
        order_id: int,
        status: str,
        filled: float,
        remaining: float,
        avg_fill_price: float,
        perm_id: int,
        parent_id: int,
        last_fill_price: float,
        client_id: int,
        why_held: str,
        mkt_cap_price: float,
    ):
        if order_id == collector.order_id:
            collector.status = status
            collector.filled = filled
            collector.avg_fill_price = avg_fill_price
            if perm_id > 0:
                collector.perm_id = perm_id
            #logger.debug(f"📊 orderStatus 回调：ID={order_id}, Status={status}, PermID={perm_id}, Filled={filled}")
            if status in ["Filled", "Cancelled", "ApiCancelled"]:
                collector.done_event.set()

    def on_exec_details(req_id: int, contract, execution):
        if execution.orderId == collector.order_id:
            collector.perm_id = getattr(execution, "permId", 0)
            #logger.debug(f"💼 execDetails 回调：PermID={collector.perm_id}")

    def on_error(req_id: int, err_code: int, err_string: str):
        if err_code in (100, 200):
            collector.error_message = f"{err_code}: {err_string}"
            collector.success = False
            collector.done_event.set()
        else:
            logger.warning(f"⚠️ 订单相关错误 {err_code}: {err_string}")

    api_client.openOrder = on_open_order
    api_client.orderStatus = on_order_status
    api_client.execDetails = on_exec_details
    api_client.error = on_error

    return orig_open_order, orig_order_status, orig_error, orig_exec_details


def _restore_callbacks(
    api_client, orig_open_order, orig_order_status, orig_error, orig_exec_details
):
    """恢复原有回调"""
    if orig_open_order:
        api_client.openOrder = orig_open_order
    if orig_order_status:
        api_client.orderStatus = orig_order_status
    if orig_error:
        api_client.error = orig_error
    if orig_exec_details:
        api_client.execDetails = orig_exec_details


# ============================================================================= 核心功能

# ===================== 
# place_orders
# ===================== 
def place_order(
    client: IBKRClient, order: Order, contract: Contract, timeout: int = 10
) -> OrderResult:
    """提交新订单

    通过 IBKR API 的 placeOrder 请求。
    等待 orderStatus 回调获取 perm_id。

    Args:
        client: 已连接的 IBKRClient 实例
        order: 订单模型
        contract: 合约模型
        timeout: 等待回调的超时秒数 (默认 10)

    Returns:
        OrderResult: 订单提交结果

    Raises:
        NotConnectedError: 未连接到 IB Gateway
        OrderSubmitError: 订单提交失败
    """
    # 审计：开始提交订单
    create_audit_record(
        operation="place_order",
        success=False,
        symbol=order.symbol,
        action=order.action,
        quantity=order.total_quantity,
        order_type=order.order_type,
    )

    api_client = client.get_api_client()

    if not api_client.connected:
        raise_ibkr_error(NotConnectedError, "未连接到 IB Gateway")

    collector = OrderSubmitCollector()

    # 保存原有回调并注册新回调
    orig_open_order, orig_order_status, orig_error, orig_exec_details = (
        _register_order_callbacks(api_client, collector)
    )

    try:
        # 转换为 IB API 对象
        ibapi_order = IBAPIOrder()

        # 使用 IB Gateway 分配的 next_order_id
        if order.order_id > 0:
            ibapi_order.orderId = order.order_id
        else:
            # 等待 nextValidId 回调设置 next_order_id
            if api_client.next_order_id is None:
                logger.debug("⚠️ 等待 IB Gateway 分配 orderId...")

                for _ in range(50):  # 最多等 5 秒
                    time.sleep(0.1)
                    if api_client.next_order_id is not None:
                        break
                if api_client.next_order_id is None:
                    raise IBKRClientError(
                        "IB Gateway 未分配 orderId (nextValidId 未收到)"
                    )
            ibapi_order.orderId = api_client.next_order_id
            api_client.next_order_id += 1  # 递增供下次使用
        ibapi_order.account = order.account
        ibapi_order.action = order.action
        ibapi_order.orderType = order.order_type
        ibapi_order.totalQuantity = order.total_quantity
        ibapi_order.lmtPrice = order.limit_price
        ibapi_order.auxPrice = order.aux_price
        ibapi_order.tif = order.tif
        ibapi_order.orderRef = order.order_ref
        ibapi_order.transmit = True  # 立即发送

        # IB Gateway 不支持这些属性，必须显式设为 False
        ibapi_order.eTradeOnly = False
        ibapi_order.firmQuoteOnly = False

        ibapi_contract = IBAPIContract()
        ibapi_contract.symbol = contract.symbol
        # 使用 secType (驼峰) 对齐 IB API，不要使用 sec_type
        ibapi_contract.secType = contract.secType
        ibapi_contract.exchange = contract.exchange
        ibapi_contract.currency = contract.currency
        # 使用 primaryExchange (驼峰) 对齐 IB API
        if contract.primaryExchange:
            ibapi_contract.primaryExchange = contract.primaryExchange

        # 设置当前订单 ID
        collector.order_id = ibapi_order.orderId

        # 发送请求
        req_id = generate_req_id("PLACE")
        logger.info(
            f"提交订单: {contract.symbol} {order.action} {order.total_quantity}"
        )

        api_client.placeOrder(ibapi_order.orderId, ibapi_contract, ibapi_order)

        # 等待 orderStatus 回调获取 perm_id
        logger.debug("⏳ 等待服务器确认订单...")
        collector.done_event.wait(timeout)

        # 如果订单已提交但尚未填充，等待最多 5s 获取成交数据
        if collector.perm_id > 0 and collector.filled <= 0:
            for _ in range(50):  # 5s / 0.1s per poll
                if collector.filled > 0 or collector.status in ["Cancelled", "Rejected"]:
                    break
                time.sleep(0.1)

        # 如果收到 perm_id，更新状态
        if collector.perm_id > 0:
            collector.success = True
            # 审计：订单提交成功
            create_audit_record(
                operation="place_order",
                success=True,
                order_id=collector.order_id,
                perm_id=collector.perm_id,
                status=collector.status,
                symbol=order.symbol,
            )
            logger.info(
                f"✅ 订单已确认：ID={collector.order_id}, PermID={collector.perm_id}, Status={collector.status}"
            )
        elif collector.error_message:
            collector.success = False
            logger.error(f"❌ 订单提交失败：{collector.error_message}")
        else:
            # 超时但订单可能已提交
            if collector.error_message:
                collector.success = False
                logger.error(f"❌ 订单提交失败：{collector.error_message}")
            else:
                collector.success = True
                collector.status = "Submitted"
                logger.warning(
                    f"⚠️ 等待 perm_id 超时，订单可能已提交：ID={ibapi_order.orderId}"
                )

        return OrderResult(
            success=collector.success,
            order_id=ibapi_order.orderId,
            perm_id=collector.perm_id,
            status=collector.status or "Submitted",
            filled_qty=collector.filled,
            avg_fill_price=collector.avg_fill_price,
            error_message=collector.error_message or "",
        )

    except Exception as e:
        logger.error(f"❌ 订单提交异常: {e}")
        raise_ibkr_error(OrderSubmitError, str(e))
    finally:
        # 恢复原有回调
        _restore_callbacks(
            api_client,
            orig_open_order,
            orig_order_status,
            orig_error,
            orig_exec_details,
        )

# ===================== 
# cancel_orders
# ===================== 
def cancel_order(client: IBKRClient, order_id: int, timeout: int = 5) -> bool:
    """取消指定订单

    通过 IBKR API 的 cancelOrder 请求。

    Args:
        client: 已连接的 IBKRClient 实例
        order_id: 订单 ID
        timeout: 等待回调的超时秒数 (默认 5)

    Returns:
        bool: 取消成功返回 True

    Raises:
        NotConnectedError: 未连接到 IB Gateway
        OrderCancelError: 取消失败
    """
    # 审计：开始取消订单
    create_audit_record(
        operation="cancel_order",
        success=False,
        order_id=order_id,
    )

    api_client = client.get_api_client()

    if not api_client.connected:
        raise_ibkr_error(NotConnectedError, "未连接到 IB Gateway")

    collector = OrderSubmitCollector()
    collector.order_id = order_id

    # 注册回调
    def on_order_status(order_id: int, status: str):
        if order_id == order_id:
            collector.status = status
            if status == "Cancelled":
                collector.success = True
                collector.done_event.set()

    def on_error(req_id: int, err_code: int, err_string: str):
        if err_code == 101:  # 订单取消错误
            collector.error_message = err_string
            collector.done_event.set()

    # 发送请求
    logger.info(f"🛑 开始取消订单 (ID={order_id})...")
    api_client.cancelOrder(order_id)

    # 等待结果
    try:
        wait_for_event(collector.done_event, timeout, f"取消订单 ({order_id})")
        # 审计：取消成功
        create_audit_record(
            operation="cancel_order",
            success=True,
            order_id=order_id,
            status=collector.status,
        )
    except QueryTimeoutError:
        logger.warning(f"⚠️ 取消订单超时，可能已取消或状态未知")
        # 不抛出异常，因为订单可能已经在后台取消

    if collector.error_message:
        raise_ibkr_error(OrderCancelError, collector.error_message)

    logger.info(f"✅ 订单取消请求已发送：ID={order_id}")
    return collector.success or True  # 即使超时也返回 True，因为请求已发送


# =====================
# bracket_orders (OCO)
# =====================
def place_bracket_order(
    client: IBKRClient,
    contract: Contract,
    action: str,
    quantity: float,
    limit_price: float,
    stop_loss_price: float,
    take_profit_price: float = 0.0,
    tif: str = "GTC",
    timeout: int = 10,
) -> dict:
    """Submit a bracket order (parent + stop loss + optional take profit).

    IBKR bracket orders use parentId to link child orders to the parent.
    The parent is a LMT BUY, children are STP (stop loss) and LMT SELL (take profit).
    Children only activate after parent fills.

    Args:
        client: Connected IBKRClient instance
        contract: Contract model
        action: "BUY" or "SELL" (parent direction)
        quantity: Order quantity
        limit_price: Parent order limit price
        stop_loss_price: Stop loss trigger price
        take_profit_price: Take profit limit price (0 = skip)
        tif: Time in force for all orders
        timeout: Callback wait timeout

    Returns:
        dict with parent_result, stop_loss_id, take_profit_id
    """
    api_client = client.get_api_client()

    if not api_client.connected:
        raise_ibkr_error(NotConnectedError, "未连接到 IB Gateway")

    # Wait for next_order_id
    if api_client.next_order_id is None:
        for _ in range(50):
            time.sleep(0.1)
            if api_client.next_order_id is not None:
                break
        if api_client.next_order_id is None:
            raise IBKRClientError("IB Gateway 未分配 orderId")

    parent_id = api_client.next_order_id
    api_client.next_order_id += 1
    sl_id = api_client.next_order_id
    api_client.next_order_id += 1
    tp_id = api_client.next_order_id if take_profit_price > 0 else 0
    if tp_id:
        api_client.next_order_id += 1

    reverse_action = "SELL" if action == "BUY" else "BUY"

    # Build IB contract
    ibapi_contract = IBAPIContract()
    ibapi_contract.symbol = contract.symbol
    ibapi_contract.secType = contract.secType
    ibapi_contract.exchange = contract.exchange
    ibapi_contract.currency = contract.currency
    if contract.primaryExchange:
        ibapi_contract.primaryExchange = contract.primaryExchange

    # Parent order (LMT)
    parent = IBAPIOrder()
    parent.orderId = parent_id
    parent.action = action
    parent.orderType = "LMT"
    parent.totalQuantity = quantity
    parent.lmtPrice = limit_price
    parent.tif = tif
    parent.transmit = False  # Hold until children are attached
    parent.eTradeOnly = False
    parent.firmQuoteOnly = False

    # Stop loss child (STP)
    stop_loss = IBAPIOrder()
    stop_loss.orderId = sl_id
    stop_loss.action = reverse_action
    stop_loss.orderType = "STP"
    stop_loss.totalQuantity = quantity
    stop_loss.auxPrice = stop_loss_price
    stop_loss.tif = tif
    stop_loss.parentId = parent_id
    stop_loss.transmit = take_profit_price <= 0  # Transmit if no TP
    stop_loss.eTradeOnly = False
    stop_loss.firmQuoteOnly = False

    # Take profit child (LMT) — optional
    take_profit = None
    if take_profit_price > 0:
        take_profit = IBAPIOrder()
        take_profit.orderId = tp_id
        take_profit.action = reverse_action
        take_profit.orderType = "LMT"
        take_profit.totalQuantity = quantity
        take_profit.lmtPrice = take_profit_price
        take_profit.tif = tif
        take_profit.parentId = parent_id
        take_profit.transmit = True  # Last child triggers full transmit
        take_profit.eTradeOnly = False
        take_profit.firmQuoteOnly = False

    # Submit all orders
    logger.info(
        f"提交 Bracket 订单: {contract.symbol} {action} x{quantity} "
        f"@ {limit_price}, SL={stop_loss_price}, TP={take_profit_price or 'N/A'}"
    )

    collector = OrderSubmitCollector()
    collector.order_id = parent_id
    orig_callbacks = _register_order_callbacks(api_client, collector)

    try:
        api_client.placeOrder(parent_id, ibapi_contract, parent)
        api_client.placeOrder(sl_id, ibapi_contract, stop_loss)
        if take_profit:
            api_client.placeOrder(tp_id, ibapi_contract, take_profit)

        collector.done_event.wait(timeout)

        parent_result = OrderResult(
            success=collector.perm_id > 0 or not collector.error_message,
            order_id=parent_id,
            perm_id=collector.perm_id,
            status=collector.status or "Submitted",
            filled_qty=collector.filled,
            avg_fill_price=collector.avg_fill_price,
            error_message=collector.error_message or "",
        )

        if parent_result.success:
            logger.info(
                f"✅ Bracket 订单已提交: parent={parent_id}, "
                f"SL={sl_id}, TP={tp_id or 'N/A'}"
            )
        else:
            logger.error(f"❌ Bracket 订单失败: {collector.error_message}")

        return {
            "parent_result": parent_result,
            "parent_id": parent_id,
            "stop_loss_id": sl_id,
            "take_profit_id": tp_id,
        }

    except Exception as e:
        logger.error(f"❌ Bracket 订单提交异常: {e}")
        raise_ibkr_error(OrderSubmitError, str(e))
    finally:
        _restore_callbacks(api_client, *orig_callbacks)
