"""
IBKR 统一策略引擎接口
版本：v3.0
日期：2026-05-21
作者：Hermes Agent
描述：提供统一的策略引擎接口，支持新的信号文件格式
"""

import logging
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from src.core.paths import get_signal_file

from src.core.strategy import StrategyFactory, TradingSignal
from src.core.client import IBKRClient
from config.config import load_config

logger = logging.getLogger(__name__)


def convert_signal_to_dict(signal) -> Dict[str, Any]:
    """将 TradingSignal 对象转换为信号文件所需的 dict 格式。

    公有函数，供 watch daemon / ibclient.py 统一调用。
    """
    action_value = signal.action
    if hasattr(signal.action, "value"):
        action_value = signal.action.value

    return {
        "strategy_name": signal.strategy_name,
        "strategy_id": getattr(signal, "strategy_id", ""),
        "signal_id": getattr(signal, "signal_id", ""),
        "symbol": signal.symbol,
        "action": action_value,
        "quantity": signal.quantity,
        "target_price": signal.target_price,
        "reason": signal.reason,
        "confidence": getattr(signal, "confidence", 0.0),
        "weight": getattr(signal, "weight", 1.0),
        "priority": getattr(signal, "priority", 0),
        "signal_price": getattr(signal, "signal_price", 0.0),
        "market_regime": getattr(signal, "market_regime", ""),
        "processed": False,
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }


class SignalGenerator:
    """交易信号生成器"""

    def __init__(self):
        """初始化信号生成器"""
        from src.core.paths import set_data_mode, resolve_data_mode
        config = load_config()
        set_data_mode(resolve_data_mode(config.gateway.account_id or ""))

        self.model = ["pre-market", "intra-day"]

        # 确保目录存在
        self.signal_file = get_signal_file()

        logger.info("-" * 60)
        logger.info(f"✅ 交易信号生成器初始化")

    def _load_signal_file(self):
        """加载现有的信号文件"""
        if self.signal_file.exists():
            with open(self.signal_file, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            return {
                "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "signals_pre_market": [],
                "signals_intra_day": [],
            }

    def _save_signal_file(self, signal_data):
        """保存信号文件"""
        try:
            with open(self.signal_file, "w", encoding="utf-8") as f:
                json.dump(signal_data, f, indent=2, ensure_ascii=False)

            logger.info(f"信号文件: {self.signal_file}")

        except Exception as e:
            logger.error(f"保存信号文件失败 {self.signal_file}: {e}")
            raise

    def _convert_signal_to_dict(self, signal) -> Dict[str, Any]:
        return convert_signal_to_dict(signal)

    def generate_signals(self, strategy_name: str = "all", signal_type: str = "pre-market", max_signals: int = 0) -> None:
        """
        信号生成核心逻辑

        Args:
            strategy_name: 策略名称，默认 "all" 表示所有策略
            signal_type: 信号类型，"pre-market" 或 "intra-day"
        """
        logger.info(f"生成 {signal_type} 信号 (strategy={strategy_name})...")

        # 加载配置和客户端
        config = load_config()
        client = IBKRClient(config)

        try:
            # 连接IBKR
            result = client.connect()
            if not result.success:
                raise Exception(f"连接失败: {result.error_message}")

            # 获取账户信息（用于 is_paper 判断）
            account_info = client.get_account_info(timeout=30)

            # 加载策略工厂（per-template 结构）
            factory = StrategyFactory(
                client=client,
                market_data_source=config.market_data_source,
                template_dir=config.watch.template_dir,
                watch_templates=config.watch.templates,
            )

            # 过滤策略（如果指定了具体策略名）
            if strategy_name and strategy_name != "all":
                filtered = [
                    s for s in factory.yaml_strategies if s.name == strategy_name
                ]
                if not filtered:
                    raise Exception(f"策略不存在: {strategy_name}")
                factory.yaml_strategies = filtered

            # 生成信号（工厂按模板 signal_factors 自动获取所需数据）
            signals = factory.analyze(
                is_paper=account_info.is_paper
            )

            # 断开IBKR
            client.disconnect()

            # 加载现有信号
            signal_data = self._load_signal_file()

            # 转换信号格式并去重
            count = 0
            signal_key = f"signals_{signal_type.replace('-', '_')}"

            for signal in signals:
                signal_dict = self._convert_signal_to_dict(signal)
                dup = any(
                    s.get("symbol") == signal_dict["symbol"]
                    and s.get("action") == signal_dict["action"]
                    and s.get("quantity") == signal_dict["quantity"]
                    for s in signal_data[signal_key]
                )
                if dup:
                    logger.info("跳过重复信号: %s %s x%s 已存在",
                                signal_dict["symbol"], signal_dict["action"], signal_dict["quantity"])
                    continue
                signal_data[signal_key].append(signal_dict)
                count += 1
                if max_signals > 0 and count >= max_signals:
                    break

            logger.info(f"{count} 个新 {signal_type} 信号生成成功" + (f" (上限={max_signals})" if max_signals > 0 else ""))

            # 更新现有信号
            self._save_signal_file(signal_data)

        except Exception as e:
            logger.error(f"生成{signal_type}信号异常: {e}")
            raise


if __name__ == "__main__":
    # 测试代码
    import sys

    if len(sys.argv) < 2:
        print("Usage: python signal.py <pre-market|intra-day> [strategy_name]")
        sys.exit(1)

    model = sys.argv[1]
    strategy_name = sys.argv[2] if len(sys.argv) > 2 else "all"

    try:
        generator = SignalGenerator()
        generator.generate_signals(strategy_name=strategy_name, signal_type=model, max_signals=2)
        sys.exit(0)

    except Exception as e:
        import traceback

        traceback.print_exc()
        sys.exit(1)
