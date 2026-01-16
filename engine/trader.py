"""
Trader entity with immutable state management and event sourcing principles.

Design philosophy:
1. Separation of concerns:
   - Trader tracks state (position, cash, fills)
   - Does NOT execute orders (that's MatchingEngine's job)
   - Does NOT enforce limits (that's RiskManager's job)
   
2. Immutable history:
   - All fills stored in immutable list
   - Can reconstruct state at any point in time
   - Critical for debugging, compliance, dispute resolution
   
3. Calculated fields:
   - PnL, VWAP, Greeks computed on demand
   - Not stored (avoid sync issues)
   - Pure functions of fill history + current market state
   
4. Adverse selection tracking:
   - Exponential moving average of fill quality
   - Used by bots to detect informed traders
   - Alpha signal in real markets

Production considerations:
- In real systems, this would be backed by database
- Fill history would be paginated (not in-memory list)
- Position reconciliation against exchange feeds
- Real-time P&L streaming to risk systems
"""
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum
import math


class TradeSide(Enum):
    """Trade direction enum for type safety."""
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Fill:
    """
    Immutable fill record.
    
    Represents a single execution (partial or full order fill).
    Immutability is CRITICAL for:
    - Audit trail (can't tamper with history)
    - Thread safety (can share across threads)
    - Functional programming (pure functions)
    
    Fields:
        price: Execution price
        quantity: Number of lots filled
        side: BUY or SELL
        timestamp: Execution time
        counterparty: Optional counterparty ID (for analysis)
        fee: Fee paid on this fill (taker fee)
    """
    price: float
    quantity: int
    side: TradeSide
    timestamp: float
    counterparty: Optional[str] = None
    fee: float = 0.0
    
    def __post_init__(self):
        """Validate fill invariants."""
        assert self.quantity > 0, "Fill quantity must be positive"
        assert self.price > 0, "Fill price must be positive"
        assert self.fee >= 0, "Fee cannot be negative"
    
    def notional_value(self) -> float:
        """Notional value of this fill (price × quantity)."""
        return self.price * self.quantity
    
    def pnl_contribution(self, settlement_price: float) -> float:
        """
        Calculate P&L contribution of this fill at settlement.
        
        Logic:
        - Buy: profit if settlement > fill price (bought cheap)
        - Sell: profit if settlement < fill price (sold high)
        
        Args:
            settlement_price: Fair value or settlement price
        
        Returns:
            float: P&L from this fill (excluding fees)
        """
        if self.side == TradeSide.BUY:
            return (settlement_price - self.price) * self.quantity
        else:  # SELL
            return (self.price - settlement_price) * self.quantity
    
    def signed_quantity(self) -> int:
        """Quantity with sign (+ for buy, - for sell)."""
        return self.quantity if self.side == TradeSide.BUY else -self.quantity


