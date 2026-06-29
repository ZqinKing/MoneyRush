from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import collector.services.tencent_quote_client as quote_client


MootdxQuoteClient = quote_client.MootdxQuoteClient
MootdxQuote = quote_client.MootdxQuote
MootdxServer = quote_client.MootdxServer
TencentQuote = quote_client.TencentQuote
ensure_mootdx_config_file = cast(Callable[[Path], Path], getattr(quote_client, "ensure_mootdx_config_file"))
parse_mootdx_servers = cast(Callable[[object], tuple[MootdxServer, ...]], getattr(quote_client, "parse_mootdx_servers"))
ensure_client = cast(Callable[[MootdxQuoteClient], object], getattr(MootdxQuoteClient, "ensure_client"))
combine_quote_timestamp = cast(Callable[[object], datetime], getattr(MootdxQuoteClient, "_combine_quote_timestamp"))
align_trade_day = cast(Callable[[MootdxQuote, TencentQuote], MootdxQuote], getattr(quote_client.MarketQuoteClient, "_align_trade_day"))


def test_parse_mootdx_servers_accepts_comma_separated_endpoints() -> None:
    assert parse_mootdx_servers("180.153.18.170:7709, 202.108.253.131:7709") == (
        ("180.153.18.170", 7709),
        ("202.108.253.131", 7709),
    )


def test_parse_mootdx_servers_rejects_invalid_endpoints() -> None:
    for value in ("", "180.153.18.170", "180.153.18.170:http", "180.153.18.170:0", ":7709"):
        try:
            _ = parse_mootdx_servers(value)
        except ValueError:
            continue
        raise AssertionError(f"expected invalid endpoint to fail: {value}")


def test_mootdx_config_bootstrap_creates_missing_config(tmp_path: Path) -> None:
    config_path = tmp_path / ".mootdx" / "config.json"

    assert ensure_mootdx_config_file(config_path) == config_path
    assert config_path.read_text(encoding="utf-8") == "{}"


def test_mootdx_config_bootstrap_preserves_existing_config(tmp_path: Path) -> None:
    config_path = tmp_path / ".mootdx" / "config.json"
    config_path.parent.mkdir(parents=True)
    existing = '{"BESTIP":{"HQ":["1.2.3.4",7709]}}'
    _ = config_path.write_text(existing, encoding="utf-8")

    assert ensure_mootdx_config_file(config_path) == config_path
    assert config_path.read_text(encoding="utf-8") == existing


def test_mootdx_client_tries_next_server_when_factory_fails(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []
    working_client = object()

    def factory(**kwargs: object) -> object:
        server = cast(MootdxServer, kwargs["server"])
        calls.append(server)
        if server[0] == "bad.example.com":
            raise OSError("server unavailable")
        return working_client

    client = MootdxQuoteClient(
        (("bad.example.com", 7709), ("good.example.com", 7709)),
        quotes_factory=factory,
        config_path=tmp_path / ".mootdx" / "config.json",
    )

    assert ensure_client(client) is working_client
    assert calls == [("bad.example.com", 7709), ("good.example.com", 7709)]


def test_mootdx_client_rotates_starting_server_after_reset(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []

    def factory(**kwargs: object) -> object:
        calls.append(cast(MootdxServer, kwargs["server"]))
        return object()

    client = MootdxQuoteClient(
        (("first.example.com", 7709), ("second.example.com", 7709)),
        quotes_factory=factory,
        config_path=tmp_path / ".mootdx" / "config.json",
    )

    _ = ensure_client(client)
    client.reset(rotate_server=True)
    _ = ensure_client(client)

    assert calls == [("first.example.com", 7709), ("second.example.com", 7709)]


def test_mootdx_quote_timestamp_accepts_placeholder_servertime() -> None:
    before = datetime.now(UTC) - timedelta(seconds=1)

    parsed = combine_quote_timestamp("0")

    after = datetime.now(UTC) + timedelta(seconds=1)
    assert before <= parsed <= after


def test_mootdx_alignment_keeps_newer_mootdx_trade_day_when_tencent_is_stale() -> None:
    mootdx_updated_at = datetime(2026, 6, 29, 7, 15, tzinfo=UTC)
    mootdx_quote = MootdxQuote(
        symbol="000001",
        exchange="SZ",
        last_price=10.0,
        previous_close=9.8,
        open_price=9.9,
        high_price=10.1,
        low_price=9.7,
        volume=1000,
        amount=10000.0,
        updated_at=mootdx_updated_at,
        bid_price_1=None,
        ask_price_1=None,
        bid_volume_1=None,
        ask_volume_1=None,
        raw={},
        daily_bucket=datetime(2026, 6, 29, tzinfo=UTC),
    )
    tencent_quote = TencentQuote(
        symbol="000001",
        company_name="平安银行",
        exchange="SZ",
        last_price=10.0,
        previous_close=9.8,
        open_price=9.9,
        high_price=10.1,
        low_price=9.7,
        volume=1000,
        amount=10000.0,
        change_pct=2.0,
        turnover_rate=None,
        pe=None,
        pb=None,
        market_cap=None,
        limit_up=None,
        limit_down=None,
        updated_at=datetime(2026, 6, 26, 7, 0, tzinfo=UTC),
        currency=None,
    )

    aligned = align_trade_day(mootdx_quote, tencent_quote)

    assert aligned.updated_at == mootdx_updated_at
