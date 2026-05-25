"""
持仓报告生成器 (Portfolio Report Generator)

功能：
1. 实时连接 IBKR 获取最新持仓数据
2. 生成人类可读 (Human-readable) 的持仓报告
3. 支持多种输出格式：文本 (Text)、Markdown、JSON
4. 适用于：随机查询、日报、周报、月报

使用场景：
- 微信即时查询：`python -m src.utils.portfolio_report --format text --channel weixin`
- 生成日报：`python -m src.utils.portfolio_report --format markdown --output reports/daily_20260420.md`
"""

import os
import sys
import random
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import logging

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.client import IBKRClient
from src.core.models import AccountInfo, Position
from config.config import GatewayConfig

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


class PortfolioReportGenerator:
    """持仓报告生成器"""

    def __init__(self, host: str = "127.0.0.1", port: int = 4001, client_id: Optional[int] = None):
        """
        初始化报告生成器
        
        Args:
            host: IB Gateway 主机地址
            port: IB Gateway 端口 (4001=实盘，4002=模拟)
            client_id: 客户端 ID (随机生成如果为 None)
        """
        self.host = host
        self.port = port
        self.client_id = client_id
        self.client: Optional[IBKRClient] = None
        self.config: Optional[GatewayConfig] = None

    def connect(self) -> bool:
        """连接 IBKR"""
        try:
            # 创建配置对象
            self.config = GatewayConfig(
                host=self.host,
                port=self.port,
                client_id=self.client_id if self.client_id else random.randint(100, 999),
                timeout=15,
                max_retries=3
            )
            
            logger.info(f"正在连接 IB Gateway: {self.host}:{self.port} (Client ID: {self.config.client_id})")
            self.client = IBKRClient(config=self.config)
            result = self.client.connect()
            if result.success:
                logger.info("✅ 连接成功")
                return True
            else:
                logger.error(f"❌ 连接失败：{result.error_message}")
                return False
        except Exception as e:
            logger.error(f"❌ 连接异常：{e}")
            return False

    def fetch_portfolio(self) -> Optional[AccountInfo]:
        """获取持仓数据"""
        if not self.client:
            logger.error("❌ 未连接 IBKR")
            return None
        
        try:
            logger.info("正在获取账户信息...")
            account_info = self.client.get_account_info()
            
            if not account_info or not account_info.account_id:
                logger.error("❌ 获取账户信息失败")
                return None
            
            logger.info(f"✅ 获取成功：账户 {account_info.account_id}, 持仓数 {account_info.position_count}")
            return account_info
        except Exception as e:
            logger.error(f"❌ 获取持仓数据异常：{e}")
            return None

    def generate_text_report(self, account_info: AccountInfo) -> str:
        """生成纯文本报告 (适合微信推送)"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        lines = [
            "=" * 50,
            f"📊 持仓报告 - {now}",
            "=" * 50,
            f"账户 ID: {account_info.account_id}",
            f"账户类型: {account_info.account_type}",
            f"货币: {account_info.currency}",
            "",
            "💰 资产概览:",
            f"  净值 (Net Liquidation): ${account_info.net_liquidation:,.2f}",
            f"  现金 (Cash): ${account_info.total_cash:,.2f}",
            f"  购买力 (Buying Power): ${account_info.buying_power:,.2f}",
            "",
            f"📈 持仓明细 ({account_info.position_count} 个):",
            "-" * 50,
        ]
        
        if account_info.positions:
            total_market_value = 0
            for pos in account_info.positions:
                market_value = pos.market_value
                total_market_value += market_value
                pnl = (pos.market_price - pos.avg_cost) * pos.quantity if pos.market_price and pos.avg_cost else 0
                pnl_pct = ((pos.market_price - pos.avg_cost) / pos.avg_cost * 100) if pos.avg_cost and pos.avg_cost != 0 else 0
                
                lines.append(f"  {pos.symbol:8} | 数量：{pos.quantity:8.2f} | 成本：${pos.avg_cost:10.2f} | 现价：${pos.market_price:10.2f}")
                lines.append(f"           | 市值：${market_value:12,.2f} | 盈亏：${pnl:10,.2f} ({pnl_pct:+.2f}%)")
                lines.append("-" * 50)
            
            lines.append(f"  {'持仓总市值':>20}: ${total_market_value:,.2f}")
        else:
            lines.append("  当前无持仓")
            lines.append("-" * 50)
        
        lines.append("")
        lines.append("🔒 风险提示：数据来自 IBKR 实时接口，仅供参考。")
        lines.append("=" * 50)
        
        return "\n".join(lines)

    def generate_markdown_report(self, account_info: AccountInfo) -> str:
        """生成 Markdown 报告 (适合日报/周报)"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        lines = [
            f"# 📊 持仓报告",
            f"**生成时间**: {now}",
            f"**账户 ID**: {account_info.account_id} ({account_info.account_type.get('value', account_info.account_type) if isinstance(account_info.account_type, dict) else account_info.account_type})",
            "",
            "## 💰 资产概览",
            "| 指标 | 金额 (USD) |",
            "|------|------------|",
            f"| 净值 (Net Liquidation) | ${account_info.net_liquidation:,.2f} |",
            f"| 现金 (Cash) | ${account_info.total_cash:,.2f} |",
            f"| 购买力 (Buying Power) | ${account_info.buying_power:,.2f} |",
            "",
            f"## 📈 持仓明细 ({account_info.position_count} 个)",
            "| 代码 | 数量 | 平均成本 | 当前价格 | 市值 | 盈亏金额 | 盈亏比例 |",
            "|------|------|----------|----------|------|----------|----------|",
        ]
        
        total_market_value = 0
        if account_info.positions:
            for pos in account_info.positions:
                market_value = pos.market_value
                total_market_value += market_value
                pnl = (pos.market_price - pos.avg_cost) * pos.quantity if pos.market_price and pos.avg_cost else 0
                pnl_pct = ((pos.market_price - pos.avg_cost) / pos.avg_cost * 100) if pos.avg_cost and pos.avg_cost != 0 else 0
                
                lines.append(
                    f"| {pos.symbol} | {pos.quantity:.2f} | ${pos.avg_cost:,.2f} | "
                    f"${pos.market_price:,.2f} | ${market_value:,.2f} | "
                    f"${pnl:,.2f} | {pnl_pct:+.2f}% |"
                )
            
            lines.append("")
            lines.append(f"**持仓总市值**: ${total_market_value:,.2f}")
        else:
            lines.append("| 无持仓 | - | - | - | - | - | - |")
        
        lines.extend([
            "",
            "---",
            f"*报告由 Hermes IBKR 系统自动生成*",
        ])
        
        return "\n".join(lines)

    def generate_report(self, account_info: AccountInfo, report_type: str = "random_query") -> str:
        """
        生成报告 (默认 Markdown 格式)
        
        Args:
            account_info: 账户信息对象
            report_type: 报告类型 (random_query, daily_report, weekly_report, monthly_report)
        
        Returns:
            Markdown 格式的报告内容
        """
        return self.generate_markdown_report(account_info)

    def save_report(self, content: str, report_type: str = "random_query", output_path: str = None) -> str:
        """
        保存报告到文件
        
        Args:
            content: 报告内容
            report_type: 报告类型 (用于生成文件名)
            output_path: 自定义输出路径 (如果为 None，则自动生成)
        
        Returns:
            保存的文件路径
        """
        if output_path:
            final_path = output_path
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{report_type}_{timestamp}.md"
            final_path = os.path.join("reports", filename)
        
        try:
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            with open(final_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"✅ 报告已保存：{final_path}")
            return final_path
        except Exception as e:
            logger.error(f"❌ 保存报告失败：{e}")
            raise

    def close(self):
        """关闭连接"""
        if self.client:
            self.client.disconnect()
            logger.info("连接已关闭")


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="IBKR 持仓报告生成器")
    parser.add_argument("--host", default="127.0.0.1", help="IB Gateway 主机")
    parser.add_argument("--port", type=int, default=4001, help="IB Gateway 端口 (4001=实盘，4002=模拟)")
    parser.add_argument("--client-id", type=int, default=None, help="客户端 ID (随机生成如果未指定)")
    parser.add_argument("--type", choices=["random_query", "daily_report", "weekly_report", "monthly_report"], default="random_query", help="报告类型")
    parser.add_argument("--output", type=str, default=None, help="输出文件路径 (默认：reports/{type}_YYYYMMDD_HHMMSS.md)")
    
    args = parser.parse_args()
    
    # 初始化生成器
    generator = PortfolioReportGenerator(host=args.host, port=args.port, client_id=args.client_id)
    
    try:
        # 1. 连接
        if not generator.connect():
            sys.exit(1)
        
        # 2. 获取数据
        account_info = generator.fetch_portfolio()
        if not account_info:
            sys.exit(1)
        
        # 3. 生成报告 (默认 Markdown)
        content = generator.generate_report(account_info, report_type=args.type)
        
        # 4. 保存
        output_path = generator.save_report(content, report_type=args.type, output_path=args.output)
        print(f"\n✅ 报告已生成：{output_path}")
    
    finally:
        generator.close()


if __name__ == "__main__":
    main()
