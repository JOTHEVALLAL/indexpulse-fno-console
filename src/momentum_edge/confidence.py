from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any

from .live_snapshot import LiveInstrumentSnapshot
from .rules import Direction, MarketContext, Signal


@dataclass(frozen=True)
class ConfidenceBreakdown:
    level: str
    score: int
    components: dict[str, str]


def confidence_breakdown(signal: Signal, context: MarketContext, live_item: LiveInstrumentSnapshot | None = None) -> ConfidenceBreakdown:
    components: dict[str, str] = {}
    score = 0
    direction = signal.signal_direction

    ema_direction = None
    if context.indicators.ema_9 > context.indicators.ema_21:
        ema_direction = Direction.BULLISH
    elif context.indicators.ema_9 < context.indicators.ema_21:
        ema_direction = Direction.BEARISH
    components["five_minute_trend_alignment"] = "aligned" if direction and ema_direction == direction else "not_aligned"
    score += 1 if components["five_minute_trend_alignment"] == "aligned" else 0

    components["fifteen_minute_trend_alignment"] = "aligned" if direction and context.indicators.trend_15m == direction else "not_aligned"
    score += 1 if components["fifteen_minute_trend_alignment"] == "aligned" else 0

    vwap_ok = live_item is None or live_item.metrics.futures_vwap is not None
    components["futures_vwap_confirmation"] = "available" if vwap_ok else "unavailable"
    score += 1 if vwap_ok else 0

    components["relative_volume"] = "strong" if context.indicators.relative_volume >= 1.35 else "acceptable" if context.indicators.relative_volume >= 1.15 else "weak"
    score += 1 if components["relative_volume"] in {"strong", "acceptable"} else 0

    body = live_item.metrics.body_percentage if live_item else 0.5
    components["trigger_candle_quality"] = "clean" if body >= 0.45 else "weak"
    score += 1 if body >= 0.45 else 0

    rr = signal.risk_reward_ratio or 0
    components["risk_reward"] = "strong" if rr >= 2 else "acceptable" if rr >= 1.5 else "weak"
    score += 1 if rr >= 1.5 else 0

    if signal.entry_trigger is not None and signal.stop_loss is not None and context.indicators.atr > 0:
        stop_distance = abs(signal.entry_trigger - signal.stop_loss) / context.indicators.atr
    else:
        stop_distance = 99
    components["atr_normalized_stop_distance"] = "acceptable" if stop_distance <= 0.75 else "wide"
    score += 1 if stop_distance <= 0.75 else 0

    if signal.entry_trigger is not None and context.indicators.atr > 0:
        extension = abs(context.candle.close - signal.entry_trigger) / context.indicators.atr
    else:
        extension = 99
    components["extension_from_entry"] = "acceptable" if extension <= 0.65 else "extended"
    score += 1 if extension <= 0.65 else 0

    components["vix_environment"] = "calm" if context.indicators.india_vix < 18 else "elevated" if context.indicators.india_vix < 22 else "high"
    score += 1 if components["vix_environment"] in {"calm", "elevated"} else 0

    current_time = context.candle.timestamp.time()
    components["time_of_day_quality"] = "good" if time(9, 25) <= current_time <= time(14, 15) else "late_or_observation"
    score += 1 if components["time_of_day_quality"] == "good" else 0

    if score >= 8:
        level = "HIGH"
    elif score >= 5:
        level = "MEDIUM"
    else:
        level = "LOW"
    return ConfidenceBreakdown(level=level, score=score, components=components)


def confidence_to_record(breakdown: ConfidenceBreakdown) -> dict[str, Any]:
    return {"level": breakdown.level, "score": breakdown.score, "components": breakdown.components}
