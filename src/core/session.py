"""
Session State Manager (会话状态管理器)
负责在模块间持久化 client_id，确保长连接会话一致性。
"""
import json
import random
from pathlib import Path
from typing import Optional
from datetime import datetime

from src.core.logger import get_logger

logger = get_logger(__name__)

SESSION_STATE_FILE = Path("config/session_state.json")


class SessionManager:
    """管理 IBKR 会话状态"""

    def __init__(self):
        self.state_file = SESSION_STATE_FILE
        if not self.state_file.parent.exists():
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_state(self) -> dict:
        """加载会话状态"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载会话状态失败：{e}，将生成新 ID")
        return {}

    def _save_state(self, state: dict) -> None:
        """保存会话状态"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            logger.info(f"💾 会话状态已保存：{state}")
        except Exception as e:
            logger.error(f"保存会话状态失败：{e}")

    def get_preferred_client_id(self, force_new: bool = False) -> int:
        """
        获取首选 client_id（复用优先逻辑）
        
        逻辑：
        1. 读取保存的 ID
        2. 如果不存在，从 100 开始
        3. 返回该 ID（由调用者尝试连接）
        4. 如果连接失败（被占用），调用 update_client_id 递增
        """
        state = self._load_state()
        saved_id = state.get('client_id')

        if saved_id is not None and not force_new:
            # 优先复用保存的 ID
            logger.info(f"🔄 尝试复用保存的 client_id: {saved_id}")
            return saved_id
        else:
            # 首次运行或强制新 ID
            new_id = 100
            logger.info(f"🆕 初始化 client_id: {new_id}")
            self._save_state({
                'client_id': new_id,
                'saved_at': datetime.now().isoformat(),
                'module': 'unknown'
            })
            return new_id

    def update_client_id(self, new_id: int) -> None:
        """更新 client_id（当发生冲突时调用）"""
        state = self._load_state()
        state['client_id'] = new_id
        state['saved_at'] = datetime.now().isoformat()
        state['updated_reason'] = 'conflict_resolution'
        self._save_state(state)
        logger.info(f"🔄 已更新 client_id 为：{new_id} (因冲突)")

    def reset_session(self) -> None:
        """重置会话（强制生成新 ID）"""
        if self.state_file.exists():
            self.state_file.unlink()
            logger.info("🗑️ 会话状态已重置")
