from datetime import UTC, date, datetime, timedelta, timezone

from collector.services.tencent_quote_client import MarketQuoteClient


CHINA_MARKET_TZ = timezone(timedelta(hours=8))


class CooldownThenSuccessScheduler:
    def __init__(self, *, source_cooldown: bool = False, eastmoney_symbol_cooldown: bool = True) -> None:
        self.failure_count: int = 0
        self.symbol_failure_count: int = 0
        self.waited_sources: list[str] = []
        self.success_sources: list[str] = []
        self.source_cooldown: bool = source_cooldown
        self.eastmoney_symbol_cooldown: bool = eastmoney_symbol_cooldown

    def raise_if_symbol_source_cooldown_active(self, source: str, symbol: str, scope: str) -> None:
        if source == "eastmoney-push2his" and self.eastmoney_symbol_cooldown and not self.source_cooldown:
            raise RuntimeError(f"{source} is in symbol cooldown for {symbol}/{scope}")

    def wait_for_slot(self, source: str) -> None:
        self.waited_sources.append(source)
        if self.source_cooldown and source == "eastmoney-push2his":
            raise RuntimeError(f"{source} is in source cooldown")

    def record_failure(self, _source: str, *, reason: str, status_code: int | None = None) -> None:
        _ = reason, status_code
        self.failure_count += 1

    def record_symbol_source_failure(self, _source: str, _symbol: str, _scope: str, *, cooldown_seconds: float, reason: str) -> None:
        _ = cooldown_seconds, reason
        self.symbol_failure_count += 1

    def record_success(self, source: str) -> None:
        self.success_sources.append(source)


class FailingMinuteClient:
    def fetch_intraday_history(self, _symbol: str, _trade_day: date) -> list[dict[str, object]]:
        raise AssertionError("cooldown source should not be fetched")


class SuccessfulMinuteClient:
    def fetch_intraday_history(self, symbol: str, trade_day: date | None = None) -> list[dict[str, object]]:
        _ = trade_day
        return [
            {
                "bucketTs": datetime(2026, 6, 26, 1, 30, tzinfo=UTC),
                "symbol": symbol,
                "period": "1m",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 100,
                "amount": 1000.0,
                "source": "mootdx",
                "raw": {"synthetic": False},
            }
        ]


class CompleteMinuteClient:
    def fetch_intraday_history(self, symbol: str, trade_day: date | None = None) -> list[dict[str, object]]:
        trade_day = trade_day or date(2026, 6, 26)
        rows: list[dict[str, object]] = []
        for hour, minute_count in ((9, 120), (13, 120)):
            start_minute = 30 if hour == 9 else 0
            for offset in range(minute_count):
                bucket_ts = datetime(trade_day.year, trade_day.month, trade_day.day, hour, start_minute, tzinfo=CHINA_MARKET_TZ) + timedelta(minutes=offset)
                rows.append(
                    {
                        "bucketTs": bucket_ts.astimezone(UTC),
                        "symbol": symbol,
                        "period": "1m",
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "close": 10.0,
                        "volume": 100,
                        "amount": 1000.0,
                        "source": "mootdx",
                        "raw": {"synthetic": False},
                    }
                )
        return rows


class EmptyMinuteClient:
    def fetch_intraday_history(self, _symbol: str, trade_day: date | None = None) -> list[dict[str, object]]:
        _ = trade_day
        return []


class RaisingMinuteClient:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def fetch_intraday_history(self, _symbol: str, trade_day: date | None = None) -> list[dict[str, object]]:
        _ = trade_day
        raise RuntimeError(self.reason)


def test_intraday_symbol_cooldown_does_not_record_new_vendor_failure() -> None:
    client = MarketQuoteClient.__new__(MarketQuoteClient)
    scheduler = CooldownThenSuccessScheduler()
    setattr(client, "_vendor_scheduler", scheduler)
    setattr(client, "_akshare_minute_client", FailingMinuteClient())
    setattr(client, "_mootdx_history_client", CompleteMinuteClient())

    history = client.fetch_intraday_history("000001", date(2026, 6, 26))

    assert len(history) == 240
    assert scheduler.waited_sources == ["mootdx"]
    assert scheduler.success_sources == ["mootdx"]
    assert scheduler.failure_count == 0
    assert scheduler.symbol_failure_count == 0


