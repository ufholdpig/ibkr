#!/usr/bin/env python3
"""
IBKR All-in-One CLI Client

统一调用 IBKR 系统所有功能模块

用法:
    python skills/ibclient-all-in-one/ibclient.py <command> [options]

    命令:
         gateway             检查 IB Gateway 连通性
         account             获取账户信息
         get-opened-order    获取活跃订单
         get-completed-order 获取已完成订单 (仅今天)
         get-executed-order  获取成交记录
         signal              生成交易信号（自动判断盘前/盘中）
         pre-market          执行盘前交易
         intra-day           执行盘中交易
         post-market         生成盘后交易报告
         watch [symbol]      启动 Watch 守护进程（默认从 config 读取多标的）
         watch --on          唤醒 Watch 守护进程（SIGUSR1）
         watch --off         休眠 Watch 守护进程（SIGUSR2）
         strategy-list                列出策略变更（含已审批和待审批）
         strategy-approve <item_id>   批准策略变更
         strategy-reject <item_id>    拒绝策略变更
"""

import argparse
import json
import logging
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 加载 Hermes .env 配置（微信通知开关、限流参数等）
# 兜底机制: ~/.hermes/profiles/ibkr/.env > ~/.hermes/.env
# 先加载基础 ~/.hermes/.env，再用 ibkr profile 覆盖
_HERMES_AGENT = Path.home() / '.hermes' / 'hermes-agent'
if str(_HERMES_AGENT) not in sys.path:
    sys.path.insert(0, str(_HERMES_AGENT))
try:
    from hermes_cli.env_loader import load_hermes_dotenv
    load_hermes_dotenv(hermes_home=str(Path.home() / '.hermes'))
    profile_env = Path.home() / '.hermes' / 'profiles' / 'ibkr' / '.env'
    if profile_env.exists():
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=profile_env, override=True)
except ImportError:
    pass

from src.core.client import IBKRClient
from config.config import load_config

logger = logging.getLogger("ibclient")


def cmd_gateway(args):
    """检查 IB Gateway 连通性"""
    logging.getLogger("src.core.client").setLevel(logging.CRITICAL)
    logging.getLogger("ibapi").setLevel(logging.CRITICAL)

    logger.info("🔍 检查 IB Gateway 连通性...")

    config = load_config()
    client = IBKRClient(config)
    result = client.connect()

    if result.success:
        logger.info(f"✅ 连接成功")
        logger.info(f"   Host: {result.host}:{result.port}")
        logger.info(f"   Client ID: {result.client_id}")
        client.disconnect()
        return 0
    else:
        logger.info(f"❌ 连接失败: {result.error_message}")
        return 1


def cmd_account(args):
    """获取账户信息"""
    from src.core.account import logger as acct_logger

    acct_logger.info(f"📊 获取账户信息...")

    config = load_config()
    if args.account_id:
        config.gateway.account_id = args.account_id
    client = IBKRClient(config)

    try:
        result = client.connect()
        if not result.success:
            acct_logger.info(f"❌ 连接失败: {result.error_message}")
            return 1

        account_info = client.get_account_info(timeout=args.timeout)

        positions_lines = ""
        if account_info.positions:
            positions_lines = "\n  持仓明细:\n"
            for p in account_info.positions:
                side = "多头" if p.quantity > 0 else "空头" if p.quantity < 0 else ""
                positions_lines += (
                    f"    {p.symbol:<6} {side} {abs(p.quantity):>8.2f}股  "
                    f"均价 ${p.average_cost:>8.2f}  市值 ${p.market_value:>8.2f}  "
                    f"浮动盈亏 ${p.unrealized_pnl:>8.2f}  ({p.exchange}/{p.currency})\n"
                )
        else:
            positions_lines = "\n  持仓明细: 无持仓\n"

        acct_logger.info(f"""
=== IBKR 账户摘要 ===

  账户 ID: {account_info.account_id}
  现金余额: ${account_info.cash_balance:,.2f}
  购买力: ${account_info.buying_power:,.2f}
  净流动资产: ${account_info.net_liquidation:,.2f}
  未实现盈亏: ${account_info.unrealized_pnl:,.2f}
  已实现盈亏: ${account_info.realized_pnl:,.2f}
  货币: {account_info.currency}{positions_lines}""")

        client.disconnect()
        return 0

    except Exception as e:
        acct_logger.info(f"❌ 错误: {e}")
        return 1


