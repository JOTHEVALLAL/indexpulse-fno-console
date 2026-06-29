from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo
import unittest

from momentum_edge.candle_utils import IST, completed_candles, ensure_ist
from momentum_edge.diagnostics import append_diagnostic_record, diagnostic_key, load_diagnostic_records
from momentum_edge.indicators import atr, ema, opening_range, previous_day_levels, relative_volume, session_vwap
from momentum_edge.instruments import resolve_nearest_monthly_future
from momentum_edge.kite_client import KiteAuthenticationError, KiteCredentials, KiteDataError
from momentum_edge.live_scanner import block_ready_when_stale, scan_live
from momentum_edge.market_data import RawFuturesData, RawInstrumentData, build_market_context, build_market_context_with_futures
from momentum_edge.rules import Candle, Direction, IndicatorSnapshot, MarketContext, Signal, SignalStatus, SetupName
from momentum_edge.sample_data import evaluate_sample_scenarios
from momentum_edge.scanner_state import DataMode, FreshnessState, ScannerCache, ScannerState


def c(ts: datetime, close: float, volume: float = 1000, high: float | None = None, low: float | None = None) -> Candle:
    return Candle(
        timestamp=ts,
        open=close - 2,
        high=high if high is not None else close + 5,
        low=low if low is not None else close - 5,
        close=close,
        volume=volume,
    )


def five_minute_series(now: datetime) -> list[Candle]:
    start = datetime(2026, 6, 29, 9, 15, tzinfo=IST)
    candles = []
    for index in range(10):
        ts = start + timedelta(minutes=5 * index)
        close = 24_000 + index * 18
        high = close + 10
        low = close - 8
        if index < 3:
            high = 24_100
            low = 23_960
        candles.append(c(ts, close, volume=1000 + index * 20, high=high, low=low))
    candles[-1] = c(candles[-1].timestamp, 24_180, volume=1600, high=24_190, low=24_155)
    candles.append(c(now.replace(second=0, microsecond=0), 24_250, volume=2000))
    return candles


def fifteen_minute_series() -> list[Candle]:
    start = datetime(2026, 6, 29, 9, 15, tzinfo=IST)
    return [c(start + timedelta(minutes=15 * index), 24_000 + index * 35, 3000) for index in range(4)]


def previous_day() -> list[Candle]:
    return [c(datetime(2026, 6, 28, 15, 30, tzinfo=IST), 23_900, 10_000, high=24_050, low=23_700)]


class FakeKiteClient:
    def __init__(self, fail: Exception | None = None, empty: bool = False, misaligned: bool = False, zero_futures_volume: bool = False) -> None:
        self.fail = fail
        self.empty = empty
        self.misaligned = misaligned
        self.zero_futures_volume = zero_futures_volume

    def instruments(self, exchange: str = "NSE") -> list[dict]:
        if self.fail:
            raise self.fail
        if exchange == "NFO":
            return futures_catalog()
        return spot_catalog()

    def quote(self, exchange: str, tradingsymbol: str) -> dict:
        if tradingsymbol == "INDIA VIX":
            return {"last_price": 14.0}
        if exchange == "NFO":
            return {"last_price": 24_210.0}
        return {"last_price": 24_180.0}

    def historical_candles(self, instrument_token: int, from_date: datetime, to_date: datetime, interval: str) -> list[Candle]:
        if self.empty:
            raise KiteDataError("No candles returned.")
        if interval == "5minute":
            candles = five_minute_series(to_date)
            if instrument_token in {11, 22}:
                volume = 0 if self.zero_futures_volume else 2500
                candles = [Candle(item.timestamp, item.open + 30, item.high + 30, item.low + 30, item.close + 30, volume) for item in candles]
                if self.misaligned:
                    candles = candles[:-2]
            return candles
        if interval == "15minute":
            return fifteen_minute_series()
        return previous_day()


def spot_catalog() -> list[dict]:
    return [
        {"exchange": "NSE", "tradingsymbol": "NIFTY 50", "instrument_token": 1},
        {"exchange": "NSE", "tradingsymbol": "NIFTY BANK", "instrument_token": 2},
        {"exchange": "NSE", "tradingsymbol": "INDIA VIX", "instrument_token": 3},
    ]


def futures_catalog() -> list[dict]:
    return [
        {
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26JUNFUT",
            "instrument_token": 11,
            "instrument_type": "FUT",
            "name": "NIFTY",
            "expiry": "2026-06-30",
        },
        {
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26JULFUT",
            "instrument_token": 12,
            "instrument_type": "FUT",
            "name": "NIFTY",
            "expiry": "2026-07-30",
        },
        {
            "exchange": "NFO",
            "tradingsymbol": "BANKNIFTY26JUNFUT",
            "instrument_token": 22,
            "instrument_type": "FUT",
            "name": "BANKNIFTY",
            "expiry": "2026-06-30",
        },
    ]


