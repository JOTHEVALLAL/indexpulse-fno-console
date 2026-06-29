from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum
from typing import Iterable


class SignalStatus(str, Enum):
    READY = "READY"
    PREPARE = "PREPARE"
    WAIT = "WAIT"
    AVOID = "AVOID"
    NO_TRADE = "NO_TRADE"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class SetupName(str, Enum):
    VWAP_RECLAIM = "VWAP Reclaim"
    VWAP_REJECTION = "VWAP Rejection"
    OPENING_RANGE_BREAKOUT = "Opening Range Breakout"
    OPENING_RANGE_BREAKDOWN = "Opening Range Breakdown"
    EMA_PULLBACK_CONTINUATION = "EMA Pullback Continuation"
    PREVIOUS_DAY_LEVEL_BREAKOUT = "Previous-Day Level Breakout"


SUPPORTED_INSTRUMENTS = {"NIFTY 50", "BANK NIFTY"}


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class IndicatorSnapshot:
    vwap: float
    ema_9: float
    ema_21: float
    ema_50: float
    opening_range_high: float
    opening_range_low: float
    previous_day_high: float
    previous_day_low: float
    atr: float
    relative_volume: float
    india_vix: float
    trend_15m: Direction | None


@dataclass(frozen=True)
class MarketContext:
    instrument: str
    candle: Candle
    previous_candles: tuple[Candle, ...]
    indicators: IndicatorSnapshot

    @property
    def session_date(self) -> date:
        return self.candle.timestamp.date()


@dataclass(frozen=True)
class RuleConfig:
    observation_start: time = time(9, 15)
    first_eligible_alert: time = time(9, 25)
    opening_range_confirmed_at: time = time(9, 30)
    no_new_entry_after: time = time(14, 45)
    min_risk_reward: float = 1.5
    min_relative_volume: float = 1.15
    strong_relative_volume: float = 1.35
    max_entry_extension_atr: float = 0.65
    max_candle_range_atr: float = 1.8
    decisive_break_atr: float = 0.10
    prepare_trigger_atr: float = 0.20
    vwap_near_atr: float = 0.18
    ema_pullback_near_atr: float = 0.25
    target_1_atr: float = 1.0
    target_2_atr: float = 1.8
    stop_buffer_atr: float = 0.15
    high_vix_threshold: float = 22.0


@dataclass(frozen=True)
class Signal:
    instrument: str
    signal_direction: Direction | None
    setup_name: SetupName | None
    spot_price: float
    vwap_value: float
    entry_trigger: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    risk_reward_ratio: float | None
    confidence_level: str
    signal_status: SignalStatus
    invalidation_condition: str
    suggested_option_side: str | None
    alert_timestamp: datetime
    reason: str


