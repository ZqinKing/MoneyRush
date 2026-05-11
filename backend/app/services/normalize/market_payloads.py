from __future__ import annotations

import re


SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,16}$")


def normalize_symbol_input(raw_symbol: str) -> str:
    symbol = raw_symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol cannot be empty")
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("symbol must contain only letters, digits, dot, underscore, or dash")
    return symbol
