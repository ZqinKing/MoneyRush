from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
import io
from importlib import import_module
import logging
import re
from time import monotonic
from typing import Protocol, cast
import warnings


logger = logging.getLogger(__name__)

AKSHARE_SECTOR_SOURCE = "akshare-official-stock-info"
AKSHARE_SHENWAN_SOURCE = "akshare-shenwan-industry"
SHENWAN_PUBLIC_SOURCE = "swsresearch-public-file"
SHENWAN_VERSION = "SW2021"
SHENWAN_STOCK_CLASSIFY_URL = "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
SHENWAN_CLASS_CODE_URL = "https://www.swsresearch.com/swindex/pdf/SwClass2021/SwClassCode_2021.xls"
SHENWAN_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}
SOURCE_FRAME_CACHE_TTL_SECONDS = 6 * 60 * 60
EMPTY_MARKERS = {"", "-", "--", "None", "none", "null", "NaN", "nan"}


@dataclass(slots=True)
class StockSectorInfo:
    industry: str | None
    region: str | None
    concepts: list[str]
    updated_at: datetime
    source: str = AKSHARE_SECTOR_SOURCE
    source_status: str = "fresh"
    sector_code: str | None = None
    shenwan: dict[str, object] | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": self.source,
            "sourceStatus": self.source_status,
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
        if self.shenwan:
            payload["shenwan"] = self.shenwan
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


class HttpResponseLike(Protocol):
    content: bytes

    def raise_for_status(self) -> None: ...


class RequestsModule(Protocol):
    def get(self, url: str, *, headers: dict[str, str], timeout: float, verify: bool) -> HttpResponseLike: ...


class PandasModule(Protocol):
    def read_excel(self, source: object, *, dtype: dict[str, str]) -> DataFrameLike: ...


