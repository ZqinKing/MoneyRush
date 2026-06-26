from __future__ import annotations

import calendar
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from collector.services.event_calendar_normalizer import normalize_timeline_event


CHINA_HOLIDAYS_BY_YEAR = {
    2026: {
        date(2026, 1, 1),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 2, 20),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 6, 19),
        date(2026, 9, 25),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
    }
}

US_MARKET_HOLIDAYS_BY_YEAR = {
    2026: {
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    },
    2027: {
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),
        date(2027, 7, 5),
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),
    },
}

CN_TZ = ZoneInfo("Asia/Shanghai")
SG_TZ = ZoneInfo("Asia/Singapore")
NY_TZ = ZoneInfo("America/New_York")


def _is_business_day(day: date, holidays_by_year: dict[int, set[date]]) -> bool:
    if day.year not in holidays_by_year:
        return False
    return day.weekday() < 5 and day not in holidays_by_year[day.year]


def _previous_business_day(day: date, holidays_by_year: dict[int, set[date]]) -> date | None:
    current = day
    for _ in range(10):
        if _is_business_day(current, holidays_by_year):
            return current
        current = date.fromordinal(current.toordinal() - 1)
    return None


def _month_business_days(year: int, month: int, holidays_by_year: dict[int, set[date]]) -> list[date]:
    _, days_in_month = calendar.monthrange(year, month)
    return [day for day in (date(year, month, number) for number in range(1, days_in_month + 1)) if _is_business_day(day, holidays_by_year)]


def _third_friday(year: int, month: int) -> date:
    fridays = [date(year, month, day) for day in range(1, calendar.monthrange(year, month)[1] + 1) if date(year, month, day).weekday() == 4]
    return fridays[2]


def _iter_months(from_date: date, to_date: date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year = from_date.year
    month = from_date.month
    while (year, month) <= (to_date.year, to_date.month):
        months.append((year, month))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def _in_window(day: date, from_date: date, to_date: date) -> bool:
    return from_date <= day <= to_date


def generate_derivatives_events(*, from_date: date, to_date: date) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for year, month in _iter_months(from_date, to_date):
        china_days = _month_business_days(year, month, CHINA_HOLIDAYS_BY_YEAR)
        if len(china_days) >= 2:
            a50_day = china_days[-2]
            if _in_window(a50_day, from_date, to_date):
                quarterly = month in {3, 6, 9, 12}
                events.append(
                    normalize_timeline_event(
                        title=f"SGX A50 {year}-{month:02d} 合约最后交易日",
                        category="derivatives",
                        level="high" if quarterly else "medium",
                        event_kind="sgx_a50_expiry",
                        source="rule",
                        source_provider="rule",
                        source_event_id=f"sgx-a50:{year}-{month:02d}",
                        source_url="https://english.sse.com.cn/start/trading/schedule/",
                        event_time=datetime.combine(a50_day, time(16, 30), tzinfo=SG_TZ),
                        event_timezone="Asia/Singapore",
                        impact_assets=["A50", "富时中国A50", "A股", "港股"],
                        description="按 A50 合约月倒数第二个中国交易日规则生成；高置信度依赖维护的中国交易日 fixture 覆盖年份。",
                        importance_score=0.82 if quarterly else 0.72,
                        confidence_score=0.88,
                        raw_payload={"rule": "second_last_china_business_day", "calendarYear": year, "fixture": "SSE/SZSE holiday override"},
                    )
                )

        cffex_base = _third_friday(year, month)
        cffex_day = _previous_business_day(cffex_base, CHINA_HOLIDAYS_BY_YEAR)
        if cffex_day is not None and _in_window(cffex_day, from_date, to_date):
            events.append(
                normalize_timeline_event(
                    title=f"CFFEX {year}-{month:02d} 股指期货到期",
                    category="derivatives",
                    level="medium",
                    event_kind="cffex_index_futures_expiry",
                    source="rule",
                    source_provider="rule",
                    source_event_id=f"cffex-index:{year}-{month:02d}",
                    source_url="https://www.cffex.com.cn/",
                    event_time=datetime.combine(cffex_day, time(15, 0), tzinfo=CN_TZ),
                    event_timezone="Asia/Shanghai",
                    impact_assets=["沪深300", "中证500", "中证1000", "A股指数"],
                    description="按 CFFEX 股指期货第三个周五、遇中国休市提前的规则生成。",
                    importance_score=0.74,
                    confidence_score=0.86,
                    raw_payload={"rule": "third_friday_previous_china_business_day", "baseDate": cffex_base.isoformat()},
                )
            )

        opex_base = _third_friday(year, month)
        opex_day = _previous_business_day(opex_base, US_MARKET_HOLIDAYS_BY_YEAR)
        if opex_day is not None and _in_window(opex_day, from_date, to_date):
            quarterly = month in {3, 6, 9, 12}
            events.append(
                normalize_timeline_event(
                    title=f"美国{'季度' if quarterly else '月度'} OpEx {year}-{month:02d}",
                    category="options",
                    level="high" if quarterly else "medium",
                    event_kind="us_opex",
                    source="rule",
                    source_provider="rule",
                    source_event_id=f"us-opex:{year}-{month:02d}",
                    source_url="https://www.cboe.com/about/hours/us-options/",
                    event_time=datetime.combine(opex_day, time(16, 0), tzinfo=NY_TZ),
                    event_timezone="America/New_York",
                    impact_assets=["SPX", "QQQ", "美股ETF", "期权波动率"],
                    description="按美股第三个周五、遇美股休市提前的月度/季度期权到期规则生成。",
                    importance_score=0.83 if quarterly else 0.72,
                    confidence_score=0.88,
                    raw_payload={"rule": "third_friday_previous_us_market_business_day", "baseDate": opex_base.isoformat()},
                )
            )
    return events
