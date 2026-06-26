from __future__ import annotations

import argparse
import asyncio
import logging

from collector.config import get_settings
from collector.workers.event_calendar_loop import EventCalendarCollectorWorker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Collect deterministic market-impact calendar events")
    parser.add_argument("--run-once", action="store_true", help="refresh once and exit")
    args = parser.parse_args()
    settings = get_settings()
    worker = EventCalendarCollectorWorker(settings)
    try:
        if args.run_once:
            await worker.run_once()
        else:
            await worker.run()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
