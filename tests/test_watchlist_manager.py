"""WatchlistManager 测试"""

import unittest
from datetime import datetime
from unittest.mock import MagicMock

import sys
sys.path.insert(0, '.')

from src.core.watchlist_manager import (
    WatchlistManager, PoolPhase, WatchlistEntry, CandidateScore
)
from src.core.strategy import MarketData


class TestWatchlistManager(unittest.TestCase):
    """WatchlistManager 单元测试"""
    
    def setUp(self):
        self.wm = WatchlistManager()
    
    def test_initial_state(self):
        """测试初始状态"""
        self.assertEqual(self.wm.phase, PoolPhase.EMPTY)
        self.assertEqual(len(self.wm.watchlist), 0)
        self.assertEqual(len(self.wm.observation_list), 0)
        self.assertIsNone(self.wm.holding_symbol)
    
    def test_blacklist(self):
        """测试黑名单"""
        self.wm.add_to_blacklist("TEST")
        self.assertIn("TEST", self.wm.blacklist)
        
        self.wm.remove_from_blacklist("TEST")
        self.assertNotIn("TEST", self.wm.blacklist)
    
    def test_position_opened(self):
        """测试建仓回调"""
        self.assertTrue(self.wm.on_position_opened("AAPL"))
        self.assertEqual(self.wm.holding_symbol, "AAPL")
        self.assertEqual(self.wm.phase, PoolPhase.HOLDING)
        
        # 重复建仓应失败
        self.assertFalse(self.wm.on_position_opened("GOOGL"))
    
    def test_position_closed(self):
        """测试平仓回调"""
        self.wm.on_position_opened("AAPL")
        self.assertTrue(self.wm.on_position_closed("AAPL", pnl=100.0))
        
        self.assertIsNone(self.wm.holding_symbol)
        self.assertEqual(self.wm.phase, PoolPhase.EMPTY)
        self.assertEqual(len(self.wm.watchlist), 0)  # 池已清空
    
    def test_blacklist_removes_from_pools(self):
        """测试黑名单自动移除"""
        # 先添加一些数据到池中（通过mock）
        entry = WatchlistEntry(
            symbol="TEST",
            score=CandidateScore(symbol="TEST"),
            added_date=datetime.now(),
            last_updated=datetime.now(),
        )
        self.wm.watchlist.append(entry)
        
        self.wm.add_to_blacklist("TEST")
        self.assertNotIn("TEST", [e.symbol for e in self.wm.watchlist])
    
    def test_get_candidates_empty_phase(self):
        """空仓期返回标的池"""
        entry = WatchlistEntry(
            symbol="AAPL",
            score=CandidateScore(symbol="AAPL"),
            added_date=datetime.now(),
            last_updated=datetime.now(),
            in_watchlist=True,
        )
        self.wm.watchlist.append(entry)
        
        candidates = self.wm.get_candidates_for_signal()
        self.assertIn("AAPL", candidates)
    
    def test_get_candidates_holding_phase(self):
        """建仓期只返回持仓标的"""
        self.wm.on_position_opened("AAPL")
        self.wm.watchlist.append(WatchlistEntry(
            symbol="GOOGL",
            score=CandidateScore(symbol="GOOGL"),
            added_date=datetime.now(),
            last_updated=datetime.now(),
        ))
        
        candidates = self.wm.get_candidates_for_signal()
        self.assertEqual(candidates, ["AAPL"])
        self.assertNotIn("GOOGL", candidates)
    
    def test_get_status(self):
        """测试状态获取"""
        status = self.wm.get_status()
        self.assertEqual(status["phase"], "empty")
        self.assertEqual(status["watchlist_size"], 0)
        self.assertIsNone(status["holding_symbol"])


class TestCandidateScore(unittest.TestCase):
    """CandidateScore 测试"""
    
    def test_to_dict(self):
        """测试序列化"""
        score = CandidateScore(
            symbol="AAPL",
            total_score=5.0,
            ma200_slope=1.5,
            rsi=55.0,
        )
        d = score.to_dict()
        self.assertEqual(d["symbol"], "AAPL")
        self.assertEqual(d["total_score"], 5.0)


if __name__ == "__main__":
    unittest.main()