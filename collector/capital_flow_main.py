from __future__ import annotations

import argparse
import asyncio
import logging

from collector.config import get_settings
from collector.workers.capital_flow_loop import CapitalFlowCollectorWorker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MoneyRush capital-flow collector")
    parser.add_argument("--run-once", action="store_true", help="collect one pass and exit")
    parser.add_argument("--symbols", default="", help="comma-separated symbols for one-shot collection")
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    settings = get_settings()
    worker = CapitalFlowCollectorWorker(settings)

    if args.run_once:
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()] if args.symbols else None
        await worker.run_once(symbols=symbols)
        return

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
