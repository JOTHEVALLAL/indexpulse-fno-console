from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .candle_utils import ensure_ist, next_expected_evaluation
from .diagnostics import append_snapshot_diagnostics
from .instrument_registry import resolve_instrument, resolve_nearest_monthly_future
from .kite_client import KiteAuthenticationError, KiteClient, KiteDataError, KiteValidationError
from .live_snapshot import DerivedMetrics, LiveInstrumentSnapshot, LiveSnapshot
from .market_data import HistoricalRangePlan, RawFuturesData, RawInstrumentData, build_market_context_with_futures, day_window, historical_range_plan
from .rules import Signal, SignalEvaluator, SignalStatus
from .scanner_state import DataMode, FreshnessState, ScannerCache, ScannerDiagnostics, ScannerState, freshness_state, should_fetch


LIVE_INSTRUMENTS = ("NIFTY 50", "BANK NIFTY")


def block_actionable_signal(signal: Signal, reason: str) -> Signal:
    if signal.signal_status not in {SignalStatus.READY, SignalStatus.PREPARE}:
        return signal
    return Signal(
        instrument=signal.instrument,
        signal_direction=signal.signal_direction,
        setup_name=signal.setup_name,
        spot_price=signal.spot_price,
        vwap_value=signal.vwap_value,
        entry_trigger=signal.entry_trigger,
        stop_loss=signal.stop_loss,
        target_1=signal.target_1,
        target_2=signal.target_2,
        risk_reward_ratio=signal.risk_reward_ratio,
        confidence_level=signal.confidence_level,
        signal_status=SignalStatus.WAIT,
        invalidation_condition=signal.invalidation_condition,
        suggested_option_side=signal.suggested_option_side,
        alert_timestamp=signal.alert_timestamp,
        reason=f"{signal.reason} {reason}",
    )


def block_ready_when_stale(signal: Signal, freshness: FreshnessState) -> Signal:
    if freshness != FreshnessState.STALE:
        return signal
    return block_actionable_signal(signal, "Actionable signal blocked because market data is STALE.")


def block_when_alignment_invalid(signal: Signal, alignment_status: str) -> Signal:
    if alignment_status == "ALIGNED":
        return signal
    if signal.signal_status not in {SignalStatus.READY, SignalStatus.PREPARE}:
        return replace(signal, reason=f"{signal.reason} Spot/futures candles are MISALIGNED; VWAP confirmation unavailable.")
    return block_actionable_signal(signal, "Actionable signal blocked because spot/futures candles are MISALIGNED.")


def block_when_cached(signal: Signal) -> Signal:
    return block_actionable_signal(signal, "Actionable signal blocked because displayed LIVE snapshot is CACHED after a fetch failure.")


def block_when_vwap_unavailable(signal: Signal, vwap_available: bool) -> Signal:
    if vwap_available:
        return signal
    if signal.signal_status not in {SignalStatus.READY, SignalStatus.PREPARE}:
        return replace(signal, reason=f"{signal.reason} Futures VWAP or volume confirmation is unavailable.")
    return block_actionable_signal(signal, "Actionable signal blocked because futures VWAP or volume confirmation is unavailable.")


def unavailable_diagnostics(error: str, now: datetime, cache: ScannerCache | None = None) -> ScannerDiagnostics:
    last_snapshot = cache.last_snapshot if cache else None
    previous = getattr(last_snapshot, "diagnostics", None)
    return ScannerDiagnostics(
        data_mode=DataMode.LIVE,
        scanner_state=ScannerState.LIVE_DATA_UNAVAILABLE,
        last_successful_fetch=getattr(previous, "last_successful_fetch", None),
        last_completed_5m_candle=getattr(previous, "last_completed_5m_candle", None),
        last_completed_15m_candle=getattr(previous, "last_completed_15m_candle", None),
        data_freshness=getattr(previous, "data_freshness", None),
        data_age_seconds=getattr(previous, "data_age_seconds", None),
        vwap_source=getattr(previous, "vwap_source", "UNAVAILABLE"),
        last_evaluation=getattr(previous, "last_evaluation", None),
        next_expected_evaluation=next_expected_evaluation(now),
        current_error=error,
        historical_range_from=getattr(previous, "historical_range_from", None),
        historical_range_to=getattr(previous, "historical_range_to", None),
        current_ist_time=ensure_ist(now),
        selected_trading_session=getattr(previous, "selected_trading_session", None),
    )


