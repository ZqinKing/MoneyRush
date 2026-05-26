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
CREATE UNIQUE INDEX IF NOT EXISTS stock_tick_identity_idx
    ON stock_tick (symbol, ts, source, price, COALESCE(volume, -1), COALESCE(amount, -1), COALESCE(side, ''));

CREATE TABLE IF NOT EXISTS stock_event (
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

SELECT create_hypertable('stock_event', 'ts', if_not_exists => TRUE, migrate_data => TRUE);

CREATE INDEX IF NOT EXISTS stock_event_symbol_ts_idx ON stock_event (symbol, ts DESC);
CREATE UNIQUE INDEX IF NOT EXISTS stock_event_identity_idx
    ON stock_event (symbol, ts, event_type, source, payload);

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

CREATE TABLE IF NOT EXISTS stock_research_report (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    title TEXT NOT NULL,
    rating TEXT,
    institution TEXT,
    analyst TEXT,
    industry TEXT,
    published_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    source_url TEXT,
    provider TEXT NOT NULL DEFAULT 'akshare',
    upstream_source TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS stock_research_report_symbol_published_idx
    ON stock_research_report (symbol, published_at DESC);
CREATE INDEX IF NOT EXISTS stock_research_report_symbol_first_seen_idx
    ON stock_research_report (symbol, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS stock_research_report_published_idx
    ON stock_research_report (published_at DESC);

CREATE TABLE IF NOT EXISTS stock_news_item (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT,
    scope TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    content TEXT,
    article_source TEXT,
    published_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    source_url TEXT,
    ai_summary TEXT,
    provider TEXT NOT NULL DEFAULT 'akshare',
    upstream_source TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS stock_news_item_scope_published_idx
    ON stock_news_item (scope, published_at DESC);
CREATE INDEX IF NOT EXISTS stock_news_item_symbol_published_idx
    ON stock_news_item (symbol, published_at DESC);
CREATE INDEX IF NOT EXISTS stock_news_item_symbol_first_seen_idx
    ON stock_news_item (symbol, first_seen_at DESC);

CREATE TABLE IF NOT EXISTS stock_announcement_item (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    title TEXT NOT NULL,
    announcement_type TEXT,
    published_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    pdf_url TEXT,
    provider TEXT NOT NULL DEFAULT 'akshare',
    upstream_source TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS stock_announcement_item_symbol_published_idx
    ON stock_announcement_item (symbol, published_at DESC);
CREATE INDEX IF NOT EXISTS stock_announcement_item_symbol_first_seen_idx
    ON stock_announcement_item (symbol, first_seen_at DESC);

CREATE TABLE IF NOT EXISTS content_fetch_checkpoint (
    lane TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT '',
    cursor JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_due_at TIMESTAMPTZ NOT NULL,
    cooldown_until TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (lane, symbol)
);

CREATE INDEX IF NOT EXISTS content_fetch_checkpoint_next_due_idx
    ON content_fetch_checkpoint (next_due_at ASC);

CREATE TABLE IF NOT EXISTS content_fetch_log (
    id BIGSERIAL PRIMARY KEY,
    lane TEXT NOT NULL,
    symbol TEXT,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    http_hint TEXT,
    error_message TEXT,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS content_fetch_log_lane_started_idx
    ON content_fetch_log (lane, started_at DESC);

CREATE TABLE IF NOT EXISTS dragon_tiger_daily_item (
    trade_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    close_price NUMERIC(18, 4),
    change_percent NUMERIC(10, 4),
    net_buy_amount NUMERIC(20, 2),
    buy_amount NUMERIC(20, 2),
    sell_amount NUMERIC(20, 2),
    deal_amount NUMERIC(20, 2),
    total_amount NUMERIC(20, 2),
    net_buy_ratio NUMERIC(18, 4),
    deal_amount_ratio NUMERIC(18, 4),
    turnover_rate NUMERIC(18, 4),
    free_market_cap NUMERIC(20, 2),
    explain TEXT,
    reason TEXT,
    after_1d NUMERIC(10, 4),
    after_2d NUMERIC(10, 4),
    after_5d NUMERIC(10, 4),
    after_10d NUMERIC(10, 4),
    source TEXT NOT NULL,
    generated_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ NOT NULL,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (trade_date, symbol)
);

CREATE INDEX IF NOT EXISTS dragon_tiger_daily_item_symbol_trade_idx
    ON dragon_tiger_daily_item (symbol, trade_date DESC);

CREATE TABLE IF NOT EXISTS dragon_tiger_institution_item (
    trade_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    close_price NUMERIC(18, 4),
    change_percent NUMERIC(10, 4),
    buy_org_count INTEGER,
    sell_org_count INTEGER,
    org_buy_amount NUMERIC(20, 2),
    org_sell_amount NUMERIC(20, 2),
    org_net_amount NUMERIC(20, 2),
    market_total_amount NUMERIC(20, 2),
    org_net_amount_ratio NUMERIC(18, 4),
    turnover_rate NUMERIC(18, 4),
    free_market_cap NUMERIC(20, 2),
    reason TEXT,
    source TEXT NOT NULL,
    generated_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ NOT NULL,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (trade_date, symbol)
);

CREATE INDEX IF NOT EXISTS dragon_tiger_institution_item_symbol_trade_idx
    ON dragon_tiger_institution_item (symbol, trade_date DESC);

CREATE TABLE IF NOT EXISTS dragon_tiger_collection_checkpoint (
    job_name TEXT PRIMARY KEY,
    next_due_at TIMESTAMPTZ NOT NULL,
    cooldown_until TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    last_collected_trade_date DATE,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS dragon_tiger_collection_checkpoint_next_due_idx
    ON dragon_tiger_collection_checkpoint (next_due_at ASC);

CREATE TABLE IF NOT EXISTS dragon_tiger_collection_log (
    id BIGSERIAL PRIMARY KEY,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    trade_date DATE,
    error_message TEXT,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS dragon_tiger_collection_log_job_started_idx
    ON dragon_tiger_collection_log (job_name, started_at DESC);
