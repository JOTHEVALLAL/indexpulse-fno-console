from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .confidence import confidence_breakdown, confidence_to_record
from .live_snapshot import LiveInstrumentSnapshot
from .rules import Direction, Signal, SignalEvaluator, SignalStatus, SetupName
from .scanner_state import DataMode, FreshnessState
from .storage import runtime_data_dir


DEFAULT_LIFECYCLE_PATH = runtime_data_dir() / "signal_lifecycle.json"
ACTIONABLE = {SignalStatus.PREPARE, SignalStatus.READY}


class AlertEventType(str, Enum):
    PREPARE_NEW = "PREPARE_NEW"
    READY_DIRECT = "READY_DIRECT"
    READY_UPGRADE = "READY_UPGRADE"
    SIGNAL_INVALIDATED = "SIGNAL_INVALIDATED"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
    DIRECTION_CHANGED = "DIRECTION_CHANGED"


@dataclass(frozen=True)
class LifecycleConfig:
    prepare_expiry_candles: int = 3


@dataclass
class LifecycleRecord:
    record_key: str
    data_mode: str
    session_date: str
    instrument: str
    current_status: str
    previous_status: str | None
    first_detected_time: str
    status_changed_time: str
    last_evaluated_time: str
    signal_direction: str | None
    setup: str | None
    trigger_candle_timestamp: str
    entry: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    confidence: str
    reasons: str
    signal_key: str | None
    candles_in_prepare: int = 0
    last_evaluation_key: str | None = None
    latest_event_type: str | None = None
    confidence_breakdown: dict[str, Any] | None = None


@dataclass
class AlertEvent:
    event_timestamp: str
    instrument: str
    event_type: str
    previous_status: str | None
    new_status: str
    direction: str | None
    setup: str | None
    spot_price: float
    vwap_source: str
    vwap_comparison_value: float
    entry: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    risk_reward: float | None
    confidence: str
    confidence_breakdown: dict[str, Any]
    reasons: str
    completed_candle_timestamp: str
    signal_key: str | None
    event_key: str
    data_freshness: str | None
    spot_futures_alignment: str
    acknowledged: bool = False


@dataclass
class LifecycleState:
    records: list[dict[str, Any]]
    events: list[dict[str, Any]]
    evaluation_keys: list[str]
    event_keys: list[str]


def empty_state() -> LifecycleState:
    return LifecycleState(records=[], events=[], evaluation_keys=[], event_keys=[])


