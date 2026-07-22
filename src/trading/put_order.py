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
from src.core.orders import place_order, place_bracket_order, Order
from src.core.market_data import MarketDataProvider
from src.core.risk_engine import RiskEngine
from src.core.logger import get_logger
from config.config import get_instrument_registry

logger = get_logger(__name__)


def _check_long_only_mode(
    action: str, symbol: str, qty: int, pos: int
) -> tuple[bool, str]:
    """
    Long Only 模式校验（allow_short_selling=false）：
    叠加信号后持仓不得为负（不能变为空头）。

    公式: new_pos = pos + qty（BUY时加，SELL时减）
    只要 new_pos < 0 → 禁止

    Returns:
        (passed, reason): passed=True 表示通过，reason 非空表示失败原因
    """
    if action == "SELL":
        new_pos = pos - qty
    elif action == "BUY":
        new_pos = pos + qty
    else:
        return True, ""

    if new_pos < 0:
        return False, (
            f"Long Only: {symbol} 持仓 {pos} 股，"
            f"{action} {qty} 股后变为空头 ({new_pos} 股)，禁止"
        )
    return True, ""


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
            registry = get_instrument_registry()
            spec = registry.get(symbol)

            if spec.is_futures:
                sym = symbol
                contract = create_contract(
                    symbol=sym,
                    sec_type=spec.sec_type,
                    exchange=spec.exchange,
                    currency=spec.currency,
                    expiry=spec.front_month,
                    multiplier=str(spec.multiplier),
                    trading_class=spec.trading_class,
                )
            elif "." in symbol:
                parts = symbol.split(".")
                sym = parts[0]
                sec_type = "STK"
                exchange = "SMART"
                currency = "USD"
                if parts[1] == "TO":
                    exchange = "TSE"
                    currency = "CAD"
                contract = create_contract(
                    symbol=sym,
                    sec_type=sec_type,
                    exchange=exchange,
                    currency=currency,
                )
            else:
                sym = symbol
                contract = create_contract(
                    symbol=sym,
                    sec_type=spec.sec_type,
                    exchange=spec.exchange,
                    currency=spec.currency,
                )

            order_obj = Order(
                order_id=0,
                account=client.config.account_id or "",
                action=ib_action,
                order_type="LMT",
                total_quantity=quantity,
                limit_price=round((signal.get("price") or signal.get("target_price") or 0) * 1.005, 2),
                tif="DAY",
                order_ref="universe-selector",
            )

            # ── 价格获取逻辑 ──────────────────────────────────────────
            # oco_enabled=false: 总是 MKT（不挂 OCO）
            # oco_enabled=true:  尝试 MDP 获取价格
            #   - 成功 → LMT + bracket order
            #   - 失败 → 降级 MKT（不挂 OCO）
            client_cfg = getattr(client, "_full_config", None)
            us_cfg = getattr(client_cfg, "universe_selector", None)
            oco_enabled = getattr(us_cfg, "oco_enabled", True) if us_cfg else True

            if not oco_enabled:
                # OCO 关闭：总是 MKT
                order_obj.order_type = "MKT"
                order_obj.limit_price = 0
                logger.info(f"📌 {symbol} OCO 关闭，使用 MKT")
            else:
                # OCO 开启：尝试 MDP 获取市价
                try:
                    data_source = getattr(client.config, "market_data_source", "yfinance")
                    mdp = MarketDataProvider(client=client, data_source=data_source)
                    md_map = mdp.fetch_basic([sym])
                    md = md_map.get(sym) if md_map else None
                    if md and md.price > 0:
                        order_obj.limit_price = round(md.price * 1.005, 2)
                        logger.info(f"📌 MDP 获取市价 {md.price} → limit_price={order_obj.limit_price}")
                    else:
                        # MDP 返回空 → 降级 MKT（不挂 OCO）
                        order_obj.order_type = "MKT"
                        order_obj.limit_price = 0
                        logger.warning(f"⚠️ {symbol} MDP 返回空，降级为 MKT（无 OCO）")
                except Exception as e:
                    # MDP 失败 → 降级 MKT（不挂 OCO）
                    order_obj.order_type = "MKT"
                    order_obj.limit_price = 0
                    logger.warning(f"⚠️ {symbol} MarketDataProvider 失败: {e}，降级为 MKT（无 OCO）")
            # ── end 价格获取逻辑 ────────────────────────────────────────

            has_price = order_obj.limit_price > 0

            if has_price:
                # 有价格：LMT + bracket order（父+子同时提交）
                try:
                    sl_pct = getattr(us_cfg, "stop_loss_pct", -10.0) if us_cfg else -10.0
                    tp_pct = getattr(us_cfg, "take_profit_pct", 20.0) if us_cfg else 20.0
                    sl_price = round(order_obj.limit_price * (1 + sl_pct / 100), 2)
                    tp_price = round(order_obj.limit_price * (1 + tp_pct / 100), 2)
                    logger.info(f"📌 OCO: {symbol} LMT={order_obj.limit_price} SL={sl_price} TP={tp_price}")
                    bracket_result = place_bracket_order(
                        client=client,
                        contract=contract,
                        action=ib_action,
                        quantity=quantity,
                        limit_price=order_obj.limit_price,
                        stop_loss_price=sl_price,
                        take_profit_price=tp_price,
                        tif="GTC",
                    )
                    place_result = bracket_result.get("parent_result")
                except Exception as e:
                    logger.warning(f"⚠️ bracket order 失败: {e}")
                    place_result = place_order(client, order_obj, contract, timeout=30)
            else:
                # 无价格：降级 MKT
                order_obj.order_type = "MKT"
                order_obj.limit_price = 0
                logger.warning(f"⚠️ {symbol} 无法获取市价，降级为 MKT（无 OCO）")
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
                if has_price:
                    bracket_result = place_bracket_order(
                        client=client,
                        contract=contract_tse,
                        action=ib_action,
                        quantity=quantity,
                        limit_price=order_obj.limit_price,
                        stop_loss_price=sl_price,
                        take_profit_price=tp_price,
                        tif="GTC",
                    )
                    place_result = bracket_result.get("parent_result")
                else:
                    place_result = place_order(client, order_obj, contract_tse, timeout=30)

            # ── 状态码映射 ──────────────────────────────────────────────
            # IBKR 返回首字母大写状态（Submitted/Filled/Cancelled）
            # 转换为全大写统一处理
            status_map = {
                "SUBMITTED": "SUBMITTED",
                "FILLED": "FILLED",
                "CANCELLED": "CANCELLED",
                "INACTIVE": "UNKNOWN",
                "REJECTED": "REJECTED",
                "UNKNOWN": "UNKNOWN",
            }
            mapped_status = status_map.get(place_result.status.upper(), "UNKNOWN")

            return {
                "status": mapped_status,
                "message": place_result.error_message or "成功",
                "perm_id": getattr(place_result, "perm_id", None),
                "order_id": getattr(place_result, "order_id", None),
                "filled_qty": place_result.filled_qty or 0.0,
                "avg_price": place_result.avg_fill_price or 0.0,
                "success": place_result.success,
            }

        except Exception as e:
            error_msg = str(e)
            if "market data utility" in error_msg.lower() or "no data" in error_msg.lower():
                logger.warning(f"⚠️ {symbol} MarketDataProvider 失败: {e}")
                return {
                    "status": "UNKNOWN",
                    "message": f"MarketDataProvider 失败: {e}",
                    "perm_id": None,
                    "order_id": None,
                    "filled_qty": 0.0,
                    "avg_price": 0.0,
                    "success": False,
                }
            raise

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
        if positions_map is None and signal.get("action") in ("SELL", "BUY"):
            qty_check = signal.get("quantity", 1)
            if signal.get("action") == "SELL" or qty_check == -1:
                try:
                    account_info = client.get_account_info(timeout=10)
                    positions_map = {}
                    for pos in account_info.positions:
                        positions_map[pos.symbol] = pos.quantity
                except Exception:
                    pass

        # 方向校验: Long Only 模式（allow_short_selling=false）
        allow_short = getattr(client._full_config, "allow_short_selling", False)
        if not allow_short:
            action = signal.get("action", "")
            symbol = signal.get("symbol", "")
            qty = signal.get("quantity", 0)
            pos = positions_map.get(symbol, 0) if positions_map else 0
            passed, reason = _check_long_only_mode(action, symbol, qty, pos)
            if not passed:
                logger.warning(f"⚠️ {reason}")
                order = {
                    "order_id": f"local_{i + 1:03d}",
                    "signal": signal,
                    "status": "FAILED",
                    "perm_id": None,
                    "processed": True,
                    "filled_qty": 0.0,
                    "avg_price": 0.0,
                    "message": reason,
                    "success": False,
                    "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                order_list.append(order)
                continue

        # 预检查: SELL 且无持仓 → 跳过
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

        # 预检查: BUY(ALL) 且无空头持仓 → 跳过
        qty = signal.get("quantity", 1)
        if signal.get("action") == "BUY" and qty == -1 and positions_map is not None:
            pos = positions_map.get(signal.get("symbol"), 0)
            if pos >= 0:
                logger.warning(f"⚠️ 跳过买入信号 - 无空头持仓: {signal.get('symbol')}")
                order = {
                    "order_id": f"local_{i + 1:03d}",
                    "signal": signal,
                    "status": "FAILED",
                    "perm_id": None,
                    "processed": True,
                    "filled_qty": 0.0,
                    "avg_price": 0.0,
                    "message": f"跳过买入信号 - 无空头持仓: {signal.get('symbol')}",
                    "success": False,
                    "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                order_list.append(order)
                continue

        # 执行层: quantity=-1 表示"全部持仓"，替换为实际数量
        if qty == -1 and positions_map is not None:
            actual_qty = int(positions_map.get(signal.get("symbol"), 0))
            if signal.get("action") == "SELL" and actual_qty > 0:
                logger.info(f"📊 SELL quantity=-1 → 替换为实际持仓 {actual_qty}: {signal.get('symbol')}")
                signal["quantity"] = actual_qty
            elif signal.get("action") == "BUY" and actual_qty < 0:
                logger.info(f"📊 BUY quantity=-1 → 替换为空头持仓 {abs(actual_qty)}: {signal.get('symbol')}")
                signal["quantity"] = abs(actual_qty)

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
