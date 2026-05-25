"""
Watch 守护进程 — 多标的策略监控 + TFSA 风控

功能：
- 从 WatchConfig.templates 推导标的列表，主循环轮询 StrategyFactory.analyze()
- 交易时段自动 ACTIVE/SLEEP 切换（9:30~16:00 ET），支持 SIGUSR1/SIGUSR2 手动控制
- 信号冷却（per-symbol，实盘倍率放大）
- PendingSignalStore 延迟信号：entry_delay_days > 0 时存入，到期自动执行
- TFSA 风控：提交订单前调用 RiskEngine 前置检查
- MarketRegimeDetector：regime_weights 按市场状态加权信号优先级
"""

import json
import os
import signal
import time
import sys
import uuid
import threading
from datetime import datetime
from pathlib import Path

import exchange_calendars as xc

from src.core.paths import (
    get_path, get_data_mode, get_current_et_time, get_order_file
)
from src.core.logger import get_logger, create_audit_record
from src.core.client import IBKRClient
from src.core.risk_engine import RiskEngine
from src.core.performance import PerformanceTracker
from src.core.models import StrategyResult
from src.core.strategy import StrategyFactory
from src.core.signal import convert_signal_to_dict
from src.core.pending_signals import PendingSignalStore
from config.config import load_config, WatchConfig, RiskConfig

logger = get_logger(__name__)
audit_logger = get_logger("audit")

PID_FILE = get_path("data") / "watch.pid"  # 运行时在 main 中重建为 data/<mode>/watch.pid

def _pid_path() -> Path:
    """获取当前 data_mode 对应的 PID 文件路径"""
    from src.core.paths import get_data_mode
    return get_path("data") / get_data_mode() / "watch.pid"

def write_pid():
    p = _pid_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()))

def remove_pid():
    _pid_path().unlink(missing_ok=True)
    PID_FILE.unlink(missing_ok=True)


def read_pid() -> int | None:
    for p in [PID_FILE, _pid_path()]:
        if p.exists():
            try:
                return int(p.read_text().strip())
            except (ValueError, OSError):
                pass
    return None


def send_signal(sig: int) -> bool:
    pid = read_pid()
    if pid is None:
        print("Watch daemon 未运行 (PID 文件不存在)")
        return False
    try:
        os.kill(pid, sig)
        name = {signal.SIGUSR1: "SIGUSR1 (唤醒)", signal.SIGUSR2: "SIGUSR2 (休眠)"}.get(sig, str(sig))
        print(f"已发送 {name} → PID {pid}")
        return True
    except ProcessLookupError:
        print(f"Watch daemon 进程不存在 (PID={pid})")
        remove_pid()
        return False


