"""日志记录工具模块"""
import logging
import os
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

EST = ZoneInfo("America/Toronto")


class ESTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, EST)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def setup_loggers():
    from src.core.session_handler import DailyFileHandler, SessionFileHandler

    logging.shutdown()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = ESTFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    audit_fmt = ESTFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # --- handlers ---
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)

    session = SessionFileHandler(LOG_DIR)
    session.setFormatter(fmt)
    session.setLevel(logging.INFO)

    audit = DailyFileHandler(LOG_DIR, "audit")
    audit.setFormatter(audit_fmt)
    audit.setLevel(logging.DEBUG)

    error = DailyFileHandler(LOG_DIR, "error")
    error.setFormatter(fmt)
    error.setLevel(logging.ERROR)

    # --- logger routing ---

    # Session: important trading operations only
    session_loggers = [
        "ibclient",
        "src.trading",
        "src.core.signal",
        "src.core.orders",
        "src.core.client",
        "src.core.account",
        "src.core.risk_engine",
        "WatchDaemon",
        "RiskEngine",
    ]

    for name in session_loggers:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(session)
        logger.addHandler(console)
        logger.addHandler(error)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

    # Audit: Phase 1-4 detailed debug + external tool logs
    audit_loggers = [
        "src.core.strategy",
        "src.core.performance",
        "src.core.conditions",
        "src.core.regime",
        "src.core.learning",
        "src.core.backtesting",
        "src.core.sandbox",
        "src.core.reporting",
        "src.core.session",
        "src.core.market_data",
        "src.core.paths",
        "src.core.models",
        "src.core.exceptions",
        "src.core.utils",
        "src.core.logger",
        "audit",
        "config",
        "ibapi",
        "yfinance",
        "peewee",
        "hermes_cli",
    ]

    for name in audit_loggers:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(audit)
        logger.addHandler(error)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

    # ibapi/peewee 子模块的轮询噪音 (每秒心跳日志, SQL查询) 抬到 WARNING 级别
    for noisy in ("ibapi.client", "ibapi.connection", "ibapi.reader", "peewee", "yfinance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Root catch-all: unconfigured loggers → audit at INFO+
    root.addHandler(audit)
    root.addHandler(error)
    root.setLevel(logging.INFO)


_setup_done = False


def get_logger(name: str):
    global _setup_done
    if not _setup_done:
        setup_loggers()
        _setup_done = True
    return logging.getLogger(name)


def create_audit_record(operation: str, success: bool, **details):
    logger = logging.getLogger("src.core.logger")
    record = {
        "timestamp": datetime.now(EST).isoformat(),
        "severity": "INFO",
        "message": f"{operation} {'成功' if success else '失败'}",
        "operation": operation,
        "success": success,
        **details,
        "hostname": os.uname().nodename,
    }
    logger.info(json.dumps(record))


def close_all_handlers():
    logging.shutdown()


__all__ = ["get_logger", "create_audit_record", "close_all_handlers"]
