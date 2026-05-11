from __future__ import annotations

import asyncio
import logging

from collector.config import get_settings
from collector.workers.symbol_loop import CollectorWorker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


async def main() -> None:
    settings = get_settings()
    worker = CollectorWorker(settings)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
