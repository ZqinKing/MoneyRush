from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)

EASTMONEY_STOCK_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_UT = "fa5fd1943c7b386f172d6893dbfba10b"
SECTOR_FIELDS = "f57,f58,f127,f128,f129,f198"
EMPTY_MARKERS = {"", "-", "--", "None", "null"}


@dataclass(slots=True)
class StockSectorInfo:
    industry: str | None
    region: str | None
    concepts: list[str]
    sector_code: str | None
    updated_at: datetime

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": "eastmoney-push2",
            "sourceStatus": "fresh",
            "updatedAt": self.updated_at.isoformat(),
        }
        if self.industry:
            payload["industry"] = self.industry
        if self.region:
            payload["region"] = self.region
        if self.concepts:
            payload["concepts"] = self.concepts
        if self.sector_code:
            payload["sectorCode"] = self.sector_code
        return payload


class EastmoneySectorClient:
    def fetch_sector_info(self, symbol: str) -> StockSectorInfo | None:
        secid = self._to_secid(symbol)
        if secid is None:
            return None

        params = urlencode(
            {
                "secid": secid,
                "ut": EASTMONEY_UT,
                "fltt": "2",
                "invt": "2",
                "fields": SECTOR_FIELDS,
            }
        )
        request = Request(
            f"{EASTMONEY_STOCK_URL}?{params}",
            headers={
                "User-Agent": "Mozilla/5.0 MoneyRush sector enrichment",
                "Referer": "https://quote.eastmoney.com/",
            },
        )

        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None

        industry = self._clean_text(data.get("f127"))
        region = self._clean_region(data.get("f128"))
        concepts = self._parse_concepts(data.get("f129"))
        sector_code = self._clean_text(data.get("f198"))

        if not any((industry, region, concepts, sector_code)):
            return None

        return StockSectorInfo(
            industry=industry,
            region=region,
            concepts=concepts,
            sector_code=sector_code,
            updated_at=datetime.now(UTC),
        )

    @staticmethod
    def _to_secid(symbol: str) -> str | None:
        normalized = symbol.strip()
        if not re.fullmatch(r"\d{6}", normalized):
            return None
        if normalized.startswith(("5", "6", "9")):
            return f"1.{normalized}"
        if normalized.startswith(("0", "2", "3")):
            return f"0.{normalized}"
        return None

    @staticmethod
    def _clean_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return None if text in EMPTY_MARKERS else text

    @classmethod
    def _clean_region(cls, value: object) -> str | None:
        text = cls._clean_text(value)
        if text is None:
            return None
        if re.fullmatch(r"BK\d+", text, flags=re.IGNORECASE):
            return None
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            return None
        return text

    @classmethod
    def _parse_concepts(cls, value: object) -> list[str]:
        text = cls._clean_text(value)
        if text is None:
            return []
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            return []
        if "," not in text and "，" not in text and "概念" not in text and "板块" not in text:
            return []

        concepts: list[str] = []
        seen: set[str] = set()
        for part in re.split(r"[,，、;；]", text):
            concept = part.strip()
            if not concept or concept in EMPTY_MARKERS or concept in seen:
                continue
            seen.add(concept)
            concepts.append(concept)
        return concepts
