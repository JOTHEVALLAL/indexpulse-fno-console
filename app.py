from __future__ import annotations

import csv
import io
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import streamlit as st

from momentum_edge.alerts import format_telegram_alert
from momentum_edge.config import kite_configuration_status, load_runtime_config, package_import_status, telegram_configuration_status
from momentum_edge.diagnostics import DEFAULT_DIAGNOSTIC_PATH, load_diagnostic_records
from momentum_edge.formatting import format_price, signal_detail
from momentum_edge.history import DEFAULT_HISTORY_PATH, append_alert, clear_alert_history, load_alert_history
from momentum_edge.kite_client import KiteAuthenticationError, KiteClient
from momentum_edge.lifecycle import (
    DEFAULT_LIFECYCLE_PATH,
    acknowledge_event,
    expire_prior_session_actionable,
    load_lifecycle_state,
    process_live_snapshot,
    save_lifecycle_state,
)
from momentum_edge.live_scanner import scan_live, unavailable_diagnostics
from momentum_edge.outcomes import (
    DEFAULT_OUTCOME_PATH,
    load_outcome_state,
    process_outcomes_for_candles,
    process_ready_signals,
    save_outcome_state,
)
from momentum_edge.options import (
    DEFAULT_OPTION_RECOMMENDATION_PATH,
    OptionSide,
    append_recommendation,
    capture_option_entry,
    capture_outcome_snapshot,
    discover_option_contracts,
    load_option_state,
    option_quote_from_payload,
    save_option_state,
    select_option_contract,
    strike_candidates,
)
from momentum_edge.performance import breakdown, filter_outcome_records, performance_dashboard_summary
from momentum_edge.rules import Signal, SignalStatus
from momentum_edge.scanner_state import DataMode, FreshnessState, ScannerCache, ScannerDiagnostics, ScannerState
from momentum_edge.sample_data import SampleScenario, evaluate_sample_scenarios
from momentum_edge.storage import PERSISTENCE_MODE, ensure_data_directory, persistence_file_status, runtime_data_dir
from momentum_edge.trades import TradeStatus, add_active_trade, trade_to_row, update_trade_status
from momentum_edge.ui_models import STATUS_ICON, filtered_signals, signal_to_display_row
from momentum_edge.version import APP_VERSION
from momentum_edge.watchlist import add_watchlist_entry, watchlist_to_row


st.set_page_config(
    page_title="IndexPulse",
    page_icon="ME",
    layout="wide",
)


def initialize_state() -> None:
    st.session_state.setdefault("active_trades", {})
    st.session_state.setdefault("watchlist", {})
    st.session_state.setdefault("selected_signal_key", "")
    st.session_state.setdefault("last_refresh_time", datetime.now())
    st.session_state.setdefault("scanner_cache", ScannerCache())
    st.session_state.setdefault("selected_event_key", "")


def render_deployment_diagnostics(data_mode: DataMode) -> None:
    runtime_config = load_runtime_config()
    data_dir = runtime_data_dir(runtime_config)
    data_status = ensure_data_directory(data_dir)
    kite_status = kite_configuration_status(runtime_config)
    telegram_status = telegram_configuration_status(runtime_config)
    st.sidebar.caption(f"Version: {APP_VERSION}")
    st.sidebar.caption(f"Deployment: {runtime_config.deployment_mode.value}")
    st.sidebar.caption(f"Data mode: {data_mode.value}")
    st.sidebar.caption(f"Persistence: {PERSISTENCE_MODE}")
    st.sidebar.warning("Cloud filesystem persistence is temporary until external storage is configured.")
    with st.sidebar.expander("Deployment Diagnostics"):
        st.write(
            {
                "application_version": APP_VERSION,
                "deployment_mode": runtime_config.deployment_mode.value,
                "app_env": runtime_config.app_env or "-",
                "data_mode": data_mode.value,
                "python_streamlit_imports": package_import_status(),
                "persistence_mode": PERSISTENCE_MODE,
                "data_directory": {
                    "path": str(data_status.path),
                    "exists": data_status.exists,
                    "writable": data_status.writable,
                    "error": data_status.error,
                },
                "persistence_files": persistence_file_status(data_dir),
                "kite_configuration": kite_status,
                "telegram_configuration": telegram_status,
            }
        )


def reset_sample_session() -> None:
    st.session_state.active_trades = {}
    st.session_state.watchlist = {}
    st.session_state.selected_signal_key = ""
    st.session_state.last_refresh_time = datetime.now()


def render_mode_banner(data_mode: DataMode, diagnostics: ScannerDiagnostics | None) -> None:
    if data_mode == DataMode.SAMPLE:
        if diagnostics and diagnostics.current_error:
            st.error(diagnostics.current_error)
        st.warning(
            "SAMPLE DATA mode. Signals are deterministic examples only. No Zerodha connection, live feed, Telegram sending, or order placement."
        )
        return
    if diagnostics and diagnostics.scanner_state == ScannerState.LIVE_READY:
        st.success("LIVE mode. Underlying index and futures data are active. Option recommendations are read-only. No order placement.")
    elif diagnostics and diagnostics.scanner_state == ScannerState.LIVE_CACHED:
        st.warning("LIVE CACHED mode. Showing the last valid live snapshot after a fetch failure or rerun cache window. Actions are disabled.")
    else:
        st.error("LIVE DATA UNAVAILABLE. Authentication or market-data retrieval failed.")


