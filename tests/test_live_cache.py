from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from momentum_edge.candle_utils import IST
from momentum_edge.live_cache import cached_snapshot_for_display, load_live_snapshot, save_live_snapshot, snapshot_payload
from momentum_edge.live_snapshot import DerivedMetrics, LiveInstrumentSnapshot, LiveSnapshot
from momentum_edge.rules import Candle, Direction, IndicatorSnapshot, MarketContext, Signal, SignalStatus, SetupName
from momentum_edge.scanner_state import DataMode, ScannerDiagnostics, ScannerState


def candle(timestamp: datetime, close: float = 100.0) -> Candle:
    return Candle(timestamp, close - 1, close + 1, close - 2, close, 1000)


def signal(timestamp: datetime, status: SignalStatus = SignalStatus.READY) -> Signal:
    return Signal(
        instrument="NIFTY 50",
        signal_direction=Direction.BULLISH,
        setup_name=SetupName.OPENING_RANGE_BREAKOUT,
        spot_price=100,
        vwap_value=98,
        entry_trigger=101,
        stop_loss=97,
        target_1=105,
        target_2=110,
        risk_reward_ratio=2.0,
        confidence_level="HIGH",
        signal_status=status,
        invalidation_condition="Below stop",
        suggested_option_side="CALL",
        alert_timestamp=timestamp,
        reason="Cached signal",
    )


def snapshot() -> LiveSnapshot:
    ts = datetime(2026, 6, 30, 15, 25, tzinfo=IST)
    context = MarketContext(
        instrument="NIFTY 50",
        candle=candle(ts),
        previous_candles=(candle(datetime(2026, 6, 30, 15, 20, tzinfo=IST), 99),),
        indicators=IndicatorSnapshot(
            vwap=98,
            ema_9=99,
            ema_21=98,
            ema_50=97,
            opening_range_high=102,
            opening_range_low=95,
            previous_day_high=104,
            previous_day_low=94,
            atr=20,
            relative_volume=1.5,
            india_vix=14,
            trend_15m=Direction.BULLISH,
        ),
    )
    diagnostics = ScannerDiagnostics(
        data_mode=DataMode.LIVE,
        scanner_state=ScannerState.LIVE_READY,
        last_successful_fetch=ts,
        last_completed_5m_candle=ts,
        last_completed_15m_candle=datetime(2026, 6, 30, 15, 15, tzinfo=IST),
        data_freshness=None,
        data_age_seconds=0,
        vwap_source="NIFTY26JUNFUT",
        last_evaluation=ts,
        next_expected_evaluation=None,
        session_state="POST_MARKET",
        display_freshness="MARKET CLOSED",
        signals_actionable=False,
    )
    item = LiveInstrumentSnapshot(
        instrument="NIFTY 50",
        context=context,
        signal=signal(ts),
        metrics=DerivedMetrics(1, 1, 1, 2, 1, futures_price=101, spot_price=100, futures_close=101, futures_vwap=99, futures_volume=1000),
        last_completed_5m=ts,
        last_completed_15m=datetime(2026, 6, 30, 15, 15, tzinfo=IST),
        vwap_source="NIFTY26JUNFUT",
        futures_symbol="NIFTY26JUNFUT",
    )
    return LiveSnapshot((item,), diagnostics, ts)


class LiveCacheTest(unittest.TestCase):
    def test_valid_live_snapshot_cache_roundtrip(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cache.json"
            save_live_snapshot(snapshot(), path)

            loaded = load_live_snapshot(path)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.diagnostics.data_mode, DataMode.LIVE)
            self.assertEqual(loaded.instruments[0].signal.instrument, "NIFTY 50")

    def test_sample_snapshot_payload_is_rejected_for_live_cache(self) -> None:
        with TemporaryDirectory() as temp_dir:
            payload = snapshot_payload(snapshot())
            payload["mode"] = DataMode.SAMPLE.value
            path = Path(temp_dir) / "cache.json"
            path.write_text(__import__("json").dumps(payload), encoding="utf-8")

            self.assertIsNone(load_live_snapshot(path))

    def test_malformed_cache_is_rejected_safely(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cache.json"
            path.write_text("{bad json", encoding="utf-8")

            self.assertIsNone(load_live_snapshot(path))

    def test_post_market_cached_snapshot_is_non_actionable(self) -> None:
        cached = cached_snapshot_for_display(snapshot(), datetime(2026, 6, 30, 16, 0, tzinfo=IST), "credentials missing")

        self.assertEqual(cached.diagnostics.scanner_state, ScannerState.CACHED_SESSION)
        self.assertEqual(cached.diagnostics.display_freshness, "MARKET CLOSED")
        self.assertFalse(cached.diagnostics.signals_actionable)
        self.assertFalse(cached.diagnostics.new_alerts_allowed)
        self.assertTrue(cached.instruments[0].is_cached)


if __name__ == "__main__":
    unittest.main()