def test_intraday_source_cooldown_does_not_record_new_vendor_failure() -> None:
    client = MarketQuoteClient.__new__(MarketQuoteClient)
    scheduler = CooldownThenSuccessScheduler(source_cooldown=True)
    setattr(client, "_vendor_scheduler", scheduler)
    setattr(client, "_akshare_minute_client", FailingMinuteClient())
    setattr(client, "_mootdx_history_client", CompleteMinuteClient())

    history = client.fetch_intraday_history("000001", date(2026, 6, 26))

    assert len(history) == 240
    assert scheduler.waited_sources == ["mootdx"]
    assert scheduler.success_sources == ["mootdx"]
    assert scheduler.failure_count == 0
    assert scheduler.symbol_failure_count == 0


def test_intraday_falls_back_to_akshare_when_mootdx_is_empty() -> None:
    client = MarketQuoteClient.__new__(MarketQuoteClient)
    scheduler = CooldownThenSuccessScheduler(eastmoney_symbol_cooldown=False)
    setattr(client, "_vendor_scheduler", scheduler)
    setattr(client, "_mootdx_history_client", EmptyMinuteClient())
    setattr(client, "_akshare_minute_client", SuccessfulMinuteClient())

    history = client.fetch_intraday_history("000001", date(2026, 6, 26))

    assert len(history) == 1
    assert scheduler.waited_sources == ["mootdx", "eastmoney-push2his"]
    assert scheduler.success_sources == ["eastmoney-push2his"]
    assert scheduler.failure_count == 0
    assert scheduler.symbol_failure_count == 1


def test_intraday_falls_back_to_akshare_when_historical_mootdx_is_incomplete() -> None:
    client = MarketQuoteClient.__new__(MarketQuoteClient)
    scheduler = CooldownThenSuccessScheduler(eastmoney_symbol_cooldown=False)
    setattr(client, "_vendor_scheduler", scheduler)
    setattr(client, "_mootdx_history_client", SuccessfulMinuteClient())
    setattr(client, "_akshare_minute_client", CompleteMinuteClient())

    history = client.fetch_intraday_history("000001", date(2026, 6, 26))

    assert len(history) == 240
    assert scheduler.waited_sources == ["mootdx", "eastmoney-push2his"]
    assert scheduler.success_sources == ["eastmoney-push2his"]
    assert scheduler.failure_count == 0
    assert scheduler.symbol_failure_count == 1


def test_intraday_falls_back_to_akshare_when_mootdx_fails() -> None:
    client = MarketQuoteClient.__new__(MarketQuoteClient)
    scheduler = CooldownThenSuccessScheduler(eastmoney_symbol_cooldown=False)
    setattr(client, "_vendor_scheduler", scheduler)
    setattr(client, "_mootdx_history_client", RaisingMinuteClient("transport closed"))
    setattr(client, "_akshare_minute_client", SuccessfulMinuteClient())

    history = client.fetch_intraday_history("000001", date(2026, 6, 26))

    assert len(history) == 1
    assert scheduler.waited_sources == ["mootdx", "eastmoney-push2his"]
    assert scheduler.success_sources == ["eastmoney-push2his"]
    assert scheduler.failure_count == 1
    assert scheduler.symbol_failure_count == 1


def test_intraday_empty_sources_record_symbol_source_failures() -> None:
    client = MarketQuoteClient.__new__(MarketQuoteClient)
    scheduler = CooldownThenSuccessScheduler()
    setattr(client, "_vendor_scheduler", scheduler)
    setattr(client, "_akshare_minute_client", EmptyMinuteClient())
    setattr(client, "_mootdx_history_client", EmptyMinuteClient())

    try:
        _ = client.fetch_intraday_history("000001", date(2026, 6, 26))
    except ValueError as exc:
        assert "mootdx:history_empty" in str(exc)
        assert "eastmoney-push2his:symbol_cooldown" in str(exc)
    else:
        raise AssertionError("empty intraday sources should fail")

    assert scheduler.waited_sources == ["mootdx"]
    assert scheduler.failure_count == 0
    assert scheduler.symbol_failure_count == 1
