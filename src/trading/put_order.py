"""
订单提交模块（trading 层）

统一 trading 层的订单提交流程：
1. 解析 signal dict → Contract + Order
2. 调用 core 层的 place_order 提交到 IBKR
3. 将 OrderResult 映射为 order dict 字段

被 PreMarketExecutor / IntraDayExecutor 共用。
"""

import time
from datetime import datetime
from src.core.client import IBKRClient, create_contract
from src.core.orders import place_order, Order
from src.core.risk_engine import RiskEngine
from src.core.logger import get_logger

logger = get_logger(__name__)


def build_and_submit_order(client: IBKRClient, signal: dict,
                           positions_map: dict | None = None,
                           risk_engine: RiskEngine | None = None) -> dict:
    """
    根据 signal dict 构建订单并通过 place_order 提交到 IBKR

    Args:
        client: 已连接的 IBKR 客户端
        signal: 信号字典（含 symbol, action, quantity 等字段）
        positions_map: 可选持仓缓存 {symbol: quantity}，避免重复调用 get_account_info
        risk_engine: 可选风控引擎，如果提供则在提交前执行 TFSA 合规检查

    Returns:
        dict: 包含提交结果的字段
            {perm_id, order_id, status, filled_qty, avg_price, message, success, error}
    """
    symbol = signal.get("symbol", "")
    action = signal.get("action", "")
    quantity = signal.get("quantity", 1)

    logger.debug(f"提交订单: {symbol} {action} {quantity}")

    # TFSA 合规前置检查
    if risk_engine is not None:
        decisions = risk_engine.precheck_order(
            symbol=symbol,
            action=action,
            quantity=quantity,
            price=signal.get("target_price", 0) or signal.get("price", 0),
            positions_map=positions_map,
        )
        rejected = [d for d in decisions if d.is_rejected()]
        if rejected:
            reasons = "; ".join(d.message for d in rejected)
            error_msg = f"风控拦截: {reasons}"
            logger.warning("❌ %s", error_msg)
            return {
                "status": "REJECTED",
                "message": error_msg,
                "perm_id": None,
                "order_id": None,
                "filled_qty": 0.0,
                "avg_price": 0.0,
                "success": False,
            }

    try:
        # 卖出信号检查持仓（非致命 — 策略已前置验证，此处仅兜底）
        if action == "SELL":
            try:
                if positions_map is None:
                    account_info = client.get_account_info(timeout=10)
                    positions_map = {}
                    for pos in account_info.positions:
                        positions_map[pos.symbol] = pos.quantity

                position_held = positions_map.get(symbol, 0)
                if position_held == 0:
                    error_msg = f"卖出信号无效 - 无持仓: {symbol}"
                    logger.warning(f"⚠️ {error_msg}")
                    return {
                        "status": "FAILED",
                        "message": error_msg,
                        "perm_id": None,
                        "order_id": None,
                        "filled_qty": 0.0,
                        "avg_price": 0.0,
                        "success": False,
                    }
                if position_held < 0:
                    logger.info(
                        f"📊 持仓检查: {symbol} = {position_held}股（空头），继续执行卖出信号"
                    )
                else:
                    qty_from_signal = int(signal.get("quantity", 1))
                    # quantity=-1 表示"全部持仓"，在此处已被 process_signals 替换
                    # 但如果直接调用 build_and_submit_order，也需兜底处理
                    if qty_from_signal == -1:
                        qty_from_signal = int(position_held)
                        logger.info(f"📊 SELL quantity=-1 兜底替换为持仓 {qty_from_signal}: {symbol}")
                    quantity = min(qty_from_signal, int(position_held))
                    if quantity != qty_from_signal:
                        logger.info(
                            f"📊 持仓检查: {symbol} = {position_held}股，卖出从 {qty_from_signal} 调整为 {quantity}"
                        )
                    signal["quantity"] = quantity

            except Exception as e:
                logger.warning(f"⚠️ 持仓检查失败（跳过，继续提交）: {e}")

        ib_action = "BUY" if action == "BUY" else "SELL"

        try:
            sec_type = "STK"
            exchange = "SMART"
            currency = "USD"

            if "." in symbol:
                parts = symbol.split(".")
                sym = parts[0]
                if parts[1] == "TO":
                    exchange = "TSE"
                    currency = "CAD"
            else:
                sym = symbol

            contract = create_contract(
                symbol=sym,
                sec_type=sec_type,
                exchange=exchange,
                currency=currency,
            )

            order_obj = Order(
                order_id=0,
                account=client.config.account_id or "",
                action=ib_action,
                order_type="MKT",
                total_quantity=quantity,
                tif="DAY",
            )

            place_result = place_order(client, order_obj, contract, timeout=30)

            # 如果 SMART/USD 找不到证券定义，自动尝试 TSE/CAD
            if (not place_result.success and exchange == "SMART" and currency == "USD"
                    and ("200" in place_result.error_message or "security definition" in place_result.error_message.lower()
                         or "证券定义" in place_result.error_message)):
                logger.info(f"🔄 SMART/USD 未找到证券 {sym}，尝试 TSE/CAD...")
                contract_tse = create_contract(
                    symbol=sym,
                    sec_type="STK",
                    exchange="TSE",
                    currency="CAD",
                )
                place_result = place_order(client, order_obj, contract_tse, timeout=30)

            server_status = getattr(place_result, "status", "Unknown")
            return {
                "perm_id": getattr(place_result, "perm_id", 0),
                "order_id": getattr(place_result, "order_id", 0),
                "filled_qty": getattr(place_result, "filled_qty", 0.0),
                "avg_price": getattr(place_result, "avg_fill_price", 0.0),
                "status": _get_execution_status(server_status),
                "message": getattr(place_result, "message", ""),
                "success": _is_success(server_status),
            }

        except Exception as e:
            error_msg = f"订单提交失败: {e}"
            logger.error(f"❌ {error_msg}")
            return {
                "status": "FAILED",
                "message": error_msg,
                "perm_id": None,
                "order_id": None,
                "filled_qty": 0.0,
                "avg_price": 0.0,
                "success": False,
            }

    except Exception as e:
        error_msg = f"订单处理异常: {e}"
        logger.error(f"❌ {error_msg}")
        return {
            "status": "FAILED",
            "message": error_msg,
            "perm_id": None,
            "order_id": None,
            "filled_qty": 0.0,
            "avg_price": 0.0,
            "success": False,
        }


