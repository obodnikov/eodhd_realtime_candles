-- PostgreSQL Schema for EODHD Real-time Candles
-- Version: 1.0
-- Compatible with: PostgreSQL 14+

-- Candles table with optimized indexes
CREATE TABLE IF NOT EXISTS candles (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    timestamp BIGINT NOT NULL,
    datetime_utc TEXT NOT NULL,
    open DECIMAL(12, 4) NOT NULL,
    high DECIMAL(12, 4) NOT NULL,
    low DECIMAL(12, 4) NOT NULL,
    close DECIMAL(12, 4) NOT NULL,
    volume BIGINT NOT NULL,
    tick_count INTEGER NOT NULL,
    is_complete BOOLEAN NOT NULL,
    interval_minutes INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, timestamp, interval_minutes)
);

-- Optimized indexes for common queries
CREATE INDEX IF NOT EXISTS idx_candles_ticker_timestamp ON candles(ticker, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_candles_ticker_complete ON candles(ticker, is_complete) WHERE is_complete = true;
CREATE INDEX IF NOT EXISTS idx_candles_timestamp ON candles(timestamp DESC);

-- Tickers table
CREATE TABLE IF NOT EXISTS tickers (
    symbol VARCHAR(10) PRIMARY KEY,
    added_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) DEFAULT 'active',
    last_tick_at TIMESTAMPTZ,
    last_price DECIMAL(12, 4),
    last_candle_request_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Config table
CREATE TABLE IF NOT EXISTS config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- WebSocket status table (single row for status sharing)
CREATE TABLE IF NOT EXISTS websocket_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    connected BOOLEAN NOT NULL,
    subscribed_tickers TEXT NOT NULL,
    subscribed_count INTEGER NOT NULL,
    pending_subscribe TEXT NOT NULL,
    connection_count INTEGER NOT NULL,
    tick_count BIGINT NOT NULL,
    tick_queue_size INTEGER NOT NULL DEFAULT 0,
    tick_queue_maxsize INTEGER NOT NULL DEFAULT 0,
    tick_enqueued_count BIGINT NOT NULL DEFAULT 0,
    tick_processed_count BIGINT NOT NULL DEFAULT 0,
    tick_dropped_count BIGINT NOT NULL DEFAULT 0,
    candle_write_queue_size INTEGER NOT NULL DEFAULT 0,
    candle_write_queue_maxsize INTEGER NOT NULL DEFAULT 0,
    candle_write_dropped_count BIGINT NOT NULL DEFAULT 0,
    last_message TEXT,
    last_update TIMESTAMPTZ NOT NULL
);

-- Active candles status table (single row for status sharing)
CREATE TABLE IF NOT EXISTS active_candles_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- Idempotent additive migrations for websocket status metrics
ALTER TABLE websocket_status ADD COLUMN IF NOT EXISTS stale_tick_dropped_count BIGINT NOT NULL DEFAULT 0;
ALTER TABLE websocket_status ADD COLUMN IF NOT EXISTS out_of_order_tick_dropped_count BIGINT NOT NULL DEFAULT 0;

-- Grant permissions (adjust user as needed)
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO eodhd_user;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eodhd_user;
