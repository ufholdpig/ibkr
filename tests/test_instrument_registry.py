"""回归测试: InstrumentRegistry + create_contract 兼容性 + risk_engine notional

运行方式:
  python3 tests/test_instrument_registry.py          (无需安装 pytest)
  python3 -m pytest tests/test_instrument_registry.py (如已安装 pytest)
"""

import sys
import types
from unittest.mock import MagicMock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── 依赖 Mock (ibapi / yaml / exchange_calendars / yfinance) ────────
def _setup_mocks():
    """在 import 项目模块前设置所有外部依赖的 mock"""
    # yaml: 使用真实 PyYAML 如果可用，否则提供简易解析器
    try:
        import yaml as _y  # noqa: F401
    except ImportError:
        class _FakeYaml:
            @staticmethod
            def safe_load(f):
                content = f.read() if hasattr(f, "read") else f
                result = {}
                if "instruments:" in content:
                    result["instruments"] = {}
                    current_sym = None
                    for line in content.split("\n"):
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#"):
                            continue
                        indent = len(line) - len(line.lstrip())
                        if indent == 2 and ":" in stripped and not stripped.startswith("-"):
                            key = stripped.split(":")[0].strip()
                            if key != "instruments":
                                current_sym = key
                                result["instruments"][current_sym] = {}
                        elif indent == 4 and current_sym and ":" in stripped:
                            parts = stripped.split(":", 1)
                            k = parts[0].strip()
                            v = parts[1].strip().strip('"')
                            if v.isdigit():
                                v = int(v)
                            result["instruments"][current_sym][k] = v
                elif "ibkr:" in content:
                    result["ibkr"] = {"market_data_source": "auto", "watch": {"templates": {}}}
                return result
        sys.modules.setdefault("yaml", _FakeYaml())

    # ibapi
    ibapi_mock = types.ModuleType("ibapi")
    contract_mod = types.ModuleType("ibapi.contract")
    client_mod = types.ModuleType("ibapi.client")
    wrapper_mod = types.ModuleType("ibapi.wrapper")
    order_mod = types.ModuleType("ibapi.order")
    common_mod = types.ModuleType("ibapi.common")
    tags_mod = types.ModuleType("ibapi.account_summary_tags")

    class MockContract:
        def __init__(self):
            self.symbol = ""
            self.secType = ""
            self.exchange = ""
            self.currency = ""
            self.lastTradeDateOrContractMonth = ""
            self.multiplier = ""
            self.tradingClass = ""

    class MockEClient:
        def __init__(self, *a, **kw): pass

    class MockEWrapper:
        pass

    class MockAccountSummaryTags:
        AllTags = "AccountType"

    contract_mod.Contract = MockContract
    client_mod.EClient = MockEClient
    wrapper_mod.EWrapper = MockEWrapper
    tags_mod.AccountSummaryTags = MockAccountSummaryTags

    for name, mod in [
        ("ibapi", ibapi_mock), ("ibapi.contract", contract_mod),
        ("ibapi.client", client_mod), ("ibapi.wrapper", wrapper_mod),
        ("ibapi.order", order_mod), ("ibapi.common", common_mod),
        ("ibapi.account_summary_tags", tags_mod),
    ]:
        sys.modules.setdefault(name, mod)

    sys.modules.setdefault("exchange_calendars", MagicMock())
    sys.modules.setdefault("yfinance", MagicMock())


_setup_mocks()

from config.config import InstrumentSpec, InstrumentRegistry, get_instrument_registry  # noqa: E402


# ── InstrumentRegistry 基础测试 ─────────────────────────────────────

class TestInstrumentRegistry:
    def test_unknown_symbol_returns_stk_default(self):
        """未注册 symbol 返回 STK 默认值"""
        registry = InstrumentRegistry()
        spec = registry.get("NVDA")
        assert spec.symbol == "NVDA"
        assert spec.sec_type == "STK"
        assert spec.exchange == "SMART"
        assert spec.currency == "USD"
        assert spec.multiplier == 1
        assert spec.is_futures is False

    def test_load_instruments_yaml(self):
        """从 instruments.yaml 加载期货定义"""
        project_root = Path(__file__).parent.parent
        yaml_path = project_root / "config" / "instruments.yaml"
        registry = InstrumentRegistry(str(yaml_path))

        es = registry.get("ES")
        assert es.sec_type == "FUT"
        assert es.exchange == "CME"
        assert es.multiplier == 50
        assert es.trading_class == "ES"
        assert es.yfinance_symbol == "ES=F"
        assert es.is_futures is True

    def test_notional_multiplier_stk(self):
        """STK multiplier 始终为 1"""
        registry = InstrumentRegistry()
        spec = registry.get("AAPL")
        assert spec.notional_multiplier == 1

    def test_notional_multiplier_futures(self):
        """FUT multiplier 使用注册值"""
        project_root = Path(__file__).parent.parent
        yaml_path = project_root / "config" / "instruments.yaml"
        registry = InstrumentRegistry(str(yaml_path))
        assert registry.get("MES").notional_multiplier == 5
        assert registry.get("NQ").notional_multiplier == 20
        assert registry.get("MNQ").notional_multiplier == 2

    def test_futures_symbols_list(self):
        """futures_symbols 属性正确返回期货列表"""
        project_root = Path(__file__).parent.parent
        yaml_path = project_root / "config" / "instruments.yaml"
        registry = InstrumentRegistry(str(yaml_path))
        futs = registry.futures_symbols
        assert "ES" in futs
        assert "NQ" in futs
        assert "MES" in futs
        assert "MNQ" in futs

    def test_case_insensitive_lookup(self):
        """符号查找不区分大小写"""
        project_root = Path(__file__).parent.parent
        yaml_path = project_root / "config" / "instruments.yaml"
        registry = InstrumentRegistry(str(yaml_path))
        assert registry.get("es").sec_type == "FUT"
        assert registry.get("Es").sec_type == "FUT"