def load_previous_candle_session(client: KiteClient, instrument_token: int, plan: HistoricalRangePlan) -> list:
    last_error: Exception | None = None
    for session_date in plan.previous_day_candidates:
        try:
            previous_start, previous_end = day_window(session_date)
            candles = client.historical_candles(instrument_token, previous_start, previous_end, "day")
        except KiteValidationError:
            raise
        except KiteDataError as exc:
            last_error = exc
            continue
        if candles:
            return candles
    suffix = f" Last error: {last_error}" if last_error else ""
    raise KiteDataError(f"No previous trading-day candles found within bounded lookback.{suffix}")


def scan_live(
    client: KiteClient,
    now: datetime,
    cache: ScannerCache | None = None,
    evaluator: SignalEvaluator | None = None,
) -> LiveSnapshot:
    local_now = ensure_ist(now)
    scanner_cache = cache or ScannerCache()
    if not should_fetch(local_now, scanner_cache.last_fetch_attempt):
        if scanner_cache.last_snapshot is not None:
            previous_snapshot = scanner_cache.last_snapshot
            if not previous_snapshot.instruments:
                return replace(
                    previous_snapshot,
                    diagnostics=replace(
                        previous_snapshot.diagnostics,
                        next_expected_evaluation=next_expected_evaluation(local_now),
                    ),
                    evaluated_at=local_now,
                )
            latest_5m = max(item.last_completed_5m for item in previous_snapshot.instruments)
            data_age = (local_now - latest_5m).total_seconds()
            freshness = freshness_state(data_age)
            diagnostics = replace(
                previous_snapshot.diagnostics,
                scanner_state=ScannerState.LIVE_CACHED,
                data_freshness=freshness,
                data_age_seconds=data_age,
                next_expected_evaluation=next_expected_evaluation(local_now),
                current_error="Using cached LIVE snapshot during Streamlit rerun window.",
            )
            refreshed_items = tuple(
                replace(
                    item,
                    signal=block_when_cached(block_ready_when_stale(item.signal, freshness)),
                    is_cached=True,
                    action_block_reason="Cached LIVE snapshot.",
                )
                for item in previous_snapshot.instruments
            )
            return LiveSnapshot(refreshed_items, diagnostics, local_now)

    scanner_cache.last_fetch_attempt = local_now
    evaluator = evaluator or SignalEvaluator()

    try:
        nse_instruments = client.instruments("NSE")
        nfo_instruments = client.instruments("NFO")
        resolved = {name: resolve_instrument(name, nse_instruments) for name in (*LIVE_INSTRUMENTS, "INDIA VIX")}
        futures_resolved = {
            name: resolve_nearest_monthly_future(name, nfo_instruments, local_now.date())
            for name in LIVE_INSTRUMENTS
        }
        failures = [result.error for result in resolved.values() if not result.ok]
        failures.extend(result.error for result in futures_resolved.values() if not result.ok)
        if failures:
            raise KiteDataError("; ".join(error for error in failures if error))

        range_plan = historical_range_plan(local_now)
        vix_spec = resolved["INDIA VIX"].instrument
        assert vix_spec is not None
        india_vix_quote = client.quote(vix_spec.exchange, vix_spec.tradingsymbol)
        india_vix = float(india_vix_quote.get("last_price") or 0)

        built = []
        for name in LIVE_INSTRUMENTS:
            spec = resolved[name].instrument
            fut_spec = futures_resolved[name].instrument
            assert spec is not None
            assert fut_spec is not None
            quote = client.quote(spec.exchange, spec.tradingsymbol)
            futures_quote = client.quote(fut_spec.exchange, fut_spec.tradingsymbol)
            raw = RawInstrumentData(
                instrument=name,
                last_price=float(quote.get("last_price") or 0),
                candles_5m=client.historical_candles(spec.instrument_token, range_plan.range_5m.from_dt, range_plan.range_5m.to_dt, "5minute"),
                candles_15m=client.historical_candles(spec.instrument_token, range_plan.range_15m.from_dt, range_plan.range_15m.to_dt, "15minute"),
                previous_day_candles=load_previous_candle_session(client, spec.instrument_token, range_plan),
            )
            raw_futures = RawFuturesData(
                tradingsymbol=fut_spec.tradingsymbol,
                instrument_token=fut_spec.instrument_token,
                expiry=getattr(fut_spec, "expiry", None),
                last_price=float(futures_quote.get("last_price") or 0),
                candles_5m=client.historical_candles(fut_spec.instrument_token, range_plan.range_5m.from_dt, range_plan.range_5m.to_dt, "5minute"),
            )
            built_context = build_market_context_with_futures(raw, raw_futures, india_vix, local_now)
            signal = evaluator.evaluate(built_context.context)
            age_seconds = (local_now - built_context.context.candle.timestamp).total_seconds()
            freshness = freshness_state(age_seconds)
            signal = block_ready_when_stale(signal, freshness)
            signal = block_when_alignment_invalid(signal, built_context.alignment_status)
            signal = block_when_vwap_unavailable(signal, built_context.futures_vwap is not None and built_context.context.indicators.relative_volume > 0)
            distance_from_trigger = None
            if signal.entry_trigger is not None:
                distance_from_trigger = built_context.context.candle.close - signal.entry_trigger
            built.append(
                LiveInstrumentSnapshot(
                    instrument=name,
                    context=built_context.context,
                    signal=signal,
                    metrics=DerivedMetrics(
                        body_percentage=built_context.body_percentage,
                        upper_wick_percentage=built_context.upper_wick_percentage,
                        lower_wick_percentage=built_context.lower_wick_percentage,
                        distance_from_vwap=built_context.distance_from_vwap,
                        distance_from_trigger=distance_from_trigger,
                        futures_price=built_context.futures_price,
                        spot_price=raw.last_price,
                        futures_spot_basis=built_context.futures_spot_basis,
                        futures_open=None if built_context.futures_candle is None else built_context.futures_candle.open,
                        futures_high=None if built_context.futures_candle is None else built_context.futures_candle.high,
                        futures_low=None if built_context.futures_candle is None else built_context.futures_candle.low,
                        futures_close=None if built_context.futures_candle is None else built_context.futures_candle.close,
                        futures_vwap=built_context.futures_vwap,
                        futures_volume=None if built_context.futures_candle is None else built_context.futures_candle.volume,
                        candle_alignment_status=built_context.alignment_status,
                        candle_alignment_difference_seconds=built_context.alignment_difference_seconds,
                    ),
                    last_completed_5m=built_context.last_completed_5m,
                    last_completed_15m=built_context.last_completed_15m,
                    vwap_source=built_context.vwap_source,
                    futures_symbol=built_context.futures_symbol,
                    futures_expiry=built_context.futures_expiry,
                    action_block_reason=None if built_context.alignment_status == "ALIGNED" else "Spot/futures candle misalignment.",
                )
            )

        latest_5m = max(item.last_completed_5m for item in built)
        latest_15m = max(item.last_completed_15m for item in built)
        data_age = (local_now - latest_5m).total_seconds()
        diagnostics = ScannerDiagnostics(
            data_mode=DataMode.LIVE,
            scanner_state=ScannerState.LIVE_READY,
            last_successful_fetch=local_now,
            last_completed_5m_candle=latest_5m,
            last_completed_15m_candle=latest_15m,
            data_freshness=freshness_state(data_age),
            data_age_seconds=data_age,
            vwap_source=", ".join(sorted({item.vwap_source for item in built})),
            last_evaluation=local_now,
            next_expected_evaluation=next_expected_evaluation(local_now),
            current_error=None,
            historical_range_from=range_plan.range_5m.from_dt,
            historical_range_to=range_plan.range_5m.to_dt,
            current_ist_time=local_now,
            selected_trading_session=range_plan.selected_session.isoformat(),
        )
        snapshot = LiveSnapshot(tuple(built), diagnostics, local_now)
        scanner_cache.last_snapshot = snapshot
        scanner_cache.last_diagnostics = diagnostics
        append_snapshot_diagnostics(snapshot.instruments, local_now, data_age, diagnostics.data_freshness)
        return snapshot
    except (KiteAuthenticationError, KiteDataError, ValueError) as exc:
        if scanner_cache.last_snapshot is not None:
            previous_snapshot = scanner_cache.last_snapshot
            diagnostics = unavailable_diagnostics(str(exc), local_now, scanner_cache)
            cached_items = tuple(
                replace(
                    item,
                    signal=block_when_cached(block_ready_when_stale(item.signal, diagnostics.data_freshness or FreshnessState.STALE)),
                    is_cached=True,
                    action_block_reason="Cached LIVE snapshot after fetch failure.",
                )
                for item in previous_snapshot.instruments
            )
            return LiveSnapshot(cached_items, replace(diagnostics, scanner_state=ScannerState.LIVE_CACHED), local_now)
        return LiveSnapshot(tuple(), unavailable_diagnostics(str(exc), local_now, scanner_cache), local_now)
