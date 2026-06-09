from __future__ import annotations

import asyncio
from datetime import date

from .llm_audit_query_service import LlmAuditQueryService


class FakeConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetchrow(self, query, *args):
        self.queries.append(query)
        return {"total_count": 0, "latest_invoked_at": None}

    async def fetch(self, query, *args):
        self.queries.append(query)
        return []


class FakeAcquire:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)


def test_daily_summary_queries_exclude_skipped_rows():
    connection = FakeConnection()
    service = LlmAuditQueryService("postgres://test")
    setattr(service, "_pool", FakePool(connection))

    asyncio.run(service.fetch_daily_summary(date(2026, 6, 8)))

    assert len(connection.queries) == 6
    assert all("status <> 'skipped'" in query for query in connection.queries)
