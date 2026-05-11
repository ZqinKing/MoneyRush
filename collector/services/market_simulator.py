from __future__ import annotations

from datetime import UTC, datetime


def _symbol_seed(symbol: str) -> int:
    return sum(ord(character) for character in symbol)


def build_market_state(symbol: str, step: int) -> dict[str, dict[str, object]]:
    now = datetime.now(UTC)
    minute_bucket = now.replace(second=0, microsecond=0)
    seed = _symbol_seed(symbol)

    base_price = 8 + (seed % 37) + ((seed % 100) / 100)
    drift = ((step % 9) - 4) * 0.07
    last_price = round(base_price + drift, 4)
    previous_close = round(base_price - 0.12, 4)
    open_price = round(base_price - 0.05, 4)
    high_price = round(max(open_price, last_price) + 0.18, 4)
    low_price = round(min(open_price, last_price) - 0.16, 4)
    change_pct = round(((last_price - previous_close) / previous_close) * 100, 4)
    volume = 10_000 + step * 250 + (seed % 1_000)
    amount = round(volume * last_price, 2)
    limit_up = round(previous_close * 1.1, 4)
    limit_down = round(previous_close * 0.9, 4)
    turnover_rate = round(0.45 + (step % 8) * 0.08, 4)
    market_cap = round((seed % 500 + 500) * 1_000_000 * last_price, 2)

    snapshot = {
        "symbol": symbol,
        "lastPrice": last_price,
        "changePct": change_pct,
        "pe": round(8 + (seed % 20) * 0.9, 4),
        "pb": round(0.9 + (seed % 10) * 0.16, 4),
        "turnoverRate": turnover_rate,
        "marketCap": market_cap,
        "limitUp": limit_up,
        "limitDown": limit_down,
        "updatedAt": now.isoformat(),
        "source": "simulated-market-loop",
    }

    tick = {
        "ts": now,
        "symbol": symbol,
        "price": last_price,
        "volume": volume,
        "amount": amount,
        "side": "buy" if step % 2 == 0 else "sell",
        "source": "simulated-market-loop",
        "raw": {
            "step": step,
            "generatedAt": now.isoformat(),
        },
    }

    kline = {
        "bucketTs": minute_bucket,
        "symbol": symbol,
        "period": "1m",
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": last_price,
        "volume": volume,
        "amount": amount,
        "source": "simulated-market-loop",
        "raw": {
            "step": step,
            "generatedAt": now.isoformat(),
        },
    }

    event = {
        "type": "market_update",
        "generatedAt": now.isoformat(),
        "symbol": symbol,
        "snapshot": snapshot,
        "tick": {
            "price": last_price,
            "volume": volume,
            "side": tick["side"],
        },
        "kline": {
            "period": "1m",
            "close": last_price,
            "high": high_price,
            "low": low_price,
        },
    }

    return {
        "snapshot": snapshot,
        "tick": tick,
        "kline": kline,
        "event": event,
    }