class LiveDataTest(unittest.TestCase):
    def test_sample_live_mode_separation(self) -> None:
        sample_signals = [signal for _, signal in evaluate_sample_scenarios()]

        self.assertTrue(sample_signals)
        self.assertEqual(DataMode.SAMPLE.value, "SAMPLE")
        self.assertEqual(DataMode.LIVE.value, "LIVE")

    def test_completed_5_minute_candle_filtering_and_duplicate_handling(self) -> None:
        now = datetime(2026, 6, 29, 10, 2, tzinfo=IST)
        candles = [
            c(datetime(2026, 6, 29, 9, 55, tzinfo=IST), 100),
            c(datetime(2026, 6, 29, 9, 55, tzinfo=IST), 101),
            c(datetime(2026, 6, 29, 10, 0, tzinfo=IST), 105),
        ]

        filtered = completed_candles(candles, now, 5)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].close, 101)

    def test_timezone_aware_timestamps(self) -> None:
        naive = datetime(2026, 6, 29, 9, 15)
        aware = ensure_ist(naive)

        self.assertIsNotNone(aware.tzinfo)
        self.assertEqual(aware.tzinfo, ZoneInfo("Asia/Kolkata"))

    def test_session_vwap_with_valid_volume(self) -> None:
        candles = [
            Candle(datetime(2026, 6, 29, 9, 15, tzinfo=IST), 10, 12, 8, 10, 100),
            Candle(datetime(2026, 6, 29, 9, 20, tzinfo=IST), 20, 22, 18, 20, 300),
        ]

        self.assertAlmostEqual(session_vwap(candles), 17.5)

    def test_vwap_rejected_when_volume_unavailable(self) -> None:
        with self.assertRaises(ValueError):
            session_vwap([c(datetime(2026, 6, 29, 9, 15, tzinfo=IST), 100, volume=0)])

    def test_ema_calculation(self) -> None:
        self.assertAlmostEqual(ema([10, 20, 30], 3), 22.5)

    def test_atr_calculation(self) -> None:
        candles = [
            Candle(datetime(2026, 6, 29, 9, 15, tzinfo=IST), 10, 15, 8, 12, 100),
            Candle(datetime(2026, 6, 29, 9, 20, tzinfo=IST), 12, 18, 11, 17, 100),
        ]

        self.assertAlmostEqual(atr(candles, 14), 7.0)

    def test_opening_range_calculation(self) -> None:
        high, low = opening_range(
            [
                c(datetime(2026, 6, 29, 9, 15, tzinfo=IST), 100, high=110, low=90),
                c(datetime(2026, 6, 29, 9, 25, tzinfo=IST), 105, high=112, low=95),
                c(datetime(2026, 6, 29, 9, 30, tzinfo=IST), 120, high=130, low=100),
            ]
        )

        self.assertEqual(high, 112)
        self.assertEqual(low, 90)

    def test_previous_day_levels(self) -> None:
        high, low = previous_day_levels(
            [
                c(datetime(2026, 6, 28, 9, 15, tzinfo=IST), 100, high=110, low=90),
                c(datetime(2026, 6, 28, 15, 25, tzinfo=IST), 105, high=120, low=95),
            ]
        )

        self.assertEqual(high, 120)
        self.assertEqual(low, 90)

    def test_relative_volume_calculation(self) -> None:
        current = c(datetime(2026, 6, 29, 10, 0, tzinfo=IST), 100, volume=200)
        prior = [c(datetime(2026, 6, 29, 9, 15, tzinfo=IST), 100, volume=100)]

        self.assertEqual(relative_volume(current, prior), 2.0)

    def test_stale_data_blocks_ready(self) -> None:
        signal = Signal(
            instrument="NIFTY 50",
            signal_direction=Direction.BULLISH,
            setup_name=SetupName.OPENING_RANGE_BREAKOUT,
            spot_price=100,
            vwap_value=90,
            entry_trigger=101,
            stop_loss=95,
            target_1=110,
            target_2=120,
            risk_reward_ratio=1.5,
            confidence_level="HIGH",
            signal_status=SignalStatus.READY,
            invalidation_condition="Stop",
            suggested_option_side="CALL",
            alert_timestamp=datetime(2026, 6, 29, 10, 0, tzinfo=IST),
            reason="Ready",
        )

        blocked = block_ready_when_stale(signal, FreshnessState.STALE)

        self.assertEqual(blocked.signal_status, SignalStatus.WAIT)
        self.assertIn("STALE", blocked.reason)

    def test_invalid_token_handling(self) -> None:
        snapshot = scan_live(FakeKiteClient(fail=KiteAuthenticationError("expired token")), datetime(2026, 6, 29, 10, 5, tzinfo=IST))

        self.assertEqual(snapshot.diagnostics.scanner_state, ScannerState.LIVE_DATA_UNAVAILABLE)
        self.assertIn("expired token", snapshot.diagnostics.current_error or "")

    def test_empty_candle_handling(self) -> None:
        snapshot = scan_live(FakeKiteClient(empty=True), datetime(2026, 6, 29, 10, 5, tzinfo=IST))

        self.assertEqual(snapshot.diagnostics.scanner_state, ScannerState.LIVE_DATA_UNAVAILABLE)
        self.assertFalse(snapshot.instruments)

    def test_live_failure_never_returns_sample_signals_without_cache(self) -> None:
        snapshot = scan_live(FakeKiteClient(fail=KiteDataError("network down")), datetime(2026, 6, 29, 10, 5, tzinfo=IST))

        self.assertEqual(snapshot.diagnostics.scanner_state, ScannerState.LIVE_DATA_UNAVAILABLE)
        self.assertEqual(snapshot.instruments, tuple())

    def test_missing_token_live_mode_returns_no_signals(self) -> None:
        old_api_key = os.environ.pop("KITE_API_KEY", None)
        old_access_token = os.environ.pop("KITE_ACCESS_TOKEN", None)
        try:
            with self.assertRaises(KiteAuthenticationError):
                KiteCredentials.from_environment()
        finally:
            if old_api_key is not None:
                os.environ["KITE_API_KEY"] = old_api_key
            if old_access_token is not None:
                os.environ["KITE_ACCESS_TOKEN"] = old_access_token

    def test_last_valid_snapshot_preserved_on_failure(self) -> None:
        cache = ScannerCache()
        now = datetime(2026, 6, 29, 10, 5, tzinfo=IST)
        first = scan_live(FakeKiteClient(), now, cache)
        second = scan_live(FakeKiteClient(fail=KiteDataError("temporary failure")), now + timedelta(seconds=31), cache)

        self.assertTrue(first.instruments)
        self.assertTrue(second.instruments)
        self.assertEqual(second.diagnostics.scanner_state, ScannerState.LIVE_CACHED)
        self.assertTrue(all(item.is_cached for item in second.instruments))

    def test_stale_cached_snapshot_blocks_ready(self) -> None:
        cache = ScannerCache()
        now = datetime(2026, 6, 29, 10, 5, tzinfo=IST)
        scan_live(FakeKiteClient(), now, cache)
        cached = scan_live(FakeKiteClient(fail=KiteDataError("temporary failure")), now + timedelta(minutes=10), cache)

        self.assertEqual(cached.diagnostics.scanner_state, ScannerState.LIVE_CACHED)
        self.assertTrue(all(item.signal.signal_status != SignalStatus.READY for item in cached.instruments))

    def test_build_market_context_has_timezone_and_indicators(self) -> None:
        now = datetime(2026, 6, 29, 10, 5, tzinfo=IST)
        built = build_market_context(
            RawInstrumentData("NIFTY 50", 24_180, five_minute_series(now), fifteen_minute_series(), previous_day()),
            india_vix=14.0,
            now=now,
        )

        self.assertIsNotNone(built.context.candle.timestamp.tzinfo)
        self.assertGreater(built.context.indicators.vwap, 0)
        self.assertEqual(built.vwap_source, "UNDERLYING_VOLUME")

    def test_futures_contract_resolution(self) -> None:
        result = resolve_nearest_monthly_future("NIFTY 50", futures_catalog(), datetime(2026, 6, 29, tzinfo=IST).date())

        self.assertTrue(result.ok)
        self.assertEqual(result.instrument.tradingsymbol, "NIFTY26JUNFUT")

    def test_expired_futures_exclusion(self) -> None:
        result = resolve_nearest_monthly_future("NIFTY 50", futures_catalog(), datetime(2026, 7, 1, tzinfo=IST).date())

        self.assertTrue(result.ok)
        self.assertEqual(result.instrument.tradingsymbol, "NIFTY26JULFUT")

    def test_wrong_exchange_or_instrument_type_rejection(self) -> None:
        bad = [
            {"exchange": "NSE", "tradingsymbol": "NIFTY26JUNFUT", "instrument_token": 1, "instrument_type": "FUT", "name": "NIFTY", "expiry": "2026-06-30"},
            {"exchange": "NFO", "tradingsymbol": "NIFTY26JUNCE", "instrument_token": 2, "instrument_type": "CE", "name": "NIFTY", "expiry": "2026-06-30"},
        ]

        result = resolve_nearest_monthly_future("NIFTY 50", bad, datetime(2026, 6, 29, tzinfo=IST).date())

        self.assertFalse(result.ok)

    def test_spot_futures_timestamp_alignment(self) -> None:
        now = datetime(2026, 6, 29, 10, 5, tzinfo=IST)
        built = build_market_context_with_futures(
            RawInstrumentData("NIFTY 50", 24_180, five_minute_series(now), fifteen_minute_series(), previous_day()),
            RawFuturesData("NIFTY26JUNFUT", 11, datetime(2026, 6, 30).date(), 24_210, FakeKiteClient().historical_candles(11, now, now, "5minute")),
            14.0,
            now,
        )

        self.assertEqual(built.alignment_status, "ALIGNED")
        self.assertEqual(built.alignment_difference_seconds, 0)

    def test_misalignment_blocks_vwap_setups(self) -> None:
        snapshot = scan_live(FakeKiteClient(misaligned=True), datetime(2026, 6, 29, 10, 5, tzinfo=IST))

        self.assertTrue(snapshot.instruments)
        self.assertTrue(any("MISALIGNED" in item.signal.reason for item in snapshot.instruments))
        self.assertTrue(all(item.signal.signal_status != SignalStatus.READY for item in snapshot.instruments))

    def test_zero_futures_volume_rejection(self) -> None:
        snapshot = scan_live(FakeKiteClient(zero_futures_volume=True), datetime(2026, 6, 29, 10, 5, tzinfo=IST))

        self.assertFalse(snapshot.instruments)
        self.assertEqual(snapshot.diagnostics.scanner_state, ScannerState.LIVE_DATA_UNAVAILABLE)

    def test_futures_vwap_calculation(self) -> None:
        now = datetime(2026, 6, 29, 10, 5, tzinfo=IST)
        built = build_market_context_with_futures(
            RawInstrumentData("NIFTY 50", 24_180, five_minute_series(now), fifteen_minute_series(), previous_day()),
            RawFuturesData("NIFTY26JUNFUT", 11, datetime(2026, 6, 30).date(), 24_210, FakeKiteClient().historical_candles(11, now, now, "5minute")),
            14.0,
            now,
        )

        self.assertIsNotNone(built.futures_vwap)
        self.assertEqual(built.vwap_source, "NIFTY26JUNFUT")

    def test_relative_volume_calculation_from_futures(self) -> None:
        now = datetime(2026, 6, 29, 10, 5, tzinfo=IST)
        built = build_market_context_with_futures(
            RawInstrumentData("NIFTY 50", 24_180, five_minute_series(now), fifteen_minute_series(), previous_day()),
            RawFuturesData("NIFTY26JUNFUT", 11, datetime(2026, 6, 30).date(), 24_210, FakeKiteClient().historical_candles(11, now, now, "5minute")),
            14.0,
            now,
        )

        self.assertGreater(built.context.indicators.relative_volume, 0)

    def test_futures_spot_basis_calculation(self) -> None:
        now = datetime(2026, 6, 29, 10, 5, tzinfo=IST)
        built = build_market_context_with_futures(
            RawInstrumentData("NIFTY 50", 24_180, five_minute_series(now), fifteen_minute_series(), previous_day()),
            RawFuturesData("NIFTY26JUNFUT", 11, datetime(2026, 6, 30).date(), 24_210, FakeKiteClient().historical_candles(11, now, now, "5minute")),
            14.0,
            now,
        )

        self.assertEqual(built.futures_spot_basis, 30)

    def test_diagnostic_persistence_and_duplicate_prevention(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "diag.csv"
            record = {
                "diagnostic_key": diagnostic_key("NIFTY 50", datetime(2026, 6, 29, 10, 0, tzinfo=IST)),
                "evaluation_timestamp": datetime(2026, 6, 29, 10, 5, tzinfo=IST).isoformat(),
                "instrument": "NIFTY 50",
            }
            first, records = append_diagnostic_record(record, path)
            second, records_again = append_diagnostic_record(record, path)

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(len(records), 1)
            self.assertEqual(len(records_again), 1)
            self.assertEqual(len(load_diagnostic_records(path)), 1)


if __name__ == "__main__":
    unittest.main()
