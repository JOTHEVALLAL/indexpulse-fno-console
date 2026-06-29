from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from momentum_edge.candle_utils import IST
from momentum_edge.lifecycle import (
    AlertEventType,
    LifecycleConfig,
    empty_state,
    expire_prior_session_actionable,
    event_key,
    evaluation_key,
    load_lifecycle_state,
    process_signal_evaluation,
    save_lifecycle_state,
)
from momentum_edge.live_snapshot import DerivedMetrics, LiveInstrumentSnapshot
from momentum_edge.rules import Candle, Direction, IndicatorSnapshot, MarketContext, Signal, SignalStatus, SetupName
from momentum_edge.scanner_state import DataMode, FreshnessState


def context(ts: datetime) -> MarketContext:
    candle = Candle(ts, 100, 110, 95, 106, 1000)
    previous = Candle(ts - timedelta(minutes=5), 98, 101, 94, 99, 900)
    indicators = IndicatorSnapshot(
        vwap=100,
        ema_9=104,
        ema_21=101,
        ema_50=98,
        opening_range_high=105,
        opening_range_low=95,
        previous_day_high=112,
        previous_day_low=90,
        atr=10,
        relative_volume=1.4,
        india_vix=14,
        trend_15m=Direction.BULLISH,
    )
    return MarketContext("NIFTY 50", candle, (previous,), indicators)


def signal(status: SignalStatus, ts: datetime, direction: Direction = Direction.BULLISH, setup: SetupName = SetupName.OPENING_RANGE_BREAKOUT) -> Signal:
    return Signal(
        instrument="NIFTY 50",
        signal_direction=direction if status in {SignalStatus.PREPARE, SignalStatus.READY, SignalStatus.INVALIDATED, SignalStatus.EXPIRED} else None,
        setup_name=setup if status in {SignalStatus.PREPARE, SignalStatus.READY, SignalStatus.INVALIDATED, SignalStatus.EXPIRED} else None,
        spot_price=106,
        vwap_value=100,
        entry_trigger=108 if status in {SignalStatus.PREPARE, SignalStatus.READY} else None,
        stop_loss=101 if status in {SignalStatus.PREPARE, SignalStatus.READY} else None,
        target_1=118 if status in {SignalStatus.PREPARE, SignalStatus.READY} else None,
        target_2=126 if status in {SignalStatus.PREPARE, SignalStatus.READY} else None,
        risk_reward_ratio=2.0 if status in {SignalStatus.PREPARE, SignalStatus.READY} else None,
        confidence_level="HIGH",
        signal_status=status,
        invalidation_condition="Spot closes below stop.",
        suggested_option_side="CALL" if direction == Direction.BULLISH else "PUT",
        alert_timestamp=ts,
        reason=f"{status.value} reason",
    )


def live_item(sig: Signal, cached: bool = False, alignment: str = "ALIGNED") -> LiveInstrumentSnapshot:
    return LiveInstrumentSnapshot(
        instrument=sig.instrument,
        context=context(sig.alert_timestamp),
        signal=sig,
        metrics=DerivedMetrics(
            body_percentage=0.6,
            upper_wick_percentage=0.1,
            lower_wick_percentage=0.3,
            distance_from_vwap=6,
            distance_from_trigger=-2 if sig.entry_trigger else None,
            futures_price=110,
            spot_price=106,
            futures_spot_basis=4,
            futures_vwap=104,
            futures_volume=2000,
            candle_alignment_status=alignment,
        ),
        last_completed_5m=sig.alert_timestamp,
        last_completed_15m=sig.alert_timestamp,
        vwap_source="NIFTY26JUNFUT",
        futures_symbol="NIFTY26JUNFUT",
        futures_expiry=sig.alert_timestamp.date(),
        is_cached=cached,
    )


