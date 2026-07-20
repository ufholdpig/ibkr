"""盘后分析模块

功能：
1. 获取当日真实成交数据（从 IBKR 或本地日志）
2. 对比盘前计划与实际执行
3. 生成盘后报告（JSON + Markdown）
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, List
from src.core.client import IBKRClient
from src.core.market_data import MarketDataProvider
from src.core.models import Execution
from src.core.logger import get_logger
from src.core.account import get_managed_account
from src.core.paths import (
    get_post_report_date,
    get_path
)
from src.trading.get_order import (
    OrderCommon,
    OrderOnline,
    get_opened_orders,
    get_completed_orders,
    get_executed_orders,
)
from config.config import load_config

logger = get_logger(__name__)


# 1. 定义dataclass（OrderCommon/OrderOnline 从 src.core.orders 导入）
@dataclass
class OrderLocal(OrderCommon):
    """本地订单数据，来自 data/intra_day/order_YYYYMMDD.json"""

    signal_name: str = ""  # 信号名称（从 signal 字典中提取）
    source: str = ""       # 信号来源（watch / pre-market 等）
    strategy_id: str = ""  # 策略 ID


@dataclass
class PostMarketReport:
    """盘后报告 raw data（纯净原始数据，不含计算结果）"""

    generated: str
    account_id: str
    report_date: str
    pre_orders: List[OrderLocal]
    intra_orders: List[OrderLocal]
    orders_opened: List[OrderOnline]
    orders_executed: List[OrderOnline]
    orders_completed: List[OrderOnline]
    positions: List[Dict]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


# 2. 盘后模块 PostMarketExecutor
class PostMarketExecutor:
    """盘后报告执行器"""

    def __init__(self, date: str = None):
        """初始化盘后执行器

        Args:
            date: 交易日期 YYYYMMDD，默认今日
        """
        logger.info("-" * 60)
        logger.info(f"  盘后执行器初始化...")

        # 1. 处理日期参数：严格验证格式
        if date:
            try:
                datetime.strptime(date, "%Y%m%d")
                self.report_date = date
            except ValueError:
                raise ValueError(f"日期格式错误，请使用 YYYYMMDD 格式（如 20260426）")
        else:
            self.report_date = get_post_report_date()

        # 2. 连接 IBKR
        self.client = None
        self.account_id = None

        try:
            config = load_config()
            self.client = IBKRClient(config)
            self.market_data_source = config.market_data_source
            result = self.client.connect()
            if not result.success:
                raise Exception(f"❌ 连接失败: {result.error_message}")

            # 获取账户ID
            account = get_managed_account(self.client, timeout=5)
            if not account:
                raise Exception("❌ 无法获取账户 ID")
            self.account_id = account
            logger.debug(f"📊 账户 ID: {self.account_id}")

        except Exception as e:
            logger.error(f"❌ IBKR 初始化失败: {e}")
            # TODO - 应该抛出异常，不允许离线模式运行

        # 3. 数据容器：初始化 PostMarketReport
        self.report = PostMarketReport(
            generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            account_id=self.account_id or "",
            report_date=self.report_date,
            pre_orders=[],
            intra_orders=[],
            orders_opened=[],
            orders_executed=[],
            orders_completed=[],
            positions=[],
        )

        logger.info(f"✅ 盘后执行器初始化完成，报告日期: {self.report_date}")

    def _load_orders(self):
        """加载指定日期的订单文件"""
        with open(
            get_path("orders", f"order_{self.report_date}.json"), "r", encoding="utf-8"
        ) as f:
            self.order_data = json.load(f)

        pre_orders = self.order_data.get("orders_pre_market", [])
        intra_orders = self.order_data.get("orders_intra_day", [])
        logger.debug(f"📄今日订单: 盘前={len(pre_orders)}，盘中={len(intra_orders)}")

    def _get_executions_from_ibkr(self, timeout: int = 30) -> List[Execution]:
        """从 IBKR 获取当日成交记录"""
        if not self.client or not self.client.is_connected() or not self.account_id:
            return []

        try:
            executions = get_executed_orders(
                self.client, account_id=self.account_id, timeout=timeout
            )
            logger.info(f"📊 从 IBKR 获取到 {len(executions)} 笔成交")
            return executions
        except Exception as e:
            logger.warning(f"⚠️ 从 IBKR 获取成交失败：{e}")
            return []

    def _get_positions_from_ibkr(self, timeout: int = 30) -> List[Dict]:
        """从 IBKR 获取当前持仓（含实时市值）

        持仓基础数据（quantity, avg_cost）来自 IBKR position 回调，
        实时价格通过 MarketDataProvider 获取（支持 auto/ibkr/yfinance 配置），
        市值和未实现盈亏在本地计算。
        """
        if not self.client or not self.client.is_connected():
            logger.warning("客户端未连接，无法获取持仓")
            return []

        try:
            logger.info(f"📊 请求 IBKR 持仓 (account_id={self.account_id}, source={self.market_data_source})...")
            account_info = self.client.get_account_info(timeout=timeout)
            if not account_info.positions:
                logger.warning("⚠️ 账户信息中无持仓数据")
                return []

            positions = account_info.positions  # List[Position]
            logger.info(f"📊 获取 {len(positions)} 个持仓，开始获取实时价格...")

            # 通过 MarketDataProvider 获取实时价格（支持 auto/ibkr/yfinance 配置）
            mdp = MarketDataProvider(self.client, data_source=self.market_data_source)
            symbols = [p.symbol for p in positions]
            market_data = mdp.fetch_basic(symbols)  # {symbol: MarketData}

            result = []
            for pos in positions:
                md = market_data.get(pos.symbol)
                price = md.price if md else 0.0
                market_value = abs(pos.quantity * price) if price > 0 else 0.0
                unrealized_pnl = (price - pos.average_cost) * pos.quantity if price > 0 else 0.0
                result.append({
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "market_value": market_value,
                    "average_cost": pos.average_cost,
                    "unrealized_pnl": unrealized_pnl,
                })

            logger.info(f"📊 持仓市值计算完成（{self.market_data_source}）")
            return result
        except Exception as e:
            logger.error(f"❌ 持仓获取失败: {e}")
            return []

    def _generate_json(self):
        """把收集到的数据汇总到 PostMarketReport（保持 raw data 纯净）

        按照 docs/refactor-post_market-0502.md 的要求：
        a. 将 self.order_data 的本地订单填入 pre_orders 和 intra_orders
        b. 从 IBKR 获取在线订单，区分 orders_opened, orders_executed, orders_completed
        c. 获取持仓
        d. 不计算 summary，保持 raw data 纯净
        """
        # 1. 加载本地订单数据
        self._load_orders()

        # 2. 填充 pre_orders 和 intra_orders（从 self.order_data 转换）
        # 盘前订单
        for order_data in self.order_data.get("orders_pre_market", []):
            sig = order_data.get("signal", {})
            order_common = OrderCommon(
                symbol=sig.get("symbol", ""),
                action=sig.get("action", ""),
                quantity=sig.get("quantity", 0),
                filled_qty=order_data.get("filled_qty", 0),
                avg_price=order_data.get("avg_price", 0.0),
                perm_id=order_data.get("perm_id", 0),
                order_id=order_data.get("order_id", 0),
                status=order_data.get("status", ""),
                exec_time=sig.get("timestamp", ""),
            )
            local_order = OrderLocal(
                **asdict(order_common),
                signal_name=sig.get("strategy_name", ""),
                source=sig.get("source", ""),
                strategy_id=sig.get("strategy_id", ""),
            )
            self.report.pre_orders.append(local_order)

        # 盘中订单
        for order_data in self.order_data.get("orders_intra_day", []):
            sig = order_data.get("signal", {})
            order_common = OrderCommon(
                symbol=sig.get("symbol", ""),
                action=sig.get("action", ""),
                quantity=sig.get("quantity", 0),
                filled_qty=order_data.get("filled_qty", 0),
                avg_price=order_data.get("avg_price", 0.0),
                perm_id=order_data.get("perm_id", 0),
                order_id=order_data.get("order_id", 0),
                status=order_data.get("status", ""),
                exec_time=sig.get("timestamp", ""),
            )
            local_order = OrderLocal(
                **asdict(order_common),
                signal_name=sig.get("strategy_name", ""),
                source=sig.get("source", ""),
                strategy_id=sig.get("strategy_id", ""),
            )
            self.report.intra_orders.append(local_order)

        # 3. 从 IBKR 获取成交记录，回填本地订单的成交数据
        if self.client and self.client.is_connected():
            # 获取活跃订单
            self.report.orders_opened = get_opened_orders(self.client, timeout=30)
            # 已完成订单不可用（API 限制）
            self.report.orders_completed = []

            executions = self._get_executions_from_ibkr()
            if executions:
                self._merge_executions(executions)

            # 获取持仓
            self.report.positions = self._get_positions_from_ibkr()

        filled_count = sum(
            1 for o in self.report.pre_orders + self.report.intra_orders
            if o.filled_qty > 0
        )
        logger.info(
            f"📊 报告数据汇总完成: "
            f"盘前={len(self.report.pre_orders)}, "
            f"盘中={len(self.report.intra_orders)}, "
            f"活跃={len(self.report.orders_opened)}, "
            f"已成交={len(self.report.orders_executed)}, "
            f"持仓={len(self.report.positions)}"
        )

    def _merge_executions(self, executions: List[Execution]):
        """用 IBKR 成交记录回填本地订单的 filled_qty/avg_price，并生成 orders_executed"""
        from collections import defaultdict

        # 按 perm_id 分组
        exec_by_perm: Dict[int, List[Execution]] = defaultdict(list)
        for e in executions:
            if e.perm_id > 0:
                exec_by_perm[e.perm_id].append(e)

        # 回填盘前订单
        for order in self.report.pre_orders:
            if order.perm_id in exec_by_perm:
                execs = exec_by_perm[order.perm_id]
                total_shares = sum(e.shares for e in execs)
                order.filled_qty = int(total_shares)
                order.avg_price = sum(e.shares * e.price for e in execs) / total_shares
                order.status = "FILLED"

        # 回填盘中订单
        for order in self.report.intra_orders:
            if order.perm_id in exec_by_perm:
                execs = exec_by_perm[order.perm_id]
                total_shares = sum(e.shares for e in execs)
                order.filled_qty = int(total_shares)
                order.avg_price = sum(e.shares * e.price for e in execs) / total_shares
                order.status = "FILLED"

        # 生成 orders_executed 列表
        self.report.orders_executed = []
        for perm_id, execs in exec_by_perm.items():
            first = execs[0]
            total_shares = sum(e.shares for e in execs)
            avg_price = sum(e.shares * e.price for e in execs) / total_shares
            side = "BUY" if first.side == "BOT" else "SELL"
            self.report.orders_executed.append(OrderOnline(
                symbol=first.symbol,
                action=side,
                quantity=int(total_shares),
                filled_qty=int(total_shares),
                avg_price=avg_price,
                perm_id=perm_id,
                order_id=first.order_id,
                status="FILLED",
                exec_time=first.exec_time,
            ))

        # 按 perm_id 降序排列
        self.report.orders_executed.sort(key=lambda o: o.perm_id, reverse=True)

    def _generate_markdown(self) -> str:
        """把 PostMarketReport 数据转换成可读报告"""
        # 计算统计信息（不依赖 summary 字段）
        total_orders = len(self.report.pre_orders) + len(self.report.intra_orders)
        total_filled = sum(
            1 for o in self.report.pre_orders + self.report.intra_orders
            if o.filled_qty > 0
        )
        total_opened = len(self.report.orders_opened)
        total_positions = len(self.report.positions)

        lines = [
            "# 🌙 盘后复盘报告",
            f"**报告时间**: {self.report.generated}",
            f"**报告日期**: {self.report.report_date}",
            f"**账户 ID**: {self.report.account_id}",
            "",
            "## 📊 执行摘要",
            f"- **信号订单**: {total_orders}",
            f"- **已成交**: {total_filled}",
            f"- **活跃订单**: {total_opened}",
            f"- **当前持仓**: {total_positions}",
            "",
        ]

        def _price_str(p: float, fq: int) -> str:
            return f"${p:.2f}" if fq > 0 and p > 0 else "-"

        # 盘前订单
        if self.report.pre_orders:
            lines.extend(
                [
                    "## 📋 盘前订单",
                    "",
                    "| 来源 | 策略 | 股票 | 操作 | 数量 | 成交数 | 成交价 | 状态 | PermID |",
                    "|:----:|:----:|:----:|:----:|:----:|:------:|:------:|:----:|:------:|",
                ]
            )
            for order in self.report.pre_orders:
                source_tag = "👁 watch" if "watch" in order.source else order.source
                lines.append(
                    f"| {source_tag} | {order.signal_name} | {order.symbol} | {order.action} | {order.quantity} | "
                    f"{order.filled_qty} | {_price_str(order.avg_price, order.filled_qty)} | "
                    f"{order.status} | {order.perm_id} |"
                )

        # 盘中订单
        if self.report.intra_orders:
            lines.extend(
                [
                    "",
                    "## 📋 盘中订单",
                    "",
                    "| 来源 | 策略 | 股票 | 操作 | 数量 | 成交数 | 成交价 | 状态 | PermID |",
                    "|:----:|:----:|:----:|:----:|:----:|:------:|:------:|:----:|:------:|",
                ]
            )
            for order in self.report.intra_orders:
                source_tag = "👁 watch" if "watch" in order.source else order.source
                lines.append(
                    f"| {source_tag} | {order.signal_name} | {order.symbol} | {order.action} | {order.quantity} | "
                    f"{order.filled_qty} | {_price_str(order.avg_price, order.filled_qty)} | "
                    f"{order.status} | {order.perm_id} |"
                )

        # 活跃订单
        if self.report.orders_opened:
            lines.extend(
                [
                    "",
                    "## 📋 活跃订单 (Open Orders)",
                    "",
                    "| 股票 | 操作 | 数量 | 状态 | 类型 | PermID | OrderID |",
                    "|:----:|:----:|:----:|:----:|:----:|:------:|:-------:|",
                ]
            )
            for order in sorted(
                self.report.orders_opened, key=lambda x: x.order_id, reverse=True
            ):
                status_emoji = "⏳" if "PreSubmitted" in order.status else "🔄"
                lines.append(
                    f"| {order.symbol} | {order.action} | {order.quantity} | "
                    f"{status_emoji} {order.status} | {order.order_type} | "
                    f"{order.perm_id} | {order.order_id} |"
                )

        # 当前持仓
        if self.report.positions:
            lines.extend(
                [
                    "",
                    "## 📈 当前持仓",
                    "",
                    "| 股票 | 数量 | 市值 | 平均成本 | 未实现盈亏 |",
                    "|:----:|:----:|:------:|:------:|:----------:|",
                ]
            )
            for pos in self.report.positions:
                cost = pos.get("average_cost", 0)
                pnl = pos.get("unrealized_pnl", 0)
                pnl_str = f"${pnl:.2f}" if isinstance(pnl, (int, float)) else str(pnl)
                if isinstance(pnl, (int, float)):
                    if pnl < 0:
                        pnl_str = f"🔴 {pnl_str}"
                    elif pnl > 0:
                        pnl_str = f"🟢 {pnl_str}"
                lines.append(
                    f"| {pos.get('symbol', 'N/A')} | {pos.get('quantity', 0)} | "
                    f"${pos.get('market_value', 0):.2f} | ${cost:.2f} | {pnl_str} |"
                )

        lines.extend(
            [
                "",
                "---",
                f"*报告由 Hermes IBKR 系统自动生成 | 生成时间：{self.report.generated}*",
            ]
        )

        return "\n".join(lines)

    def _save_report(self) -> None:
        """保存报告到 JSON 和 Markdown（使用 self.report）"""
        report = get_path("reports", f"post-report_{self.report_date}")

        with open(f"{report}.json", "w", encoding="utf-8") as f:
            f.write(self.report.to_json())

        md_content = self._generate_markdown()
        with open(f"{report}.md", "w", encoding="utf-8") as f:
            f.write(md_content)

        logger.info(f"✅ 盘后数据：{report}.json")
        logger.info(f"✅ 盘后报告：{report}.md")


# ── 盘后分析摘要（供 watch daemon 调用） ──────────────────────────────
def summarize_watch_orders():
    """读取当日订单文件，统计 watch 信号执行情况

    Returns:
        dict: {"total": int, "success": int, "failed": int} 或空字典
    """
    from src.core.paths import get_order_file

    logger.info("=" * 50)
    logger.info("盘后分析 — watch 订单摘要")

    order_file = get_order_file()
    if not order_file.exists():
        logger.info("今日无订单文件")
        logger.info("盘后分析完成")
        logger.info("=" * 50)
        return {}

    try:
        with open(order_file) as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"读取订单文件异常: {e}")
        logger.info("盘后分析完成")
        logger.info("=" * 50)
        return {}

    watch_orders = [
        o for o in data.get("orders_intra_day", [])
        if o.get("signal", {}).get("source") == "watch"
    ]

    if watch_orders:
        success_count = sum(1 for o in watch_orders if o.get("success"))
        total = len(watch_orders)
        failed = total - success_count
        logger.info(f"今日 watch 订单: {total} 笔 ({success_count} 成功 / {failed} 失败)")
        for o in watch_orders:
            sig = o.get("signal", {})
            logger.info(
                f"  {sig.get('symbol', '?')} {sig.get('action', '?')} "
                f"x{sig.get('quantity', '?')} @ ${o.get('avg_price', 0):.2f} "
                f"→ {o.get('status', 'UNKNOWN')}"
            )
        logger.info("盘后分析完成")
        logger.info("=" * 50)
        return {"total": total, "success": success_count, "failed": failed}

    logger.info("今日无 watch 订单")
    logger.info("盘后分析完成")
    logger.info("=" * 50)
    return {}


# 3. 暴露 execute 方法供 ibclient.py 使用
def execute(date: str = None, account: str = None) -> bool:
    """外部调用接口 - 供 ibclient.py 使用

    Args:
        date: 指定日期YYYYMMDD，默认今日
        account: 指定账户ID，默认自动获取

    Returns:
        bool: 成功返回 True，失败返回 False
    """
    from src.core.paths import set_data_mode, resolve_data_mode
    cfg = load_config()
    set_data_mode(resolve_data_mode(cfg.gateway.account_id or ""))

    executor = None

    try:
        # 创建执行器（__init__ 内部会连接 IBKR）
        executor = PostMarketExecutor(date=date)

        # 如果 __init__ 没有成功获取 account_id，尝试手动获取
        if not executor.account_id:
            if account:
                executor.account_id = account
            else:
                print("❌ 无法获取账户 ID，请使用 --account 指定")
                return False

        print(f"🚀 生成盘后报告...")
        print(f"📋 账户 ID: {executor.account_id}")

        # 生成报告（更新 self.report）
        executor._generate_json()

        # 保存报告（使用 self.report）
        executor._save_report()

        # 计算统计信息（不使用 summary）
        total_orders = len(executor.report.pre_orders) + len(
            executor.report.intra_orders
        )

        print(f"✅ 报告已生成")
        print(f"   信号订单: {total_orders}")
        print(f"   已成交: {len(executor.report.orders_executed)}")
        print(f"   活跃订单: {len(executor.report.orders_opened)}")
        print(f"   当前持仓: {len(executor.report.positions)}")

        return True

    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback

        traceback.print_exc()
        return False

    finally:
        if executor is not None and executor.client:
            try:
                executor.client.disconnect()
            except:
                pass
