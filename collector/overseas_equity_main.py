from __future__ import annotations

import argparse
import asyncio
import logging

from collector.config import get_settings
from collector.workers.overseas_equity_loop import OverseasEquityCollectorWorker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    worker = OverseasEquityCollectorWorker(settings)
    await worker.run(run_once=args.run_once)


if __name__ == "__main__":
    asyncio.run(main())
