from __future__ import annotations

from dataclasses import dataclass
from urllib.request import urlopen


QUOTE_URL = "https://qt.gtimg.cn/q="


def _symbol_to_vendor_code(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return f"sh{symbol}"
    return f"sz{symbol}"


def _infer_exchange(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class SymbolLookupResult:
    symbol: str
    company_name: str | None
    exchange: str
    last_price: float | None
    previous_close: float | None

    @property
    def is_valid(self) -> bool:
        if not self.company_name or self.company_name == self.symbol:
            return False
        if self.previous_close is not None and self.previous_close > 0:
            return True
        return self.last_price is not None and self.last_price > 0


class SymbolLookupService:
    def lookup(self, symbol: str) -> SymbolLookupResult:
        vendor_code = _symbol_to_vendor_code(symbol)
        url = f"{QUOTE_URL}{vendor_code}"

        with urlopen(url, timeout=10) as response:
            payload = response.read().decode("gbk", errors="replace").strip()

        if not payload or '="' not in payload:
            return SymbolLookupResult(symbol=symbol, company_name=None, exchange=_infer_exchange(symbol), last_price=None, previous_close=None)

        _, quoted_value = payload.split('="', 1)
        parts = quoted_value.removesuffix('";').split("~")
        if len(parts) < 5:
            return SymbolLookupResult(symbol=symbol, company_name=None, exchange=_infer_exchange(symbol), last_price=None, previous_close=None)

        company_name = (parts[1] or "").strip() or None
        last_price = _to_float(parts[3]) if len(parts) > 3 else None
        previous_close = _to_float(parts[4]) if len(parts) > 4 else None
        return SymbolLookupResult(
            symbol=symbol,
            company_name=company_name,
            exchange=_infer_exchange(symbol),
            last_price=last_price,
            previous_close=previous_close,
        )
