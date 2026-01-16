"""
Comprehensive trader tests.

Coverage:
- Fill application and state tracking
- P&L calculations (MTM, realized, unrealized)
- VWAP calculation
- Adverse selection tracking
- Performance metrics
- Edge cases
"""
import pytest
import time
from engine.trader import Trader, Fill, TradeSide


class TestTraderBasics:
    """Test fundamental trader operations."""
    
    def setup_method(self):
        self.trader = Trader("test_trader", is_bot=False)
    
    def test_initialization(self):
        """Trader initializes with zero state."""
        assert self.trader.position == 0
        assert self.trader.cash == 0.0
        assert self.trader.fees_paid == 0.0
        assert self.trader.num_fills == 0
    
    def test_apply_buy_fill(self):
        """Buy fill increases position, decreases cash."""
        fill = Fill(price=100.0, quantity=5, side=TradeSide.BUY, timestamp=time.time())
        self.trader.apply_fill(fill)
        
        assert self.trader.position == 5
        assert self.trader.cash == -500.0  # Paid 5 * 100
        assert self.trader.num_fills == 1
    
    def test_apply_sell_fill(self):
        """Sell fill decreases position, increases cash."""
        fill = Fill(price=100.0, quantity=3, side=TradeSide.SELL, timestamp=time.time())
        self.trader.apply_fill(fill)
        
        assert self.trader.position == -3
        assert self.trader.cash == 300.0  # Received 3 * 100
        assert self.trader.num_fills == 1
    
    def test_fees_deducted(self):
        """Fees are deducted from cash."""
        fill = Fill(price=100.0, quantity=1, side=TradeSide.BUY, timestamp=time.time(), fee=2.0)
        self.trader.apply_fill(fill)
        
        assert self.trader.cash == -102.0  # -100 (trade) - 2 (fee)
        assert self.trader.fees_paid == 2.0
    
    def test_multiple_fills_accumulate(self):
        """Multiple fills correctly accumulate."""
        self.trader.apply_fill(Fill(100.0, 2, TradeSide.BUY, time.time()))
        self.trader.apply_fill(Fill(105.0, 3, TradeSide.BUY, time.time()))
        
        assert self.trader.position == 5  # 2 + 3
        assert self.trader.cash == -515.0  # -(2*100 + 3*105)
        assert self.trader.num_fills == 2


class TestPnLCalculations:
    """Test P&L calculation correctness."""
    
    def setup_method(self):
        self.trader = Trader("test_trader", is_bot=False)
    
    def test_mtm_pnl_long_position_profit(self):
        """Long position profits when price rises."""
        # Buy at 100
        self.trader.apply_fill(Fill(100.0, 10, TradeSide.BUY, time.time()))
        
        # Mark at 110 (price rose)
        pnl = self.trader.mark_to_market(110.0)
        
        # P&L = cash + position * mark
        # = -1000 + 10 * 110
        # = -1000 + 1100
        # = 100
        assert pnl == pytest.approx(100.0)
    
    def test_mtm_pnl_long_position_loss(self):
        """Long position loses when price falls."""
        self.trader.apply_fill(Fill(100.0, 10, TradeSide.BUY, time.time()))
        
        # Mark at 90 (price fell)
        pnl = self.trader.mark_to_market(90.0)
        
        # P&L = -1000 + 10 * 90 = -100
        assert pnl == pytest.approx(-100.0)
    
    def test_mtm_pnl_short_position_profit(self):
        """Short position profits when price falls."""
        # Sell at 100
        self.trader.apply_fill(Fill(100.0, 10, TradeSide.SELL, time.time()))
        
        # Mark at 90 (price fell)
        pnl = self.trader.mark_to_market(90.0)
        
        # P&L = 1000 + (-10) * 90 = 1000 - 900 = 100
        assert pnl == pytest.approx(100.0)
    
    def test_mtm_includes_fees(self):
        """P&L includes fees paid."""
        fill = Fill(100.0, 10, TradeSide.BUY, time.time(), fee=5.0)
        self.trader.apply_fill(fill)
        
        # Break-even price should be 100, but we paid fees
        pnl = self.trader.mark_to_market(100.0)
        
        # P&L = -1000 - 5 (fees) + 10 * 100 = -5
        assert pnl == pytest.approx(-5.0)
    
    def test_flat_position_pnl_is_realized(self):
        """Flat position P&L = realized P&L."""
        # Buy at 100
        self.trader.apply_fill(Fill(100.0, 5, TradeSide.BUY, time.time()))
        
        # Sell at 110
        self.trader.apply_fill(Fill(110.0, 5, TradeSide.SELL, time.time()))
        
        assert self.trader.position == 0  # Flat
        
        # P&L = realized profit
        pnl = self.trader.mark_to_market(105.0)  # Current price doesn't matter
        
        # Profit = (110 - 100) * 5 = 50
        assert pnl == pytest.approx(50.0)


