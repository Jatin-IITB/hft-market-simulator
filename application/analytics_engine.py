"""
Analytics engine - performance metrics and attribution.

Calculations:
1. P&L attribution (gross, net, realized, unrealized)
2. Execution quality (VWAP, adverse fill rate)
3. Risk-adjusted returns (Sharpe, Sortino, max drawdown)
4. Comparative analytics (vs bots, vs settlement)

All functions are pure (stateless) - take trader state as input.

Used by:
- UI (performance displays)
- Session export (JSON reports)
- Leaderboard calculations
"""
import math
from typing import List, Dict, Tuple
from engine.trader import Trader, Fill


class AnalyticsEngine:
    """
    Pure analytics calculations.
    
    Design: All static methods - no state.
    This allows:
    - Easy testing
    - Parallel computation
    - Caching at call site
    """
    
    # ==================== P&L Attribution ====================
    
    @staticmethod
    def calculate_pnl_attribution(trader: Trader, settlement: float) -> Dict[str, float]:
        """
        Break down P&L into components.
        
        Components:
        - Realized P&L: From closed trades
        - Unrealized P&L: From open position
        - Fees paid: Total transaction costs
        - Gross P&L: Before fees
        - Net P&L: After fees
        
        Args:
            trader: Trader to analyze
            settlement: Settlement price (or current price)
        
        Returns:
            Dict with P&L breakdown
        """
        net_pnl = trader.mark_to_market(settlement)
        fees = trader.fees_paid
        gross_pnl = net_pnl + fees
        
        # Realized vs unrealized (simplified)
        # In reality, need to track cost basis per trade
        unrealized_pnl = trader.position * (settlement - trader.calculate_vwap() if trader.calculate_vwap() > 0 else 0)
        realized_pnl = gross_pnl - unrealized_pnl
        
        return {
            'gross_pnl': gross_pnl,
            'net_pnl': net_pnl,
            'realized_pnl': realized_pnl,
            'unrealized_pnl': unrealized_pnl,
            'fees_paid': fees,
            'fee_rate': (fees / gross_pnl * 100) if gross_pnl != 0 else 0.0
        }
    
    @staticmethod
    def calculate_execution_quality(trader: Trader, settlement: float) -> Dict[str, float]:
        """
        Measure execution quality.
        
        Metrics:
        - VWAP: Volume-weighted average price
        - Average edge: Profit per trade
        - Adverse fill rate: % of losing trades
        - Best execution: Distance from theoretical best
        
        Args:
            trader: Trader to analyze
            settlement: Settlement price
        
        Returns:
            Dict with execution metrics
        """
        fills = trader.fills
        
        if not fills:
            return {
                'vwap': 0.0,
                'avg_edge': 0.0,
                'adverse_fill_rate': 0.0,
                'vwap_vs_settlement': 0.0,
                'total_trades': 0
            }
        
        # VWAP
        vwap = trader.calculate_vwap()
        
        # Average edge per trade
        total_edge = sum(f.pnl_contribution(settlement) for f in fills)
        avg_edge = total_edge / len(fills)
        
        # Adverse fill rate (trades that lost money)
        adverse_count = sum(1 for f in fills if f.pnl_contribution(settlement) < 0)
        adverse_rate = (adverse_count / len(fills)) * 100.0
        
        # VWAP vs settlement (execution quality)
        # Positive = bought below settlement (good)
        # Negative = bought above settlement (bad)
        if trader.position > 0:
            vwap_vs_settlement = settlement - vwap
        elif trader.position < 0:
            vwap_vs_settlement = vwap - settlement
        else:
            vwap_vs_settlement = 0.0
        
        return {
            'vwap': vwap,
            'avg_edge': avg_edge,
            'adverse_fill_rate': adverse_rate,
            'vwap_vs_settlement': vwap_vs_settlement,
            'total_trades': len(fills)
        }
    
    @staticmethod
    def calculate_risk_adjusted_returns(trader: Trader, current_price: float, initial_capital: float = 1000.0) -> Dict[str, float]:
        """
        Calculate risk-adjusted performance metrics.
        
        Metrics:
        - Return %: Total return percentage
        - Sharpe ratio: Return per unit risk
        - Sortino ratio: Return per unit downside risk
        - Max drawdown: Largest peak-to-trough decline
        
        Args:
            trader: Trader to analyze
            current_price: Current market price
            initial_capital: Starting capital (for % calculations)
        
        Returns:
            Dict with risk-adjusted metrics
        """
        fills = trader.fills
        
        if not fills:
            return {
                'return_pct': 0.0,
                'sharpe_ratio': 0.0,
                'sortino_ratio': 0.0,
                'max_drawdown': 0.0,
                'volatility': 0.0
            }
        
        # Calculate P&L progression
        pnl_series = []
        running_pnl = 0.0
        
        for fill in fills:
            running_pnl += fill.pnl_contribution(current_price)
            pnl_series.append(running_pnl)
        
        # Return percentage
        final_pnl = trader.mark_to_market(current_price)
        return_pct = (final_pnl / initial_capital) * 100.0
        
        # Calculate returns (differences)
        if len(pnl_series) < 2:
            returns = [0.0]
        else:
            returns = [pnl_series[i] - pnl_series[i-1] for i in range(1, len(pnl_series))]
        
        # Volatility (std dev of returns)
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        volatility = math.sqrt(variance)
        
        # Sharpe ratio (return per unit total risk)
        sharpe = mean_return / volatility if volatility > 0 else 0.0
        
        # Sortino ratio (return per unit downside risk)
        downside_returns = [r for r in returns if r < 0]
        if downside_returns:
            downside_variance = sum(r ** 2 for r in downside_returns) / len(downside_returns)
            downside_vol = math.sqrt(downside_variance)
            sortino = mean_return / downside_vol if downside_vol > 0 else 0.0
        else:
            sortino = float('inf') if mean_return > 0 else 0.0
        
        # Max drawdown
        peak = pnl_series[0]
        max_drawdown = 0.0
        
        for pnl in pnl_series:
            if pnl > peak:
                peak = pnl
            drawdown = peak - pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        return {
            'return_pct': return_pct,
            'sharpe_ratio': sharpe,
            'sortino_ratio': sortino,
            'max_drawdown': max_drawdown,
            'volatility': volatility
        }
    
    @staticmethod
    def generate_performance_report(trader: Trader, settlement: float, initial_capital: float = 1000.0) -> Dict:
        """
        Generate comprehensive performance report.
        
        Combines all analytics into single report.
        
        Args:
            trader: Trader to analyze
            settlement: Settlement price
            initial_capital: Starting capital
        
        Returns:
            Dict with complete performance analysis
        """
        report = {}
        
        # P&L attribution
        report['pnl'] = AnalyticsEngine.calculate_pnl_attribution(trader, settlement)
        
        # Execution quality
        report['execution'] = AnalyticsEngine.calculate_execution_quality(trader, settlement)
        
        # Risk-adjusted returns
        report['risk_adjusted'] = AnalyticsEngine.calculate_risk_adjusted_returns(
            trader, settlement, initial_capital
        )
        
        # Basic stats
        report['summary'] = {
            'trader_id': trader.trader_id,
            'final_position': trader.position,
            'total_fills': trader.num_fills,
            'adverse_selection_score': trader.adverse_selection_score
        }
        
        return report
