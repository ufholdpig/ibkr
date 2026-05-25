"""
Intra-Day Module (盘中模块)

功能：
1. 读取信号文件中的 signals_intra_day
2. 提交订单到 IBKR
3. 回填订单状态和 permID 到 orders_intra_day
4. 更新信号文件和订单文件

设计原则：
- 只处理 processed=false 的信号
- 无论成功失败，都标记 processed=true
- 单个订单失败不影响后续订单执行
- 整个执行过程只连接一次 IBKR
"""

import json

from src.core.paths import (
    get_trading_date,
    get_signal_file,
    get_order_file
)
from src.core.logger import get_logger
from src.core.client import IBKRClient
from src.trading.put_order import process_signals
from config.config import load_config

logger = get_logger(__name__)


class IntraDayExecutor:
    """盘中执行器 - 整个执行过程只连接一次 IBKR"""

    def __init__(self):
        """初始化盘中执行器"""
        logger.info("-" * 60)
        logger.info(f"  盘中执行器初始化...")

        self.today = get_trading_date()
        self.signal_file = get_signal_file()
        self.order_file = get_order_file()

        # 读取订单文件
        if self.order_file.exists():
            with open(self.order_file, "r", encoding="utf-8") as f:
                self.order_data = json.load(f)
        else:
            self.order_data = {"orders_pre_market": [], "orders_intra_day": []}

        logger.info("✅ 盘中执行器初始化完成")

    def execute(self):
        """
        外部调用接口 - 整个执行过程只连接一次 IBKR
        """

        config = load_config()
        client = IBKRClient(config)

        signal_data = None

        try:
            conn_result = client.connect()
            if not conn_result.success:
                raise Exception(f"连接失败: {conn_result.error_message}")

            logger.debug("IBKR 已连接，开始处理盘中订单...")

            # 读取信号文件
            if self.signal_file.exists():
                with open(self.signal_file, "r", encoding="utf-8") as f:
                    signal_data = json.load(f)

                signals = signal_data.get("signals_intra_day", [])

                unprocessed_signals = [
                    s for s in signals if not s.get("processed", False)
                ]
                logger.info(
                    f"今日有{len(signals)}个信号，{len(unprocessed_signals)}个未处理"
                )

                if unprocessed_signals:
                    process_signals(client, unprocessed_signals, self.order_data["orders_intra_day"])

            # 更新订单文件
            with open(self.order_file, "w", encoding="utf-8") as f:
                json.dump(self.order_data, f, indent=2, ensure_ascii=False)
            logger.debug(f"更新订单文件: {self.order_file}")

            # 更新信号文件
            if signal_data is not None:
                with open(self.signal_file, "w", encoding="utf-8") as f:
                    json.dump(signal_data, f, indent=2, ensure_ascii=False)
                logger.debug(f"更新信号文件: {self.signal_file}")

            client.disconnect()
            logger.debug("IBKR 已断开连接")

        except Exception as e:
            logger.error(f"执行过程异常: {e}")
            try:
                client.disconnect()
            except:
                pass
            raise


def execute():
    """外部调用接口 - 供 ibclient.py 使用"""
    from src.core.paths import set_data_mode, resolve_data_mode
    config = load_config()
    set_data_mode(resolve_data_mode(config.gateway.account_id or ""))
    executor = IntraDayExecutor()
    executor.execute()


# 测试代码
if __name__ == "__main__":
    try:
        execute()
        print("盘中处理完成")
    except Exception as e:
        print(f"盘中处理失败: {e}")
        import traceback

        traceback.print_exc()
