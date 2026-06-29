from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from momentum_edge.candle_utils import IST
from momentum_edge.outcomes import (
    ExecutionRecord,
    ExecutionState,
    OutcomeConfig,
    empty_outcome_state,
    ensure_execution_record,
    load_outcome_state,
    performance_summary,
    process_outcomes_for_candles,
    realised_r,
    save_outcome_state,
    time_bucket,
    update_execution_with_candle,
)
from momentum_edge.performance import filter_outcome_records, performance_dashboard_summary
from momentum_edge.rules import Candle, Direction, Signal, SignalStatus, SetupName
from momentum_edge.scanner_state import DataMode


def ready(ts: datetime, direction: Direction = Direction.BULLISH) -> Signal:
    return Signal(
        instrument="NIFTY 50",
        signal_direction=direction,
        setup_name=SetupName.OPENING_RANGE_BREAKOUT if direction == Direction.BULLISH else SetupName.OPENING_RANGE_BREAKDOWN,
        spot_price=100,
        vwap_value=99,
        entry_trigger=105 if direction == Direction.BULLISH else 95,
        stop_loss=100 if direction == Direction.BULLISH else 100,
        target_1=110 if direction == Direction.BULLISH else 90,
        target_2=115 if direction == Direction.BULLISH else 85,
        risk_reward_ratio=1.0,
        confidence_level="HIGH",
        signal_status=SignalStatus.READY,
        invalidation_condition="stop",
        suggested_option_side="CALL" if direction == Direction.BULLISH else "PUT",
        alert_timestamp=ts,
        reason="ready",
    )


def candle(ts: datetime, high: float, low: float, close: float) -> Candle:
    return Candle(ts, close, high, low, close, 1000)


class OutcomeTest(unittest.TestCase):
    def test_ready_creates_pending_entry_once(self) -> None:
        state = empty_outcome_state()
        sig = ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST))

        first, created = ensure_execution_record(state, sig, DataMode.LIVE)
        second, created_again = ensure_execution_record(state, sig, DataMode.LIVE)

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.execution_state, ExecutionState.PENDING_ENTRY.value)
        self.assertEqual(second.execution_key, first.execution_key)
        self.assertEqual(len(state.records), 1)

    def test_bullish_entry_trigger(self) -> None:
        state = empty_outcome_state()
        record, _ = ensure_execution_record(state, ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)

        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 106, 102, 105), state)

        self.assertEqual(record.execution_state, ExecutionState.ENTRY_TRIGGERED.value)
        self.assertEqual(record.actual_entry, 105)

    def test_bearish_entry_trigger(self) -> None:
        state = empty_outcome_state()
        record, _ = ensure_execution_record(state, ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST), Direction.BEARISH), DataMode.LIVE)

        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 98, 94, 95), state)

        self.assertEqual(record.execution_state, ExecutionState.ENTRY_TRIGGERED.value)
        self.assertEqual(record.actual_entry, 95)

    def test_no_entry_when_price_does_not_reach_trigger(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)

        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 104, 101, 103))

        self.assertEqual(record.execution_state, ExecutionState.PENDING_ENTRY.value)

    def test_invalidation_before_entry(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)

        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 104, 99, 100))

        self.assertEqual(record.execution_state, ExecutionState.INVALIDATED_BEFORE_ENTRY.value)

    def test_expiry_before_entry(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)

        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 55, tzinfo=IST), 104, 101, 103), config=OutcomeConfig(validity_candles=3))

        self.assertEqual(record.execution_state, ExecutionState.EXPIRED_BEFORE_ENTRY.value)

    def test_new_entry_cutoff_handling(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 14, 40, tzinfo=IST)), DataMode.LIVE)

        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 14, 50, tzinfo=IST), 106, 101, 105))

        self.assertEqual(record.execution_state, ExecutionState.EXPIRED_BEFORE_ENTRY.value)

    def test_target_1_hit(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 106, 102, 105))
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 45, tzinfo=IST), 111, 104, 110))

        self.assertEqual(record.execution_state, ExecutionState.TARGET_1_HIT.value)

    def test_target_2_hit(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 106, 102, 105))
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 45, tzinfo=IST), 116, 104, 115))

        self.assertEqual(record.execution_state, ExecutionState.TARGET_2_HIT.value)
        self.assertEqual(record.realised_r, 2.0)

    def test_stop_loss_hit(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 106, 102, 105))
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 45, tzinfo=IST), 107, 99, 100))

        self.assertEqual(record.execution_state, ExecutionState.STOP_LOSS_HIT.value)
        self.assertEqual(record.realised_r, -1.0)

    def test_same_candle_target_stop_ambiguity(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 106, 102, 105))
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 45, tzinfo=IST), 111, 99, 105))

        self.assertEqual(record.execution_state, ExecutionState.AMBIGUOUS.value)
        self.assertIsNotNone(record.ambiguity_reason)

    def test_mfe_mae_for_bullish(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 108, 103, 106))

        self.assertEqual(record.mfe_points, 3)
        self.assertEqual(record.mae_points, 2)

    def test_mfe_mae_for_bearish(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST), Direction.BEARISH), DataMode.LIVE)
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 97, 92, 94))

        self.assertEqual(record.mfe_points, 3)
        self.assertEqual(record.mae_points, 2)

    def test_realised_r_calculation(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)
        record.actual_entry = 105

        self.assertEqual(realised_r(record, 115), 2.0)

    def test_duplicate_candle_prevention(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)
        bar = candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 106, 102, 105)

        first = update_execution_with_candle(record, bar)
        second = update_execution_with_candle(record, bar)

        self.assertTrue(first)
        self.assertFalse(second)

    def test_persistence_across_restart(self) -> None:
        state = empty_outcome_state()
        ensure_execution_record(state, ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "outcomes.json"
            save_outcome_state(state, path)
            loaded = load_outcome_state(path)

        self.assertEqual(len(loaded.records), 1)

    def test_sample_live_separation(self) -> None:
        live = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)[0]
        sample = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.SAMPLE)[0]

        self.assertNotEqual(live.execution_key, sample.execution_key)

    def test_stale_cached_data_blocks_transitions(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)

        changed = update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 106, 102, 105), data_safe=False)

        self.assertFalse(changed)
        self.assertEqual(record.execution_state, ExecutionState.PENDING_ENTRY.value)

    def test_performance_aggregation(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 40, tzinfo=IST), 106, 102, 105))
        update_execution_with_candle(record, candle(datetime(2026, 6, 29, 9, 45, tzinfo=IST), 116, 104, 115))

        summary = performance_summary([record.__dict__])

        self.assertEqual(summary["total_ready_signals"], 1)
        self.assertEqual(summary["entry_trigger_rate"], 1)
        self.assertEqual(summary["target_2_hit_rate"], 1)

    def test_time_bucket_classification(self) -> None:
        self.assertEqual(time_bucket(datetime(2026, 6, 29, 9, 45, tzinfo=IST)), "09:25-10:30")
        self.assertEqual(time_bucket(datetime(2026, 6, 29, 12, 30, tzinfo=IST)), "12:00-13:30")

    def test_filter_outcome_records(self) -> None:
        record, _ = ensure_execution_record(empty_outcome_state(), ready(datetime(2026, 6, 29, 9, 35, tzinfo=IST)), DataMode.LIVE)

        filtered = filter_outcome_records([record.__dict__], instruments={"NIFTY 50"}, data_modes={"LIVE"})

        self.assertEqual(len(filtered), 1)


if __name__ == "__main__":
    unittest.main()