def scenario_key(scenario: SampleScenario, signal: Signal) -> str:
    return f"{scenario.name}|{signal.instrument}|{signal.signal_status.value}"


def render_signal_card(signal: Signal, scenario: SampleScenario) -> None:
    status_icon = STATUS_ICON[signal.signal_status]
    direction = signal.signal_direction.value if signal.signal_direction else "-"
    setup = signal.setup_name.value if signal.setup_name else "-"

    st.markdown(f"### {status_icon} {signal.instrument} - {signal.signal_status.value}")
    st.caption(scenario.description)

    top = st.columns(4)
    top[0].metric("Spot", format_price(signal.spot_price))
    top[1].metric("Direction", direction)
    top[2].metric("Setup", setup)
    top[3].metric("Confidence", signal.confidence_level)

    levels = st.columns(5)
    levels[0].metric("Entry", format_price(signal.entry_trigger))
    levels[1].metric("Stop Loss", format_price(signal.stop_loss))
    levels[2].metric("Target 1", format_price(signal.target_1))
    levels[3].metric("Target 2", format_price(signal.target_2))
    levels[4].metric("Risk-reward", "-" if signal.risk_reward_ratio is None else f"{signal.risk_reward_ratio:.2f}")

    st.write(f"**Reason:** {signal.reason}")
    st.write(f"**Invalidation:** {signal.invalidation_condition}")
    st.write(f"**Suggested option side:** {signal.suggested_option_side or '-'}")
    st.write(f"**Alert timestamp:** {signal.alert_timestamp.isoformat(sep=' ', timespec='minutes')}")

    with st.expander("Raw signal JSON"):
        st.json(signal_detail(signal))


def render_scanner_status(diagnostics: ScannerDiagnostics | None) -> None:
    if diagnostics is None:
        return
    st.subheader("Scanner Status")
    columns = st.columns(5)
    columns[0].metric("Data Mode", diagnostics.data_mode.value)
    columns[1].metric("Scanner State", diagnostics.scanner_state.value)
    columns[2].metric("Freshness", diagnostics.data_freshness.value if diagnostics.data_freshness else "-")
    columns[3].metric("Data Age", "-" if diagnostics.data_age_seconds is None else f"{diagnostics.data_age_seconds:.0f}s")
    columns[4].metric("VWAP Source", diagnostics.vwap_source)

    detail_columns = st.columns(5)
    detail_columns[0].caption(f"Last fetch: {diagnostics.last_successful_fetch or '-'}")
    detail_columns[1].caption(f"Last 5m candle: {diagnostics.last_completed_5m_candle or '-'}")
    detail_columns[2].caption(f"Last 15m candle: {diagnostics.last_completed_15m_candle or '-'}")
    detail_columns[3].caption(f"Last evaluation: {diagnostics.last_evaluation or '-'}")
    detail_columns[4].caption(f"Next expected: {diagnostics.next_expected_evaluation or '-'}")
    range_columns = st.columns(5)
    range_columns[0].caption(f"Historical from: {diagnostics.historical_range_from or '-'}")
    range_columns[1].caption(f"Historical to: {diagnostics.historical_range_to or '-'}")
    range_columns[2].caption(f"Timezone: {diagnostics.timezone}")
    range_columns[3].caption(f"Current IST: {diagnostics.current_ist_time or '-'}")
    range_columns[4].caption(f"Session: {diagnostics.selected_trading_session or '-'}")
    if diagnostics.current_error:
        st.error(diagnostics.current_error)


def lifecycle_rows(lifecycle_state: Any, data_mode: DataMode) -> list[dict[str, Any]]:
    return [record for record in lifecycle_state.records if record.get("data_mode") == data_mode.value]


def lifecycle_by_instrument(lifecycle_state: Any, data_mode: DataMode) -> dict[str, dict[str, Any]]:
    return {record["instrument"]: record for record in lifecycle_rows(lifecycle_state, data_mode)}