@dataclass
class SignalEvaluator:
    config: RuleConfig = field(default_factory=RuleConfig)
    _alerted_keys: set[tuple[date, str, Direction, SetupName]] = field(default_factory=set)

    def evaluate(self, context: MarketContext) -> Signal:
        validation_signal = self._validate_context(context)
        if validation_signal:
            return validation_signal

        safety_signal = self._apply_global_safety_gates(context)
        if safety_signal:
            return safety_signal

        candidates = self._find_candidates(context)
        if not candidates:
            return self._empty_signal(context, SignalStatus.WAIT, "No setup is confirmed yet.")

        ready_candidates = [candidate for candidate in candidates if candidate.signal_status == SignalStatus.READY]
        if ready_candidates:
            signal = max(ready_candidates, key=lambda item: item.risk_reward_ratio or 0.0)
            key = (context.session_date, signal.instrument, signal.signal_direction, signal.setup_name)
            if key in self._alerted_keys:
                return self._empty_signal(
                    context,
                    SignalStatus.WAIT,
                    "Duplicate alert suppressed for this instrument, direction, and setup.",
                )
            self._alerted_keys.add(key)
            return signal

        return max(candidates, key=lambda item: self._status_rank(item.signal_status))

    def _validate_context(self, context: MarketContext) -> Signal | None:
        if context.instrument not in SUPPORTED_INSTRUMENTS:
            return self._empty_signal(context, SignalStatus.NO_TRADE, "Unsupported instrument.")
        if context.indicators.atr <= 0:
            return self._empty_signal(context, SignalStatus.NO_TRADE, "ATR must be positive to define risk.")
        if not context.previous_candles:
            return self._empty_signal(context, SignalStatus.WAIT, "Previous candle context is required.")
        return None

    def _apply_global_safety_gates(self, context: MarketContext) -> Signal | None:
        current_time = context.candle.timestamp.time()
        if current_time < self.config.first_eligible_alert:
            return self._empty_signal(context, SignalStatus.WAIT, "Observation period is still active.")
        if current_time > self.config.no_new_entry_after:
            return self._empty_signal(context, SignalStatus.NO_TRADE, "No new entries after 14:45 IST.")
        if self._candle_range(context.candle) > self.config.max_candle_range_atr * context.indicators.atr:
            return self._empty_signal(context, SignalStatus.AVOID, "Current candle movement is unstable.")
        if context.indicators.india_vix >= self.config.high_vix_threshold and context.indicators.relative_volume < self.config.strong_relative_volume:
            return self._empty_signal(context, SignalStatus.AVOID, "India VIX is elevated without strong participation.")
        return None

    def _find_candidates(self, context: MarketContext) -> list[Signal]:
        candidates: list[Signal] = []
        candidates.extend(self._vwap_candidates(context))
        candidates.extend(self._opening_range_candidates(context))
        candidates.extend(self._ema_pullback_candidates(context))
        candidates.extend(self._previous_day_level_candidates(context))
        return candidates

    def _vwap_candidates(self, context: MarketContext) -> Iterable[Signal]:
        candle = context.candle
        prev = context.previous_candles[-1]
        indicators = context.indicators
        near_vwap = abs(prev.close - indicators.vwap) <= self.config.vwap_near_atr * indicators.atr

        if (prev.close <= indicators.vwap or near_vwap) and candle.close > indicators.vwap:
            yield self._build_signal(
                context,
                Direction.BULLISH,
                SetupName.VWAP_RECLAIM,
                entry=candle.high,
                stop=min(candle.low, indicators.vwap - self.config.stop_buffer_atr * indicators.atr),
            )

        if (prev.close >= indicators.vwap or near_vwap) and candle.close < indicators.vwap:
            yield self._build_signal(
                context,
                Direction.BEARISH,
                SetupName.VWAP_REJECTION,
                entry=candle.low,
                stop=max(candle.high, indicators.vwap + self.config.stop_buffer_atr * indicators.atr),
            )

        bullish_prepare = prev.close <= indicators.vwap and candle.close <= indicators.vwap
        if bullish_prepare and indicators.vwap - candle.close <= self.config.prepare_trigger_atr * indicators.atr:
            yield self._build_signal(
                context,
                Direction.BULLISH,
                SetupName.VWAP_RECLAIM,
                entry=indicators.vwap + self.config.decisive_break_atr * indicators.atr,
                stop=min(candle.low, indicators.vwap - self.config.stop_buffer_atr * indicators.atr),
                developing=True,
            )

        bearish_prepare = prev.close >= indicators.vwap and candle.close >= indicators.vwap
        if bearish_prepare and candle.close - indicators.vwap <= self.config.prepare_trigger_atr * indicators.atr:
            yield self._build_signal(
                context,
                Direction.BEARISH,
                SetupName.VWAP_REJECTION,
                entry=indicators.vwap - self.config.decisive_break_atr * indicators.atr,
                stop=max(candle.high, indicators.vwap + self.config.stop_buffer_atr * indicators.atr),
                developing=True,
            )

    def _opening_range_candidates(self, context: MarketContext) -> Iterable[Signal]:
        if context.candle.timestamp.time() < self.config.opening_range_confirmed_at:
            return

        candle = context.candle
        indicators = context.indicators
        break_buffer = self.config.decisive_break_atr * indicators.atr

        if candle.close >= indicators.opening_range_high + break_buffer:
            yield self._build_signal(
                context,
                Direction.BULLISH,
                SetupName.OPENING_RANGE_BREAKOUT,
                entry=candle.high,
                stop=min(indicators.opening_range_high, candle.low) - self.config.stop_buffer_atr * indicators.atr,
            )
        elif 0 <= indicators.opening_range_high - candle.close <= self.config.prepare_trigger_atr * indicators.atr:
            yield self._build_signal(
                context,
                Direction.BULLISH,
                SetupName.OPENING_RANGE_BREAKOUT,
                entry=indicators.opening_range_high + break_buffer,
                stop=min(indicators.opening_range_high, candle.low) - self.config.stop_buffer_atr * indicators.atr,
                developing=True,
            )

        if candle.close <= indicators.opening_range_low - break_buffer:
            yield self._build_signal(
                context,
                Direction.BEARISH,
                SetupName.OPENING_RANGE_BREAKDOWN,
                entry=candle.low,
                stop=max(indicators.opening_range_low, candle.high) + self.config.stop_buffer_atr * indicators.atr,
            )
        elif 0 <= candle.close - indicators.opening_range_low <= self.config.prepare_trigger_atr * indicators.atr:
            yield self._build_signal(
                context,
                Direction.BEARISH,
                SetupName.OPENING_RANGE_BREAKDOWN,
                entry=indicators.opening_range_low - break_buffer,
                stop=max(indicators.opening_range_low, candle.high) + self.config.stop_buffer_atr * indicators.atr,
                developing=True,
            )

    def _ema_pullback_candidates(self, context: MarketContext) -> Iterable[Signal]:
        candle = context.candle
        indicators = context.indicators
        levels = (indicators.ema_9, indicators.ema_21, indicators.vwap)
        near_support = any(abs(candle.low - level) <= self.config.ema_pullback_near_atr * indicators.atr for level in levels)
        near_resistance = any(abs(candle.high - level) <= self.config.ema_pullback_near_atr * indicators.atr for level in levels)
        bullish_stack = indicators.ema_9 > indicators.ema_21 > indicators.ema_50
        bearish_stack = indicators.ema_9 < indicators.ema_21 < indicators.ema_50

        if bullish_stack and near_support and candle.close > indicators.ema_9:
            yield self._build_signal(
                context,
                Direction.BULLISH,
                SetupName.EMA_PULLBACK_CONTINUATION,
                entry=candle.high,
                stop=min(candle.low, indicators.ema_21) - self.config.stop_buffer_atr * indicators.atr,
            )
        elif bullish_stack and near_support and candle.close <= indicators.ema_9:
            yield self._build_signal(
                context,
                Direction.BULLISH,
                SetupName.EMA_PULLBACK_CONTINUATION,
                entry=indicators.ema_9 + self.config.decisive_break_atr * indicators.atr,
                stop=min(candle.low, indicators.ema_21) - self.config.stop_buffer_atr * indicators.atr,
                developing=True,
            )

        if bearish_stack and near_resistance and candle.close < indicators.ema_9:
            yield self._build_signal(
                context,
                Direction.BEARISH,
                SetupName.EMA_PULLBACK_CONTINUATION,
                entry=candle.low,
                stop=max(candle.high, indicators.ema_21) + self.config.stop_buffer_atr * indicators.atr,
            )
        elif bearish_stack and near_resistance and candle.close >= indicators.ema_9:
            yield self._build_signal(
                context,
                Direction.BEARISH,
                SetupName.EMA_PULLBACK_CONTINUATION,
                entry=indicators.ema_9 - self.config.decisive_break_atr * indicators.atr,
                stop=max(candle.high, indicators.ema_21) + self.config.stop_buffer_atr * indicators.atr,
                developing=True,
            )

    def _previous_day_level_candidates(self, context: MarketContext) -> Iterable[Signal]:
        candle = context.candle
        indicators = context.indicators
        break_buffer = self.config.decisive_break_atr * indicators.atr

        if candle.close >= indicators.previous_day_high + break_buffer:
            yield self._build_signal(
                context,
                Direction.BULLISH,
                SetupName.PREVIOUS_DAY_LEVEL_BREAKOUT,
                entry=candle.high,
                stop=indicators.previous_day_high - self.config.stop_buffer_atr * indicators.atr,
            )
        elif 0 <= indicators.previous_day_high - candle.close <= self.config.prepare_trigger_atr * indicators.atr:
            yield self._build_signal(
                context,
                Direction.BULLISH,
                SetupName.PREVIOUS_DAY_LEVEL_BREAKOUT,
                entry=indicators.previous_day_high + break_buffer,
                stop=indicators.previous_day_high - self.config.stop_buffer_atr * indicators.atr,
                developing=True,
            )

        if candle.close <= indicators.previous_day_low - break_buffer:
            yield self._build_signal(
                context,
                Direction.BEARISH,
                SetupName.PREVIOUS_DAY_LEVEL_BREAKOUT,
                entry=candle.low,
                stop=indicators.previous_day_low + self.config.stop_buffer_atr * indicators.atr,
            )
        elif 0 <= candle.close - indicators.previous_day_low <= self.config.prepare_trigger_atr * indicators.atr:
            yield self._build_signal(
                context,
                Direction.BEARISH,
                SetupName.PREVIOUS_DAY_LEVEL_BREAKOUT,
                entry=indicators.previous_day_low - break_buffer,
                stop=indicators.previous_day_low + self.config.stop_buffer_atr * indicators.atr,
                developing=True,
            )

    def _build_signal(
        self,
        context: MarketContext,
        direction: Direction,
        setup: SetupName,
        entry: float,
        stop: float,
        developing: bool = False,
    ) -> Signal:
        indicators = context.indicators
        spot = context.candle.close
        risk = abs(entry - stop)
        if risk <= 0:
            return self._empty_signal(context, SignalStatus.NO_TRADE, "Invalid setup risk; stop loss is not defined correctly.")

        target_1 = entry + self.config.target_1_atr * indicators.atr if direction == Direction.BULLISH else entry - self.config.target_1_atr * indicators.atr
        target_2 = entry + self.config.target_2_atr * indicators.atr if direction == Direction.BULLISH else entry - self.config.target_2_atr * indicators.atr
        reward = abs(target_1 - entry)
        risk_reward = round(reward / risk, 2)

        status, reason = self._candidate_status(context, direction, entry, risk_reward, developing)
        confidence = self._confidence_level(context, direction, risk_reward)
        return Signal(
            instrument=context.instrument,
            signal_direction=direction,
            setup_name=setup,
            spot_price=spot,
            vwap_value=indicators.vwap,
            entry_trigger=round(entry, 2),
            stop_loss=round(stop, 2),
            target_1=round(target_1, 2),
            target_2=round(target_2, 2),
            risk_reward_ratio=risk_reward,
            confidence_level=confidence,
            signal_status=status,
            invalidation_condition=self._invalidation_condition(direction, stop),
            suggested_option_side="CALL" if direction == Direction.BULLISH else "PUT",
            alert_timestamp=context.candle.timestamp,
            reason=reason,
        )

    def _candidate_status(
        self,
        context: MarketContext,
        direction: Direction,
        entry: float,
        risk_reward: float,
        developing: bool,
    ) -> tuple[SignalStatus, str]:
        indicators = context.indicators
        if indicators.trend_15m and indicators.trend_15m != direction:
            return SignalStatus.WAIT, "15-minute trend confirmation conflicts with the setup."
        if indicators.relative_volume < self.config.min_relative_volume:
            return SignalStatus.WAIT, "Relative volume is below the configured confirmation threshold."
        if risk_reward < self.config.min_risk_reward:
            return SignalStatus.WAIT, "Risk-reward is below the configured minimum."

        moved_beyond_entry = context.candle.close - entry if direction == Direction.BULLISH else entry - context.candle.close
        if moved_beyond_entry > self.config.max_entry_extension_atr * indicators.atr:
            return SignalStatus.AVOID, "Price has already moved excessively beyond the entry trigger."
        if developing:
            return SignalStatus.PREPARE, "Valid setup is developing close to the entry trigger."

        return SignalStatus.READY, "Setup confirmed with acceptable risk, volume, and trend context."

    def _confidence_level(self, context: MarketContext, direction: Direction, risk_reward: float) -> str:
        score = 0
        indicators = context.indicators
        if indicators.trend_15m == direction:
            score += 1
        if indicators.relative_volume >= self.config.strong_relative_volume:
            score += 1
        if risk_reward >= self.config.min_risk_reward + 0.5:
            score += 1
        if indicators.india_vix < self.config.high_vix_threshold:
            score += 1

        if score >= 3:
            return "HIGH"
        if score == 2:
            return "MEDIUM"
        return "LOW"

    def _empty_signal(self, context: MarketContext, status: SignalStatus, reason: str) -> Signal:
        return Signal(
            instrument=context.instrument,
            signal_direction=None,
            setup_name=None,
            spot_price=context.candle.close,
            vwap_value=context.indicators.vwap,
            entry_trigger=None,
            stop_loss=None,
            target_1=None,
            target_2=None,
            risk_reward_ratio=None,
            confidence_level="LOW",
            signal_status=status,
            invalidation_condition=reason,
            suggested_option_side=None,
            alert_timestamp=context.candle.timestamp,
            reason=reason,
        )

    @staticmethod
    def alert_key(signal: Signal) -> str | None:
        if not signal.signal_direction or not signal.setup_name:
            return None
        session_date = signal.alert_timestamp.date().isoformat()
        return f"{session_date}|{signal.instrument}|{signal.signal_direction.value}|{signal.setup_name.value}"

    @staticmethod
    def _candle_range(candle: Candle) -> float:
        return candle.high - candle.low

    @staticmethod
    def _invalidation_condition(direction: Direction, stop: float) -> str:
        side = "below" if direction == Direction.BULLISH else "above"
        return f"Spot closes {side} stop loss {round(stop, 2)}."

    @staticmethod
    def _status_rank(status: SignalStatus) -> int:
        return {
            SignalStatus.READY: 4,
            SignalStatus.PREPARE: 3,
            SignalStatus.WAIT: 2,
            SignalStatus.AVOID: 1,
            SignalStatus.NO_TRADE: 1,
            SignalStatus.INVALIDATED: 1,
            SignalStatus.EXPIRED: 1,
        }[status]
