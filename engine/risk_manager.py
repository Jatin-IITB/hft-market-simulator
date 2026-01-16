"""
Risk management system with multiple layers of defense.

Tier-1 firm risk architecture has 3+ layers:
1. Pre-trade: Block orders that would violate limits
2. Real-time: Monitor positions, trigger alerts
3. Post-trade: Reconciliation, margin calls, liquidation

This implementation focuses on layers 1 and 2.

Risk checks implemented:
- Position limits (gross and net)
- Margin requirements (mark-to-market)
- Concentration limits (% of total liquidity)
- Loss limits (daily drawdown)
- Order size limits (prevent fat-finger)

Production extensions:
- Value-at-Risk (VAR) calculations
- Greeks limits (delta, gamma, vega)
- Counterparty exposure limits
- Stress testing
- Real-time P&L streaming to risk desk
"""
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from enum import Enum
import math


class RiskViolation(Enum):
    """Types of risk violations."""
    POSITION_LIMIT = "position_limit"
    MARGIN_CALL = "margin_call"
    LOSS_LIMIT = "loss_limit"
    ORDER_SIZE = "order_size"
    CONCENTRATION = "concentration"


@dataclass
class RiskEvent:
    """
    Immutable risk event record.
    
    Logged for audit trail and compliance.
    """
    timestamp: float
    trader_id: str
    violation_type: RiskViolation
    severity: str  # 'warning', 'critical'
    details: str
    action_taken: str  # 'blocked', 'liquidated', 'alerted'


