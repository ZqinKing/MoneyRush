from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from importlib import import_module
import logging
import random
from time import monotonic, sleep
from typing import Protocol, cast
from urllib.request import urlopen

from mootdx.quotes import Quotes

from collector.services.akshare_sector_client import AkshareSectorClient, StockSectorInfo
from collector.services.vendor_scheduler import VendorScheduler


QUOTE_URL = "https://qt.gtimg.cn/q="
CHINA_MARKET_TZ = timezone(timedelta(hours=8))
DEFAULT_MOOTDX_SERVER = ("180.153.18.170", 7709)
A_SHARE_LOT_SIZE = 100
QUOTE_SYMBOL_FAILURE_BACKOFF_SECONDS = 30.0
QUOTE_SERVER_FAILURE_BACKOFF_SECONDS = 10.0
HISTORY_FAILURE_BACKOFF_INITIAL_SECONDS = 60.0
HISTORY_FAILURE_BACKOFF_MAX_SECONDS = 300.0
SECTOR_CACHE_TTL_SECONDS = 6 * 60 * 60
SECTOR_FAILURE_CACHE_TTL_SECONDS = 30 * 60
SECTOR_FETCH_TIMEOUT_SECONDS = 10.0
AKSHARE_INTRADAY_SOURCE = "eastmoney-akshare"
MOOTDX_INTRADAY_SOURCE = "mootdx"
REALTIME_AGGREGATED_SOURCE = "realtime-quote-aggregate"
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
        normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=CHINA_MARKET_TZ).astimezone(UTC)
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
                "providerVolumeUnit": "shares",
                "volumeUnit": "shares",
                "currency": self.currency,
                "previousClose": self.previous_close,
                "open": self.open_price,
                "sideBasis": "price_vs_previous_close",
                "sideConfidence": "estimated",
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
                "providerVolumeUnit": "shares",
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
                "sideLabel": "高于/持平昨收" if tick["side"] == "buy" else "低于昨收" if tick["side"] == "sell" else "--",
                "sideBasis": "price_vs_previous_close",
                "sideConfidence": "estimated",
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


class DataFrameLike(Protocol):
    @property
    def empty(self) -> bool: ...

    def copy(self) -> "DataFrameLike": ...

    def to_dict(self, orient: str) -> list[dict[str, object]]: ...


class AkshareMinuteModule(Protocol):
    def stock_zh_a_hist_min_em(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str,
        adjust: str,
    ) -> DataFrameLike | None: ...


