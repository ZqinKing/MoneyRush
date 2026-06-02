from __future__ import annotations

from datetime import date, timedelta
import logging

import requests


logger = logging.getLogger(__name__)


class FredMacroClientError(RuntimeError):
    def __init__(self, reason: str, *, series_id: str, status_code: int | None = None) -> None:
        self.reason = reason
        self.series_id = series_id
        self.status_code = status_code
        message = f"{reason}: series={series_id}"
        if status_code is not None:
            message = f"{message} status={status_code}"
        super().__init__(message)


class FredMacroClient:
    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, settings) -> None:
        self._api_key = settings.fred_api_key
        self._timeout = max(float(settings.macro_fred_request_timeout_seconds), 1.0)

    def fetch_series(self, series_id: str, *, lookback_days: int) -> list[dict[str, object]]:
        if not self._api_key:
            raise RuntimeError("FRED API key is not configured")
        end_date = date.today()
        start_date = end_date - timedelta(days=max(lookback_days, 1))
        try:
            response = requests.get(
                self.BASE_URL,
                params={
                    "series_id": series_id,
                    "api_key": self._api_key,
                    "file_type": "json",
                    "observation_start": start_date.isoformat(),
                    "observation_end": end_date.isoformat(),
                    "sort_order": "desc",
                    "limit": max(lookback_days, 1),
                },
                timeout=self._timeout,
            )
        except requests.Timeout as exc:
            raise FredMacroClientError("fred_timeout", series_id=series_id) from exc
        except requests.RequestException as exc:
            raise FredMacroClientError("fred_request_error", series_id=series_id) from exc

        if response.status_code >= 400:
            raise FredMacroClientError("fred_http_error", series_id=series_id, status_code=response.status_code)

        try:
            payload = response.json()
        except ValueError as exc:
            raise FredMacroClientError("fred_parse_error", series_id=series_id, status_code=response.status_code) from exc
        observations = payload.get("observations") if isinstance(payload, dict) else None
        if not isinstance(observations, list):
            return []
        rows: list[dict[str, object]] = []
        for item in observations:
            if not isinstance(item, dict) or not item.get("date"):
                continue
            try:
                observation_date = date.fromisoformat(str(item["date"]))
                value = self._parse_value(item.get("value"))
            except ValueError as exc:
                raise FredMacroClientError("fred_parse_error", series_id=series_id, status_code=response.status_code) from exc
            rows.append(
                {
                    "series_id": series_id,
                    "observation_date": observation_date,
                    "value": value,
                    "source": "fred",
                    "raw_payload": item,
                }
            )
        return rows

    @staticmethod
    def _parse_value(value: object) -> float | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text == ".":
            return None
        return float(text)
