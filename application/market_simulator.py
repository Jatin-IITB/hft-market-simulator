from __future__ import annotations

import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, Dict, List, Optional, Tuple

from engine.order_book import OrderBook, Order
from engine.matching_engine import MatchingEngine, MatchEvent
from engine.trader import Trader, Fill, TradeSide
from engine.bot_strategies import BotManager, TradePrint
from engine.risk_manager import RiskManager
from infrastructure.config import DifficultyConfig


class GameState(Enum):
    NOT_STARTED = "not_started"
    ROUND_ACTIVE = "round_active"
    ROUND_ENDING = "round_ending"
    GAME_COMPLETE = "game_complete"
    PAUSED = "paused"


class EventType(Enum):
    ROUND_START = "round_start"
    ROUND_END = "round_end"
    DIGIT_REVEAL = "digit_reveal"
    TRADE_EXECUTED = "trade_executed"
    POSITION_CHANGE = "position_change"
    RISK_ALERT = "risk_alert"
    VOLATILITY_SPIKE = "volatility_spike"
    LIQUIDITY_CRASH = "liquidity_crash"
    MARGIN_CALL = "margin_call"
    LEADERBOARD = "leaderboard"


@dataclass(frozen=True)
class MarketEvent:
    timestamp: float
    event_type: EventType
    data: Dict
    message: str


@dataclass(frozen=True)
class MarketSnapshot:
    timestamp: float
    game_state: GameState
    current_round: int
    total_rounds: int
    time_remaining: int
    fair_value: float
    theoretical_std: float
    volatility: float
    digits: List[Optional[int]]
    masked_digits: List[str] = field(default_factory=list)

    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    spread: Optional[float] = None
    mid_price: Optional[float] = None
    bids: List[tuple] = field(default_factory=list)
    asks: List[tuple] = field(default_factory=list)

    user_position: int = 0
    user_cash: float = 0.0
    user_fees: float = 0.0
    user_pnl: float = 0.0
    user_vwap: float = 0.0
    user_toxicity: float = 0.0

    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0

    position_utilization: float = 0.0
    margin_cushion: float = 0.0
    var_95: float = 0.0
    at_risk: bool = False

    recent_trades: List[str] = field(default_factory=list)
    recent_alerts: List[str] = field(default_factory=list)

    bot_positions: Dict[str, int] = field(default_factory=dict)
    bot_pnls: Dict[str, float] = field(default_factory=dict)
    leaderboard: List[Tuple[str, float]] = field(default_factory=list)

    settlement_price: Optional[int] = None
    total_volume: float = 0.0
    num_matches: int = 0
    book_depth: int = 0