class Trader:
    """
    Trader entity with position and P&L tracking.
    
    State management:
        - Mutable state: position, cash, fees
        - Immutable history: fills list (append-only)
        - Computed on demand: PnL, VWAP, Greeks
    
    Thread safety:
        - NOT thread-safe by default
        - Apply fills from single thread (matching engine)
        - Read-only methods safe for concurrent access
    
    Invariants maintained:
        - position = sum(fill.signed_quantity())
        - cash = -sum(fill.notional_value() × sign)
        - fees_paid = sum(fill.fee)
    """
    
    def __init__(self, trader_id: str, is_bot: bool = True, initial_cash: float = 0.0):
        """
        Initialize trader.
        
        Args:
            trader_id: Unique trader identifier
            is_bot: True for bots, False for human user
            initial_cash: Starting cash (doesn't affect P&L calculation)
        """
        # Immutable attributes
        self.trader_id = trader_id
        self.is_bot = is_bot
        
        # Mutable state (managed by application layer)
        self._position = 0
        self._cash = initial_cash
        self._fees_paid = 0.0
        self._fills: List[Fill] = []
        
        # Adverse selection tracking (EMA of fill quality)
        self._adverse_selection_score = 0.0
        self._ema_alpha = 0.15  # Weight for new observations
        
        # Performance metrics cache (invalidated on new fills)
        self._metrics_cache: Optional[dict] = None
        self._cache_valid = False
    
    # ==================== Read-only properties ====================
    
    @property
    def position(self) -> int:
        """Current position (positive = long, negative = short, zero = flat)."""
        return self._position
    
    @property
    def cash(self) -> float:
        """
        Cash account balance.
        
        Note: This is NOT your P&L. It's the cumulative cash flow from trades.
        P&L = cash + (position × current_price)
        """
        return self._cash
    
    @property
    def fees_paid(self) -> float:
        """Total fees paid across all fills."""
        return self._fees_paid
    
    @property
    def fills(self) -> List[Fill]:
        """
        All fills (immutable copies).
        
        Returns a copy to prevent external mutation.
        In production, this would be paginated.
        """
        return list(self._fills)
    
    @property
    def adverse_selection_score(self) -> float:
        """
        Adverse selection score (toxicity metric).
        
        Interpretation:
        - Positive: You're trading at favorable prices (good!)
        - Negative: You're getting picked off (bad!)
        - Large magnitude: Strong signal
        
        Used by sophisticated bots to widen spreads against informed traders.
        """
        return self._adverse_selection_score
    
    @property
    def num_fills(self) -> int:
        """Total number of fills."""
        return len(self._fills)
    
    # ==================== Core calculations ====================
    
    def mark_to_market(self, mark_price: float) -> float:
        """
        Calculate mark-to-market P&L.
        
        Formula: cash + (position × mark_price)
        
        This is your TOTAL P&L including:
        - Realized P&L (closed trades)
        - Unrealized P&L (open position)
        - Fees paid
        
        Args:
            mark_price: Current fair value or market price
        
        Returns:
            float: Total P&L
        """
        return self._cash + (self._position * mark_price)
    
    def calculate_vwap(self) -> float:
        """
        Volume-weighted average price of all fills.
        
        VWAP is a key metric:
        - Lower VWAP (when long) = bought cheap
        - Higher VWAP (when short) = sold high
        - Compare to settlement to measure execution quality
        
        Returns:
            float: VWAP, or 0.0 if no fills
        
        Complexity: O(N) where N = number of fills
        """
        if not self._fills:
            return 0.0
        
        total_value = sum(f.notional_value() for f in self._fills)
        total_quantity = sum(f.quantity for f in self._fills)
        
        return total_value / total_quantity if total_quantity > 0 else 0.0
    
    def calculate_realized_pnl(self, current_price: float) -> float:
        """
        Calculate realized P&L (closed positions only).
        
        This is more complex than it looks. Need to track:
        - Cost basis of position
        - FIFO or LIFO accounting (we use average cost)
        
        For simplicity, we calculate total P&L and subtract unrealized.
        
        Args:
            current_price: Current market price
        
        Returns:
            float: Realized P&L
        """
        total_pnl = self.mark_to_market(current_price)
        unrealized_pnl = self._position * (current_price - self._get_average_cost())
        return total_pnl - unrealized_pnl
    
    def _get_average_cost(self) -> float:
        """
        Calculate average cost basis of current position.
        
        Returns:
            float: Average price paid (buys) or received (sells)
        """
        if not self._fills:
            return 0.0
        
        # Separate buys and sells
        buy_value = sum(f.notional_value() for f in self._fills if f.side == TradeSide.BUY)
        buy_qty = sum(f.quantity for f in self._fills if f.side == TradeSide.BUY)
        
        sell_value = sum(f.notional_value() for f in self._fills if f.side == TradeSide.SELL)
        sell_qty = sum(f.quantity for f in self._fills if f.side == TradeSide.SELL)
        
        # Net position
        if self._position > 0:
            # Long: average buy price
            return buy_value / buy_qty if buy_qty > 0 else 0.0
        elif self._position < 0:
            # Short: average sell price
            return sell_value / sell_qty if sell_qty > 0 else 0.0
        else:
            # Flat: doesn't matter
            return 0.0
    
    def calculate_return(self, current_price: float, initial_capital: float = 1000.0) -> float:
        """
        Calculate percentage return.
        
        Args:
            current_price: Current market price
            initial_capital: Starting capital (for % calculation)
        
        Returns:
            float: Return in percentage (e.g., 5.0 = 5%)
        """
        pnl = self.mark_to_market(current_price)
        return (pnl / initial_capital) * 100.0
    
    def calculate_sharpe_ratio(self, current_price: float, num_periods: int = 10) -> float:
        """
        Calculate simplified Sharpe ratio (risk-adjusted return).
        
        Uses last N fills to estimate return volatility.
        
        Args:
            current_price: Current market price
            num_periods: Number of recent fills to analyze
        
        Returns:
            float: Sharpe ratio (higher = better risk-adjusted returns)
        """
        if len(self._fills) < 2:
            return 0.0
        
        # Calculate returns for each fill
        recent_fills = self._fills[-num_periods:]
        returns = [f.pnl_contribution(current_price) for f in recent_fills]
        
        if not returns:
            return 0.0
        
        # Mean and std dev
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        
        if std_dev == 0:
            return 0.0
        
        return mean_return / std_dev
    
    # ==================== State mutations ====================
    
    def apply_fill(self, fill: Fill):
        """
        Apply a fill to trader state.
        
        This is the ONLY way to mutate trader state (except initialization).
        Called by matching engine after each execution.
        
        Updates:
        - Position (increase on buy, decrease on sell)
        - Cash (decrease on buy, increase on sell)
        - Fees
        - Fill history
        
        Args:
            fill: Fill to apply
        
        Invariants maintained:
        - position = sum of signed quantities
        - cash reflects cumulative cash flows
        """
        # Validate fill
        assert isinstance(fill, Fill), "Must be a Fill object"
        
        # Update position
        if fill.side == TradeSide.BUY:
            self._position += fill.quantity
            self._cash -= fill.notional_value()
        else:  # SELL
            self._position -= fill.quantity
            self._cash += fill.notional_value()
        
        # Deduct fees
        self._cash -= fill.fee
        self._fees_paid += fill.fee
        
        # Append to history (immutable)
        self._fills.append(fill)
        
        # Invalidate metrics cache
        self._cache_valid = False
    
    def update_adverse_selection(self, fill_price: float, fair_value: float, is_buyer: bool):
        """
        Update adverse selection score based on fill quality.
        
        Logic:
        - If you bought above fair value: adverse (negative score)
        - If you bought below fair value: favorable (positive score)
        - If you sold below fair value: adverse
        - If you sold above fair value: favorable
        
        Uses exponential moving average to track persistent patterns.
        
        Args:
            fill_price: Price you traded at
            fair_value: True fair value (post-reveal)
            is_buyer: True if you bought, False if you sold
        """
        if is_buyer:
            # Buyer: profit if FV > fill_price
            edge = fair_value - fill_price
        else:
            # Seller: profit if fill_price > FV
            edge = fill_price - fair_value
        
        # Update EMA
        self._adverse_selection_score = (
            (1 - self._ema_alpha) * self._adverse_selection_score +
            self._ema_alpha * edge
        )
    
    # ==================== Analytics ====================
    
    def get_fill_summary(self) -> dict:
        """
        Get summary statistics of fills.
        
        Returns:
            Dict with fill metrics
        """
        if not self._fills:
            return {
                'total_fills': 0,
                'buy_fills': 0,
                'sell_fills': 0,
                'total_volume': 0.0,
                'avg_fill_price': 0.0,
                'avg_fill_size': 0.0
            }
        
        buy_fills = [f for f in self._fills if f.side == TradeSide.BUY]
        sell_fills = [f for f in self._fills if f.side == TradeSide.SELL]
        
        total_volume = sum(f.notional_value() for f in self._fills)
        avg_price = self.calculate_vwap()
        avg_size = sum(f.quantity for f in self._fills) / len(self._fills)
        
        return {
            'total_fills': len(self._fills),
            'buy_fills': len(buy_fills),
            'sell_fills': len(sell_fills),
            'total_volume': total_volume,
            'avg_fill_price': avg_price,
            'avg_fill_size': avg_size
        }
    
    def get_performance_metrics(self, current_price: float) -> dict:
        """
        Get comprehensive performance metrics.
        
        Cached for performance (invalidated on new fills).
        
        Args:
            current_price: Current market price
        
        Returns:
            Dict with all performance metrics
        """
        if self._cache_valid and self._metrics_cache is not None:
            return self._metrics_cache
        
        metrics = {
            'position': self._position,
            'cash': self._cash,
            'fees_paid': self._fees_paid,
            'mtm_pnl': self.mark_to_market(current_price),
            'vwap': self.calculate_vwap(),
            'num_fills': len(self._fills),
            'adverse_selection_score': self._adverse_selection_score,
            'return_pct': self.calculate_return(current_price),
            'sharpe_ratio': self.calculate_sharpe_ratio(current_price),
            'avg_cost_basis': self._get_average_cost()
        }
        
        # Add fill summary
        metrics.update(self.get_fill_summary())
        
        # Cache it
        self._metrics_cache = metrics
        self._cache_valid = True
        
        return metrics
    
    def reset(self):
        """
        Reset trader state (for new session).
        
        WARNING: Clears all history. Use with caution.
        """
        self._position = 0
        self._cash = 0.0
        self._fees_paid = 0.0
        self._fills.clear()
        self._adverse_selection_score = 0.0
        self._cache_valid = False
    
    # ==================== Debugging ====================
    
    def __repr__(self):
        return (f"Trader(id={self.trader_id}, pos={self._position}, "
                f"cash=${self._cash:.2f}, fills={len(self._fills)}, "
                f"is_bot={self.is_bot})")
    
    def print_fill_history(self, max_fills: int = 10):
        """Print recent fill history (for debugging)."""
        print(f"\n=== Fill History for {self.trader_id} ===")
        recent = self._fills[-max_fills:]
        for i, fill in enumerate(recent, 1):
            print(f"{i}. {fill.side.value.upper()} {fill.quantity}@{fill.price:.1f} "
                  f"(fee: ${fill.fee:.2f})")
        print(f"Total fills: {len(self._fills)}\n")
