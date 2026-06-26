from datetime import UTC, date, datetime
from typing import Callable, cast

import collector.services.tencent_quote_client as quote_client


to_utc_intraday_bucket = cast(
    Callable[[object, date | None], datetime | None],
    getattr(quote_client, "_to_utc_intraday_bucket"),
)


def test_intraday_bucket_treats_naive_vendor_datetime_as_china_time() -> None:
    bucket = to_utc_intraday_bucket(
        datetime(2026, 6, 26, 9, 31),
        date(2026, 6, 26),
    )

    assert bucket == datetime(2026, 6, 26, 1, 31, tzinfo=UTC)


def test_intraday_bucket_treats_iso_vendor_datetime_as_china_time() -> None:
    bucket = to_utc_intraday_bucket(
        "2026-06-26 13:05:00",
        date(2026, 6, 26),
    )

    assert bucket == datetime(2026, 6, 26, 5, 5, tzinfo=UTC)
