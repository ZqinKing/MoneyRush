from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
import logging
import re
from time import monotonic
from typing import Protocol, cast


logger = logging.getLogger(__name__)

AKSHARE_SECTOR_SOURCE = "akshare-official-stock-info"
SOURCE_FRAME_CACHE_TTL_SECONDS = 6 * 60 * 60
EMPTY_MARKERS = {"", "-", "--", "None", "none", "null", "NaN", "nan"}


@dataclass(slots=True)
class StockSectorInfo:
    industry: str | None
    region: str | None
    concepts: list[str]
    updated_at: datetime

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": AKSHARE_SECTOR_SOURCE,
            "sourceStatus": "fresh",
            "updatedAt": self.updated_at.isoformat(),
        }
        if self.industry:
            payload["industry"] = self.industry
        if self.region:
            payload["region"] = self.region
        if self.concepts:
            payload["concepts"] = self.concepts
        return payload


class AkshareModule(Protocol):
    def stock_info_sz_name_code(self, symbol: str) -> DataFrameLike: ...

    def stock_info_bj_name_code(self) -> DataFrameLike: ...

    def stock_profile_cninfo(self, symbol: str) -> DataFrameLike: ...


class DataFrameLike(Protocol):
    @property
    def empty(self) -> bool: ...

    def copy(self) -> DataFrameLike: ...

    def to_dict(self, orient: str) -> list[dict[str, object]]: ...


class AkshareSectorClient:
    def __init__(self, akshare_module: AkshareModule | None = None) -> None:
        self._akshare: AkshareModule = akshare_module or self._load_akshare()
        self._source_frame_cache: dict[str, tuple[DataFrameLike, float]] = {}

    def fetch_sector_info(self, symbol: str) -> StockSectorInfo | None:
        normalized = self._normalize_symbol(symbol)
        if normalized is None:
            return None

        if self._is_beijing_symbol(normalized):
            return self._fetch_from_beijing_list(normalized)
        if normalized.startswith(("0", "2", "3")):
            return self._fetch_from_shenzhen_list(normalized)
        if normalized.startswith(("5", "6", "9")):
            return self._fetch_from_cninfo_profile(normalized)
        return None

    @staticmethod
    def _normalize_symbol(symbol: str) -> str | None:
        normalized = symbol.strip()
        return normalized if re.fullmatch(r"\d{6}", normalized) else None

    @staticmethod
    def _is_beijing_symbol(symbol: str) -> bool:
        return symbol.startswith(("4", "8", "92"))

    def _fetch_from_shenzhen_list(self, symbol: str) -> StockSectorInfo | None:
        frame = self._get_source_frame(
            "sz-a-list",
            lambda: self._akshare.stock_info_sz_name_code(symbol="A股列表"),
        )
        record = self._find_record_by_code(frame, symbol, code_columns=("A股代码", "证券代码", "代码", "股票代码"))
        if record is None:
            return None
        return self._record_to_sector_info(
            record,
            industry_columns=("所属行业", "行业", "证监会行业", "行业分类"),
            region_columns=("地区", "地域", "省份"),
        )

    def _fetch_from_beijing_list(self, symbol: str) -> StockSectorInfo | None:
        frame = self._get_source_frame("bj-list", self._akshare.stock_info_bj_name_code)
        record = self._find_record_by_code(frame, symbol, code_columns=("证券代码", "A股代码", "代码", "股票代码"))
        if record is None:
            return None
        return self._record_to_sector_info(
            record,
            industry_columns=("所属行业", "行业", "证监会行业", "行业分类"),
            region_columns=("地区", "地域", "省份"),
        )

    def _fetch_from_cninfo_profile(self, symbol: str) -> StockSectorInfo | None:
        frame = self._get_source_frame(
            f"cninfo-profile-{symbol}",
            lambda: self._akshare.stock_profile_cninfo(symbol=symbol),
        )
        records = self._frame_to_records(frame)
        if not records:
            return None

        merged: dict[str, object] = {}
        for record in records:
            merged.update(record)
            key = self._first_present(record, ("项目", "指标", "item", "name"))
            value = self._first_present(record, ("值", "内容", "value"))
            if key is not None and value is not None:
                merged[str(key).strip()] = value

        return self._record_to_sector_info(
            merged,
            industry_columns=("所属行业", "行业", "证监会行业", "行业分类", "公司行业"),
            region_columns=("地区", "地域", "省份"),
        )

    def _get_source_frame(self, cache_key: str, fetch: Callable[[], DataFrameLike]) -> DataFrameLike:
        now = monotonic()
        cached_entry = self._source_frame_cache.get(cache_key)
        if cached_entry is not None and now - cached_entry[1] < SOURCE_FRAME_CACHE_TTL_SECONDS:
            return cached_entry[0]

        frame = fetch()
        self._source_frame_cache[cache_key] = (frame, now)
        return frame

    @classmethod
    def _find_record_by_code(cls, frame: DataFrameLike, symbol: str, *, code_columns: tuple[str, ...]) -> dict[str, object] | None:
        for record in cls._frame_to_records(frame):
            for column in code_columns:
                value = record.get(column)
                if value is None:
                    continue
                digits = "".join(character for character in str(value) if character.isdigit())
                if digits.zfill(6)[-6:] == symbol:
                    return record
        return None

    @staticmethod
    def _frame_to_records(frame: DataFrameLike) -> list[dict[str, object]]:
        if frame.empty:
            return []
        normalized_frame = frame.copy()
        return normalized_frame.to_dict("records")

    @staticmethod
    def _load_akshare() -> AkshareModule:
        module = import_module("akshare")
        return cast(AkshareModule, cast(object, module))

    @classmethod
    def _record_to_sector_info(
        cls,
        record: dict[str, object],
        *,
        industry_columns: tuple[str, ...],
        region_columns: tuple[str, ...],
    ) -> StockSectorInfo | None:
        industry = cls._first_clean_text(record, industry_columns)
        region = cls._first_clean_text(record, region_columns)
        if industry is None and region is None:
            return None
        return StockSectorInfo(
            industry=industry,
            region=region,
            concepts=[],
            updated_at=datetime.now(UTC),
        )

    @staticmethod
    def _first_present(record: dict[str, object], columns: tuple[str, ...]) -> object | None:
        for column in columns:
            if column in record:
                return record[column]
        return None

    @classmethod
    def _first_clean_text(cls, record: dict[str, object], columns: tuple[str, ...]) -> str | None:
        for column in columns:
            if column not in record:
                continue
            text = cls._clean_text(record[column])
            if text is not None:
                return text
        return None

    @staticmethod
    def _clean_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if text in EMPTY_MARKERS:
            return None
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            return None
        return text
