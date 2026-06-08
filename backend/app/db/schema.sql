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

CREATE TABLE IF NOT EXISTS stock_capital_flow_daily (
    trade_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    company_name TEXT,
    main_net_inflow NUMERIC(20, 2),
    main_net_ratio NUMERIC(18, 4),
    super_large_net_inflow NUMERIC(20, 2),
    super_large_net_ratio NUMERIC(18, 4),
    large_net_inflow NUMERIC(20, 2),
    large_net_ratio NUMERIC(18, 4),
    medium_net_inflow NUMERIC(20, 2),
    medium_net_ratio NUMERIC(18, 4),
    small_net_inflow NUMERIC(20, 2),
    small_net_ratio NUMERIC(18, 4),
    close_price NUMERIC(18, 4),
    change_pct NUMERIC(10, 4),
    source TEXT NOT NULL,
    source_status TEXT NOT NULL DEFAULT 'fresh',
    generated_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ NOT NULL,
    last_attempt_at TIMESTAMPTZ,
    stale_reason TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (trade_date, symbol)
);

CREATE INDEX IF NOT EXISTS stock_capital_flow_daily_symbol_trade_idx
    ON stock_capital_flow_daily (symbol, trade_date DESC);

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

CREATE TABLE IF NOT EXISTS fund_profile (
    fund_code TEXT PRIMARY KEY,
    fund_name TEXT NOT NULL,
    fund_type TEXT,
    fund_company TEXT,
    manager_name TEXT,
    established_date DATE,
    risk_level TEXT,
    benchmark_index TEXT,
    management_fee NUMERIC(8, 4),
    custody_fee NUMERIC(8, 4),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fund_nav (
    fund_code TEXT NOT NULL,
    nav_date DATE NOT NULL,
    nav NUMERIC(18, 6),
    accum_nav NUMERIC(18, 6),
    daily_return NUMERIC(10, 4),
    source TEXT NOT NULL,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (fund_code, nav_date)
);

CREATE INDEX IF NOT EXISTS fund_nav_code_date_idx
    ON fund_nav (fund_code, nav_date DESC);

CREATE TABLE IF NOT EXISTS fund_snapshot (
    fund_code TEXT PRIMARY KEY,
    nav NUMERIC(18, 6),
    daily_return NUMERIC(10, 4),
    nav_date DATE,
    estimated_intraday_return NUMERIC(10, 4),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS fund_stock_holding (
    fund_code TEXT NOT NULL,
    stock_symbol TEXT NOT NULL,
    stock_market TEXT,
    stock_name TEXT,
    report_date DATE NOT NULL,
    rank INTEGER,
    weight_percent NUMERIC(10, 4),
    hold_shares BIGINT,
    hold_market_value NUMERIC(20, 2),
    change_type TEXT,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (fund_code, stock_symbol, report_date)
);

CREATE INDEX IF NOT EXISTS fund_stock_holding_fund_report_idx
    ON fund_stock_holding (fund_code, report_date DESC, rank ASC);
CREATE INDEX IF NOT EXISTS fund_stock_holding_stock_report_idx
    ON fund_stock_holding (stock_symbol, report_date DESC);

CREATE TABLE IF NOT EXISTS stock_fund_holding (
    stock_symbol TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    fund_name TEXT,
    fund_type TEXT,
    report_date DATE NOT NULL,
    weight_percent NUMERIC(10, 4),
    hold_market_value NUMERIC(20, 2),
    change_type TEXT,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (stock_symbol, fund_code, report_date)
);

CREATE INDEX IF NOT EXISTS stock_fund_holding_stock_report_idx
    ON stock_fund_holding (stock_symbol, report_date DESC);

CREATE TABLE IF NOT EXISTS fund_stock_link (
    fund_code TEXT NOT NULL,
    stock_symbol TEXT NOT NULL,
    link_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (fund_code, stock_symbol)
);

CREATE INDEX IF NOT EXISTS fund_stock_link_symbol_idx
    ON fund_stock_link (stock_symbol);

CREATE TABLE IF NOT EXISTS fund_command_log (
    ts TIMESTAMPTZ NOT NULL,
    fund_code TEXT NOT NULL,
    command_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

SELECT create_hypertable('fund_command_log', 'ts', if_not_exists => TRUE, migrate_data => TRUE);

CREATE INDEX IF NOT EXISTS fund_command_log_fund_ts_idx
    ON fund_command_log (fund_code, ts DESC);

CREATE TABLE IF NOT EXISTS significant_anomaly (
    id BIGSERIAL PRIMARY KEY,
    anomaly_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    anomaly_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    trigger_price NUMERIC(18, 4),
    reference_price NUMERIC(18, 4),
    change_pct NUMERIC(10, 4),
    trigger_volume BIGINT,
    volume_ratio NUMERIC(10, 4),
    first_trigger_ts TIMESTAMPTZ NOT NULL,
    last_trigger_ts TIMESTAMPTZ,
    duration_minutes INTEGER,
    event_count INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'collector-anomaly-aggregator',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ai_reason TEXT,
    ai_reason_status TEXT NOT NULL DEFAULT 'pending',
    ai_reason_generated_at TIMESTAMPTZ,
    ai_reason_phase TEXT NOT NULL DEFAULT 'intraday',
    ai_reason_evidence_cutoff_at TIMESTAMPTZ,
    ai_reason_includes_dragon_tiger BOOLEAN NOT NULL DEFAULT FALSE,
    ai_reason_post_close_required BOOLEAN NOT NULL DEFAULT FALSE,
    ai_reason_post_close_status TEXT NOT NULL DEFAULT 'not_due',
    ai_reason_post_close_generated_at TIMESTAMPTZ,
    ai_reason_post_close TEXT,
    ai_reason_evidence_fingerprint TEXT,
    ai_reason_attempt_count INTEGER NOT NULL DEFAULT 0,
    ai_reason_next_retry_at TIMESTAMPTZ,
    ai_reason_last_error TEXT,
    ai_reason_post_close_evidence_fingerprint TEXT,
    ai_reason_post_close_attempt_count INTEGER NOT NULL DEFAULT 0,
    ai_reason_post_close_next_retry_at TIMESTAMPTZ,
    related_news_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    related_announcement_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    first_trigger_bucket TIMESTAMPTZ NOT NULL,
    UNIQUE (anomaly_date, symbol, anomaly_type, first_trigger_bucket)
);

CREATE INDEX IF NOT EXISTS significant_anomaly_date_idx
    ON significant_anomaly (anomaly_date DESC, severity);
CREATE INDEX IF NOT EXISTS significant_anomaly_symbol_date_idx
    ON significant_anomaly (symbol, anomaly_date DESC);
CREATE INDEX IF NOT EXISTS significant_anomaly_first_trigger_idx
    ON significant_anomaly (first_trigger_ts DESC);
CREATE INDEX IF NOT EXISTS significant_anomaly_ai_status_idx
    ON significant_anomaly (ai_reason_status, anomaly_date DESC);
CREATE INDEX IF NOT EXISTS significant_anomaly_ai_phase_idx
    ON significant_anomaly (ai_reason_phase, anomaly_date DESC);
CREATE INDEX IF NOT EXISTS significant_anomaly_post_close_status_idx
    ON significant_anomaly (ai_reason_post_close_status, anomaly_date DESC);

CREATE TABLE IF NOT EXISTS macro_observation (
    series_id TEXT NOT NULL,
    observation_date DATE NOT NULL,
    value NUMERIC(18, 6),
    source TEXT NOT NULL DEFAULT 'fred',
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (series_id, observation_date)
);

CREATE INDEX IF NOT EXISTS macro_observation_series_date_idx
    ON macro_observation (series_id, observation_date DESC);

CREATE TABLE IF NOT EXISTS macro_snapshot (
    snapshot_key TEXT PRIMARY KEY,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS macro_analysis (
    id BIGSERIAL PRIMARY KEY,
    trigger_type TEXT NOT NULL,
    focus TEXT NOT NULL DEFAULT 'general',
    depth TEXT NOT NULL DEFAULT 'brief',
    snapshot_date DATE,
    data_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    analysis JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'completed',
    model_used TEXT,
    prompt_version TEXT NOT NULL DEFAULT 'v1',
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cache_key TEXT
);

CREATE INDEX IF NOT EXISTS macro_analysis_generated_idx
    ON macro_analysis (generated_at DESC);
CREATE INDEX IF NOT EXISTS macro_analysis_snapshot_idx
    ON macro_analysis (snapshot_date DESC, trigger_type);

CREATE TABLE IF NOT EXISTS llm_invocation_audit (
    id BIGSERIAL PRIMARY KEY,
    invoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    audit_date DATE NOT NULL,
    menu_module TEXT NOT NULL,
    call_category TEXT NOT NULL,
    status TEXT NOT NULL,
    model_used TEXT,
    prompt_version TEXT,
    latency_ms INTEGER,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS llm_invocation_audit_date_module_category_idx
    ON llm_invocation_audit (audit_date DESC, menu_module, call_category);
CREATE INDEX IF NOT EXISTS llm_invocation_audit_status_date_idx
    ON llm_invocation_audit (status, audit_date DESC);
