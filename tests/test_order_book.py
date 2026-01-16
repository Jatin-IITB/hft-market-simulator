"""
Comprehensive order book tests.

Focus areas:
1. Order lifecycle (add, cancel, expire)
2. Price-level management (aggregation, cleanup)
3. Edge cases (empty book, single-sided, huge orders)
4. Performance characteristics (not timing, but complexity verification)
5. Invariant preservation (best bid < best ask when spread exists)
"""
import pytest
import time
from engine.order_book import OrderBook, Order


class TestOrderBookBasics:
    """Test fundamental order book operations."""
    
    def setup_method(self):
        """Fresh book for each test."""
        self.book = OrderBook(quote_lifetime=5.0)
    
    def test_add_order_success(self):
        """Adding valid order should succeed."""
        order = Order("trader1", "buy", 100.0, 5, time.time())
        result = self.book.add_order(order)
        
        assert result is True
        assert len(self.book.bids) == 1
        assert 100.0 in self.book.bids
    
    def test_add_order_zero_quantity_fails(self):
        """Order with zero quantity should fail validation."""
        with pytest.raises(ValueError):
            Order("trader1", "buy", 100.0, 0, time.time())
    
    def test_add_order_negative_price_fails(self):
        """Negative price should fail."""
        with pytest.raises(ValueError):
            Order("trader1", "buy", -10.0, 1, time.time())
    
    def test_add_order_invalid_side_fails(self):
        """Invalid side should fail."""
        with pytest.raises(ValueError):
            Order("trader1", "invalid_side", 100.0, 1, time.time())
    
    def test_multiple_orders_same_price_fifo(self):
        """Multiple orders at same price maintain FIFO order."""
        t0 = time.time()
        
        o1 = Order("t1", "buy", 100.0, 1, t0)
        o2 = Order("t2", "buy", 100.0, 2, t0 + 0.1)
        o3 = Order("t3", "buy", 100.0, 3, t0 + 0.2)
        
        self.book.add_order(o1)
        self.book.add_order(o2)
        self.book.add_order(o3)
        
        # Check FIFO ordering
        queue = self.book.bids[100.0]
        assert len(queue) == 3
        assert queue[0].trader_id == "t1"
        assert queue[1].trader_id == "t2"
        assert queue[2].trader_id == "t3"
    
    def test_multiple_price_levels(self):
        """Orders at different prices create separate levels."""
        self.book.add_order(Order("t1", "buy", 100.0, 1, time.time()))
        self.book.add_order(Order("t2", "buy", 101.0, 1, time.time()))
        self.book.add_order(Order("t3", "buy", 99.0, 1, time.time()))
        
        assert len(self.book.bids) == 3
        assert 100.0 in self.book.bids
        assert 101.0 in self.book.bids
        assert 99.0 in self.book.bids
    
    def test_best_bid_ask_correct(self):
        """Best bid/ask correctly identified."""
        self.book.add_order(Order("t1", "buy", 100.0, 1, time.time()))
        self.book.add_order(Order("t2", "buy", 101.0, 1, time.time()))
        self.book.add_order(Order("t3", "buy", 99.0, 1, time.time()))
        
        self.book.add_order(Order("t4", "sell", 103.0, 1, time.time()))
        self.book.add_order(Order("t5", "sell", 102.0, 1, time.time()))
        
        bb, ba = self.book.get_best_bid_ask()
        
        assert bb == 101.0  # Highest bid
        assert ba == 102.0  # Lowest ask
    
    def test_spread_calculation(self):
        """Spread correctly calculated."""
        self.book.add_order(Order("t1", "buy", 100.0, 1, time.time()))
        self.book.add_order(Order("t2", "sell", 105.0, 1, time.time()))
        
        spread = self.book.get_spread()
        assert spread == 5.0
    
    def test_mid_price_calculation(self):
        """Mid price correctly calculated."""
        self.book.add_order(Order("t1", "buy", 100.0, 1, time.time()))
        self.book.add_order(Order("t2", "sell", 104.0, 1, time.time()))
        
        mid = self.book.get_mid_price()
        assert mid == 102.0


