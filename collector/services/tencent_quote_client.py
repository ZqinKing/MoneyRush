from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
import logging
import random
from time import monotonic, sleep
from urllib.request import urlopen

from mootdx.quotes import Quotes


QUOTE_URL = "https://qt.gtimg.cn/q="
CHINA_MARKET_TZ = timezone(timedelta(hours=8))
DEFAULT_MOOTDX_SERVER = ("180.153.18.170", 7709)
A_SHARE_LOT_SIZE = 100
logger = logging.getLogger(__name__)


def _symbol_to_vendor_code(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return f"sh{symbol}"
    return f"sz{symbol}"


def _infer_exchange(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _infer_market_code(symbol: str) -> int:
    if symbol.startswith(("5", "6", "9")):
        return 1
    return 0


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _lots_to_shares(value: int | None) -> int | None:
    if value is None:
        return None
    return value * A_SHARE_LOT_SIZE


def _parse_timestamp(value: str) -> datetime:
    if len(value) == 14 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_MARKET_TZ).astimezone(UTC)
    return datetime.now(UTC)


def _to_utc_day_bucket(value: object) -> datetime | None:
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        for parser in (datetime.fromisoformat,):
            try:
                parsed = parser(value)
                return _to_utc_day_bucket(parsed)
            except ValueError:
                continue
        for pattern in ("%Y-%m-%d", "%Y%m%d"):
            try:
                parsed_date = datetime.strptime(value, pattern).date()
                return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=UTC)
            except ValueError:
                continue
    return None


def _to_utc_intraday_bucket(value: object, trade_day: date | None = None) -> datetime | None:
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized.replace(second=0, microsecond=0)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        for parser in (datetime.fromisoformat,):
            try:
                parsed = parser(value)
                return _to_utc_intraday_bucket(parsed, trade_day=trade_day)
            except ValueError:
                continue
        normalized_value = value.strip()
        if trade_day is not None:
            for pattern in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%H:%M:%S",
                "%H:%M",
            ):
                try:
                    parsed = datetime.strptime(normalized_value, pattern)
                    local_timestamp = datetime.combine(
                        trade_day,
                        parsed.time(),
                        tzinfo=CHINA_MARKET_TZ,
                    )
                    return local_timestamp.astimezone(UTC).replace(second=0, microsecond=0)
                except ValueError:
                    continue
    return None


@dataclass(slots=True)
class TencentQuote:
    symbol: str
    company_name: str
    exchange: str
    last_price: float
    previous_close: float
    open_price: float
    high_price: float
    low_price: float
    volume: int
    amount: float
    change_pct: float
    turnover_rate: float | None
    pe: float | None
    pb: float | None
    market_cap: float | None
    limit_up: float | None
    limit_down: float | None
    updated_at: datetime
    currency: str | None

    def to_market_state(self) -> dict[str, dict[str, object]]:
        day_bucket = self.updated_at.replace(hour=0, minute=0, second=0, microsecond=0)

        snapshot = {
            "symbol": self.symbol,
            "companyName": self.company_name,
            "exchange": self.exchange,
            "lastPrice": self.last_price,
            "changePct": self.change_pct,
            "pe": self.pe,
            "pb": self.pb,
            "turnoverRate": self.turnover_rate,
            "marketCap": self.market_cap,
            "limitUp": self.limit_up,
            "limitDown": self.limit_down,
            "updatedAt": self.updated_at.isoformat(),
            "source": "tencent-finance",
        }

        tick = {
            "ts": self.updated_at,
            "symbol": self.symbol,
            "price": self.last_price,
            "volume": self.volume,
            "amount": self.amount,
            "side": "buy" if self.last_price >= self.previous_close else "sell",
            "source": "tencent-finance",
            "raw": {
                "provider": "tencent-finance",
                "providerVolumeUnit": "lots",
                "volumeUnit": "shares",
                "currency": self.currency,
                "previousClose": self.previous_close,
                "open": self.open_price,
            },
        }

        kline = {
            "bucketTs": day_bucket,
            "symbol": self.symbol,
            "period": "1d",
            "open": self.open_price,
            "high": self.high_price,
            "low": self.low_price,
            "close": self.last_price,
            "volume": self.volume,
            "amount": self.amount,
            "source": "tencent-finance",
            "raw": {
                "provider": "tencent-finance",
                "providerVolumeUnit": "lots",
                "volumeUnit": "shares",
                "previousClose": self.previous_close,
                "currency": self.currency,
            },
        }

        event = {
            "type": "market_update",
            "generatedAt": self.updated_at.isoformat(),
            "symbol": self.symbol,
            "companyName": self.company_name,
            "exchange": self.exchange,
            "snapshot": snapshot,
            "tick": {
                "price": self.last_price,
                "volume": self.volume,
                "volumeUnit": "shares",
                "side": tick["side"],
            },
            "kline": {
                "period": "1d",
                "close": self.last_price,
                "high": self.high_price,
                "low": self.low_price,
            },
        }

        return {
            "snapshot": snapshot,
            "tick": tick,
            "kline": kline,
            "event": event,
        }


