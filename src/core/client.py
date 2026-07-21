"""
IBKR Gateway 客户端模块

负责与IB Gateway/TWS的连接和数据交互
基于ibkr.0414遗留代码重构，消除硬编码

官方文档: https://interactivebrokers.github.io/tws-api/

重要限制说明:
1. reqOpenOrders/reqAllOpenOrders - 无法获取已取消或完全成交的订单
   "Note: it is not possible to obtain cancelled or fully filled orders."

2. reqExecutions - 需要在 TWS 中启用 Trade Log 才能获取历史成交数据

3. Client ID 绑定 - 订单与提交它的 client ID 绑定，只有该 client 能修改

4. Paper Account - 某些功能可能受限制，需在 TWS 设置中启用:
   - "Download open orders on connection"
   - "Download trades on connection"

Used logging:
- ibkr.client: 业务逻辑
- ibapi: 原始底层日志
"""

import logging
import threading
import time
from typing import Optional, Dict, Any, List
from datetime import datetime
import json

from src.core.logger import get_logger, create_audit_record

# logger需在ibapi import之前定义（except块中使用）
logger = get_logger(__name__)
ibapi_logger = get_logger("ibapi")

# Attempt to import ibapi, but provide fallback dummies if unavailable
try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.account_summary_tags import AccountSummaryTags
except ImportError as e:
    logger.error(f"❌ IBKR API 依赖缺失: {e}")
    logger.error("请安装依赖: pip install ibapi>=9.81")
    logger.error("参考文档: docs/important.md")
    raise ImportError(
        "缺少 IBKR API 依赖，请运行: pip install ibapi>=9.81"
    ) from e


from config.config import GatewayConfig
from src.core.models import (
    AccountInfo,
    Position,
    ConnectionResult,
    Bar,
)
from src.core.exceptions import (
    NotConnectedError,
    QueryTimeoutError,
    AccountInfoError,
)


class IBKRClientError(Exception):
    """IBKR客户端错误基类"""

    pass


class ConnectionError(IBKRClientError):
    """连接错误"""

    pass


class TimeoutError(IBKRClientError):
    """超时错误"""

    pass


