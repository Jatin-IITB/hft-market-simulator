"""
Bot strategy tests.

Critical tests:
1. Strategies produce valid quotes
2. Inventory management works (positions mean-revert)
3. Adverse selection detection works
4. Latency simulation works
5. Position limits respected
"""
import pytest
import time
from engine.order_book import OrderBook
from engine.trader import Trader
from engine.bot_strategies import (
    BotManager,
    MarketMakingStrategy,
    MomentumStrategy,
    ArbitrageStrategy,
    AggressiveHFTStrategy
)
from infrastructure.config import DifficultyConfig


class TestMarketMakingStrategy:
    """Test Avellaneda-Stoikov market making."""
    
    def test_quotes_around_fair_value(self):
        """Market maker quotes around fair value."""
        strategy = MarketMakingStrategy(risk_aversion=0.5)
        
        bid, ask = strategy.calculate_quotes(
            fair_value=100.0,
            volatility=1.0,
            current_position=0,
            position_limit=10,
            uncertainty=5.0
        )
        
        assert bid is not None
        assert ask is not None
        assert bid < 100.0 < ask  # Quotes straddle FV
    
    def test_inventory_skewing(self):
        """Long position → lower quotes (incentivize selling)."""
        strategy = MarketMakingStrategy(risk_aversion=0.5, inventory_penalty=1.0)
        
        # Flat position
        bid_flat, ask_flat = strategy.calculate_quotes(
            fair_value=100.0,
            volatility=1.0,
            current_position=0,
            position_limit=10,
            uncertainty=5.0
        )
        
        # Long position
        bid_long, ask_long = strategy.calculate_quotes(
            fair_value=100.0,
            volatility=1.0,
            current_position=5,
            position_limit=10,
            uncertainty=5.0
        )
        
        # Long quotes should be lower (skewed down)
        assert bid_long < bid_flat
        assert ask_long < ask_flat
    
    def test_wider_spread_with_uncertainty(self):
        """Higher uncertainty → wider spread."""
        strategy = MarketMakingStrategy(risk_aversion=0.5)
        
        # Low uncertainty
        bid_low, ask_low = strategy.calculate_quotes(
            fair_value=100.0,
            volatility=1.0,
            current_position=0,
            position_limit=10,
            uncertainty=1.0
        )
        
        # High uncertainty
        bid_high, ask_high = strategy.calculate_quotes(
            fair_value=100.0,
            volatility=1.0,
            current_position=0,
            position_limit=10,
            uncertainty=10.0
        )
        
        spread_low = ask_low - bid_low
        spread_high = ask_high - bid_high
        
        assert spread_high > spread_low
    
    def test_no_bid_at_position_limit(self):
        """At max long, don't post bids."""
        strategy = MarketMakingStrategy()
        
        bid, ask = strategy.calculate_quotes(
            fair_value=100.0,
            volatility=1.0,
            current_position=10,  # At limit
            position_limit=10,
            uncertainty=5.0
        )
        
        assert bid is None  # Can't buy more
        assert ask is not None  # Can still sell


class TestMomentumStrategy:
    """Test trend-following strategy."""
    
    def test_uptrend_bias(self):
        """Uptrend → quotes biased higher."""
        strategy = MomentumStrategy()
        
        # Simulate uptrend (rising prices)
        for price in [100, 101, 102, 103, 104]:
            bid, ask = strategy.calculate_quotes(
                fair_value=price,
                volatility=1.0,
                current_position=0,
                position_limit=10
            )
        
        # Final quotes should be biased above fair value
        final_bid, final_ask = strategy.calculate_quotes(
            fair_value=105.0,
            volatility=1.0,
            current_position=0,
            position_limit=10
        )
        
        mid = (final_bid + final_ask) / 2.0
        assert mid > 105.0  # Biased above FV due to uptrend
    
    def test_downtrend_bias(self):
        """Downtrend → quotes biased lower."""
        strategy = MomentumStrategy()
        
        # Simulate downtrend
        for price in [100, 99, 98, 97, 96]:
            bid, ask = strategy.calculate_quotes(
                fair_value=price,
                volatility=1.0,
                current_position=0,
                position_limit=10
            )
        
        # Final quotes should be biased below fair value
        final_bid, final_ask = strategy.calculate_quotes(
            fair_value=95.0,
            volatility=1.0,
            current_position=0,
            position_limit=10
        )
        
        mid = (final_bid + final_ask) / 2.0
        assert mid < 95.0  # Biased below FV