@dataclass(slots=True)
class MootdxQuote:
    symbol: str
    exchange: str
    last_price: float
    previous_close: float
    open_price: float
    high_price: float
    low_price: float
    volume: int
    amount: float
    updated_at: datetime
    bid_price_1: float | None
    ask_price_1: float | None
    bid_volume_1: int | None
    ask_volume_1: int | None
    raw: dict[str, object]
    daily_bucket: datetime


class TencentQuoteClient:
    def fetch_quote(self, symbol: str) -> TencentQuote:
        vendor_code = _symbol_to_vendor_code(symbol)
        url = f"{QUOTE_URL}{vendor_code}"

        with urlopen(url, timeout=10) as response:
            payload = response.read().decode("gbk", errors="replace").strip()

        if not payload:
            raise ValueError(f"empty quote payload for {symbol}")

        _, quoted_value = payload.split('="', 1)
        parts = quoted_value.removesuffix('";').split("~")
        if len(parts) < 49:
            raise ValueError(f"unexpected quote payload shape for {symbol}")

        company_name = parts[1] or symbol
        last_price = _to_float(parts[3])
        previous_close = _to_float(parts[4])
        open_price = _to_float(parts[5])
        high_price = _to_float(parts[33])
        low_price = _to_float(parts[34])
        volume = _lots_to_shares(_to_int(parts[36]))
        amount = None
        if parts[35]:
            detail_parts = parts[35].split("/")
            if len(detail_parts) >= 3:
                amount = _to_float(detail_parts[2])

        if None in (last_price, previous_close, open_price, high_price, low_price, volume, amount):
            raise ValueError(f"missing required quote fields for {symbol}")

        change_pct = _to_float(parts[32])
        turnover_rate = _to_float(parts[38])
        pe = _to_float(parts[39])
        market_cap = _to_float(parts[45])
        pb = _to_float(parts[46])
        limit_up = _to_float(parts[47])
        limit_down = _to_float(parts[48])
        updated_at = _parse_timestamp(parts[30])
        currency = None
        if len(parts) > 82:
            currency = parts[82] or None

        return TencentQuote(
            symbol=symbol,
            company_name=company_name,
            exchange=_infer_exchange(symbol),
            last_price=last_price,
            previous_close=previous_close,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            volume=volume,
            amount=amount,
            change_pct=change_pct if change_pct is not None else round(((last_price - previous_close) / previous_close) * 100, 4),
            turnover_rate=turnover_rate,
            pe=pe,
            pb=pb,
            market_cap=market_cap * 100_000_000 if market_cap is not None else None,
            limit_up=limit_up,
            limit_down=limit_down,
            updated_at=updated_at,
            currency=currency,
        )


