from __future__ import annotations

import asyncio
import logging

from collector.config import get_settings
from collector.workers.macro_loop import MacroCollectorWorker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


async def main() -> None:
    settings = get_settings()
    worker = MacroCollectorWorker(settings)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
