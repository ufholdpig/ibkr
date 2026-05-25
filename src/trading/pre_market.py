"""
Pre-Market Module (盘前模块)

功能：
1. 读取信号文件 data/signals/signal_YYYYMMDD.json 中的 signals_pre_market
2. 连接 IBKR 提交订单（通过 build_and_submit_order 统一入口）
3. 转化并保存到 data/orders/order_YYYYMMDD.json 中的 orders_pre_market
4. 回填订单状态、permID 等字段到本地订单文件
5. 更新信号文件中的 processed=true
6. 生成盘前报告 data/reports/pre-report_YYYYMMDD.md

设计原则：
- 统一使用 build_and_submit_order() 提交订单（内部调用 orders.place_order）
- 无论成功失败，都标记 processed=true
- 单个订单失败不影响后续订单执行
- 整个执行过程只连接一次 IBKR
"""

import json
from datetime import datetime
from src.core.paths import (
    get_path,
    get_trading_date,
    get_signal_file,
    get_order_file
)
from src.core.logger import get_logger
from src.core.client import IBKRClient
from src.trading.put_order import process_signals
from config.config import load_config

logger = get_logger(__name__)


class PreMarketExecutor:
    """盘前执行器"""

    def __init__(self):
        """初始化盘前执行器"""
        logger.info("-" * 60)
        logger.info(f"  盘前执行器初始化...")

        self.today = get_trading_date()
        self.signal_file = get_signal_file()
        self.order_file = get_order_file()
        self.report_file = get_path("reports", f"pre-report_{self.today}.md")

        if not self.signal_file.exists():
            signal_data = {
                "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "signals_pre_market": [],
                "signals_intra_day": [],
            }
            with open(self.signal_file, "w", encoding="utf-8") as f:
                json.dump(signal_data, f, indent=2, ensure_ascii=False)

        if not self.order_file.exists():
            order_data = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "orders_pre_market": [],
                "orders_intra_day": [],
            }
            with open(self.order_file, "w", encoding="utf-8") as f:
                json.dump(order_data, f, indent=2, ensure_ascii=False)

        logger.info(f"✅ 盘前执行器初始化完成")

    def _generate_pre_market_report(self):
        """
        生成盘前报告（表格格式，参考盘后报告）
        """
        logger.info("📊 生成盘前报告...")

        # 1. 读取信号文件 self.signal_file
        with open(self.signal_file, "r", encoding="utf-8") as f:
            signal_data = json.load(f)

        # 2. 读取订单文件 self.order_file
        with open(self.order_file, "r", encoding="utf-8") as f:
            order_data = json.load(f)

        # 获取信号和订单数据
        signals = signal_data.get("signals_pre_market", [])
        orders = order_data.get("orders_pre_market", [])

        # 统计
        total_signals = len(signals)
        processed_signals = sum(1 for s in signals if s.get("processed", False))
        total_orders = len(orders)
        filled_orders = sum(1 for o in orders if o.get("status") == "FILLED")
        submitted_orders = sum(1 for o in orders if o.get("status") == "SUBMITTED")
        failed_orders = sum(1 for o in orders if o.get("status") == "FAILED")
        unknown_orders = sum(1 for o in orders if o.get("status") == "UNKNOWN")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "# ☀️ 盘前交易报告",
            f"**报告时间**: {now}",
            f"**报告日期**: {self.today}",
            "",
            "## 📊 执行摘要",
            f"- **信号总数**: {total_signals}",
            f"- **已处理信号**: {processed_signals}",
            f"- **订单总数**: {total_orders}",
            f"- **订单状态**: 已提交 {submitted_orders} / 已成交 {filled_orders} / 失败 {failed_orders} / 未知 {unknown_orders}",
            "",
        ]

        lines.append("## 📋 盘前订单详情")
        lines.append("")
        lines.append(
            "| 策略 | 股票 | 操作 | 数量 | 状态 | PermID | OrderID |"
        )
        lines.append(
            "|:----:|:----:|:----:|:----:|:----:|:------:|:-------:|"
        )

        if orders:
            for order in orders:
                sig = order.get("signal", {}) if isinstance(order.get("signal"), dict) else {}
                strategy = sig.get("strategy_name", "")
                symbol = sig.get("symbol", "")
                action = sig.get("action", "")
                quantity = sig.get("quantity", "")
                status = order.get("status", "")
                perm_id = order.get("perm_id", "")
                order_id = order.get("order_id", "")
                lines.append(
                    f"| {strategy} | {symbol} | {action} | {quantity} | "
                    f"{status} | {perm_id} | {order_id} |"
                )
        else:
            lines.append("| - | - | - | - | - | - | - |")

        lines.extend(
            [
                "",
                "---",
                f"*报告由 Hermes IBKR 系统自动生成 | 生成时间：{now}*",
            ]
        )

        report_content = "\n".join(lines)

        # 保存报告
        with open(self.report_file, "w", encoding="utf-8") as f:
            f.write(report_content)

        logger.info(f"✅ 盘前报告已生成: {self.report_file}")

    def execute(self):
        """
        外部调用接口 - 整个执行过程只连接一次 IBKR
        1. 读取信号文件，获取未处理盘前信号
        2. 连接 IBKR
        3. 遍历未处理信号：构建订单 → 提交到 IBKR → 回填结果
        4. 更新信号文件 + 订单文件
        5. 断开 IBKR
        6. 生成盘前报告
        """
        logger.debug("开始处理盘前信号...")

        # 1. 读取信号文件
        if not self.signal_file.exists():
            logger.info(f"信号文件不存在: {self.signal_file}")
            self._generate_pre_market_report()
            return

        with open(self.signal_file, "r", encoding="utf-8") as f:
            signal_data = json.load(f)

        signals = signal_data.get("signals_pre_market", [])
        unprocessed = [s for s in signals if not s.get("processed", False)]

        logger.info(f"今日有 {len(signals)} 个盘前信号，{len(unprocessed)} 个未处理")

        if not unprocessed:
            logger.info("无未处理信号，跳过 IBKR 连接")
            self._generate_pre_market_report()
            return

        # 2. 连接 IBKR
        config = load_config()
        client = IBKRClient(config)

        try:
            conn_result = client.connect()
            if not conn_result.success:
                raise Exception(f"连接失败: {conn_result.error_message}")

            logger.debug("IBKR 已连接，开始提交订单...")

            # 读取订单文件
            with open(self.order_file, "r", encoding="utf-8") as f:
                order_data = json.load(f)

            orders = order_data.get("orders_pre_market", [])

            # 3. 遍历未处理信号（process_signals 内部处理提交 + 组装 + sleep）
            process_signals(client, unprocessed, orders)

            # 4. 更新文件
            with open(self.order_file, "w", encoding="utf-8") as f:
                json.dump(order_data, f, indent=2, ensure_ascii=False)
            logger.debug(f"更新订单文件: {self.order_file}")

            with open(self.signal_file, "w", encoding="utf-8") as f:
                json.dump(signal_data, f, indent=2, ensure_ascii=False)
            logger.debug(f"更新信号文件: {self.signal_file}")

            # 5. 断开连接
            client.disconnect()
            logger.debug("IBKR 已断开连接")

        except Exception as e:
            logger.error(f"执行过程异常: {e}")
            try:
                client.disconnect()
            except Exception:
                pass
            raise

        # 6. 生成报告
        self._generate_pre_market_report()


def execute():
    """
    外部调用接口 - 供 ibclient.py 使用
    """
    from src.core.paths import set_data_mode, resolve_data_mode
    config = load_config()
    set_data_mode(resolve_data_mode(config.gateway.account_id or ""))
    executor = PreMarketExecutor()
    executor.execute()


# 测试代码
if __name__ == "__main__":
    try:
        execute()
        print("盘前处理完成")
    except Exception as e:
        print(f"盘前处理失败: {e}")
