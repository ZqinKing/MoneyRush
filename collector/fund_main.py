from __future__ import annotations

import asyncio
import logging

from collector.config import get_settings
from collector.workers.fund_loop import FundCollectorWorker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


async def main() -> None:
    settings = get_settings()
    worker = FundCollectorWorker(settings)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
