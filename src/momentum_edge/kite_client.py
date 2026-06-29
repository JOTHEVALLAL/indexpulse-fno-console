from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .candle_utils import ensure_ist
from .config import load_runtime_config
from .rules import Candle


class KiteAuthenticationError(RuntimeError):
    pass


class KiteDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class KiteCredentials:
    api_key: str
    access_token: str

    @staticmethod
    def from_environment() -> "KiteCredentials":
        config = load_runtime_config()
        api_key = (config.secrets.get("KITE_API_KEY") or "").strip()
        access_token = (config.secrets.get("KITE_ACCESS_TOKEN") or "").strip()
        if not api_key or not access_token:
            raise KiteAuthenticationError("KITE_API_KEY and KITE_ACCESS_TOKEN are required for LIVE mode.")
        return KiteCredentials(api_key=api_key, access_token=access_token)


class KiteClient:
    def __init__(self, credentials: KiteCredentials | None = None, timeout_seconds: int = 8, retries: int = 2) -> None:
        self.credentials = credentials or KiteCredentials.from_environment()
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self._kite = self._build_client()

    def _build_client(self) -> Any:
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise KiteAuthenticationError("kiteconnect is not installed. Install requirements for LIVE mode.") from exc

        kite = KiteConnect(api_key=self.credentials.api_key, timeout=self.timeout_seconds)
        kite.set_access_token(self.credentials.access_token)
        return kite

    def _with_retries(self, operation: str, callback: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return callback()
            except Exception as exc:  # Kite raises several concrete exceptions depending on version.
                last_error = exc
                message = str(exc).lower()
                if "token" in message or "session" in message or "permission" in message:
                    raise KiteAuthenticationError(f"{operation} failed due to authentication: {exc}") from exc
                if attempt < self.retries:
                    time.sleep(0.25 * (attempt + 1))
        raise KiteDataError(f"{operation} failed after retries: {last_error}") from last_error

    def instruments(self, exchange: str = "NSE") -> list[dict]:
        return self._with_retries("instrument lookup", lambda: self._kite.instruments(exchange))

    def quote(self, exchange: str, tradingsymbol: str) -> dict:
        key = f"{exchange}:{tradingsymbol}"
        payload = self._with_retries("quote", lambda: self._kite.quote([key]))
        if key not in payload:
            raise KiteDataError(f"Quote response missing {key}.")
        return payload[key]

    def quotes(self, keys: list[str]) -> dict:
        payload = self._with_retries("quotes", lambda: self._kite.quote(keys))
        return {key: payload[key] for key in keys if key in payload}

    def historical_candles(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
    ) -> list[Candle]:
        raw = self._with_retries(
            f"historical candles {interval}",
            lambda: self._kite.historical_data(instrument_token, from_date, to_date, interval),
        )
        if not raw:
            raise KiteDataError(f"No candles returned for interval {interval}.")
        return [
            Candle(
                timestamp=ensure_ist(item["date"]),
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=float(item.get("volume", 0) or 0),
            )
            for item in raw
        ]
