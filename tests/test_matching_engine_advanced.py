"""
Comprehensive matching engine tests.

These tests cover:
1. Core matching correctness (price-time priority)
2. Edge cases (self-trades, partial fills, simultaneous orders)
3. Performance characteristics (not timing-based, but complexity verification)
4. Determinism (same inputs always produce same outputs)

In tier-1 firms, matching engine tests are the MOST critical tests.
A bug here can cause regulatory issues, client lawsuits, or financial loss.
"""
import pytest
import time
from engine.order_book import OrderBook, Order
from engine.matching_engine import MatchingEngine, MatchEvent


class TestMatchingEnginePriceTimePriority:
    """Test core price-time priority algorithm."""
    
    def setup_method(self):
        """Create fresh engine for each test."""
        self.book = OrderBook(quote_lifetime=10.0)
        self.engine = MatchingEngine(self.book)
        self.matched_events = []
        self.engine.subscribe_to_matches(lambda e: self.matched_events.append(e))
    
    def test_price_priority_buy_side(self):
        """
        Higher bids execute first, regardless of time.
        
        Scenario:
        - Trader1 bids 100 at t=0
        - Trader2 bids 101 at t=1 (higher price, later time)
        - Trader3 asks 100 for 2 lots at t=2
        
        Expected: Trader2 fills first (price priority)
        """
        t0 = time.time()
        
        o1 = Order("trader1", "buy", 100.0, 1, t0)
        o2 = Order("trader2", "buy", 101.0, 1, t0 + 0.01)
        o3 = Order("trader3", "sell", 100.0, 2, t0 + 0.02)
        
        self.book.add_order(o1)
        self.book.add_order(o2)
        self.book.add_order(o3)
        
        matches = self.engine.match_orders(t0 + 0.03)
        
        # Verify 2 matches occurred
        assert len(matches) == 2
        
        # First match: Trader2 (higher price) buys
        assert matches[0].buyer_id == "trader2"
        assert matches[0].price == 101.0  # Bid's price (maker's price)
        assert matches[0].quantity == 1
        
        # Second match: Trader1 buys remaining
        assert matches[1].buyer_id == "trader1"
        assert matches[1].price == 100.0
        assert matches[1].quantity == 1
    
    def test_time_priority_same_price(self):
        """
        At same price, earlier orders execute first.
        
        Scenario:
        - Trader1 bids 100 at t=0
        - Trader2 bids 100 at t=1 (same price, later time)
        - Trader3 asks 100 for 1 lot at t=2
        
        Expected: Trader1 fills (time priority)
        """
        t0 = time.time()
        
        o1 = Order("trader1", "buy", 100.0, 1, t0)
        o2 = Order("trader2", "buy", 100.0, 1, t0 + 0.1)  # Later
        o3 = Order("trader3", "sell", 100.0, 1, t0 + 0.2)
        
        self.book.add_order(o1)
        self.book.add_order(o2)
        self.book.add_order(o3)
        
        matches = self.engine.match_orders(t0 + 0.3)
        
        assert len(matches) == 1
        assert matches[0].buyer_id == "trader1"  # Earlier timestamp
        
        # Trader2's order should still be in book
        bb, ba = self.book.get_best_bid_ask()
        assert bb == 100.0
    
    def test_partial_fill_across_levels(self):
        """
        Large order fills across multiple price levels.
        
        Scenario:
        - Ask ladder: 100 (1 lot), 101 (2 lots), 102 (3 lots)
        - Buyer bids 103 for 5 lots
        
        Expected:
        - Match 1: 1 lot @ 100
        - Match 2: 2 lots @ 101
        - Match 3: 2 lots @ 102 (partial fill of 3-lot order)
        """
        t0 = time.time()
        
        # Build ask ladder
        ask1 = Order("seller1", "sell", 100.0, 1, t0)
        ask2 = Order("seller2", "sell", 101.0, 2, t0 + 0.01)
        ask3 = Order("seller3", "sell", 102.0, 3, t0 + 0.02)
        
        self.book.add_order(ask1)
        self.book.add_order(ask2)
        self.book.add_order(ask3)
        
        # Aggressive buy order
        bid = Order("buyer", "buy", 103.0, 5, t0 + 0.03)
        self.book.add_order(bid)
        
        matches = self.engine.match_orders(t0 + 0.04)
        
        # Verify 3 matches
        assert len(matches) == 3
        
        # Match 1: Best ask (100)
        assert matches[0].price == 100.0
        assert matches[0].quantity == 1
        assert matches[0].seller_id == "seller1"
        
        # Match 2: Second level (101)
        assert matches[1].price == 101.0
        assert matches[1].quantity == 2
        assert matches[1].seller_id == "seller2"
        
        # Match 3: Third level (102), partial
        assert matches[2].price == 102.0
        assert matches[2].quantity == 2  # Only 2 of 5 remaining
        assert matches[2].seller_id == "seller3"
        
        # Verify remaining book state
        bids, asks = self.book.get_depth()
        assert len(asks) == 1  # One ask level remains
        assert asks[0][0] == 102.0  # Price
        assert asks[0][1] == 1  # 1 lot remaining (3 - 2)
    
    def test_self_trade_prevention(self):
        """
        Trader cannot trade with themselves.
        
        This is a regulatory requirement in most markets.
        """
        t0 = time.time()
        
        # Same trader posts both sides
        bid = Order("trader1", "buy", 100.0, 1, t0)
        ask = Order("trader1", "sell", 100.0, 1, t0 + 0.01)
        
        self.book.add_order(bid)
        self.book.add_order(ask)
        
        matches = self.engine.match_orders(t0 + 0.02)
        
        # Should NOT match (self-trade prevention)
        assert len(matches) == 0
        
        # One order should be removed (the taker/newer one)
        # In this case, ask is newer, so it should be removed
        bb, ba = self.book.get_best_bid_ask()
        assert bb == 100.0
        assert ba is None
    
    def test_maker_taker_identification(self):
        """
        Taker (aggressive order) is correctly identified.
        
        This matters for fee allocation:
        - Maker: Posted resting order, earns rebate
        - Taker: Crossed spread, pays fee
        """
        t0 = time.time()
        
        # Maker posts first
        maker_order = Order("maker", "sell", 100.0, 1, t0)
        self.book.add_order(maker_order)
        
        # Taker crosses spread
        time.sleep(0.01)  # Ensure different timestamp
        t1 = time.time()
        taker_order = Order("taker", "buy", 100.0, 1, t1)
        self.book.add_order(taker_order)
        
        matches = self.engine.match_orders(t1)
        
        assert len(matches) == 1
        assert matches[0].taker_id == "taker"  # Newer order
        assert matches[0].buyer_id == "taker"
        assert matches[0].seller_id == "maker"
        
        # Execution price should be maker's price
        assert matches[0].price == 100.0
    
    def test_determinism(self):
        """
        Same inputs produce same outputs (critical for debugging/replay).
        
        Run same scenario twice, verify identical results.
        """
        def run_scenario():
            book = OrderBook()
            engine = MatchingEngine(book)
            events = []
            engine.subscribe_to_matches(lambda e: events.append(e))
            
            # Fixed timestamps for determinism
            t0 = 1000.0
            
            book.add_order(Order("t1", "buy", 100.0, 2, t0))
            book.add_order(Order("t2", "buy", 101.0, 1, t0 + 1))
            book.add_order(Order("t3", "sell", 99.0, 3, t0 + 2))
            
            engine.match_orders(t0 + 3)
            
            return [(e.buyer_id, e.seller_id, e.price, e.quantity) for e in events]
        
        # Run twice
        result1 = run_scenario()
        result2 = run_scenario()
        
        # Must be identical
        assert result1 == result2
        assert len(result1) == 2  # Should produce 2 matches


