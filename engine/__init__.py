"""
Domain layer - Core trading logic.
Pure functions, zero dependencies on UI/infrastructure.
"""
from .order_book import OrderBook, Order
from .matching_engine import MatchingEngine, MatchEvent
from .trader import Trader, Fill, TradeSide
from .bot_strategies import BotManager, BotConfig
from .risk_manager import RiskManager

__all__ = [
    'OrderBook',
    'Order',
    'MatchingEngine',
    'MatchEvent',
    'Trader',
    'Fill',
    'TradeSide',
    'BotManager',
    'BotConfig',
    'RiskManager',
]