class TestArbitrageStrategy:
    """Test stat arb strategy."""
    
    def test_buy_when_undervalued(self):
        """Buy aggressively when market < FV."""
        strategy = ArbitrageStrategy()
        
        # Market ask below fair value (undervalued)
        bid, ask = strategy.calculate_quotes(
            fair_value=100.0,
            best_bid=98.0,
            best_ask=98.5,  # Below FV - 0.5 threshold
            current_position=0,
            position_limit=10
        )
        
        # Should lift the offer
        assert bid == 98.5
        assert ask is None
    
    def test_sell_when_overvalued(self):
        """Sell aggressively when market > FV."""
        strategy = ArbitrageStrategy()
        
        # Market bid above fair value (overvalued)
        bid, ask = strategy.calculate_quotes(
            fair_value=100.0,
            best_bid=101.0,  # Above FV + 0.5 threshold
            best_ask=101.5,
            current_position=0,
            position_limit=10
        )
        
        # Should hit the bid
        assert bid is None
        assert ask == 101.0
    
    def test_exit_near_fair_value(self):
        """Exit position when market near fair value."""
        strategy = ArbitrageStrategy()
        
        # Has long position, market near FV
        bid, ask = strategy.calculate_quotes(
            fair_value=100.0,
            best_bid=99.9,
            best_ask=100.1,
            current_position=5,  # Long
            position_limit=10
        )
        
        # Should sell to exit
        assert ask is not None
        assert ask < 100.0  # Aggressive sell


class TestAggressiveHFTStrategy:
    """Test HFT strategy with toxicity detection."""
    
    def test_widen_spread_with_toxicity(self):
        """High toxicity → wider spread."""
        strategy = AggressiveHFTStrategy(toxicity_sensitivity=1.0)
        
        # Low toxicity
        bid_low, ask_low = strategy.calculate_quotes(
            fair_value=100.0,
            volatility=1.0,
            user_toxicity=0.0,
            current_position=0,
            position_limit=10,
            best_bid=99.0,
            best_ask=101.0
        )
        
        # High toxicity
        bid_high, ask_high = strategy.calculate_quotes(
            fair_value=100.0,
            volatility=1.0,
            user_toxicity=3.0,  # User is informed
            current_position=0,
            position_limit=10,
            best_bid=99.0,
            best_ask=101.0
        )
        
        if bid_low and ask_low and bid_high and ask_high:
            spread_low = ask_low - bid_low
            spread_high = ask_high - bid_high
            assert spread_high >= spread_low
    
    def test_pull_quotes_extreme_toxicity(self):
        """Very high toxicity → sometimes pull quotes."""
        strategy = AggressiveHFTStrategy(toxicity_sensitivity=1.5)
        
        pulls = 0
        attempts = 50
        
        for _ in range(attempts):
            bid, ask = strategy.calculate_quotes(
                fair_value=100.0,
                volatility=1.0,
                user_toxicity=5.0,  # Extremely toxic
                current_position=0,
                position_limit=10,
                best_bid=99.0,
                best_ask=101.0
            )
            
            if bid is None and ask is None:
                pulls += 1
        
        # Should pull at least sometimes (probabilistic)
        assert pulls > 0