class WatchDaemon:
    def __init__(self, watch_config: WatchConfig | None = None,
                 risk_config: RiskConfig | None = None,
                 symbol_filter: str | None = None):
        self.logger = get_logger("WatchDaemon")

        if watch_config is None:
            full_config = load_config()
            watch_config = full_config.watch
            if risk_config is None:
                risk_config = full_config.risk_engine

        self.watch_config = watch_config
        all_symbols = watch_config.symbol_list
        if symbol_filter:
            sym = symbol_filter.upper()
            self.symbols = [sym] if sym in all_symbols else all_symbols
        else:
            self.symbols = all_symbols
        self.POLL_INTERVAL_SECONDS = watch_config.poll_interval
        self.real_cooldown_multiplier = getattr(watch_config, 'real_cooldown_multiplier', 4.0)

        self.running = True
        self.active = False
        self._wake_request = False
        self._sleep_request = False
        self._woken_manually = False

        self.heartbeat_counters: dict[str, int] = {s: 0 for s in self.symbols}
        self._signal_generated_at: dict[str, datetime] = {}
        self._last_submitted_signal: dict[str, datetime] = {}   # "SYMBOL_ACTION_strategyId" -> timestamp

        self.cooldown_file = get_path("data") / get_data_mode() / "watch_cooldown.json"
        self.cooldowns = self._load_cooldowns()

        self.risk_engine: RiskEngine | None = None
        if risk_config and risk_config.enabled:
            self.risk_engine = RiskEngine(risk_config)
            self.logger.info("TFSA 风控引擎已启用")

        self.performance_tracker = PerformanceTracker()
        self.logger.info("PerformanceTracker 已就绪")

        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGUSR1, self._handle_sigusr1)
        signal.signal(signal.SIGUSR2, self._handle_sigusr2)

        self._pending_notifications: list[dict] = []
        self._notify_fail_count = 0
        self._notification_thread: threading.Thread | None = None
        self._notification_lock = threading.Lock()

        self.pending_signal_store = PendingSignalStore()

        self._ibkr_client = None
        self._connect_ibkr_client()

        config = load_config()

        try:
            from src.core.regime import MarketRegimeDetector
            regime_detector = MarketRegimeDetector()
        except Exception as e:
            self.logger.warning("RegimeDetector 初始化失败，降级为无加权模式: %s", e)
            regime_detector = None

        self.factory = StrategyFactory(
            regime_detector=regime_detector,
            client=self._ibkr_client,
            market_data_source=config.market_data_source,
            template_dir=config.watch.template_dir,
            watch_templates=config.watch.templates,
        )
        self.logger.info("策略引擎已就绪（%d 个策略实例）", len(self.factory.yaml_strategies))

    def _connect_ibkr_client(self):
        """连接 IBKR 数据客户端 — 供 StrategyFactory 使用"""
        try:
            config = load_config()
            client = IBKRClient(config)
            conn = client.connect()
            if conn.success:
                self._ibkr_client = client
                self.logger.info("IBKR 数据客户端已连接")
            else:
                self.logger.warning("IBKR 数据客户端连接失败: %s, 使用 yfinance 回退", conn.error_message)
        except Exception as e:
            self.logger.warning("IBKR 数据客户端连接异常: %s, 使用 yfinance 回退", e)

    def _disconnect_ibkr_client(self):
        if self._ibkr_client is not None:
            try:
                self._ibkr_client.disconnect()
            except Exception:
                pass
            self._ibkr_client = None

    def _handle_sigterm(self, signum, frame):
        self.running = False

    def _handle_sigusr1(self, signum, frame):
        self._wake_request = True

    def _handle_sigusr2(self, signum, frame):
        self._sleep_request = True

    def _load_cooldowns(self) -> dict:
        if not self.cooldown_file.exists():
            return {}
        try:
            with open(self.cooldown_file) as f:
                data = json.load(f)
            result = {}
            for key, ts_str in data.items():
                try:
                    result[key] = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    pass
            return result
        except Exception as e:
            self.logger.warning(f"读取 cooldown 文件失败: {e}")
            return {}

    def _save_cooldowns(self):
        self.cooldown_file.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.isoformat() for k, v in self.cooldowns.items()}
        with open(self.cooldown_file, "w") as f:
            json.dump(data, f, indent=2)

    def _is_trading_now(self) -> bool:
        et = get_current_et_time()
        try:
            nyse = xc.get_calendar("XNYS")
            return nyse.is_open_on_minute(et)
        except Exception:
            if et.hour < 9 or (et.hour == 9 and et.minute < 30):
                return False
            if et.hour >= 16:
                return False
            if et.weekday() >= 5:
                return False
            return True

    def _get_cooldown_minutes(self, symbol: str) -> int:
        base = self.watch_config.get_cooldown(symbol)
        if self.risk_engine and not self.risk_engine._is_paper:
            return int(base * self.real_cooldown_multiplier)
        return base

    def _check_cooldown(self, symbol: str, direction: str) -> bool:
        # 检查两个方向，任一方向冷却中则双向都跳过（防止互斥信号）
        for d in ("BUY", "SELL"):
            key = f"{symbol}_{d}"
            last_time = self.cooldowns.get(key)
            if last_time:
                cd_min = self._get_cooldown_minutes(symbol)
                elapsed = datetime.now() - last_time
                if elapsed.total_seconds() < cd_min * 60:
                    remaining = cd_min * 60 - elapsed.total_seconds()
                    audit_logger.info(f"冷却中 [{key}]: 剩余 {remaining:.0f}s ({direction} 跳过)")
                    return True
        return False

    def _set_cooldown(self, symbol: str, direction: str):
        cd_min = self._get_cooldown_minutes(symbol)
        now = datetime.now()
        # 同时设置两个方向，确保双向冷却
        for d in ("BUY", "SELL"):
            key = f"{symbol}_{d}"
            self.cooldowns[key] = now
        self._save_cooldowns()
        audit_logger.info(f"冷却已设置 [{symbol}] ({direction}): {cd_min}min (双向)")

    def _flush_pending_notifications(self):
        """将本轮收集的待发送通知合并成一条微信消息，后台线程异步发送"""
        if not self._pending_notifications:
            return
        # 如果已有发送线程在跑，跳过本轮（防止堆积）
        if self._notification_thread and self._notification_thread.is_alive():
            self.logger.info("通知线程仍在运行中，跳过本轮")
            return

        self.logger.info("待发送通知 %d 条 (重试次数: %d)",
                         len(self._pending_notifications), self._notify_fail_count)

        # 原子交换：取出待发送消息，主线程可继续添加新消息不冲突
        with self._notification_lock:
            notifications = self._pending_notifications
            self._pending_notifications = []

        is_paper = getattr(self.risk_engine, '_is_paper', True) if self.risk_engine else True
        ts = datetime.now().strftime('%H:%M:%S')
        lines = [f"{ts} 模拟盘信号汇总 ({len(notifications)}条)"]
        lines.append("-" * 30)

        for n in notifications:
            action_cn = "买入" if n["action"] == "BUY" else "卖出"
            price_str = f"${n['price']:.2f}" if n['price'] else "?"
            lines.append(f"{'🟢' if n['action']=='BUY' else '🔴'} {n['symbol']} {action_cn} {n['quantity']}股 @ {price_str}")
            if n["reason"]:
                lines.append(f"  {n['reason']}")
            if is_paper and n["symbol"] in self.symbols:
                if n["action"] == "BUY":
                    lines.append(f"  🔥 实盘建仓: 请手动买入 {n['symbol']}")
                elif n["action"] == "SELL" and n["symbol"] == "F":
                    f_proceeds = (n["price"] or 0) * 100
                    lines.append(f"  🚗 F 清仓: 全部卖出 (回款 ~${f_proceeds:,.0f})")
            lines.append("")

        msg = "\n".join(lines).strip()

        def _do_send():
            try:
                # 1) 强制重置熔断器，避免静默丢弃
                cb_file = Path('/home/mango/.hermes/skills/weixin-direct-notify/.wx_circuit_breaker.json')
                try:
                    if cb_file.exists():
                        cb_file.write_text(json.dumps({
                            "state": "CLOSED",
                            "consecutive_failures": 0,
                            "last_failure_time": 0
                        }))
                except Exception:
                    pass

                # 2) 发送
                # TODO: 盘中信号推送到微信暂时裁剪，留给盘前/盘后报告使用
                # wx_path = '/home/mango/.hermes/skills/weixin-direct-notify'
                # if wx_path not in sys.path:
                #     sys.path.insert(0, wx_path)
                # if 'send_wx' in sys.modules:
                #     del sys.modules['send_wx']
                # from send_wx import send_text

                max_len = 1800
                chunks = [msg[i:i+max_len] for i in range(0, len(msg), max_len)]
                all_ok = False
                # for i, chunk in enumerate(chunks):
                #     if i > 0:
                #         time.sleep(4)
                #     ok = send_text(chunk, max_retries=1)
                #     if not ok:
                #         all_ok = False
                #         self.logger.warning("通知段 %d/%d 发送失败", i + 1, len(chunks))

                with self._notification_lock:
                    self._pending_notifications.clear()
                    self._notify_fail_count = 0

            except Exception as e:
                self.logger.warning("通知线程异常: %s", e)
                with self._notification_lock:
                    self._notify_fail_count += 1

        self._notification_thread = threading.Thread(target=_do_send, daemon=True)
        self._notification_thread.start()

    def _ensure_risk_engine_ready(self, client) -> bool:
        if self.risk_engine is None:
            return True
        if getattr(self.risk_engine, '_initialized', False):
            return True
        try:
            account_info = client.get_account_info(timeout=10)
            positions = {}
            for pos in account_info.positions:
                positions[pos.symbol] = pos.quantity
            self.risk_engine.set_account_info(
                account_id=account_info.account_id,
                net_liquidation=account_info.net_liquidation,
                positions=positions,
            )
            self.risk_engine.load_trade_history()
            self.risk_engine._initialized = True

            acct_type = "Paper (DU)" if self.risk_engine._is_paper else "实盘"
            self.logger.info("✅ 风控引擎已就绪 — %s 账户 (%s)", acct_type, account_info.account_id)

            return True
        except Exception as e:
            self.logger.error("❌ 风控引擎初始化失败: %s", e)
            if self.risk_engine.config.fail_closed:
                self.logger.error("fail_closed=true — 风控不可用，拒绝交易")
                return False
            self.logger.warning("fail_closed=false — 交易继续（无风控）")
            return True

    def _record_performance(self, signal_dict: dict, order: dict):
        """D8: 成交后自动记录策略执行结果到 PerformanceTracker"""
        try:
            result_id = str(uuid.uuid4())[:8]
            signal_time = signal_dict.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            avg_price = order.get("avg_price", 0) or 0
            signal_price = signal_dict.get("target_price") or avg_price

            result = StrategyResult(
                result_id=result_id,
                strategy_id=signal_dict.get("strategy_id", ""),
                signal_id=result_id,
                symbol=signal_dict["symbol"],
                action=signal_dict["action"],
                signal_price=signal_price,
                actual_fill_price=avg_price,
                quantity=order.get("filled_qty", 0) or signal_dict.get("quantity", 0),
                signal_time=signal_time,
            )
            if avg_price > 0 and signal_price > 0:
                result.slippage_pct = (avg_price - signal_price) / signal_price * 100

            self.performance_tracker.record_result(result)
            self.logger.info(f"绩效已记录: {result.strategy_id}/{result_id} {result.symbol} @ ${avg_price:.2f}")
        except Exception as e:
            self.logger.warning(f"记录绩效失败: {e}")

    def _submit_order(self, signal_dict: dict):
        symbol = signal_dict["symbol"]
        action = signal_dict["action"]

        # 重复信号防护: 同一 symbol+action+strategy_id 在冷却期内不重复提交
        strategy_id = signal_dict.get("strategy_id", "")
        dedup_key = f"{symbol}_{action}_{strategy_id}"
        last_submit = self._last_submitted_signal.get(dedup_key)
        if last_submit:
            cd_sec = self._get_cooldown_minutes(symbol) * 60
            elapsed = (datetime.now() - last_submit).total_seconds()
            if elapsed < cd_sec:
                self.logger.info(
                    "重复信号跳过: %s 冷却中 (剩余 %.0fs)", dedup_key, cd_sec - elapsed
                )
                return
        self._last_submitted_signal[dedup_key] = datetime.now()

    def _batch_submit_orders(self, signal_dicts: list):
        """批量写入 signal JSON，只调一次 execute()

        相比逐个 _submit_order，避免 N 个信号 = N 次连接/断开/报告。
        一次连接，多次提交。

        approval_required=true 时，信号入 signals_pending_approval 而非直接 execute。
        """
        from src.core.paths import get_signal_file, get_current_et_time
        from src.trading.intra_day import execute as _intra_exec
        from src.trading.pre_market import execute as _premkt_exec

        # approval 检查: 信号入待审批队列，不执行
        is_paper = getattr(self.risk_engine, '_is_paper', True) if self.risk_engine else True
        approval_required = (
            self.risk_engine.config.approval_required
            if self.risk_engine and hasattr(self.risk_engine.config, 'approval_required')
            else False
        )
        if approval_required:
            from src.core.order_approval import OrderApprovalQueue
            queue = OrderApprovalQueue()
            for sd in signal_dicts:
                item_id = queue.submit(sd)
                mode_label = "模拟" if is_paper else "实盘"
                self.logger.info("待审批订单已入队: %s %s %s x%d [%s]",
                    item_id, sd["symbol"], sd["action"], sd["quantity"], mode_label)
            return

        signal_file = get_signal_file()
        et = get_current_et_time()
        before_open = et.hour < 9 or (et.hour == 9 and et.minute < 30)
        after_close = et.hour >= 16
        section = "signals_pre_market" if (before_open or after_close) else "signals_intra_day"

        # 读取现有 signal JSON，批量追加
        signal_data = {"signals_pre_market": [], "signals_intra_day": []}
        if signal_file.exists():
            with open(signal_file) as f:
                signal_data = json.load(f)

        for sd in signal_dicts:
            sd_copy = dict(sd, processed=False)
            signal_data.setdefault(section, []).append(sd_copy)
            self._set_cooldown(sd["symbol"], sd["action"])

        with open(signal_file, "w") as f:
            json.dump(signal_data, f, indent=2, ensure_ascii=False, default=str)

        is_paper = getattr(self.risk_engine, '_is_paper', True) if self.risk_engine else True
        mode_label = "模拟" if is_paper else "实盘"
        self.logger.info("批量写入 %d 个信号到 %s [%s]，调用 execute()", len(signal_dicts), section, mode_label)

        if not self.running:
            self.logger.info("进程正在退出，跳过 execute()")
            return

        try:
            if before_open or after_close:
                _premkt_exec()
            else:
                _intra_exec()
        except Exception as e:
            self.logger.error(f"execute 异常: {e}")
            return

        if not self.running:
            self.logger.info("进程正在退出，跳过订单结果处理")
            return

        # 逐个处理执行结果（通知/绩效）
        for sd in signal_dicts:
            self._handle_executed_order(sd)
            create_audit_record("watch_order_submitted", True,
                symbol=sd["symbol"],
                action=sd["action"],
                quantity=sd["quantity"])
    def _handle_executed_order(self, signal_dict: dict):
        """execute() 完成后，从 order_YYYYMMDD.json 读回结果以触发通知/绩效"""
        from src.core.paths import get_order_file
        order_file = get_order_file()
        if not order_file.exists():
            return
        with open(order_file) as f:
            order_data = json.load(f)
        orders = order_data.get("orders_intra_day", []) + order_data.get("orders_pre_market", [])
        if not orders:
            return

        # 按 symbol+action+qty 匹配最近一条
        for order in reversed(orders):
            sig = order.get("signal", {})
            if (sig.get("symbol") == signal_dict["symbol"]
                    and sig.get("action") == signal_dict["action"]
                    and sig.get("quantity") == signal_dict["quantity"]):
                break
        else:
            return

        if order.get("success"):
            # 通知
            if os.getenv("WATCH_WX_NOTIFY_ENABLED", "true").lower() in ("1", "true", "yes"):
                with self._notification_lock:
                    self._pending_notifications.append({
                        "symbol": signal_dict["symbol"],
                        "action": signal_dict["action"],
                        "strategy_name": signal_dict.get("strategy_name", ""),
                        "strategy_id": signal_dict.get("strategy_id", ""),
                        "quantity": signal_dict["quantity"],
                        "price": order.get("avg_price") or signal_dict.get("target_price") or 0,
                        "reason": signal_dict.get("reason", ""),
                    })

            # 绩效记录
            filled_qty = order.get("filled_qty", 0) or signal_dict["quantity"]
            avg_price = order.get("avg_price", 0) or 0
            if self.risk_engine is not None:
                try:
                    self.risk_engine.record_trade(
                        signal_dict["symbol"], signal_dict["action"], filled_qty, avg_price,
                    )
                except Exception as e:
                    self.logger.error("record_trade 异常 (不影响后续流程): %s", e)
            self._record_performance(signal_dict, order)



    def run(self):
        if not self.factory.yaml_strategies:
            self.logger.error("没有可用的策略模板，退出")
            return

        write_pid()

        self.logger.info("=" * 50)
        self.logger.info(f"Watch 守护进程启动: {', '.join(self.symbols)}")
        self.logger.info(f"策略模板: {len(self.factory.yaml_strategies)} 个")
        self.logger.info(f"轮询间隔: {self.POLL_INTERVAL_SECONDS}s")
        cd_info = ", ".join(f"{s}={self._get_cooldown_minutes(s)}min" for s in self.symbols)
        self.logger.info(f"冷却时间: {cd_info}")
        self.logger.info(f"PID: {os.getpid()}")
        self.logger.info("=" * 50)

        if self._ibkr_client is not None:
            self._ensure_risk_engine_ready(self._ibkr_client)

        et = get_current_et_time()
        if self._is_trading_now():
            self.active = True
            self.logger.info(f"当前在交易时段 ({et.hour:02d}:{et.minute:02d} ET)，直接进入 ACTIVE 模式")
        else:
            self.logger.info(f"当前在非交易时段 ({et.hour:02d}:{et.minute:02d} ET)，进入 SLEEP 模式等待信号")

        while self.running:
            try:
                if self._wake_request:
                    self._wake_request = False
                    self.active = True
                    self._woken_manually = True
                    self.logger.info("收到唤醒信号 (SIGUSR1)，进入 ACTIVE 模式")
        
                if self._sleep_request:
                    self._sleep_request = False
                    self.active = False
                    self._woken_manually = False
                    self.logger.info("收到休眠信号 (SIGUSR2)，进入 SLEEP 模式")
        
                if not self.active:
                    if self._is_trading_now():
                        self.active = True
                        self._woken_manually = False
                        self.logger.info("交易时段已到，自动进入 ACTIVE 模式")
                    else:
                        remaining = 60
                        while remaining > 0 and self.running:
                            time.sleep(min(1, remaining))
                            remaining -= 1
                        continue
                elif not self._is_trading_now():
                    if self._woken_manually:
                        pass  # 手动唤醒：非交易时段保持 ACTIVE
                    else:
                        self.active = False
                        self.logger.info("交易时段已结束，自动进入 SLEEP 模式")
                        remaining = 60
                        while remaining > 0 and self.running:
                            time.sleep(min(1, remaining))
                            remaining -= 1
                        continue

                is_paper = getattr(self.risk_engine, '_is_paper', True) if self.risk_engine else True

                # Phase 1: 检查到期的延迟信号并执行
                ready_pending = self.pending_signal_store.get_ready_signals()
                if ready_pending:
                    self.logger.info(f"执行 {len(ready_pending)} 个到期延迟信号")
                    self._batch_submit_orders(ready_pending)

                # Phase 2: 生成新信号
                signals = self.factory.analyze(
                    target_symbols=set(self.symbols), is_paper=is_paper
                )
                
                immediate_signals = []
                
                for signal in signals:
                    direction = signal.action.value if hasattr(signal.action, 'value') else signal.action
                    if self._check_cooldown(signal.symbol, direction):
                        continue
                        
                    signal_key = f"{signal.symbol}_{signal.strategy_id}"
                    last_gen = self._signal_generated_at.get(signal_key)
                    cd_seconds = self._get_cooldown_minutes(signal.symbol) * 60
                    if last_gen and (datetime.now() - last_gen).total_seconds() < cd_seconds:
                        continue
                        
                    self._signal_generated_at[signal_key] = datetime.now()
                
                    signal_dict = convert_signal_to_dict(signal)
                    signal_dict["source"] = "watch"
                
                    self.logger.info(
                        f"SIGNAL: {signal_dict['symbol']} {signal_dict['action']} "
                        f"x{signal_dict['quantity']} "
                        f"strategy={signal_dict['strategy_id']} "
                        f"reason={signal_dict['reason']}"
                    )

                    # Phase 3: 延迟信号存入 pending store，立即信号直接提交
                    delay_days = getattr(signal, 'entry_delay_days', 0) or 0
                    if delay_days > 0:
                        self.pending_signal_store.add(signal_dict, delay_days=delay_days)
                        self.logger.info(
                            f"延迟信号已存入: {signal.symbol} {delay_days}天后执行"
                        )
                    else:
                        immediate_signals.append(signal_dict)
                
                if immediate_signals:
                    self._batch_submit_orders(immediate_signals)

                # 定期清理过期的 pending signals (每 100 轮)
                total_heartbeat = sum(self.heartbeat_counters.values())
                if total_heartbeat > 0 and total_heartbeat % 100 == 0:
                    self.pending_signal_store.cleanup(max_age_days=7)

                for symbol in self.symbols:
                    self.heartbeat_counters[symbol] += 1
                    if self.heartbeat_counters[symbol] % 30 == 0:
                        self.logger.info(f"[❤] {symbol}")

                self._flush_pending_notifications()
                remaining = self.POLL_INTERVAL_SECONDS
                while remaining > 0 and self.running:
                    time.sleep(min(1, remaining))
                    remaining -= 1

            except KeyboardInterrupt:
                self.logger.info("用户中断 (Ctrl+C)")
                break
            except Exception as e:
                self.logger.error(f"监控循环异常: {e}", exc_info=True)
                time.sleep(self.POLL_INTERVAL_SECONDS * 2)

        remove_pid()
        self._disconnect_ibkr_client()
        self.logger.info(f"Watch 守护进程已停止: {', '.join(self.symbols)}")


def run_watch(symbol: str | None = None):
    from src.core.paths import set_data_mode, resolve_data_mode
    cfg = load_config()
    set_data_mode(resolve_data_mode(cfg.gateway.account_id or ""))
    daemon = WatchDaemon(cfg.watch, cfg.risk_engine, symbol_filter=symbol)
    daemon.run()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_watch(sys.argv[1])
    else:
        print("用法: python watch_daemon.py <symbol>")