def cmd_order(args):
    """订单相关命令"""
    if args.subcommand == "get-opened":
        return cmd_get_opened_orders(args)
    elif args.subcommand == "get-completed":
        return cmd_get_completed_orders(args)
    elif args.subcommand == "get-executions":
        return cmd_get_executions(args)
    else:
        logger.info(f"❌ 未知子命令: {args.subcommand}")
        return 1


def cmd_get_opened_orders(args):
    """获取活跃订单"""
    from src.trading.get_order import get_opened_orders, logger as order_logger

    # 忽略连接状态警告信息：IBKR 连接已关闭
    logging.getLogger("src.core.client").setLevel(logging.ERROR)
    logging.getLogger("ibapi").setLevel(logging.CRITICAL)

    order_logger.info(f"📋 获取活跃订单...")

    config = load_config()
    client = IBKRClient(config)

    try:
        result = client.connect()
        if not result.success:
            order_logger.info(f"❌ 连接失败: {result.error_message}")
            return 1

        orders = get_opened_orders(client, timeout=args.timeout)
        client.disconnect()

        if not orders:
            order_logger.info("📭 无活跃订单")
            return 0

        orders.sort(key=lambda o: o.perm_id)
        order_logger.info(f"{'PermID':<12} {'Symbol':<8} {'Action':<6} {'Qty':<6} {'Filled':<8} {'Status':<15} {'Type':<6}")
        order_logger.info("-" * 65)
        for o in orders:
            order_logger.info(f"{o.perm_id:<12} {o.symbol:<8} {o.action:<6} {o.quantity:<6} {o.filled_qty:<8} {o.status:<15} {o.order_type:<6}")
        order_logger.info(f"共 {len(orders)} 个活跃订单")
        return 0

    except Exception as e:
        logger.info(f"❌ 错误: {e}")
        return 1


def cmd_get_completed_orders(args):
    """获取已完成订单 (今天)
    ...
    """
    # 由于 IBKR 限制，不执行请求，不输出结果
    logger.info("⚠️ 由于 IBKR 限制，reqCompletedOrders 目前不可用")
    logger.info("   建议使用: python ibclient.py get-executed-orders 获取成交记录")
    return 0


def cmd_get_executions(args):
    """获取成交记录"""
    from src.trading.get_order import get_executed_orders, logger as exec_logger
    from datetime import date

    query_date = args.date or date.today().strftime("%Y-%m-%d")
    exec_logger.info(f"📋 获取 {query_date} 的成交记录...")

    config = load_config()
    client = IBKRClient(config)

    try:
        result = client.connect()
        if not result.success:
            exec_logger.info(f"❌ 连接失败: {result.error_message}")
            return 1

        executions = get_executed_orders(client, query_date, timeout=args.timeout)
        client.disconnect()

        exec_logger.info(f"成交记录 ({query_date}) — 共 {len(executions)} 条")
        for e in executions:
            side_cn = "买入" if e.side == "BOT" else "卖出" if e.side == "SLD" else e.side
            exec_logger.info(f"  {e.exec_time} | {e.symbol:<6} {side_cn} {int(e.shares)}股 @ {e.price:.2f} | PermID={e.perm_id} | {e.exchange}")
        return 0

    except Exception as e:
        exec_logger.info(f"❌ 错误: {e}")
        return 1


def cmd_signal(args):
    """策略引擎信号生成 - 调用 signal.py 接口"""
    from src.core.signal import SignalGenerator
    from src.core.paths import get_current_et_time

    et = get_current_et_time()
    before_open = et.hour < 9 or (et.hour == 9 and et.minute < 30)
    after_close = et.hour >= 16
    if after_close or before_open:
        model = "pre-market"
    else:
        model = "intra-day"

    strategy_name = args.strategy or "all"

    try:
        generator = SignalGenerator()
        generator.generate_signals(strategy_name=strategy_name, signal_type=model, max_signals=2)
        return 0

    except Exception as e:
        traceback.print_exc()
        return 1


