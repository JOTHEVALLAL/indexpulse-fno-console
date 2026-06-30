from __future__ import annotations

from datetime import datetime
import unittest

import momentum_edge.alerts as alerts_module
from momentum_edge.dashboard_ui import (
    AUTO_REFRESH_INTERVAL_SECONDS,
    SCANNER_CARD_LABEL_CLASS,
    SUMMARY_CARD_LABEL_CLASS,
    compact_vwap_source,
    format_dashboard_timestamp,
    normalize_status_display,
)
from momentum_edge.sample_data import evaluate_sample_scenarios
from momentum_edge.scanner_state import DataMode


class DashboardUiTest(unittest.TestCase):
    def test_dashboard_timestamp_compact_formatting(self) -> None:
        timestamp = datetime(2026, 6, 30, 14, 22, 12)

        self.assertEqual(format_dashboard_timestamp(timestamp), "30-Jun 14:22")
        self.assertEqual(format_dashboard_timestamp(timestamp, include_seconds=True), "30-Jun 14:22:12")

    def test_missing_dashboard_timestamp_displays_dash(self) -> None:
        self.assertEqual(format_dashboard_timestamp(None), "-")

    def test_vwap_source_compact_formatting(self) -> None:
        self.assertEqual(compact_vwap_source("BANKNIFTY26JUNFUT"), "BANKNIFTY JUN FUT")
        self.assertEqual(compact_vwap_source("NIFTY26JULFUT"), "NIFTY JUL FUT")

    def test_exact_vwap_source_can_remain_available_for_diagnostics(self) -> None:
        exact = "BANKNIFTY26JUNFUT"

        self.assertEqual(compact_vwap_source(exact), "BANKNIFTY JUN FUT")
        self.assertEqual(exact, "BANKNIFTY26JUNFUT")

    def test_auto_refresh_interval_is_30_seconds(self) -> None:
        self.assertEqual(AUTO_REFRESH_INTERVAL_SECONDS, 30)
        self.assertGreaterEqual(AUTO_REFRESH_INTERVAL_SECONDS, 30)

    def test_card_css_classes_are_scoped(self) -> None:
        self.assertEqual(SCANNER_CARD_LABEL_CLASS, "indexpulse-status-label")
        self.assertEqual(SUMMARY_CARD_LABEL_CLASS, "indexpulse-summary-label")

    def test_status_normalization_removes_duplicate_text(self) -> None:
        cases = {
            "[READY] READY": "READY",
            "[READY]READY": "READY",
            "PREPARE": "PREPARE",
            "[WAIT] WAIT": "WAIT",
            "[AVOID]AVOID": "AVOID",
            "NO_TRADE": "NO_TRADE",
        }

        for raw, expected in cases.items():
            self.assertEqual(normalize_status_display(raw), expected)

    def test_auto_refresh_does_not_introduce_telegram_dispatch(self) -> None:
        self.assertFalse(hasattr(alerts_module, "send_telegram_alert"))

    def test_sample_deterministic_behaviour_remains_available(self) -> None:
        first = [(scenario.name, signal.signal_status.value) for scenario, signal in evaluate_sample_scenarios()]
        second = [(scenario.name, signal.signal_status.value) for scenario, signal in evaluate_sample_scenarios()]

        self.assertEqual(first, second)
        self.assertEqual(DataMode.SAMPLE.value, "SAMPLE")


if __name__ == "__main__":
    unittest.main()
