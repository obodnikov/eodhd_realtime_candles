# Last Candle Request Tracking Implementation

**Date**: 2025-12-26  
**Feature**: Track when candles were last requested for each ticker

---

## Overview

Added `last_candle_request_at` field to track the timestamp of the last candle data request for each ticker. This is a prerequisite for future implementations (e.g., auto-cleanup of inactive tickers).

---

## Changes Made

### 1. Database Schema (`storage.py`)

- Added `last_candle_request_at TEXT` column to `tickers` table
- **Improved migration logic** (2025-01-09):
  - Uses `PRAGMA table_info(tickers)` to check column existence
  - Only runs ALTER TABLE if column doesn't exist
  - Avoids unnecessary ALTER TABLE attempts on every startup
  - More robust than try-except approach
- Column allows NULL for backward compatibility

### 2. TrackedTicker Dataclass (`storage.py`)

- Added `last_candle_request_at: Optional[str]` field
- Field is included in `to_dict()` output via `asdict()`

### 3. Storage Methods (`storage.py`)

- Added `update_ticker_last_request(symbol: str)` method
- Updates timestamp to current UTC time
- Updated `get_tickers()` to SELECT and populate the new field

### 4. API Routes (`api/routes.py`)

Added `update_ticker_last_request()` calls to all candle retrieval endpoints:
- `get_candles()` - Single ticker candle retrieval
- `get_all_candles()` - All tickers candle retrieval
- `get_multi_candles()` - Multiple tickers candle retrieval
- `get_latest_candle()` - Current incomplete candle retrieval

**Bug Fix**: Changed `ticker_obj.ticker` to `ticker_obj.symbol` in `get_all_candles()` (correct field name)

### 5. Test Coverage (`tests/test_last_candle_request.py`) - Added 2025-01-09

**TestLastCandleRequestTracking**:
- `test_new_ticker_has_null_last_request` - Verify NULL for new tickers
- `test_update_ticker_last_request` - Verify timestamp is set correctly
- `test_update_ticker_last_request_case_insensitive` - Verify case handling
- `test_update_ticker_last_request_multiple_times` - Verify updates overwrite
- `test_update_nonexistent_ticker` - Verify no error on non-existent ticker
- `test_get_tickers_includes_last_request_field` - Verify field in response
- `test_tracked_ticker_to_dict_includes_field` - Verify serialization

**TestMigration**:
- `test_migration_on_existing_database` - Verify migration adds column
- `test_migration_idempotent` - Verify multiple runs don't fail
- `test_column_check_uses_pragma` - Verify PRAGMA-based check works

---

## API Response Changes

### Before:
```json
{
  "symbol": "AAPL",
  "added_at": "2025-12-09T10:00:00Z",
  "status": "active",
  "last_tick_at": "2025-12-09T14:29:55Z",
  "last_price": 245.67,
  "candle_count": 15
}
```

### After:
```json
{
  "symbol": "AAPL",
  "added_at": "2025-12-09T10:00:00Z",
  "status": "active",
  "last_tick_at": "2025-12-09T14:29:55Z",
  "last_price": 245.67,
  "candle_count": 15,
  "last_candle_request_at": "2025-12-26T10:15:30Z"
}
```

**Note**: `last_candle_request_at` will be `null` for:
- Newly added tickers (before first candle request)
- Existing tickers in database (before first request after upgrade)

---

## Backward Compatibility

✅ **Fully backward compatible**:
- Existing databases automatically get the new column via migration
- NULL values are handled properly in all queries
- No breaking changes to API responses (new field is additive)

## Code Review Fixes (2025-01-09)

### Issue 1: Migration Logic
**Problem**: Migration ran on every instantiation using try-except, could silently ignore other OperationalErrors.

**Solution**: 
- Use `PRAGMA table_info(tickers)` to check if column exists
- Only run ALTER TABLE if column is missing
- More explicit and safer than exception handling

### Issue 2: Test Coverage
**Problem**: No tests for new functionality.

**Solution**: Added comprehensive test suite:
- 7 tests for `update_ticker_last_request()` functionality
- 3 tests for migration logic
- Coverage for edge cases: NULL handling, case insensitivity, non-existent tickers, idempotency

---

## Testing Recommendations

1. **New Installation**: Verify field is created and populated on candle requests
2. **Existing Database**: Verify migration adds column without errors
3. **NULL Handling**: Verify tickers without requests show `null` in response
4. **Timestamp Updates**: Verify timestamp updates on each candle request

---

## Future Use Cases

This field enables:
- Auto-cleanup of inactive tickers (not requested for X days)
- Usage analytics (most/least requested tickers)
- Rate limiting per ticker
- Stale data detection