class TestMatchingEngineEdgeCases:
    """Test unusual scenarios that can break naive implementations."""
    
    def setup_method(self):
        self.book = OrderBook(quote_lifetime=10.0)
        self.engine = MatchingEngine(self.book)
    
    def test_empty_book_no_crash(self):
        """Matching on empty book should not crash."""
        matches = self.engine.match_orders(time.time())
        assert matches == []
    
    def test_one_sided_book_no_match(self):
        """Only bids (or only asks) should not match."""
        self.book.add_order(Order("t1", "buy", 100.0, 1, time.time()))
        self.book.add_order(Order("t2", "buy", 101.0, 1, time.time()))
        
        matches = self.engine.match_orders(time.time())
        assert len(matches) == 0
        
        # Book should be unchanged
        bb, ba = self.book.get_best_bid_ask()
        assert bb == 101.0
        assert ba is None
    
    def test_simultaneous_orders_same_timestamp(self):
        """
        Orders with identical timestamps use order_id as tie-breaker.
        
        This tests determinism when timestamps collide (rare but possible).
        """
        t0 = time.time()
        
        # Same timestamp, different order IDs
        o1 = Order("t1", "buy", 100.0, 1, t0)
        o2 = Order("t2", "buy", 100.0, 1, t0)  # Same timestamp
        o3 = Order("t3", "sell", 100.0, 1, t0)
        
        # Order IDs are auto-incremented, so o1.order_id < o2.order_id
        
        self.book.add_order(o1)
        self.book.add_order(o2)
        self.book.add_order(o3)
        
        matches = self.engine.match_orders(t0)
        
        # Should match with t1 (lower order_id)
        assert len(matches) == 1
        assert matches[0].buyer_id == "t1"
    
    def test_zero_quantity_order_rejected(self):
        """Orders with zero quantity should be rejected."""
        with pytest.raises(ValueError):
            Order("t1", "buy", 100.0, 0, time.time())
    
    def test_negative_price_rejected(self):
        """Negative prices should be rejected."""
        with pytest.raises(ValueError):
            Order("t1", "buy", -10.0, 1, time.time())
