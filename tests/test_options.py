from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from momentum_edge.candle_utils import IST
from momentum_edge.options import (
    Moneyness,
    OptionSelectionConfig,
    OptionSide,
    append_recommendation,
    capture_option_entry,
    capture_outcome_snapshot,
    classify_moneyness,
    derive_strike_spacing,
    discover_option_contracts,
    empty_option_state,
    evaluate_candidate,
    load_option_state,
    nearest_atm_strike,
    nearest_expiry,
    option_quote_from_payload,
    save_option_state,
    select_option_contract,
    spread_values,
    strike_candidates,
    direction_to_option_side,
)
from momentum_edge.rules import Direction, Signal, SignalStatus, SetupName
from momentum_edge.scanner_state import DataMode, FreshnessState


NOW = datetime(2026, 6, 29, 10, 0, tzinfo=IST)


def option_master() -> list[dict]:
    rows = []
    token = 100
    for underlying in ["NIFTY", "BANKNIFTY"]:
        strikes = [25500, 25600, 25700] if underlying == "NIFTY" else [57100, 57200, 57300]
        for expiry in ["2026-06-30", "2026-07-07"]:
            for strike in strikes:
                for option_type in ["CE", "PE"]:
                    token += 1
                    rows.append(
                        {
                            "exchange": "NFO",
                            "tradingsymbol": f"{underlying}26JUN{strike}{option_type}",
                            "instrument_token": token,
                            "instrument_type": option_type,
                            "name": underlying,
                            "strike": strike,
                            "expiry": expiry,
                            "lot_size": 50 if underlying == "NIFTY" else 15,
                            "tick_size": 0.05,
                        }
                    )
    rows.append(
        {
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26JUN25000CE",
            "instrument_token": 999,
            "instrument_type": "CE",
            "name": "NIFTY",
            "strike": 25000,
            "expiry": "2026-06-01",
            "lot_size": 50,
            "tick_size": 0.05,
        }
    )
    return rows


def quote(symbol: str, ltp: float = 120, bid: float = 119, ask: float = 121, volume: int = 5000, oi: int = 10000, ts: datetime = NOW) -> dict:
    return {
        "last_price": ltp,
        "exchange_timestamp": ts,
        "volume": volume,
        "oi": oi,
        "oi_day_high": oi + 100,
        "oi_day_low": oi - 100,
        "depth": {"buy": [{"price": bid, "quantity": 100}], "sell": [{"price": ask, "quantity": 100}]},
        "ohlc": {"open": 100, "high": 130, "low": 90, "close": 110},
        "last_quantity": 50,
    }


def quotes_for_contracts(contracts) -> dict:
    return {f"NFO:{contract.trading_symbol}": quote(contract.trading_symbol) for contract in contracts}


def ready(direction: Direction = Direction.BULLISH, instrument: str = "NIFTY 50", confidence: str = "HIGH") -> Signal:
    return Signal(
        instrument=instrument,
        signal_direction=direction,
        setup_name=SetupName.OPENING_RANGE_BREAKOUT,
        spot_price=25610 if instrument == "NIFTY 50" else 57225,
        vwap_value=25590,
        entry_trigger=25620,
        stop_loss=25580,
        target_1=25670,
        target_2=25720,
        risk_reward_ratio=1.5,
        confidence_level=confidence,
        signal_status=SignalStatus.READY,
        invalidation_condition="stop",
        suggested_option_side="CALL" if direction == Direction.BULLISH else "PUT",
        alert_timestamp=NOW,
        reason="ready",
    )