class TestOrderCancellation:
    """Test order cancellation logic."""
    
    def setup_method(self):
        self.book = OrderBook()
    
    def test_cancel_all_orders_by_trader(self):
        """Cancel all orders for specific trader."""
        t0 = time.time()
        
        # Add orders from multiple traders
        self.book.add_order(Order("trader1", "buy", 100.0, 1, t0))
        self.book.add_order(Order("trader1", "buy", 101.0, 2, t0))
        self.book.add_order(Order("trader2", "buy", 102.0, 1, t0))
        self.book.add_order(Order("trader1", "sell", 105.0, 1, t0))
        
        # Cancel trader1's orders
        canceled = self.book.cancel_orders("trader1")
        
        assert canceled == 3  # 2 bids + 1 ask
        assert len(self.book.bids) == 1  # Only trader2's bid remains
        assert 102.0 in self.book.bids
        assert len(self.book.asks) == 0
    
    def test_cancel_orders_by_side(self):
        """Cancel orders filtered by side."""
        t0 = time.time()
        
        self.book.add_order(Order("trader1", "buy", 100.0, 1, t0))
        self.book.add_order(Order("trader1", "sell", 105.0, 1, t0))
        
        # Cancel only buy side
        canceled = self.book.cancel_orders("trader1", side="buy")
        
        assert canceled == 1
        assert len(self.book.bids) == 0
        assert len(self.book.asks) == 1  # Sell still there
    
    def test_cancel_nonexistent_trader(self):
        """Canceling nonexistent trader returns 0."""
        self.book.add_order(Order("trader1", "buy", 100.0, 1, time.time()))
        
        canceled = self.book.cancel_orders("nonexistent_trader")
        assert canceled == 0
    
    def test_cancel_by_order_id(self):
        """Cancel specific order by ID."""
        order = Order("trader1", "buy", 100.0, 1, time.time())
        self.book.add_order(order)
        
        # Cancel by ID
        result = self.book.cancel_order_by_id(order.order_id)
        
        assert result is True
        assert len(self.book.bids) == 0
    
    def test_cancel_by_invalid_order_id(self):
        """Canceling nonexistent order ID returns False."""
        result = self.book.cancel_order_by_id(99999)
        assert result is False
    
    def test_empty_price_level_cleaned_up(self):
        """Price level removed when last order canceled."""
        o1 = Order("t1", "buy", 100.0, 1, time.time())
        o2 = Order("t2", "buy", 101.0, 1, time.time())
        
        self.book.add_order(o1)
        self.book.add_order(o2)
        
        assert len(self.book.bids) == 2
        
        # Cancel t1's order
        self.book.cancel_orders("t1")
        
        # Price level 100.0 should be removed
        assert 100.0 not in self.book.bids
        assert len(self.book.bids) == 1


class TestOrderExpiration:
    """Test order expiration logic."""
    
    def test_orders_expire_after_lifetime(self):
        """Orders older than quote_lifetime are removed."""
        book = OrderBook(quote_lifetime=2.0)
        
        t0 = time.time()
        
        # Add old order
        old_order = Order("t1", "buy", 100.0, 1, t0 - 3.0)  # 3 seconds ago
        book.bids[100.0].append(old_order)  # Bypass validation to test expiration
        
        # Add fresh order
        fresh_order = Order("t2", "buy", 101.0, 1, t0 - 0.5)  # 0.5 seconds ago
        book.add_order(fresh_order)
        
        # Expire orders
        expired = book.expire_orders(t0)
        
        assert expired == 1
        assert 100.0 not in book.bids  # Old order removed
        assert 101.0 in book.bids  # Fresh order remains
    
    def test_expiration_disabled_when_lifetime_zero(self):
        """No expiration when quote_lifetime = 0."""
        book = OrderBook(quote_lifetime=0.0)
        
        t0 = time.time()
        old_order = Order("t1", "buy", 100.0, 1, t0 - 1000.0)
        book.bids[100.0].append(old_order)
        
        expired = book.expire_orders(t0)
        
        assert expired == 0
        assert 100.0 in book.bids  # Order still there
    
    def test_empty_level_cleaned_after_expiration(self):
        """Price level removed when all orders expire."""
        book = OrderBook(quote_lifetime=1.0)
        
        t0 = time.time()
        
        # Add 3 old orders at same price
        for i in range(3):
            order = Order(f"t{i}", "buy", 100.0, 1, t0 - 2.0)
            book.bids[100.0].append(order)
        
        expired = book.expire_orders(t0)
        
        assert expired == 3
        assert 100.0 not in book.bids  # Level removed


