"""Unit tests for trend-following condition evaluators."""

import pytest
from unittest.mock import MagicMock
from src.core.strategy import MarketData, ConditionNode
from src.core.conditions.base import ConditionContext
from src.core.conditions.ma_stack import MAStackEvaluator
from src.core.conditions.sma_slope import SMASlopeEvaluator
from src.core.conditions.ma_spread import MASpreadEvaluator
from src.core.conditions.consolidation_breakout import ConsolidationBreakoutEvaluator
from src.core.conditions.retrace_breakout import RetraceBreakoutEvaluator
from src.core.conditions.fib_time import FibTimeEvaluator
from src.core.conditions.volume_spike import VolumeSpikeEvaluator
from src.core.conditions.ma_slope_turn import MASlopeTurnEvaluator


def _make_md(**kwargs) -> MarketData:
    defaults = {"symbol": "NVDA", "price": 150.0, "volume": 1000000}
    defaults.update(kwargs)
    return MarketData(**defaults)


def _make_ctx(md: MarketData) -> ConditionContext:
    return ConditionContext(
        symbol=md.symbol,
        market_price=md.price,
        avg_cost=0,
        market_data=[md],
    )


# =============================================================================
# ma_stack tests
# =============================================================================


class TestMAStack:
    def test_bullish_alignment(self):
        md = _make_md(price=150.0, ma_50=140.0, ma_200=130.0)
        node = ConditionNode(type="ma_stack", operator=">")
        assert MAStackEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_bearish_alignment(self):
        md = _make_md(price=100.0, ma_50=110.0, ma_200=120.0)
        node = ConditionNode(type="ma_stack", operator="<")
        assert MAStackEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_not_aligned(self):
        md = _make_md(price=150.0, ma_50=160.0, ma_200=130.0)
        node = ConditionNode(type="ma_stack", operator=">")
        assert MAStackEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_missing_ma200(self):
        md = _make_md(price=150.0, ma_50=140.0, ma_200=None)
        node = ConditionNode(type="ma_stack", operator=">")
        assert MAStackEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_default_operator_is_bullish(self):
        md = _make_md(price=150.0, ma_50=140.0, ma_200=130.0)
        node = ConditionNode(type="ma_stack")
        assert MAStackEvaluator().evaluate(node, _make_ctx(md)) is True


# =============================================================================
# sma_slope tests
# =============================================================================


class TestSMASlope:
    def test_slope_in_range(self):
        md = _make_md(ma_50_slope=25.0)
        node = ConditionNode(type="sma_slope", period=50, threshold=5.0, multiplier=45.0, operator=">")
        assert SMASlopeEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_slope_below_min(self):
        md = _make_md(ma_50_slope=2.0)
        node = ConditionNode(type="sma_slope", period=50, threshold=5.0, multiplier=45.0, operator=">")
        assert SMASlopeEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_slope_above_max(self):
        md = _make_md(ma_50_slope=50.0)
        node = ConditionNode(type="sma_slope", period=50, threshold=5.0, multiplier=45.0, operator=">")
        assert SMASlopeEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_ma200_slope(self):
        md = _make_md(ma_200_slope=8.0)
        node = ConditionNode(type="sma_slope", period=200, threshold=5.0, multiplier=45.0, operator=">")
        assert SMASlopeEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_slope_none(self):
        md = _make_md(ma_50_slope=None)
        node = ConditionNode(type="sma_slope", period=50, threshold=5.0)
        assert SMASlopeEvaluator().evaluate(node, _make_ctx(md)) is False


# =============================================================================
# ma_spread tests
# =============================================================================


class TestMASpread:
    def test_spread_below_threshold(self):
        md = _make_md(ma_spread_ratio=0.03)
        node = ConditionNode(type="ma_spread", operator="<", threshold=0.05)
        assert MASpreadEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_spread_above_threshold(self):
        md = _make_md(ma_spread_ratio=0.10)
        node = ConditionNode(type="ma_spread", operator="<", threshold=0.05)
        assert MASpreadEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_spread_none(self):
        md = _make_md(ma_spread_ratio=None)
        node = ConditionNode(type="ma_spread", operator="<", threshold=0.05)
        assert MASpreadEvaluator().evaluate(node, _make_ctx(md)) is False


# =============================================================================
# consolidation_breakout tests
# =============================================================================


class TestConsolidationBreakout:
    def test_breakout_after_consolidation(self):
        md = _make_md(is_consolidating=True, breakout_detected=True)
        node = ConditionNode(type="consolidation_breakout")
        assert ConsolidationBreakoutEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_consolidating_no_breakout(self):
        md = _make_md(is_consolidating=True, breakout_detected=False)
        node = ConditionNode(type="consolidation_breakout")
        assert ConsolidationBreakoutEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_not_consolidating(self):
        md = _make_md(is_consolidating=False, breakout_detected=True)
        node = ConditionNode(type="consolidation_breakout")
        assert ConsolidationBreakoutEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_fields_none(self):
        md = _make_md(is_consolidating=None, breakout_detected=None)
        node = ConditionNode(type="consolidation_breakout")
        assert ConsolidationBreakoutEvaluator().evaluate(node, _make_ctx(md)) is False


# =============================================================================
# retrace_breakout tests
# =============================================================================