class AkshareSectorClient:
    def __init__(self, akshare_module: AkshareModule | None = None) -> None:
        self._akshare: AkshareModule = akshare_module or self._load_akshare()
        self._source_frame_cache: dict[str, tuple[DataFrameLike, float]] = {}

    def fetch_sector_info(self, symbol: str) -> StockSectorInfo | None:
        normalized = self._normalize_symbol(symbol)
        if normalized is None:
            return None

        try:
            shenwan_info = self._fetch_from_shenwan(normalized)
        except Exception:
            logger.exception("akshare shenwan sector fetch failed; falling back to official stock info", extra={"symbol": normalized})
            shenwan_info = None
        if shenwan_info is not None:
            return shenwan_info

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

    def _fetch_from_shenwan(self, symbol: str) -> StockSectorInfo | None:
        history_frame = self._get_shenwan_history_frame()
        if history_frame is None:
            return None

        matches = self._find_records_by_code(history_frame, symbol, code_columns=("symbol", "股票代码", "证券代码", "代码", "A股代码"))
        if not matches:
            return None

        latest_record = max(matches, key=self._industry_history_sort_key)
        industry_code = self._normalize_industry_code(self._first_present(latest_record, ("industry_code", "行业代码")))
        if industry_code is None:
            return None

        shenwan = self._resolve_shenwan_payload(industry_code)
        industry_name = self._industry_name_from_shenwan_payload(shenwan) or industry_code
        return StockSectorInfo(
            industry=industry_name,
            region=None,
            concepts=[],
            updated_at=datetime.now(UTC),
            source=AKSHARE_SHENWAN_SOURCE,
            sector_code=industry_code,
            shenwan=shenwan,
        )

    def _get_shenwan_history_frame(self) -> DataFrameLike | None:
        try:
            return self._get_optional_source_frame("stock_industry_clf_hist_sw", "shenwan-stock-industry-history")
        except Exception:
            logger.warning("akshare shenwan history wrapper failed; trying direct public SWS file")
        return self._get_source_frame("shenwan-stock-industry-history", self._fetch_shenwan_history_file)

    @classmethod
    def _industry_name_from_shenwan_payload(cls, shenwan: dict[str, object]) -> str | None:
        for level in ("level3", "level2", "level1"):
            level_payload = shenwan.get(level)
            if not isinstance(level_payload, dict):
                continue
            name = cls._clean_text(cast(dict[str, object], level_payload).get("name"))
            if name:
                return name
        return None

    def _resolve_shenwan_payload(self, industry_code: str) -> dict[str, object]:
        try:
            code_frame = self._get_shenwan_class_code_frame()
            record = self._find_industry_record_by_code(self._frame_to_records(code_frame), industry_code)
        except Exception:
            logger.warning("sws shenwan class code fetch failed; using industry code only")
            record = None

        level1_code = f"{industry_code[:2]}0000" if re.fullmatch(r"\d{6}", industry_code) else None
        level2_code = f"{industry_code[:4]}00" if re.fullmatch(r"\d{6}", industry_code) else None
        return self._build_shenwan_payload(
            level1_code=level1_code,
            level1_name=self._first_clean_text(record or {}, ("一级行业名称", "level1Name")),
            level2_code=level2_code,
            level2_name=self._first_clean_text(record or {}, ("二级行业名称", "level2Name")),
            level3_code=industry_code,
            level3_name=self._first_clean_text(record or {}, ("三级行业名称", "level3Name")),
        )

    def _get_shenwan_class_code_frame(self) -> DataFrameLike:
        try:
            frame = self._get_optional_source_frame("sw_class_code_2021", "shenwan-class-code")
        except Exception:
            frame = None
        return frame or self._get_source_frame("shenwan-class-code", self._fetch_shenwan_class_code_file)

    @staticmethod
    def _fetch_shenwan_history_file() -> DataFrameLike:
        return AkshareSectorClient._fetch_excel_file(SHENWAN_STOCK_CLASSIFY_URL, dtype={"股票代码": "str", "行业代码": "str"})

    @staticmethod
    def _fetch_shenwan_class_code_file() -> DataFrameLike:
        return AkshareSectorClient._fetch_excel_file(SHENWAN_CLASS_CODE_URL, dtype={"行业代码": "str"})

    @staticmethod
    def _fetch_excel_file(url: str, *, dtype: dict[str, str]) -> DataFrameLike:
        requests = cast(RequestsModule, cast(object, import_module("requests")))
        pandas = cast(PandasModule, cast(object, import_module("pandas")))
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Unverified HTTPS request")
            response = requests.get(url, headers=SHENWAN_REQUEST_HEADERS, timeout=30.0, verify=False)
        response.raise_for_status()
        return pandas.read_excel(io.BytesIO(response.content), dtype=dtype)

    def _get_source_frame(self, cache_key: str, fetch: Callable[[], DataFrameLike]) -> DataFrameLike:
        now = monotonic()
        cached_entry = self._source_frame_cache.get(cache_key)
        if cached_entry is not None and now - cached_entry[1] < SOURCE_FRAME_CACHE_TTL_SECONDS:
            return cached_entry[0]

        frame = fetch()
        self._source_frame_cache[cache_key] = (frame, now)
        return frame

    def _get_optional_source_frame(self, method_name: str, cache_key: str) -> DataFrameLike | None:
        fetch = getattr(self._akshare, method_name, None)
        if not callable(fetch):
            return None
        return self._get_source_frame(cache_key, cast(Callable[[], DataFrameLike], fetch))

    def _optional_frame_records(self, method_name: str, cache_key: str) -> list[dict[str, object]]:
        frame = self._get_optional_source_frame(method_name, cache_key)
        return self._frame_to_records(frame) if frame is not None else []

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

    @classmethod
    def _find_records_by_code(cls, frame: DataFrameLike, symbol: str, *, code_columns: tuple[str, ...]) -> list[dict[str, object]]:
        matches: list[dict[str, object]] = []
        for record in cls._frame_to_records(frame):
            for column in code_columns:
                value = record.get(column)
                if value is None:
                    continue
                digits = "".join(character for character in str(value) if character.isdigit())
                if digits.zfill(6)[-6:] == symbol:
                    matches.append(record)
                    break
        return matches

    @classmethod
    def _find_industry_record_by_code(cls, records: list[dict[str, object]], industry_code: str) -> dict[str, object] | None:
        for record in records:
            code = cls._normalize_industry_code(cls._first_present(record, ("行业代码", "code", "industry_code")))
            if code == industry_code:
                return record
        return None

    @classmethod
    def _find_industry_record_by_name(cls, records: list[dict[str, object]], industry_name: str | None) -> dict[str, object] | None:
        if industry_name is None:
            return None
        for record in records:
            name = cls._first_clean_text(record, ("行业名称", "name"))
            if name == industry_name:
                return record
        return None

    @staticmethod
    def _frame_to_records(frame: DataFrameLike) -> list[dict[str, object]]:
        if frame.empty:
            return []
        normalized_frame = frame.copy()
        return normalized_frame.to_dict("records")

    @classmethod
    def _build_shenwan_payload(
        cls,
        *,
        level1_code: str | None,
        level1_name: str | None,
        level2_code: str | None,
        level2_name: str | None,
        level3_code: str | None,
        level3_name: str | None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": SHENWAN_VERSION,
            "source": SHENWAN_PUBLIC_SOURCE,
        }
        for key, level_payload in (
            ("level1", cls._build_industry_level_payload(level1_code, level1_name)),
            ("level2", cls._build_industry_level_payload(level2_code, level2_name)),
            ("level3", cls._build_industry_level_payload(level3_code, level3_name)),
        ):
            if level_payload:
                payload[key] = level_payload
        return payload

    @staticmethod
    def _build_industry_level_payload(code: str | None, name: str | None) -> dict[str, object] | None:
        payload: dict[str, object] = {}
        if code:
            payload["code"] = code
        if name:
            payload["name"] = name
        return payload or None

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

    @classmethod
    def _normalize_industry_code(cls, value: object | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if text in EMPTY_MARKERS:
            return None
        text = text.upper()
        if re.fullmatch(r"\d+\.0", text):
            text = text.split(".", 1)[0]
        return text

    @classmethod
    def _industry_history_sort_key(cls, record: dict[str, object]) -> tuple[int, int]:
        return (
            cls._date_sort_value(record.get("update_time") or record.get("更新日期")),
            cls._date_sort_value(record.get("start_date") or record.get("计入日期")),
        )

    @staticmethod
    def _date_sort_value(value: object) -> int:
        if isinstance(value, datetime):
            return value.date().toordinal()
        if isinstance(value, date):
            return value.toordinal()
        if value is None:
            return 0
        text = str(value).strip()
        for date_format in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, date_format).date().toordinal()
            except ValueError:
                continue
        return 0

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
