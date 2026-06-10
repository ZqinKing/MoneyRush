from __future__ import annotations

import argparse
import asyncio
import logging

from collector.config import get_settings
from collector.workers.global_markets_loop import GlobalMarketsCollectorWorker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Collect global market dashboard cache")
    parser.add_argument("--run-once", action="store_true", help="refresh once and exit")
    args = parser.parse_args()

    settings = get_settings()
    worker = GlobalMarketsCollectorWorker(settings)
    await worker.run(run_once=args.run_once)


if __name__ == "__main__":
    asyncio.run(main())