def _get_execution_status(server_status: str) -> str:
    """根据 IBKR 服务器状态确定执行状态"""
    if not server_status:
        return "UNKNOWN"

    if server_status in ["PreSubmitted", "Submitted", "PendingSubmit"]:
        return "SUBMITTED"
    elif "Filled" in server_status or "PartiallyFilled" in server_status:
        return "FILLED"
    elif "Cancelled" in server_status:
        return "CANCELLED"
    elif "Rejected" in server_status:
        return "REJECTED"
    elif "Expired" in server_status:
        return "EXPIRED"
    else:
        return "UNKNOWN"


def _is_success(server_status: str) -> bool:
    """判断订单是否成功"""
    return server_status in ["PreSubmitted", "Submitted", "Filled"]


def process_signals(client: IBKRClient, signals: list, order_list: list):
    """
    批量处理信号：逐个提交订单并组装 order dict 追加到 order_list

    Args:
        client: 已连接的 IBKR 客户端
        signals: 未处理的信号列表（函数内部会标记 processed=True）
        order_list: 目标订单列表（每个信号对应的 order dict 会 append 到此列表）
    """
    positions_map = None
    for i, signal in enumerate(signals):
        signal["processed"] = True
        if signal.get("action") == "SELL" and positions_map is None:
            try:
                account_info = client.get_account_info(timeout=10)
                positions_map = {}
                for pos in account_info.positions:
                    positions_map[pos.symbol] = pos.quantity
            except Exception:
                pass

        # 预检查: SELL 且无持仓 → 跳过，不浪费提交资源
        if signal.get("action") == "SELL" and positions_map is not None:
            pos = positions_map.get(signal.get("symbol"), 0)
            if pos <= 0:
                logger.warning(f"⚠️ 跳过卖出信号 - 无持仓: {signal.get('symbol')}")
                order = {
                    "order_id": f"local_{i + 1:03d}",
                    "signal": signal,
                    "status": "FAILED",
                    "perm_id": None,
                    "processed": True,
                    "filled_qty": 0.0,
                    "avg_price": 0.0,
                    "message": f"跳过卖出信号 - 无持仓: {signal.get('symbol')}",
                    "success": False,
                    "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                order_list.append(order)
                continue
    
        # 执行层: quantity=-1 表示"全部持仓"，替换为实际数量
        qty = signal.get("quantity", 1)
        if qty == -1 and signal.get("action") == "SELL" and positions_map is not None:
            actual_qty = int(positions_map.get(signal.get("symbol"), 0))
            if actual_qty > 0:
                logger.info(f"📊 SELL quantity=-1 → 替换为实际持仓 {actual_qty}: {signal.get('symbol')}")
                signal["quantity"] = actual_qty

        submit_result = build_and_submit_order(client, signal, positions_map)

        order = {
            "order_id": submit_result.get("order_id", f"local_{i + 1:03d}"),
            "signal": signal,
            "status": submit_result.get("status", "UNKNOWN"),
            "perm_id": submit_result.get("perm_id"),
            "processed": True,
            "filled_qty": submit_result.get("filled_qty", 0.0),
            "avg_price": submit_result.get("avg_price", 0.0),
            "message": submit_result.get("message", ""),
            "success": submit_result.get("success", False),
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        order_list.append(order)
        logger.info(
            f"✅ {signal.get('symbol')} {signal.get('action')} "
            f"- Status={order['status']}, PermID={order['perm_id']}"
        )

        time.sleep(3)
