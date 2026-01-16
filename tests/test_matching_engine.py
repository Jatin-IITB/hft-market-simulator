"""
Unit tests for matching engine.
Critical: Ensures price-time priority and deterministic execution.
"""
import pytest
import time
from engine.order_book import OrderBook, Order
from engine.matching_engine import MatchingEngine, MatchEvent


class TestMatchingEngine:
    """Test suite for matching engine."""
    
    def setup_method(self):
        """Setup before each test."""
        self.book = OrderBook(quote_lifetime=10.0)
        self.engine = MatchingEngine(self.book)
        self.matched_events = []
        
        # Subscribe to matches
        self.engine.subscribe_to_matches(lambda e: self.matched_events.append(e))
    
    def test_price_priority_buy_side(self):
        """Higher bids get filled first."""
        # Add orders
        o1 = Order("trader1", "buy", 100.0, 1, time.time())
        o2 = Order("trader2", "buy", 101.0, 1, time.time() + 0.001)  # Higher price, later time
        o3 = Order("trader3", "sell", 100.0, 2, time.time() + 0.002)
        
        self.book.add_order(o1)
        self.book.add_order(o2)
        self.book.add_order(o3)
        
        # Match
        matches = self.engine.match_orders(time.time())
        
        # Assertions
        assert len(matches) == 2
        assert matches[0].buyer_id == "trader2"  # Higher price filled first
        assert matches[0].price == 101.0
        assert matches[1].buyer_id == "trader1"
        assert matches[1].price == 100.0
    
    def test_time_priority_same_price(self):
        """Earlier orders at same price get filled first."""
        # Add orders with same price, different times
        t0 = time.time()
        o1 = Order("trader1", "buy", 100.0, 1, t0)
        o2 = Order("trader2", "buy", 100.0, 1, t0 + 0.1)  # Later
        o3 = Order("trader3", "sell", 100.0, 1, t0 + 0.2)
        
        self.book.add_order(o1)
        self.book.add_order(o2)
        self.book.add_order(o3)
        
        # Match
        matches = self.engine.match_orders(time.time())
        
        # Assertions
        assert len(matches) == 1
        assert matches[0].buyer_id == "trader1"  # Earlier timestamp wins
        assert matches[0].seller_id == "trader3"
    
    def test_partial_fills(self):
        """Orders can be partially filled."""
        o1 = Order("buyer", "buy", 100.0, 5, time.time())
        o2 = Order("seller", "sell", 100.0, 3, time.time() + 0.001)
        
        self.book.add_order(o1)
        self.book.add_order(o2)
        
        matches = self.engine.match_orders(time.time())
        
        # Should match 3, leaving 2 on bid side
        assert len(matches) == 1
        assert matches[0].quantity == 3
        
        # Check remaining book
        bb, ba = self.book.get_best_bid_ask()
        assert bb == 100.0
        assert ba is None
        
        # Bid should have 2 remaining
        bids, asks = self.book.get_depth()
        assert bids[0][1] == 2  # Quantity = 2
    
    def test_no_cross_no_match(self):
        """Orders don't match when bid < ask."""
        o1 = Order("buyer", "buy", 99.0, 1, time.time())
        o2 = Order("seller", "sell", 101.0, 1, time.time() + 0.001)
        
        self.book.add_order(o1)
        self.book.add_order(o2)
        
        matches = self.engine.match_orders(time.time())
        
        assert len(matches) == 0
        
        # Book should still have both orders
        bb, ba = self.book.get_best_bid_ask()
        assert bb == 99.0
        assert ba == 101.0
    
    def test_taker_identification(self):
        """Taker (aggressive order) is correctly identified."""
        # Maker order (resting)
        t0 = time.time()
        maker = Order("maker", "sell", 100.0, 1, t0)
        self.book.add_order(maker)
        
        # Taker order (crosses spread)
        time.sleep(0.01)
        t1 = time.time()
        taker = Order("taker", "buy", 100.0, 1, t1)
        self.book.add_order(taker)
        
        matches = self.engine.match_orders(time.time())
        
        assert len(matches) == 1
        assert matches[0].taker_id == "taker"  # Newer order is taker
        assert matches[0].buyer_id == "taker"
        assert matches[0].seller_id == "maker"
