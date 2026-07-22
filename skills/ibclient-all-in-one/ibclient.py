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
         universe-refresh    刷新候选池（每日盘后调用）
         watch [symbol]      启动 Watch 守护进程（默认从 config 读取多标的）
         watch --on          唤醒 Watch 守护进程（SIGUSR1）
         watch --off         休眠 Watch 守护进程（SIGUSR2）
         strategy-list                列出策略变更（含已审批和待审批）
         strategy-approve <item_id>   批准策略变更
         strategy-reject <item_id>    拒绝策略变更

    universe-refresh 用法:
   %(prog)s universe-refresh                  # 刷新候选池，打印摘要
   %(prog)s universe-refresh --date 20250718 # 指定日期
   %(prog)s universe-refresh --output /path/to/report.json  # 保存 JSON 报告
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

        # 账户基础信息（cash/buying_power/net_liquidation 等）+ 持仓（一次调用获取所有）
        account_info = client.get_account_info(timeout=args.timeout)

        # 持仓含实时价格（通过 MarketDataProvider 计算市值和盈亏）
        # 复用 account_info.positions，避免重复调用 get_account_info（减少一次日志）
        positions = client.get_positions_with_prices(
            _positions=account_info.positions, timeout=args.timeout
        )

        positions_lines = ""
        if positions:
            positions_lines = "\n  持仓明细:\n"
            for p in positions:
                side = "多头" if p.quantity > 0 else "空头" if p.quantity < 0 else ""
                positions_lines += (
                    f"    {p.symbol:<6} {side} {abs(p.quantity):>8.2f}股  "
                    f"均价 ${p.average_cost:>8.2f}  市值 ${p.market_value:>12,.2f}  "
                    f"浮动盈亏 ${p.unrealized_pnl:>12,.2f}  ({p.exchange}/{p.currency})\n"
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


def cmd_universe_refresh(args):
    """刷新候选池（供盘后调用，或独立运行）"""
    from src.trading.universe_selector import create_universe_selector
    from src.core.client import IBKRClient
    from config.config import load_config
    import json
    from typing import Dict

    config = load_config()
    client = IBKRClient(config)

    try:
        result = client.connect()
        if not result.success:
            logger.info(f"❌ 连接失败: {result.error_message}")
            return 1

        # 获取候选池 selector（用于后续 refresh 和 report）
        selector = create_universe_selector()

        # 根据 NYSE 日历判断当前是否在交易时段（9:30-16:00 ET）
        # 盘中（NYSE开市）→ pool_only；盘前/盘后/周末/假日 → full（全量刷新）
        from datetime import datetime
        from src.core.paths import get_current_et_time
        from src.core.market_data import MarketDataProvider
        from src.core.strategy import MarketData
        import exchange_calendars as xc

        et = get_current_et_time()
        try:
            nyse = xc.get_calendar("XNYS")
            is_market_open = bool(nyse.is_open_on_minute(et))
        except Exception:
            is_market_open = False  # 降级：无法确定时默认 full
        execution_scope = "pool_only" if is_market_open else "full"

        # 全量刷新时扫描所有板块龙头股（sectors），盘中只扫描候选池（candidate_pool）
        if execution_scope == "full":
            from config.config import load_strong_accumulation_config
            sa_cfg = load_strong_accumulation_config()
            symbols = sa_cfg.get_candidate_pool_for_scope("full")
            symbols = [s for s in symbols if s not in set(config.universe_selector.blacklist or [])]
        else:
            # scope=pool_only: 盘中仅扫描候选池
            from config.config import load_strong_accumulation_config
            sa_cfg = load_strong_accumulation_config()
            symbols = sa_cfg.get_candidate_pool_for_scope("pool_only")
            if not symbols:
                logger.info("⏭ scope=pool_only，候选池为空，跳过盘中刷新（请先执行 scope=full 填充 top10）")
                return 0

        if not symbols:
            logger.info("❌ 候选池为空，请先执行 universe-refresh (scope=full) 扫描板块龙头股填充 top10")
            return 1
        logger.info(f"📊 获取 {len(symbols)} 只候选标的的市场数据... [scope={execution_scope}]")

        # 使用 MarketDataProvider（有 yfinance fallback）获取历史数据并计算技术指标
        data_source = getattr(config, 'market_data_source', 'yfinance')
        provider = MarketDataProvider(client, data_source=data_source)
        market_data_map: Dict[str, MarketData] = {}
        failed = []

        for i, sym in enumerate(symbols):
            bars = provider.fetch_historical(sym, days=365)
            if not bars or len(bars) < 60:
                msg = f"[{i+1}/{len(symbols)}] ⚠️ {sym}: 历史数据不足（{len(bars) if bars else 0} bars），跳过"
                logger.debug(msg)  # 次要信息 → audit.log
                logger.warning(msg)
                failed.append(sym)
                continue
            ind = provider.compute_indicators(bars)
            md = MarketData(
                symbol=sym,
                price=bars[-1].close,
                volume=bars[-1].volume,
                high_52w=max(b.close for b in bars[-252:]) if len(bars) >= 252 else None,
                low_52w=min(b.close for b in bars[-252:]) if len(bars) >= 252 else None,
                **ind
            )
            market_data_map[sym] = md
            logger.debug(f"[{i+1}/{len(symbols)}] ✅ {sym}: ${bars[-1].close:.2f}")  # 次要信息 → audit.log

        if not market_data_map:
            logger.info("❌ 所有候选标的均无有效数据")
            client.disconnect()
            return 1

        logger.info(f"✅ 获取 {len(market_data_map)} 只标的的技术指标")

        # 刷新前保存旧 top2（用于盘后持仓决策：判断持仓标的是否新入 top2）
        old_top2 = selector.top2
        old_candidate_symbols = [c.symbol for c in selector.candidates]
        logger.info(f"📊 刷新前 top2: {old_top2}")

        logger.info(f"🔄 刷新候选池（top {selector.CAPACITY}），market_data_map 大小: {len(market_data_map)}")
        # scope=full：直接用全量 sectors 刷新（不依赖 candidate_pool 初始化）
        # scope=pool_only：走 selector 内部逻辑（从 candidate_pool 取）
        if execution_scope == "full":
            # 手动传入所有标的，不依赖 selector._candidate_symbols（此时为空的）
            selector.refresh(market_data_map, symbols=symbols)
        else:
            selector.refresh(market_data_map)
        logger.info(f"✅ 候选池刷新完成")

        # ============================================================
        # scope=full 后写回 top 10（同时更新两个配置文件）
        # 注意：即使 candidates 为空（0只通过评审），_candidates_for_save 仍包含得分最高的标的
        # ============================================================
        if execution_scope == "full":
            top_symbols = selector._candidates_for_save
            logger.info(f"📝 scope=full 写回 top {len(top_symbols)}: {top_symbols}")

            # 1. 更新 strong_accumulation.yaml candidate_pool
            from config.config import (load_strong_accumulation_config,
                                       save_strong_accumulation_config)
            sa_cfg = load_strong_accumulation_config()
            sa_cfg.update_candidate_pool(top_symbols)
            save_strong_accumulation_config(sa_cfg)
            logger.info(f"✅ 已写回 strong_accumulation.yaml candidate_pool")

            # 2. 同步 ibkr.yaml watch.templates.strong_accumulation
            import yaml
            from pathlib import Path
            project_root = Path(__file__).resolve().parent.parent.parent
            ibkr_path = project_root / "config" / "ibkr.yaml"
            with open(ibkr_path, "r", encoding="utf-8") as f:
                ibkr_data = yaml.safe_load(f) or {}
            ibkr_data.setdefault("ibkr", {}).setdefault("watch", {}).setdefault("templates", {})["strong_accumulation"] = top_symbols
            with open(ibkr_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(ibkr_data, f, allow_unicode=True, sort_keys=False)
            logger.info(f"✅ 已同步 ibkr.yaml watch.templates.strong_accumulation")

        logger.info(f"📋 获取持仓信息...")
        account_info = client.get_account_info(timeout=30)
        positions = [
            {
                "symbol": p.symbol,
                "quantity": p.quantity,
                "avg_cost": p.average_cost,
                "market_price": market_data_map[p.symbol].price if p.symbol in market_data_map else 0,
            }
            for p in account_info.positions
            if p.quantity > 0 and p.symbol in market_data_map
        ]

        # 评估报告
        report = selector.generate_report(
            report_date=args.date or "",
            positions=positions,
            old_top2=old_top2,
        )

        # 输出结果
        report_dict = report.to_dict()

        if args.output:
            output_path = Path(args.output)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report_dict, f, indent=2, ensure_ascii=False)
            logger.info(f"✅ 报告已保存: {output_path}")

        # 打印摘要
        logger.info(f"\n📊 候选池评估报告 — {report.report_date}")
        logger.info(f"   候选池: {report.candidate_count} 只通过评审")

        if report.candidate_pool:
            logger.info("   TOP5 标的:")
            for i, c in enumerate(report.candidate_pool[:5], 1):
                logger.info(f"     #{i} {c.symbol}: score={c.score:.1f}, pass={c.passing_count}/7")

        if report.position_reviews:
            logger.info("   持仓评审:")
            for r in report.position_reviews:
                pnl_pct = f"{r.unrealized_pnl_pct:+.1f}%" if r.unrealized_pnl_pct else "N/A"
                logger.info(
                    f"     {r.symbol}: {r.action.value} | 盈亏{pnl_pct} | {r.reason}"
                )

        if report.opening_suggestions:
            logger.info(f"   建仓建议: {[c.symbol for c in report.opening_suggestions]}")

        # scope=pool_only（盘中）：仅评估，不生成信号
        # 盘中 WatchDaemon 全权负责 signals_intra_day 链路
        if execution_scope == "pool_only":
            logger.info("ℹ️ scope=pool_only，仅评估候选池（不生成信号，由 WatchDaemon 负责盘中信号）")
            logger.info(f"   候选池: {report.candidate_count} 只通过评审")
            if report.candidate_pool:
                logger.info("   TOP5 标的:")
                for i, c in enumerate(report.candidate_pool[:5], 1):
                    logger.info(f"     #{i} {c.symbol}: score={c.score:.1f}, pass={c.passing_count}/7")
            client.disconnect()
            logger.info("✅ universe-refresh 完成")
            return 0

        # scope=full（盘后）：评估 + 生成信号 + 写 signals_pre_market
        if report.actions_summary:
            summary_str = ", ".join(f"{k}:{v}" for k, v in report.actions_summary.items())
            logger.info(f"   动作汇总: {summary_str}")

        # 生成信号并写入 signal JSON（复用 watch_daemon 链路）
        from src.core.signal import SignalGenerator
        from src.trading.universe_selector import PoolAction

        signals_to_write = []
        for review in report.position_reviews:
            if review.action == PoolAction.HOLD or review.action == PoolAction.SKIP:
                continue
            signals_to_write.append({
                "strategy_name": "universe-selector",
                "strategy_id": "universe-refresh",
                "symbol": review.symbol,
                "action": "SELL" if review.action in (PoolAction.REDUCE, PoolAction.CLOSE) else "BUY",
                "quantity": abs(review.suggested_qty_change),
                "reason": review.reason or review.action.value,
                "source": "universe-refresh",
                "processed": False,
            })

        # 建仓信号（Section 11.4 盘后决策逻辑）：
        # 无持仓的新 top2 标的 → BUY signal
        held_symbols = {p["symbol"] for p in positions} if positions else set()
        for candidate in report.opening_suggestions:
            if candidate.symbol in held_symbols:
                continue  # 已有持仓，跳过（由 position_reviews 处理）
            signals_to_write.append({
                "strategy_name": "universe-selector",
                "strategy_id": "universe-refresh",
                "symbol": candidate.symbol,
                "action": "BUY",
                "quantity": 10,  # 默认建仓量（模板 action.quantity）
                "reason": f"新入 Top2 建仓候选（得分{candidate.score:.1f}，通过{candidate.passing_count}/7）",
                "source": "universe-refresh",
                "processed": False,
            })

        if signals_to_write:
            # ========== 去重检查：查 order.json ========
            # signals 写入 T+1 的 order 文件（如 07/19 运行 → order_20260720.json）
            from src.core.paths import get_order_file
            order_file = get_order_file()  # 获取当日 order 文件路径
            duplicate_count = 0
            signals_after_dedup = []
            for sig in signals_to_write:
                sym = sig["symbol"]
                strat = sig["strategy_id"]
                # 检查 order 文件中是否已有已提交/已成交的同标的同策略订单
                dup = False
                if order_file.exists():
                    with open(order_file, "r", encoding="utf-8") as f:
                        order_data = json.load(f)
                    for section_orders in order_data.values():
                        if not isinstance(section_orders, list):
                            continue
                        for o in section_orders:
                            o_sig = o.get("signal", {})
                            o_status = o.get("status", "")
                            # IBKR 原始状态：PreSubmitted/Submitted/PendingSubmit/PartiallyFilled/Filled
                            if (o_sig.get("symbol") == sym
                                    and o_sig.get("strategy_id") == strat
                                    and o_status in ("PreSubmitted", "Submitted", "PendingSubmit", "PartiallyFilled", "Filled")
                                    and o.get("success", False)):
                                dup = True
                                break
                        if dup:
                            break
                if dup:
                    logger.info(f"  ⏭️ 跳过重复信号: {sym} ({strat})，已有活跃订单")
                    duplicate_count += 1
                else:
                    signals_after_dedup.append(sig)
            if not signals_after_dedup:
                logger.info("ℹ️  所有信号均为重复，跳过写入")
            logger.info(f"✅ 去重完成：{len(signals_to_write)} → {len(signals_after_dedup)}（过滤 {duplicate_count} 个重复）")
            # ========== 去重检查结束 ==========

            # 生成候选池更新报告（每次 scope=full 都追加一条记录）
            from src.trading.universe_selector import generate_candidate_pool_markdown_report
            exec_time = get_current_et_time().strftime("%H:%M:%S")
            report_date = args.date or get_current_et_time().strftime("%Y%m%d")
            generate_candidate_pool_markdown_report(
                report_date=report_date,
                execution_time=exec_time,
                old_top2=old_top2,
                new_top2=selector.top2,
                old_pool_symbols=old_candidate_symbols,
                new_candidates=report.candidate_pool,
                position_reviews=report.position_reviews,
                signals=signals_after_dedup,
            )

            if signals_after_dedup:
                # 写入信号文件
                et = get_current_et_time()
                # universe-refresh 是盘后操作，信号始终写入 signals_pre_market，等待次日盘前执行
                section = "signals_pre_market"

                generator = SignalGenerator()
                signal_data = generator._load_signal_file()
                # 替换 signals_pre_market 而非追加，防止重复执行导致信号累积
                signal_data[section] = list(signals_after_dedup)
                generator._save_signal_file(signal_data)

                logger.info(f"✅ 写入 {len(signals_after_dedup)} 个信号到 {section}，触发 execute()")

                # 触发 execute（复用 watch_daemon 链路）— 始终走盘前通道
                from src.trading.pre_market import execute as premkt_exec
                premkt_exec()
            else:
                logger.info("ℹ️  无新信号，跳过 pre-market.execute()")

        client.disconnect()
        logger.info("✅ universe-refresh 完成")
        return 0

    except Exception as e:
        logger.error(f"❌ 错误: {e}")
        traceback.print_exc()
        return 1


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

    # universe-refresh
    p_universe = subparsers.add_parser("universe-refresh", help="刷新候选池（盘后调用）")
    p_universe.add_argument("--date", type=str, default="", help="日期 (YYYYMMDD)")
    p_universe.add_argument("--output", type=str, default="", help="输出 JSON 报告路径")
    p_universe.set_defaults(func=cmd_universe_refresh)

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
