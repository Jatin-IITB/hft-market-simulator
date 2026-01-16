"""
Order matching engine with price-time priority.

This implementation follows CME/NYSE matching semantics:
- Price priority: Best prices match first
- Time priority: Within same price, earliest timestamp wins
- Pro-rata NOT implemented (pure FIFO within price level)
- Deterministic: No randomness, fully reproducible

Critical for correctness:
1. Order IDs must be monotonically increasing (enforced)
2. Timestamps used for time priority, order_id for determinism
3. Taker identification: newer order is aggressive (pays fee)
4. Execution price: maker's price (standard maker-taker model)

Edge cases handled:
- Partial fills across multiple price levels
- Simultaneous orders at same price (timestamp tie-breaker)
- Self-trade prevention (if trader_id matches)
- Quantity validation (must be positive integer)
"""
from __future__ import annotations

from typing import List, Callable, Optional, Tuple
from dataclasses import dataclass

from .order_book import OrderBook


def _get_attr(obj, *names, default=None):
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


@dataclass
class MatchEvent:
    """
    Immutable match event (by convention).

    Downstream consumers (risk, P&L, analytics, UI) should treat this as read-only.
    """
    buyer_id: str
    seller_id: str
    price: float
    quantity: int
    taker_id: str
    timestamp: float
    match_id: int = 0

    def __post_init__(self):
        assert self.quantity > 0, "Quantity must be positive"
        assert self.price > 0, "Price must be positive"
        assert self.buyer_id != self.seller_id, "Cannot self-trade"
        assert self.taker_id in (self.buyer_id, self.seller_id), "Taker must be buyer or seller"

    # Compatibility aliases (so older code using buyerid/sellerid/takerid doesn't break)
    @property
    def buyerid(self) -> str:
        return self.buyer_id

    @property
    def sellerid(self) -> str:
        return self.seller_id

    @property
    def takerid(self) -> str:
        return self.taker_id

    @property
    def matchid(self) -> int:
        return self.match_id


class MatchingEngine:
    def __init__(self, order_book: OrderBook):
        self.book = order_book
        self.match_listeners: List[Callable[[MatchEvent], None]] = []
        self._match_id_counter = 0
        self._total_matches = 0
        self._total_volume = 0.0

    def subscribe_to_matches(self, callback: Callable[[MatchEvent], None]):
        if not callable(callback):
            raise TypeError("Callback must be callable")
        self.match_listeners.append(callback)

    def match_orders(self, current_time: float) -> List[MatchEvent]:
        """
        Execute matching algorithm on current book state.

        Maker/taker is determined by (timestamp, order_id).
        Execution price is maker's resting limit price.
        """
        matches: List[MatchEvent] = []

        book_lock = getattr(self.book, "lock", None)

        def order_key(o) -> Tuple[float, int]:
            ts = _get_attr(o, "timestamp")
            oid = _get_attr(o, "order_id", "orderid")
            if ts is None or oid is None:
                raise AttributeError("Order must have timestamp and order_id/orderid")
            return (float(ts), int(oid))

        def trader_id(o) -> str:
            tid = _get_attr(o, "trader_id", "traderid")
            if tid is None:
                raise AttributeError("Order must have trader_id/traderid")
            return str(tid)

        def index_remove_if_present(order) -> None:
            remover = getattr(self.book, "index_remove", None)
            if remover is None:
                return
            oid = _get_attr(order, "order_id", "orderid")
            if oid is None:
                return
            remover(int(oid))

        def loop() -> None:
            nonlocal matches

            while True:
                best_bid_price, best_ask_price = self._get_best_prices()
                if best_bid_price is None or best_ask_price is None:
                    break
                if best_bid_price < best_ask_price:
                    break  # spread exists

                bid_queue = self.book.bids.get(best_bid_price)
                ask_queue = self.book.asks.get(best_ask_price)
                if not bid_queue or not ask_queue:
                    self._clean_empty_levels()
                    continue

                bid_order = bid_queue[0]
                ask_order = ask_queue[0]

                bid_tid = trader_id(bid_order)
                ask_tid = trader_id(ask_order)

                bid_key = order_key(bid_order)
                ask_key = order_key(ask_order)

                # Older order is maker; newer is taker.
                if bid_key <= ask_key:
                    # Bid is maker, ask is taker, trade at bid (maker) price.
                    execution_price = float(_get_attr(bid_order, "price"))
                    taker_id = ask_tid
                    taker_side = "ask"
                else:
                    # Ask is maker, bid is taker, trade at ask (maker) price.
                    execution_price = float(_get_attr(ask_order, "price"))
                    taker_id = bid_tid
                    taker_side = "bid"

                # Self-trade prevention: remove the taker order (newer one) deterministically.
                if bid_tid == ask_tid:
                    if taker_side == "bid":
                        removed = bid_queue.popleft()
                        index_remove_if_present(removed)
                        if not bid_queue:
                            self.book.bids.pop(best_bid_price, None)
                    else:
                        removed = ask_queue.popleft()
                        index_remove_if_present(removed)
                        if not ask_queue:
                            self.book.asks.pop(best_ask_price, None)
                    self._clean_empty_levels()
                    continue

                bid_qty = int(_get_attr(bid_order, "quantity"))
                ask_qty = int(_get_attr(ask_order, "quantity"))
                match_qty = min(bid_qty, ask_qty)

                self._match_id_counter += 1
                event = MatchEvent(
                    buyer_id=bid_tid,
                    seller_id=ask_tid,
                    price=execution_price,
                    quantity=match_qty,
                    taker_id=taker_id,
                    timestamp=float(current_time),
                    match_id=self._match_id_counter,
                )

                matches.append(event)
                self._total_matches += 1
                self._total_volume += match_qty * execution_price

                self._notify_listeners(event)

                # Update quantities on the live Order objects
                bid_order.quantity -= match_qty
                ask_order.quantity -= match_qty

                # Remove fully filled orders + keep OrderBook indices consistent
                if int(_get_attr(bid_order, "quantity")) == 0:
                    removed = bid_queue.popleft()
                    index_remove_if_present(removed)
                    if not bid_queue:
                        self.book.bids.pop(best_bid_price, None)

                if int(_get_attr(ask_order, "quantity")) == 0:
                    removed = ask_queue.popleft()
                    index_remove_if_present(removed)
                    if not ask_queue:
                        self.book.asks.pop(best_ask_price, None)

        if book_lock is None:
            loop()
        else:
            with book_lock:
                loop()

        return matches

    def _get_best_prices(self) -> Tuple[Optional[float], Optional[float]]:
        best_bid = max(self.book.bids.keys()) if self.book.bids else None
        best_ask = min(self.book.asks.keys()) if self.book.asks else None
        return best_bid, best_ask

    def _notify_listeners(self, event: MatchEvent):
        for listener in self.match_listeners:
            try:
                listener(event)
            except Exception as e:
                print(f"Error in match listener: {e}")

    def _clean_empty_levels(self):
        empty_bid_prices = [p for p, q in self.book.bids.items() if not q]
        for price in empty_bid_prices:
            self.book.bids.pop(price, None)

        empty_ask_prices = [p for p, q in self.book.asks.items() if not q]
        for price in empty_ask_prices:
            self.book.asks.pop(price, None)

    def get_stats(self) -> dict:
        return {
            "total_matches": self._total_matches,
            "total_volume": self._total_volume,
            "active_listeners": len(self.match_listeners),
        }

    def reset_stats(self):
        self._match_id_counter = 0
        self._total_matches = 0
        self._total_volume = 0.0
