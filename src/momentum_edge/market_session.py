from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum

from .candle_utils import ensure_ist


class MarketSessionState(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    MARKET_OPEN = "MARKET_OPEN"
    POST_MARKET = "POST_MARKET"
    NON_TRADING_DAY = "NON_TRADING_DAY"


class MarketFreshnessState(str, Enum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"
    NO_DATA = "NO DATA"
    PREVIOUS_SESSION = "PREVIOUS SESSION"
    MARKET_CLOSED = "MARKET CLOSED"


@dataclass(frozen=True)
class SessionStatus:
    session_state: MarketSessionState
    freshness_state: MarketFreshnessState
    signals_actionable: bool
    new_ready_allowed: bool
    new_alerts_allowed: bool
    message: str
    block_reason: str | None
    candle_age_seconds: float | None
    current_ist: datetime
    current_calendar_date: date


def get_market_session_state(now_ist: datetime) -> MarketSessionState:
    local = ensure_ist(now_ist)
    if local.weekday() >= 5:
        return MarketSessionState.NON_TRADING_DAY
    current_time = local.time()
    if current_time < time(9, 15):
        return MarketSessionState.PRE_MARKET
    if current_time <= time(15, 30):
        return MarketSessionState.MARKET_OPEN
    return MarketSessionState.POST_MARKET


def calculate_freshness_state(
    now_ist: datetime,
    latest_5m_candle: datetime | None,
    session_state: MarketSessionState | None = None,
) -> tuple[MarketFreshnessState, float | None]:
    local = ensure_ist(now_ist)
    resolved_session = session_state or get_market_session_state(local)
    if resolved_session == MarketSessionState.PRE_MARKET:
        age = None if latest_5m_candle is None else (local - ensure_ist(latest_5m_candle)).total_seconds()
        return MarketFreshnessState.PREVIOUS_SESSION, age
    if resolved_session in {MarketSessionState.POST_MARKET, MarketSessionState.NON_TRADING_DAY}:
        age = None if latest_5m_candle is None else (local - ensure_ist(latest_5m_candle)).total_seconds()
        return MarketFreshnessState.MARKET_CLOSED, age
    if latest_5m_candle is None:
        return MarketFreshnessState.NO_DATA, None
    age = (local - ensure_ist(latest_5m_candle)).total_seconds()
    if age <= 7 * 60:
        return MarketFreshnessState.LIVE, age
    if age <= 15 * 60:
        return MarketFreshnessState.DELAYED, age
    return MarketFreshnessState.STALE, age


def is_signal_generation_allowed(session_state: MarketSessionState, freshness_state: MarketFreshnessState) -> bool:
    return session_state == MarketSessionState.MARKET_OPEN and freshness_state == MarketFreshnessState.LIVE


def build_session_status_message(session_state: MarketSessionState, freshness_state: MarketFreshnessState) -> tuple[str, str | None]:
    if session_state == MarketSessionState.PRE_MARKET:
        return (
            "Showing previous trading session data. Live candle updates begin after market open.",
            "Blocked: pre-market session",
        )
    if session_state == MarketSessionState.POST_MARKET:
        return (
            "Showing final data from the latest completed trading session.",
            "Blocked: market is closed",
        )
    if session_state == MarketSessionState.NON_TRADING_DAY:
        return (
            "Non-trading day. Showing latest available session data.",
            "Blocked: market is closed",
        )
    if freshness_state == MarketFreshnessState.LIVE:
        return "Live index and futures data are updating normally.", None
    if freshness_state == MarketFreshnessState.DELAYED:
        return "Data updates are delayed. Review the last candle time before using any signal.", "Blocked: live candle feed is delayed"
    if freshness_state == MarketFreshnessState.NO_DATA:
        return "Live data is unavailable. Signals must not be treated as actionable until fresh candles resume.", "Blocked: latest 5-minute candle is unavailable"
    return "Live data is stale. Signals must not be treated as actionable until fresh candles resume.", "Blocked: live candle feed is stale"


def build_session_status(now_ist: datetime, latest_5m_candle: datetime | None) -> SessionStatus:
    local = ensure_ist(now_ist)
    session_state = get_market_session_state(local)
    freshness_state, age = calculate_freshness_state(local, latest_5m_candle, session_state)
    actionable = is_signal_generation_allowed(session_state, freshness_state)
    message, block_reason = build_session_status_message(session_state, freshness_state)
    return SessionStatus(
        session_state=session_state,
        freshness_state=freshness_state,
        signals_actionable=actionable,
        new_ready_allowed=actionable,
        new_alerts_allowed=actionable,
        message=message,
        block_reason=block_reason,
        candle_age_seconds=age,
        current_ist=local,
        current_calendar_date=local.date(),
    )