class IBKRApiClient(EWrapper, EClient):
    """IBKR API 客户端包装，提供一个统一的 `connect` 接口以兼容上层 `IBKRClient`。"""

    def connect(self, host: str, port: int, client_id: int):
        """建立到 IB Gateway 的 TCP 连接。

        通过调用底层 `EClient` 的 `connect` 方法完成实际的网络握手。
        - `host`、`port`、`client_id` 均来源于 `IBKRConfig.gateway`。
        - 连接成功后会触发 `nextValidId` 回调，进而把 `self.connected` 置为 `True`。
        """
        try:
            # 调用父类 EClient 的 connect 方法
            EClient.connect(self, host, port, client_id)
        except Exception as exc:
            # 记录错误，保持向上层抛出统一异常类型
            logger.error(f"IBKRApiClient 连接失败: {exc}")
            raise
        # 等待 onConnectAck（或 nextValidId）回调设置 `self.connected`
        timeout = time.time() + 10
        while not self.connected and time.time() < timeout:
            time.sleep(0.1)
        if not self.connected:
            raise RuntimeError("IBKRApiClient 连接超时，未收到 nextValidId 回调")

    def openOrder(self, order_id, contract, order, order_state):
        """活跃订单回调 - 正式方法"""
        for callback in self._open_order_callbacks:
            callback(order_id, contract, order, order_state)

    def openOrderEnd(self):
        """活跃订单请求结束"""
        for callback in self._open_order_end_callbacks:
            callback()

    def completedOrder(self, contract, order, orderState):
        """已完成订单回调"""
        order_id = getattr(order, "orderId", getattr(order, "order_id", None))
        logger.info(
            f"📝 已完成订单 [ID={order_id}]: {contract.symbol} {order.action} {order.totalQuantity} {order.orderType}"
        )

        # 尝试更新订单状态事件
        if (
            hasattr(self, "_order_status_events")
            and order_id in self._order_status_events
        ):
            event_data = self._order_status_events[order_id]
            status = orderState.status
            filled = orderState.filled
            perm_id = orderState.permId

            event_data["status"] = status
            event_data["filled"] = filled
            event_data["perm_id"] = perm_id

            logger.info(
                f"🔄 更新已完成订单状态：{status}, PermID={perm_id}, Filled={filled}"
            )

            if status in ["Filled", "Cancelled", "Rejected"]:
                event_data["event"].set()

        # 调用注册的回调
        for callback in self._completed_order_callbacks:
            callback(contract, order, orderState)

    def completedOrderEnd(self):
        """已完成订单请求结束"""
        logger.info("✅ 已完成订单数据接收完成")
        for callback in self._completed_order_end_callbacks:
            callback()

    """
    IBKR API客户端
    
    继承EWrapper和EClient，实现IBKR API回调
    """

    def __init__(self):
        EClient.__init__(self, self)
        self.connected = False
        self.next_order_id = None
        self.account_info: Dict[str, Any] = {}
        self._account_summary_received = threading.Event()
        self._positions_received = threading.Event()
        self._req_id_counter = 1
        self._error_message: Optional[str] = None
        self._managed_account: str = ""
        self._executions_cache: List[
            Dict[str, Any]
        ] = []  # 新增：存储已成交订单的执行记录

        # 历史数据缓存
        self._historical_data: Dict[int, List[Bar]] = {}
        self._historical_data_events: Dict[int, threading.Event] = {}

        # 市场数据错误跟踪 (req_id -> error_code)
        self._market_data_errors: Dict[int, int] = {}

        # 回调注册机制（替代动态赋值）
        self._open_order_callbacks = []
        self._open_order_end_callbacks = []
        self._completed_order_callbacks = []
        self._completed_order_end_callbacks = []

    def get_next_req_id(self) -> int:
        """获取下一个请求ID"""
        req_id = self._req_id_counter
        self._req_id_counter += 1
        return req_id

    # ========== EWrapper 回调方法 ==========

    def nextValidId(self, order_id: int):
        """接收到下一个有效订单ID - 连接成功标志"""
        super().nextValidId(order_id)
        self.next_order_id = order_id
        self.connected = True
        logger.info(f"连接成功，订单ID: {order_id}")

    def managedAccounts(self, accounts_list: str):
        """管理账户列表 — 优先匹配配置的 account_id"""
        super().managedAccounts(accounts_list)
        configured = getattr(self, '_configured_account', '')
        accounts = accounts_list.split(",") if accounts_list else []
        if configured and configured in accounts:
            self._managed_account = configured
        else:
            self._managed_account = accounts[0] if accounts else ""
        logger.info(f"管理账户: {self._managed_account} (配置={configured})")

    def error(self, req_id: int, error_code: int, error_string: str):
        """错误处理"""

        # 市场数据订阅失败 (10089/300) — 已知问题，跳过 super().error()
        # 避免 ibapi.wrapper 打印 ERROR 日志；由 get_market_data 自动回退 yfinance
        if error_code in (300, 10089) and req_id >= 0:
            self._market_data_errors[req_id] = error_code
            logger.debug(f"市场数据订阅失败 [{error_code}]: {error_string} [ReqID={req_id}]")
            return

        super().error(req_id, error_code, error_string)

        # 特殊处理：10268 - ETradeOnly 属性不支持
        # 这通常发生在限价单中错误地设置了 ETradeOnly 标志
        # 我们的代码没有主动设置此标志，可能是 ibapi 库的默认行为或兼容性问题
        # 解决方案：在创建订单时明确设置 eTradeOnly=False (默认即为 False，无需额外设置)
        if error_code == 10268:
            logger.warning(
                f"API 错误 [10268]: {error_string} - 这可能是限价单的兼容性问题，通常不影响订单提交"
            )
            # 不将其视为致命错误，继续处理
            return

        # 市场数据农场连接中 / 数据源重连 — 瞬时状态，非错误
        if error_code == 2119:
            logger.debug(f"市场数据农场连接中 [{error_code}]: {error_string}")
            return

        # 合约定义未找到 — 已知场景(TSX股票、非交易时段等)，非致命
        if error_code == 200:
            logger.debug(f"合约定义未找到 [{error_code}]: {error_string}")
            return

        # Account summary 请求超限 — 由 accountSummaryEnd 自动 cancel 释放，
        # 理论上不再触发；若仍出现则为残留订阅，debug 记录即可
        if error_code == 322:
            logger.debug(f"账户摘要请求超限 [{error_code}]: {error_string}")
            return

        # 连接相关消息（非错误）
        if error_code in (2104, 2106, 2107, 2158):
            logger.debug(f"连接消息 [{error_code}]: {error_string}")
            return

        # 连接错误（含 326 client_id 冲突 — API 连接后异步到达）
        if error_code in (326, 502, 503, 504, 501):
            logger.error(f"连接错误 [{error_code}]: {error_string}")
            self.connected = False
            self._error_message = f"{error_code}: {error_string}"
            return

        # 其他错误
        logger.warning(f"API 错误 [{error_code}]: {error_string}")

    def connectAck(self):
        """连接确认"""
        super().connectAck()
        self.connected = True
        logger.info("IBKR连接已确认")

    def connectionClosed(self):
        """连接关闭"""
        super().connectionClosed()
        self.connected = False
        logger.warning("IBKR 连接已关闭")

    def tickPrice(self, req_id: int, tick_type: int, price: float, attrib):
        """市场价格回调"""
        super().tickPrice(req_id, tick_type, price, attrib)
        if not hasattr(self, "_market_data"):
            self._market_data = {}
        self._market_data[req_id] = {"price": price, "tick_type": tick_type}
        logger.debug(f"收到价格数据 [ReqID={req_id}]: 价格={price}")

    def tickSize(self, req_id: int, tick_type: int, size: int):
        """市场数量回调"""
        super().tickSize(req_id, tick_type, size)
        if not hasattr(self, "_market_data"):
            self._market_data = {}
        if req_id in self._market_data:
            self._market_data[req_id]["size"] = size

    def historicalData(self, req_id: int, bar):
        """历史K线数据回调"""
        super().historicalData(req_id, bar)
        b = Bar(
            time=bar.date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            wap=getattr(bar, "wap", 0.0),
            count=getattr(bar, "barCount", 0),
        )
        if req_id not in self._historical_data:
            self._historical_data[req_id] = []
        self._historical_data[req_id].append(b)

    def historicalDataEnd(self, req_id: int, start_date: str, end_date: str):
        """历史K线数据接收结束"""
        super().historicalDataEnd(req_id, start_date, end_date)
        if req_id in self._historical_data_events:
            self._historical_data_events[req_id].set()

    def orderStatus(
        self,
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
        """订单状态回调 - IBKR 服务器确认订单的关键证据"""
        super().orderStatus(
            order_id,
            status,
            filled,
            remaining,
            avg_fill_price,
            perm_id,
            parent_id,
            last_fill_price,
            client_id,
            why_held,
            mkt_cap_price,
        )

        # 记录订单状态变化（包含所有关键字段）
        logger.info(
            f"📊 订单状态更新 [ID={order_id}]: Status={status}, "
            f"Filled={filled}, Remaining={remaining}, "
            f"PermID={perm_id}, AvgPrice={avg_fill_price:.2f}"
        )

        # 存储最新状态到内部字典，供 place_order 等待
        if not hasattr(self, "_order_status_events"):
            self._order_status_events = {}

        # 创建或更新事件对象
        if order_id not in self._order_status_events:
            self._order_status_events[order_id] = {
                "event": threading.Event(),
                "status": status,
                "filled": filled,
                "perm_id": perm_id,
                "avg_fill_price": avg_fill_price,
                "remaining": remaining,
            }
        else:
            # 关键修复：更新时同步所有字段，不仅仅是 status 和 filled
            event_data = self._order_status_events[order_id]
            event_data["status"] = status
            event_data["filled"] = filled
            event_data["perm_id"] = perm_id
            event_data["avg_fill_price"] = avg_fill_price
            event_data["remaining"] = remaining

        # 如果订单已最终状态（Filled, Cancelled, Rejected），触发事件
        if status in ["Filled", "Cancelled", "Rejected", "ApiCancelled"]:
            self._order_status_events[order_id]["event"].set()

    def orderBound(self, order_id: int, api_client_id: int, api_order_id: int):
        """订单绑定回调 - API order ID 与 permId 的映射"""
        logger.info(
            f"🔗 订单绑定: OrderID={order_id}, ApiClientID={api_client_id}, ApiOrderID={api_order_id}"
        )

    def accountSummary(
        self, req_id: int, account: str, tag: str, value: str, currency: str
    ):
        """账户摘要回调"""
        if account not in self.account_info:
            self.account_info[account] = {}
        self.account_info[account][tag] = {"value": value, "currency": currency}
        if tag == "AccountType":
            self.account_info[account]["accountType"] = value

    def accountSummaryEnd(self, req_id: int):
        """账户摘要结束 — 数据已到齐，立刻释放订阅避免 322 叠加超限"""
        self._account_summary_received.set()
        self.cancelAccountSummary(req_id)

    def position(
        self, account: str, contract: Contract, position: float, avg_cost: float
    ):
        """持仓回调"""
        if account not in self.account_info:
            self.account_info[account] = {}
        if "positions" not in self.account_info[account]:
            self.account_info[account]["positions"] = {}

        symbol = contract.symbol
        self.account_info[account]["positions"][symbol] = {
            "position": position,
            "avg_cost": avg_cost,
            "contract": contract,
        }
        logger.debug(f"持仓: {symbol} - {position}股 @ {avg_cost}")

    def positionEnd(self):
        """持仓结束"""
        logger.debug("持仓数据接收完成")
        self._positions_received.set()

    def reqCompletedOrders(self):
        """请求已完成订单"""
        logger.info("📋 请求已完成订单...")
        if self.connected:
            super().reqCompletedOrders(apiOnly=False)
        else:
            logger.error("❌ 未连接，无法请求已完成订单")

    def reqAllOpenOrders(self):
        """请求所有活跃订单 - 包括其他客户端提交的

        官方文档: https://interactivebrokers.github.io/tws-api/open_orders.html

        reqOpenOrders 只返回当前 client ID 提交的订单
        reqAllOpenOrders 返回所有客户端提交的订单
        """
        logger.info("📋 请求所有活跃订单...")
        if self.connected:
            super().reqAllOpenOrders()
        else:
            logger.error("❌ 未连接，无法请求订单")

    def reqOpenOrders(self):
        """请求当前客户端提交的活跃订单

        官方文档: https://interactivebrokers.github.io/tws-api/open_orders.html
        """
        logger.info("📋 请求活跃订单...")
        if self.connected:
            super().reqOpenOrders()
        else:
            logger.error("❌ 未连接，无法请求订单")



class IBKRClient:
    def reqCompletedOrders(self):
        """向 IB Gateway 请求已完成订单，结果会通过回调填充到 _executions_cache"""
        if not self._api_client:
            raise RuntimeError("IBKRClient 未连接，无法请求已完成订单")
        try:
            self._api_client.reqCompletedOrders()
        except Exception as e:
            logger.error(f"请求已完成订单失败: {e}")
            raise

    """IBKR客户端 - 高级封装
    
    提供简化的连接和数据获取接口
    """

    def __init__(self, config: GatewayConfig):
        """
        初始化客户端

        Args:
            config: Gateway配置对象或完整的IBKRConfig对象
        """
        # 如果传入的是完整的IBKRConfig对象，提取gateway配置
        if hasattr(config, "gateway"):
            self._full_config = config
            self.config = config.gateway
        else:
            self._full_config = None
            self.config = config
        self._api_client: Optional[IBKRApiClient] = None
        self._event_thread: Optional[threading.Thread] = None

    def connect(self) -> ConnectionResult:
        """
        连接到IB Gateway

        Returns:
            ConnectionResult: 连接结果
        """
        client_id = self.config.client_id

        # 审计操作开始
        create_audit_record(
            operation="connect",
            success=False,
            host=self.config.host,
            port=self.config.port,
            client_id=client_id,
            attempt=1,
        )

        logger.info(
            f"尝试连接 IB Gateway {self.config.host}:{self.config.port} "
            f"(client_id={client_id})"
        )

        for attempt in range(self.config.max_retries):
            operation_details = {
                "host": self.config.host,
                "port": self.config.port,
                "client_id": client_id,
                "attempt": attempt + 1,
                "max_retries": self.config.max_retries,
            }

            try:
                self._api_client = IBKRApiClient()
                self._api_client._configured_account = self.config.account_id

                # 先启动事件循环线程，确保回调能被处理
                self._event_thread = threading.Thread(
                    target=self._run_event_loop, daemon=True
                )
                self._event_thread.start()

                # 再连接
                self._api_client.connect(self.config.host, self.config.port, client_id)

                # 等待连接确认
                timeout = time.time() + self.config.timeout
                while not self._api_client.connected and time.time() < timeout:
                    time.sleep(0.1)

                if self._api_client.connected:
                    # 等待一小段时间捕获异步错误（如 326 client_id 冲突）
                    settle_time = time.time() + 0.5
                    while time.time() < settle_time:
                        err = self._api_client._error_message
                        if err and "326" in err:
                            logger.warning(
                                f"检测到 client_id 冲突 (异步 326)"
                            )
                            raise ConnectionError(str(err))
                        time.sleep(0.05)

                    logger.info(f"连接成功 (第{attempt + 1}次尝试)")

                    # 记录成功审计
                    create_audit_record(
                        operation="connect",
                        success=True,
                        host=self.config.host,
                        port=self.config.port,
                        client_id=client_id,
                        attempt=attempt + 1,
                    )

                    return ConnectionResult(
                        success=True,
                        host=self.config.host,
                        port=self.config.port,
                        error_message="连接成功",
                        client_id=client_id,
                    )
                else:
                    error_msg = self._api_client._error_message or "连接超时"
                    ibapi_logger.warning(f"连接失败：{error_msg} (第{attempt + 1}次)")

                    # 处理 326 race condition:
                    # connectAck 后 error(326) 异步到达，在检查 connected 前已将其置为 False
                    if "326" in error_msg:
                        logger.error(f"检测到 client_id 冲突 (326 race)")
                        if self._api_client:
                            try:
                                self._api_client.disconnect()
                            except Exception:
                                pass
                        self._api_client = None
                        self._event_thread = None
                        client_id += 1
                        logger.info(f"递增 client_id 为 {client_id}，准备重试")
                        time.sleep(0.5)
                        continue

                    logger.warning(f"连接超时，重试中...", extra={"error": error_msg})

            except Exception as e:
                error_str = str(e)
                ibapi_logger.error(f"连接异常：{error_str}")
                logger.error(f"连接失败", exc_info=True)

                # 检查是否是 client_id 冲突错误 (326)
                if "326" in error_str:
                    # 断开旧连接释放资源，否则旧连接仍占用 client_id
                    if self._api_client:
                        try:
                            self._api_client.disconnect()
                        except Exception:
                            pass
                    self._api_client = None
                    self._event_thread = None

                    client_id += 1
                    logger.info(f"检测到 client_id 冲突，递增为 {client_id}，准备重试")
                    time.sleep(0.5)  # 等旧连接完全释放
                    continue

                # 其他错误，等待后重试
                if attempt < self.config.max_retries - 1:
                    wait_time = self.config.retry_delay * (attempt + 1)
                    logger.info(f"重试等待 {wait_time} 秒...")
                    time.sleep(wait_time)

        # 全部尝试失败
        failure_msg = f"连接失败，已重试 {self.config.max_retries} 次"
        logger.error(failure_msg)

        # 记录失败审计
        create_audit_record(
            operation="connect",
            success=False,
            host=self.config.host,
            port=self.config.port,
            client_id=client_id,
            attempt=attempt + 1,
            max_retries=self.config.max_retries,
            failure_reason=failure_msg,
        )

        return ConnectionResult(
            success=False,
            host=self.config.host,
            port=self.config.port,
            error_message=failure_msg,
            client_id=client_id,
        )

    def disconnect(self):
        """断开连接（带超时保护）"""
        import threading
        if self._api_client and self._api_client.isConnected():
            result = [None]
            def _disconnect():
                try:
                    self._api_client.disconnect()
                    result[0] = "ok"
                    logger.info("已断开IBKR连接")
                except Exception as e:
                    result[0] = f"err:{e}"
                    logger.error(f"断开连接时出错: {e}")
            t = threading.Thread(target=_disconnect)
            t.start()
            t.join(timeout=5)
            if t.is_alive():
                logger.warning("IBKR disconnect 超时，强制终止")
        self._api_client = None
        self._event_thread = None

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._api_client is not None and self._api_client.connected

    def is_paper(self) -> bool:
        """检查是否为模拟账户 (Paper Trading) — 优先使用配置的 account_id"""
        # 配置的 account_id 最可靠
        if self.config.account_id:
            return self.config.account_id.startswith("DU")
        api = self._api_client
        if hasattr(api, "_managed_account") and api._managed_account:
            return api._managed_account.startswith("DU")
        if hasattr(api, "account_info") and api.account_info:
            for acc_id in api.account_info.keys():
                if acc_id and acc_id.startswith("DU"):
                    return True
        return False

    def get_api_client(self) -> "IBKRApiClient":
        """获取底层 API 客户端实例

        供高层模块（如 account.py, orders.py）直接调用 IBKR API 方法。

        Returns:
            IBKRApiClient: 底层 API 客户端实例

        Raises:
            NotConnectedError: 如果未连接
        """
        if not self._api_client or not self._api_client.connected:
            raise NotConnectedError("未连接到 IB Gateway，无法获取 API 客户端")
        return self._api_client

    def _run_event_loop(self):
        """运行事件循环"""
        # 等待连接成功后再进入事件循环
        for _ in range(300):
            if not self._api_client:
                break
            if self._api_client.connected:
                break
            time.sleep(0.1)
        
        while self._api_client and self._api_client.connected:
            try:
                self._api_client.run()
            except Exception as e:
                logger.error(f"事件循环异常: {e}")
                break

    def get_account_info(self, timeout: int = 30) -> AccountInfo:
        """
        获取账户信息（资金、持仓、账户类型）

        Args:
            timeout: 超时时间（秒）

        Returns:
            AccountInfo: 账户信息

        Raises:
            TimeoutError: 数据获取超时
            AccountInfoError: 账户数据解析失败
            ConnectionError: 未连接到IB Gateway
        """
        # 审计操作开始
        create_audit_record(
            operation="get_account_info",
            success=False,
            timeout=timeout,
            client_id=self.config.client_id,
        )

        if not self._api_client or not self._api_client.connected:
            connection_err = ConnectionError("未连接到IB Gateway")
            logger.error(
                "获取账户信息失败", exc_info=True, extra={"error": str(connection_err)}
            )
            create_audit_record(
                operation="get_account_info",
                details={"error": "not_connected"},
                success=False,
            )
            raise connection_err

        try:
            # 重置状态
            self._api_client.account_info = {}
            self._api_client._account_summary_received.clear()
            self._api_client._positions_received.clear()

            # 请求账户摘要（accountSummaryEnd 回调会自动 cancel 释放订阅）
            req_id = self._api_client.get_next_req_id()
            self._api_client.reqAccountSummary(
                req_id, "All", AccountSummaryTags.AllTags
            )

            # 请求持仓
            self._api_client.reqPositions()

            # 等待账户摘要（必须）
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self._api_client._account_summary_received.is_set():
                    break
                time.sleep(0.1)
            else:
                timeout_err = TimeoutError("请求账户信息超时")
                logger.error("获取账户信息超时", exc_info=True)
                create_audit_record(
                    operation="get_account_info",
                    details={"error": "timeout"},
                    success=False,
                )
                raise timeout_err

            # 等待持仓（可选 — paper 账户可能不发持仓数据）
            positions_waited = time.time() - start_time
            remaining = max(5, timeout - positions_waited)
            while time.time() - start_time < remaining:
                if self._api_client._positions_received.is_set():
                    break
                time.sleep(0.1)
            if not self._api_client._positions_received.is_set():
                logger.warning("持仓数据未收到（可能为 paper 账户），使用空持仓继续")

            # 解析数据
            account_info = self._parse_account_info()

            # 审计操作成功
            create_audit_record(
                operation="get_account_info",
                success=True,
                account_id=account_info.account_id,
                net_liquidation=account_info.net_liquidation,
                position_count=len(account_info.positions),
                currency=account_info.currency,
            )

            return account_info

        except Exception as e:
            # 记录失败审计
            create_audit_record(
                operation="get_account_info",
                success=False,
                error_type=type(e).__name__,
                error_message=str(e),
                client_id=self.config.client_id,
            )

            # 日志记录
            logger.error(f"获取账户信息失败: {e}", exc_info=True)

            if isinstance(e, (TimeoutError, ConnectionError)):
                raise

            raise AccountInfoError(f"获取账户信息错误: {e}")

    def _parse_account_info(self) -> AccountInfo:
        """解析账户信息

        官方文档：https://www.interactivebroker.com/en/software/tws/account_information_names.htm

        Raises:
            AccountInfoError: 当无法确定账户ID或账户类型时抛出
        """
        if not self._api_client or not self._api_client.account_info:
            logger.warning("无账户数据可解析")
            return AccountInfo(account_id="UNKNOWN")

        try:
            # 优先使用配置的 account_id 查找，否则取第一个
            configured_id = self.config.account_id
            if configured_id and configured_id in self._api_client.account_info:
                account, data = configured_id, self._api_client.account_info[configured_id]
            else:
                account, data = next(iter(self._api_client.account_info.items()))

            account_info = AccountInfo(
                account_id=account,
                cash_balance=float(data.get("TotalCashValue", {}).get("value", 0.0)),
                buying_power=float(data.get("BuyingPower", {}).get("value", 0.0)),
                net_liquidation=float(data.get("NetLiquidation", {}).get("value", 0.0)),
                total_securities_value=float(
                    data.get("TotalSecuritiesValue", {}).get("value", 0.0)
                ),
                unrealized_pnl=float(data.get("UnrealizedPnL", {}).get("value", 0.0)),
                realized_pnl=float(data.get("RealizedPnL", {}).get("value", 0.0)),
                currency=data.get("NetLiquidation", {}).get("currency", "USD"),
                is_paper=account.startswith("DU"),
            )

            if not account_info.account_id:
                raise AccountInfoError("账户ID缺失")

            if "positions" in data:
                for symbol, pos_data in data["positions"].items():
                    if not isinstance(pos_data, dict):
                        continue
                    quantity = pos_data.get("position", 0)
                    avg_cost = pos_data.get("avg_cost", 0.0)
                    market_price = pos_data.get("mktPrice", 0.0)

                    contract = pos_data.get("contract", {})
                    if hasattr(contract, "symbol"):
                        sec_type = getattr(contract, "secType", "STK")
                        currency = getattr(contract, "currency", "USD")
                    elif isinstance(contract, dict):
                        sec_type = contract.get("secType", "STK")
                        currency = contract.get("currency", "USD")
                    else:
                        sec_type = "STK"
                        currency = "USD"

                    account_info.positions.append(
                        Position(
                            account=account,
                            symbol=symbol,
                            secType=sec_type,
                            exchange="SMART",
                            currency=currency,
                            quantity=quantity,
                            average_cost=avg_cost,
                            market_price=market_price,
                            market_value=abs(quantity * market_price)
                            if market_price > 0
                            else 0,
                        )  # secType (驼峰) 对齐 IB API
                    )

            logger.info(
                f"账户信息解析完成：{account_info.account_id} ({len(account_info.positions)} 个持仓)"
            )
            return account_info

        except AccountInfoError:
            raise
        except Exception as e:
            logger.error(f"解析账户信息失败: {e}")
            raise AccountInfoError(f"账户数据解析失败: {e}")

    def __enter__(self):
        """上下文管理器入口"""
        result = self.connect()
        if not result.success:
            raise ConnectionError(result.message)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.disconnect()
        return False

    def get_market_data(self, symbols: List[str], timeout: int = 30) -> Dict[str, Dict]:
        """
        获取市场数据（价格、成交量等）

        Args:
            symbols: 股票代码列表
            timeout: 超时时间（秒）

        Returns:
            Dict[str, Dict]: {symbol: {"price": float, "volume": int, ...}}
        """
        create_audit_record(
            operation="get_market_data", success=False, symbols=symbols, timeout=timeout
        )

        if not self._api_client or not self._api_client.connected:
            raise NotConnectedError("未连接到 IB Gateway")

        api_client = self._api_client
        api_client._market_data = {}
        api_client._market_data_errors = {}
        market_data = {}

        start_req_id = self._api_client.get_next_req_id()
        request_ids = {}

        for i, symbol in enumerate(symbols):
            req_id = start_req_id + i
            request_ids[req_id] = symbol

            contract = create_contract(symbol, "STK", "SMART", "CAD")
            try:
                api_client.reqMktData(req_id, contract, "", False, False, [])
                logger.debug(f"请求 {symbol} 市场数据 [ReqID={req_id}]")
            except Exception as e:
                logger.warning(f"请求 {symbol} 市场数据失败: {e}")

        start_time = time.time()
        while time.time() - start_time < timeout:
            if len(api_client._market_data) >= len(request_ids):
                break
            time.sleep(0.1)

        for req_id, symbol in request_ids.items():
            if req_id in api_client._market_data:
                market_data[symbol] = api_client._market_data[req_id]
                logger.debug(f"获取到 {symbol} 价格: {market_data[symbol].get('price')}")

        for req_id in request_ids.keys():
            try:
                api_client.cancelMktData(req_id)
            except:
                pass

        create_audit_record(
            operation="get_market_data",
            success=True,
            symbols=symbols,
            data_count=len(market_data),
            source="ibkr",
        )

        return market_data

    def get_historical_data(
        self, symbol: str, days: int = 60, bar_size: str = "1 day", timeout: int = 30
    ) -> List[Bar]:
        """获取历史K线数据

        Args:
            symbol: 股票代码
            days: 历史天数
            bar_size: K线粒度 ("1 day", "1 hour", "30 mins", etc.)
            timeout: 超时秒数

        Returns:
            List[Bar]: K线列表
        """
        api = self.get_api_client()
        req_id = api.get_next_req_id()

        contract = create_contract(symbol, "STK", "SMART", "USD")
        api._historical_data_events[req_id] = threading.Event()

        end_dt = datetime.now().strftime("%Y%m%d-%H:%M:%S")
        duration = f"{days} D"

        api.reqHistoricalData(
            req_id, contract, end_dt, duration, bar_size,
            "TRADES", 1, 1, False, []
        )

        if not api._historical_data_events[req_id].wait(timeout=timeout):
            api._historical_data_events.pop(req_id, None)
            raise TimeoutError(f"获取 {symbol} 历史数据超时 (请求 {days} 天)")

        bars = api._historical_data.pop(req_id, [])
        api._historical_data_events.pop(req_id, None)
        return bars

    def get_positions_with_prices(
        self, account_id: str = "", timeout: int = 30,
        _positions=None  # 可选：直接传入 positions，避免 get_positions_with_prices 内部重复调 get_account_info
    ) -> List[Position]:
        """
        获取持仓（含实时价格）

        Args:
            account_id: (已废弃，仅保留签名兼容性)
            timeout: 超时时间
            _positions: (可选) 直接传入 positions 列表，避免重复调用 get_account_info

        Returns:
            List[Position]: 持仓列表（含实时价格和计算后的市值/pnl）
        """
        if _positions is not None:
            positions = _positions
        else:
            account_info = self.get_account_info(timeout)
            positions = account_info.positions

        if positions and self._api_client and self._api_client.connected:
            from src.core.market_data import MarketDataProvider

            data_source = getattr(self.config, "market_data_source", "yfinance")
            mdp = MarketDataProvider(self, data_source=data_source)
            symbols = [pos.symbol for pos in positions]
            market_data = mdp.fetch_basic(symbols)  # {symbol: MarketData}

            for pos in positions:
                md = market_data.get(pos.symbol)
                price = md.price if md else 0.0
                pos.market_price = price
                pos.market_value = abs(pos.quantity * price) if price > 0 else 0.0
                pos.unrealized_pnl = (
                    (price - pos.average_cost) * pos.quantity if price > 0 else 0.0
                )

        return positions


def create_contract(
    symbol: str, sec_type: str = "STK", exchange: str = "SMART", currency: str = "USD",
    expiry: str = "", multiplier: str = "", trading_class: str = "",
) -> Contract:
    """
    创建合约对象

    Args:
        symbol: 代码 (支持 RY.TO 格式，自动转换为 IBKR 格式)
        sec_type: 证券类型 (STK, FUT, OPT 等)
        exchange: 交易所
        currency: 货币
        expiry: 到期月 YYYYMM (FUT 必填)
        multiplier: 合约乘数 (FUT 填写，如 "50")
        trading_class: 交易类别 (FUT 填写，如 "ES")

    Returns:
        Contract: 合约对象
    """
    contract = Contract()

    if "." in symbol:
        parts = symbol.split(".")
        contract.symbol = parts[0]
        suffix = parts[1]
        if suffix == "TO":
            contract.exchange = "TSE"
            contract.currency = "CAD"
        else:
            contract.exchange = exchange
            contract.currency = currency
    else:
        contract.symbol = symbol
        contract.exchange = exchange
        contract.currency = currency

    contract.secType = sec_type

    if expiry:
        contract.lastTradeDateOrContractMonth = expiry
    if multiplier:
        contract.multiplier = multiplier
    if trading_class:
        contract.tradingClass = trading_class

    return contract