def cmd_pre_market(args):
    """执行盘前模块 - 调用 pre_market.execute()"""
    from src.trading.pre_market import execute

    try:
        execute()
        return 0

    except Exception as e:
        traceback.print_exc()
        return 1


def cmd_intra_day(args):
    """执行盘中模块 - 调用 intra_day.execute()"""
    from src.trading.intra_day import execute

    try:
        execute()
        return 0

    except Exception as e:
        traceback.print_exc()
        return 1


def cmd_post_market(args):
    """盘后报告"""
    from src.trading.post_market import execute

    success = execute(date=args.date, account=args.account)
    return 0 if success else 1


def cmd_watch(args):
    """Watch 守护进程 - yfinance 实时监控"""
    if args.on or args.off:
        import signal
        from src.trading.watch_daemon import send_signal

        sig = signal.SIGUSR1 if args.on else signal.SIGUSR2
        action = "唤醒" if args.on else "休眠"
        if send_signal(sig):
            logger.info(f"Watch daemon {action} 成功")
            return 0
        return 1

    from src.trading.watch_daemon import run_watch

    try:
        run_watch(args.symbol)  # None → 从 config 读取 symbols
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        traceback.print_exc()
        return 1


def cmd_order_list(args):
    """列出待审批订单"""
    from src.core.order_approval import OrderApprovalQueue

    queue = OrderApprovalQueue()
    summary = queue.get_pending_summary()
    if summary:
        logger.info(summary)
    else:
        logger.info("✅ 暂无待审批订单")
    return 0


def write_signal_and_execute(signals, detail: str = ""):
    """写入一个或多个信号到 signal_YYYYMMDD.json 并调用 pre-market/intra-day 提交"""
    from datetime import datetime
    from src.core.paths import get_signal_file, get_current_et_time
    from src.trading.pre_market import execute as pre_market_execute
    from src.trading.intra_day import execute as intra_day_execute

    if isinstance(signals, dict):
        signals = [signals]

    if not signals:
        return

    signal_file = get_signal_file()
    et = get_current_et_time()
    before_open = et.hour < 9 or (et.hour == 9 and et.minute < 30)
    after_close = et.hour >= 16
    section = "signals_pre_market" if (before_open or after_close) else "signals_intra_day"

    if signal_file.exists():
        with open(signal_file) as f:
            signal_data = json.load(f)
    else:
        signal_data = {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "signals_pre_market": [],
            "signals_intra_day": [],
        }

    for signal in signals:
        signal_data.setdefault(section, []).append(dict(signal, processed=False))

    with open(signal_file, "w") as f:
        json.dump(signal_data, f, indent=2, ensure_ascii=False)

    n = len(signals)
    extra = f" | {detail}" if detail else ""
    logger.info(f"📋 {n}个信号已追加到 {section}{extra}，正在提交...")

    try:
        if before_open or after_close:
            pre_market_execute()
        else:
            intra_day_execute()
    except Exception as e:
        logger.info(f"⚠️ 执行异常（信号已排队）: {e}")


def cmd_order_approve(args):
    """批准待审批订单 — 追加到信号文件对应 section，由 pre-market/intra-day 统一提交"""
    from src.core.order_approval import OrderApprovalQueue

    queue = OrderApprovalQueue()

    approved = []
    for item_id in args.item_id:
        signal_data = queue.approve(item_id)
        if not signal_data:
            logger.info(f"❌ 未找到待审批订单: {item_id}")
            continue
        logger.info(f"✅ 已批准: {signal_data.get('symbol','')} {signal_data.get('action','')} x{signal_data.get('quantity',0)}")
        approved.append(signal_data)

    if not approved:
        return 1

    write_signal_and_execute(approved)
    return 0


