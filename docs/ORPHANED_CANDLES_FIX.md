# Orphaned Candles Fix

## Problem

In production, the `/status` endpoint showed a discrepancy:
- `database.ticker_count`: 1 (only ALAB tracked)
- `database.candles_per_ticker`: 46 different tickers with candle data

This happened because the `delete_all_tickers()` method was preserving candle data when removing tickers, while the single `remove_ticker()` method was deleting candles. This inconsistency led to orphaned candle records.

## Root Cause

### Original Bug in `src/storage.py`

The `delete_all_tickers()` method at line 276-293 was explicitly preserving candle data:

```python
def delete_all_tickers(self) -> int:
    """Remove all tickers from tracking (preserves candle data)."""
    # ...
    # Delete all tickers (candles are preserved)  # ← BUG
    cursor.execute('DELETE FROM tickers')
    # ...
```

Meanwhile, `remove_ticker()` at line 179-198 was deleting candles:

```python
def remove_ticker(self, symbol: str) -> bool:
    """Remove a ticker and its candles."""
    # Remove candles first
    cursor.execute('DELETE FROM candles WHERE ticker = ?', (symbol,))
    # Remove ticker
    cursor.execute('DELETE FROM tickers WHERE symbol = ?', (symbol,))
```

This created an inconsistency where batch deletion preserved data but single deletion removed it.

## Solution Implemented

### 1. Fixed `delete_all_tickers()` Method

Updated [storage.py:276-296](../src/storage.py#L276-L296) to delete candles consistently:

```python
def delete_all_tickers(self) -> int:
    """Remove all tickers from tracking and delete their candle data."""
    conn = self._get_connection()
    cursor = conn.cursor()

    # Get count before deletion
    cursor.execute('SELECT COUNT(*) FROM tickers')
    count = cursor.fetchone()[0]

    # Delete all candles first
    cursor.execute('DELETE FROM candles')

    # Delete all tickers
    cursor.execute('DELETE FROM tickers')

    conn.commit()
    logger.info(f"Deleted all {count} tickers and their candle data")
    return count
```

### 2. Added `cleanup_orphaned_candles()` Method

New method in [storage.py:298-320](../src/storage.py#L298-L320) to clean up existing orphaned data:

```python
def cleanup_orphaned_candles(self) -> int:
    """
    Remove candles for tickers that are no longer tracked.
    Returns count of deleted candle records.
    """
    conn = self._get_connection()
    cursor = conn.cursor()

    # Delete candles where ticker doesn't exist in tickers table
    cursor.execute('''
        DELETE FROM candles
        WHERE ticker NOT IN (SELECT symbol FROM tickers)
    ''')

    deleted = cursor.rowcount
    conn.commit()

    if deleted > 0:
        logger.info(f"Cleaned up {deleted} orphaned candles")
    else:
        logger.debug("No orphaned candles found")

    return deleted
```

### 3. Added API Endpoint

New endpoint in [routes.py:548-556](../src/api/routes.py#L548-L556):

```
POST /candles/cleanup
```

**Response:**
```json
{
  "message": "Orphaned candles cleaned up",
  "deleted_count": 4250,
  "timestamp": "2025-12-26T16:34:58.708430+00:00"
}
```

### 4. Updated API Response Messages

Updated [routes.py:304-307](../src/api/routes.py#L304-L307) to reflect the new behavior:

```python
return web.json_response({
    'error': 'Confirmation required',
    'detail': 'Add ?confirm=true to confirm deletion of all tickers',
    'warning': 'This will remove all tracked tickers and their candle data'  # ← Updated
}, status=400)
```

## How to Fix Your Production Database

### Option 1: Using the Cleanup Script (Recommended)

```bash
# Set your API key
export API_KEY="your_api_key_here"

# Run the cleanup script
./scripts/cleanup_orphaned_candles.sh
```

The script will:
1. Check current database state
2. Show how many orphaned candles exist
3. Ask for confirmation
4. Clean up orphaned candles
5. Verify the cleanup was successful

### Option 2: Using the API Directly

```bash
# Clean up orphaned candles
curl -X POST \
  -H "X-API-Key: your_api_key" \
  http://your-server:8765/candles/cleanup
```

**Response:**
```json
{
  "message": "Orphaned candles cleaned up",
  "deleted_count": 4250,
  "timestamp": "2025-12-26T16:34:58.708430+00:00"
}
```

### Option 3: Using Docker

```bash
# If running in Docker
docker exec -it your-container-name /bin/bash

# Inside container
export API_KEY=$(printenv API_KEY)
./scripts/cleanup_orphaned_candles.sh
```

## Verification

After cleanup, verify the fix:

```bash
curl -H "X-API-Key: your_api_key" http://your-server:8765/status | jq .database
```

You should now see:
- `ticker_count` matches the number of tickers in `candles_per_ticker`
- No more discrepancy between tracked tickers and tickers with candles

**Example (before cleanup):**
```json
{
  "ticker_count": 1,
  "candles_per_ticker": {
    "AAPL": 101,
    "AMD": 101,
    "AMZN": 101,
    ... (46 total tickers)
  }
}
```

**Example (after cleanup):**
```json
{
  "ticker_count": 1,
  "candles_per_ticker": {
    "ALAB": 101
  }
}
```

## Documentation Updates

Updated the following files:
- [README.md](../README.md) - Added cleanup endpoint to API reference
- [README.md](../README.md) - Updated ticker management notes about candle deletion behavior
- Created [cleanup_orphaned_candles.sh](../scripts/cleanup_orphaned_candles.sh) - Automated cleanup script

## Future Prevention

With this fix:
- ✅ `remove_ticker(symbol)` deletes the ticker AND its candles
- ✅ `delete_all_tickers()` deletes all tickers AND all candles
- ✅ Behavior is now consistent across both methods
- ✅ `cleanup_orphaned_candles()` available to fix any legacy issues

## Related Files

- [src/storage.py](../src/storage.py) - Storage layer with fixed deletion logic
- [src/api/routes.py](../src/api/routes.py) - API endpoints with cleanup route
- [scripts/cleanup_orphaned_candles.sh](../scripts/cleanup_orphaned_candles.sh) - Cleanup automation script
- [README.md](../README.md) - Updated API documentation