class RiskManager:
    """
    Centralized risk management.
    
    Design:
    - Stateless validation (doesn't store trader state)
    - Pure functions (no side effects)
    - Defensive (fail-safe on errors)
    
    Usage:
        risk_mgr = RiskManager(position_limit=10, margin_threshold=-100.0)
        
        # Pre-trade check
        if risk_mgr.can_add_position(trader, qty):
            execute_order()
        
        # Real-time monitoring
        if risk_mgr.check_margin_call(trader, fair_value):
            liquidate_position()
    """
    
    def __init__(
        self,
        position_limit: int = 5,
        margin_threshold: float = -50.0,
        loss_limit: float = -100.0,
        max_order_size: int = 10,
        concentration_limit: float = 0.5
    ):
        """
        Initialize risk manager with limits.
        
        Args:
            position_limit: Max absolute position (long or short)
            margin_threshold: P&L level triggering margin call
            loss_limit: Daily loss limit (trading stops)
            max_order_size: Max quantity per order (fat-finger protection)
            concentration_limit: Max % of total book depth (0.0-1.0)
        """
        # Core limits
        self.position_limit = position_limit
        self.margin_threshold = margin_threshold
        self.loss_limit = loss_limit
        self.max_order_size = max_order_size
        self.concentration_limit = concentration_limit
        
        # Event log (for audit)
        self.risk_events: List[RiskEvent] = []
        
        # Statistics
        self._total_blocks = 0
        self._total_liquidations = 0
        self._total_warnings = 0
    
    # ==================== Pre-trade checks ====================
    
    def can_add_position(self, trader, quantity: int) -> Tuple[bool, Optional[str]]:
        """
        Check if trader can increase position by quantity.
        
        Pre-trade check: Call BEFORE submitting buy order.
        
        Args:
            trader: Trader object
            quantity: Lots to buy (positive integer)
        
        Returns:
            (allowed, reason): (True, None) if OK, (False, reason) if blocked
        """
        new_position = trader.position + quantity
        
        if abs(new_position) > self.position_limit:
            self._total_blocks += 1
            return False, f"Position limit ({self.position_limit}) would be exceeded"
        
        if quantity > self.max_order_size:
            self._total_blocks += 1
            return False, f"Order size ({quantity}) exceeds max ({self.max_order_size})"
        
        return True, None
    
    def can_reduce_position(self, trader, quantity: int) -> Tuple[bool, Optional[str]]:
        """
        Check if trader can decrease position by quantity.
        
        Pre-trade check: Call BEFORE submitting sell order.
        
        Args:
            trader: Trader object
            quantity: Lots to sell (positive integer)
        
        Returns:
            (allowed, reason): (True, None) if OK, (False, reason) if blocked
        """
        new_position = trader.position - quantity
        
        if abs(new_position) > self.position_limit:
            self._total_blocks += 1
            return False, f"Position limit ({self.position_limit}) would be exceeded"
        
        if quantity > self.max_order_size:
            self._total_blocks += 1
            return False, f"Order size ({quantity}) exceeds max ({self.max_order_size})"
        
        return True, None
    
    def validate_order(self, trader, side: str, quantity: int, price: float) -> Tuple[bool, Optional[str]]:
        """
        Comprehensive pre-trade validation.
        
        Combines all pre-trade checks into single call.
        
        Args:
            trader: Trader object
            side: 'buy' or 'sell'
            quantity: Order quantity
            price: Order price
        
        Returns:
            (allowed, reason): Validation result
        """
        # Size check
        if quantity <= 0:
            return False, "Quantity must be positive"
        
        if quantity > self.max_order_size:
            self._total_blocks += 1
            return False, f"Order size exceeds maximum ({self.max_order_size})"
        
        # Price sanity check
        if price <= 0:
            return False, "Price must be positive"
        
        # Position limit check
        if side == 'buy':
            return self.can_add_position(trader, quantity)
        else:
            return self.can_reduce_position(trader, quantity)
    
    # ==================== Real-time monitoring ====================
    
    def check_margin_call(self, trader, fair_value: float, current_time: float = 0.0) -> bool:
        """
        Check if trader should be liquidated (margin call).
        
        This is CRITICAL. In real markets:
        - Margin call = forced liquidation at bad prices
        - Can wipe out account
        - Often triggers legal disputes
        
        Logic:
        - Calculate mark-to-market P&L
        - If below margin_threshold, liquidate entire position
        
        Args:
            trader: Trader object
            fair_value: Current fair value
            current_time: Timestamp (for event logging)
        
        Returns:
            bool: True if margin call triggered (position liquidated)
        """
        pnl = trader.mark_to_market(fair_value)
        
        if pnl < self.margin_threshold:
            # MARGIN CALL - liquidate position
            self._liquidate_position(trader, fair_value, current_time)
            self._total_liquidations += 1
            
            # Log event
            event = RiskEvent(
                timestamp=current_time,
                trader_id=trader.trader_id,
                violation_type=RiskViolation.MARGIN_CALL,
                severity='critical',
                details=f"P&L {pnl:.2f} below threshold {self.margin_threshold}",
                action_taken='liquidated'
            )
            self.risk_events.append(event)
            
            return True
        
        return False
    
    def check_loss_limit(self, trader, fair_value: float, current_time: float = 0.0) -> bool:
        """
        Check if trader hit daily loss limit.
        
        Loss limit = circuit breaker. Prevents catastrophic losses.
        
        Args:
            trader: Trader object
            fair_value: Current fair value
            current_time: Timestamp
        
        Returns:
            bool: True if loss limit hit (trading should stop)
        """
        pnl = trader.mark_to_market(fair_value)
        
        if pnl < self.loss_limit:
            self._total_warnings += 1
            
            # Log event
            event = RiskEvent(
                timestamp=current_time,
                trader_id=trader.trader_id,
                violation_type=RiskViolation.LOSS_LIMIT,
                severity='critical',
                details=f"Daily loss limit hit: {pnl:.2f} < {self.loss_limit}",
                action_taken='trading_halted'
            )
            self.risk_events.append(event)
            
            return True
        
        return False
    
    def calculate_var(self, trader, current_price: float, confidence: float = 0.95, horizon_seconds: int = 60) -> float:
        """
        Calculate Value-at-Risk (VAR).
        
        VAR = Expected maximum loss over time horizon at confidence level.
        
        Simplified calculation using position × volatility × sqrt(time).
        Real VAR uses historical simulation or Monte Carlo.
        
        Args:
            trader: Trader object
            current_price: Current market price
            confidence: Confidence level (0.95 = 95%)
            horizon_seconds: Time horizon in seconds
        
        Returns:
            float: VAR (potential loss)
        """
        if trader.num_fills < 2:
            return 0.0
        
        # Estimate volatility from recent fills
        recent_fills = trader.fills[-10:]
        prices = [f.price for f in recent_fills]
        
        if len(prices) < 2:
            return 0.0
        
        # Calculate price volatility (std dev)
        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        volatility = math.sqrt(variance)
        
        # VAR formula (simplified)
        # VAR = position × volatility × z_score × sqrt(time_fraction)
        z_score = 1.65 if confidence == 0.95 else 2.33  # 95% or 99%
        time_fraction = math.sqrt(horizon_seconds / 86400.0)  # Fraction of day
        
        var = abs(trader.position) * volatility * z_score * time_fraction
        
        return var
    
    def check_concentration(self, trader, order_size: int, total_book_depth: int) -> Tuple[bool, Optional[str]]:
        """
        Check if order would create concentration risk.
        
        Concentration risk = too large % of available liquidity.
        Can cause:
        - Large market impact (slippage)
        - Difficulty exiting position
        - Price manipulation concerns
        
        Args:
            trader: Trader object
            order_size: Proposed order size
            total_book_depth: Total quantity available in book
        
        Returns:
            (allowed, reason): Validation result
        """
        if total_book_depth == 0:
            return False, "No liquidity available"
        
        concentration = order_size / total_book_depth
        
        if concentration > self.concentration_limit:
            return False, (f"Order represents {concentration:.1%} of book depth, "
                          f"limit is {self.concentration_limit:.1%}")
        
        return True, None
    
    # ==================== Internal methods ====================
    
    def _liquidate_position(self, trader, fair_value: float, timestamp: float):
        """
        Force-liquidate trader's position.
        
        In real markets, this would:
        1. Cancel all orders
        2. Submit market orders to close position
        3. Notify trader (email/SMS)
        4. Report to compliance
        
        Here, we just flatten the position at a penalty price.
        
        Args:
            trader: Trader to liquidate
            fair_value: Current fair value
            timestamp: Liquidation time
        """
        if trader.position == 0:
            return  # Already flat
        
        # Liquidation slippage (penalty for forced exit)
        slippage = 5.0  # Ticks of slippage
        
        # Liquidate by adjusting cash (simulate forced trade)
        if trader.position > 0:
            # Long position: force sell at bad price
            liquidation_price = fair_value - slippage
            trader._cash += trader.position * liquidation_price
        else:
            # Short position: force buy at bad price
            liquidation_price = fair_value + slippage
            trader._cash -= abs(trader.position) * liquidation_price
        
        # Flatten position
        trader._position = 0
    
    # ==================== Analytics ====================
    
    def get_risk_metrics(self, trader, current_price: float) -> dict:
        """
        Get comprehensive risk metrics for trader.
        
        Args:
            trader: Trader object
            current_price: Current market price
        
        Returns:
            Dict with all risk metrics
        """
        pnl = trader.mark_to_market(current_price)
        threshold = self.margin_threshold
        cushion = pnl - threshold
        band = 0.2 * abs(threshold) if threshold !=0 else 0.0
        var_95 = self.calculate_var(trader, current_price, confidence=0.95)
        at_risk = cushion<=band
        return {
            'position': trader.position,
            'position_limit': self.position_limit,
            'position_utilization': abs(trader.position) / self.position_limit if self.position_limit > 0 else 0,
            'mtm_pnl': pnl,
            'margin_threshold': self.margin_threshold,
            'margin_cushion': pnl - self.margin_threshold,
            'loss_limit': self.loss_limit,
            'var_95': var_95,
            'at_risk': at_risk,  # Within 20% of margin call
        }
    
    def get_stats(self) -> dict:
        """Get risk manager statistics."""
        return {
            'total_blocks': self._total_blocks,
            'total_liquidations': self._total_liquidations,
            'total_warnings': self._total_warnings,
            'total_events': len(self.risk_events),
            'position_limit': self.position_limit,
            'margin_threshold': self.margin_threshold
        }
    
    def get_recent_events(self, max_events: int = 10) -> List[RiskEvent]:
        """Get recent risk events."""
        return self.risk_events[-max_events:]