def cmd_order_reject(args):
    """拒绝待审批订单"""
    from src.core.order_approval import OrderApprovalQueue

    queue = OrderApprovalQueue()
    if queue.reject(args.item_id):
        logger.info(f"✅ 已拒绝: {args.item_id}")
        return 0
    logger.info(f"❌ 拒绝失败 (不存在或已处理): {args.item_id}")
    return 1


def cmd_order_buy(args):
    """手动买入 — 直接生成 BUY 信号进入审批队列"""
    return submit_manual_order(args.symbol, args.quantity, "BUY")


def cmd_order_sell(args):
    """手动卖出 — 直接生成 SELL 信号进入审批队列"""
    return submit_manual_order(args.symbol, args.quantity, "SELL")


def submit_manual_order(symbol: str, quantity: int, action: str) -> int:
    from src.core.order_approval import OrderApprovalQueue
    from config.config import load_config
    from datetime import datetime

    symbol = symbol.upper()

    # 获取当前价格
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
        else:
            price = 0.0
    except Exception as e:
        logger.info(f"⚠️ 获取价格失败: {e}")
        price = 0.0

    signal = {
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "strategy_name": f"手动{action}",
        "strategy_id": f"MANUAL_{action}_{symbol}",
        "target_price": price,
        "reason": f"用户手动下单: {action} {symbol} x{quantity} @ ${price:.2f}",
        "source": "manual",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    config = load_config()
    approval_required = config.approval_required

    if approval_required:
        queue = OrderApprovalQueue()
        item_id = queue.submit(signal)
        action_cn = "买入" if action == "BUY" else "卖出"
        logger.info(f"✅ 订单已提交审批队列")
        logger.info(f"   订单 ID: {item_id}")
        logger.info(f"   标的: {symbol} {action_cn} {quantity}股 @ ${price:.2f}")
    else:
        action_cn = "买入" if action == "BUY" else "卖出"
        write_signal_and_execute(signal, f"{symbol} {action_cn} {quantity}股 @ ${price:.2f}")

    return 0


def cmd_strategy_approve(args):
    """批准策略进化审批项 (Phase 3 D32)"""
    logger.info("~~~~~~~~~~~~~~~~ ibclient strategy_approve")
    from src.core.learning import ApprovalQueue
    from src.core.paths import get_path, ensure_dir

    data_dir = ensure_dir(path=get_path("data") / "learning")
    queue = ApprovalQueue(data_dir=data_dir)
    if queue.approve(args.item_id):
        logger.info(f"✅ 已批准: {args.item_id}")
        return 0
    logger.info(f"❌ 审批失败 (不存在或已处理): {args.item_id}")
    return 1


def cmd_strategy_reject(args):
    """拒绝策略进化审批项 (Phase 3 D32)"""
    logger.info("~~~~~~~~~~~~~~~~ ibclient strategy_reject")
    from src.core.learning import ApprovalQueue
    from src.core.paths import get_path, ensure_dir

    data_dir = ensure_dir(path=get_path("data") / "learning")
    queue = ApprovalQueue(data_dir=data_dir)
    if queue.reject(args.item_id):
        logger.info(f"✅ 已拒绝: {args.item_id}")
        return 0
    logger.info(f"❌ 拒绝失败 (不存在或已处理): {args.item_id}")
    return 1


def cmd_strategy_list(args):
    """列出待审批项（策略变更 + 待审批订单）"""
    from src.core.learning import ApprovalQueue
    from src.core.paths import get_path, ensure_dir
    from src.core.order_approval import OrderApprovalQueue

    # 策略变更待审批
    data_dir = ensure_dir(path=get_path("data") / "learning")
    strategy_queue = ApprovalQueue(data_dir=data_dir)
    strategy_summary = strategy_queue.get_pending_summary()

    # 待审批订单
    order_queue = OrderApprovalQueue()
    order_summary = order_queue.get_pending_summary()

    # 合并输出
    if strategy_summary:
        logger.info(strategy_summary)
    if order_summary:
        if strategy_summary:
            pass  # 不需要分隔符，logger 自带换行
        logger.info(order_summary)
    if not strategy_summary and not order_summary:
        logger.info("✅ 暂无待审批项")






def main():
    from src.core.paths import set_data_mode, resolve_data_mode
    from config.config import load_config
    cfg = load_config()
    set_data_mode(resolve_data_mode(cfg.gateway.account_id or ""))

    parser = argparse.ArgumentParser(
        description="IBKR All-in-One CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令示例:
   %(prog)s gateway                          # 检查连接
   %(prog)s account                          # 获取账户信息（从 config 读取 account_id）
   %(prog)s account U25896526                # 获取指定账户信息（覆盖 config）
   %(prog)s get-opened-orders                # 获取活跃订单
   %(prog)s get-completed-orders             # 获取已完成订单
   %(prog)s get-executed-orders              # 获取今日成交
   %(prog)s signal                           # 生成交易信号（自动判断盘前/盘中）
   %(prog)s signal --strategy FORCE_BUY      # 生成指定策略的信号
   %(prog)s pre-market                       # 执行盘前全流程
   %(prog)s intra-day                        # 执行盘中全流程
   %(prog)s post-market                      # 生成盘后报告
   %(prog)s watch F                          # 启动 Watch 守护进程
   %(prog)s watch --on                       # 唤醒 Watch 守护进程
   %(prog)s watch --off                      # 休眠 Watch 守护进程
   %(prog)s order-list                       # 列出待审批订单
   %(prog)s order-approve <id> [<id> ...]    # 批准订单并提交 IBKR（支持多个）
   %(prog)s order-reject <id>                # 拒绝订单
   %(prog)s order-buy AAPL 10                # 手动买入 10 股 AAPL，进入审批队列
   %(prog)s order-sell F 5                   # 手动卖出 5 股 F，进入审批队列
   %(prog)s strategy-list                    # 列出策略变更（含已审批和待审批）
   %(prog)s strategy-approve <item_id>       # 批准策略变更
   %(prog)s strategy-reject <item_id>        # 拒绝策略变更
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # gateway
    subparsers.add_parser("gateway", help="检查 IB Gateway 连通性").set_defaults(
        func=cmd_gateway
    )

    # account
    p_account = subparsers.add_parser("account", help="获取账户信息")
    p_account.add_argument("account_id", nargs="?", type=str, default="", help="账户 ID（可选，覆盖 config 中的 account_id）")
    p_account.add_argument("--timeout", type=int, default=15, help="超时秒数")
    p_account.set_defaults(func=cmd_account)

    # 订单相关命令 (独立命令)
    p_opened = subparsers.add_parser("get-opened-orders", help="获取活跃订单")
    p_opened.add_argument("--timeout", type=int, default=10, help="超时秒数")
    p_opened.set_defaults(func=cmd_get_opened_orders)

    p_completed = subparsers.add_parser("get-completed-orders", help="获取已完成订单 (仅今天)")
    p_completed.add_argument("--timeout", type=int, default=30, help="超时秒数")
    p_completed.set_defaults(func=cmd_get_completed_orders)

    p_exec = subparsers.add_parser("get-executed-orders", help="获取成交记录")
    p_exec.add_argument("--date", type=str, default="", help="日期 (YYYY-MM-DD)")
    p_exec.add_argument("--timeout", type=int, default=30, help="超时秒数")
    p_exec.set_defaults(func=cmd_get_executions)

    # signal
    p_signal = subparsers.add_parser("signal", help="生成交易信号（自动判断盘前/盘中）")
    p_signal.add_argument("--strategy", type=str, default="", help="策略模板名称")
    p_signal.add_argument("--timeout", type=int, default=30, help="超时秒数")
    p_signal.set_defaults(func=cmd_signal)

    # pre-market
    p_premarket = subparsers.add_parser("pre-market", help="执行盘前全流程")
    p_premarket.add_argument("--signal", type=str, default="", help="信号文件路径 (可选)")
    p_premarket.add_argument("--timeout", type=int, default=30, help="超时秒数")
    p_premarket.set_defaults(func=cmd_pre_market)

    # intra-day
    p_intraday = subparsers.add_parser("intra-day", help="执行盘中交易")
    p_intraday.add_argument("--order", type=str, default="", help="订单文件路径")
    p_intraday.add_argument("--signal", type=str, default="", help="信号文件路径")
    p_intraday.add_argument("--dry-run", action="store_true", help="模拟执行")
    p_intraday.add_argument("--timeout", type=int, default=30, help="超时秒数")
    p_intraday.set_defaults(func=cmd_intra_day)

    # post-market
    p_postmarket = subparsers.add_parser("post-market", help="生成盘后报告")
    p_postmarket.add_argument("--date", type=str, default="", help="日期 (YYYYMMDD)，默认为今天")
    p_postmarket.add_argument("--account", type=str, default="", help="账户 ID")
    p_postmarket.add_argument("--timeout", type=int, default=30, help="超时秒数")
    p_postmarket.set_defaults(func=cmd_post_market)

    # order-list
    subparsers.add_parser("order-list", help="列出待审批订单").set_defaults(func=cmd_order_list)

    # order-approve (一键审批+提交)
    p_order_approve = subparsers.add_parser("order-approve", help="批准待审批订单并提交 IBKR")
    p_order_approve.add_argument("item_id", type=str, nargs="+", help="订单审批项ID（支持多个）")
    p_order_approve.set_defaults(func=cmd_order_approve)

    # order-reject
    p_order_reject = subparsers.add_parser("order-reject", help="拒绝待审批订单")
    p_order_reject.add_argument("item_id", type=str, help="订单审批项ID")
    p_order_reject.set_defaults(func=cmd_order_reject)

    # order-buy / order-sell (手动下单进审批队列)
    p_order_buy = subparsers.add_parser("order-buy", help="手动买入，订单进入审批队列")
    p_order_buy.add_argument("symbol", type=str, help="股票代码")
    p_order_buy.add_argument("quantity", type=int, help="股数")
    p_order_buy.set_defaults(func=cmd_order_buy)

    p_order_sell = subparsers.add_parser("order-sell", help="手动卖出，订单进入审批队列")
    p_order_sell.add_argument("symbol", type=str, help="股票代码")
    p_order_sell.add_argument("quantity", type=int, help="股数")
    p_order_sell.set_defaults(func=cmd_order_sell)

    # strategy-approve
    p_strategy_approve = subparsers.add_parser("strategy-approve", help="批准策略变更")
    p_strategy_approve.add_argument("item_id", type=str, help="审批项ID")
    p_strategy_approve.set_defaults(func=cmd_strategy_approve)

    # strategy-reject
    p_strategy_reject = subparsers.add_parser("strategy-reject", help="拒绝策略变更")
    p_strategy_reject.add_argument("item_id", type=str, help="审批项ID")
    p_strategy_reject.set_defaults(func=cmd_strategy_reject)

    # strategy-list
    p_strategy_list = subparsers.add_parser("strategy-list", help="列出策略变更（含已审批和待审批）")
    p_strategy_list.set_defaults(func=cmd_strategy_list)

    # watch
    p_watch = subparsers.add_parser("watch", help="Watch 守护进程：启动 / 唤醒 / 休眠")
    p_watch.add_argument("symbol", nargs="?", type=str, help="标的代码 (如 F)，省略时从 config 读取多标的")
    group = p_watch.add_mutually_exclusive_group()
    group.add_argument("--on", action="store_true", help="唤醒 daemon (SIGUSR1)")
    group.add_argument("--off", action="store_true", help="休眠 daemon (SIGUSR2)")
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    cmd = args.command or ''
    parts = [cmd]
    skip = {'command', 'func', 'timeout', 'dry_run'}
    for k, v in vars(args).items():
        if k in skip:
            continue
        if v is not None and v is not False and v != '':
            if isinstance(v, list):
                parts.extend(str(x) for x in v)
            else:
                parts.append(str(v))
    logger.info(f"=============================================== {' '.join(parts)}")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
