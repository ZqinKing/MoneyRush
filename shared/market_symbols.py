from __future__ import annotations


DOMESTIC_STOCK_COLLECTOR_PREFIXES = (
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "600",
    "601",
    "603",
    "605",
    "688",
)


def normalize_market_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def is_domestic_stock_collector_symbol(value: object) -> bool:
    symbol = normalize_market_symbol(value)
    return len(symbol) == 6 and symbol.isdigit() and symbol.startswith(DOMESTIC_STOCK_COLLECTOR_PREFIXES)
