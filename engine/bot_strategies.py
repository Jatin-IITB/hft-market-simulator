from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple, Literal

from engine.order_book import OrderBook, Order
from engine.trader import Trader
from engine.risk_manager import RiskManager
BotType = Literal["hft_mm", "momentum", "arbitrage", "noise"]
Side = Literal["buy", "sell"]


# ---------------------------------------------------------------------
# Compatibility export (your engine/__init__.py imports BotConfig)  [file:212]
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class BotConfig:
    """
    Backward-compatible bot configuration.
    This symbol MUST exist because engine/__init__.py exports it. [file:212]
    """
    name: str
    bot_type: BotType
    base_latency: float

    # Microstructure knobs
    quote_size: int = 1
    aggression: float = 0.25          # probability of firing on a signal
    risk_aversion: float = 0.6        # higher => wider / more inventory-averse
    inventory_skew: float = 0.8       # skew strength vs position
    toxicity_sensitivity: float = 1.0 # widen/pull vs toxicity
    refresh_min_s: float = 0.20       # minimum quote refresh interval
    stickiness_ticks: int = 1         # change threshold to refresh quotes


class _EWM:
    def __init__(self, alpha: float):
        self.alpha = float(alpha)
        self.value: Optional[float] = None

    def update(self, x: float) -> float:
        if self.value is None:
            self.value = float(x)
        else:
            self.value = self.alpha * float(x) + (1.0 - self.alpha) * self.value
        return float(self.value)


class _BotState:
    def __init__(self):
        self.next_action_time: float = 0.0
        self.last_quote_time: float = 0.0
        self.last_bid: Optional[float] = None
        self.last_ask: Optional[float] = None

        # For momentum/trend
        self.ema_fast = _EWM(alpha=0.35)
        self.ema_slow = _EWM(alpha=0.08)


@dataclass(frozen=True)
class TradePrint:
    """
    Minimal tape print passed from simulator -> bots.
    taker_side: 'buy' means aggressor lifted offers (taker is buyer).
    """
    timestamp: float
    price: float
    qty: int
    taker_side: Side


class BaseBot:
    def __init__(self, cfg: BotConfig, *, rng: random.Random):
        self.cfg = cfg
        self._rng = rng
        self.state = _BotState()

    def latency_ready(self, now: float, latency_mult: float) -> bool:
        if now < self.state.next_action_time:
            return False
        # deterministic-ish latency with small jitter
        jitter = 0.25 * self.cfg.base_latency
        wait = max(0.01, (self.cfg.base_latency + self._rng.uniform(-jitter, jitter)) * latency_mult)
        self.state.next_action_time = now + wait
        return True

    def _tick(self, book: OrderBook) -> float:
        return float(getattr(book, "min_tick_size", 0.1) or 0.1)

    def _snap(self, book: OrderBook, px: float) -> float:
        t = self._tick(book)
        ticks = round(float(px) / t)
        return round(ticks * t, 10)

    def _should_refresh(self, book: OrderBook, bid: Optional[float], ask: Optional[float], now: float) -> bool:
        # No previous quote => publish
        if self.state.last_bid is None and self.state.last_ask is None:
            return True

        # Too soon => don't churn
        if (now - self.state.last_quote_time) < self.cfg.refresh_min_s:
            return False

        t = self._tick(book)
        thr = self.cfg.stickiness_ticks * t

        def changed(a: Optional[float], b: Optional[float]) -> bool:
            if a is None and b is None:
                return False
            if (a is None) != (b is None):
                return True
            return abs(float(a) - float(b)) >= thr

        return changed(self.state.last_bid, bid) or changed(self.state.last_ask, ask)

    def decide(
        self,
        *,
        now: float,
        book: OrderBook,
        trader: Trader,
        fair_value: float,
        volatility: float,
        user_toxicity: float,
        position_limit: int,
        tape: List[TradePrint],
    ) -> Tuple[Optional[float], Optional[float], List[Tuple[Side, int]]]:
        """
        Returns:
          (bid_px, ask_px, ioc_orders=[(side, qty), ...])
        Default: do nothing.
        """
        return None, None, []


