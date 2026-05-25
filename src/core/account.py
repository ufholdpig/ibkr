"""账户工具 — 持仓映射、账户信息查询等账户级功能"""

from typing import List, Dict, Optional
from src.core.logger import get_logger

logger = get_logger(__name__)

def build_position_map(positions: List) -> Dict[str, Dict]:
    """将原始持仓对象列表转换为 {symbol: {average_cost, quantity}} 映射"""
    position_map = {}
    for pos in positions:
        if hasattr(pos, "symbol") and hasattr(pos, "average_cost"):
            position_map[pos.symbol] = {
                "average_cost": pos.average_cost,
                "quantity": getattr(pos, "quantity", 0),
            }
    return position_map


def get_managed_account(client, timeout: int = 5) -> Optional[str]:
    """获取当前登录账号（从 API 回调获取）

    等待 managedAccounts 回调，最多等待 timeout 秒。

    Args:
        client: IBKRClient 实例
        timeout: 等待回调的超时秒数 (默认 5)

    Returns:
        账户 ID，如果未获取到则返回 None
    """
    import time
    from src.core.exceptions import NotConnectedError, raise_ibkr_error

    api_client = client.get_api_client()

    if not api_client.connected:
        raise_ibkr_error(NotConnectedError, "未连接到 IB Gateway")

    start_time = time.time()
    while time.time() - start_time < timeout:
        account_id = api_client._managed_account
        if account_id:
            from src.core.logger import get_logger
            get_logger(__name__).info(f"✅ 从 API 回调获取账户 ID: {account_id}")
            return account_id
        time.sleep(0.1)

    from src.core.logger import get_logger
    get_logger(__name__).warning("⚠️ 等待 managedAccounts 回调超时")
    return None
