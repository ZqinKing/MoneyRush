from shared.market_symbols import is_domestic_stock_collector_symbol, normalize_market_symbol


def test_domestic_stock_collector_symbol_accepts_supported_a_share_prefixes() -> None:
    assert is_domestic_stock_collector_symbol("000001")
    assert is_domestic_stock_collector_symbol("002028")
    assert is_domestic_stock_collector_symbol("300750")
    assert is_domestic_stock_collector_symbol("600000")
    assert is_domestic_stock_collector_symbol("688525")


def test_domestic_stock_collector_symbol_rejects_unsupported_six_digit_symbols() -> None:
    assert not is_domestic_stock_collector_symbol("005930")
    assert not is_domestic_stock_collector_symbol("920522")
    assert not is_domestic_stock_collector_symbol("AAPL.US")
    assert not is_domestic_stock_collector_symbol("")


def test_normalize_market_symbol_uppercases_and_trims_values() -> None:
    assert normalize_market_symbol(" aapl.us ") == "AAPL.US"