class AkshareMinuteClient:
    def __init__(self, akshare_module: AkshareMinuteModule | None = None) -> None:
        self._akshare = akshare_module or self._load_akshare()

    def fetch_intraday_history(self, symbol: str, trade_day: date) -> list[dict[str, object]]:
        start_date = f"{trade_day:%Y-%m-%d} 09:30:00"
        end_date = f"{trade_day:%Y-%m-%d} 15:00:00"
        frame = self._akshare.stock_zh_a_hist_min_em(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            period="1",
            adjust="",
        )
        if frame is None or frame.empty:
            return []

        normalized_frame = frame.copy()
        records: list[dict[str, object]] = []
        for row in normalized_frame.to_dict("records"):
            bucket_ts = _to_utc_intraday_bucket(
                row.get("时间")
                or row.get("datetime")
                or row.get("date")
                or row.get("time"),
                trade_day=trade_day,
            )
            open_price = _to_float(str(row.get("开盘") if row.get("开盘") is not None else row.get("open")))
            high_price = _to_float(str(row.get("最高") if row.get("最高") is not None else row.get("high")))
            low_price = _to_float(str(row.get("最低") if row.get("最低") is not None else row.get("low")))
            close_price = _to_float(str(row.get("收盘") if row.get("收盘") is not None else row.get("close")))
            volume = _to_int(str(row.get("成交量") if row.get("成交量") is not None else row.get("volume")))
            amount = _to_float(str(row.get("成交额") if row.get("成交额") is not None else row.get("amount")))
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
                    "source": AKSHARE_INTRADAY_SOURCE,
                    "raw": {
                        **row,
                        "provider": AKSHARE_INTRADAY_SOURCE,
                        "quality": "vendor_verified",
                        "synthetic": False,
                        "volumeUnit": "shares",
                    },
                }
            )

        deduped: dict[datetime, dict[str, object]] = {}
        for item in records:
            bucket_ts = item.get("bucketTs")
            if isinstance(bucket_ts, datetime):
                deduped[bucket_ts] = item
        return [deduped[key] for key in sorted(deduped.keys())]

    @staticmethod
    def _load_akshare() -> AkshareMinuteModule:
        module = import_module("akshare")
        return cast(AkshareMinuteModule, cast(object, module))


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
        volume = _to_int(parts[36])
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

    def reset(self) -> None:
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
            bucket_ts = item.get("bucketTs")
            if isinstance(bucket_ts, datetime):
                deduped[bucket_ts] = item
        return [deduped[key] for key in sorted(deduped.keys(), reverse=True)[:limit]]

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
                        "quality": "vendor_verified",
                        "synthetic": False,
                        "providerVolumeUnit": "lots",
                        "volumeUnit": "shares",
                    },
                }
            )

        deduped: dict[datetime, dict[str, object]] = {}
        for item in records:
            bucket_ts = item.get("bucketTs")
            if isinstance(bucket_ts, datetime):
                deduped[bucket_ts] = item
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
        self._mootdx_quote_client = MootdxQuoteClient()
        self._mootdx_history_client = MootdxQuoteClient()
        self._akshare_minute_client = AkshareMinuteClient()
        self._tencent_client = TencentQuoteClient()
        self._vendor_scheduler = VendorScheduler()
        self._sector_client: AkshareSectorClient | None = None
        self._sector_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sector-enrichment")
        self._tencent_cache: dict[str, tuple[TencentQuote, float]] = {}
        self._sector_cache: dict[str, tuple[dict[str, object] | None, float]] = {}
        self._quote_symbol_failure_until: dict[str, float] = {}
        self._quote_server_failure_until = 0.0
        self._tencent_failure_until = 0.0
        self._sector_failure_until = 0.0
        self._history_failure_until_by_key: dict[tuple[str, str, str], float] = {}
        self._history_failure_count_by_key: dict[tuple[str, str, str], int] = {}
        self._last_history_request_at = 0.0

    def fetch_quote(self, symbol: str) -> dict[str, dict[str, object]]:
        try:
            mootdx_quote = self._get_mootdx_quote(symbol)
        except Exception:
            tencent_quote = self._get_tencent_quote(symbol)
            logger.exception("mootdx primary quote fetch failed; falling back to tencent", extra={"symbol": symbol})
            market_state = tencent_quote.to_market_state()
            self._attach_sector_info(market_state, symbol)
            return market_state

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
                market_state = tencent_quote.to_market_state()
                self._attach_sector_info(market_state, symbol)
                return market_state
            raise RuntimeError(f"mootdx quote failed validation for {symbol}")

        market_state = self._build_market_state(mootdx_quote, tencent_quote)
        self._attach_sector_info(market_state, symbol)
        return market_state

    def fetch_daily_history(self, symbol: str, limit: int = 60) -> list[dict[str, object]]:
        trade_day = datetime.now(CHINA_MARKET_TZ).date()
        self._raise_if_history_backoff_active(symbol, trade_day, "daily")
        self._wait_for_history_slot()
        try:
            history = self._mootdx_history_client.fetch_daily_history(symbol, limit=limit)
        except Exception as exc:
            self._record_history_failure(
                symbol,
                trade_day,
                scope="daily",
                reason=self._classify_mootdx_failure(exc),
                transport_failure=self._is_transport_failure(exc),
            )
            raise

        if not history:
            self._record_history_failure(
                symbol,
                trade_day,
                scope="daily",
                reason="history_empty",
                transport_failure=False,
            )
            raise ValueError(f"empty mootdx daily history payload for {symbol} on {trade_day.isoformat()}")

        self._clear_history_backoff(symbol, trade_day, "daily")
        return history

    def fetch_intraday_history(self, symbol: str, trade_day: date | None = None) -> list[dict[str, object]]:
        trade_day = trade_day or datetime.now(CHINA_MARKET_TZ).date()
        errors: list[str] = []
        for source, fetch in (
            ("eastmoney-push2his", lambda: self._akshare_minute_client.fetch_intraday_history(symbol, trade_day)),
            ("mootdx", lambda: self._mootdx_history_client.fetch_intraday_history(symbol, trade_day=trade_day)),
        ):
            try:
                self._vendor_scheduler.raise_if_symbol_source_cooldown_active(source, symbol, "intraday")
            except RuntimeError as exc:
                reason = "symbol_cooldown"
                errors.append(f"{source}:{reason}")
                logger.info(
                    "intraday minute source skipped due to symbol cooldown",
                    extra={"symbol": symbol, "trade_day": trade_day.isoformat(), "source": source, "reason": str(exc)},
                )
                continue

            try:
                self._vendor_scheduler.wait_for_slot(source)
            except RuntimeError as exc:
                reason = "source_cooldown"
                errors.append(f"{source}:{reason}")
                logger.info(
                    "intraday minute source skipped due to source cooldown",
                    extra={"symbol": symbol, "trade_day": trade_day.isoformat(), "source": source, "reason": str(exc)},
                )
                continue

            try:
                history = fetch()
            except Exception as exc:
                reason = self._classify_mootdx_failure(exc) if source == "mootdx" else self._classify_generic_vendor_failure(exc)
                errors.append(f"{source}:{reason}")
                self._vendor_scheduler.record_failure(source, reason=reason)
                self._vendor_scheduler.record_symbol_source_failure(source, symbol, "intraday", cooldown_seconds=HISTORY_FAILURE_BACKOFF_INITIAL_SECONDS, reason=reason)
                logger.exception("intraday minute source failed; trying next source", extra={"symbol": symbol, "trade_day": trade_day.isoformat(), "source": source, "reason": reason})
                continue

            if history:
                self._vendor_scheduler.record_success(source)
                return history

            errors.append(f"{source}:history_empty")
            self._vendor_scheduler.record_symbol_source_failure(source, symbol, "intraday", cooldown_seconds=HISTORY_FAILURE_BACKOFF_INITIAL_SECONDS, reason="history_empty")
            logger.warning("intraday minute source returned no rows; trying next source", extra={"symbol": symbol, "trade_day": trade_day.isoformat(), "source": source})

        raise ValueError(f"empty intraday history payload for {symbol} on {trade_day.isoformat()} after sources {', '.join(errors)}")

    def _wait_for_history_slot(self) -> None:
        now = monotonic()
        base_interval = float(self._settings.collector_intraday_history_request_interval_seconds)
        jitter = float(self._settings.collector_intraday_history_request_jitter_seconds)
        desired_interval = max(base_interval + random.uniform(-jitter, jitter), 0.0)
        elapsed = now - self._last_history_request_at
        if elapsed < desired_interval:
            sleep(desired_interval - elapsed)

        self._last_history_request_at = monotonic()

    def source_health_snapshot(self) -> dict[str, dict[str, object]]:
        return self._vendor_scheduler.health_snapshot()

    def _get_mootdx_quote(self, symbol: str) -> MootdxQuote:
        now = monotonic()
        symbol_failure_until = self._quote_symbol_failure_until.get(symbol, 0.0)
        if now < symbol_failure_until:
            logger.info(
                "mootdx quote skipped due to symbol-scoped cooldown",
                extra={
                    "symbol": symbol,
                    "cooldown_scope": "symbol",
                    "cooldown_reason": "content_error",
                    "cooldown_remaining_seconds": round(symbol_failure_until - now, 2),
                },
            )
            raise RuntimeError("mootdx quote is in symbol cooldown")

        if now < self._quote_server_failure_until:
            logger.info(
                "mootdx quote skipped due to server-scoped cooldown",
                extra={
                    "symbol": symbol,
                    "cooldown_scope": "server",
                    "cooldown_reason": "transport_error",
                    "cooldown_remaining_seconds": round(self._quote_server_failure_until - now, 2),
                },
            )
            raise RuntimeError("mootdx quote is in server cooldown")

        try:
            quote = self._mootdx_quote_client.fetch_quote(symbol)
        except Exception as exc:
            reason = self._classify_mootdx_failure(exc)
            if self._is_transport_failure(exc):
                cooldown_seconds = min(
                    float(self._settings.collector_vendor_failure_cooldown_seconds),
                    QUOTE_SERVER_FAILURE_BACKOFF_SECONDS,
                )
                self._quote_server_failure_until = now + cooldown_seconds
                self._mootdx_quote_client.reset()
                logger.exception(
                    "mootdx quote transport failure; applying server-scoped cooldown",
                    extra={
                        "symbol": symbol,
                        "cooldown_scope": "server",
                        "cooldown_reason": reason,
                        "cooldown_seconds": cooldown_seconds,
                        "client_reset": "quote",
                    },
                )
            else:
                cooldown_seconds = min(
                    float(self._settings.collector_vendor_failure_cooldown_seconds),
                    QUOTE_SYMBOL_FAILURE_BACKOFF_SECONDS,
                )
                self._quote_symbol_failure_until[symbol] = now + cooldown_seconds
                logger.exception(
                    "mootdx quote content failure; applying symbol-scoped cooldown",
                    extra={
                        "symbol": symbol,
                        "cooldown_scope": "symbol",
                        "cooldown_reason": reason,
                        "cooldown_seconds": cooldown_seconds,
                    },
                )
            raise

        self._quote_symbol_failure_until.pop(symbol, None)
        self._quote_server_failure_until = 0.0
        return quote

    def _raise_if_history_backoff_active(self, symbol: str, trade_day: date, scope: str) -> None:
        key = (symbol, trade_day.isoformat(), scope)
        now = monotonic()
        failure_until = self._history_failure_until_by_key.get(key, 0.0)
        if now < failure_until:
            logger.info(
                "mootdx history skipped due to symbol/trade-day cooldown",
                extra={
                    "symbol": symbol,
                    "trade_day": trade_day.isoformat(),
                    "history_scope": scope,
                    "cooldown_scope": "symbol_trade_day",
                    "cooldown_reason": "history_backoff",
                    "cooldown_remaining_seconds": round(failure_until - now, 2),
                },
            )
            raise RuntimeError("mootdx history is in symbol/trade-day cooldown")

    def _record_history_failure(
        self,
        symbol: str,
        trade_day: date,
        *,
        scope: str,
        reason: str,
        transport_failure: bool,
    ) -> None:
        key = (symbol, trade_day.isoformat(), scope)
        failure_count = self._history_failure_count_by_key.get(key, 0) + 1
        self._history_failure_count_by_key[key] = failure_count
        backoff_seconds = min(
            HISTORY_FAILURE_BACKOFF_INITIAL_SECONDS * (2 ** (failure_count - 1)),
            HISTORY_FAILURE_BACKOFF_MAX_SECONDS,
        )
        self._history_failure_until_by_key[key] = monotonic() + backoff_seconds
        if transport_failure:
            self._mootdx_history_client.reset()

        logger.warning(
            "mootdx history fetch failed; applying symbol/trade-day cooldown",
            extra={
                "symbol": symbol,
                "trade_day": trade_day.isoformat(),
                "history_scope": scope,
                "cooldown_scope": "symbol_trade_day",
                "cooldown_reason": reason,
                "cooldown_seconds": backoff_seconds,
                "failure_count": failure_count,
                "client_reset": "history" if transport_failure else None,
            },
        )

    def _clear_history_backoff(self, symbol: str, trade_day: date, scope: str) -> None:
        key = (symbol, trade_day.isoformat(), scope)
        self._history_failure_until_by_key.pop(key, None)
        self._history_failure_count_by_key.pop(key, None)

    @staticmethod
    def _is_transport_failure(exc: Exception) -> bool:
        if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
            return True
        message = str(exc).lower()
        return any(token in message for token in ("timeout", "timed out", "reconnect", "socket", "connection", "server unavailable"))

    @staticmethod
    def _classify_mootdx_failure(exc: Exception) -> str:
        if isinstance(exc, ValueError):
            message = str(exc).lower()
            if "empty" in message:
                return "empty_payload"
            return "value_error"
        if isinstance(exc, TypeError):
            return "type_error"
        if isinstance(exc, (IndexError, KeyError)):
            return "parse_error"
        if MarketQuoteClient._is_transport_failure(exc):
            return "transport_error"
        return "unknown_error"

    @staticmethod
    def _classify_generic_vendor_failure(exc: Exception) -> str:
        message = str(exc).lower()
        if "403" in message or "forbidden" in message:
            return "forbidden"
        if "429" in message or "too many" in message:
            return "rate_limited"
        if MarketQuoteClient._is_transport_failure(exc):
            return "transport_error"
        if isinstance(exc, ValueError) and "empty" in message:
            return "empty_payload"
        if isinstance(exc, (IndexError, KeyError, TypeError)):
            return "parse_error"
        return "unknown_error"

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

    def _attach_sector_info(self, market_state: dict[str, dict[str, object]], symbol: str) -> None:
        sector_info = self._get_sector_info_or_none(symbol)
        if sector_info is None:
            return

        snapshot = market_state.get("snapshot")
        if isinstance(snapshot, dict):
            snapshot["sector"] = sector_info

        event = market_state.get("event")
        event_snapshot = event.get("snapshot") if isinstance(event, dict) else None
        if isinstance(event_snapshot, dict):
            event_snapshot["sector"] = sector_info

    def _get_sector_info_or_none(self, symbol: str) -> dict[str, object] | None:
        now = monotonic()
        cached_entry = self._sector_cache.get(symbol)
        if cached_entry is not None and now - cached_entry[1] < SECTOR_CACHE_TTL_SECONDS:
            return cached_entry[0]

        if now < self._sector_failure_until:
            return cached_entry[0] if cached_entry is not None else None

        try:
            sector_info = self._fetch_sector_info_with_timeout(symbol)
        except Exception:
            self._sector_failure_until = now + SECTOR_FAILURE_CACHE_TTL_SECONDS
            if cached_entry is not None:
                logger.exception("akshare sector fetch failed; reusing cached sector info", extra={"symbol": symbol})
                return cached_entry[0]
            logger.exception("akshare sector fetch failed; continuing without sector info", extra={"symbol": symbol})
            self._sector_cache[symbol] = (None, now - SECTOR_CACHE_TTL_SECONDS + SECTOR_FAILURE_CACHE_TTL_SECONDS)
            return None

        self._sector_failure_until = 0.0
        payload = sector_info.to_payload() if sector_info is not None else None
        self._sector_cache[symbol] = (payload, now)
        return payload

    def _fetch_sector_info_with_timeout(self, symbol: str) -> StockSectorInfo | None:
        sector_client = self._get_sector_client()
        future = self._sector_executor.submit(sector_client.fetch_sector_info, symbol)
        try:
            return future.result(timeout=SECTOR_FETCH_TIMEOUT_SECONDS)
        except FutureTimeoutError:
            future.cancel()
            raise TimeoutError(f"akshare sector fetch timed out after {SECTOR_FETCH_TIMEOUT_SECONDS:.1f}s")

    def _get_sector_client(self) -> AkshareSectorClient:
        if self._sector_client is None:
            self._sector_client = AkshareSectorClient()
        return self._sector_client

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
                "sideBasis": "price_vs_previous_close",
                "sideConfidence": "estimated",
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
                "sideLabel": "高于/持平昨收" if tick["side"] == "buy" else "低于昨收" if tick["side"] == "sell" else "--",
                "sideBasis": "price_vs_previous_close",
                "sideConfidence": "estimated",
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
