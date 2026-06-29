from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, time
from enum import Enum
from pathlib import Path
from statistics import median
from typing import Any

from .rules import Candle, Direction, Signal, SignalEvaluator, SignalStatus
from .scanner_state import DataMode


DEFAULT_OUTCOME_PATH = Path("data") / "signal_outcomes.json"


class ExecutionState(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    ENTRY_TRIGGERED = "ENTRY_TRIGGERED"
    TARGET_1_HIT = "TARGET_1_HIT"
    TARGET_2_HIT = "TARGET_2_HIT"
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    INVALIDATED_BEFORE_ENTRY = "INVALIDATED_BEFORE_ENTRY"
    EXPIRED_BEFORE_ENTRY = "EXPIRED_BEFORE_ENTRY"
    SESSION_CLOSED = "SESSION_CLOSED"
    CLOSED_MANUALLY = "CLOSED_MANUALLY"
    AMBIGUOUS = "AMBIGUOUS"


FINAL_STATES = {
    ExecutionState.TARGET_2_HIT,
    ExecutionState.STOP_LOSS_HIT,
    ExecutionState.INVALIDATED_BEFORE_ENTRY,
    ExecutionState.EXPIRED_BEFORE_ENTRY,
    ExecutionState.SESSION_CLOSED,
    ExecutionState.CLOSED_MANUALLY,
    ExecutionState.AMBIGUOUS,
}


@dataclass(frozen=True)
class OutcomeConfig:
    validity_candles: int = 3
    new_entry_cutoff: time = time(14, 45)
    session_close: time = time(15, 30)
    conservative_ambiguity: bool = True
    time_buckets: tuple[tuple[str, time, time], ...] = (
        ("09:25-10:30", time(9, 25), time(10, 30)),
        ("10:30-12:00", time(10, 30), time(12, 0)),
        ("12:00-13:30", time(12, 0), time(13, 30)),
        ("13:30-14:45", time(13, 30), time(14, 45)),
    )


@dataclass
class ExecutionRecord:
    execution_key: str
    data_mode: str
    session_date: str
    instrument: str
    setup: str | None
    direction: str | None
    confidence: str
    signal_key: str | None
    signal_time: str
    ready_time: str
    planned_entry: float | None
    actual_entry: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    execution_state: str
    current_outcome: str | None
    final_outcome: str | None
    entry_trigger_timestamp: str | None = None
    entry_trigger_candle: str | None = None
    actual_trigger_price: float | None = None
    slippage_points: float | None = None
    signal_to_entry_delay_minutes: float | None = None
    signal_to_entry_delay_candles: int | None = None
    highest_price_after_entry: float | None = None
    lowest_price_after_entry: float | None = None
    current_unrealised_points: float | None = None
    mfe_points: float = 0.0
    mae_points: float = 0.0
    mfe_r: float | None = None
    mae_r: float | None = None
    realised_r: float | None = None
    exit_timestamp: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    ambiguity_reason: str | None = None
    last_processed_candle: str | None = None
    processed_candles: list[str] | None = None


@dataclass
class OutcomeState:
    records: list[dict[str, Any]]
    event_keys: list[str]
    events: list[dict[str, Any]]


def empty_outcome_state() -> OutcomeState:
    return OutcomeState(records=[], event_keys=[], events=[])


def backup_corrupt_outcomes(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_suffix(f"{path.suffix}.corrupt-{timestamp}.bak")
    shutil.copy2(path, backup)
    return backup


def load_outcome_state(path: Path | str = DEFAULT_OUTCOME_PATH) -> OutcomeState:
    outcome_path = Path(path)
    if not outcome_path.exists() or outcome_path.stat().st_size == 0:
        return empty_outcome_state()
    try:
        payload = json.loads(outcome_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup_corrupt_outcomes(outcome_path)
        save_outcome_state(empty_outcome_state(), outcome_path)
        return empty_outcome_state()
    return OutcomeState(
        records=list(payload.get("records", [])),
        event_keys=list(payload.get("event_keys", [])),
        events=list(payload.get("events", [])),
    )


def save_outcome_state(state: OutcomeState, path: Path | str = DEFAULT_OUTCOME_PATH) -> None:
    outcome_path = Path(path)
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    outcome_path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def execution_key(data_mode: DataMode, session_date: datetime, instrument: str, signal_key: str | None) -> str:
    return f"EXEC|{data_mode.value}|{session_date.date().isoformat()}|{instrument}|{signal_key or 'NO_SIGNAL_KEY'}"


def outcome_event_key(record: ExecutionRecord, event_type: str, candle_timestamp: datetime) -> str:
    return f"EXEC_EVENT|{record.session_date}|{record.instrument}|{record.execution_key}|{event_type}|{candle_timestamp.isoformat()}"


def risk_points(record: ExecutionRecord) -> float | None:
    if record.planned_entry is None or record.stop_loss is None:
        return None
    risk = abs(record.planned_entry - record.stop_loss)
    return risk if risk > 0 else None


def create_execution_from_ready(signal: Signal, data_mode: DataMode) -> ExecutionRecord:
    if signal.signal_status != SignalStatus.READY:
        raise ValueError("Only READY signals create paper execution records.")
    key = execution_key(data_mode, signal.alert_timestamp, signal.instrument, SignalEvaluator.alert_key(signal))
    return ExecutionRecord(
        execution_key=key,
        data_mode=data_mode.value,
        session_date=signal.alert_timestamp.date().isoformat(),
        instrument=signal.instrument,
        setup=signal.setup_name.value if signal.setup_name else None,
        direction=signal.signal_direction.value if signal.signal_direction else None,
        confidence=signal.confidence_level,
        signal_key=SignalEvaluator.alert_key(signal),
        signal_time=signal.alert_timestamp.isoformat(),
        ready_time=signal.alert_timestamp.isoformat(),
        planned_entry=signal.entry_trigger,
        actual_entry=None,
        stop_loss=signal.stop_loss,
        target_1=signal.target_1,
        target_2=signal.target_2,
        execution_state=ExecutionState.PENDING_ENTRY.value,
        current_outcome=None,
        final_outcome=None,
        processed_candles=[],
    )


def ensure_execution_record(state: OutcomeState, signal: Signal, data_mode: DataMode) -> tuple[ExecutionRecord, bool]:
    record = create_execution_from_ready(signal, data_mode)
    existing = {item["execution_key"]: ExecutionRecord(**item) for item in state.records}
    if record.execution_key in existing:
        return existing[record.execution_key], False
    existing[record.execution_key] = record
    state.records = [asdict(item) for item in existing.values()]
    return record, True


def direction(record: ExecutionRecord) -> Direction | None:
    return Direction(record.direction) if record.direction else None


def entry_reached(record: ExecutionRecord, candle: Candle) -> bool:
    if record.planned_entry is None:
        return False
    if direction(record) == Direction.BULLISH:
        return candle.high >= record.planned_entry
    if direction(record) == Direction.BEARISH:
        return candle.low <= record.planned_entry
    return False


def invalidation_reached(record: ExecutionRecord, candle: Candle) -> bool:
    if record.stop_loss is None:
        return False
    if direction(record) == Direction.BULLISH:
        return candle.low <= record.stop_loss
    if direction(record) == Direction.BEARISH:
        return candle.high >= record.stop_loss
    return False


def target_hit(record: ExecutionRecord, candle: Candle, target: float | None) -> bool:
    if target is None:
        return False
    if direction(record) == Direction.BULLISH:
        return candle.high >= target
    if direction(record) == Direction.BEARISH:
        return candle.low <= target
    return False


def stop_hit(record: ExecutionRecord, candle: Candle) -> bool:
    return invalidation_reached(record, candle)


def candles_elapsed(record: ExecutionRecord, candle: Candle) -> int:
    ready_time = datetime.fromisoformat(record.ready_time)
    return max(0, int((candle.timestamp - ready_time).total_seconds() // 300))


def set_final(record: ExecutionRecord, state: ExecutionState, candle: Candle, price: float | None, reason: str) -> None:
    record.execution_state = state.value
    record.current_outcome = state.value
    record.final_outcome = state.value
    record.exit_timestamp = candle.timestamp.isoformat()
    record.exit_price = price
    record.exit_reason = reason


def update_mfe_mae(record: ExecutionRecord, candle: Candle) -> None:
    if record.actual_entry is None:
        return
    record.highest_price_after_entry = candle.high if record.highest_price_after_entry is None else max(record.highest_price_after_entry, candle.high)
    record.lowest_price_after_entry = candle.low if record.lowest_price_after_entry is None else min(record.lowest_price_after_entry, candle.low)
    if direction(record) == Direction.BULLISH:
        record.mfe_points = max(0.0, (record.highest_price_after_entry or record.actual_entry) - record.actual_entry)
        record.mae_points = max(0.0, record.actual_entry - (record.lowest_price_after_entry or record.actual_entry))
        record.current_unrealised_points = candle.close - record.actual_entry
    elif direction(record) == Direction.BEARISH:
        record.mfe_points = max(0.0, record.actual_entry - (record.lowest_price_after_entry or record.actual_entry))
        record.mae_points = max(0.0, (record.highest_price_after_entry or record.actual_entry) - record.actual_entry)
        record.current_unrealised_points = record.actual_entry - candle.close
    risk = risk_points(record)
    if risk:
        record.mfe_r = record.mfe_points / risk
        record.mae_r = record.mae_points / risk


def realised_r(record: ExecutionRecord, exit_price: float | None) -> float | None:
    if exit_price is None or record.actual_entry is None:
        return None
    risk = risk_points(record)
    if not risk:
        return None
    if direction(record) == Direction.BULLISH:
        return (exit_price - record.actual_entry) / risk
    if direction(record) == Direction.BEARISH:
        return (record.actual_entry - exit_price) / risk
    return None


def append_execution_event(state: OutcomeState, record: ExecutionRecord, event_type: str, candle: Candle) -> None:
    key = outcome_event_key(record, event_type, candle.timestamp)
    if key in state.event_keys:
        return
    state.event_keys.append(key)
    state.events.append(
        {
            "event_key": key,
            "event_timestamp": candle.timestamp.isoformat(),
            "instrument": record.instrument,
            "event_type": event_type,
            "execution_state": record.execution_state,
            "setup": record.setup,
            "direction": record.direction,
            "confidence": record.confidence,
            "execution_key": record.execution_key,
            "exit_reason": record.exit_reason,
            "acknowledged": False,
        }
    )


def update_execution_with_candle(
    record: ExecutionRecord,
    candle: Candle,
    state: OutcomeState | None = None,
    data_safe: bool = True,
    config: OutcomeConfig | None = None,
) -> bool:
    config = config or OutcomeConfig()
    if not data_safe or record.execution_state in {state.value for state in FINAL_STATES}:
        return False
    processed = record.processed_candles or []
    candle_key = candle.timestamp.isoformat()
    if candle_key in processed:
        return False
    processed.append(candle_key)
    record.processed_candles = processed
    record.last_processed_candle = candle_key

    if record.execution_state == ExecutionState.PENDING_ENTRY.value:
        if candle.timestamp.time() >= config.session_close:
            set_final(record, ExecutionState.SESSION_CLOSED, candle, candle.close, "Session closed before entry.")
        elif candle.timestamp.time() > config.new_entry_cutoff:
            set_final(record, ExecutionState.EXPIRED_BEFORE_ENTRY, candle, candle.close, "New-entry cutoff crossed before entry.")
        elif candles_elapsed(record, candle) > config.validity_candles:
            set_final(record, ExecutionState.EXPIRED_BEFORE_ENTRY, candle, candle.close, "Validity period ended before entry.")
        elif invalidation_reached(record, candle):
            set_final(record, ExecutionState.INVALIDATED_BEFORE_ENTRY, candle, record.stop_loss, "Invalidation level reached before entry.")
        elif entry_reached(record, candle):
            planned = record.planned_entry or candle.close
            actual = planned
            record.actual_entry = actual
            record.actual_trigger_price = actual
            record.entry_trigger_timestamp = candle.timestamp.isoformat()
            record.entry_trigger_candle = candle.timestamp.isoformat()
            record.slippage_points = actual - planned
            record.signal_to_entry_delay_minutes = (candle.timestamp - datetime.fromisoformat(record.ready_time)).total_seconds() / 60
            record.signal_to_entry_delay_candles = candles_elapsed(record, candle)
            record.execution_state = ExecutionState.ENTRY_TRIGGERED.value
            record.current_outcome = ExecutionState.ENTRY_TRIGGERED.value
            update_mfe_mae(record, candle)
        if state and record.execution_state != ExecutionState.PENDING_ENTRY.value:
            append_execution_event(state, record, record.execution_state, candle)
            if record.final_outcome:
                record.realised_r = realised_r(record, record.exit_price)
        return True

    update_mfe_mae(record, candle)
    hit_stop = stop_hit(record, candle)
    hit_t1 = target_hit(record, candle, record.target_1)
    hit_t2 = target_hit(record, candle, record.target_2)
    if hit_stop and (hit_t1 or hit_t2):
        set_final(record, ExecutionState.AMBIGUOUS, candle, candle.close, "Target and stop touched in the same completed candle.")
        record.ambiguity_reason = record.exit_reason
    elif hit_stop:
        set_final(record, ExecutionState.STOP_LOSS_HIT, candle, record.stop_loss, "Stop loss hit.")
    elif hit_t2:
        set_final(record, ExecutionState.TARGET_2_HIT, candle, record.target_2, "Target 2 hit.")
    elif hit_t1:
        record.execution_state = ExecutionState.TARGET_1_HIT.value
        record.current_outcome = ExecutionState.TARGET_1_HIT.value
    if record.final_outcome:
        record.realised_r = realised_r(record, record.exit_price)
    if state and record.execution_state in {item.value for item in ExecutionState}:
        append_execution_event(state, record, record.execution_state, candle)
    return True


def upsert_execution_record(state: OutcomeState, record: ExecutionRecord) -> None:
    records = {item["execution_key"]: ExecutionRecord(**item) for item in state.records}
    records[record.execution_key] = record
    state.records = [asdict(item) for item in records.values()]


def process_ready_signals(state: OutcomeState, signals: list[Signal], data_mode: DataMode) -> None:
    for signal in signals:
        if signal.signal_status == SignalStatus.READY:
            ensure_execution_record(state, signal, data_mode)


def process_outcomes_for_candles(
    state: OutcomeState,
    candles_by_instrument: dict[str, Candle],
    data_safe: bool,
    config: OutcomeConfig | None = None,
) -> None:
    updated = []
    for payload in state.records:
        record = ExecutionRecord(**payload)
        candle = candles_by_instrument.get(record.instrument)
        if candle and update_execution_with_candle(record, candle, state, data_safe, config):
            updated.append(record)
        else:
            updated.append(record)
    state.records = [asdict(record) for record in updated]


def time_bucket(timestamp: datetime, config: OutcomeConfig | None = None) -> str:
    config = config or OutcomeConfig()
    current = timestamp.time()
    for label, start, end in config.time_buckets:
        if start <= current < end:
            return label
    return "OUTSIDE_BUCKETS"


def performance_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    total_ready = len(records)
    entered = [record for record in records if record.get("actual_entry") is not None]
    target_1 = [record for record in records if record.get("execution_state") in {ExecutionState.TARGET_1_HIT.value, ExecutionState.TARGET_2_HIT.value}]
    target_2 = [record for record in records if record.get("execution_state") == ExecutionState.TARGET_2_HIT.value]
    stops = [record for record in records if record.get("execution_state") == ExecutionState.STOP_LOSS_HIT.value]
    invalidated = [record for record in records if record.get("execution_state") == ExecutionState.INVALIDATED_BEFORE_ENTRY.value]
    expired = [record for record in records if record.get("execution_state") == ExecutionState.EXPIRED_BEFORE_ENTRY.value]
    ambiguous = [record for record in records if record.get("execution_state") == ExecutionState.AMBIGUOUS.value]
    realised = [record.get("realised_r") for record in records if record.get("realised_r") is not None and record.get("execution_state") != ExecutionState.AMBIGUOUS.value]
    mfe = [record.get("mfe_r") for record in records if record.get("mfe_r") is not None]
    mae = [record.get("mae_r") for record in records if record.get("mae_r") is not None]

    def rate(count: int) -> float:
        return 0.0 if total_ready == 0 else count / total_ready

    return {
        "total_ready_signals": total_ready,
        "entry_trigger_rate": rate(len(entered)),
        "target_1_hit_rate": rate(len(target_1)),
        "target_2_hit_rate": rate(len(target_2)),
        "stop_loss_rate": rate(len(stops)),
        "invalidated_before_entry_rate": rate(len(invalidated)),
        "expired_before_entry_rate": rate(len(expired)),
        "average_realised_r": None if not realised else sum(realised) / len(realised),
        "median_realised_r": None if not realised else median(realised),
        "average_mfe_r": None if not mfe else sum(mfe) / len(mfe),
        "average_mae_r": None if not mae else sum(mae) / len(mae),
        "ambiguous_outcome_count": len(ambiguous),
    }
