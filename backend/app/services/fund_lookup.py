from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.request import urlopen


FUND_QUOTE_URL = "https://fundgz.1234567.com.cn/js/{fund_code}.js"
_JSONP_RE = re.compile(r"jsonpgz\((.*)\);?$")


def _to_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class FundLookupResult:
    fund_code: str
    fund_name: str | None
    nav: float | None
    daily_return: float | None
    nav_date: str | None
    raw_payload: dict[str, object]

    @property
    def is_valid(self) -> bool:
        return bool(self.fund_name and self.fund_code)


class FundLookupService:
    def lookup(self, fund_code: str) -> FundLookupResult:
        with urlopen(FUND_QUOTE_URL.format(fund_code=fund_code), timeout=10) as response:
            payload = response.read().decode("utf-8", errors="replace").strip()

        match = _JSONP_RE.match(payload)
        if match is None:
            return FundLookupResult(fund_code=fund_code, fund_name=None, nav=None, daily_return=None, nav_date=None, raw_payload={})

        data = json.loads(match.group(1))
        return FundLookupResult(
            fund_code=str(data.get("fundcode") or fund_code),
            fund_name=str(data.get("name") or "").strip() or None,
            nav=_to_float(data.get("dwjz")),
            daily_return=_to_float(data.get("gszzl")),
            nav_date=str(data.get("jzrq") or "").strip() or None,
            raw_payload=data if isinstance(data, dict) else {},
        )
