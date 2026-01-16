from __future__ import annotations

from dataclasses import dataclass, field
from collections import defaultdict, deque
from threading import RLock
from typing import DefaultDict, Deque, Dict, List, Optional, Set, Tuple
import itertools


_order_id_counter = itertools.count(1)


@dataclass
class Order:
    trader_id: str
    side: str                 # 'buy' | 'sell'
    price: float
    quantity: int
    timestamp: float
    order_id: int = field(default_factory=lambda: next(_order_id_counter))

    def __post_init__(self) -> None:
        if not self.trader_id:
            raise ValueError("trader_id cannot be empty")
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if not isinstance(self.quantity, int):
            raise ValueError("quantity must be int")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {self.quantity}")
        if self.price <= 0:
            raise ValueError(f"price must be > 0, got {self.price}")
        if self.timestamp < 0:
            raise ValueError(f"timestamp must be >= 0, got {self.timestamp}")

    def __repr__(self) -> str:
        return f"Order(id={self.order_id}, trader={self.trader_id}, {self.side} {self.quantity}@{self.price:.1f})"


class OrderBook:
    """
    Thread-safe CLOB book storage with:
    - price->deque FIFO at each level
    - order_id index for cancels
    - trader_id -> set(order_id) index for fast mass-cancel
    - deterministic expiration via expire_orders(current_time) called by main loop

    Compatibility targets (your existing code expects):
    - self.bids / self.asks are dict-like keyed by float price
    - values are deques containing mutable Order objects (quantity mutates on fills)
    - add_order(order) -> bool
    - cancel_orders(trader_id, side=None) -> int
    - cancel_order_by_id(order_id) -> bool
    - expire_orders(current_time) -> int
    - get_best_bid_ask/get_spread/get_mid_price/get_depth/get_stats/clear
    """

    def __init__(self, quote_lifetime: float = 5.0, min_tick_size: float = 0.1):
        if min_tick_size <= 0:
            raise ValueError("min_tick_size must be > 0")
        if quote_lifetime < 0:
            raise ValueError("quote_lifetime must be >= 0 (0 disables expiry)")

        self._lock = RLock()

        self.quote_lifetime = float(quote_lifetime)
        self.min_tick_size = float(min_tick_size)

        self.bids: DefaultDict[float, Deque[Order]] = defaultdict(deque)
        self.asks: DefaultDict[float, Deque[Order]] = defaultdict(deque)

        # Indices
        self._order_index: Dict[int, Tuple[str, float, str]] = {}            # order_id -> (side, price, trader_id)
        self._trader_order_ids: DefaultDict[str, Set[int]] = defaultdict(set)

        # Stats
        self._total_orders_added = 0
        self._total_orders_canceled = 0
        self._total_orders_expired = 0

    # ---------- internal helpers (lock required) ----------

    def _snap_price(self, price: float) -> float:
        # snap to tick grid (float key stays stable)
        ticks = round(float(price) / self.min_tick_size)
        return round(ticks * self.min_tick_size, 10)

    def _book_for_side(self, side: str) -> DefaultDict[float, Deque[Order]]:
        return self.bids if side == "buy" else self.asks

    def _clean_empty_level(self, side: str, price: float) -> None:
        book = self._book_for_side(side)
        q = book.get(price)
        if q is not None and not q:
            del book[price]

    def _index_add(self, order: Order) -> None:
        self._order_index[order.order_id] = (order.side, order.price, order.trader_id)
        self._trader_order_ids[order.trader_id].add(order.order_id)

    def _index_remove(self, order_id: int) -> None:
        loc = self._order_index.pop(order_id, None)
        if not loc:
            return
        _, _, trader_id = loc
        s = self._trader_order_ids.get(trader_id)
        if s is not None:
            s.discard(order_id)
            if not s:
                self._trader_order_ids.pop(trader_id, None)

    # ---------- public API ----------

    def add_order(self, order: Order) -> bool:
        with self._lock:
            # normalize price to tick grid (critical for stable dict keys)
            order.price = self._snap_price(order.price)

            book = self._book_for_side(order.side)
            book[order.price].append(order)

            self._index_add(order)
            self._total_orders_added += 1
            return True

    def cancel_order_by_id(self, order_id: int) -> bool:
        with self._lock:
            loc = self._order_index.get(order_id)
            if not loc:
                return False

            side, price, _trader_id = loc
            book = self._book_for_side(side)
            q = book.get(price)
            if not q:
                # stale index; clean up
                self._index_remove(order_id)
                return False

            old_len = len(q)
            new_q = deque(o for o in q if o.order_id != order_id)
            if len(new_q) == old_len:
                return False

            book[price] = new_q
            self._clean_empty_level(side, price)
            self._index_remove(order_id)

            self._total_orders_canceled += 1
            return True

    def cancel_orders(self, trader_id: str, side: Optional[str] = None) -> int:
        """
        Cancel all orders for trader_id; if side specified, cancel only that side.
        Uses indices so we don't scan the whole book.
        """
        with self._lock:
            ids = self._trader_order_ids.get(trader_id)
            if not ids:
                return 0

            # group by (side, price) so each deque is filtered once
            targets: DefaultDict[Tuple[str, float], Set[int]] = defaultdict(set)
            for oid in list(ids):
                loc = self._order_index.get(oid)
                if not loc:
                    continue
                s, p, t = loc
                if t != trader_id:
                    continue
                if side is None or side == s:
                    targets[(s, p)].add(oid)

            canceled = 0
            for (s, p), oids in targets.items():
                book = self._book_for_side(s)
                q = book.get(p)
                if not q:
                    continue

                old_len = len(q)
                new_q = deque(o for o in q if o.order_id not in oids)
                removed = old_len - len(new_q)
                if removed <= 0:
                    continue

                book[p] = new_q
                self._clean_empty_level(s, p)
                for oid in oids:
                    self._index_remove(oid)
                canceled += removed

            self._total_orders_canceled += canceled
            return canceled

    def expire_orders(self, current_time: float) -> int:
        if self.quote_lifetime <= 0:
            return 0

        with self._lock:
            cutoff = float(current_time) - self.quote_lifetime
            expired = 0

            for side, book in (("buy", self.bids), ("sell", self.asks)):
                for price in list(book.keys()):
                    q = book[price]
                    keep: Deque[Order] = deque()
                    for o in q:
                        if o.timestamp >= cutoff:
                            keep.append(o)
                        else:
                            expired += 1
                            self._index_remove(o.order_id)

                    if keep:
                        book[price] = keep
                    else:
                        del book[price]

            self._total_orders_expired += expired
            return expired

    def get_best_bid_ask(self) -> Tuple[Optional[float], Optional[float]]:
        with self._lock:
            best_bid = max(self.bids.keys()) if self.bids else None
            best_ask = min(self.asks.keys()) if self.asks else None
            return best_bid, best_ask

    def get_spread(self) -> Optional[float]:
        bb, ba = self.get_best_bid_ask()
        if bb is None or ba is None:
            return None
        return ba - bb

    def get_mid_price(self) -> Optional[float]:
        bb, ba = self.get_best_bid_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2.0

    def get_depth(self, levels: int = 6) -> Tuple[List[Tuple[float, int]], List[Tuple[float, int]]]:
        with self._lock:
            bid_prices = sorted(self.bids.keys(), reverse=True)[:levels]
            ask_prices = sorted(self.asks.keys())[:levels]

            bids = [(p, sum(o.quantity for o in self.bids[p])) for p in bid_prices]
            asks = [(p, sum(o.quantity for o in self.asks[p])) for p in ask_prices]
            return bids, asks

    def get_orders_by_trader(self, trader_id: str) -> List[Order]:
        with self._lock:
            ids = self._trader_order_ids.get(trader_id)
            if not ids:
                return []

            out: List[Order] = []
            for oid in ids:
                loc = self._order_index.get(oid)
                if not loc:
                    continue
                side, price, _t = loc
                book = self._book_for_side(side)
                q = book.get(price)
                if not q:
                    continue
                for o in q:
                    if o.order_id == oid:
                        out.append(o)
                        break

            out.sort(key=lambda o: (o.timestamp, o.order_id))
            return out

    def get_total_quantity(self, side: str) -> int:
        with self._lock:
            book = self.bids if side == "buy" else self.asks
            return sum(sum(o.quantity for o in q) for q in book.values())

    def get_stats(self) -> dict:
        with self._lock:
            bb, ba = self.get_best_bid_ask()
            return {
                "total_orders_added": self._total_orders_added,
                "total_orders_canceled": self._total_orders_canceled,
                "total_orders_expired": self._total_orders_expired,
                "active_bid_levels": len(self.bids),
                "active_ask_levels": len(self.asks),
                "total_bid_quantity": self.get_total_quantity("buy"),
                "total_ask_quantity": self.get_total_quantity("sell"),
                "best_bid": bb,
                "best_ask": ba,
                "spread": self.get_spread(),
                "mid_price": self.get_mid_price(),
                "active_traders": len(self._trader_order_ids),
            }

    def clear(self) -> None:
        with self._lock:
            self.bids.clear()
            self.asks.clear()
            self._order_index.clear()
            self._trader_order_ids.clear()
