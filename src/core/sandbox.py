"""沙盒部署器 — 安全验证生产变更 (Phase 4 D42-D43)

职责:
1. deploy_to_paper(): 将审批变更部署到 Paper 配置, 返回 sandbox_id
2. validate(): N 天后验证 Paper 执行结果, 对比回测预期
3. promote(): 验证通过后自动推广到 Real 配置
4. rollback(): 验证失败后回滚 Paper 配置

设计约束:
- 沙盒配置独立于生产配置 (strategy/strategies/sandbox/)
- 所有操作有审计日志
- 推广/回滚阈值可配置
"""

import json
import logging
import uuid
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from src.core.models import ApprovalItem, BacktestResult
from src.core.paths import get_path, ensure_dir
from src.core.performance import PerformanceTracker

logger = logging.getLogger("SandboxDeployer")


class SandboxDeployer:
    """沙盒部署器

    流程:
    1. 用户批准变更 (ApprovalQueue.approve)
    2. SandboxDeployer.deploy_to_paper() 部署到 paper 配置并记录沙盒
    3. 运行 N 天收集 Paper 执行数据 (外部 cron 调用 validate)
    4. SandboxDeployer.validate() 对比 Paper 结果与回测预期
    5. 偏差 < 阈值 → promote() 推广到 Real
    6. 偏差 >= 阈值 → rollback() 回滚 Paper
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.sandbox_dir = Path(cfg.get(
            "sandbox_dir",
            get_path("sandbox"),
        ))
        self.strategy_dir = Path(cfg.get(
            "strategy_dir",
            Path(__file__).parent.parent.parent / "strategy" / "templates",
        ))
        self.validation_days = cfg.get("validation_days", 7)
        self.pnl_deviation_threshold = cfg.get("pnl_deviation_threshold", 5.0)

        ensure_dir(path=self.sandbox_dir)
        self.tracker = PerformanceTracker()
        self.logger = logger

    def deploy_to_paper(self, item: ApprovalItem) -> Optional[str]:
        """将审批变更部署到 Paper 配置

        Args:
            item: 已批准的审批项

        Returns:
            sandbox_id (str) 或 None (失败)
        """
        if item.status != "APPROVED":
            self.logger.warning("审批项未批准, 跳过部署: %s", item.item_id)
            return None

        sandbox_id = f"sandbox_{item.item_id}_{uuid.uuid4().hex[:6]}"
        sandbox_item_dir = self.sandbox_dir / sandbox_id
        ensure_dir(path=sandbox_item_dir)

        # 1. 查找并复制原 YAML 到沙盒目录
        src_yaml = self._find_strategy_yaml(item.strategy_id)
        if src_yaml is None:
            self.logger.error("未找到策略 YAML: %s", item.strategy_id)
            return None

        sandbox_yaml_path = sandbox_item_dir / src_yaml.name
        shutil.copy2(src_yaml, sandbox_yaml_path)

        # 2. 读取并应用变更
        with open(sandbox_yaml_path) as f:
            config = yaml.safe_load(f)

        success = self._apply_change(config, item)
        if not success:
            self.logger.error("应用变更失败: %s", item.item_id)
            return None

        with open(sandbox_yaml_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

        # 3. 部署到 paper 配置目录
        paper_dir = self.strategy_dir / "paper"
        ensure_dir(path=paper_dir)
        paper_yaml_path = paper_dir / src_yaml.name
        shutil.copy2(sandbox_yaml_path, paper_yaml_path)

        # 4. 记录沙盒元数据
        meta = {
            "sandbox_id": sandbox_id,
            "item_id": item.item_id,
            "strategy_id": item.strategy_id,
            "change_type": item.change_type,
            "current_value": item.current_value,
            "proposed_value": item.proposed_value,
            "deployed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "validation_complete": False,
            "promoted": False,
            "rolled_back": False,
        }
        self._save_meta(sandbox_id, meta)

        self.logger.info(
            "沙盒部署完成: %s (%s %s → %s)",
            sandbox_id, item.change_type, item.current_value, item.proposed_value,
        )
        return sandbox_id

    def validate(self, sandbox_id: str, backtest_expected: BacktestResult = None) -> dict:
        """验证沙盒运行结果

        Args:
            sandbox_id: 沙盒 ID
            backtest_expected: 回测预期结果 (用于偏差对比)

        Returns:
            {validated, paper_pnl, deviation_pct, message}
        """
        meta = self._load_meta(sandbox_id)
        if meta is None:
            return {"validated": False, "error": "沙盒不存在"}

        strategy_id = meta["strategy_id"]
        perf = self.tracker.get_performance(strategy_id)

        paper_pnl = perf.avg_pnl_pct if perf.total_closed > 0 else 0.0
        expected_pnl = backtest_expected.avg_pnl_pct if backtest_expected else 0.0
        deviation = abs(paper_pnl - expected_pnl) if expected_pnl != 0 else abs(paper_pnl)

        validation = {
            "validated": True,
            "sandbox_id": sandbox_id,
            "strategy_id": strategy_id,
            "paper_pnl": paper_pnl,
            "expected_pnl": expected_pnl,
            "deviation_pct": round(deviation, 2),
            "paper_trades": perf.total_closed,
            "sample_sufficient": perf.sample_size_sufficient,
            "within_threshold": deviation <= self.pnl_deviation_threshold,
            "message": "",
        }

        if deviation <= self.pnl_deviation_threshold:
            validation["message"] = (
                f"偏差 {deviation:.1f}% ≤ 阈值 {self.pnl_deviation_threshold}%, 可推广"
            )
        else:
            validation["message"] = (
                f"偏差 {deviation:.1f}% > 阈值 {self.pnl_deviation_threshold}%, 建议回滚"
            )

        meta["validation_complete"] = True
        meta["validated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_meta(sandbox_id, meta)

        self.logger.info("沙盒验证: %s — %s", sandbox_id, validation["message"])
        return validation

    def promote(self, sandbox_id: str) -> bool:
        """推广沙盒变更到 Real 配置

        Args:
            sandbox_id: 沙盒 ID

        Returns:
            是否成功推广
        """
        meta = self._load_meta(sandbox_id)
        if meta is None:
            return False

        if not meta.get("validation_complete"):
            self.logger.warning("沙盒未验证, 跳过推广: %s", sandbox_id)
            return False

        src_yaml = self._find_strategy_yaml(meta["strategy_id"])
        if src_yaml is None:
            return False

        sandbox_yaml = self.sandbox_dir / sandbox_id / src_yaml.name
        if not sandbox_yaml.exists():
            self.logger.error("沙盒 YAML 不存在: %s", sandbox_yaml)
            return False

        # 备份当前生产配置
        backup_dir = self.sandbox_dir / sandbox_id / "backup"
        ensure_dir(path=backup_dir)
        shutil.copy2(src_yaml, backup_dir / src_yaml.name)

        # 沙盒 YAML → 生产 YAML
        shutil.copy2(sandbox_yaml, src_yaml)

        meta["promoted"] = True
        meta["promoted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_meta(sandbox_id, meta)

        self.logger.info("沙盒推广完成: %s → %s", sandbox_id, src_yaml)
        return True

    def rollback(self, sandbox_id: str) -> bool:
        """回滚沙盒变更

        Args:
            sandbox_id: 沙盒 ID

        Returns:
            是否成功回滚
        """
        meta = self._load_meta(sandbox_id)
        if meta is None:
            return False

        src_yaml = self._find_strategy_yaml(meta["strategy_id"])
        if src_yaml is None:
            return False

        backup_yaml = self.sandbox_dir / sandbox_id / "backup" / src_yaml.name
        if not backup_yaml.exists():
            self.logger.warning("无备份配置, 跳过回滚: %s", sandbox_id)
            return False

        shutil.copy2(backup_yaml, src_yaml)

        # 也回滚 paper 配置
        paper_yaml = self.strategy_dir / "paper" / src_yaml.name
        if paper_yaml.exists():
            shutil.copy2(backup_yaml, paper_yaml)

        meta["rolled_back"] = True
        meta["rolled_back_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_meta(sandbox_id, meta)

        self.logger.info("沙盒回滚完成: %s", sandbox_id)
        return True

    # =========================================================================
    # 内部方法
    # =========================================================================

    def _find_strategy_yaml(self, strategy_id: str) -> Optional[Path]:
        """按 strategy_id 查找 YAML 文件"""
        for yaml_file in self.strategy_dir.glob("*.yaml"):
            try:
                with open(yaml_file) as f:
                    cfg = yaml.safe_load(f)
                if cfg and cfg.get("strategy_id") == strategy_id:
                    return yaml_file
            except Exception:
                continue
        return None

    @staticmethod
    def _apply_change(config: dict, item: ApprovalItem) -> bool:
        """将审批项应用到策略配置"""
        try:
            if item.change_type == "WEIGHT_ADJUST":
                config["weight"] = item.proposed_value
            elif item.change_type == "LIFECYCLE_TRANSITION":
                config["state"] = item.proposed_value
            elif item.change_type == "REGIME_WEIGHT" and item.proposed_value:
                regime, value = str(item.proposed_value).split(": ")
                config.setdefault("regime_weights", {})[regime] = float(value)
            else:
                logger.warning("不支持的变更类型: %s", item.change_type)
                return False
            return True
        except Exception as e:
            logger.error("应用变更异常: %s", e)
            return False

    def _save_meta(self, sandbox_id: str, meta: dict):
        filepath = self.sandbox_dir / sandbox_id / "meta.json"
        filepath.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_meta(self, sandbox_id: str) -> Optional[dict]:
        filepath = self.sandbox_dir / sandbox_id / "meta.json"
        if not filepath.exists():
            return None
        try:
            return json.loads(filepath.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("加载沙盒元数据失败: %s", e)
            return None

    def list_sandboxes(self) -> List[dict]:
        """列出所有沙盒"""
        sandboxes = []
        if not self.sandbox_dir.exists():
            return sandboxes
        for d in self.sandbox_dir.iterdir():
            if d.is_dir():
                meta = self._load_meta(d.name)
                if meta:
                    sandboxes.append(meta)
        return sandboxes
