from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
import logging
from time import monotonic
from urllib.request import urlopen

from mootdx.quotes import Quotes


QUOTE_URL = "https://qt.gtimg.cn/q="
CHINA_MARKET_TZ = timezone(timedelta(hours=8))
DEFAULT_MOOTDX_SERVER = ("180.153.18.170", 7709)
logger = logging.getLogger(__name__)


def _symbol_to_vendor_code(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return f"sh{symbol}"
    return f"sz{symbol}"


def _infer_exchange(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


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


def _parse_timestamp(value: str) -> datetime:
    if len(value) == 14 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_MARKET_TZ).astimezone(UTC)
    return datetime.now(UTC)


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

        return MootdxQuote(
            symbol=symbol,
            exchange=_infer_exchange(symbol),
            last_price=float(quote_row["price"]),
            previous_close=float(quote_row["last_close"]),
            open_price=float(quote_row["open"]),
            high_price=float(quote_row["high"]),
            low_price=float(quote_row["low"]),
            volume=int(float(quote_row["volume"])),
            amount=float(quote_row["amount"]),
            updated_at=updated_at,
            bid_price_1=_to_float(str(quote_row.get("bid1"))),
            ask_price_1=_to_float(str(quote_row.get("ask1"))),
            bid_volume_1=_to_int(str(quote_row.get("bid_vol1"))),
            ask_volume_1=_to_int(str(quote_row.get("ask_vol1"))),
            raw={key: self._normalize_scalar(value) for key, value in quote_row.to_dict().items()},
            daily_bucket=daily_bucket,
        )

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

    def fetch_quote(self, symbol: str) -> dict[str, dict[str, object]]:
        tencent_quote = self._get_tencent_quote(symbol)

        try:
            mootdx_quote = self._get_mootdx_quote(symbol)
        except Exception:
            logger.exception("mootdx primary quote fetch failed; falling back to tencent", extra={"symbol": symbol})
            return tencent_quote.to_market_state()

        return self._build_market_state(mootdx_quote, tencent_quote)

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

    @staticmethod
    def _build_market_state(mootdx_quote: MootdxQuote, tencent_quote: TencentQuote) -> dict[str, dict[str, object]]:
        change_pct = round(((mootdx_quote.last_price - mootdx_quote.previous_close) / mootdx_quote.previous_close) * 100, 4)

        snapshot = {
            "symbol": mootdx_quote.symbol,
            "companyName": tencent_quote.company_name,
            "exchange": mootdx_quote.exchange,
            "lastPrice": mootdx_quote.last_price,
            "changePct": change_pct,
            "pe": tencent_quote.pe,
            "pb": tencent_quote.pb,
            "turnoverRate": tencent_quote.turnover_rate,
            "marketCap": tencent_quote.market_cap,
            "limitUp": tencent_quote.limit_up,
            "limitDown": tencent_quote.limit_down,
            "updatedAt": mootdx_quote.updated_at.isoformat(),
            "source": "mootdx+tencent-finance",
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
                "updatedAt": mootdx_quote.updated_at.isoformat(),
                "previousClose": mootdx_quote.previous_close,
            },
        }

        event = {
            "type": "market_update",
            "generatedAt": mootdx_quote.updated_at.isoformat(),
            "symbol": mootdx_quote.symbol,
            "companyName": tencent_quote.company_name,
            "exchange": mootdx_quote.exchange,
            "snapshot": snapshot,
            "tick": {
                "price": mootdx_quote.last_price,
                "volume": mootdx_quote.volume,
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
