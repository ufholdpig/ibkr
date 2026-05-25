"""IBKR 项目统一路径管理模块

核心原则：
1. **项目根目录固定**: `/home/mango/projects/ibkr` (硬编码，确保稳定)
2. **用户主目录动态**: 通过 `pwd` 模块动态获取，绕过 `$HOME` 环境变量污染
3. **统一接口**: 所有路径访问必须通过 `get_path()` 或 `get_hermes_path()`
4. **禁止硬编码**: 严禁在代码中直接使用 `~`、`$HOME` 或字符串拼接路径

设计依据：
- Hermes Agent 在独立 profile 中运行，`~` 可能被重定向到 profile 目录
- 项目文件（代码、配置、报告）必须与 Hermes 配置隔离
"""

import os
import pwd
from pathlib import Path
from typing import Optional
import pytz
import exchange_calendars as xc
from datetime import datetime, timedelta

# 1. 动态获取真实用户主目录 (绕过环境变量 $HOME)
# 使用 pwd 模块获取当前运行用户的真实家目录
try:
    REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
except Exception:
    # 兜底：如果 pwd 失败，回退到 $HOME (极少发生)
    REAL_HOME = Path(os.environ.get("HOME", "/home/mango"))

# 2. 动态获取项目根目录 (避免硬编码，提升可移植性)
# 当前文件位于 <root>/src/core/paths.py，因此向上两级即可得到根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 3. Hermes Profile 目录 (独立子系统)
HERMES_PROFILE_ROOT = REAL_HOME / ".hermes" / "profiles" / "ibkr"

# 4. 虚拟环境路径
VENV_PATH = HERMES_PROFILE_ROOT / "venv"

# 5. 技能目录 (IBKR 专属技能)
SKILLS_PATH = HERMES_PROFILE_ROOT / "skills"

# 6. 一级子目录 (项目根目录下)
LOGS_PATH = PROJECT_ROOT / "logs"
DATA_PATH = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config"

# 数据模式: "paper" 或 "real"，决定 data/ 下 signals/orders/reports 使用哪个子目录
# 默认 paper (安全)，通过 set_data_mode() 在运行时切换
_data_mode = "paper"

# 预定义路径映射表（signals/orders/reports 为动态目录，不在表中）
_PATH_MAP = {
    "root": PROJECT_ROOT,
    "home": REAL_HOME,
    "hermes": HERMES_PROFILE_ROOT,
    "venv": VENV_PATH,
    "skills": SKILLS_PATH,
    "src": PROJECT_ROOT / "src",
    "docs": PROJECT_ROOT / "docs",
    "logs": LOGS_PATH,
    "data": DATA_PATH,
    "config": CONFIG_PATH,
}

# 确保默认数据目录存在
(DATA_PATH / _data_mode / "signals").mkdir(parents=True, exist_ok=True)
(DATA_PATH / _data_mode / "orders").mkdir(parents=True, exist_ok=True)
(DATA_PATH / _data_mode / "reports").mkdir(parents=True, exist_ok=True)

_DATA_KEYS = {"signals", "orders", "reports", "performances", "learning", "sandbox"}


def get_data_mode() -> str:
    """获取当前数据模式"""
    return _data_mode


def set_data_mode(mode: str):
    global _data_mode
    if mode not in ("paper", "real"):
        raise ValueError(f"data_mode 必须是 'paper' 或 'real'，当前值: {mode}")
    _data_mode = mode
    for key in _DATA_KEYS:
        new_dir = DATA_PATH / _data_mode / key
        new_dir.mkdir(parents=True, exist_ok=True)
        # 迁移旧数据: data/<key>/ -> data/<mode>/<key>/
        old_dir = DATA_PATH / key
        if old_dir.is_dir() and (not new_dir.exists() or not any(new_dir.iterdir())):
            for f in sorted(old_dir.iterdir()):
                if f.is_file():
                    f.rename(new_dir / f.name)
            # 如果旧目录已清空，删除
            if not any(old_dir.iterdir()):
                old_dir.rmdir()


def resolve_data_mode(account_id: str = "") -> str:
    """从 account_id 推断数据模式: DU 前缀 = paper, 其余 = real"""
    return "paper" if account_id.startswith("DU") else "real"


