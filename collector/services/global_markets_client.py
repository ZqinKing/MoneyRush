from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import unescape
import re
from math import isfinite
from collections.abc import Mapping
from typing import cast
from urllib.parse import quote

import requests

from collector.services.global_markets_contract import GLOBAL_MARKET_BY_ID, GLOBAL_MARKET_INDICES, GLOBAL_MARKET_REGIONS


STALE_AFTER = timedelta(minutes=30)
EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_QUOTE_PAGE_URL = "https://finance.yahoo.com/quote/{symbol}/"
YAHOO_QUOTE_PAGE_SOURCE_PREFIX = "yahoo-finance-quote-page"
MOEX_ISS_INDEX_URL = "https://iss.moex.com/iss/engines/stock/markets/index/boards/SNDX/securities/IMOEX.json"
YAHOO_QUOTE_PAGE_MARKET_NAMES: dict[str, tuple[str, ...]] = {
    "nasdaq_composite": ("NASDAQ Composite",),
    "nikkei_225": ("Nikkei 225", "Nikkei 225 Index"),
    "kospi": ("KOSPI Composite Index", "KOSPI", "Korea Composite Stock Price Index"),
    "ftse_100": ("FTSE 100",),
    "dax": ("DAX", "DAX P"),
    "cac40": ("CAC 40",),
    "moex_russia": ("MOEX Russia Index", "MOEX Russia", "IMOEX"),
    "sensex": ("S&P BSE SENSEX", "BSE SENSEX", "SENSEX"),
    "ibovespa": ("IBOVESPA",),
}
YAHOO_QUOTE_PAGE_PRICE_BOUNDS: dict[str, tuple[float, float]] = {
    "nikkei_225": (10_000, 80_000),
    "kospi": (1_000, 20_000),
    "ftse_100": (1_000, 20_000),
    "dax": (1_000, 50_000),
    "cac40": (1_000, 20_000),
    "moex_russia": (500, 10_000),
    "sensex": (10_000, 200_000),
    "ibovespa": (10_000, 300_000),
}
YAHOO_QUOTE_PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True, slots=True)
class GlobalMarketSnapshot:
    id: str
    symbol: str
    provider_symbol: str
    name: str
    region: str
    country: str
    exchange: str
    longitude: float
    latitude: float
    price: float | None
    change: float | None
    change_percent: float | None
    currency: str
    market_status: str | None
    source: str
    delay_label: str | None
    updated_at: str
    stale: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "providerSymbol": self.provider_symbol,
            "name": self.name,
            "region": self.region,
            "country": self.country,
            "exchange": self.exchange,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "price": self.price,
            "change": self.change,
            "changePercent": self.change_percent,
            "currency": self.currency,
            "marketStatus": self.market_status,
            "source": self.source,
            "delayLabel": self.delay_label,
            "updatedAt": self.updated_at,
            "stale": self.stale,
        }


