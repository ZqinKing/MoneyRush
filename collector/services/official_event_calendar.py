from __future__ import annotations

from datetime import UTC, date, datetime
import json
import logging
from typing import TypedDict
from zoneinfo import ZoneInfo

import requests

from collector.services.event_calendar_normalizer import normalize_timeline_event


logger = logging.getLogger(__name__)

BEA_RELEASE_URL = "https://apps.bea.gov/API/signup/release_dates.json"
BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BLS_CPI_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"
BLS_EMPSIT_URL = "https://www.bls.gov/schedule/news_release/empsit.htm"
FED_FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
US_EASTERN = ZoneInfo("America/New_York")


class EventCalendarSourceError(RuntimeError):
    def __init__(self, reason: str, *, provider: str, status_code: int | None = None) -> None:
        self.reason: str = reason
        self.provider: str = provider
        self.status_code: int | None = status_code
        message = f"{provider}: {reason}"
        if status_code is not None:
            message = f"{message} status={status_code}"
        super().__init__(message)


class CalendarEventSpec(TypedDict):
    release_name: str
    event_kind: str
    title: str
    level: str
    importance: float
    assets: list[str]
    description: str


class BlsEventSpec(TypedDict):
    event_kind: str
    title: str
    level: str
    importance: float
    assets: list[str]
    description: str
    source_url: str


BLS_RELEASE_SPECS: dict[str, BlsEventSpec] = {
    "Consumer Price Index": {
        "event_kind": "us_cpi",
        "title": "美国 CPI / Core CPI 发布",
        "level": "high",
        "importance": 0.95,
        "assets": ["USD", "UST", "Gold", "BTC", "美股指数"],
        "description": "BLS Consumer Price Index 官方发布时间。",
        "source_url": BLS_CPI_URL,
    },
    "Employment Situation": {
        "event_kind": "us_nfp",
        "title": "美国非农就业 / 失业率发布",
        "level": "high",
        "importance": 0.94,
        "assets": ["USD", "UST", "Gold", "美股指数", "风险资产"],
        "description": "BLS Employment Situation 官方发布时间，市场通常按非农就业和失业率解读。",
        "source_url": BLS_EMPSIT_URL,
    },
}

BLS_FIXTURE_2026 = {
    "Consumer Price Index": [
        date(2026, 1, 13),
        date(2026, 2, 13),
        date(2026, 3, 11),
        date(2026, 4, 10),
        date(2026, 5, 12),
        date(2026, 6, 10),
        date(2026, 7, 14),
        date(2026, 8, 12),
        date(2026, 9, 11),
        date(2026, 10, 14),
        date(2026, 11, 10),
        date(2026, 12, 10),
    ],
    "Employment Situation": [
        date(2026, 1, 9),
        date(2026, 2, 11),
        date(2026, 3, 6),
        date(2026, 4, 3),
        date(2026, 5, 8),
        date(2026, 6, 5),
        date(2026, 7, 2),
        date(2026, 8, 7),
        date(2026, 9, 4),
        date(2026, 10, 2),
        date(2026, 11, 6),
        date(2026, 12, 4),
    ],
}


def _within_window(event_date: date, from_date: date, to_date: date) -> bool:
    return from_date <= event_date <= to_date


def _parse_iso_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("expected ISO datetime string")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_bea_release_dates(
    payload: dict[str, object],
    *,
    from_date: date,
    to_date: date,
    include_gdp: bool = True,
) -> list[dict[str, object]]:
    source_updated = payload.get("file_last_updated")
    release_specs: list[CalendarEventSpec] = [
        {
            "release_name": "Personal Income and Outlays",
            "event_kind": "us_pce",
            "title": "美国 PCE / Personal Income and Outlays 发布",
            "level": "high",
            "importance": 0.94,
            "assets": ["USD", "UST", "Gold", "BTC", "美股成长股"],
            "description": "BEA Personal Income and Outlays 官方发布时间，包含 PCE 通胀相关数据。",
        }
    ]
    if include_gdp:
        release_specs.append(
            {
                "release_name": "Gross Domestic Product",
                "event_kind": "us_gdp",
                "title": "美国 GDP 发布",
                "level": "medium",
                "importance": 0.78,
                "assets": ["USD", "UST", "美股指数", "周期股"],
                "description": "BEA Gross Domestic Product 官方发布时间。",
            }
        )

    events: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for spec in release_specs:
        release_name = str(spec["release_name"])
        release_payload = payload.get(release_name)
        release_dates = release_payload.get("release_dates") if isinstance(release_payload, dict) else None
        if not isinstance(release_dates, list):
            continue
        for index, value in enumerate(release_dates):
            try:
                event_time = _parse_iso_datetime(value)
            except ValueError:
                continue
            local_date = event_time.astimezone(ZoneInfo("Asia/Shanghai")).date()
            if not _within_window(local_date, from_date, to_date):
                continue
            dedupe_key = (release_name, event_time.isoformat())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            events.append(
                normalize_timeline_event(
                    title=str(spec["title"]),
                    category="macro",
                    level=str(spec["level"]),
                    event_kind=str(spec["event_kind"]),
                    source="official",
                    source_provider="bea",
                    source_event_id=f"bea:{release_name}:{event_time.isoformat()}",
                    source_url=BEA_RELEASE_URL,
                    event_time=event_time,
                    event_timezone="UTC",
                    impact_assets=list(spec["assets"]),
                    description=str(spec["description"]),
                    importance_score=float(spec["importance"]),
                    confidence_score=0.98,
                    raw_payload={
                        "releaseName": release_name,
                        "releaseDate": value,
                        "releaseIndex": index,
                        "fileLastUpdated": source_updated,
                        "sourceUrl": BEA_RELEASE_URL,
                    },
                )
            )
    return events