class MootdxQuoteClient:
    def __init__(self) -> None:
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            self._client = Quotes.factory(
                market="std",
                server=DEFAULT_MOOTDX_SERVER,
                heartbeat=True,
                timeout=15,
            )

        return self._client

    def fetch_quote(self, symbol: str) -> MootdxQuote:
        client = self._ensure_client()
        quote_frame = client.quotes(symbol=[symbol])
        if quote_frame.empty:
            raise ValueError(f"empty mootdx quote payload for {symbol}")

        quote_row = quote_frame.iloc[0]
        updated_at = self._combine_quote_timestamp(quote_row["servertime"])
        daily_bucket = datetime(
            year=updated_at.year,
            month=updated_at.month,
            day=updated_at.day,
            tzinfo=UTC,
        )

        volume_shares = _lots_to_shares(int(float(quote_row["volume"])))
        bid_volume_1 = _lots_to_shares(_to_int(str(quote_row.get("bid_vol1"))))
        ask_volume_1 = _lots_to_shares(_to_int(str(quote_row.get("ask_vol1"))))

        return MootdxQuote(
            symbol=symbol,
            exchange=_infer_exchange(symbol),
            last_price=float(quote_row["price"]),
            previous_close=float(quote_row["last_close"]),
            open_price=float(quote_row["open"]),
            high_price=float(quote_row["high"]),
            low_price=float(quote_row["low"]),
            volume=volume_shares,
            amount=float(quote_row["amount"]),
            updated_at=updated_at,
            bid_price_1=_to_float(str(quote_row.get("bid1"))),
            ask_price_1=_to_float(str(quote_row.get("ask1"))),
            bid_volume_1=bid_volume_1,
            ask_volume_1=ask_volume_1,
            raw={key: self._normalize_scalar(value) for key, value in quote_row.to_dict().items()},
            daily_bucket=daily_bucket,
        )

    def fetch_daily_history(self, symbol: str, limit: int = 60) -> list[dict[str, object]]:
        client = self._ensure_client()
        remaining = max(limit, 0)
        start = 0
        records: list[dict[str, object]] = []

        while remaining > 0:
          chunk_size = min(remaining, 800)
          frame = client.bars(symbol=symbol, frequency=9, start=start, offset=chunk_size)
          if frame is None or frame.empty:
              break

          normalized_frame = frame.copy()
          if getattr(normalized_frame.index, "name", None) and normalized_frame.index.name not in normalized_frame.columns:
              normalized_frame = normalized_frame.reset_index()

          for row in normalized_frame.to_dict("records"):
              bucket_ts = _to_utc_day_bucket(
                  row.get("datetime")
                  or row.get("date")
                  or row.get("trade_date")
              )
              open_price = _to_float(str(row.get("open")))
              high_price = _to_float(str(row.get("high")))
              low_price = _to_float(str(row.get("low")))
              close_price = _to_float(str(row.get("close")))
              volume_lots = _to_int(str(row.get("volume") if row.get("volume") is not None else row.get("vol")))
              amount = _to_float(str(row.get("amount")))
              volume = _lots_to_shares(volume_lots)

              if bucket_ts is None or None in (open_price, high_price, low_price, close_price):
                  continue

              records.append(
                  {
                      "bucketTs": bucket_ts,
                      "symbol": symbol,
                      "period": "1d",
                      "open": open_price,
                      "high": high_price,
                      "low": low_price,
                      "close": close_price,
                      "volume": volume,
                      "amount": amount,
                      "source": "mootdx",
                      "raw": {
                          **row,
                          "provider": "mootdx",
                          "providerVolumeUnit": "lots",
                          "volumeUnit": "shares",
                      },
                  }
              )

          fetched_count = len(frame.index)
          if fetched_count < chunk_size:
              break
          remaining -= fetched_count
          start += fetched_count

        deduped: dict[datetime, dict[str, object]] = {}
        for item in records:
            deduped[item["bucketTs"]] = item
        return [deduped[key] for key in sorted(deduped.keys(), reverse=True)[:limit]]

    def fetch_intraday_history(self, symbol: str, trade_day: date) -> list[dict[str, object]]:
        client = self._ensure_client()
        market = _infer_market_code(symbol)
        day_code = trade_day.strftime("%Y%m%d")
        frame = self._fetch_minute_frame(client, symbol=symbol, market=market, day_code=day_code)
        if frame is None or frame.empty:
            return []

        normalized_frame = frame.copy()
        if getattr(normalized_frame.index, "name", None) and normalized_frame.index.name not in normalized_frame.columns:
            normalized_frame = normalized_frame.reset_index()

        records: list[dict[str, object]] = []
        for row in normalized_frame.to_dict("records"):
            bucket_ts = self._intraday_bucket_ts(row, trade_day=trade_day)
            close_price = _to_float(str(row.get("price") if row.get("price") is not None else row.get("close")))
            open_price = _to_float(str(row.get("open"))) if row.get("open") is not None else close_price
            high_price = _to_float(str(row.get("high"))) if row.get("high") is not None else close_price
            low_price = _to_float(str(row.get("low"))) if row.get("low") is not None else close_price
            volume_lots = _to_int(str(row.get("volume") if row.get("volume") is not None else row.get("vol")))
            amount = _to_float(str(row.get("amount")))
            volume = _lots_to_shares(volume_lots)

            if bucket_ts is None or None in (open_price, high_price, low_price, close_price):
                continue

            records.append(
                {
                    "bucketTs": bucket_ts,
                    "symbol": symbol,
                    "period": "1m",
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": volume,
                    "amount": amount,
                    "source": "mootdx",
                    "raw": {
                        **row,
                        "provider": "mootdx",
                        "providerVolumeUnit": "lots",
                        "volumeUnit": "shares",
                        "tradeDate": day_code,
                    },
                }
            )

        deduped: dict[datetime, dict[str, object]] = {}
        for item in records:
            deduped[item["bucketTs"]] = item
        return [deduped[key] for key in sorted(deduped.keys())]

    @staticmethod
    def _fetch_minute_frame(client, *, symbol: str, market: int, day_code: str):
        minutes_method = getattr(client, "minutes", None)
        if callable(minutes_method):
            for args, kwargs in (
                ((), {"symbol": symbol, "market": market, "date": day_code}),
                ((symbol, market, day_code), {}),
                ((), {"symbol": symbol, "date": day_code}),
            ):
                try:
                    return minutes_method(*args, **kwargs)
                except TypeError:
                    continue

        minute_method = getattr(client, "minute", None)
        if callable(minute_method):
            for args, kwargs in (
                ((), {"symbol": symbol, "market": market, "date": day_code}),
                ((symbol, market, day_code), {}),
                ((), {"symbol": symbol, "market": market}),
            ):
                try:
                    return minute_method(*args, **kwargs)
                except TypeError:
                    continue

        raise AttributeError("mootdx client does not expose usable minute history methods")

    @classmethod
    def _intraday_bucket_ts(cls, row: dict[str, object], *, trade_day: date) -> datetime | None:
        raw_datetime = row.get("datetime")
        if isinstance(raw_datetime, datetime):
            normalized = raw_datetime.astimezone(UTC) if raw_datetime.tzinfo is not None else raw_datetime.replace(tzinfo=CHINA_MARKET_TZ).astimezone(UTC)
            return normalized.replace(second=0, microsecond=0)

        if isinstance(raw_datetime, str):
            normalized = raw_datetime.strip()
            for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d %H:%M:%S", "%Y%m%d %H:%M"):
                try:
                    parsed = datetime.strptime(normalized, pattern)
                    return parsed.replace(tzinfo=CHINA_MARKET_TZ).astimezone(UTC).replace(second=0, microsecond=0)
                except ValueError:
                    continue

        raw_time = row.get("time")
        if raw_time is None:
            return None

        digits = "".join(character for character in str(raw_time).strip() if character.isdigit())
        if len(digits) == 3:
            digits = f"0{digits}"
        if len(digits) == 4:
            digits = f"{digits}00"
        if len(digits) != 6:
            return None

        local_timestamp = datetime(
            trade_day.year,
            trade_day.month,
            trade_day.day,
            int(digits[0:2]),
            int(digits[2:4]),
            int(digits[4:6]),
            tzinfo=CHINA_MARKET_TZ,
        )
        return local_timestamp.astimezone(UTC).replace(second=0, microsecond=0)

    def fetch_intraday_history(self, symbol: str, trade_day: date | None = None) -> list[dict[str, object]]:
        client = self._ensure_client()
        trade_day = trade_day or datetime.now(CHINA_MARKET_TZ).date()
        day_label = trade_day.strftime("%Y%m%d")
        market = _infer_market_code(symbol)

        frame = None
        fetch_attempts = (
            lambda: client.minutes(symbol=symbol, market=market, date=day_label),
            lambda: client.minutes(symbol, market, day_label),
            lambda: client.minutes(symbol=symbol, date=day_label),
            lambda: client.minute(symbol=symbol, market=market, date=day_label),
            lambda: client.minute(symbol, market, day_label),
            lambda: client.minute(symbol=symbol, market=market),
            lambda: client.minute(symbol, market),
        )
        for fetch in fetch_attempts:
            try:
                frame = fetch()
                break
            except TypeError:
                continue

        if frame is None or getattr(frame, "empty", False):
            return []

        normalized_frame = frame.copy() if hasattr(frame, "copy") else frame
        if getattr(normalized_frame.index, "name", None) and normalized_frame.index.name not in getattr(normalized_frame, "columns", []):
            normalized_frame = normalized_frame.reset_index()

        records: list[dict[str, object]] = []
        rows = normalized_frame.to_dict("records")
        synthesized_timeline = self._build_intraday_timeline(trade_day=trade_day, length=len(rows))
        for index, row in enumerate(rows):
            bucket_ts = _to_utc_intraday_bucket(
                row.get("datetime")
                or row.get("date")
                or row.get("time")
                or row.get("trade_time")
                or synthesized_timeline[index],
                trade_day=trade_day,
            )
            close_price = _to_float(str(row.get("close") if row.get("close") is not None else row.get("price")))
            if bucket_ts is None or close_price is None:
                continue

            open_price = _to_float(str(row.get("open") if row.get("open") is not None else close_price)) or close_price
            high_price = _to_float(str(row.get("high") if row.get("high") is not None else close_price)) or close_price
            low_price = _to_float(str(row.get("low") if row.get("low") is not None else close_price)) or close_price
            volume_lots = _to_int(str(row.get("volume") if row.get("volume") is not None else row.get("vol")))
            volume = _lots_to_shares(volume_lots)
            amount = _to_float(str(row.get("amount")))
            if amount is None and volume is not None:
                amount = round(close_price * volume, 2)

            records.append(
                {
                    "bucketTs": bucket_ts,
                    "symbol": symbol,
                    "period": "1m",
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": volume,
                    "amount": amount,
                    "source": "mootdx",
                    "raw": {
                        **row,
                        "provider": "mootdx",
                        "providerVolumeUnit": "lots",
                        "volumeUnit": "shares",
                    },
                }
            )

        deduped: dict[datetime, dict[str, object]] = {}
        for item in records:
            deduped[item["bucketTs"]] = item
        return [deduped[key] for key in sorted(deduped.keys())]

    @staticmethod
    def _build_intraday_timeline(*, trade_day: date, length: int) -> list[str]:
        if length <= 0:
            return []

        morning_start = datetime(trade_day.year, trade_day.month, trade_day.day, 9, 30, tzinfo=CHINA_MARKET_TZ)
        morning_minutes = 120
        afternoon_start = datetime(trade_day.year, trade_day.month, trade_day.day, 13, 0, tzinfo=CHINA_MARKET_TZ)
        afternoon_minutes = 120

        timeline: list[str] = []
        for offset in range(morning_minutes):
            timeline.append((morning_start + timedelta(minutes=offset)).strftime("%H:%M:%S"))
        for offset in range(afternoon_minutes):
            timeline.append((afternoon_start + timedelta(minutes=offset)).strftime("%H:%M:%S"))

        if length <= len(timeline):
            return timeline[:length]

        last_timestamp = afternoon_start + timedelta(minutes=afternoon_minutes - 1)
        while len(timeline) < length:
            last_timestamp += timedelta(minutes=1)
            timeline.append(last_timestamp.strftime("%H:%M:%S"))
        return timeline

    @classmethod
    def _combine_quote_timestamp(cls, servertime: object) -> datetime:
        if not isinstance(servertime, str):
            raise TypeError("mootdx quote servertime must be a string")

        trade_day = datetime.now(CHINA_MARKET_TZ).date()
        time_part = servertime.split(".", 1)[0]
        local_timestamp = datetime.strptime(f"{trade_day:%Y-%m-%d} {time_part}", "%Y-%m-%d %H:%M:%S")
        return local_timestamp.replace(tzinfo=CHINA_MARKET_TZ).astimezone(UTC)

    @staticmethod
    def _normalize_scalar(value: object) -> object:
        item = value.item() if hasattr(value, "item") else value
        if isinstance(item, tuple):
            return list(item)
        return item