class GlobalMarketsClient:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._timeout = settings.global_markets_request_timeout_seconds
        self._stale_after = timedelta(minutes=settings.global_markets_stale_after_minutes)

    def fetch(self, previous_payload: Mapping[str, object] | None = None) -> dict[str, object]:
        now = datetime.now(UTC)
        errors: list[dict[str, object]] = []
        eastmoney_snapshots: dict[str, GlobalMarketSnapshot | None] = {}
        fallback_snapshots: dict[str, GlobalMarketSnapshot | None] = {}

        eastmoney_payload = None
        try:
            eastmoney_payload = self._fetch_eastmoney_payload()
        except Exception as exc:
            errors.append(_error_entry("eastmoney", None, exc))

        if eastmoney_payload is not None:
            for metadata in GLOBAL_MARKET_INDICES:
                if metadata.eastmoney_symbol is None:
                    continue
                try:
                    eastmoney_snapshots[metadata.id] = parse_eastmoney_quote(metadata.id, eastmoney_payload, now=now)
                except Exception as exc:
                    eastmoney_snapshots[metadata.id] = None
                    errors.append(_error_entry("eastmoney", metadata.id, exc))

        missing_ids = [
            metadata.id
            for metadata in GLOBAL_MARKET_INDICES
            if metadata.eastmoney_symbol is None or eastmoney_snapshots.get(metadata.id) is None
        ]
        if missing_ids:
            try:
                fallback_payload = self._fetch_fallback_payload(missing_ids)
            except Exception as exc:
                fallback_payload = None
                errors.append(_error_entry("fallback", None, exc))
            if fallback_payload is not None:
                for market_id in missing_ids:
                    try:
                        fallback_snapshots[market_id] = parse_fallback_quote(market_id, fallback_payload, now=now)
                    except Exception as exc:
                        fallback_snapshots[market_id] = None
                        errors.append(_error_entry("fallback", market_id, exc))

            for market_id in missing_ids:
                if market_id == "moex_russia":
                    continue
                if market_id not in YAHOO_QUOTE_PAGE_MARKET_NAMES or fallback_snapshots.get(market_id) is not None:
                    continue
                try:
                    metadata = GLOBAL_MARKET_BY_ID[market_id]
                    if metadata.fallback_symbol is None:
                        continue
                    html = self._fetch_yahoo_quote_page(metadata.fallback_symbol)
                    snapshot = parse_yahoo_quote_page(market_id, html, now=now)
                    if snapshot is None:
                        raise ValueError(f"no usable {metadata.display_name} quote in Yahoo quote page")
                    fallback_snapshots[market_id] = snapshot
                except Exception as exc:
                    fallback_snapshots[market_id] = None
                    errors.append(_error_entry("yahoo-quote-page", market_id, exc))

            if "moex_russia" in missing_ids and fallback_snapshots.get("moex_russia") is None:
                try:
                    moex_payload = self._fetch_moex_iss_payload()
                    fallback_snapshots["moex_russia"] = parse_moex_iss_quote(moex_payload, now=now)
                    if fallback_snapshots["moex_russia"] is None:
                        raise ValueError("no usable MOEX ISS quote")
                except Exception as exc:
                    fallback_snapshots["moex_russia"] = None
                    errors.append(_error_entry("moex-iss", "moex_russia", exc))

        snapshots = merge_provider_snapshots(eastmoney_snapshots, fallback_snapshots)
        if len(snapshots) < len(GLOBAL_MARKET_INDICES):
            existing_ids = {snapshot.id for snapshot in snapshots}
            recovered = _snapshots_from_previous(previous_payload, existing_ids, now=now, stale_after=self._stale_after)
            snapshots.extend(recovered)
            existing_ids.update(snapshot.id for snapshot in recovered)
            unavailable = _unavailable_snapshots(existing_ids, now=now)
            snapshots.extend(unavailable)
            errors.extend(
                {"source": "unavailable", "marketId": snapshot.id, "message": "no provider quote available"}
                for snapshot in unavailable
            )

        snapshots.sort(key=lambda snapshot: GLOBAL_MARKET_BY_ID[snapshot.id].display_order)
        if not snapshots:
            raise RuntimeError("global markets providers returned no usable quotes")

        return _build_latest_payload(snapshots, now=now, errors=errors, stale_after=self._stale_after)

    def _fetch_eastmoney_payload(self) -> dict[str, object]:
        symbols = ",".join(metadata.eastmoney_symbol for metadata in GLOBAL_MARKET_INDICES if metadata.eastmoney_symbol)
        response = requests.get(
            EASTMONEY_QUOTE_URL,
            params={"fltt": "2", "invt": "2", "fields": "f12,f13,f2,f3,f4,f124", "secids": symbols},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return cast(dict[str, object], response.json())

    def _fetch_fallback_payload(self, market_ids: list[str]) -> dict[str, object]:
        symbols = ",".join(
            metadata.fallback_symbol
            for market_id in market_ids
            if (metadata := GLOBAL_MARKET_BY_ID[market_id]).fallback_symbol is not None
        )
        if not symbols:
            return {"quoteResponse": {"result": []}}
        response = requests.get(f"{YAHOO_QUOTE_URL}?symbols={quote(symbols, safe=',^.')}", timeout=self._timeout)
        response.raise_for_status()
        return cast(dict[str, object], response.json())

    def _fetch_yahoo_quote_page(self, provider_symbol: str) -> str:
        response = requests.get(
            YAHOO_QUOTE_PAGE_URL.format(symbol=quote(provider_symbol, safe="")),
            headers=YAHOO_QUOTE_PAGE_HEADERS,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.text

    def _fetch_moex_iss_payload(self) -> dict[str, object]:
        response = requests.get(
            MOEX_ISS_INDEX_URL,
            params={
                "iss.meta": "off",
                "iss.only": "securities,marketdata",
                "marketdata.columns": "SECID,CURRENTVALUE,LASTCHANGE,LASTCHANGEPRC,TIME,UPDATETIME",
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return cast(dict[str, object], response.json())


def parse_eastmoney_quote(
    market_id: str,
    payload: Mapping[str, object] | None,
    *,
    now: datetime | None = None,
) -> GlobalMarketSnapshot | None:
    metadata = GLOBAL_MARKET_BY_ID.get(market_id)
    if metadata is None or metadata.eastmoney_symbol is None or payload is None:
        return None

    row = _extract_eastmoney_row(payload, metadata.eastmoney_symbol)
    if row is None:
        return None

    price = _to_float(_pick(row, "f2", "price", "lastPrice", "latestPrice"))
    if price is None:
        return None

    updated_at = _parse_timestamp(_pick(row, "f124", "updatedAt", "updated_at", "time"), now=now)
    return _build_snapshot(
        market_id=market_id,
        provider_symbol=metadata.eastmoney_symbol,
        price=price,
        change=_to_float(_pick(row, "f4", "change", "changeAmount")),
        change_percent=_to_float(_pick(row, "f3", "changePercent", "changePct")),
        currency=str(_pick(row, "currency") or metadata.currency),
        market_status=_to_text(_pick(row, "marketStatus", "market_status")),
        source="eastmoney",
        delay_label=_to_text(_pick(row, "delayLabel", "delay_label")),
        updated_at=updated_at,
        now=now,
    )


def parse_fallback_quote(
    market_id: str,
    payload: Mapping[str, object] | None,
    *,
    now: datetime | None = None,
) -> GlobalMarketSnapshot | None:
    metadata = GLOBAL_MARKET_BY_ID.get(market_id)
    if metadata is None or metadata.fallback_symbol is None or payload is None:
        return None

    row = _extract_fallback_row(payload, metadata.fallback_symbol)
    if row is None:
        return None

    price = _to_float(_pick(row, "regularMarketPrice", "price", "lastPrice", "close"))
    if price is None:
        return None

    updated_at = _parse_timestamp(
        _pick(row, "regularMarketTime", "updatedAt", "updated_at", "timestamp", "time"),
        now=now,
    )
    source = str(_pick(row, "source", "provider") or metadata.fallback_source_hint)
    return _build_snapshot(
        market_id=market_id,
        provider_symbol=metadata.fallback_symbol,
        price=price,
        change=_to_float(_pick(row, "regularMarketChange", "change", "changeAmount")),
        change_percent=_to_float(_pick(row, "regularMarketChangePercent", "changePercent", "changePct")),
        currency=str(_pick(row, "currency") or metadata.currency),
        market_status=_to_text(_pick(row, "marketState", "marketStatus", "market_status")),
        source=source,
        delay_label=_to_text(_pick(row, "exchangeTimezoneShortName", "delayLabel", "delay_label")),
        updated_at=updated_at,
        now=now,
    )


def parse_moex_iss_quote(
    payload: Mapping[str, object] | None,
    *,
    now: datetime | None = None,
) -> GlobalMarketSnapshot | None:
    metadata = GLOBAL_MARKET_BY_ID["moex_russia"]
    row = _extract_moex_iss_marketdata_row(payload)
    if row is None:
        return None

    price = _to_float(_pick(row, "CURRENTVALUE", "LASTVALUE"))
    if price is None:
        return None

    return _build_snapshot(
        market_id="moex_russia",
        provider_symbol="IMOEX",
        price=price,
        change=_to_float(_pick(row, "LASTCHANGE")),
        change_percent=_to_float(_pick(row, "LASTCHANGEPRC")),
        currency=metadata.currency,
        market_status=_to_text(_pick(row, "TRADINGSTATUS", "STATUS")),
        source="moex-iss",
        delay_label="MOEX ISS",
        updated_at=_normalize_datetime(now) or datetime.now(UTC),
        now=now,
    )


def parse_yahoo_quote_page(
    market_id: str,
    html: str | None,
    *,
    now: datetime | None = None,
) -> GlobalMarketSnapshot | None:
    metadata = GLOBAL_MARKET_BY_ID.get(market_id)
    if metadata is None or metadata.fallback_symbol is None or market_id not in YAHOO_QUOTE_PAGE_MARKET_NAMES or not html:
        return None

    row = _extract_yahoo_quote_page_row(
        html,
        metadata.fallback_symbol,
        YAHOO_QUOTE_PAGE_MARKET_NAMES[market_id],
        YAHOO_QUOTE_PAGE_PRICE_BOUNDS.get(market_id),
    )
    if row is None:
        return None

    price = _to_float(_pick(row, "regularMarketPrice", "price"))
    if price is None or not _price_within_bounds(price, YAHOO_QUOTE_PAGE_PRICE_BOUNDS.get(market_id)):
        return None

    updated_at = _parse_timestamp(_pick(row, "regularMarketTime", "updatedAt", "timestamp"), now=now)
    return _build_snapshot(
        market_id=market_id,
        provider_symbol=metadata.fallback_symbol,
        price=price,
        change=_to_float(_pick(row, "regularMarketChange", "change")),
        change_percent=_to_float(_pick(row, "regularMarketChangePercent", "changePercent")),
        currency=str(_pick(row, "currency") or metadata.currency),
        market_status=_to_text(_pick(row, "marketState", "marketStatus")),
        source=_yahoo_quote_page_source(market_id),
        delay_label=_to_text(_pick(row, "exchangeTimezoneShortName", "delayLabel")),
        updated_at=updated_at,
        now=now,
    )


def merge_provider_snapshots(
    eastmoney_snapshots: dict[str, GlobalMarketSnapshot | None],
    fallback_snapshots: dict[str, GlobalMarketSnapshot | None],
) -> list[GlobalMarketSnapshot]:
    snapshots: list[GlobalMarketSnapshot] = []
    for metadata in sorted(GLOBAL_MARKET_BY_ID.values(), key=lambda item: item.display_order):
        snapshot = eastmoney_snapshots.get(metadata.id) or fallback_snapshots.get(metadata.id)
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def is_snapshot_stale(updated_at: str | datetime | None, *, now: datetime | None = None) -> bool:
    parsed = _parse_timestamp(updated_at, now=now)
    current = _normalize_datetime(now) or datetime.now(UTC)
    return current - parsed > STALE_AFTER


def _build_latest_payload(
    snapshots: list[GlobalMarketSnapshot],
    *,
    now: datetime,
    errors: list[dict[str, object]],
    stale_after: timedelta,
) -> dict[str, object]:
    items = [_snapshot_with_stale(snapshot, now=now, stale_after=stale_after).to_dict() for snapshot in snapshots]
    return {
        "items": items,
        "regions": _build_region_summaries(items),
        "source": _payload_source(items),
        "updatedAt": now.isoformat(),
        "delayLabel": _payload_delay_label(items),
        "stale": any(bool(item.get("stale")) for item in items),
        "errors": errors,
    }


def _build_region_summaries(items: list[dict[str, object]]) -> list[dict[str, object]]:
    by_id = {str(item["id"]): item for item in items}
    regions: list[dict[str, object]] = []
    for region in GLOBAL_MARKET_REGIONS.values():
        region_items = [by_id[market_id] for market_id in region.market_ids if market_id in by_id]
        changes = [item.get("changePercent") for item in region_items if isinstance(item.get("changePercent"), (int, float))]
        change_percent = sum(cast(list[float], changes)) / len(changes) if changes else None
        regions.append(
            {
                "id": region.id,
                "displayName": region.display_name,
                "changePercent": change_percent,
                "stale": bool(region_items) and all(bool(item.get("stale")) for item in region_items),
                "marketIds": list(region.market_ids),
            }
        )
    return regions


def _payload_source(items: list[dict[str, object]]) -> str:
    sources = sorted({str(item.get("source")) for item in items if item.get("source")})
    if not sources:
        return "unknown"
    if len(sources) == 1:
        return sources[0]
    return "+".join(sources)


def _payload_delay_label(items: list[dict[str, object]]) -> str | None:
    labels = [str(item.get("delayLabel")) for item in items if item.get("delayLabel")]
    return labels[0] if labels else None


def _snapshots_from_previous(
    previous_payload: Mapping[str, object] | None,
    existing_ids: set[str],
    *,
    now: datetime,
    stale_after: timedelta,
) -> list[GlobalMarketSnapshot]:
    if previous_payload is None:
        return []
    raw_items = previous_payload.get("items")
    if not isinstance(raw_items, list):
        return []
    snapshots: list[GlobalMarketSnapshot] = []
    for raw_item in raw_items:
        item = _as_object_dict(raw_item)
        if item is None:
            continue
        market_id = _to_text(item.get("id"))
        if market_id is None or market_id in existing_ids or market_id not in GLOBAL_MARKET_BY_ID:
            continue
        snapshot = _snapshot_from_item(item, now=now, stale_after=stale_after)
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def _unavailable_snapshots(existing_ids: set[str], *, now: datetime) -> list[GlobalMarketSnapshot]:
    snapshots: list[GlobalMarketSnapshot] = []
    for metadata in GLOBAL_MARKET_INDICES:
        if metadata.id in existing_ids:
            continue
        snapshots.append(
            GlobalMarketSnapshot(
                id=metadata.id,
                symbol=metadata.fallback_symbol or metadata.id,
                provider_symbol=metadata.fallback_symbol or metadata.id,
                name=metadata.display_name,
                region=metadata.region,
                country=metadata.country,
                exchange=metadata.exchange,
                longitude=metadata.longitude,
                latitude=metadata.latitude,
                price=None,
                change=None,
                change_percent=None,
                currency=metadata.currency,
                market_status="unavailable",
                source="unavailable",
                delay_label="暂无可用行情",
                updated_at=now.isoformat(),
                stale=True,
            )
        )
    return snapshots


def _snapshot_from_item(
    item: Mapping[str, object],
    *,
    now: datetime,
    stale_after: timedelta,
) -> GlobalMarketSnapshot | None:
    market_id = _to_text(item.get("id"))
    if market_id is None or market_id not in GLOBAL_MARKET_BY_ID:
        return None
    metadata = GLOBAL_MARKET_BY_ID[market_id]
    price = _to_float(item.get("price"))
    if price is None:
        return None
    updated_at = _parse_timestamp(item.get("updatedAt"), now=now).astimezone(UTC)
    return GlobalMarketSnapshot(
        id=metadata.id,
        symbol=metadata.fallback_symbol or metadata.id,
        provider_symbol=_to_text(item.get("providerSymbol")) or metadata.fallback_symbol or metadata.id,
        name=metadata.display_name,
        region=metadata.region,
        country=metadata.country,
        exchange=metadata.exchange,
        longitude=metadata.longitude,
        latitude=metadata.latitude,
        price=price,
        change=_to_float(item.get("change")),
        change_percent=_to_float(item.get("changePercent")),
        currency=_to_text(item.get("currency")) or metadata.currency,
        market_status=_to_text(item.get("marketStatus")),
        source=_to_text(item.get("source")) or "last-good",
        delay_label=_to_text(item.get("delayLabel")),
        updated_at=updated_at.isoformat(),
        stale=True if now - updated_at > stale_after else bool(item.get("stale")),
    )


def _snapshot_with_stale(snapshot: GlobalMarketSnapshot, *, now: datetime, stale_after: timedelta) -> GlobalMarketSnapshot:
    updated_at = _parse_timestamp(snapshot.updated_at, now=now).astimezone(UTC)
    stale = snapshot.stale or now - updated_at > stale_after
    if stale == snapshot.stale:
        return snapshot
    return GlobalMarketSnapshot(
        id=snapshot.id,
        symbol=snapshot.symbol,
        provider_symbol=snapshot.provider_symbol,
        name=snapshot.name,
        region=snapshot.region,
        country=snapshot.country,
        exchange=snapshot.exchange,
        longitude=snapshot.longitude,
        latitude=snapshot.latitude,
        price=snapshot.price,
        change=snapshot.change,
        change_percent=snapshot.change_percent,
        currency=snapshot.currency,
        market_status=snapshot.market_status,
        source=snapshot.source,
        delay_label=snapshot.delay_label,
        updated_at=snapshot.updated_at,
        stale=stale,
    )


def _error_entry(source: str, market_id: str | None, exc: Exception) -> dict[str, object]:
    return {"source": source, "marketId": market_id, "message": str(exc) or exc.__class__.__name__}


def _build_snapshot(
    *,
    market_id: str,
    provider_symbol: str,
    price: float,
    change: float | None,
    change_percent: float | None,
    currency: str,
    market_status: str | None,
    source: str,
    delay_label: str | None,
    updated_at: datetime,
    now: datetime | None,
) -> GlobalMarketSnapshot:
    metadata = GLOBAL_MARKET_BY_ID[market_id]
    updated_at_utc = updated_at.astimezone(UTC)
    return GlobalMarketSnapshot(
        id=metadata.id,
        symbol=metadata.fallback_symbol or metadata.id,
        provider_symbol=provider_symbol,
        name=metadata.display_name,
        region=metadata.region,
        country=metadata.country,
        exchange=metadata.exchange,
        longitude=metadata.longitude,
        latitude=metadata.latitude,
        price=price,
        change=change,
        change_percent=change_percent,
        currency=currency,
        market_status=market_status,
        source=source,
        delay_label=delay_label,
        updated_at=updated_at_utc.isoformat(),
        stale=is_snapshot_stale(updated_at_utc, now=now),
    )


def _extract_eastmoney_row(payload: Mapping[str, object], provider_symbol: str) -> dict[str, object] | None:
    rows = payload.get("diff")
    data = _as_object_dict(payload.get("data"))
    if rows is None and data is not None:
        rows = data.get("diff")
    if isinstance(rows, dict):
        row_items = list(cast(dict[object, object], rows).values())
    elif isinstance(rows, list):
        row_items = cast(list[object], rows)
    else:
        row_items = [payload]

    for row in row_items:
        row_payload = _as_object_dict(row)
        if row_payload is None:
            continue
        row_symbol = _to_text(_pick(row_payload, "f12", "symbol", "code", "providerSymbol"))
        market = _to_text(_pick(row_payload, "f13", "market"))
        combined = f"{market}.{row_symbol}" if market and row_symbol else row_symbol
        if row_symbol == provider_symbol or combined == provider_symbol:
            return row_payload
    return None


def _extract_fallback_row(payload: Mapping[str, object], provider_symbol: str) -> dict[str, object] | None:
    result = _as_object_dict(payload.get("quoteResponse"))
    if result is not None:
        rows = result.get("result")
        if isinstance(rows, list):
            for row in cast(list[object], rows):
                row_payload = _as_object_dict(row)
                if row_payload is not None and _to_text(_pick(row_payload, "symbol", "providerSymbol")) == provider_symbol:
                    return row_payload

    chart = _as_object_dict(payload.get("chart"))
    if chart is not None:
        rows = chart.get("result")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return _normalize_yahoo_chart_row(cast(dict[str, object], rows[0]), provider_symbol)

    symbol = _to_text(_pick(payload, "symbol", "providerSymbol"))
    if symbol in (None, provider_symbol):
        return dict(payload)
    return None


def _extract_moex_iss_marketdata_row(payload: Mapping[str, object] | None) -> dict[str, object] | None:
    if payload is None:
        return None

    section = _as_object_dict(payload.get("marketdata"))
    if section is None:
        return None

    columns = section.get("columns")
    rows = section.get("data")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return None

    column_names = [str(column) for column in cast(list[object], columns)]
    for row in cast(list[object], rows):
        if not isinstance(row, list):
            continue
        row_payload: dict[str, object] = dict(zip(column_names, cast(list[object], row), strict=False))
        if _to_text(row_payload.get("SECID")) == "IMOEX":
            return row_payload
    return None


def _normalize_yahoo_chart_row(row: dict[str, object], provider_symbol: str) -> dict[str, object]:
    meta = _as_object_dict(row.get("meta")) or {}
    indicators = _as_object_dict(row.get("indicators")) or {}
    raw_quote_rows = indicators.get("quote")
    quote_rows = cast(list[object], raw_quote_rows) if isinstance(raw_quote_rows, list) else []
    quote = _as_object_dict(quote_rows[0]) if quote_rows else None
    raw_timestamps = row.get("timestamp")
    timestamps = cast(list[object], raw_timestamps) if isinstance(raw_timestamps, list) else []
    raw_close_values = quote.get("close") if quote is not None else None
    close_values = cast(list[object], raw_close_values) if isinstance(raw_close_values, list) else []
    price = meta.get("regularMarketPrice") or _last_present(close_values)
    previous_close = meta.get("previousClose")
    change = None
    price_number = _to_float(price)
    previous_close_number = _to_float(previous_close)
    if price_number is not None and previous_close_number is not None:
        change = price_number - previous_close_number
    return {
        "symbol": meta.get("symbol") or provider_symbol,
        "regularMarketPrice": price,
        "regularMarketChange": change,
        "regularMarketChangePercent": _percent_change(change, previous_close),
        "currency": meta.get("currency"),
        "marketState": meta.get("marketState"),
        "exchangeTimezoneShortName": meta.get("exchangeTimezoneShortName"),
        "regularMarketTime": meta.get("regularMarketTime") or _last_present(timestamps),
        "source": meta.get("source") or "yahoo-finance",
    }


def _extract_yahoo_quote_page_row(
    html: str,
    provider_symbol: str,
    expected_names: tuple[str, ...],
    price_bounds: tuple[float, float] | None,
) -> dict[str, object] | None:
    text = _normalize_html_payload(html)
    if not _page_title_matches_expected_market(text, provider_symbol, expected_names):
        return None

    json_row = _extract_yahoo_quote_page_json_row(text, provider_symbol, expected_names, price_bounds)
    if json_row is not None:
        return json_row

    return _extract_yahoo_quote_page_visible_row(text, provider_symbol, expected_names, price_bounds)


def _extract_yahoo_quote_page_json_row(
    text: str,
    provider_symbol: str,
    expected_names: tuple[str, ...],
    price_bounds: tuple[float, float] | None,
) -> dict[str, object] | None:
    for block in _iter_yahoo_quote_json_blocks(text, provider_symbol):
        fragment = _find_yahoo_quote_json_fragment(block, provider_symbol, expected_names, price_bounds)
        if fragment is None:
            continue
        price = _extract_json_number(fragment, "regularMarketPrice")
        if price is None:
            continue
        return {
            "symbol": provider_symbol,
            "regularMarketPrice": price,
            "regularMarketChange": _extract_json_number(fragment, "regularMarketChange"),
            "regularMarketChangePercent": _extract_json_number(fragment, "regularMarketChangePercent"),
            "currency": _extract_json_text(fragment, "currency"),
            "marketState": _extract_json_text(fragment, "marketState"),
            "exchangeTimezoneShortName": _extract_json_text(fragment, "exchangeTimezoneShortName"),
            "regularMarketTime": _extract_json_number(fragment, "regularMarketTime"),
        }
    return None


def _extract_yahoo_quote_page_visible_row(
    text: str,
    provider_symbol: str,
    expected_names: tuple[str, ...],
    price_bounds: tuple[float, float] | None,
) -> dict[str, object] | None:
    quote_price_row = _extract_yahoo_quote_price_section_row(text, price_bounds)
    if quote_price_row is not None:
        return quote_price_row

    context = _extract_symbol_scoped_html_context(text, provider_symbol)
    if context is None or _find_expected_market_name(context, expected_names) is None:
        return None

    price = _extract_html_data_field_number(context, "regularMarketPrice")
    if price is None:
        visible_text = _strip_html_tags(context)
        price = _first_visible_number(visible_text)
    if price is None or not _price_within_bounds(price, price_bounds):
        return None
    return {
        "regularMarketPrice": price,
        "regularMarketChange": _extract_html_data_field_number(context, "regularMarketChange"),
        "regularMarketChangePercent": _extract_html_data_field_number(context, "regularMarketChangePercent"),
    }


def _extract_yahoo_quote_price_section_row(
    text: str,
    price_bounds: tuple[float, float] | None,
) -> dict[str, object] | None:
    context = _extract_testid_scoped_html_context(text, "quote-price")
    if context is None:
        return None

    price = _extract_html_testid_number(context, "qsp-price")
    if price is None or not _price_within_bounds(price, price_bounds):
        return None
    return {
        "regularMarketPrice": price,
        "regularMarketChange": _extract_html_testid_number(context, "qsp-price-change"),
        "regularMarketChangePercent": _extract_html_testid_number(context, "qsp-price-change-percent"),
    }


def _extract_symbol_scoped_html_context(text: str, provider_symbol: str) -> str | None:
    pattern = rf'<(?P<tag>[a-zA-Z][\w:-]*)[^>]*data-symbol=["\']{re.escape(provider_symbol)}["\'][^>]*>'
    for match in re.finditer(pattern, text):
        tag = match.group("tag")
        close = re.search(rf"</{re.escape(tag)}>", text[match.end() :], flags=re.IGNORECASE)
        if close is not None:
            return text[match.start() : match.end() + close.end()]
        context = text[max(0, match.start() - 1000) : match.end() + 3000]
        if "regularMarketPrice" in context:
            return context
    return None


def _extract_testid_scoped_html_context(text: str, test_id: str) -> str | None:
    pattern = rf'<(?P<tag>[a-zA-Z][\w:-]*)[^>]*data-testid=["\']{re.escape(test_id)}["\'][^>]*>'
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        return None
    return text[match.start() : match.end() + 3000]


def _iter_yahoo_quote_json_blocks(text: str, provider_symbol: str):
    symbol_pattern = rf'"symbol"\s*:\s*"{re.escape(provider_symbol)}"'
    for match in re.finditer(symbol_pattern, text):
        search_end = match.start()
        attempts = 0
        while attempts < 40:
            start = text.rfind("{", 0, search_end)
            if start < 0 or match.start() - start > 30000:
                break
            search_end = start
            attempts += 1
            block = _balanced_json_object_from(text, start)
            if block is None:
                continue
            if start <= match.start() < start + len(block):
                yield block


def _balanced_json_object_from(text: str, start: int, max_length: int = 30000) -> str | None:
    if start >= len(text) or text[start] != "{":
        return None

    depth = 0
    in_string = False
    escaped = False
    end = min(len(text), start + max_length)
    for index in range(start, end):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _find_yahoo_quote_json_fragment(
    block: str,
    provider_symbol: str,
    expected_names: tuple[str, ...],
    price_bounds: tuple[float, float] | None,
) -> str | None:
    candidates: list[tuple[int, str]] = []
    symbol_pattern = rf'"symbol"\s*:\s*"{re.escape(provider_symbol)}"'
    for match in re.finditer(symbol_pattern, block):
        search_end = match.start()
        attempts = 0
        while attempts < 20:
            start = block.rfind("{", 0, search_end)
            if start < 0 or match.start() - start > 10000:
                break
            search_end = start
            attempts += 1
            fragment = _balanced_json_object_from(block, start, max_length=10000)
            if fragment is None or not start <= match.start() < start + len(fragment):
                continue
            if _json_fragment_matches_expected_market(fragment, provider_symbol, expected_names, price_bounds):
                score = _score_yahoo_quote_json_fragment(block, start, fragment)
                if score >= 0:
                    candidates.append((score, fragment))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _score_yahoo_quote_json_fragment(block: str, start: int, fragment: str) -> int:
    prefix = block[max(0, start - 300) : start].casefold()
    score = 0
    if re.search(r'"(?:price|quoteheader|quotesummary)"\s*:\s*$', prefix, flags=re.IGNORECASE):
        score += 100
    if re.search(r'related|similar|recommended|recommendation|watchlist|portfolio|card', prefix, flags=re.IGNORECASE):
        score -= 100
    if _extract_json_text(fragment, "marketState") is not None:
        score += 10
    if _extract_json_text(fragment, "exchangeTimezoneShortName") is not None:
        score += 10
    if len(fragment) > 5000:
        score -= 20
    return score


def _json_fragment_matches_expected_market(
    fragment: str,
    provider_symbol: str,
    expected_names: tuple[str, ...],
    price_bounds: tuple[float, float] | None,
) -> bool:
    if _extract_json_text(fragment, "symbol") != provider_symbol:
        return False
    if "regularMarketPrice" not in fragment:
        return False
    price = _extract_json_number(fragment, "regularMarketPrice")
    if price is None or not _price_within_bounds(price, price_bounds):
        return False
    names = (
        _extract_json_text(fragment, "shortName"),
        _extract_json_text(fragment, "longName"),
        _extract_json_text(fragment, "title"),
        _extract_json_text(fragment, "displayName"),
    )
    return any(_matches_expected_market_name(name, expected_names) for name in names)


def _page_title_matches_expected_market(text: str, provider_symbol: str, expected_names: tuple[str, ...]) -> bool:
    match = re.search(r"<title[^>]*>(?P<title>.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return True

    title = _strip_html_tags(match.group("title"))
    return provider_symbol in title and _matches_expected_market_name(title, expected_names)


def _find_expected_market_name(text: str, expected_names: tuple[str, ...]) -> re.Match[str] | None:
    title_patterns = [rf'title=["\']{re.escape(name)}["\']' for name in expected_names]
    text_patterns = [re.escape(name) for name in expected_names]
    for pattern in title_patterns + text_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            return match
    return None


def _matches_expected_market_name(value: str | None, expected_names: tuple[str, ...]) -> bool:
    if value is None:
        return False
    normalized = value.casefold()
    return any(expected_name.casefold() in normalized for expected_name in expected_names)


def _price_within_bounds(price: float, bounds: tuple[float, float] | None) -> bool:
    if bounds is None:
        return True
    lower, upper = bounds
    return lower <= price <= upper


def _yahoo_quote_page_source(market_id: str) -> str:
    return f"{YAHOO_QUOTE_PAGE_SOURCE_PREFIX}-{market_id.replace('_', '-')}"


def _normalize_html_payload(html: str) -> str:
    text = unescape(html)
    return text.replace('\\"', '"').replace("\\u005e", "^").replace("\\u005E", "^")


def _extract_json_number(text: str, key: str) -> float | None:
    pattern = rf'"{re.escape(key)}"\s*:\s*(?:{{[^{{}}]*?"raw"\s*:\s*)?"?(?P<value>[-+]?\d[\d,]*(?:\.\d+)?)%?"?'
    match = re.search(pattern, text)
    return _to_float(match.group("value")) if match is not None else None


def _extract_json_text(text: str, key: str) -> str | None:
    pattern = rf'"{re.escape(key)}"\s*:\s*"(?P<value>[^"\\]*(?:\\.[^"\\]*)*)"'
    match = re.search(pattern, text)
    return _to_text(match.group("value")) if match is not None else None


def _extract_html_data_field_number(text: str, field: str) -> float | None:
    pattern = rf'data-field=["\']{re.escape(field)}["\'][^>]*>(?P<value>[^<]+)'
    match = re.search(pattern, text)
    if match is None:
        return None
    return _to_float(match.group("value").strip().strip("()"))


def _extract_html_testid_number(text: str, test_id: str) -> float | None:
    pattern = rf'data-testid\s*=\s*(["\']){re.escape(test_id)}\1[^>]*>(?P<value>[^<]+)'
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        return None
    return _to_float(match.group("value").strip().strip("()"))


def _strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def _first_visible_number(text: str) -> float | None:
    match = re.search(r"[-+]?\d[\d,]*\.\d+", text)
    return _to_float(match.group(0)) if match is not None else None


def _pick(payload: Mapping[str, object], *keys: str) -> object | None:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if isfinite(number) else None
    try:
        normalized = str(value).replace(",", "").replace("%", "").strip()
        if not normalized or normalized in {"--", "-"}:
            return None
        number = float(normalized)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _to_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_timestamp(value: object, *, now: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                pass
            if text.isdigit():
                return _parse_timestamp(float(text), now=now)
    return _normalize_datetime(now) or datetime.now(UTC)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _last_present(values: list[object]) -> object | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def _percent_change(change: float | None, previous_close: object) -> float | None:
    base = _to_float(previous_close)
    if change is None or base in (None, 0):
        return None
    return change / base * 100


def _as_object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


__all__ = [
    "GlobalMarketSnapshot",
    "is_snapshot_stale",
    "merge_provider_snapshots",
    "parse_eastmoney_quote",
    "parse_fallback_quote",
    "parse_yahoo_quote_page",
]
