"""
订单审批队列 — 操作 signal_YYYYMMDD.json[signals_pending_approval]

Watch daemon 生成信号后写入当日信号文件待审批区，
用户 approve 后取出、追加到 signals_pre_market/intra_day 并调用 execute()。

设计原则：
- 数据不分散：审批中信号与已确认信号同在一个文件中
- 自动过期：过期的待审批项随 signal_YYYYMMDD.json 更替自然消失，
  无需额外清理
- 原子读写：fcntl.flock 保证 daemon（5s 轮询）和 CLI 并发安全
"""

import json
import uuid
import fcntl
from datetime import datetime
from typing import Optional

from src.core.paths import get_signal_file
from src.core.logger import get_logger

logger = get_logger(__name__)


class OrderApprovalQueue:
    """操作 signal_YYYYMMDD.json[signals_pending_approval] 的审批队列"""

    def __init__(self):
        self._signal_file = get_signal_file()

    def submit(self, signal_dict: dict) -> str:
        """提交信号到待审批区"""
        item_id = uuid.uuid4().hex[:12]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "item_id": item_id,
            "signal": dict(signal_dict),
            "created_at": now,
        }

        def _add(data):
            data.setdefault("signals_pending_approval", []).append(entry)

        self._modify(_add)
        logger.info("待审批已入队: %s  %s %s x%d", item_id,
                     signal_dict.get("symbol", ""),
                     signal_dict.get("action", ""),
                     signal_dict.get("quantity", 0))
        return item_id

    def approve(self, item_id: str) -> Optional[dict]:
        """从待审批区取出 signal_data，调用者负责传给 write_signal_and_execute()"""
        found = []

        def _approve(data):
            pending = data.get("signals_pending_approval", [])
            for i, item in enumerate(pending):
                if item["item_id"] == item_id:
                    found.append(item["signal"])
                    pending.pop(i)
                    break

        self._modify(_approve)
        if found:
            sig = found[0]
            logger.info("审批通过: %s  %s %s", item_id, sig.get("symbol", ""), sig.get("action", ""))
        else:
            logger.info("未找到待审批项: %s", item_id)
        return found[0] if found else None

    def reject(self, item_id: str) -> bool:
        """拒绝：从待审批区移除"""
        found = [False]

        def _reject(data):
            pending = data.get("signals_pending_approval", [])
            for i, item in enumerate(pending):
                if item["item_id"] == item_id:
                    pending.pop(i)
                    found[0] = True
                    break

        self._modify(_reject)
        if found[0]:
            logger.info("审批拒绝: %s", item_id)
        return found[0]

    @property
    def pending_items(self) -> list[dict]:
        """返回待审批条目列表，每项含 item_id/signal/created_at"""
        try:
            with open(self._signal_file) as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                data = json.load(f)
            return data.get("signals_pending_approval", [])
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def get_pending_summary(self) -> str:
        """格式化待审批列表，供 order-list 命令输出"""
        pending = self.pending_items
        if not pending:
            return ""
        lines = ["📋 待审批订单", "=" * 30]
        for i, item in enumerate(pending, 1):
            sig = item.get("signal", {})
            action_cn = "买入" if sig.get("action") == "BUY" else "卖出"
            price = sig.get("target_price", 0) or 0
            price_str = f" @ ${price:.2f}" if price > 0 else ""
            lines.append(
                f"  [{i}] {item['item_id']}  {sig.get('symbol', '')}  "
                f"{action_cn}  {sig.get('quantity', 0)}股{price_str}"
            )
            lines.append(
                f"      策略: {sig.get('strategy_name', '')}  |  "
                f"时间: {item.get('created_at', '')}"
            )
            reason = sig.get("reason", "")
            if reason:
                lines.append(f"      原因: {reason}")
            lines.append("")
        lines.append(f"共 {len(pending)} 条待审批")
        return "\n".join(lines)

    def is_empty(self) -> bool:
        return len(self.pending_items) == 0

    def _modify(self, modifier_fn):
        """原子 read-modify-write（排他锁），保证并发安全"""
        self._signal_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._signal_file.exists():
            self._signal_file.write_text("{}", encoding="utf-8")
        with open(self._signal_file, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                data = json.load(f)
            except (json.JSONDecodeError, Exception):
                data = {}
            f.seek(0)
            f.truncate()
            modifier_fn(data)
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