class MarketQuoteClient:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._mootdx_client = MootdxQuoteClient()
        self._tencent_client = TencentQuoteClient()
        self._tencent_cache: dict[str, tuple[TencentQuote, float]] = {}
        self._mootdx_failure_until = 0.0
        self._tencent_failure_until = 0.0
        self._history_failure_until = 0.0
        self._last_history_request_at = 0.0

    def fetch_quote(self, symbol: str) -> dict[str, dict[str, object]]:
        try:
            mootdx_quote = self._get_mootdx_quote(symbol)
        except Exception:
            tencent_quote = self._get_tencent_quote(symbol)
            logger.exception("mootdx primary quote fetch failed; falling back to tencent", extra={"symbol": symbol})
            return tencent_quote.to_market_state()

        tencent_quote = self._get_tencent_quote_or_none(symbol)
        if tencent_quote is not None:
            mootdx_quote = self._align_trade_day(mootdx_quote, tencent_quote)

        if not self._is_mootdx_quote_sane(mootdx_quote, tencent_quote):
            if tencent_quote is not None:
                logger.warning(
                    "mootdx quote diverged from enrichment source; falling back to tencent",
                    extra={
                        "symbol": symbol,
                        "mootdx_last_price": mootdx_quote.last_price,
                        "tencent_last_price": tencent_quote.last_price,
                    },
                )
                return tencent_quote.to_market_state()
            raise RuntimeError(f"mootdx quote failed validation for {symbol}")

        return self._build_market_state(mootdx_quote, tencent_quote)

    def fetch_daily_history(self, symbol: str, limit: int = 60) -> list[dict[str, object]]:
        self._wait_for_history_slot()
        try:
            return self._mootdx_client.fetch_daily_history(symbol, limit=limit)
        except Exception:
            self._history_failure_until = monotonic() + self._settings.collector_intraday_history_vendor_cooldown_seconds
            raise

    def fetch_intraday_history(self, symbol: str, trade_day: date | None = None) -> list[dict[str, object]]:
        self._wait_for_history_slot()
        try:
            return self._mootdx_client.fetch_intraday_history(symbol, trade_day=trade_day)
        except Exception:
            self._history_failure_until = monotonic() + self._settings.collector_intraday_history_vendor_cooldown_seconds
            raise

    def _wait_for_history_slot(self) -> None:
        now = monotonic()
        if now < self._history_failure_until:
            raise RuntimeError("mootdx history fetch is in cooldown")

        base_interval = float(self._settings.collector_intraday_history_request_interval_seconds)
        jitter = float(self._settings.collector_intraday_history_request_jitter_seconds)
        desired_interval = max(base_interval + random.uniform(-jitter, jitter), 0.0)
        elapsed = now - self._last_history_request_at
        if elapsed < desired_interval:
            sleep(desired_interval - elapsed)

        self._last_history_request_at = monotonic()

    def _get_mootdx_quote(self, symbol: str) -> MootdxQuote:
        now = monotonic()
        if now < self._mootdx_failure_until:
            raise RuntimeError("mootdx fetch is in cooldown")

        try:
            return self._mootdx_client.fetch_quote(symbol)
        except Exception:
            self._mootdx_failure_until = now + self._settings.collector_vendor_failure_cooldown_seconds
            raise

    def _get_tencent_quote(self, symbol: str) -> TencentQuote:
        now = monotonic()
        cached_entry = self._tencent_cache.get(symbol)
        if cached_entry is not None:
            cached_quote, cached_at = cached_entry
            if now - cached_at < self._settings.collector_tencent_enrichment_interval_seconds:
                return cached_quote

        if now < self._tencent_failure_until and cached_entry is not None:
            return cached_entry[0]

        try:
            quote = self._tencent_client.fetch_quote(symbol)
        except Exception:
            self._tencent_failure_until = now + self._settings.collector_vendor_failure_cooldown_seconds
            if cached_entry is not None:
                logger.exception("tencent enrichment fetch failed; reusing cached enrichment", extra={"symbol": symbol})
                return cached_entry[0]
            raise

        self._tencent_failure_until = 0.0
        self._tencent_cache[symbol] = (quote, now)
        return quote

    def _get_tencent_quote_or_none(self, symbol: str) -> TencentQuote | None:
        try:
            return self._get_tencent_quote(symbol)
        except Exception:
            logger.exception("tencent enrichment fetch failed; continuing with mootdx core quote", extra={"symbol": symbol})
            return None

    @staticmethod
    def _align_trade_day(mootdx_quote: MootdxQuote, tencent_quote: TencentQuote) -> MootdxQuote:
        trade_day = tencent_quote.updated_at.astimezone(CHINA_MARKET_TZ).date()
        quote_time = mootdx_quote.updated_at.astimezone(CHINA_MARKET_TZ).time()
        aligned_local = datetime.combine(trade_day, quote_time, tzinfo=CHINA_MARKET_TZ)
        aligned_updated_at = aligned_local.astimezone(UTC)
        aligned_daily_bucket = aligned_updated_at.replace(hour=0, minute=0, second=0, microsecond=0)
        return replace(mootdx_quote, updated_at=aligned_updated_at, daily_bucket=aligned_daily_bucket)

    def _is_mootdx_quote_sane(self, mootdx_quote: MootdxQuote, tencent_quote: TencentQuote | None) -> bool:
        if mootdx_quote.previous_close <= 0:
            return False

        price_fields = (
            mootdx_quote.last_price,
            mootdx_quote.open_price,
            mootdx_quote.high_price,
            mootdx_quote.low_price,
        )
        if any(value <= 0 for value in price_fields):
            return False

        if mootdx_quote.high_price < max(mootdx_quote.open_price, mootdx_quote.last_price):
            return False

        if mootdx_quote.low_price > min(mootdx_quote.open_price, mootdx_quote.last_price):
            return False

        if mootdx_quote.volume < 0 or mootdx_quote.amount < 0:
            return False

        if tencent_quote is None or tencent_quote.last_price <= 0:
            return True

        divergence_pct = abs(mootdx_quote.last_price - tencent_quote.last_price) / tencent_quote.last_price * 100
        return divergence_pct <= self._settings.collector_vendor_price_divergence_limit_pct

    @staticmethod
    def _build_market_state(mootdx_quote: MootdxQuote, tencent_quote: TencentQuote | None) -> dict[str, dict[str, object]]:
        change_pct = round(((mootdx_quote.last_price - mootdx_quote.previous_close) / mootdx_quote.previous_close) * 100, 4)
        company_name = tencent_quote.company_name if tencent_quote is not None else mootdx_quote.symbol
        composite_source = "mootdx+tencent-finance" if tencent_quote is not None else "mootdx"

        snapshot = {
            "symbol": mootdx_quote.symbol,
            "companyName": company_name,
            "exchange": mootdx_quote.exchange,
            "lastPrice": mootdx_quote.last_price,
            "changePct": change_pct,
            "pe": tencent_quote.pe if tencent_quote is not None else None,
            "pb": tencent_quote.pb if tencent_quote is not None else None,
            "turnoverRate": tencent_quote.turnover_rate if tencent_quote is not None else None,
            "marketCap": tencent_quote.market_cap if tencent_quote is not None else None,
            "limitUp": tencent_quote.limit_up if tencent_quote is not None else None,
            "limitDown": tencent_quote.limit_down if tencent_quote is not None else None,
            "updatedAt": mootdx_quote.updated_at.isoformat(),
            "source": composite_source,
        }

        tick = {
            "ts": mootdx_quote.updated_at,
            "symbol": mootdx_quote.symbol,
            "price": mootdx_quote.last_price,
            "volume": mootdx_quote.volume,
            "amount": mootdx_quote.amount,
            "side": "buy" if mootdx_quote.last_price >= mootdx_quote.previous_close else "sell",
            "source": "mootdx",
            "raw": {
                "provider": "mootdx",
                "providerVolumeUnit": "lots",
                "volumeUnit": "shares",
                "quote": mootdx_quote.raw,
                "previousClose": mootdx_quote.previous_close,
                "bid1": mootdx_quote.bid_price_1,
                "ask1": mootdx_quote.ask_price_1,
                "bidVolume1": mootdx_quote.bid_volume_1,
                "askVolume1": mootdx_quote.ask_volume_1,
            },
        }

        kline = {
            "bucketTs": mootdx_quote.daily_bucket,
            "symbol": mootdx_quote.symbol,
            "period": "1d",
            "open": mootdx_quote.open_price,
            "high": mootdx_quote.high_price,
            "low": mootdx_quote.low_price,
            "close": mootdx_quote.last_price,
            "volume": mootdx_quote.volume,
            "amount": mootdx_quote.amount,
            "source": "mootdx",
            "raw": {
                "provider": "mootdx",
                "providerVolumeUnit": "lots",
                "volumeUnit": "shares",
                "updatedAt": mootdx_quote.updated_at.isoformat(),
                "previousClose": mootdx_quote.previous_close,
            },
        }

        event = {
            "type": "market_update",
            "generatedAt": mootdx_quote.updated_at.isoformat(),
            "symbol": mootdx_quote.symbol,
            "companyName": company_name,
            "exchange": mootdx_quote.exchange,
            "snapshot": snapshot,
            "tick": {
                "price": mootdx_quote.last_price,
                "volume": mootdx_quote.volume,
                "volumeUnit": "shares",
                "side": tick["side"],
            },
            "kline": {
                "period": "1d",
                "close": mootdx_quote.last_price,
                "high": mootdx_quote.high_price,
                "low": mootdx_quote.low_price,
            },
        }

        return {
            "snapshot": snapshot,
            "tick": tick,
            "kline": kline,
            "event": event,
        }