class OptionSelectionTest(unittest.TestCase):
    def test_bullish_to_ce_mapping(self) -> None:
        self.assertEqual(direction_to_option_side(Direction.BULLISH), OptionSide.CE)

    def test_bearish_to_pe_mapping(self) -> None:
        self.assertEqual(direction_to_option_side(Direction.BEARISH), OptionSide.PE)

    def test_nifty_banknifty_underlying_matching(self) -> None:
        nifty = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())
        bank = discover_option_contracts(option_master(), "BANKNIFTY", OptionSide.CE, NOW.date())

        self.assertTrue(all(contract.underlying == "NIFTY" for contract in nifty))
        self.assertTrue(all(contract.underlying == "BANKNIFTY" for contract in bank))

    def test_dynamic_expiry_selection_and_expired_exclusion(self) -> None:
        contracts = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())

        self.assertEqual(nearest_expiry(contracts).isoformat(), "2026-06-30")
        self.assertFalse(any(contract.expiry.isoformat() == "2026-06-01" for contract in contracts))

    def test_strike_spacing_derivation(self) -> None:
        self.assertEqual(derive_strike_spacing([25500, 25600, 25700]), 100)

    def test_atm_detection(self) -> None:
        self.assertEqual(nearest_atm_strike([25500, 25600, 25700], 25610), 25600)

    def test_ce_itm_otm_classification(self) -> None:
        self.assertEqual(classify_moneyness(OptionSide.CE, 25500, 25600), Moneyness.ITM)
        self.assertEqual(classify_moneyness(OptionSide.CE, 25700, 25600), Moneyness.OTM)

    def test_pe_itm_otm_classification(self) -> None:
        self.assertEqual(classify_moneyness(OptionSide.PE, 25700, 25600), Moneyness.ITM)
        self.assertEqual(classify_moneyness(OptionSide.PE, 25500, 25600), Moneyness.OTM)

    def test_missing_quote_rejection(self) -> None:
        contract = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())[0]

        result = evaluate_candidate(contract, Moneyness.ATM, None, NOW, 25610, "HIGH", 14)

        self.assertIn("Missing quote response.", result.rejection_reasons)

    def test_stale_quote_rejection(self) -> None:
        contract = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())[0]
        q = option_quote_from_payload(contract.trading_symbol, quote(contract.trading_symbol, ts=NOW - timedelta(minutes=10)))

        result = evaluate_candidate(contract, Moneyness.ATM, q, NOW, 25610, "HIGH", 14)

        self.assertIn("Quote is stale.", result.rejection_reasons)

    def test_invalid_bid_ask_rejection(self) -> None:
        contract = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())[0]
        q = option_quote_from_payload(contract.trading_symbol, quote(contract.trading_symbol, bid=125, ask=121))

        result = evaluate_candidate(contract, Moneyness.ATM, q, NOW, 25610, "HIGH", 14)

        self.assertIn("Invalid or crossed bid/ask market.", result.rejection_reasons)

    def test_spread_calculation(self) -> None:
        midpoint, spread, spread_pct = spread_values(100, 110)

        self.assertEqual(midpoint, 105)
        self.assertEqual(spread, 10)
        self.assertAlmostEqual(spread_pct, 9.5238095)

    def test_excessive_spread_rejection(self) -> None:
        contract = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())[0]
        q = option_quote_from_payload(contract.trading_symbol, quote(contract.trading_symbol, bid=100, ask=130))

        result = evaluate_candidate(contract, Moneyness.ATM, q, NOW, 25610, "HIGH", 14, OptionSelectionConfig(max_spread_pct=5))

        self.assertIn("Spread percentage is excessive.", result.rejection_reasons)

    def test_zero_volume_rejection(self) -> None:
        contract = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())[0]
        q = option_quote_from_payload(contract.trading_symbol, quote(contract.trading_symbol, volume=0))

        result = evaluate_candidate(contract, Moneyness.ATM, q, NOW, 25610, "HIGH", 14)

        self.assertIn("Volume is below threshold.", result.rejection_reasons)

    def test_zero_oi_rejection(self) -> None:
        contract = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())[0]
        q = option_quote_from_payload(contract.trading_symbol, quote(contract.trading_symbol, oi=0))

        result = evaluate_candidate(contract, Moneyness.ATM, q, NOW, 25610, "HIGH", 14)

        self.assertIn("Open interest is below threshold.", result.rejection_reasons)

    def test_premium_range_rejection(self) -> None:
        contract = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())[0]
        q = option_quote_from_payload(contract.trading_symbol, quote(contract.trading_symbol, ltp=2))

        result = evaluate_candidate(contract, Moneyness.ATM, q, NOW, 25610, "HIGH", 14)

        self.assertIn("Premium is outside configured range.", result.rejection_reasons)

    def test_quality_score_calculation(self) -> None:
        contract = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())[0]
        q = option_quote_from_payload(contract.trading_symbol, quote(contract.trading_symbol))

        result = evaluate_candidate(contract, Moneyness.ITM, q, NOW, 25610, "HIGH", 14)

        self.assertGreater(result.quality_score, 0)
        self.assertIn("moneyness", result.quality_breakdown)

    def test_high_confidence_preference(self) -> None:
        contracts = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())
        rec, _ = select_option_contract(ready(confidence="HIGH"), DataMode.LIVE, option_master(), quotes_for_contracts(contracts), NOW, 14)

        self.assertIn(rec.moneyness, {"ATM", "ITM"})

    def test_elevated_vix_itm_preference(self) -> None:
        contracts = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())
        rec, _ = select_option_contract(ready(confidence="MEDIUM"), DataMode.LIVE, option_master(), quotes_for_contracts(contracts), NOW, 22)

        self.assertEqual(rec.moneyness, "ITM")

    def test_no_suitable_contract_behavior(self) -> None:
        contracts = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())
        bad_quotes = {f"NFO:{contract.trading_symbol}": quote(contract.trading_symbol, volume=0, oi=0) for contract in contracts}

        rec, evaluations = select_option_contract(ready(), DataMode.LIVE, option_master(), bad_quotes, NOW, 14)

        self.assertEqual(rec.selection_status, "NO_SUITABLE_CONTRACT")
        self.assertTrue(evaluations)
        self.assertTrue(rec.rejection_reasons)

    def test_recommendation_deduplication(self) -> None:
        contracts = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())
        rec, _ = select_option_contract(ready(), DataMode.LIVE, option_master(), quotes_for_contracts(contracts), NOW, 14)
        state = empty_option_state()

        first, _ = append_recommendation(state, rec)
        second, _ = append_recommendation(state, rec)

        self.assertTrue(first)
        self.assertFalse(second)

    def test_persistence_across_restart(self) -> None:
        contracts = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())
        rec, _ = select_option_contract(ready(), DataMode.LIVE, option_master(), quotes_for_contracts(contracts), NOW, 14)
        state = empty_option_state()
        append_recommendation(state, rec)

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "options.json"
            save_option_state(state, path)
            loaded = load_option_state(path)

        self.assertEqual(len(loaded.recommendations), 1)

    def test_quote_capture_at_underlying_entry(self) -> None:
        contracts = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())
        rec, _ = select_option_contract(ready(), DataMode.LIVE, option_master(), quotes_for_contracts(contracts), NOW, 14)
        payload = quote(rec.trading_symbol, ltp=130, bid=129, ask=131)

        updated = capture_option_entry(rec.__dict__.copy(), option_quote_from_payload(rec.trading_symbol, payload), NOW)

        self.assertEqual(updated["option_entry_price"], 130)
        self.assertEqual(updated["option_entry_slippage"], 10)

    def test_outcome_premium_snapshots(self) -> None:
        contracts = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())
        rec, _ = select_option_contract(ready(), DataMode.LIVE, option_master(), quotes_for_contracts(contracts), NOW, 14)

        updated = capture_outcome_snapshot(rec.__dict__.copy(), "TARGET_1_HIT", option_quote_from_payload(rec.trading_symbol, quote(rec.trading_symbol)), NOW)

        self.assertEqual(updated["outcome_snapshots"][0]["outcome"], "TARGET_1_HIT")

    def test_sample_live_separation(self) -> None:
        contracts = discover_option_contracts(option_master(), "NIFTY", OptionSide.CE, NOW.date())
        live, _ = select_option_contract(ready(), DataMode.LIVE, option_master(), quotes_for_contracts(contracts), NOW, 14)
        sample, _ = select_option_contract(ready(), DataMode.SAMPLE, option_master(), quotes_for_contracts(contracts), NOW, 14)

        self.assertNotEqual(live.key, sample.key)
        self.assertEqual(sample.selection_status, "NOT_ELIGIBLE")


if __name__ == "__main__":
    unittest.main()