def _unfold_ics_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip("\r")
        if line.startswith((" ", "\t")) and lines:
            lines[-1] = f"{lines[-1]}{line[1:]}"
        else:
            lines.append(line)
    return lines


def _parse_ics_property(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    left, value = line.split(":", 1)
    pieces = left.split(";")
    name = pieces[0].upper()
    params: dict[str, str] = {}
    for param in pieces[1:]:
        if "=" in param:
            key, param_value = param.split("=", 1)
            params[key.upper()] = param_value
    return name, params, value.replace("\\,", ",")


def _parse_ics_datetime(value: str, params: dict[str, str]) -> datetime:
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    if "T" not in value:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC)
    parsed = datetime.strptime(value, "%Y%m%dT%H%M%S" if len(value) == 15 else "%Y%m%dT%H%M")
    tzid = params.get("TZID")
    tzinfo = US_EASTERN if tzid in {"US-Eastern", "America/New_York"} else UTC
    return parsed.replace(tzinfo=tzinfo).astimezone(UTC)


def _iter_ics_events(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line in _unfold_ics_lines(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None:
            continue
        parsed = _parse_ics_property(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "DTSTART":
            current["dtstart"] = _parse_ics_datetime(value, params)
            current["dtstartRaw"] = value
            current["timezone"] = params.get("TZID") or ("UTC" if value.endswith("Z") else None)
        elif name in {"UID", "SUMMARY", "LOCATION", "CATEGORIES"}:
            current[name.lower()] = value
    return events


def parse_bls_ics(text: str, *, from_date: date, to_date: date) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for item in _iter_ics_events(text):
        summary = str(item.get("summary") or "")
        spec = BLS_RELEASE_SPECS.get(summary)
        event_time = item.get("dtstart")
        if spec is None or not isinstance(event_time, datetime):
            continue
        local_date = event_time.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if not _within_window(local_date, from_date, to_date):
            continue
        uid = str(item.get("uid") or f"{summary}:{event_time.isoformat()}")
        events.append(
            normalize_timeline_event(
                title=str(spec["title"]),
                category="macro",
                level=str(spec["level"]),
                event_kind=str(spec["event_kind"]),
                source="official",
                source_provider="bls",
                source_event_id=f"bls:{uid}",
                source_url=BLS_ICS_URL,
                event_time=event_time,
                event_timezone=str(item.get("timezone") or "US-Eastern"),
                impact_assets=list(spec["assets"]),
                description=str(spec["description"]),
                importance_score=float(spec["importance"]),
                confidence_score=0.98,
                raw_payload={
                    "uid": uid,
                    "summary": summary,
                    "dtstart": item.get("dtstartRaw"),
                    "timezone": item.get("timezone"),
                    "categories": item.get("categories"),
                    "sourceUrl": BLS_ICS_URL,
                },
            )
        )
    return events


def load_bls_fixture_events(*, from_date: date, to_date: date) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for summary, dates in BLS_FIXTURE_2026.items():
        spec = BLS_RELEASE_SPECS[summary]
        for release_date in dates:
            event_time = datetime.combine(release_date, datetime.min.time().replace(hour=8, minute=30), tzinfo=US_EASTERN).astimezone(UTC)
            local_date = event_time.astimezone(ZoneInfo("Asia/Shanghai")).date()
            if not _within_window(local_date, from_date, to_date):
                continue
            events.append(
                normalize_timeline_event(
                    title=spec["title"],
                    category="macro",
                    level=spec["level"],
                    event_kind=spec["event_kind"],
                    source="official-fixture",
                    source_provider="bls",
                    source_event_id=f"bls-fixture:{spec['event_kind']}:{release_date.isoformat()}",
                    source_url=spec["source_url"],
                    event_time=event_time,
                    event_timezone="US-Eastern",
                    impact_assets=spec["assets"],
                    description=f"{spec['description']} ICS 被访问策略阻挡时使用的 BLS 官方 release page fixture。",
                    importance_score=spec["importance"],
                    confidence_score=0.92,
                    raw_payload={
                        "summary": summary,
                        "releaseDate": release_date.isoformat(),
                        "releaseTime": "08:30 AM ET",
                        "sourceUrl": spec["source_url"],
                        "fixtureYear": 2026,
                        "lastVerified": "2026-06-26",
                    },
                )
            )
    return events


FOMC_FIXTURE_2026 = [
    (date(2026, 1, 27), date(2026, 1, 28), False),
    (date(2026, 3, 17), date(2026, 3, 18), True),
    (date(2026, 4, 28), date(2026, 4, 29), False),
    (date(2026, 6, 16), date(2026, 6, 17), True),
    (date(2026, 7, 28), date(2026, 7, 29), False),
    (date(2026, 9, 15), date(2026, 9, 16), True),
    (date(2026, 10, 27), date(2026, 10, 28), False),
    (date(2026, 12, 8), date(2026, 12, 9), True),
]


def load_fomc_fixture_events(*, from_date: date, to_date: date) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for start_date, end_date, has_sep in FOMC_FIXTURE_2026:
        if end_date < from_date or start_date > to_date:
            continue
        year_month = f"{start_date.year}-{start_date.month:02d}"
        title = f"FOMC {year_month} 会议窗口" + (" + SEP" if has_sep else "")
        events.append(
            normalize_timeline_event(
                title=title,
                category="fomc",
                level="high",
                event_kind="fomc_meeting_window",
                source="official-fixture",
                source_provider="fed-fomc-fixture",
                source_event_id=f"fomc:{start_date.isoformat()}:{end_date.isoformat()}",
                source_url=FED_FOMC_URL,
                event_date=start_date,
                end_date=end_date,
                impact_assets=["UST", "USD", "Gold", "BTC", "风险资产"],
                description="Fed 官方 FOMC calendar 维护的会议窗口；V1 不推断精确声明发布时间。",
                importance_score=0.96 if has_sep else 0.9,
                confidence_score=0.93,
                raw_payload={
                    "sourceUrl": FED_FOMC_URL,
                    "fixtureYear": 2026,
                    "lastVerified": "2026-06-26",
                    "hasSummaryOfEconomicProjections": has_sep,
                },
            )
        )
    return events


class OfficialEventCalendarClient:
    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        self._timeout: float = max(float(timeout_seconds), 1.0)

    def fetch_bea_events(self, *, from_date: date, to_date: date, include_gdp: bool = True) -> list[dict[str, object]]:
        payload = self._get_json(BEA_RELEASE_URL, provider="bea")
        if not isinstance(payload, dict):
            raise EventCalendarSourceError("invalid_payload", provider="bea")
        return parse_bea_release_dates(payload, from_date=from_date, to_date=to_date, include_gdp=include_gdp)

    def fetch_bls_events(self, *, from_date: date, to_date: date) -> list[dict[str, object]]:
        text = self._get_text(BLS_ICS_URL, provider="bls")
        return parse_bls_ics(text, from_date=from_date, to_date=to_date)

    def _get_text(self, url: str, *, provider: str) -> str:
        try:
            response = requests.get(url, timeout=self._timeout)
        except requests.Timeout as exc:
            raise EventCalendarSourceError("timeout", provider=provider) from exc
        except requests.RequestException as exc:
            raise EventCalendarSourceError("request_error", provider=provider) from exc
        if response.status_code >= 400:
            raise EventCalendarSourceError("http_error", provider=provider, status_code=response.status_code)
        return response.text

    def _get_json(self, url: str, *, provider: str) -> object:
        text = self._get_text(url, provider=provider)
        try:
            payload: object = json.loads(text)
        except ValueError as exc:
            raise EventCalendarSourceError("parse_error", provider=provider) from exc
        return payload