class LifecycleTest(unittest.TestCase):
    def test_one_evaluation_per_completed_candle_and_rerun_dedup(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        item = live_item(signal(SignalStatus.PREPARE, ts))

        _record, event, processed = process_signal_evaluation(state, item.signal, DataMode.LIVE, item, FreshnessState.FRESH)
        _record2, event2, processed2 = process_signal_evaluation(state, item.signal, DataMode.LIVE, item, FreshnessState.FRESH)

        self.assertTrue(processed)
        self.assertFalse(processed2)
        self.assertIsNotNone(event)
        self.assertIsNone(event2)
        self.assertEqual(len(state.evaluation_keys), 1)

    def test_no_trade_to_prepare(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        process_signal_evaluation(state, signal(SignalStatus.NO_TRADE, ts), DataMode.LIVE)
        _record, event, _ = process_signal_evaluation(state, signal(SignalStatus.PREPARE, ts + timedelta(minutes=5)), DataMode.LIVE, live_item(signal(SignalStatus.PREPARE, ts + timedelta(minutes=5))), FreshnessState.FRESH)

        self.assertEqual(event.event_type, AlertEventType.PREPARE_NEW.value)

    def test_wait_to_prepare(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        process_signal_evaluation(state, signal(SignalStatus.WAIT, ts), DataMode.LIVE)
        _record, event, _ = process_signal_evaluation(state, signal(SignalStatus.PREPARE, ts + timedelta(minutes=5)), DataMode.LIVE, live_item(signal(SignalStatus.PREPARE, ts + timedelta(minutes=5))), FreshnessState.FRESH)

        self.assertEqual(event.event_type, AlertEventType.PREPARE_NEW.value)

    def test_prepare_to_ready(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        process_signal_evaluation(state, signal(SignalStatus.PREPARE, ts), DataMode.LIVE, live_item(signal(SignalStatus.PREPARE, ts)), FreshnessState.FRESH)
        _record, event, _ = process_signal_evaluation(state, signal(SignalStatus.READY, ts + timedelta(minutes=5)), DataMode.LIVE, live_item(signal(SignalStatus.READY, ts + timedelta(minutes=5))), FreshnessState.FRESH)

        self.assertEqual(event.event_type, AlertEventType.READY_UPGRADE.value)

    def test_direct_ready(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        _record, event, _ = process_signal_evaluation(state, signal(SignalStatus.READY, ts), DataMode.LIVE, live_item(signal(SignalStatus.READY, ts)), FreshnessState.FRESH)

        self.assertEqual(event.event_type, AlertEventType.READY_DIRECT.value)

    def test_unchanged_status_later_candle_no_event(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        process_signal_evaluation(state, signal(SignalStatus.WAIT, ts), DataMode.LIVE)
        _record, event, _ = process_signal_evaluation(state, signal(SignalStatus.WAIT, ts + timedelta(minutes=5)), DataMode.LIVE)

        self.assertIsNone(event)

    def test_prepare_expiry_after_configured_candles(self) -> None:
        state = empty_state()
        config = LifecycleConfig(prepare_expiry_candles=3)
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        event = None
        record = None
        for index in range(4):
            sig = signal(SignalStatus.PREPARE, ts + timedelta(minutes=5 * index))
            record, event, _ = process_signal_evaluation(state, sig, DataMode.LIVE, live_item(sig), FreshnessState.FRESH, config)

        self.assertEqual(record.current_status, SignalStatus.EXPIRED.value)
        self.assertEqual(event.event_type, AlertEventType.SIGNAL_EXPIRED.value)

    def test_structural_invalidation(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        process_signal_evaluation(state, signal(SignalStatus.PREPARE, ts), DataMode.LIVE, live_item(signal(SignalStatus.PREPARE, ts)), FreshnessState.FRESH)
        bad = signal(SignalStatus.WAIT, ts + timedelta(minutes=5))
        bad = Signal(**{**bad.__dict__, "reason": "15-minute trend confirmation conflicts with the setup."})
        record, _event, _ = process_signal_evaluation(state, bad, DataMode.LIVE)

        self.assertEqual(record.current_status, SignalStatus.INVALIDATED.value)

    def test_direction_change(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        process_signal_evaluation(state, signal(SignalStatus.READY, ts, Direction.BULLISH), DataMode.LIVE, live_item(signal(SignalStatus.READY, ts, Direction.BULLISH)), FreshnessState.FRESH)
        bearish = signal(SignalStatus.READY, ts + timedelta(minutes=5), Direction.BEARISH, SetupName.OPENING_RANGE_BREAKDOWN)
        _record, event, _ = process_signal_evaluation(state, bearish, DataMode.LIVE, live_item(bearish), FreshnessState.FRESH)

        self.assertEqual(event.event_type, AlertEventType.DIRECTION_CHANGED.value)

    def test_event_key_deduplication(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        item = live_item(signal(SignalStatus.READY, ts))
        _record, event, _ = process_signal_evaluation(state, item.signal, DataMode.LIVE, item, FreshnessState.FRESH)
        state.event_keys.append(event.event_key)
        _record2, event2, _ = process_signal_evaluation(state, signal(SignalStatus.READY, ts + timedelta(minutes=5)), DataMode.LIVE, live_item(signal(SignalStatus.READY, ts + timedelta(minutes=5))), FreshnessState.FRESH)

        self.assertIsNone(event2)

    def test_persistence_across_restart(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        process_signal_evaluation(state, signal(SignalStatus.READY, ts), DataMode.LIVE, live_item(signal(SignalStatus.READY, ts)), FreshnessState.FRESH)

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            save_lifecycle_state(state, path)
            loaded = load_lifecycle_state(path)

        self.assertEqual(len(loaded.records), 1)
        self.assertEqual(len(loaded.events), 1)

    def test_sample_live_state_separation(self) -> None:
        live_key = evaluation_key(DataMode.LIVE, datetime(2026, 6, 29, tzinfo=IST).date(), "NIFTY 50", datetime(2026, 6, 29, 9, 35, tzinfo=IST))
        sample_key = evaluation_key(DataMode.SAMPLE, datetime(2026, 6, 29, tzinfo=IST).date(), "NIFTY 50", datetime(2026, 6, 29, 9, 35, tzinfo=IST))

        self.assertNotEqual(live_key, sample_key)

    def test_session_date_rollover(self) -> None:
        old = evaluation_key(DataMode.LIVE, datetime(2026, 6, 29, tzinfo=IST).date(), "NIFTY 50", datetime(2026, 6, 29, 9, 35, tzinfo=IST))
        new = evaluation_key(DataMode.LIVE, datetime(2026, 6, 30, tzinfo=IST).date(), "NIFTY 50", datetime(2026, 6, 30, 9, 35, tzinfo=IST))

        self.assertNotEqual(old, new)

    def test_session_rollover_expires_prior_actionable(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 14, 30, tzinfo=IST)
        process_signal_evaluation(state, signal(SignalStatus.PREPARE, ts), DataMode.LIVE, live_item(signal(SignalStatus.PREPARE, ts)), FreshnessState.FRESH)

        events = expire_prior_session_actionable(state, DataMode.LIVE, datetime(2026, 6, 30).date(), datetime(2026, 6, 30, 9, 15, tzinfo=IST))

        self.assertEqual(state.records[0]["current_status"], SignalStatus.EXPIRED.value)
        self.assertEqual(events[0].event_type, AlertEventType.SIGNAL_EXPIRED.value)

    def test_stale_cached_ready_blocking(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        stale = Signal(**{**signal(SignalStatus.READY, ts).__dict__, "signal_status": SignalStatus.WAIT, "reason": "READY blocked because market data is STALE."})
        record, event, _ = process_signal_evaluation(state, stale, DataMode.LIVE, live_item(stale, cached=True), FreshnessState.STALE)

        self.assertEqual(record.current_status, SignalStatus.WAIT.value)
        self.assertIsNone(event)

    def test_misalignment_ready_blocking(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        blocked = Signal(**{**signal(SignalStatus.READY, ts).__dict__, "signal_status": SignalStatus.WAIT, "reason": "MISALIGNED"})
        record, event, _ = process_signal_evaluation(state, blocked, DataMode.LIVE, live_item(blocked, alignment="MISALIGNED"), FreshnessState.FRESH)

        self.assertEqual(record.current_status, SignalStatus.WAIT.value)
        self.assertIsNone(event)

    def test_confidence_breakdown_calculation(self) -> None:
        state = empty_state()
        ts = datetime(2026, 6, 29, 9, 35, tzinfo=IST)
        record, _event, _ = process_signal_evaluation(state, signal(SignalStatus.READY, ts), DataMode.LIVE, live_item(signal(SignalStatus.READY, ts)), FreshnessState.FRESH)

        self.assertIn("five_minute_trend_alignment", record.confidence_breakdown["components"])
        self.assertIn(record.confidence_breakdown["level"], {"HIGH", "MEDIUM", "LOW"})


if __name__ == "__main__":
    unittest.main()
