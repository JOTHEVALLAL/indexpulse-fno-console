from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .rules import Candle, Direction, IndicatorSnapshot, MarketContext, Signal, SignalEvaluator


@dataclass(frozen=True)
class SampleScenario:
    name: str
    description: str
    context: MarketContext


def candle(
    hour: int,
    minute: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100_000,
) -> Candle:
    return Candle(
        timestamp=datetime(2026, 6, 29, hour, minute),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def indicators(**overrides: object) -> IndicatorSnapshot:
    values = {
        "vwap": 25_610.40,
        "ema_9": 25_650.0,
        "ema_21": 25_610.0,
        "ema_50": 25_540.0,
        "opening_range_high": 25_700.0,
        "opening_range_low": 25_480.0,
        "previous_day_high": 25_760.0,
        "previous_day_low": 25_360.0,
        "atr": 75.0,
        "relative_volume": 1.42,
        "india_vix": 13.8,
        "trend_15m": Direction.BULLISH,
    }
    values.update(overrides)
    return IndicatorSnapshot(**values)


def get_sample_scenarios() -> list[SampleScenario]:
    return [
        SampleScenario(
            name="NIFTY 50 ORB Ready",
            description="Opening range breakout confirmed with strong relative volume.",
            context=MarketContext(
                instrument="NIFTY 50",
                candle=candle(9, 35, 25_680.0, 25_720.0, 25_690.0, 25_715.0),
                previous_candles=(candle(9, 30, 25_625.0, 25_690.0, 25_610.0, 25_680.0),),
                indicators=indicators(),
            ),
        ),
        SampleScenario(
            name="NIFTY 50 VWAP Reclaim Prepare",
            description="Price is developing near a bullish VWAP reclaim trigger.",
            context=MarketContext(
                instrument="NIFTY 50",
                candle=candle(10, 20, 25_590.0, 25_606.0, 25_582.0, 25_604.0),
                previous_candles=(candle(10, 15, 25_570.0, 25_598.0, 25_560.0, 25_588.0),),
                indicators=indicators(
                    opening_range_high=25_760.0,
                    opening_range_low=25_430.0,
                    previous_day_high=25_820.0,
                    previous_day_low=25_300.0,
                ),
            ),
        ),
        SampleScenario(
            name="BANK NIFTY VWAP Rejection Prepare",
            description="Price is developing near a bearish VWAP rejection trigger.",
            context=MarketContext(
                instrument="BANK NIFTY",
                candle=candle(10, 5, 57_245.0, 57_252.0, 57_231.0, 57_236.0),
                previous_candles=(candle(10, 0, 57_210.0, 57_255.0, 57_205.0, 57_238.0),),
                indicators=indicators(
                    vwap=57_225.80,
                    ema_9=57_210.0,
                    ema_21=57_245.0,
                    ema_50=57_320.0,
                    opening_range_high=57_520.0,
                    opening_range_low=57_120.0,
                    previous_day_high=57_650.0,
                    previous_day_low=56_950.0,
                    atr=110.0,
                    relative_volume=1.38,
                    india_vix=15.1,
                    trend_15m=Direction.BEARISH,
                ),
            ),
        ),
        SampleScenario(
            name="NIFTY 50 Low Volume Wait",
            description="Directional breakout context exists but relative volume is not confirmed.",
            context=MarketContext(
                instrument="NIFTY 50",
                candle=candle(11, 10, 25_692.0, 25_718.0, 25_688.0, 25_712.0),
                previous_candles=(candle(11, 5, 25_640.0, 25_694.0, 25_620.0, 25_690.0),),
                indicators=indicators(relative_volume=0.92),
            ),
        ),
        SampleScenario(
            name="BANK NIFTY High VIX Avoid",
            description="Elevated India VIX without enough participation makes the setup unstable.",
            context=MarketContext(
                instrument="BANK NIFTY",
                candle=candle(12, 20, 57_040.0, 57_160.0, 57_030.0, 57_145.0),
                previous_candles=(candle(12, 15, 57_000.0, 57_060.0, 56_980.0, 57_030.0),),
                indicators=indicators(
                    vwap=57_225.80,
                    ema_9=57_080.0,
                    ema_21=57_020.0,
                    ema_50=56_940.0,
                    opening_range_high=57_120.0,
                    opening_range_low=56_850.0,
                    previous_day_high=57_300.0,
                    previous_day_low=56_700.0,
                    atr=125.0,
                    relative_volume=1.05,
                    india_vix=24.6,
                    trend_15m=Direction.BULLISH,
                ),
            ),
        ),
    ]


def evaluate_sample_scenarios() -> list[tuple[SampleScenario, Signal]]:
    evaluator = SignalEvaluator()
    return [(scenario, evaluator.evaluate(scenario.context)) for scenario in get_sample_scenarios()]