class HFTMarketMaker(BaseBot):
    """
    The "Citadels":
    - Always posts two-sided quotes (unless at limits)
    - Tight in calm markets, widens/skews hard vs toxicity + inventory
    - Uses user_toxicity + its own adverse_selection_score to protect itself
    """
    def decide(self, *, now, book, trader, fair_value, volatility, user_toxicity, position_limit, tape):
        pos = trader.position
        vol = max(0.25, float(volatility))

        # Toxicity proxy: combine user toxicity + own adverse selection (if you wire it)
        own_tox = float(getattr(trader, "adverse_selection_score", 0.0))
        tox = abs(user_toxicity) * 0.7 + abs(own_tox) * 0.3

        # Widen on toxicity (pulling is optional, but widening gives better game feel)
        tox_mult = 1.0 + self.cfg.toxicity_sensitivity * max(0.0, tox) * 0.12

        # Base spread scales with vol, then widen with tox
        base_spread = (0.9 + 1.2 * vol) * tox_mult
        spread = max(0.8, min(5.0, base_spread))

        # Inventory skew: move reservation price against inventory
        inv = pos / max(1, position_limit)
        reservation = fair_value - (self.cfg.inventory_skew * self.cfg.risk_aversion * inv * (vol ** 2)) * 0.8

        bid = self._snap(book, reservation - spread / 2.0)
        ask = self._snap(book, reservation + spread / 2.0)

        if bid >= ask:
            ask = self._snap(book, bid + self._tick(book))

        if pos >= position_limit:
            bid = None
        if pos <= -position_limit:
            ask = None

        return bid, ask, []


class MomentumTrader(BaseBot):
    """
    The "Trend Followers":
    - Watches *order flow imbalance* from tape (aggressive buying/selling)
    - Also uses a mid-price EMA trend filter
    - Takes liquidity (IOC) when signal triggers
    """
    def decide(self, *, now, book, trader, fair_value, volatility, user_toxicity, position_limit, tape):
        bb, ba = book.get_best_bid_ask()
        if bb is None or ba is None:
            return None, None, []

        mid = (bb + ba) / 2.0
        fast = self.state.ema_fast.update(mid)
        slow = self.state.ema_slow.update(mid)
        trend = fast - slow

        # Flow imbalance: + means more aggressive buys
        flow = 0.0
        for p in tape[-12:]:
            flow += (p.qty if p.taker_side == "buy" else -p.qty)

        vol = max(0.25, float(volatility))
        thr_trend = 0.25 * vol
        thr_flow = 2.0

        iocs: List[Tuple[Side, int]] = []
        if trader.position < position_limit and (trend > thr_trend) and (flow > thr_flow):
            if self._rng.random() < self.cfg.aggression:
                iocs.append(("buy", 1))
        if trader.position > -position_limit and (trend < -thr_trend) and (flow < -thr_flow):
            if self._rng.random() < self.cfg.aggression:
                iocs.append(("sell", 1))

        # Passive leaning quotes (optional; makes them present even when not taking)
        spread = max(1.0, min(4.0, 1.2 + 0.9 * vol))
        lean = max(-1.0, min(1.0, trend / max(1e-6, 2.0 * thr_trend))) * 0.25 * spread

        bid = self._snap(book, fair_value - spread / 2.0 + lean)
        ask = self._snap(book, fair_value + spread / 2.0 + lean)

        if trader.position >= position_limit:
            bid = None
        if trader.position <= -position_limit:
            ask = None

        return bid, ask, iocs


class Arbitrageur(BaseBot):
    """
    The "Vultures":
    - Calculates fair value (passed in)
    - If mid deviates enough, hits the book (IOC) to correct
    - Otherwise may quote near FV (optional)
    """
    def decide(self, *, now, book, trader, fair_value, volatility, user_toxicity, position_limit, tape):
        bb, ba = book.get_best_bid_ask()
        if bb is None or ba is None:
            return None, None, []

        mid = (bb + ba) / 2.0
        vol = max(0.25, float(volatility))

        # Entry threshold: how wrong must the market be vs FV
        edge = max(0.8, 0.9 * vol)

        iocs: List[Tuple[Side, int]] = []
        if (mid < fair_value - edge) and (trader.position < position_limit):
            if self._rng.random() < self.cfg.aggression:
                iocs.append(("buy", 1))
        elif (mid > fair_value + edge) and (trader.position > -position_limit):
            if self._rng.random() < self.cfg.aggression:
                iocs.append(("sell", 1))

        # Resting quote around FV (keeps them visible)
        spread = max(1.0, min(4.0, 1.0 + 0.7 * vol))
        bid = self._snap(book, fair_value - spread / 2.0)
        ask = self._snap(book, fair_value + spread / 2.0)

        if trader.position >= position_limit:
            bid = None
        if trader.position <= -position_limit:
            ask = None

        return bid, ask, iocs


