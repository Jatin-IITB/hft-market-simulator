"""
Risk manager tests.

Critical tests:
- Position limits enforced
- Margin calls triggered correctly
- Loss limits work
- Edge cases handled
"""
import pytest
import time
from engine.trader import Trader, Fill, TradeSide
from engine.risk_manager import RiskManager


class TestPositionLimits:
    """Test position limit enforcement."""
    
    def test_can_add_within_limit(self):
        """Adding position within limit allowed."""
        risk = RiskManager(position_limit=10)
        trader = Trader("t1")
        trader._position = 5
        
        allowed, reason = risk.can_add_position(trader, 3)
        
        assert allowed is True
        assert reason is None
    
    def test_cannot_exceed_limit(self):
        """Adding position beyond limit blocked."""
        risk = RiskManager(position_limit=10)
        trader = Trader("t1")
        trader._position = 8
        
        allowed, reason = risk.can_add_position(trader, 5)  # Would be 13
        
        assert allowed is False
        assert "limit" in reason.lower()
    
    def test_negative_position_respects_limit(self):
        """Short positions also respect limit."""
        risk = RiskManager(position_limit=10)
        trader = Trader("t1")
        trader._position = -8
        
        allowed, reason = risk.can_reduce_position(trader, 5)  # Would be -13
        
        assert allowed is False
    
    def test_order_size_limit(self):
        """Individual order size limited."""
        risk = RiskManager(position_limit=100, max_order_size=5)
        trader = Trader("t1")
        
        allowed, reason = risk.can_add_position(trader, 10)
        
        assert allowed is False
        assert "size" in reason.lower()


class TestMarginCalls:
    """Test margin call logic."""
    
    def test_margin_call_triggered_below_threshold(self):
        """Margin call when P&L falls below threshold."""
        risk = RiskManager(margin_threshold=-50.0)
        trader = Trader("t1")
        
        # Create losing position
        trader.apply_fill(Fill(100.0, 10, TradeSide.BUY, time.time()))
        
        # Price drops to 95 → P&L = -1000 + 10*95 = -50 (at threshold)
        triggered = risk.check_margin_call(trader, fair_value=94.0, current_time=time.time())
        
        assert triggered is True
        assert trader.position == 0  # Liquidated
    
    def test_no_margin_call_above_threshold(self):
        """No margin call when P&L healthy."""
        risk = RiskManager(margin_threshold=-50.0)
        trader = Trader("t1")
        
        trader.apply_fill(Fill(100.0, 10, TradeSide.BUY, time.time()))
        
        # Price at 100 → P&L = 0 (above threshold)
        triggered = risk.check_margin_call(trader, fair_value=100.0)
        
        assert triggered is False
        assert trader.position == 10  # Not liquidated
    
    def test_liquidation_flattens_position(self):
        """Margin call flattens entire position."""
        risk = RiskManager(margin_threshold=-20.0)
        trader = Trader("t1")
        
        trader.apply_fill(Fill(100.0, 5, TradeSide.BUY, time.time()))
        
        # Trigger margin call
        risk.check_margin_call(trader, fair_value=90.0, current_time=time.time())
        
        assert trader.position == 0


class TestRiskMetrics:
    """Test risk metrics calculation."""
    
    def test_risk_metrics_complete(self):
        """All risk metrics calculated."""
        risk = RiskManager(position_limit=10, margin_threshold=-50.0)
        trader = Trader("t1")
        trader.apply_fill(Fill(100.0, 5, TradeSide.BUY, time.time()))
        
        metrics = risk.get_risk_metrics(trader, current_price=105.0)
        
        assert 'position' in metrics
        assert 'position_utilization' in metrics
        assert 'mtm_pnl' in metrics
        assert 'margin_cushion' in metrics
        assert 'var_95' in metrics
        assert 'at_risk' in metrics
    
    def test_position_utilization(self):
        """Position utilization calculated correctly."""
        risk = RiskManager(position_limit=10)
        trader = Trader("t1")
        trader._position = 5
        
        metrics = risk.get_risk_metrics(trader, current_price=100.0)
        
        assert metrics['position_utilization'] == pytest.approx(0.5)  # 5/10
    
    def test_at_risk_flag(self):
        """At-risk flag set when near margin call."""
        risk = RiskManager(margin_threshold=-50.0)
        trader = Trader("t1")
        trader.apply_fill(Fill(100.0, 10, TradeSide.BUY, time.time()))
        
        # P&L = -1000 + 10*96 = -40 (within 20% of -50 threshold)
        metrics = risk.get_risk_metrics(trader, current_price=96.0)
        
        assert metrics['at_risk'] is True
