from __future__ import annotations

from datetime import datetime
import unittest

from momentum_edge.candle_utils import IST, ensure_ist
from momentum_edge.lifecycle import empty_state, process_live_snapshot
from momentum_edge.market_session import (
    MarketFreshnessState,
    MarketSessionState,
    build_session_status,
    calculate_freshness_state,
    get_market_session_state,
)
from momentum_edge.outcomes import empty_outcome_state, process_ready_signals
from momentum_edge.rules import Direction, Signal, SignalStatus, SetupName
from momentum_edge.sample_data import evaluate_sample_scenarios
from momentum_edge.scanner_state import DataMode


def ready_signal(timestamp: datetime) -> Signal:
    return Signal(
        instrument="NIFTY 50",
        signal_direction=Direction.BULLISH,
        setup_name=SetupName.OPENING_RANGE_BREAKOUT,
        spot_price=100,
        vwap_value=95,
        entry_trigger=101,
        stop_loss=98,
        target_1=105,
        target_2=110,
        risk_reward_ratio=2.0,
        confidence_level="HIGH",
        signal_status=SignalStatus.READY,
        invalidation_condition="Below stop",
        suggested_option_side="BUY CE",
        alert_timestamp=timestamp,
        reason="Ready test signal",
    )


class MarketSessionTest(unittest.TestCase):
    def test_weekday_pre_market_previous_session(self) -> None:
        status = build_session_status(
            datetime(2026, 6, 30, 8, 30, tzinfo=IST),
            datetime(2026, 6, 29, 15, 25, tzinfo=IST),
        )

        self.assertEqual(status.session_state, MarketSessionState.PRE_MARKET)
        self.assertEqual(status.freshness_state, MarketFreshnessState.PREVIOUS_SESSION)
        self.assertFalse(status.signals_actionable)
        self.assertFalse(status.new_alerts_allowed)

    def test_market_open_with_fresh_candle(self) -> None:
        status = build_session_status(
            datetime(2026, 6, 30, 10, 2, tzinfo=IST),
            datetime(2026, 6, 30, 10, 0, tzinfo=IST),
        )

        self.assertEqual(status.session_state, MarketSessionState.MARKET_OPEN)
        self.assertEqual(status.freshness_state, MarketFreshnessState.LIVE)
        self.assertTrue(status.signals_actionable)

    def test_market_open_with_delayed_candle(self) -> None:
        status = build_session_status(
            datetime(2026, 6, 30, 10, 20, tzinfo=IST),
            datetime(2026, 6, 30, 10, 10, tzinfo=IST),
        )

        self.assertEqual(status.freshness_state, MarketFreshnessState.DELAYED)
        self.assertFalse(status.signals_actionable)
        self.assertIn("delayed", status.message.lower())

    def test_market_open_with_stale_candle(self) -> None:
        status = build_session_status(
            datetime(2026, 6, 30, 12, 0, tzinfo=IST),
            datetime(2026, 6, 30, 10, 30, tzinfo=IST),
        )

        self.assertEqual(status.freshness_state, MarketFreshnessState.STALE)
        self.assertFalse(status.signals_actionable)
        self.assertFalse(status.new_alerts_allowed)

    def test_post_market(self) -> None:
        status = build_session_status(
            datetime(2026, 6, 30, 16, 0, tzinfo=IST),
            datetime(2026, 6, 30, 15, 25, tzinfo=IST),
        )

        self.assertEqual(status.session_state, MarketSessionState.POST_MARKET)
        self.assertEqual(status.freshness_state, MarketFreshnessState.MARKET_CLOSED)
        self.assertFalse(status.signals_actionable)

    def test_weekend(self) -> None:
        status = build_session_status(datetime(2026, 7, 4, 11, 0, tzinfo=IST), None)

        self.assertEqual(status.session_state, MarketSessionState.NON_TRADING_DAY)
        self.assertEqual(status.freshness_state, MarketFreshnessState.MARKET_CLOSED)
        self.assertFalse(status.signals_actionable)

    def test_missing_latest_candle_during_market_hours(self) -> None:
        status = build_session_status(datetime(2026, 6, 30, 10, 0, tzinfo=IST), None)

        self.assertEqual(status.freshness_state, MarketFreshnessState.NO_DATA)
        self.assertFalse(status.signals_actionable)
        self.assertIn("unavailable", status.message.lower())

    def test_boundary_times(self) -> None:
        self.assertEqual(get_market_session_state(datetime(2026, 6, 30, 9, 14, 59, tzinfo=IST)), MarketSessionState.PRE_MARKET)
        self.assertEqual(get_market_session_state(datetime(2026, 6, 30, 9, 15, tzinfo=IST)), MarketSessionState.MARKET_OPEN)
        self.assertEqual(get_market_session_state(datetime(2026, 6, 30, 15, 30, tzinfo=IST)), MarketSessionState.MARKET_OPEN)
        self.assertEqual(get_market_session_state(datetime(2026, 6, 30, 15, 30, 1, tzinfo=IST)), MarketSessionState.POST_MARKET)

    def test_timezone_awareness_normalizes_naive_inputs(self) -> None:
        freshness, age = calculate_freshness_state(
            datetime(2026, 6, 30, 10, 2),
            datetime(2026, 6, 30, 10, 0),
        )

        self.assertEqual(ensure_ist(datetime(2026, 6, 30, 10, 2)).tzinfo, IST)
        self.assertEqual(freshness, MarketFreshnessState.LIVE)
        self.assertEqual(age, 120)

    def test_sample_mode_signals_remain_unchanged(self) -> None:
        statuses = [signal.signal_status for _scenario, signal in evaluate_sample_scenarios()]

        self.assertIn(SignalStatus.READY, statuses)
        self.assertIn(SignalStatus.PREPARE, statuses)

    def test_existing_lifecycle_records_not_deleted_when_not_actionable(self) -> None:
        state = empty_state()
        state.records.append({"record_key": "LIVE|2026-06-30|NIFTY 50", "data_mode": "LIVE", "session_date": "2026-06-30"})

        records, events = process_live_snapshot(state, tuple(), None, signals_actionable=False)

        self.assertEqual(records, [])
        self.assertEqual(events, [])
        self.assertEqual(len(state.records), 1)

    def test_prepare_does_not_promote_to_ready_when_not_actionable(self) -> None:
        state = empty_state()

        process_live_snapshot(state, tuple(), None, signals_actionable=False)

        self.assertEqual(state.events, [])

    def test_ready_does_not_create_paper_execution_when_not_actionable(self) -> None:
        state = empty_outcome_state()

        process_ready_signals(state, [ready_signal(datetime(2026, 6, 30, 10, 0, tzinfo=IST))], DataMode.LIVE, signals_actionable=False)

        self.assertEqual(state.records, [])

    def test_no_order_placement_capability_introduced(self) -> None:
        import momentum_edge.kite_client as kite_client

        self.assertFalse(hasattr(kite_client.KiteClient, "place_order"))
        self.assertFalse(hasattr(kite_client.KiteClient, "modify_order"))
        self.assertFalse(hasattr(kite_client.KiteClient, "cancel_order"))


if __name__ == "__main__":
    unittest.main()
