from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import asyncpg


BEIJING_TZ = timezone(timedelta(hours=8))
DISPLAY_TIMEZONE = "Asia/Shanghai"
TIMED_EVENT_ACTIVE_WINDOW = timedelta(hours=2)


TIMELINE_EVENT_SEEDS = [
    {
        "id": "seed-fomc-2026-06",
        "event_date": date(2026, 6, 16),
        "end_date": date(2026, 6, 17),
        "title": "FOMC 利率决议 + SEP",
        "category": "fomc",
        "impact_assets": ["UST", "USD", "Gold", "BTC", "成长股"],
        "level": "high",
        "source": "seed",
        "description": "美联储议息会议与经济预测摘要，作为未来风险时间轴的 MVP 种子事件。",
    },
    {
        "id": "seed-options-etf-2026-06",
        "event_date": date(2026, 6, 18),
        "end_date": None,
        "title": "ETF/期权月度到期",
        "category": "options",
        "impact_assets": ["美股ETF", "指数", "对冲链条"],
        "level": "high",
        "source": "seed",
        "description": "月度到期窗口可能放大指数、ETF 与对冲链条波动。",
    },
    {
        "id": "seed-cme-btc-2026-06",
        "event_date": date(2026, 6, 26),
        "end_date": None,
        "title": "CME BTC 6月合约结算",
        "category": "crypto",
        "impact_assets": ["BTC", "ETH", "矿股", "加密主题ETF"],
        "level": "medium",
        "source": "seed",
        "description": "CME 加密期货结算窗口，先用于风险时间轴占位。",
    },
    {
        "id": "seed-fomc-2026-07",
        "event_date": date(2026, 7, 28),
        "end_date": date(2026, 7, 29),
        "title": "FOMC 7月会议",
        "category": "fomc",
        "impact_assets": ["UST", "USD", "Gold", "BTC", "风险资产"],
        "level": "high",
        "source": "seed",
        "description": "后续 FOMC 会议预告，后续可接入官方日历数据源。",
    },
    {
        "id": "seed-fomc-2026-09",
        "event_date": date(2026, 9, 15),
        "end_date": date(2026, 9, 16),
        "title": "FOMC 9月会议",
        "category": "fomc",
        "impact_assets": ["UST", "USD", "Gold", "BTC", "风险资产"],
        "level": "high",
        "source": "seed",
        "description": "后续 FOMC 会议预告，后续可接入官方日历数据源。",
    },
]


def _date_to_iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(BEIJING_TZ).date().isoformat() if value.tzinfo else value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None


def _decode_jsonish_list(value: object) -> list[str]:
    decoded: object
    if isinstance(value, list):
        decoded = value
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = []
    else:
        decoded = []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded if item not in (None, "")]


def _decode_jsonish_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _numeric_to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def _timeline_status(event_date: date, end_date: date | None, today: date | None = None) -> str:
    local_today = today or datetime.now(BEIJING_TZ).date()
    effective_end = end_date or event_date
    if local_today < event_date:
        return "upcoming"
    if local_today > effective_end:
        return "passed"
    return "active"


def _timed_timeline_status(event_time: datetime, now: datetime | None = None) -> str:
    normalized_time = event_time.astimezone(UTC) if event_time.tzinfo else event_time.replace(tzinfo=UTC)
    current_time = now or datetime.now(UTC)
    if current_time < normalized_time:
        return "upcoming"
    if current_time <= normalized_time + TIMED_EVENT_ACTIVE_WINDOW:
        return "active"
    return "passed"


def _date_label(event_date: date, end_date: date | None) -> str:
    if end_date is None or end_date == event_date:
        return event_date.isoformat()
    return f"{event_date.isoformat()} 至 {end_date.isoformat()}"


def _beijing_time_label(event_time: datetime) -> str:
    normalized_time = event_time.astimezone(BEIJING_TZ) if event_time.tzinfo else event_time.replace(tzinfo=UTC).astimezone(BEIJING_TZ)
    return normalized_time.strftime("%Y-%m-%d %H:%M")