def backup_corrupt_state(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_suffix(f"{path.suffix}.corrupt-{timestamp}.bak")
    shutil.copy2(path, backup)
    return backup


def load_lifecycle_state(path: Path | str = DEFAULT_LIFECYCLE_PATH) -> LifecycleState:
    state_path = Path(path)
    if not state_path.exists() or state_path.stat().st_size == 0:
        return empty_state()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup_corrupt_state(state_path)
        save_lifecycle_state(empty_state(), state_path)
        return empty_state()
    return LifecycleState(
        records=list(payload.get("records", [])),
        events=list(payload.get("events", [])),
        evaluation_keys=list(payload.get("evaluation_keys", [])),
        event_keys=list(payload.get("event_keys", [])),
    )


def save_lifecycle_state(state: LifecycleState, path: Path | str = DEFAULT_LIFECYCLE_PATH) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def evaluation_key(data_mode: DataMode, session_date: date, instrument: str, completed_candle_timestamp: datetime) -> str:
    return f"EVAL|{data_mode.value}|{session_date.isoformat()}|{instrument}|{completed_candle_timestamp.isoformat()}"


def record_key(data_mode: DataMode, session_date: date, instrument: str) -> str:
    return f"{data_mode.value}|{session_date.isoformat()}|{instrument}"


def event_key(session_date: date, instrument: str, signal_key: str | None, event_type: AlertEventType, candle_timestamp: datetime) -> str:
    stable_signal_key = signal_key or "NO_SIGNAL_KEY"
    return f"EVENT|{session_date.isoformat()}|{instrument}|{stable_signal_key}|{event_type.value}|{candle_timestamp.isoformat()}"


def lifecycle_record_from_signal(
    signal: Signal,
    data_mode: DataMode,
    confidence_record: dict[str, Any],
    previous_status: str | None = None,
    candles_in_prepare: int = 0,
) -> LifecycleRecord:
    session_date = signal.alert_timestamp.date()
    return LifecycleRecord(
        record_key=record_key(data_mode, session_date, signal.instrument),
        data_mode=data_mode.value,
        session_date=session_date.isoformat(),
        instrument=signal.instrument,
        current_status=signal.signal_status.value,
        previous_status=previous_status,
        first_detected_time=signal.alert_timestamp.isoformat(),
        status_changed_time=signal.alert_timestamp.isoformat(),
        last_evaluated_time=signal.alert_timestamp.isoformat(),
        signal_direction=signal.signal_direction.value if signal.signal_direction else None,
        setup=signal.setup_name.value if signal.setup_name else None,
        trigger_candle_timestamp=signal.alert_timestamp.isoformat(),
        entry=signal.entry_trigger,
        stop_loss=signal.stop_loss,
        target_1=signal.target_1,
        target_2=signal.target_2,
        confidence=signal.confidence_level,
        reasons=signal.reason,
        signal_key=SignalEvaluator.alert_key(signal),
        candles_in_prepare=candles_in_prepare,
        confidence_breakdown=confidence_record,
    )


def _status(value: str | None) -> SignalStatus | None:
    if not value:
        return None
    return SignalStatus(value)


def _direction(value: str | None) -> Direction | None:
    if not value:
        return None
    return Direction(value)


def classify_event(previous: LifecycleRecord | None, new_record: LifecycleRecord) -> AlertEventType | None:
    previous_status = _status(previous.current_status) if previous else None
    new_status = _status(new_record.current_status)
    previous_direction = _direction(previous.signal_direction) if previous else None
    new_direction = _direction(new_record.signal_direction)

    if previous and previous_direction and new_direction and previous_direction != new_direction and new_status in ACTIONABLE:
        return AlertEventType.DIRECTION_CHANGED
    if new_status == SignalStatus.PREPARE and previous_status in {None, SignalStatus.NO_TRADE, SignalStatus.WAIT}:
        return AlertEventType.PREPARE_NEW
    if new_status == SignalStatus.READY and previous_status == SignalStatus.PREPARE:
        return AlertEventType.READY_UPGRADE
    if new_status == SignalStatus.READY and previous_status in {None, SignalStatus.NO_TRADE, SignalStatus.WAIT}:
        return AlertEventType.READY_DIRECT
    if new_status == SignalStatus.INVALIDATED and previous_status == SignalStatus.READY:
        return AlertEventType.SIGNAL_INVALIDATED
    if new_status == SignalStatus.EXPIRED and previous_status in {SignalStatus.PREPARE, SignalStatus.READY}:
        return AlertEventType.SIGNAL_EXPIRED
    return None


def should_invalidate_prepare(previous: LifecycleRecord | None, signal: Signal) -> bool:
    if not previous or previous.current_status != SignalStatus.PREPARE.value:
        return False
    if signal.signal_status in ACTIONABLE:
        return False
    text = signal.reason.lower()
    return any(keyword in text for keyword in ["conflict", "stale", "misaligned", "unavailable", "risk-reward", "excessively"])


def apply_prepare_expiry(previous: LifecycleRecord | None, signal: Signal, config: LifecycleConfig) -> tuple[SignalStatus, int, str]:
    if previous and previous.current_status == SignalStatus.PREPARE.value and signal.signal_status == SignalStatus.PREPARE:
        candles = int(previous.candles_in_prepare) + 1
        if candles > config.prepare_expiry_candles:
            return SignalStatus.EXPIRED, candles, f"{signal.reason} PREPARE expired after {config.prepare_expiry_candles} completed candles."
        return SignalStatus.PREPARE, candles, signal.reason
    if should_invalidate_prepare(previous, signal):
        return SignalStatus.INVALIDATED, int(previous.candles_in_prepare), f"{signal.reason} PREPARE invalidated by failed structure or safety condition."
    return signal.signal_status, 1 if signal.signal_status == SignalStatus.PREPARE else 0, signal.reason


def signal_with_status(signal: Signal, status: SignalStatus, reason: str) -> Signal:
    if signal.signal_status == status and signal.reason == reason:
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
        signal_status=status,
        invalidation_condition=signal.invalidation_condition,
        suggested_option_side=signal.suggested_option_side,
        alert_timestamp=signal.alert_timestamp,
        reason=reason,
    )


def build_event(
    event_type_: AlertEventType,
    previous: LifecycleRecord | None,
    record: LifecycleRecord,
    signal: Signal,
    live_item: LiveInstrumentSnapshot | None,
    data_freshness: FreshnessState | None,
) -> AlertEvent:
    candle_timestamp = signal.alert_timestamp
    session_date = candle_timestamp.date()
    key = event_key(session_date, signal.instrument, record.signal_key, event_type_, candle_timestamp)
    return AlertEvent(
        event_timestamp=datetime.now(tz=candle_timestamp.tzinfo).isoformat(),
        instrument=signal.instrument,
        event_type=event_type_.value,
        previous_status=previous.current_status if previous else None,
        new_status=record.current_status,
        direction=record.signal_direction,
        setup=record.setup,
        spot_price=signal.spot_price,
        vwap_source=live_item.vwap_source if live_item else "SAMPLE_DATA",
        vwap_comparison_value=signal.vwap_value,
        entry=signal.entry_trigger,
        stop_loss=signal.stop_loss,
        target_1=signal.target_1,
        target_2=signal.target_2,
        risk_reward=signal.risk_reward_ratio,
        confidence=record.confidence,
        confidence_breakdown=record.confidence_breakdown or {},
        reasons=record.reasons,
        completed_candle_timestamp=candle_timestamp.isoformat(),
        signal_key=record.signal_key,
        event_key=key,
        data_freshness=data_freshness.value if data_freshness else None,
        spot_futures_alignment=live_item.metrics.candle_alignment_status if live_item else "SAMPLE",
    )


