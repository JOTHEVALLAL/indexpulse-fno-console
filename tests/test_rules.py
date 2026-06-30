from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from momentum_edge.alerts import format_telegram_alert, telegram_preview_context
import momentum_edge.alerts as alerts_module
from momentum_edge.history import append_alert, load_alert_history, save_alert_history
from momentum_edge.rules import (
    Candle,
    Direction,
    IndicatorSnapshot,
    MarketContext,
    SignalEvaluator,
    SignalStatus,
    SetupName,
)
from momentum_edge.sample_data import evaluate_sample_scenarios
from momentum_edge.scanner_state import DataMode
from momentum_edge.trades import add_active_trade, create_active_trade
from momentum_edge.watchlist import add_watchlist_entry, create_watchlist_entry


def candle(hour: int, minute: int, close: float, high: float | None = None, low: float | None = None) -> Candle:
    return Candle(
        timestamp=datetime(2026, 6, 29, hour, minute),
        open=close - 5,
        high=high if high is not None else close + 5,
        low=low if low is not None else close - 5,
        close=close,
        volume=100_000,
    )


def indicators(**overrides: object) -> IndicatorSnapshot:
    values = {
        "vwap": 24_000.0,
        "ema_9": 24_080.0,
        "ema_21": 24_030.0,
        "ema_50": 23_980.0,
        "opening_range_high": 24_100.0,
        "opening_range_low": 23_900.0,
        "previous_day_high": 24_250.0,
        "previous_day_low": 23_750.0,
        "atr": 60.0,
        "relative_volume": 1.4,
        "india_vix": 14.0,
        "trend_15m": Direction.BULLISH,
    }
    values.update(overrides)
    return IndicatorSnapshot(**values)


def context(
    current: Candle,
    previous: Candle | None = None,
    snapshot: IndicatorSnapshot | None = None,
    instrument: str = "NIFTY 50",
) -> MarketContext:
    return MarketContext(
        instrument=instrument,
        candle=current,
        previous_candles=(previous or candle(9, 25, 24_060),),
        indicators=snapshot or indicators(),
    )