class MarketSimulator:
    def __init__(self, config: DifficultyConfig, seed: Optional[int] = None):
        self.config = config
        self.game_state = GameState.NOT_STARTED

        self.book = OrderBook(quote_lifetime=config.quote_lifetime)
        self.matching_engine = MatchingEngine(self.book)

        self.risk_manager = RiskManager(
            position_limit=config.position_limit,
            margin_threshold=-500.0,
            loss_limit=-1000.0,
            max_order_size=config.position_limit,
        )

        self.user = Trader("YOU", is_bot=False)
        self.traders: Dict[str, Trader] = {"YOU": self.user}

        if seed is None:
            seed = int(time.time() * 1_000_000) & 0xFFFFFFFF
        self.seed = seed
        self.rng = random.Random(self.seed)

        self.bot_manager = BotManager(config, seed=self.seed ^ 0xA11CE)
        for name in self.bot_manager.get_bot_names():
            self.traders[name] = Trader(name, is_bot=True)
        self.bot_manager.initialize_bots(self.traders)

        self.current_round = 0
        self.total_rounds = getattr(config, "total_rounds", 6)
        self.time_remaining = 0
        self.round_start_time = 0.0

        self._all_digits = [self.rng.randint(0, 9) for _ in range(self.total_rounds)]
        self.digits: List[Optional[int]] = [None] * self.total_rounds
        self.settlement_price = int(sum(self._all_digits))

        self.volatility = 1.0

        self.events: Deque[MarketEvent] = deque(maxlen=200)
        self.trade_log: Deque[str] = deque(maxlen=80)
        self.alert_log: Deque[str] = deque(maxlen=40)

        # Tape for bots: last N trade prints
        self._tape: Deque[TradePrint] = deque(maxlen=120)

        self._state_subscribers: List[Callable[[MarketSnapshot], None]] = []
        self._event_subscribers: List[Callable[[MarketEvent], None]] = []

        self._lock = threading.RLock()
        self._timer_thread: Optional[threading.Thread] = None
        self._timer_running = False

        self._tick_count = 0
        self._last_leaderboard: List[Tuple[str, float]] = []

    # ---------------- Subscription API ----------------

    def subscribe_to_state_changes(self, callback):
        with self._lock:
            self._state_subscribers.append(callback)

    def subscribe_to_events(self, callback):
        with self._lock:
            self._event_subscribers.append(callback)

    # ---------------- Game lifecycle ----------------

    def start_round(self, round_number: int) -> None:
        with self._lock:
            if round_number < 1 or round_number > self.total_rounds:
                raise ValueError(f"Invalid round number: {round_number}")
            if self.game_state == GameState.ROUND_ACTIVE:
                raise RuntimeError("Cannot start a round while another round is active.")
            if self.game_state == GameState.GAME_COMPLETE:
                raise RuntimeError("Game complete.")

            self.current_round = round_number
            self.game_state = GameState.ROUND_ACTIVE
            self.time_remaining = int(self.config.round_time)
            self.round_start_time = time.time()

            self._start_timer()
            self._log_event(EventType.ROUND_START, {"round": round_number}, f"Round {round_number} started")
            self._emit_state_change()

    def end_round(self) -> None:
        with self._lock:
            if self.game_state != GameState.ROUND_ACTIVE:
                return

            for trader_name in list(self.traders.keys()):
                self.book.cancel_orders(trader_name)

            fv = self._calculate_fair_value()
            self._last_leaderboard = self._compute_leaderboard(mark_price=fv)

            self._log_event(EventType.ROUND_END, {"round": self.current_round}, f"Round {self.current_round} ended")
            self._log_event(EventType.LEADERBOARD, {"leaderboard": self._last_leaderboard[:12]}, "Leaderboard updated")

            if self.current_round >= self.total_rounds:
                self._end_game()
                return

            idx = self.current_round - 1
            if 0 <= idx < self.total_rounds and self.digits[idx] is None:
                revealed = self._all_digits[idx]
                self.digits[idx] = revealed
                self._log_event(EventType.DIGIT_REVEAL, {"digit": revealed, "index": idx}, f"Digit {idx+1} revealed: {revealed}")

            unknowns = sum(1 for d in self.digits if d is None)
            spike = 1.0 + (0.05 + 0.02 * max(0, unknowns)) * self.rng.random()
            self.volatility = min(self.volatility * spike, self.config.volatility_cap)

            self.game_state = GameState.ROUND_ENDING
            self.time_remaining = 10
            self._timer_running = True
            self._log_alert("Intermission: Next round starts in 10s...")
            self._emit_state_change()

    def _end_game(self) -> None:
        self.game_state = GameState.GAME_COMPLETE

        last_idx = self.total_rounds - 1
        if self.digits[last_idx] is None:
            self.digits[last_idx] = self._all_digits[last_idx]

        settlement = self.settlement_price
        self._log_event(
            EventType.DIGIT_REVEAL,
            {"digit": self.digits[last_idx], "settlement": settlement},
            f"Final digit: {self.digits[last_idx]} | Settlement: {settlement}",
        )
        self._last_leaderboard = self._compute_leaderboard(mark_price=settlement)
        self._emit_state_change()

    # ---------------- Main loop ----------------

    def tick(self) -> None:
        with self._lock:
            if self.game_state == GameState.ROUND_ENDING:
                if self.time_remaining <= 0:
                    self.start_round(self.current_round + 1)
                return
            if self.game_state != GameState.ROUND_ACTIVE:
                return

            self._tick_count += 1
            now = time.time()

            self.book.expire_orders(now)

            fv = self._calculate_fair_value()

            # 1) Bots act (quotes + IOC placements). Returns IOC order_ids to cancel after matching.
            ioc_ids = self.bot_manager.update_quotes(
                book=self.book,
                traders=self.traders,
                fair_value=fv,
                volatility=self.volatility,
                user_toxicity=self.user.adverse_selection_score,
                tape=list(self._tape),
                now=now,
                risk_manager=self.risk_manager,
            )

            # 2) Match once (single source of truth for executions)
            matches = self.matching_engine.match_orders(now)
            for m in matches:
                self._execute_match(m, fv)

            # 3) Cancel IOC leftovers (true IOC semantics)
            for oid in ioc_ids:
                self.book.cancel_order_by_id(int(oid))

            # 4) Vol feedback
            if len(matches) > 2:
                self.volatility = min(self.volatility * 1.03, self.config.volatility_cap)
            else:
                self.volatility = max(1.0, self.volatility * 0.999)

            # 5) Risk checks
            for name, tr in self.traders.items():
                if self.risk_manager.check_margin_call(tr, fv, now):
                    self.book.cancel_orders(name)
                    msg = f"MARGIN CALL: {name} liquidated"
                    if name == "YOU":
                        self._log_alert(msg)
                    self._log_event(EventType.MARGIN_CALL, {"trader": name}, msg)

            if self.time_remaining <= 0:
                self.end_round()

            self._emit_state_change()

    # ---------------- User commands (GUI expects these signatures) ----------------

    def make_market(self, bid: float, ask: float, qty: int) -> bool:
        with self._lock:
            if self.game_state != GameState.ROUND_ACTIVE:
                return False
            if bid >= ask:
                return False
            if qty <= 0:
                return False

            now = time.time()
            self.book.cancel_orders("YOU")
            self.book.add_order(Order("YOU", "buy", float(bid), int(qty), now))
            self.book.add_order(Order("YOU", "sell", float(ask), int(qty), now))
            self._log_trade(f"YOU quoted {qty}x {bid:.1f}/{ask:.1f}")
            self.tick()
            return True

    def aggress_buy(self, price: float, qty: int) -> bool:
        with self._lock:
            if self.game_state != GameState.ROUND_ACTIVE:
                return False
            if qty <= 0 or price <= 0:
                return False
            ok, reason = self.risk_manager.can_add_position(self.user, int(qty))
            if not ok:
                self._log_alert(str(reason))
                return False
            self.book.add_order(Order("YOU", "buy", float(price), int(qty), time.time()))
            self._log_trade(f"YOU buy {qty} @ {price:.1f}")
            self.tick()
            return True

    def aggress_sell(self, price: float, qty: int) -> bool:
        with self._lock:
            if self.game_state != GameState.ROUND_ACTIVE:
                return False
            if qty <= 0 or price <= 0:
                return False
            ok, reason = self.risk_manager.can_reduce_position(self.user, int(qty))
            if not ok:
                self._log_alert(str(reason))
                return False
            self.book.add_order(Order("YOU", "sell", float(price), int(qty), time.time()))
            self._log_trade(f"YOU sell {qty} @ {price:.1f}")
            self.tick()
            return True

    def cancel_user_orders(self) -> int:
        with self._lock:
            n = self.book.cancel_orders("YOU")
            if n:
                self._log_trade(f"Canceled {n} orders")
            return n

    # ---------------- Snapshot ----------------

    def get_state_snapshot(self) -> MarketSnapshot:
        with self._lock:
            fv = self._calculate_fair_value()
            std = self._calculate_theoretical_std()
            bb, ba = self.book.get_best_bid_ask()
            bids, asks = self.book.get_depth(6)

            masked = ["?" if d is None else str(d) for d in self.digits]

            bot_positions = {n: t.position for n, t in self.traders.items() if t.is_bot}
            bot_pnls = {n: t.mark_to_market(fv) for n, t in self.traders.items() if t.is_bot}

            eng_stats = self.matching_engine.get_stats()
            book_stats = self.book.get_stats()

            risk = self.risk_manager.get_risk_metrics(self.user, fv)

            return MarketSnapshot(
                timestamp=time.time(),
                game_state=self.game_state,
                current_round=self.current_round,
                total_rounds=self.total_rounds,
                time_remaining=int(self.time_remaining),
                fair_value=float(fv),
                theoretical_std=float(std),
                volatility=float(self.volatility),
                digits=list(self.digits),
                masked_digits=masked,
                best_bid=bb,
                best_ask=ba,
                spread=self.book.get_spread(),
                mid_price=self.book.get_mid_price(),
                bids=bids,
                asks=asks,
                user_position=int(self.user.position),
                user_cash=float(self.user.cash),
                user_fees=float(self.user.fees_paid),
                user_pnl=float(self.user.mark_to_market(fv)),
                user_vwap=float(self.user.calculate_vwap()),
                user_toxicity=float(self.user.adverse_selection_score),
                delta=float(self.user.position),
                gamma=float(abs(self.user.position) * max(0, sum(1 for d in self.digits if d is None)) * 0.15),
                vega=float(sum(1 for d in self.digits if d is None)) * 0.5,
                theta=-0.01 * float(sum(1 for d in self.digits if d is None)),
                position_utilization=float(risk.get("position_utilization", 0.0)),
                margin_cushion=float(risk.get("margin_cushion", 0.0)),
                var_95=float(risk.get("var_95", 0.0)),
                at_risk=bool(risk.get("at_risk", False)),
                recent_trades=list(self.trade_log),
                recent_alerts=list(self.alert_log),
                bot_positions=bot_positions,
                bot_pnls=bot_pnls,
                leaderboard=list(self._last_leaderboard) if self._last_leaderboard else [],
                settlement_price=self.settlement_price if self.game_state == GameState.GAME_COMPLETE else None,
                total_volume=float(eng_stats.get("total_volume", 0.0)),
                num_matches=int(eng_stats.get("total_matches", 0)),
                book_depth=int(book_stats.get("active_bid_levels", 0) + book_stats.get("active_ask_levels", 0)),
            )

    # ---------------- Internals ----------------

    def _calculate_fair_value(self) -> float:
        known_sum = sum(d for d in self.digits if d is not None)
        unknowns = sum(1 for d in self.digits if d is None)
        return float(known_sum + unknowns * 4.5)

    def _calculate_theoretical_std(self) -> float:
        unknowns = sum(1 for d in self.digits if d is None)
        return math.sqrt(unknowns * 8.25)

    def _compute_leaderboard(self, mark_price: float) -> List[Tuple[str, float]]:
        lb = [(n, float(t.mark_to_market(mark_price))) for n, t in self.traders.items()]
        lb.sort(key=lambda x: x[1], reverse=True)
        return lb

    def _execute_match(self, match: MatchEvent, fair_value: float) -> None:
        buyer = self.traders[match.buyer_id]
        seller = self.traders[match.seller_id]

        fee = float(self.config.taker_fee or 0.0)
        buy_fee = fee if match.taker_id == match.buyer_id else 0.0
        sell_fee = fee if match.taker_id == match.seller_id else 0.0

        buyer.apply_fill(Fill(match.price, match.quantity, TradeSide.BUY, match.timestamp, match.seller_id, buy_fee))
        seller.apply_fill(Fill(match.price, match.quantity, TradeSide.SELL, match.timestamp, match.buyer_id, sell_fee))

        # Update adverse selection (toxicity) for both sides
        try:
            buyer.update_adverse_selection(match.price, fair_value, is_buyer=True)
            seller.update_adverse_selection(match.price, fair_value, is_buyer=False)
        except Exception:
            pass

        # Tape print for bots (order flow)
        taker_side: str = "buy" if match.taker_id == match.buyer_id else "sell"
        self._tape.append(
            TradePrint(
                timestamp=float(match.timestamp),
                price=float(match.price),
                qty=int(match.quantity),
                taker_side=taker_side,  # type: ignore[arg-type]
            )
        )

        b = "YOU" if match.buyer_id == "YOU" else match.buyer_id[:10]
        s = "YOU" if match.seller_id == "YOU" else match.seller_id[:10]
        self._log_trade(f"Trade: {b} bought {match.quantity} @ {match.price:.1f} from {s}")
        self._log_event(EventType.TRADE_EXECUTED, {"price": match.price, "quantity": match.quantity}, "Trade executed")

    def _start_timer(self) -> None:
        if self._timer_running:
            return
        self._timer_running = True

        def _run() -> None:
            while True:
                time.sleep(1.0)
                with self._lock:
                    if not self._timer_running:
                        return
                    if self.game_state not in (GameState.ROUND_ACTIVE, GameState.ROUND_ENDING):
                        continue
                    self.time_remaining -= 1

        self._timer_thread = threading.Thread(target=_run, daemon=True, name="round-timer")
        self._timer_thread.start()

    def _log_trade(self, message: str) -> None:
        self.trade_log.append(message)

    def _log_alert(self, message: str) -> None:
        self.alert_log.append(message)

    def _log_event(self, event_type: EventType, data: Dict, message: str) -> None:
        ev = MarketEvent(time.time(), event_type, data, message)
        self.events.append(ev)
        subs = list(self._event_subscribers)
        for cb in subs:
            try:
                cb(ev)
            except Exception:
                pass

    def _emit_state_change(self) -> None:
        snap = self.get_state_snapshot()
        subs = list(self._state_subscribers)
        for cb in subs:
            try:
                cb(snap)
            except Exception:
                pass