class TestVWAP:
    """Test VWAP calculation."""
    
    def test_vwap_single_fill(self):
        """VWAP of single fill is fill price."""
        trader = Trader("t1")
        trader.apply_fill(Fill(100.0, 5, TradeSide.BUY, time.time()))
        
        assert trader.calculate_vwap() == pytest.approx(100.0)
    
    def test_vwap_multiple_fills(self):
        """VWAP is volume-weighted average."""
        trader = Trader("t1")
        
        # Buy 10 @ 100 (notional = 1000)
        trader.apply_fill(Fill(100.0, 10, TradeSide.BUY, time.time()))
        
        # Buy 5 @ 110 (notional = 550)
        trader.apply_fill(Fill(110.0, 5, TradeSide.BUY, time.time()))
        
        # VWAP = (1000 + 550) / (10 + 5) = 1550 / 15 = 103.33
        assert trader.calculate_vwap() == pytest.approx(103.33, rel=1e-2)
    
    def test_vwap_includes_both_sides(self):
        """VWAP includes both buys and sells."""
        trader = Trader("t1")
        
        trader.apply_fill(Fill(100.0, 10, TradeSide.BUY, time.time()))
        trader.apply_fill(Fill(105.0, 10, TradeSide.SELL, time.time()))
        
        # Total notional = 1000 + 1050 = 2050
        # Total quantity = 10 + 10 = 20
        # VWAP = 2050 / 20 = 102.5
        assert trader.calculate_vwap() == pytest.approx(102.5)
    
    def test_vwap_zero_fills(self):
        """VWAP is 0 when no fills."""
        trader = Trader("t1")
        assert trader.calculate_vwap() == 0.0


class TestAdverseSelection:
    """Test adverse selection tracking."""
    
    def test_favorable_trade_positive_score(self):
        """Buying below FV increases score."""
        trader = Trader("t1")
        
        # Bought at 100, FV is 110 (good trade!)
        trader.update_adverse_selection(fill_price=100.0, fair_value=110.0, is_buyer=True)
        
        # Score should be positive
        assert trader.adverse_selection_score > 0
    
    def test_adverse_trade_negative_score(self):
        """Buying above FV decreases score."""
        trader = Trader("t1")
        
        # Bought at 110, FV is 100 (bad trade!)
        trader.update_adverse_selection(fill_price=110.0, fair_value=100.0, is_buyer=True)
        
        # Score should be negative
        assert trader.adverse_selection_score < 0
    
    def test_ema_smoothing(self):
        """Score uses exponential moving average."""
        trader = Trader("t1")
        
        # Series of adverse trades
        for _ in range(10):
            trader.update_adverse_selection(fill_price=110.0, fair_value=100.0, is_buyer=True)
        
        score_after_10 = trader.adverse_selection_score
        
        # One more adverse trade
        trader.update_adverse_selection(fill_price=110.0, fair_value=100.0, is_buyer=True)
        
        score_after_11 = trader.adverse_selection_score
        
        # Score should have changed, but not by full -10 (due to EMA)
        assert score_after_11 != score_after_10
        assert abs(score_after_11 - score_after_10) < 10


class TestPerformanceMetrics:
    """Test comprehensive metrics."""
    
    def test_sharpe_ratio_calculation(self):
        """Sharpe ratio calculated from fill history."""
        trader = Trader("t1")
        
        # Series of profitable fills
        for i in range(10):
            trader.apply_fill(Fill(100.0 - i, 1, TradeSide.BUY, time.time()))
        
        sharpe = trader.calculate_sharpe_ratio(current_price=110.0)
        
        # Should be positive (profitable trades)
        assert sharpe > 0
    
    def test_return_percentage(self):
        """Return calculated as percentage."""
        trader = Trader("t1")
        
        # Buy at 100, mark at 110
        trader.apply_fill(Fill(100.0, 10, TradeSide.BUY, time.time()))
        
        return_pct = trader.calculate_return(current_price=110.0, initial_capital=1000.0)
        
        # P&L = 100, return = 100/1000 = 10%
        assert return_pct == pytest.approx(10.0)