class SignalEvaluatorTest(unittest.TestCase):
    def test_waits_during_observation_period(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 20, 24_120)))

        self.assertEqual(signal.signal_status, SignalStatus.WAIT)
        self.assertIn("Observation period", signal.reason)
        self.assertIsNone(signal.entry_trigger)

    def test_opening_range_breakout_ready_after_confirmation(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))

        self.assertEqual(signal.signal_status, SignalStatus.READY)
        self.assertEqual(signal.signal_direction, Direction.BULLISH)
        self.assertEqual(signal.setup_name, SetupName.OPENING_RANGE_BREAKOUT)
        self.assertEqual(signal.suggested_option_side, "CALL")
        self.assertIsNotNone(signal.stop_loss)
        self.assertIsNotNone(signal.risk_reward_ratio)
        self.assertGreaterEqual(signal.risk_reward_ratio or 0, 1.5)

    def test_low_relative_volume_prevents_ready_status(self) -> None:
        signal = SignalEvaluator().evaluate(
            context(
                candle(9, 35, 24_125, high=24_130, low=24_100),
                snapshot=indicators(relative_volume=0.9),
            )
        )

        self.assertEqual(signal.signal_status, SignalStatus.WAIT)
        self.assertIn("Relative volume", signal.reason)

    def test_opening_range_breakout_prepare_near_trigger(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_095, high=24_100, low=24_095)))

        self.assertEqual(signal.signal_status, SignalStatus.PREPARE)
        self.assertEqual(signal.signal_direction, Direction.BULLISH)
        self.assertEqual(signal.setup_name, SetupName.OPENING_RANGE_BREAKOUT)
        self.assertIsNotNone(signal.entry_trigger)
        self.assertIn("developing close", signal.reason)

    def test_vwap_reclaim_prepare_near_trigger(self) -> None:
        signal = SignalEvaluator().evaluate(
            context(
                candle(10, 0, 23_992, high=23_996, low=23_985),
                previous=candle(9, 55, 23_980, high=23_995, low=23_970),
            )
        )

        self.assertEqual(signal.signal_status, SignalStatus.PREPARE)
        self.assertEqual(signal.setup_name, SetupName.VWAP_RECLAIM)
        self.assertEqual(signal.suggested_option_side, "CALL")

    def test_duplicate_alert_is_suppressed_for_same_session_key(self) -> None:
        evaluator = SignalEvaluator()
        market_context = context(candle(9, 35, 24_125, high=24_130, low=24_100))

        first = evaluator.evaluate(market_context)
        second = evaluator.evaluate(market_context)

        self.assertEqual(first.signal_status, SignalStatus.READY)
        self.assertEqual(second.signal_status, SignalStatus.WAIT)
        self.assertIn("Duplicate alert", second.reason)

    def test_bearish_vwap_rejection_suggests_put(self) -> None:
        signal = SignalEvaluator().evaluate(
            context(
                candle(10, 5, 23_970, high=23_995, low=23_970),
                previous=candle(10, 0, 24_010, high=24_030, low=23_990),
                snapshot=indicators(
                    ema_9=23_940,
                    ema_21=23_980,
                    ema_50=24_020,
                    trend_15m=Direction.BEARISH,
                ),
            )
        )

        self.assertEqual(signal.signal_status, SignalStatus.READY)
        self.assertEqual(signal.signal_direction, Direction.BEARISH)
        self.assertEqual(signal.setup_name, SetupName.VWAP_REJECTION)
        self.assertEqual(signal.suggested_option_side, "PUT")

    def test_unsupported_instrument_is_no_trade(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125), instrument="FIN NIFTY"))

        self.assertEqual(signal.signal_status, SignalStatus.NO_TRADE)
        self.assertIn("Unsupported instrument", signal.reason)

    def test_telegram_alert_formatter_is_preview_only(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))

        preview = format_telegram_alert(signal)

        self.assertIn("IndexPulse F&O Console — SAMPLE DATA", preview)
        self.assertIn("SAMPLE DATA", preview)
        self.assertIn("Preview only", preview)
        self.assertIn("No order placed", preview)
        self.assertIn("Opening Range Breakout", preview)

    def test_live_actionable_telegram_preview_uses_live_data_header(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))
        actionable, reason = telegram_preview_context(signal, DataMode.LIVE, True)

        preview = format_telegram_alert(signal, data_mode=DataMode.LIVE, actionable=actionable, block_reason=reason)

        self.assertIn("IndexPulse F&O Console — LIVE DATA", preview)
        self.assertNotIn("LIVE PREVIEW", preview)
        self.assertNotIn("Reference only", preview)

    def test_live_delayed_feed_ready_row_uses_feed_block_reason(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))
        actionable, reason = telegram_preview_context(signal, DataMode.LIVE, False, "Blocked: live candle feed is delayed")

        preview = format_telegram_alert(
            signal,
            data_mode=DataMode.LIVE,
            actionable=actionable,
            block_reason=reason,
        )

        self.assertIn("IndexPulse F&O Console — LIVE PREVIEW", preview)
        self.assertIn("Reference only — not actionable", preview)
        self.assertIn("Blocked: live candle feed is delayed", preview)

    def test_live_fresh_feed_avoid_row_uses_live_preview_and_status_reason(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))
        avoid_signal = replace(signal, signal_status=SignalStatus.AVOID, reason="Current candle movement is unstable.")
        actionable, reason = telegram_preview_context(avoid_signal, DataMode.LIVE, True)

        preview = format_telegram_alert(avoid_signal, data_mode=DataMode.LIVE, actionable=actionable, block_reason=reason)

        self.assertIn("LIVE PREVIEW", preview)
        self.assertIn("Reference only", preview)
        self.assertIn("Blocked: signal status is AVOID", preview)

    def test_live_fresh_feed_wait_row_uses_live_preview_and_status_reason(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))
        wait_signal = replace(signal, signal_status=SignalStatus.WAIT, reason="Waiting for confirmation.")
        actionable, reason = telegram_preview_context(wait_signal, DataMode.LIVE, True)

        preview = format_telegram_alert(wait_signal, data_mode=DataMode.LIVE, actionable=actionable, block_reason=reason)

        self.assertIn("LIVE PREVIEW", preview)
        self.assertIn("Reference only", preview)
        self.assertIn("Blocked: signal status is WAIT", preview)

    def test_telegram_preview_formatting_does_not_dispatch(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))

        preview = format_telegram_alert(signal, data_mode=DataMode.LIVE, actionable=True)

        self.assertIn("No Telegram message sent", preview)
        self.assertFalse(hasattr(alerts_module, "send_telegram_alert"))

    def test_alert_history_handles_missing_empty_and_append(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "history.json"
            self.assertEqual(load_alert_history(path), [])

            path.write_text("", encoding="utf-8")
            self.assertEqual(load_alert_history(path), [])

            added, records = append_alert(signal, path)
            self.assertTrue(added)
            self.assertEqual(len(records), 1)
            self.assertEqual(load_alert_history(path)[0]["instrument"], "NIFTY 50")

            save_alert_history([], path)
            self.assertEqual(load_alert_history(path), [])

    def test_sample_scenarios_cover_phase_one_statuses(self) -> None:
        statuses = {signal.signal_status for _, signal in evaluate_sample_scenarios()}
        vwap_positions = {
            signal.setup_name.value
            for _, signal in evaluate_sample_scenarios()
            if signal.setup_name in {SetupName.VWAP_RECLAIM, SetupName.VWAP_REJECTION}
        }

        self.assertTrue({SignalStatus.READY, SignalStatus.PREPARE, SignalStatus.WAIT, SignalStatus.AVOID} <= statuses)
        self.assertEqual(vwap_positions, {"VWAP Reclaim", "VWAP Rejection"})

    def test_duplicate_active_trade_prevention(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))
        active_trades = {}

        first_added, first_message = add_active_trade(active_trades, signal)
        second_added, second_message = add_active_trade(active_trades, signal)

        self.assertTrue(first_added)
        self.assertIn("Added", first_message)
        self.assertFalse(second_added)
        self.assertIn("already exists", second_message)
        self.assertEqual(len(active_trades), 1)

    def test_ready_only_active_trade_rule(self) -> None:
        prepare_signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_095, high=24_100, low=24_095)))

        with self.assertRaises(ValueError):
            create_active_trade(prepare_signal)

    def test_prepare_watchlist_rule(self) -> None:
        ready_signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))
        prepare_signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_095, high=24_100, low=24_095)))
        watchlist = {}

        with self.assertRaises(ValueError):
            create_watchlist_entry(ready_signal)

        first_added, _ = add_watchlist_entry(watchlist, prepare_signal)
        second_added, message = add_watchlist_entry(watchlist, prepare_signal)

        self.assertTrue(first_added)
        self.assertFalse(second_added)
        self.assertIn("already exists", message)
        self.assertEqual(len(watchlist), 1)

    def test_corrupt_history_recovery_creates_backup(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "history.json"
            path.write_text("{not valid json", encoding="utf-8")

            records = load_alert_history(path)
            backups = list(Path(temp_dir).glob("history.json.corrupt-*.bak"))

            self.assertEqual(records, [])
            self.assertEqual(load_alert_history(path), [])
            self.assertEqual(len(backups), 1)

    def test_duplicate_history_prevention(self) -> None:
        signal = SignalEvaluator().evaluate(context(candle(9, 35, 24_125, high=24_130, low=24_100)))

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "history.json"
            first_added, first_records = append_alert(signal, path)
            second_added, second_records = append_alert(signal, path)

            self.assertTrue(first_added)
            self.assertFalse(second_added)
            self.assertEqual(len(first_records), 1)
            self.assertEqual(len(second_records), 1)


if __name__ == "__main__":
    unittest.main()