class TestOrderBookDepth:
    """Test market depth aggregation."""
    
    def setup_method(self):
        self.book = OrderBook()
    
    def test_depth_aggregates_quantities(self):
        """Depth correctly sums quantities at each price."""
        t0 = time.time()
        
        # Add multiple orders at 100.0
        self.book.add_order(Order("t1", "buy", 100.0, 5, t0))
        self.book.add_order(Order("t2", "buy", 100.0, 3, t0))
        self.book.add_order(Order("t3", "buy", 100.0, 2, t0))
        
        # Add order at 101.0
        self.book.add_order(Order("t4", "buy", 101.0, 10, t0))
        
        bids, asks = self.book.get_depth(levels=5)
        
        # Should have 2 bid levels
        assert len(bids) == 2
        
        # Best bid (101.0) first
        assert bids[0] == (101.0, 10)
        
        # Second bid (100.0) with aggregated quantity
        assert bids[1] == (100.0, 10)  # 5 + 3 + 2
    
    def test_depth_sorted_correctly(self):
        """Bids descending, asks ascending."""
        t0 = time.time()
        
        self.book.add_order(Order("t1", "buy", 99.0, 1, t0))
        self.book.add_order(Order("t2", "buy", 101.0, 1, t0))
        self.book.add_order(Order("t3", "buy", 100.0, 1, t0))
        
        self.book.add_order(Order("t4", "sell", 103.0, 1, t0))
        self.book.add_order(Order("t5", "sell", 102.0, 1, t0))
        self.book.add_order(Order("t6", "sell", 104.0, 1, t0))
        
        bids, asks = self.book.get_depth(levels=10)
        
        # Bids should be [101, 100, 99]
        assert bids[0][0] == 101.0
        assert bids[1][0] == 100.0
        assert bids[2][0] == 99.0
        
        # Asks should be [102, 103, 104]
        assert asks[0][0] == 102.0
        assert asks[1][0] == 103.0
        assert asks[2][0] == 104.0
    
    def test_depth_respects_level_limit(self):
        """Only requested number of levels returned."""
        t0 = time.time()
        
        # Add 10 bid levels
        for i in range(10):
            self.book.add_order(Order(f"t{i}", "buy", 100.0 - i, 1, t0))
        
        bids, asks = self.book.get_depth(levels=3)
        
        assert len(bids) == 3  # Only top 3


class TestOrderBookEdgeCases:
    """Test unusual scenarios."""
    
    def test_empty_book_operations(self):
        """Operations on empty book don't crash."""
        book = OrderBook()
        
        bb, ba = book.get_best_bid_ask()
        assert bb is None
        assert ba is None
        
        spread = book.get_spread()
        assert spread is None
        
        mid = book.get_mid_price()
        assert mid is None
        
        bids, asks = book.get_depth()
        assert bids == []
        assert asks == []
    
    def test_one_sided_book_spread_none(self):
        """Spread is None when book is one-sided."""
        book = OrderBook()
        book.add_order(Order("t1", "buy", 100.0, 1, time.time()))
        
        spread = book.get_spread()
        assert spread is None
    
    def test_get_orders_by_trader_multiple_levels(self):
        """Trader's orders retrieved across all price levels."""
        book = OrderBook()
        t0 = time.time()
        
        book.add_order(Order("trader1", "buy", 100.0, 1, t0))
        book.add_order(Order("trader1", "buy", 101.0, 2, t0))
        book.add_order(Order("trader2", "buy", 102.0, 1, t0))
        book.add_order(Order("trader1", "sell", 105.0, 3, t0))
        
        orders = book.get_orders_by_trader("trader1")
        
        assert len(orders) == 3
        assert sum(o.quantity for o in orders) == 6  # 1 + 2 + 3
    
    def test_price_rounding_to_tick_size(self):
        """Prices rounded to minimum tick size."""
        book = OrderBook(min_tick_size=0.5)
        
        # Try to add order at 100.3 (not aligned with 0.5 tick)
        order = Order("t1", "buy", 100.3, 1, time.time())
        book.add_order(order)
        
        # Should be rounded to 100.5
        assert 100.5 in book.bids or 100.0 in book.bids  # Either round up or down