# ── create_contract 兼容性测试 ──────────────────────────────────────

class TestCreateContract:
    def test_stk_contract_unchanged(self):
        """股票合约创建行为不变"""
        from src.core.client import create_contract
        c = create_contract("NVDA")
        assert c.symbol == "NVDA"
        assert c.secType == "STK"
        assert c.exchange == "SMART"
        assert c.currency == "USD"
        assert c.lastTradeDateOrContractMonth == ""
        assert c.multiplier == ""

    def test_futures_contract_with_params(self):
        """期货合约传入可选参数"""
        from src.core.client import create_contract
        c = create_contract(
            "ES", sec_type="FUT", exchange="CME", currency="USD",
            expiry="202506", multiplier="50", trading_class="ES"
        )
        assert c.symbol == "ES"
        assert c.secType == "FUT"
        assert c.exchange == "CME"
        assert c.lastTradeDateOrContractMonth == "202506"
        assert c.multiplier == "50"
        assert c.tradingClass == "ES"

    def test_canadian_stock_still_works(self):
        """RY.TO 格式依然正确解析"""
        from src.core.client import create_contract
        c = create_contract("RY.TO")
        assert c.symbol == "RY"
        assert c.exchange == "TSE"
        assert c.currency == "CAD"
        assert c.secType == "STK"


# ── Risk Engine notional 回归测试 ────────────────────────────────────

class TestRiskEngineNotional:
    def setup_method(self):
        import config.config as cfg_mod
        project_root = Path(__file__).parent.parent
        yaml_path = project_root / "config" / "instruments.yaml"
        cfg_mod._INSTRUMENT_REGISTRY = InstrumentRegistry(str(yaml_path))

    def teardown_method(self):
        import config.config as cfg_mod
        cfg_mod._INSTRUMENT_REGISTRY = None

    def test_stock_notional_unchanged(self):
        """股票 notional = qty * price (multiplier=1)"""
        from src.core.risk_engine import RiskEngine
        engine = RiskEngine()
        notional = engine._get_notional_value("NVDA", 10, 150.0)
        assert notional == 10 * 150.0

    def test_futures_notional_uses_multiplier(self):
        """期货 notional = qty * price * multiplier"""
        from src.core.risk_engine import RiskEngine
        engine = RiskEngine()
        notional = engine._get_notional_value("ES", 1, 5400.0)
        assert notional == 1 * 5400.0 * 50

    def test_futures_skip_tfsa_rules(self):
        """期货跳过 TFSA 特有规则（short_sell, day_trading, yearly_limit）"""
        from src.core.risk_engine import RiskEngine, RiskDecision
        from config.config import RiskConfig
        config = RiskConfig(
            forbid_short_sell=True,
            forbid_day_trading=True,
            max_trades_per_year=80,
            position_limit_pct=0,
            max_order_value_pct=0,
        )
        engine = RiskEngine(config)
        engine._net_liquidation = 100000
        engine._is_paper = False
        engine._trades_loaded = True

        results = engine.precheck_order("ES", "SELL", 1, price=0)
        assert all(r.is_allowed() for r in results)
        assert len(results) == 0

    def test_stock_still_enforces_tfsa(self):
        """股票仍然执行 TFSA 检查"""
        from src.core.risk_engine import RiskEngine
        from config.config import RiskConfig
        config = RiskConfig(
            forbid_short_sell=True,
            forbid_day_trading=True,
            max_trades_per_year=80,
            position_limit_pct=0,
            max_order_value_pct=0,
        )
        engine = RiskEngine(config)
        engine._net_liquidation = 100000
        engine._is_paper = False
        engine._trades_loaded = True

        results = engine.precheck_order("NVDA", "BUY", 10, price=0)
        rule_names = [r.rule_name for r in results]
        assert "SHORT_SELL" in rule_names
        assert "YEARLY_TRADE_LIMIT" in rule_names
        assert "DAY_TRADING" in rule_names


if __name__ == "__main__":
    import traceback
    passed = 0
    failed = 0
    for cls in [TestInstrumentRegistry, TestCreateContract, TestRiskEngineNotional]:
        inst = cls()
        for name in dir(inst):
            if not name.startswith("test_"):
                continue
            if hasattr(inst, "setup_method"):
                inst.setup_method()
            try:
                getattr(inst, name)()
                print(f"  PASS: {cls.__name__}.{name}")
                passed += 1
            except Exception as e:
                print(f"  FAIL: {cls.__name__}.{name} — {e}")
                traceback.print_exc()
                failed += 1
            finally:
                if hasattr(inst, "teardown_method"):
                    inst.teardown_method()
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