def _iso_datetime(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    normalized = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.isoformat()


class TimelineQueryService:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            await self._ensure_runtime_schema()
            await self._seed_initial_events()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _ensure_runtime_schema(self) -> None:
        if self._pool is None:
            raise RuntimeError("TimelineQueryService must be connected before schema initialization")
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS timeline_event (
                    id TEXT PRIMARY KEY,
                    event_date DATE NOT NULL,
                    end_date DATE,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    impact_assets JSONB NOT NULL DEFAULT '[]'::jsonb,
                    level TEXT NOT NULL,
                    source TEXT,
                    description TEXT,
                    previous_value TEXT,
                    market_expectation TEXT,
                    status TEXT NOT NULL DEFAULT 'upcoming',
                    source_url TEXT,
                    display_timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                    event_time TIMESTAMPTZ,
                    event_timezone TEXT,
                    source_provider TEXT,
                    source_event_id TEXT,
                    event_kind TEXT,
                    importance_score NUMERIC,
                    confidence_score NUMERIC,
                    actual_value NUMERIC,
                    forecast_value NUMERIC,
                    previous_value_numeric NUMERIC,
                    unit TEXT,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    duplicate_group_key TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS event_time TIMESTAMPTZ")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS event_timezone TEXT")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS source_provider TEXT")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS source_event_id TEXT")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS event_kind TEXT")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS importance_score NUMERIC")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS confidence_score NUMERIC")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS actual_value NUMERIC")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS forecast_value NUMERIC")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS previous_value_numeric NUMERIC")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS unit TEXT")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb")
            await connection.execute("ALTER TABLE timeline_event ADD COLUMN IF NOT EXISTS duplicate_group_key TEXT")
            await connection.execute("CREATE INDEX IF NOT EXISTS timeline_event_date_idx ON timeline_event (event_date)")
            await connection.execute("CREATE INDEX IF NOT EXISTS timeline_event_time_idx ON timeline_event (event_time)")
            await connection.execute("CREATE INDEX IF NOT EXISTS timeline_event_category_idx ON timeline_event (category)")
            await connection.execute("CREATE INDEX IF NOT EXISTS timeline_event_level_date_idx ON timeline_event (level, event_date)")
            await connection.execute("CREATE INDEX IF NOT EXISTS timeline_event_category_level_time_idx ON timeline_event (category, level, event_time)")
            await connection.execute("CREATE INDEX IF NOT EXISTS timeline_event_source_identity_idx ON timeline_event (source_provider, source_event_id)")
            await connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS timeline_event_duplicate_group_idx ON timeline_event (duplicate_group_key) WHERE duplicate_group_key IS NOT NULL"
            )

    async def _seed_initial_events(self) -> None:
        if self._pool is None:
            raise RuntimeError("TimelineQueryService must be connected before seeding")
        rows = [
            (
                item["id"],
                item["event_date"],
                item.get("end_date"),
                item["title"],
                item["category"],
                json.dumps(item["impact_assets"]),
                item["level"],
                item.get("source"),
                item.get("description"),
            )
            for item in TIMELINE_EVENT_SEEDS
        ]
        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO timeline_event (
                    id, event_date, end_date, title, category, impact_assets,
                    level, source, description
                ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9)
                ON CONFLICT (id) DO UPDATE SET
                    event_date = EXCLUDED.event_date,
                    end_date = EXCLUDED.end_date,
                    title = EXCLUDED.title,
                    category = EXCLUDED.category,
                    impact_assets = EXCLUDED.impact_assets,
                    level = EXCLUDED.level,
                    source = EXCLUDED.source,
                    description = EXCLUDED.description,
                    updated_at = NOW()
                """,
                rows,
            )

    async def fetch_events(
        self,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        category: str | None = None,
        level: str | None = None,
    ) -> list[dict[str, object]]:
        filters = []
        args: list[object] = []
        if from_date is not None:
            args.append(from_date)
            filters.append(f"COALESCE(end_date, event_date) >= ${len(args)}")
        if to_date is not None:
            args.append(to_date)
            filters.append(f"event_date <= ${len(args)}")
        if category:
            args.append(category)
            filters.append(f"category = ${len(args)}")
        if level:
            args.append(level)
            filters.append(f"level = ${len(args)}")

        filters.append(
            """
            NOT (
                source = 'seed'
                AND EXISTS (
                    SELECT 1
                    FROM timeline_event replacement
                    WHERE replacement.source IS DISTINCT FROM 'seed'
                      AND replacement.event_kind IS NOT NULL
                      AND (
                          replacement.category = timeline_event.category
                          OR (timeline_event.category = 'fomc' AND replacement.event_kind = 'fomc_meeting_window')
                      )
                      AND replacement.event_date <= COALESCE(timeline_event.end_date, timeline_event.event_date)
                      AND COALESCE(replacement.end_date, replacement.event_date) >= timeline_event.event_date
                )
            )
            """
        )
        where_clause = f"WHERE {' AND '.join(filters)}"
        rows = await self._fetch(
            f"""
            SELECT id, event_date, end_date, title, category, impact_assets, level,
                    source, description, previous_value, market_expectation, status,
                   source_url, display_timezone, event_time, event_timezone,
                   source_provider, source_event_id, event_kind, importance_score,
                   confidence_score, actual_value, forecast_value, previous_value_numeric,
                   unit, raw_payload, duplicate_group_key, created_at, updated_at
            FROM timeline_event
            {where_clause}
            ORDER BY event_date ASC, event_time ASC NULLS LAST, category ASC, title ASC
            """,
            *args,
        )
        return [self._serialize_event(row) for row in rows]

    async def _fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("TimelineQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return list(await connection.fetch(query, *args))

    @staticmethod
    def _serialize_event(row: asyncpg.Record) -> dict[str, object]:
        event_date = row["event_date"]
        end_date = row["end_date"]
        if not isinstance(event_date, date):
            raise RuntimeError("timeline_event.event_date must be a date")
        normalized_end_date = end_date if isinstance(end_date, date) else None
        event_time = row["event_time"]
        timed_event_time = event_time if isinstance(event_time, datetime) else None
        computed_status = _timed_timeline_status(timed_event_time) if timed_event_time is not None else _timeline_status(event_date, normalized_end_date)
        date_label = _beijing_time_label(timed_event_time) if timed_event_time is not None else _date_label(event_date, normalized_end_date)
        return {
            "id": row["id"],
            "eventDate": _date_to_iso(event_date),
            "endDate": _date_to_iso(normalized_end_date),
            "dateLabel": date_label,
            "title": row["title"],
            "category": row["category"],
            "impactAssets": _decode_jsonish_list(row["impact_assets"]),
            "level": row["level"],
            "source": row["source"],
            "description": row["description"],
            "previousValue": row["previous_value"],
            "marketExpectation": row["market_expectation"],
            "status": computed_status,
            "sourceUrl": row["source_url"],
            "displayTimezone": row["display_timezone"] or DISPLAY_TIMEZONE,
            "eventTime": _iso_datetime(timed_event_time),
            "eventTimezone": row["event_timezone"],
            "sourceProvider": row["source_provider"],
            "sourceEventId": row["source_event_id"],
            "eventKind": row["event_kind"],
            "importanceScore": _numeric_to_float(row["importance_score"]),
            "confidenceScore": _numeric_to_float(row["confidence_score"]),
            "actualValue": _numeric_to_float(row["actual_value"]),
            "forecastValue": _numeric_to_float(row["forecast_value"]),
            "previousValueNumeric": _numeric_to_float(row["previous_value_numeric"]),
            "unit": row["unit"],
            "rawPayload": _decode_jsonish_dict(row["raw_payload"]),
            "duplicateGroupKey": row["duplicate_group_key"],
            "createdAt": _iso_datetime(row["created_at"]),
            "updatedAt": _iso_datetime(row["updated_at"]),
        }