class TestRetraceBreakout:
    def test_retrace_bounce(self):
        md = _make_md(price=152.0, ma_50=150.0, retrace_to_ma50=True, change_1d_pct=1.5)
        node = ConditionNode(type="retrace_breakout")
        assert RetraceBreakoutEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_retrace_but_below_ma50(self):
        md = _make_md(price=148.0, ma_50=150.0, retrace_to_ma50=True, change_1d_pct=1.0)
        ctx = ConditionContext(symbol="NVDA", market_price=148.0, avg_cost=0, market_data=[md])
        node = ConditionNode(type="retrace_breakout")
        assert RetraceBreakoutEvaluator().evaluate(node, ctx) is False

    def test_not_near_ma50(self):
        md = _make_md(price=170.0, ma_50=150.0, retrace_to_ma50=False, change_1d_pct=2.0)
        node = ConditionNode(type="retrace_breakout")
        assert RetraceBreakoutEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_retrace_negative_momentum(self):
        md = _make_md(price=151.0, ma_50=150.0, retrace_to_ma50=True, change_1d_pct=-0.5)
        node = ConditionNode(type="retrace_breakout")
        assert RetraceBreakoutEvaluator().evaluate(node, _make_ctx(md)) is False


# =============================================================================
# fib_time tests
# =============================================================================


class TestFibTime:
    def test_exact_fib_match(self):
        md = _make_md(consolidation_days=13)
        node = ConditionNode(type="fib_time", threshold=2)
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_within_tolerance(self):
        md = _make_md(consolidation_days=7)  # 8 - 1 = within tolerance of 2
        node = ConditionNode(type="fib_time", threshold=2)
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_outside_tolerance(self):
        md = _make_md(consolidation_days=17)  # between 13+2=15 and 21-2=19, 17 is within 21's range
        node = ConditionNode(type="fib_time", threshold=2)
        # 17 is within [21-2, 21+2] = [19,23]? No, 17 < 19. Check 13: [11,15], 17>15. So False.
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_zero_days(self):
        md = _make_md(consolidation_days=0)
        node = ConditionNode(type="fib_time", threshold=2)
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_none_days(self):
        md = _make_md(consolidation_days=None)
        node = ConditionNode(type="fib_time", threshold=2)
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is False


# =============================================================================
# volume_spike tests (parameterized multiplier)
# =============================================================================


class TestVolumeSpike:
    def test_spike_above_multiplier(self):
        md = _make_md(volume=2600000, volume_avg_20d=2000000.0)
        node = ConditionNode(type="volume_spike", multiplier=1.3)
        assert VolumeSpikeEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_spike_below_multiplier(self):
        md = _make_md(volume=2400000, volume_avg_20d=2000000.0)
        node = ConditionNode(type="volume_spike", multiplier=1.3)
        assert VolumeSpikeEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_default_multiplier_2x(self):
        md = _make_md(volume=4100000, volume_avg_20d=2000000.0)
        node = ConditionNode(type="volume_spike")
        assert VolumeSpikeEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_no_avg_volume(self):
        md = _make_md(volume=1000000, volume_avg_20d=None)
        node = ConditionNode(type="volume_spike", multiplier=1.3)
        assert VolumeSpikeEvaluator().evaluate(node, _make_ctx(md)) is False


# =============================================================================
# fib_time pullback mode tests
# =============================================================================


class TestFibTimePullback:
    def test_pullback_days_match_fib(self):
        md = _make_md(days_from_high=8)
        node = ConditionNode(type="fib_time", mode="pullback", threshold=2)
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_pullback_days_within_tolerance(self):
        md = _make_md(days_from_high=20)  # 21 - 1 = within tolerance of 2
        node = ConditionNode(type="fib_time", mode="pullback", threshold=2)
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_pullback_days_no_match(self):
        md = _make_md(days_from_high=17)
        node = ConditionNode(type="fib_time", mode="pullback", threshold=2)
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_pullback_zero_days(self):
        md = _make_md(days_from_high=0)
        node = ConditionNode(type="fib_time", mode="pullback", threshold=2)
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_pullback_none(self):
        md = _make_md(days_from_high=None)
        node = ConditionNode(type="fib_time", mode="pullback", threshold=2)
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_default_mode_is_consolidation(self):
        md = _make_md(consolidation_days=13, days_from_high=17)
        node = ConditionNode(type="fib_time", threshold=2)
        assert FibTimeEvaluator().evaluate(node, _make_ctx(md)) is True


# =============================================================================
# ma_slope_turn tests
# =============================================================================


class TestMASlopeTurn:
    def test_turn_detected(self):
        md = _make_md(ma_200_slope=2.5, ma_200_slope_prev=0.5)
        node = ConditionNode(type="ma_slope_turn", period=200, flat_threshold=1.0)
        assert MASlopeTurnEvaluator().evaluate(node, _make_ctx(md)) is True

    def test_already_positive_before(self):
        md = _make_md(ma_200_slope=3.0, ma_200_slope_prev=2.0)
        node = ConditionNode(type="ma_slope_turn", period=200, flat_threshold=1.0)
        assert MASlopeTurnEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_still_flat(self):
        md = _make_md(ma_200_slope=0.8, ma_200_slope_prev=-0.5)
        node = ConditionNode(type="ma_slope_turn", period=200, flat_threshold=1.0)
        assert MASlopeTurnEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_negative_to_flat(self):
        md = _make_md(ma_200_slope=0.5, ma_200_slope_prev=-2.0)
        node = ConditionNode(type="ma_slope_turn", period=200, flat_threshold=1.0)
        assert MASlopeTurnEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_none_slopes(self):
        md = _make_md(ma_200_slope=None, ma_200_slope_prev=None)
        node = ConditionNode(type="ma_slope_turn", period=200)
        assert MASlopeTurnEvaluator().evaluate(node, _make_ctx(md)) is False

    def test_custom_flat_threshold(self):
        md = _make_md(ma_200_slope=3.0, ma_200_slope_prev=1.8)
        node = ConditionNode(type="ma_slope_turn", period=200, flat_threshold=2.0)
        assert MASlopeTurnEvaluator().evaluate(node, _make_ctx(md)) is True