def signal_age(record: dict[str, Any]) -> str:
    first = record.get("first_detected_time")
    last = record.get("last_evaluated_time")
    if not first or not last:
        return "-"
    try:
        minutes = int((datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds() // 60)
    except ValueError:
        return "-"
    return f"{minutes}m"


def signal_display_rows_with_lifecycle(signals: list[Signal], lifecycle_state: Any, data_mode: DataMode) -> list[dict[str, Any]]:
    lifecycle_map = lifecycle_by_instrument(lifecycle_state, data_mode)
    rows = []
    for signal in signals:
        row = signal_to_display_row(signal)
        record = lifecycle_map.get(signal.instrument, {})
        row["Lifecycle Status"] = record.get("current_status", "-")
        row["Previous Status"] = record.get("previous_status") or "-"
        row["Signal Age"] = signal_age(record) if record else "-"
        row["Candles In PREPARE"] = record.get("candles_in_prepare", "-")
        row["Last Transition"] = record.get("status_changed_time", "-")
        row["Last Evaluated Candle"] = record.get("trigger_candle_timestamp", "-")
        row["Latest Event"] = record.get("latest_event_type") or "-"
        row["Confidence Explanation"] = record.get("confidence_breakdown", {}).get("components", {})
        rows.append(row)
    return rows


def render_dashboard(signals: list[Signal], diagnostics: ScannerDiagnostics | None, lifecycle_state: Any, data_mode: DataMode) -> None:
    render_scanner_status(diagnostics)
    ready_count = sum(signal.signal_status == SignalStatus.READY for signal in signals)
    prepare_count = sum(signal.signal_status == SignalStatus.PREPARE for signal in signals)
    active_count = len(st.session_state.active_trades)
    latest_signal_time = max((signal.alert_timestamp for signal in signals), default=None)
    last_refresh = st.session_state.last_refresh_time.isoformat(sep=" ", timespec="seconds")

    columns = st.columns(5)
    columns[0].metric("READY", ready_count)
    columns[1].metric("PREPARE", prepare_count)
    columns[2].metric("Active Trades", active_count)
    columns[3].metric("Latest Signal", "-" if latest_signal_time is None else latest_signal_time.isoformat(sep=" ", timespec="minutes"))
    columns[4].metric("Last Refresh", last_refresh)

    st.subheader("Signal Overview")
    if not signals:
        st.info("No signals available for the selected data mode.")
    else:
        st.dataframe(signal_display_rows_with_lifecycle(signals, lifecycle_state, data_mode), use_container_width=True, hide_index=True)


def render_signal_actions(signal: Signal, actions_enabled: bool, disabled_reason: str | None = None) -> None:
    if not actions_enabled:
        st.button("Trade Action Unavailable", disabled=True)
        st.caption(disabled_reason or "Actions are disabled for this scanner state.")
        return

    if signal.signal_status == SignalStatus.READY:
        if st.button("Add to Active Trades", type="primary"):
            added, message = add_active_trade(st.session_state.active_trades, signal)
            st.success(message) if added else st.warning(message)
    elif signal.signal_status == SignalStatus.PREPARE:
        if st.button("Add to Watchlist", type="primary"):
            added, message = add_watchlist_entry(st.session_state.watchlist, signal)
            st.success(message) if added else st.warning(message)
    else:
        st.button("Trade Action Unavailable", disabled=True)
        st.caption("WAIT, AVOID, and NO_TRADE signals cannot be added to active trades or watchlist.")

    if st.button("Persist Alert to Local History"):
        added, _records = append_alert(signal, DEFAULT_HISTORY_PATH)
        if added:
            st.success(f"Saved to {DEFAULT_HISTORY_PATH}.")
        else:
            st.warning("Duplicate alert already exists in local history.")


def render_intraday_setups(
    scenarios_and_signals: list[tuple[SampleScenario, Signal]],
    actions_enabled: bool,
    lifecycle_state: Any,
    data_mode: DataMode,
    disabled_reason: str | None = None,
) -> None:
    st.subheader("Intraday Setups")
    signals_by_key = {scenario_key(scenario, signal): (scenario, signal) for scenario, signal in scenarios_and_signals}
    all_signals = [signal for _, signal in scenarios_and_signals]
    if not all_signals:
        st.info("No setup rows available. In LIVE mode this usually means LIVE DATA UNAVAILABLE.")
        return

    filter_columns = st.columns(3)
    instruments = sorted({signal.instrument for signal in all_signals})
    statuses = [status.value for status in SignalStatus]
    setups = sorted({signal.setup_name.value if signal.setup_name else "-" for signal in all_signals})

    selected_instruments = set(filter_columns[0].multiselect("Instrument", instruments, default=instruments))
    selected_statuses = set(filter_columns[1].multiselect("Status", statuses, default=statuses))
    selected_setups = set(filter_columns[2].multiselect("Setup", setups, default=setups))

    filtered = filtered_signals(all_signals, selected_instruments, selected_statuses, selected_setups)
    st.dataframe(signal_display_rows_with_lifecycle(filtered, lifecycle_state, data_mode), use_container_width=True, hide_index=True)

    selectable = [
        key
        for key, (_scenario, signal) in signals_by_key.items()
        if signal in filtered
    ]
    if not selectable:
        st.info("No signals match the selected filters.")
        return

    if st.session_state.selected_signal_key not in selectable:
        st.session_state.selected_signal_key = selectable[0]

    selected_key = st.selectbox("Detailed signal view", selectable, index=selectable.index(st.session_state.selected_signal_key))
    st.session_state.selected_signal_key = selected_key
    selected_scenario, selected_signal = signals_by_key[selected_key]

    left, right = st.columns([1.2, 1])
    with left:
        render_signal_card(selected_signal, selected_scenario)
        render_signal_actions(selected_signal, actions_enabled, disabled_reason)
    with right:
        st.subheader("Telegram Preview")
        st.code(format_telegram_alert(selected_signal), language="text")

    if st.session_state.watchlist:
        st.subheader("Watchlist")
        st.dataframe(
            [watchlist_to_row(entry) for entry in st.session_state.watchlist.values()],
            use_container_width=True,
            hide_index=True,
        )


def render_active_trades() -> None:
    st.subheader("Active Trades")
    active_trades = st.session_state.active_trades
    if not active_trades:
        st.info("No active trades in this session.")
        return

    st.dataframe([trade_to_row(trade) for trade in active_trades.values()], use_container_width=True, hide_index=True)
    selected_key = st.selectbox("Trade", list(active_trades.keys()))
    selected_status = st.selectbox("Update status", [status.value for status in TradeStatus])

    if st.button("Apply Trade Status"):
        active_trades[selected_key] = update_trade_status(active_trades[selected_key], TradeStatus(selected_status))
        st.success("Trade status updated.")


def history_csv(records: list[dict[str, Any]]) -> str:
    if not records:
        return ""
    output = io.StringIO()
    columns = sorted({key for record in records for key in record.keys()})
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(records)
    return output.getvalue()


def render_alert_history() -> None:
    st.subheader("Alert History")
    records = load_alert_history(DEFAULT_HISTORY_PATH)
    st.caption(f"Local file: {DEFAULT_HISTORY_PATH}")

    if not records:
        st.info("No persisted alert history yet.")
        return

    dates = sorted({str(record.get("alert_timestamp", ""))[:10] for record in records if record.get("alert_timestamp")})
    instruments = sorted({str(record.get("instrument", "-")) for record in records})
    statuses = sorted({str(record.get("signal_status", "-")) for record in records})
    outcomes = sorted({str(record.get("outcome", record.get("signal_status", "-"))) for record in records})

    columns = st.columns(4)
    selected_dates = set(columns[0].multiselect("Date", dates, default=dates))
    selected_instruments = set(columns[1].multiselect("Instrument", instruments, default=instruments))
    selected_statuses = set(columns[2].multiselect("Status", statuses, default=statuses))
    selected_outcomes = set(columns[3].multiselect("Outcome", outcomes, default=outcomes))

    filtered_records = [
        record
        for record in records
        if str(record.get("alert_timestamp", ""))[:10] in selected_dates
        and str(record.get("instrument", "-")) in selected_instruments
        and str(record.get("signal_status", "-")) in selected_statuses
        and str(record.get("outcome", record.get("signal_status", "-"))) in selected_outcomes
    ]

    st.dataframe(filtered_records, use_container_width=True, hide_index=True)
    st.download_button(
        "Download CSV",
        data=history_csv(filtered_records),
        file_name="momentum_edge_alert_history.csv",
        mime="text/csv",
        disabled=not filtered_records,
    )


def render_alert_centre(lifecycle_state: Any, outcome_state: Any, option_state: Any) -> None:
    st.subheader("Alert Centre")
    today = datetime.now().date().isoformat()
    option_by_signal = {item.get("underlying_signal_key"): item for item in option_state.recommendations}
    events = []
    for event in [*lifecycle_state.events, *outcome_state.events]:
        if str(event.get("event_timestamp", ""))[:10] != today:
            continue
        enriched = dict(event)
        rec = option_by_signal.get(event.get("signal_key"))
        if rec:
            enriched.update(
                {
                    "selected_option_symbol": rec.get("trading_symbol"),
                    "option_side": rec.get("option_side"),
                    "option_strike": rec.get("strike"),
                    "option_expiry": rec.get("expiry"),
                    "option_premium": rec.get("ltp"),
                    "option_bid": rec.get("bid"),
                    "option_ask": rec.get("ask"),
                    "option_spread": rec.get("spread"),
                    "option_volume": rec.get("volume"),
                    "option_oi": rec.get("open_interest"),
                    "option_quality_score": rec.get("quality_score"),
                    "option_selection_status": rec.get("selection_status"),
                    "option_rejection_summary": "; ".join(rec.get("rejection_reasons") or []),
                }
            )
        events.append(enriched)
    events = sorted(events, key=lambda event: event.get("event_timestamp", ""), reverse=True)
    unacknowledged = sum(not event.get("acknowledged", False) for event in events)
    st.metric("Unacknowledged", unacknowledged)
    if not events:
        st.info("No alert events for today.")
        return

    filter_columns = st.columns(4)
    instruments = sorted({event.get("instrument", "-") for event in events})
    event_types = sorted({event.get("event_type", "-") for event in events})
    directions = sorted({event.get("direction") or "-" for event in events})
    confidences = sorted({event.get("confidence", "-") for event in events})
    selected_instruments = set(filter_columns[0].multiselect("Instrument", instruments, default=instruments))
    selected_event_types = set(filter_columns[1].multiselect("Event Type", event_types, default=event_types))
    selected_directions = set(filter_columns[2].multiselect("Direction", directions, default=directions))
    selected_confidences = set(filter_columns[3].multiselect("Confidence", confidences, default=confidences))

    filtered = [
        event
        for event in events
        if event.get("instrument", "-") in selected_instruments
        and event.get("event_type", "-") in selected_event_types
        and (event.get("direction") or "-") in selected_directions
        and event.get("confidence", "-") in selected_confidences
    ]
    st.dataframe(filtered, use_container_width=True, hide_index=True)
    st.download_button(
        "Download Alert Events CSV",
        data=history_csv(filtered),
        file_name="momentum_edge_alert_events.csv",
        mime="text/csv",
        disabled=not filtered,
    )
    if not filtered:
        return
    keys = [event["event_key"] for event in filtered]
    if st.session_state.selected_event_key not in keys:
        st.session_state.selected_event_key = keys[0]
    selected_key = st.selectbox("Event detail", keys, index=keys.index(st.session_state.selected_event_key))
    st.session_state.selected_event_key = selected_key
    selected_event = next(event for event in filtered if event["event_key"] == selected_key)
    st.json(selected_event)
    if not selected_event.get("acknowledged", False) and st.button("Acknowledge Event"):
        acknowledge_event(lifecycle_state, selected_key)
        for event in outcome_state.events:
            if event.get("event_key") == selected_key:
                event["acknowledged"] = True
        save_lifecycle_state(lifecycle_state, DEFAULT_LIFECYCLE_PATH)
        save_outcome_state(outcome_state, DEFAULT_OUTCOME_PATH)
        st.success("Event acknowledged.")


def render_performance(outcome_state: Any, lifecycle_state: Any) -> None:
    st.subheader("Performance")
    records = outcome_state.records
    if not records:
        st.info("No paper-trade outcome records yet.")
        return

    dates = sorted({record["session_date"] for record in records})
    filter_columns = st.columns(6)
    start = filter_columns[0].date_input("Start", value=datetime.fromisoformat(dates[0]).date())
    end = filter_columns[1].date_input("End", value=datetime.fromisoformat(dates[-1]).date())
    instruments = sorted({record.get("instrument", "-") for record in records})
    setups = sorted({record.get("setup") or "-" for record in records})
    directions = sorted({record.get("direction") or "-" for record in records})
    confidences = sorted({record.get("confidence") or "-" for record in records})
    outcomes = sorted({record.get("execution_state") or "-" for record in records})
    modes = sorted({record.get("data_mode") or "-" for record in records})

    selected_instruments = set(filter_columns[2].multiselect("Instrument", instruments, default=instruments))
    selected_setups = set(filter_columns[3].multiselect("Setup", setups, default=setups))
    selected_directions = set(filter_columns[4].multiselect("Direction", directions, default=directions))
    selected_confidences = set(filter_columns[5].multiselect("Confidence", confidences, default=confidences))
    more_filters = st.columns(2)
    selected_outcomes = set(more_filters[0].multiselect("Outcome", outcomes, default=outcomes))
    selected_modes = set(more_filters[1].multiselect("Data Mode", modes, default=modes))

    filtered = filter_outcome_records(
        records,
        start_date=start,
        end_date=end,
        instruments=selected_instruments,
        setups=selected_setups,
        directions=selected_directions,
        confidences=selected_confidences,
        outcomes=selected_outcomes,
        data_modes=selected_modes,
    )
    summary = performance_dashboard_summary(filtered, lifecycle_state.records)
    metric_columns = st.columns(6)
    metric_columns[0].metric("Total PREPARE", summary["total_prepare_signals"])
    metric_columns[1].metric("PREPARE -> READY", f"{summary['prepare_to_ready_conversion_rate']:.0%}")
    metric_columns[2].metric("Total READY", summary["total_ready_signals"])
    metric_columns[3].metric("Entry Rate", f"{summary['entry_trigger_rate']:.0%}")
    metric_columns[4].metric("T1 Hit Rate", f"{summary['target_1_hit_rate']:.0%}")
    metric_columns[5].metric("T2 Hit Rate", f"{summary['target_2_hit_rate']:.0%}")
    metric_columns_2 = st.columns(6)
    metric_columns_2[0].metric("Stop Rate", f"{summary['stop_loss_rate']:.0%}")
    metric_columns_2[1].metric("Invalidated", f"{summary['invalidated_before_entry_rate']:.0%}")
    metric_columns_2[2].metric("Expired", f"{summary['expired_before_entry_rate']:.0%}")
    metric_columns_2[3].metric("Avg R", "-" if summary["average_realised_r"] is None else f"{summary['average_realised_r']:.2f}")
    metric_columns_2[4].metric("Median R", "-" if summary["median_realised_r"] is None else f"{summary['median_realised_r']:.2f}")
    metric_columns_2[5].metric("Ambiguous", summary["ambiguous_outcome_count"])

    st.subheader("Paper Trades")
    table = [
        {
            "Instrument": record.get("instrument"),
            "Setup": record.get("setup"),
            "Direction": record.get("direction"),
            "Signal Time": record.get("signal_time"),
            "READY Time": record.get("ready_time"),
            "Planned Entry": record.get("planned_entry"),
            "Actual Entry": record.get("actual_entry"),
            "Stop Loss": record.get("stop_loss"),
            "Target 1": record.get("target_1"),
            "Target 2": record.get("target_2"),
            "Execution State": record.get("execution_state"),
            "Outcome": record.get("final_outcome") or record.get("current_outcome"),
            "Realised R": record.get("realised_r"),
            "MFE R": record.get("mfe_r"),
            "MAE R": record.get("mae_r"),
            "Confidence": record.get("confidence"),
            "Delay": record.get("signal_to_entry_delay_minutes"),
            "Exit Time": record.get("exit_timestamp"),
            "Exit Reason": record.get("exit_reason"),
        }
        for record in filtered
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)
    st.download_button(
        "Download Paper Trades CSV",
        data=history_csv(table),
        file_name="momentum_edge_paper_trades.csv",
        mime="text/csv",
        disabled=not table,
    )

    st.subheader("Breakdowns")
    for label, field in [
        ("Instrument", "instrument"),
        ("Setup", "setup"),
        ("Confidence", "confidence"),
        ("Direction", "direction"),
        ("Hour / Time Slot", "hour_bucket"),
        ("Weekday", "weekday"),
    ]:
        with st.expander(label):
            st.dataframe(breakdown(filtered, field), use_container_width=True, hide_index=True)


def render_option_selection(option_state: Any, lifecycle_state: Any) -> None:
    st.subheader("Option Selection")
    st.warning("Recommendation only. No order placed. Exit remains controlled by underlying levels.")
    records = option_state.recommendations
    if not records:
        st.info("No option recommendations yet.")
        return
    rows = sorted(records, key=lambda item: item.get("recommendation_timestamp", ""), reverse=True)
    st.dataframe(
        [
            {
                "Underlying": item.get("underlying_instrument"),
                "Direction": item.get("underlying_direction"),
                "Suggested": item.get("option_side"),
                "Contract": item.get("trading_symbol") or "No suitable contract",
                "Strike": item.get("strike"),
                "Expiry": item.get("expiry"),
                "Moneyness": item.get("moneyness"),
                "Premium": item.get("ltp"),
                "Bid": item.get("bid"),
                "Ask": item.get("ask"),
                "Spread %": item.get("spread_percentage"),
                "Volume": item.get("volume"),
                "OI": item.get("open_interest"),
                "Freshness": item.get("quote_freshness"),
                "Score": item.get("quality_score"),
                "Status": item.get("selection_status"),
            }
            for item in rows
        ],
        use_container_width=True,
        hide_index=True,
    )
    selected = st.selectbox("Recommendation detail", [item["key"] for item in rows])
    record = next(item for item in rows if item["key"] == selected)
    st.json(record)
    st.download_button(
        "Download Option Recommendations CSV",
        data=history_csv(rows),
        file_name="momentum_edge_option_recommendations.csv",
        mime="text/csv",
    )


def build_option_recommendations(client: KiteClient, live_items: tuple[Any, ...], diagnostics: ScannerDiagnostics | None, option_state: Any) -> None:
    if diagnostics is None or diagnostics.scanner_state != ScannerState.LIVE_READY or diagnostics.data_freshness == FreshnessState.STALE:
        return
    nfo_instruments = client.instruments("NFO")
    now = st.session_state.last_refresh_time
    for item in live_items:
        signal = item.signal
        if signal.signal_status != SignalStatus.READY or item.is_cached or item.metrics.candle_alignment_status != "ALIGNED":
            continue
        side = "CE" if signal.signal_direction and signal.signal_direction.value == "BULLISH" else "PE"
        underlying = "NIFTY" if signal.instrument == "NIFTY 50" else "BANKNIFTY"
        contracts = discover_option_contracts(nfo_instruments, underlying, OptionSide(side), now.date())
        candidate_pairs = strike_candidates(contracts, signal.spot_price)
        quote_keys = [f"NFO:{contract.trading_symbol}" for contract, _moneyness in candidate_pairs]
        quote_payloads = client.quotes(quote_keys) if quote_keys else {}
        recommendation, _evaluations = select_option_contract(
            signal,
            DataMode.LIVE,
            nfo_instruments,
            quote_payloads,
            now,
            item.context.indicators.india_vix,
        )
        append_recommendation(option_state, recommendation)


def update_option_recommendations_from_outcomes(client: KiteClient, option_state: Any, outcome_state: Any) -> None:
    if not option_state.recommendations:
        return
    records_by_signal = {item.get("underlying_signal_key"): item for item in option_state.recommendations}
    now = st.session_state.last_refresh_time
    final_states = {
        "TARGET_1_HIT",
        "TARGET_2_HIT",
        "STOP_LOSS_HIT",
        "INVALIDATED_BEFORE_ENTRY",
        "EXPIRED_BEFORE_ENTRY",
        "SESSION_CLOSED",
    }
    for outcome in outcome_state.records:
        recommendation = records_by_signal.get(outcome.get("signal_key"))
        symbol = recommendation.get("trading_symbol") if recommendation else None
        if not recommendation or not symbol:
            continue
        payload = client.quotes([f"NFO:{symbol}"]).get(f"NFO:{symbol}")
        quote = option_quote_from_payload(symbol, payload)
        if outcome.get("execution_state") == "ENTRY_TRIGGERED" and recommendation.get("option_entry_price") is None:
            capture_option_entry(recommendation, quote, now)
        if outcome.get("execution_state") in final_states:
            existing = recommendation.get("outcome_snapshots") or []
            if not any(snapshot.get("outcome") == outcome.get("execution_state") for snapshot in existing):
                capture_outcome_snapshot(recommendation, outcome.get("execution_state"), quote, now)


def render_diagnostics(diagnostics: ScannerDiagnostics | None, live_items: tuple[Any, ...] = tuple()) -> None:
    st.subheader("Diagnostics")
    render_scanner_status(diagnostics)
    if live_items:
        st.subheader("Current Live Snapshot")
        st.dataframe(
            [
                {
                    "Instrument": item.instrument,
                    "Spot Symbol": item.instrument,
                    "Futures Symbol": item.futures_symbol or "-",
                    "Futures Expiry": item.futures_expiry or "-",
                    "Spot Timestamp": item.context.candle.timestamp,
                    "Futures Timestamp": item.last_completed_5m,
                    "Candle Alignment": item.metrics.candle_alignment_status,
                    "Alignment Diff": item.metrics.candle_alignment_difference_seconds,
                    "VWAP Source": item.vwap_source,
                    "Futures VWAP": item.metrics.futures_vwap,
                    "Spot Price": item.metrics.spot_price,
                    "Futures Price": item.metrics.futures_price,
                    "Basis": item.metrics.futures_spot_basis,
                    "Cached": item.is_cached,
                    "Action Block": item.action_block_reason or "-",
                }
                for item in live_items
            ],
            use_container_width=True,
            hide_index=True,
        )
    records = load_diagnostic_records(DEFAULT_DIAGNOSTIC_PATH)
    st.caption(f"Market diagnostic log: {DEFAULT_DIAGNOSTIC_PATH}")
    if records:
        recent = records[-50:]
        st.dataframe(recent, use_container_width=True, hide_index=True)
        st.download_button(
            "Download Diagnostics CSV",
            data=history_csv(recent),
            file_name="momentum_edge_market_diagnostics.csv",
            mime="text/csv",
        )
    else:
        st.info("No market diagnostic records yet.")


def render_controls() -> None:
    st.sidebar.header("Controls")
    if st.sidebar.checkbox("Confirm reset sample session"):
        if st.sidebar.button("Reset Sample Session"):
            reset_sample_session()
            st.sidebar.success("Sample session reset.")

    if st.sidebar.checkbox("Confirm clear active trades"):
        if st.sidebar.button("Clear Active Trades"):
            st.session_state.active_trades = {}
            st.sidebar.success("Active trades cleared.")

    if st.sidebar.checkbox("Confirm clear sample alert history"):
        if st.sidebar.button("Clear Sample Alert History"):
            clear_alert_history(DEFAULT_HISTORY_PATH)
            st.sidebar.success("Sample alert history cleared.")


def safe_save(label: str, callback: Any) -> None:
    try:
        callback()
    except OSError as exc:
        st.warning(f"{label} could not be persisted on this filesystem: {exc}")


def sample_diagnostics(now: datetime, scenarios_and_signals: list[tuple[SampleScenario, Signal]], error: str | None = None) -> ScannerDiagnostics:
    return ScannerDiagnostics(
        data_mode=DataMode.SAMPLE,
        scanner_state=ScannerState.SAMPLE_READY,
        last_successful_fetch=None,
        last_completed_5m_candle=max(signal.alert_timestamp for _, signal in scenarios_and_signals),
        last_completed_15m_candle=None,
        data_freshness=None,
        data_age_seconds=None,
        vwap_source="SAMPLE_DATA",
        last_evaluation=now,
        next_expected_evaluation=None,
        current_error=error,
    )


def sample_scenarios_with_diagnostics(now: datetime, error: str | None = None) -> tuple[list[tuple[SampleScenario, Signal]], ScannerDiagnostics]:
    scenarios_and_signals = evaluate_sample_scenarios()
    return scenarios_and_signals, sample_diagnostics(now, scenarios_and_signals, error)


def main() -> None:
    initialize_state()
    st.session_state.last_refresh_time = datetime.now()
    selected_data_mode = DataMode(st.sidebar.radio("Data Mode", [mode.value for mode in DataMode], horizontal=True))
    render_deployment_diagnostics(selected_data_mode)

    diagnostics = None
    live_items = tuple()
    lifecycle_state = load_lifecycle_state(DEFAULT_LIFECYCLE_PATH)
    outcome_state = load_outcome_state(DEFAULT_OUTCOME_PATH)
    option_state = load_option_state(DEFAULT_OPTION_RECOMMENDATION_PATH)
    expire_prior_session_actionable(lifecycle_state, selected_data_mode, st.session_state.last_refresh_time.date(), st.session_state.last_refresh_time)
    effective_data_mode = selected_data_mode
    if selected_data_mode == DataMode.SAMPLE:
        scenarios_and_signals, diagnostics = sample_scenarios_with_diagnostics(st.session_state.last_refresh_time)
    else:
        runtime_config = load_runtime_config()
        kite_status = kite_configuration_status(runtime_config)
        if not kite_status["configured"]:
            live_snapshot = None
            diagnostics = unavailable_diagnostics(
                "LIVE mode unavailable: Kite API key, API secret, and access token must be configured.",
                st.session_state.last_refresh_time,
                st.session_state.scanner_cache,
            )
        else:
            try:
                kite_client = KiteClient()
                live_snapshot = scan_live(kite_client, st.session_state.last_refresh_time, st.session_state.scanner_cache)
            except KiteAuthenticationError as exc:
                live_snapshot = None
                diagnostics = unavailable_diagnostics(str(exc), st.session_state.last_refresh_time, st.session_state.scanner_cache)
        if live_snapshot is not None:
            diagnostics = live_snapshot.diagnostics
            live_items = live_snapshot.instruments
            if live_items:
                process_live_snapshot(lifecycle_state, live_items, diagnostics.data_freshness)
                process_ready_signals(outcome_state, [item.signal for item in live_items], selected_data_mode)
                data_safe = (
                    diagnostics.scanner_state == ScannerState.LIVE_READY
                    and diagnostics.data_freshness != FreshnessState.STALE
                    and all(item.metrics.candle_alignment_status == "ALIGNED" and not item.is_cached for item in live_items)
                )
                process_outcomes_for_candles(
                    outcome_state,
                    {item.instrument: item.context.candle for item in live_items},
                    data_safe=data_safe,
                )
                try:
                    build_option_recommendations(kite_client, live_items, diagnostics, option_state)
                    update_option_recommendations_from_outcomes(kite_client, option_state, outcome_state)
                except Exception as exc:
                    if diagnostics:
                        diagnostics = ScannerDiagnostics(
                            data_mode=diagnostics.data_mode,
                            scanner_state=diagnostics.scanner_state,
                            last_successful_fetch=diagnostics.last_successful_fetch,
                            last_completed_5m_candle=diagnostics.last_completed_5m_candle,
                            last_completed_15m_candle=diagnostics.last_completed_15m_candle,
                            data_freshness=diagnostics.data_freshness,
                            data_age_seconds=diagnostics.data_age_seconds,
                            vwap_source=diagnostics.vwap_source,
                            last_evaluation=diagnostics.last_evaluation,
                            next_expected_evaluation=diagnostics.next_expected_evaluation,
                            current_error=f"{diagnostics.current_error or ''} Option recommendation unavailable: {exc}".strip(),
                        )
            scenarios_and_signals = [
                (
                    SampleScenario(
                        name=f"LIVE {item.instrument}",
                        description="Live underlying-market-data evaluation. No option-chain selection or order placement.",
                        context=item.context,
                    ),
                    item.signal,
                )
                for item in live_snapshot.instruments
            ]
        else:
            scenarios_and_signals = []
    safe_save("Lifecycle state", lambda: save_lifecycle_state(lifecycle_state, DEFAULT_LIFECYCLE_PATH))
    safe_save("Outcome state", lambda: save_outcome_state(outcome_state, DEFAULT_OUTCOME_PATH))
    safe_save("Option recommendations", lambda: save_option_state(option_state, DEFAULT_OPTION_RECOMMENDATION_PATH))

    signals = [signal for _, signal in scenarios_and_signals]
    actions_enabled = selected_data_mode == DataMode.SAMPLE
    disabled_reason = None
    if selected_data_mode == DataMode.LIVE:
        actions_enabled = (
            diagnostics is not None
            and diagnostics.scanner_state == ScannerState.LIVE_READY
            and diagnostics.data_freshness != FreshnessState.STALE
            and all(item.metrics.candle_alignment_status == "ALIGNED" for item in live_items)
            and not any(item.is_cached for item in live_items)
        )
        if not actions_enabled:
            disabled_reason = "LIVE actions disabled because data is unavailable, cached, stale, or not safely confirmed."

    st.title("IndexPulse — NIFTY & BANK NIFTY F&O Signal Console")
    if selected_data_mode != effective_data_mode:
        st.info(f"Selected DATA MODE: {selected_data_mode.value}. Effective DATA MODE: {effective_data_mode.value}.")
    render_mode_banner(effective_data_mode, diagnostics)
    render_controls()

    dashboard_tab, setups_tab, trades_tab, history_tab, alert_tab, option_tab, performance_tab, diagnostics_tab = st.tabs(
        ["Dashboard", "Intraday Setups", "Active Trades", "Alert History", "Alert Centre", "Option Selection", "Performance", "Diagnostics"]
    )

    with dashboard_tab:
        render_dashboard(signals, diagnostics, lifecycle_state, selected_data_mode)
    with setups_tab:
        render_intraday_setups(scenarios_and_signals, actions_enabled, lifecycle_state, selected_data_mode, disabled_reason)
    with trades_tab:
        render_active_trades()
    with history_tab:
        render_alert_history()
    with alert_tab:
        render_alert_centre(lifecycle_state, outcome_state, option_state)
    with option_tab:
        render_option_selection(option_state, lifecycle_state)
    with performance_tab:
        render_performance(outcome_state, lifecycle_state)
    with diagnostics_tab:
        render_diagnostics(diagnostics, live_items)


if __name__ == "__main__":
    main()
