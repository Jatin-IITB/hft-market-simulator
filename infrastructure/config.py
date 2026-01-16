from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DifficultyConfig:
    """
    DifficultyConfig controls market/game parameters.

    Note:
    - position_limit is now intended to be a hard game rule (±2) across all difficulties.
    - difficulty mainly affects bot speed, event frequency, quote lifetime, and fees.
    """
    name: str
    round_time: int
    quote_lifetime: float

    # Hard game rule (user requirement): at most ±2 open positions.
    position_limit: int

    # Fees (optional). Set taker_fee=0.0 to disable.
    taker_fee: float

    # Bot difficulty knobs
    bot_latency_mult: float
    toxicity_threshold: float

    # Vol / events
    volatility_cap: float
    enable_sudden_events: bool

    # Session settings
    total_rounds: int = 6

    @staticmethod
    def EASY() -> "DifficultyConfig":
        return DifficultyConfig(
            name="EASY",
            round_time=120,
            quote_lifetime=9.0,
            position_limit=2,
            taker_fee=0.00,           # default: no fees for easy
            bot_latency_mult=2.0,     # slower bots
            toxicity_threshold=10.0,  # bots barely react to toxicity
            volatility_cap=3.0,
            enable_sudden_events=False,
            total_rounds=6,
        )

    @staticmethod
    def MEDIUM() -> "DifficultyConfig":
        return DifficultyConfig(
            name="MEDIUM",
            round_time=90,
            quote_lifetime=7.0,
            position_limit=2,
            taker_fee=0.10,
            bot_latency_mult=1.2,
            toxicity_threshold=4.0,
            volatility_cap=4.5,
            enable_sudden_events=True,
            total_rounds=6,
        )

    @staticmethod
    def HARD() -> "DifficultyConfig":
        return DifficultyConfig(
            name="HARD",
            round_time=75,
            quote_lifetime=6.0,
            position_limit=2,
            taker_fee=0.15,
            bot_latency_mult=0.9,
            toxicity_threshold=3.0,
            volatility_cap=6.0,
            enable_sudden_events=True,
            total_rounds=6,
        )

    @staticmethod
    def AXXELA() -> "DifficultyConfig":
        return DifficultyConfig(
            name="AXXELA",
            round_time=60,
            quote_lifetime=5.0,
            position_limit=2,
            taker_fee=0.20,
            bot_latency_mult=0.65,
            toxicity_threshold=2.0,
            volatility_cap=7.0,
            enable_sudden_events=True,
            total_rounds=6,
        )