def process_signal_evaluation(
    state: LifecycleState,
    signal: Signal,
    data_mode: DataMode,
    live_item: LiveInstrumentSnapshot | None = None,
    data_freshness: FreshnessState | None = None,
    config: LifecycleConfig | None = None,
) -> tuple[LifecycleRecord, AlertEvent | None, bool]:
    config = config or LifecycleConfig()
    eval_key = evaluation_key(data_mode, signal.alert_timestamp.date(), signal.instrument, signal.alert_timestamp)
    existing_record_map = {record["record_key"]: LifecycleRecord(**record) for record in state.records}
    current_record_key = record_key(data_mode, signal.alert_timestamp.date(), signal.instrument)
    previous = existing_record_map.get(current_record_key)

    if eval_key in state.evaluation_keys:
        if previous is None:
            previous = lifecycle_record_from_signal(signal, data_mode, {"level": signal.confidence_level, "score": 0, "components": {}})
        return previous, None, False

    status, candles_in_prepare, reason = apply_prepare_expiry(previous, signal, config)
    effective_signal = signal_with_status(signal, status, reason)
    breakdown_obj = confidence_breakdown(effective_signal, live_item.context, live_item) if live_item else None
    confidence_record = confidence_to_record(breakdown_obj) if breakdown_obj else {"level": effective_signal.confidence_level, "score": 0, "components": {}}
    new_record = lifecycle_record_from_signal(
        effective_signal,
        data_mode,
        confidence_record,
        previous_status=previous.current_status if previous else None,
        candles_in_prepare=candles_in_prepare,
    )
    if previous:
        new_record.first_detected_time = previous.first_detected_time
        if previous.current_status == new_record.current_status:
            new_record.status_changed_time = previous.status_changed_time
        new_record.latest_event_type = previous.latest_event_type
    new_record.last_evaluation_key = eval_key

    event = None
    event_type_ = classify_event(previous, new_record)
    if event_type_:
        event = build_event(event_type_, previous, new_record, effective_signal, live_item, data_freshness)
        if event.event_key in state.event_keys:
            event = None
        else:
            new_record.latest_event_type = event_type_.value
            state.events.append(asdict(event))
            state.event_keys.append(event.event_key)

    existing_record_map[current_record_key] = new_record
    state.records = [asdict(record) for record in existing_record_map.values()]
    state.evaluation_keys.append(eval_key)
    return new_record, event, True


def process_live_snapshot(
    state: LifecycleState,
    items: tuple[LiveInstrumentSnapshot, ...],
    data_freshness: FreshnessState | None,
    config: LifecycleConfig | None = None,
) -> tuple[list[LifecycleRecord], list[AlertEvent]]:
    records = []
    events = []
    for item in items:
        record, event, _processed = process_signal_evaluation(state, item.signal, DataMode.LIVE, item, data_freshness, config)
        records.append(record)
        if event:
            events.append(event)
    return records, events


def acknowledge_event(state: LifecycleState, event_key_: str) -> None:
    for event in state.events:
        if event.get("event_key") == event_key_:
            event["acknowledged"] = True


def expire_prior_session_actionable(state: LifecycleState, data_mode: DataMode, new_session_date: date, timestamp: datetime) -> list[AlertEvent]:
    generated: list[AlertEvent] = []
    updated_records: list[dict[str, Any]] = []
    for payload in state.records:
        record = LifecycleRecord(**payload)
        if (
            record.data_mode == data_mode.value
            and record.session_date < new_session_date.isoformat()
            and record.current_status in {SignalStatus.PREPARE.value, SignalStatus.READY.value}
        ):
            previous = LifecycleRecord(**asdict(record))
            record.previous_status = record.current_status
            record.current_status = SignalStatus.EXPIRED.value
            record.status_changed_time = timestamp.isoformat()
            record.last_evaluated_time = timestamp.isoformat()
            record.reasons = f"{record.reasons} Session expired at rollover."
            event_type_ = AlertEventType.SIGNAL_EXPIRED
            key = event_key(new_session_date, record.instrument, record.signal_key, event_type_, timestamp)
            if key not in state.event_keys:
                event = AlertEvent(
                    event_timestamp=timestamp.isoformat(),
                    instrument=record.instrument,
                    event_type=event_type_.value,
                    previous_status=previous.current_status,
                    new_status=record.current_status,
                    direction=record.signal_direction,
                    setup=record.setup,
                    spot_price=0,
                    vwap_source="ROLLOVER",
                    vwap_comparison_value=0,
                    entry=record.entry,
                    stop_loss=record.stop_loss,
                    target_1=record.target_1,
                    target_2=record.target_2,
                    risk_reward=None,
                    confidence=record.confidence,
                    confidence_breakdown=record.confidence_breakdown or {},
                    reasons=record.reasons,
                    completed_candle_timestamp=timestamp.isoformat(),
                    signal_key=record.signal_key,
                    event_key=key,
                    data_freshness=None,
                    spot_futures_alignment="ROLLOVER",
                )
                state.events.append(asdict(event))
                state.event_keys.append(key)
                record.latest_event_type = event_type_.value
                generated.append(event)
        updated_records.append(asdict(record))
    state.records = updated_records
    return generated
