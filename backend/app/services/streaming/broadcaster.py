from __future__ import annotations

from datetime import UTC, datetime

from app.services.cache.redis_store import RedisStore


def _china_market_status(now_utc: datetime) -> tuple[str, bool]:
    from datetime import timedelta, timezone

    china_tz = timezone(timedelta(hours=8))
    china_now = now_utc.astimezone(china_tz)
    weekday = china_now.weekday()
    if weekday >= 5:
        return "closed", False

    current_minutes = china_now.hour * 60 + china_now.minute
    morning_open = 9 * 60 + 30
    morning_close = 11 * 60 + 30
    afternoon_open = 13 * 60
    afternoon_close = 15 * 60

    if morning_open <= current_minutes < morning_close or afternoon_open <= current_minutes < afternoon_close:
        return "trading", True
    if morning_close <= current_minutes < afternoon_open:
        return "break", False
    return "closed", False


class MarketBroadcaster:
    def __init__(self, redis_store: RedisStore) -> None:
        self._redis_store = redis_store

    async def build_heartbeat_event(self) -> dict[str, object]:
        active_symbols = await self._redis_store.get_active_symbols()
        snapshots = await self._redis_store.get_symbol_snapshots(active_symbols)
        latest_events = await self._redis_store.get_symbol_events(active_symbols)
        market_overview = await self._redis_store.get_market_overview()
        generated_at = datetime.now(UTC)
        market_status, is_trading_session = _china_market_status(generated_at)

        return {
            "type": "market_state",
            "generatedAt": generated_at.isoformat(),
            "marketStatus": market_status,
            "isTradingSession": is_trading_session,
            "serverGeneratedAt": generated_at.isoformat(),
            "activeSymbols": active_symbols,
            "snapshots": snapshots,
            "events": latest_events,
            "marketOverview": market_overview,
        }