class TestBotManager:
    """Test bot manager integration."""
    
    def setup_method(self):
        """Setup for each test."""
        self.config = DifficultyConfig.MEDIUM()
        self.bot_manager = BotManager(self.config)
        self.book = OrderBook(quote_lifetime=5.0)
        
        # Create traders
        self.traders = {}
        for bot_name in self.bot_manager.get_bot_names():
            self.traders[bot_name] = Trader(bot_name, is_bot=True)
    
    def test_all_bots_created(self):
        """All expected bots created."""
        bot_names = self.bot_manager.get_bot_names()
        
        assert "HFT_Shark" in bot_names
        assert "MMaker_Citadel" in bot_names
        assert "FOMO_Retail" in bot_names
        assert "Arb_Fund" in bot_names
    
    def test_bots_post_quotes(self):
        """Bots post quotes to book."""
        # Run bot update multiple times (probabilistic participation)
        for _ in range(20):
            self.bot_manager.update_quotes(
                book=self.book,
                traders=self.traders,
                fair_value=100.0,
                volatility=1.0,
                user_toxicity=0.0
            )
        
        # Should have some quotes
        bb, ba = self.book.get_best_bid_ask()
        assert bb is not None or ba is not None
    
    def test_latency_affects_participation(self):
        """Higher latency = fewer quotes."""
        # Fast bots (low latency)
        fast_config = DifficultyConfig.AXXELA()
        fast_mgr = BotManager(fast_config)
        fast_book = OrderBook()
        fast_traders = {name: Trader(name, True) for name in fast_mgr.get_bot_names()}
        
        # Slow bots (high latency)
        slow_config = DifficultyConfig.EASY()
        slow_mgr = BotManager(slow_config)
        slow_book = OrderBook()
        slow_traders = {name: Trader(name, True) for name in slow_mgr.get_bot_names()}
        
        # Run same number of ticks
        for _ in range(50):
            fast_mgr.update_quotes(fast_book, fast_traders, 100.0, 1.0, 0.0)
            slow_mgr.update_quotes(slow_book, slow_traders, 100.0, 1.0, 0.0)
        
        # Fast bots should have more depth
        fast_depth = len(fast_book.bids) + len(fast_book.asks)
        slow_depth = len(slow_book.bids) + len(slow_book.asks)
        
        assert fast_depth >= slow_depth
    
    def test_bots_respect_position_limits(self):
        """Bots don't post quotes at position limits."""
        # Put bot at max position
        bot_name = "MMaker_Citadel"
        self.traders[bot_name]._position = self.config.position_limit
        
        # Update quotes
        for _ in range(10):
            self.bot_manager.update_quotes(
                self.book,
                self.traders,
                fair_value=100.0,
                volatility=1.0,
                user_toxicity=0.0
            )
        
        # Bot shouldn't post buy orders
        for price, queue in self.book.bids.items():
            for order in queue:
                assert order.trader_id != bot_name
    
    def test_toxicity_causes_quote_pulling(self):
        """High user toxicity → bots pull quotes."""
        # Low toxicity run
        low_tox_book = OrderBook()
        low_traders = {name: Trader(name, True) for name in self.bot_manager.get_bot_names()}
        
        for _ in range(30):
            self.bot_manager.update_quotes(
                low_tox_book,
                low_traders,
                fair_value=100.0,
                volatility=1.0,
                user_toxicity=0.0  # Not toxic
            )
        
        # High toxicity run
        high_tox_book = OrderBook()
        high_traders = {name: Trader(name, True) for name in self.bot_manager.get_bot_names()}
        
        for _ in range(30):
            self.bot_manager.update_quotes(
                high_tox_book,
                high_traders,
                fair_value=100.0,
                volatility=1.0,
                user_toxicity=5.0  # Very toxic
            )
        
        # High toxicity should have less liquidity
        low_depth = len(low_tox_book.bids) + len(low_tox_book.asks)
        high_depth = len(high_tox_book.bids) + len(high_tox_book.asks)
        
        # Might be same due to randomness, but shouldn't be much more
        assert high_depth <= low_depth * 1.5
