CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS stock_profile (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    exchange TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stock_snapshot (
    symbol TEXT PRIMARY KEY,
    last_price NUMERIC(18, 4),
    change_pct NUMERIC(10, 4),
    pe NUMERIC(18, 4),
    pb NUMERIC(18, 4),
    turnover_rate NUMERIC(18, 4),
    market_cap NUMERIC(20, 2),
    limit_up NUMERIC(18, 4),
    limit_down NUMERIC(18, 4),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS stock_tick (
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    price NUMERIC(18, 4) NOT NULL,
    volume BIGINT,
    amount NUMERIC(20, 2),
    side TEXT,
    source TEXT NOT NULL,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb
);

SELECT create_hypertable('stock_tick', 'ts', if_not_exists => TRUE, migrate_data => TRUE);

CREATE INDEX IF NOT EXISTS stock_tick_symbol_ts_idx ON stock_tick (symbol, ts DESC);

CREATE TABLE IF NOT EXISTS stock_event (
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

SELECT create_hypertable('stock_event', 'ts', if_not_exists => TRUE, migrate_data => TRUE);

CREATE INDEX IF NOT EXISTS stock_event_symbol_ts_idx ON stock_event (symbol, ts DESC);

CREATE TABLE IF NOT EXISTS symbol_command_log (
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    command_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

SELECT create_hypertable('symbol_command_log', 'ts', if_not_exists => TRUE, migrate_data => TRUE);

CREATE INDEX IF NOT EXISTS symbol_command_log_symbol_ts_idx ON symbol_command_log (symbol, ts DESC);

CREATE TABLE IF NOT EXISTS stock_kline (
    bucket_ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    period TEXT NOT NULL,
    open NUMERIC(18, 4) NOT NULL,
    high NUMERIC(18, 4) NOT NULL,
    low NUMERIC(18, 4) NOT NULL,
    close NUMERIC(18, 4) NOT NULL,
    volume BIGINT,
    amount NUMERIC(20, 2),
    source TEXT NOT NULL,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (symbol, period, bucket_ts)
);

SELECT create_hypertable('stock_kline', 'bucket_ts', if_not_exists => TRUE, migrate_data => TRUE);

CREATE INDEX IF NOT EXISTS stock_kline_symbol_period_ts_idx ON stock_kline (symbol, period, bucket_ts DESC);