class NoiseTrader(BaseBot):
    """
    The "Retail":
    - Random IOC buys/sells regardless of FV
    - Also posts wide quotes sometimes (optional)
    """
    def decide(self, *, now, book, trader, fair_value, volatility, user_toxicity, position_limit, tape):
        iocs: List[Tuple[Side, int]] = []

        # Random immediate liquidity needs
        if self._rng.random() < 0.08 and self._rng.random() < self.cfg.aggression:
            if self._rng.random() < 0.5 and trader.position < position_limit:
                iocs.append(("buy", 1))
            elif trader.position > -position_limit:
                iocs.append(("sell", 1))

        # Wide passive quote so it doesn't dominate liquidity
        vol = max(0.25, float(volatility))
        spread = 3.5 + 0.8 * vol
        bid = self._snap(book, fair_value - spread / 2.0)
        ask = self._snap(book, fair_value + spread / 2.0)

        if trader.position >= position_limit:
            bid = None
        if trader.position <= -position_limit:
            ask = None

        return bid, ask, iocs


class BotManager:
    """
    Manager that:
    - Creates MANY bots per archetype (ecosystem)
    - Lets bots both PROVIDE liquidity (quotes) and TAKE liquidity (IOC)
    - Returns IOC order_ids so the simulator can cancel leftovers after matching
    """
    def __init__(self, difficulty_config, seed: Optional[int] = None):
        self.config = difficulty_config
        self._rng = random.Random(seed if seed is not None else 12345)

        self._bots: Dict[str, BaseBot] = {}
        self._bot_cfgs: List[BotConfig] = self._build_roster()
        self._tape: List[TradePrint] = []

        for cfg in self._bot_cfgs:
            self._bots[cfg.name] = self._make_bot(cfg)

    def _make_bot(self, cfg: BotConfig) -> BaseBot:
        if cfg.bot_type == "hft_mm":
            return HFTMarketMaker(cfg, rng=self._rng)
        if cfg.bot_type == "momentum":
            return MomentumTrader(cfg, rng=self._rng)
        if cfg.bot_type == "arbitrage":
            return Arbitrageur(cfg, rng=self._rng)
        return NoiseTrader(cfg, rng=self._rng)

    def _build_roster(self) -> List[BotConfig]:
        diff = str(getattr(self.config, "name", "MEDIUM")).upper()

        # More players => richer self-trading ecosystem
        if diff in ("HARD", "AXXELA"):
            mm_n, mom_n, arb_n, noise_n = 4, 4, 3, 10
            lat_mult = 0.9
            agg = 0.55
        elif diff == "EASY":
            mm_n, mom_n, arb_n, noise_n = 2, 2, 1, 6
            lat_mult = 1.6
            agg = 0.25
        else:
            mm_n, mom_n, arb_n, noise_n = 3, 3, 2, 8
            lat_mult = 1.2
            agg = 0.40

        out: List[BotConfig] = []

        for i in range(mm_n):
            out.append(
                BotConfig(
                    name=f"MM_Citadel_{i+1}",
                    bot_type="hft_mm",
                    base_latency=0.10 * lat_mult,
                    quote_size=1,
                    aggression=0.10,
                    risk_aversion=0.70,
                    inventory_skew=1.1,
                    toxicity_sensitivity=1.4,
                    refresh_min_s=0.18,
                    stickiness_ticks=1,
                )
            )

        for i in range(mom_n):
            out.append(
                BotConfig(
                    name=f"Mom_Trend_{i+1}",
                    bot_type="momentum",
                    base_latency=0.22 * lat_mult,
                    quote_size=1,
                    aggression=agg,
                    risk_aversion=0.25,
                    inventory_skew=0.4,
                    toxicity_sensitivity=0.6,
                    refresh_min_s=0.22,
                    stickiness_ticks=1,
                )
            )

        for i in range(arb_n):
            out.append(
                BotConfig(
                    name=f"Arb_Vulture_{i+1}",
                    bot_type="arbitrage",
                    base_latency=0.14 * lat_mult,
                    quote_size=1,
                    aggression=min(0.80, agg + 0.15),
                    risk_aversion=0.35,
                    inventory_skew=0.6,
                    toxicity_sensitivity=0.8,
                    refresh_min_s=0.20,
                    stickiness_ticks=1,
                )
            )

        for i in range(noise_n):
            out.append(
                BotConfig(
                    name=f"Retail_{i+1}",
                    bot_type="noise",
                    base_latency=0.55 * lat_mult,
                    quote_size=1,
                    aggression=0.35,
                    risk_aversion=0.10,
                    inventory_skew=0.2,
                    toxicity_sensitivity=0.2,
                    refresh_min_s=0.30,
                    stickiness_ticks=2,
                )
            )

        return out

    def get_bot_names(self) -> List[str]:
        return [c.name for c in self._bot_cfgs]

    def initialize_bots(self, traders: Dict[str, Trader]) -> None:
        # Keep it simple: ensure trader exists; optional cash seeding can be added later
        for c in self._bot_cfgs:
            if c.name not in traders:
                continue

    def update_quotes(
        self,
        *,
        book: OrderBook,
        traders: Dict[str, Trader],
        fair_value: float,
        volatility: float,
        user_toxicity: float,
        tape: Optional[List[TradePrint]] = None,
        now: Optional[float] = None,
        risk_manager:Optional[RiskManager]=None,
    ) -> List[int]:
        """
        Main entrypoint called by MarketSimulator each tick.

        Returns list of IOC order_ids to cancel after matching (true IOC semantics).
        """
        if now is None:
            now = time.time()

        if tape is not None:
            self._tape = tape

        latency_mult = float(getattr(self.config, "bot_latency_mult", 1.0))
        position_limit = int(getattr(self.config, "position_limit", 2))

        ioc_order_ids: List[int] = []

        def _allowed(trader: Trader, side: str, qty: int, px: float) -> bool:
            if risk_manager is None:
                return True
            ok, _ = risk_manager.validate_order(trader,side,qty,px)
            if not ok:
                return False
            total_depth = book.get_total_quantity("buy") + book.get_total_quantity("sell")
            if total_depth == 0:
                return True
            
            ok2, _ = risk_manager.check_concentration(trader,qty,total_depth)
            return ok2
        
        for cfg in self._bot_cfgs:
            bot = self._bots[cfg.name]
            trader = traders.get(cfg.name)
            if trader is None:
                continue

            if not bot.latency_ready(now, latency_mult=latency_mult):
                continue

            bid, ask, iocs = bot.decide(
                now=now,
                book=book,
                trader=trader,
                fair_value=float(fair_value),
                volatility=float(volatility),
                user_toxicity=float(user_toxicity),
                position_limit=position_limit,
                tape=self._tape,
            )

            # Passive quoting (cancel+replace only when needed)
            if bot._should_refresh(book, bid, ask, now):
                q = int(cfg.quote_size)

                can_bid = (bid is not None and bid > 0 and _allowed(trader,"buy",q,float(bid)))
                can_ask = (ask is not None and ask > 0 and _allowed(trader,"sell",q,float(ask)))

                if not can_bid:
                    book.cancel_orders(cfg.name,side="buy")
                else:
                    book.cancel_orders(cfg.name,side="buy")
                    book.add_order(Order(cfg.name,"buy",float(bid),q,float(now)))
                if not can_ask:
                    book.cancel_orders(cfg.name,side="sell")
                else:
                    book.cancel_orders(cfg.name,side="sell")
                    book.add_order(Order(cfg.name,"sell",float(ask),q,float(now)))

                bot.state.last_bid = bid if can_bid else None
                bot.state.last_ask = ask if can_ask else None
                bot.state.last_quote_time = now

            # Aggressive IOC intentions: place marketable limit at top-of-book
            bb, ba = book.get_best_bid_ask()
            for side, qty in iocs:
                if qty <= 0:
                    continue

                if side == "buy":
                    if ba is None:
                        continue
                    px = float(ba)
                    # Respect position limit
                    if trader.position + qty > position_limit:
                        continue
                else:
                    if bb is None:
                        continue
                    px = float(bb)
                    if trader.position - qty < -position_limit:
                        continue

                o = Order(cfg.name, side, px, int(qty), float(now))
                if _allowed(trader,side,int(qty),float(px)):
                    book.add_order(o)
                    ioc_order_ids.append(int(o.order_id))

        return ioc_order_ids
