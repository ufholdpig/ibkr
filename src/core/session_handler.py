"""Session日志处理器 - 每天创建 session_YYYYMMDD.log"""
import logging
from pathlib import Path
from datetime import datetime


class DailyFileHandler(logging.FileHandler):
    """每天创建一个日志文件"""
    
    def __init__(self, log_dir: Path, prefix: str):
        self.log_dir = log_dir
        self.prefix = prefix
        self.baseFilename = str(log_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d')}.log")
        super().__init__(self.baseFilename, mode='a', encoding='utf8')
    
    def emit(self, record):
        today = datetime.now().strftime("%Y%m%d")
        expected = str(self.log_dir / f"{self.prefix}_{today}.log")
        if self.baseFilename != expected:
            self.close()
            self.baseFilename = expected
            self.stream = open(self.baseFilename, self.mode, encoding=self.encoding)
        super().emit(record)


class SessionFileHandler(DailyFileHandler):
    def __init__(self, log_dir: Path):
        super().__init__(log_dir, "session")


class AuditFileHandler(DailyFileHandler):
    def __init__(self, log_dir: Path):
        super().__init__(log_dir, "audit")


class ErrorFileHandler(DailyFileHandler):
    def __init__(self, log_dir: Path):
        super().__init__(log_dir, "error")
    
    # error handler 需要额外的 filter
    def __init__(self, log_dir: Path, filter_func=None):
        super().__init__(log_dir, "error")
        self.filter_func = filter_func
    
    def emit(self, record):
        if self.filter_func and not self.filter_func(record):
            return
        super().emit(record)