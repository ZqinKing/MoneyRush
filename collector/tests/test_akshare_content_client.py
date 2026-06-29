from __future__ import annotations

from collector.services.akshare_content_client import AkshareContentClient


def test_fetch_research_reports_treats_missing_upstream_column_as_warning() -> None:
    client = AkshareContentClient.__new__(AkshareContentClient)

    def raise_missing_column(*_args: object, **_kwargs: object) -> object:
        raise KeyError("infoCode")

    object.__setattr__(client, "_call", raise_missing_column)

    result = client.fetch_research_reports("002759")

    assert result.items == []
    assert result.upstream_source == "eastmoney"
    assert result.warning_message == "stock_research_report_em unusable payload for 002759: missing 'infoCode'"