def get_path(key: Optional[str] = None, *subpaths: str) -> Path:
    """获取统一路径

    Args:
        key: 路径类型键 (如 "reports", "logs", "skills", "root")
             如果为 None，返回 PROJECT_ROOT
        *subpaths: 子路径组件 (如 "2026-04-23", "account_info.md")

    Returns:
        pathlib.Path 对象

    Raises:
        KeyError: 如果 key 不存在

    Examples:
        get_path("reports") -> /home/mango/projects/ibkr/data/<mode>/reports
        get_path("logs", "intraday.log") -> /home/mango/projects/ibkr/logs/intraday.log
        get_path() -> /home/mango/projects/ibkr
    """
    if key is None:
        base = PROJECT_ROOT
    elif key in _DATA_KEYS:
        base = DATA_PATH / _data_mode / key
    elif key in _PATH_MAP:
        base = _PATH_MAP[key]
    else:
        raise KeyError(f"未知路径键：{key}。可用键：{list(_PATH_MAP.keys()) + list(_DATA_KEYS)}")

    return base.joinpath(*subpaths)


def get_hermes_path(*subpaths: str) -> Path:
    """获取 Hermes Profile 内部路径

    专门用于访问 Hermes 配置、技能缓存等 Profile 内部文件。

    Args:
        *subpaths: 子路径组件

    Returns:
        pathlib.Path 对象

    Examples:
        get_hermes_path("skills", "weixin-direct-notify")
    """
    return HERMES_PROFILE_ROOT.joinpath(*subpaths)


def ensure_dir(path: Optional[Path] = None, key: Optional[str] = None) -> Path:
    """确保目录存在，如果不存在则创建

    Args:
        path: 直接传入 Path 对象
        key: 或者传入路径键，自动调用 get_path()

    Returns:
        创建的目录 Path 对象
    """
    if path is None and key:
        path = get_path(key)
    elif path is None:
        raise ValueError("必须提供 path 或 key")

    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_environment() -> dict:
    """验证运行环境

    Returns:
        包含环境状态的字典
    """
    return {
        "real_home": str(REAL_HOME),
        "project_root": str(PROJECT_ROOT),
        "hermes_profile": str(HERMES_PROFILE_ROOT),
        "venv_exists": VENV_PATH.exists(),
        "venv_python": str(VENV_PATH / "bin" / "python3"),
        "reports_exists": (DATA_PATH / _data_mode / "reports").exists(),
        "logs_exists": LOGS_PATH.exists(),
    }

def get_current_et_time():
    """获取当前ET时间对象（用于时间戳转换）"""
    utc_now = datetime.utcnow()
    eastern = pytz.timezone('US/Eastern')
    return utc_now.replace(tzinfo=pytz.utc).astimezone(eastern)

def get_trading_date():
    """获取北美交易日日期（ET时区：盘中返回当天，盘后或非交易日返回下一个合法交易日）"""
    et_time = get_current_et_time()
    try:
        nyse = xc.get_calendar("XNYS")
        if nyse.is_open_on_minute(et_time):
            return et_time.strftime("%Y%m%d")
        return _next_trading_day(et_time)
    except Exception:
        if et_time.hour >= 16:
            return (et_time + timedelta(days=1)).strftime("%Y%m%d")
        return et_time.strftime("%Y%m%d")

def _next_trading_day(et_time: datetime) -> str:
    """查找et_time之后的下一个合法美股交易日"""
    try:
        nyse = xc.get_calendar("XNYS")
        next_open = nyse.next_open(et_time)
        return next_open.strftime("%Y%m%d")
    except Exception:
        return (et_time + timedelta(days=1)).strftime("%Y%m%d")

def get_post_report_date():
    """获取北美交易日盘后报告文件（ET时区，16:00前使用前日日期）"""
    et_time = get_current_et_time()
    if et_time.hour < 16:
       return (et_time - timedelta(days=1)).strftime("%Y%m%d")
    return et_time.strftime("%Y%m%d")

def get_signal_file():
    """获取当日信号文件"""
    return get_path("signals", f"signal_{get_trading_date()}.json")

def get_order_file():
    """获取当日订单文件"""
    return get_path("orders", f"order_{get_trading_date()}.json")


def get_performance_file():
    """获取当日绩效记录文件"""
    return get_path("performances", f"performance_{get_trading_date()}.json")

# 初始化：确保关键目录存在
if __name__ == "__main__":
    # 仅用于调试
    print("🔍 路径验证:")
    for key, path in _PATH_MAP.items():
        exists = "✅" if path.exists() else "❌"
        print(f" {exists} {key}: {path}")

    print("\n📊 环境状态:")
    status = validate_environment()
    for k, v in status.items():
        print(f" {k}: {v}")
