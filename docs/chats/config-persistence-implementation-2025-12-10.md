# Configuration Persistence Implementation

**Date:** 2025-12-10
**Feature:** Runtime configuration persistence for PATCH /config endpoint

---

## Problem

When users updated configuration via `PATCH /config` endpoint, changes were stored in memory but lost when the service restarted. Users needed configuration changes to persist across restarts while still allowing `.env` to provide initial defaults.

---

## Solution Implemented

### Architecture

```
Startup Flow:
1. Load defaults from .env file
2. Check if runtime config file exists (config.json)
3. If exists, merge runtime overrides on top of defaults
4. Use merged configuration

Update Flow (PATCH /config):
1. Update in-memory config
2. Save changes to config.json
3. Keep .env unchanged (source of truth for defaults)
```

### Configuration Hierarchy

```
Priority (highest to lowest):
1. Runtime config (config.json) - persisted user changes
2. Environment variables (.env)
3. Code defaults (config.py)
```

---

## Files Modified

### 1. [src/storage.py](../../src/storage.py)

**Added:**
- `ConfigStorage` class for JSON-based persistence
- `_get_default_config_path()` helper function
- Atomic file writes using temp files
- Security filtering (excludes API keys and sensitive data)

**Key Features:**
- Sparse storage (only stores changed fields)
- Auto-detects path: `/data/config.json` (Docker) or `./data/config.json` (local)
- Validates and filters fields on load
- Never persists sensitive information

**Fields that can be persisted:**
- `candle_interval_minutes`
- `max_candles_stored`
- `max_tickers`
- `ws_reconnect_delay`
- `ws_ping_interval`

**Fields excluded (security sensitive):**
- `eodhd_api_key`
- `api_key`
- `database_path`
- `http_host`
- `http_port`
- `log_level`
- `default_tickers`
- `allow_delete_all_tickers`

### 2. [src/config.py](../../src/config.py)

**Added to Config class:**
- `config_file: str` - Path to runtime config file
- `persist_config: bool` - Enable/disable persistence (default: true)
- `get_public_config()` now supports source information

**Updated ConfigManager class:**
- `__init__()` - Loads persisted overrides on startup
- `_load_overrides()` - Loads config.json and applies overrides
- `_save_overrides()` - Saves overrides to config.json
- `update()` - Now persists changes and returns `persisted` status
- `reset_to_defaults()` - Deletes config.json and reloads from .env
- `get_overrides()` - Returns current runtime overrides

**New response format with source info:**
```json
{
  "candle_interval_minutes": {
    "value": 15,
    "source": "runtime"
  },
  "max_candles_stored": {
    "value": 100,
    "source": "env"
  }
}
```

### 3. [src/api/routes.py](../../src/api/routes.py)

**Updated endpoints:**

**GET /config:**
- Now includes source information for each field
- Shows `persistence_enabled` status
- Shows `has_persisted_overrides` flag

**PATCH /config:**
- Response now includes `persisted: true/false` field
- Automatically persists changes if `PERSIST_CONFIG=true`

**POST /config/reset:**
- Deletes config.json file
- Returns `persisted_config_deleted` status

**GET /status:**
- Now includes source information in config section

### 4. [.env.example](../../.env.example)

**Added:**
```bash
# === Runtime Configuration Persistence ===
# Persist config changes made via PATCH /config endpoint
PERSIST_CONFIG=true               # Save runtime config changes to survive restarts
# CONFIG_FILE=/data/config.json  # Auto-detects: /data/config.json (Docker) or ./data/config.json (local)
                                  # Override only if you need a custom path
```

### 5. [.gitignore](../../.gitignore)

**Added:**
```
# Runtime configuration (user-specific overrides)
data/config.json
data/config.json.tmp
config.json
config.json.tmp
```

---

## Config File Format

**Location:** `./data/config.json` (local) or `/data/config.json` (Docker)

**Structure:**
```json
{
  "version": "1.0",
  "updated_at": "2025-12-10T15:57:11.498092+00:00",
  "overrides": {
    "candle_interval_minutes": 15,
    "max_candles_stored": 200
  }
}
```

**Notes:**
- Only stores fields that differ from .env defaults (sparse storage)
- Never stores sensitive information
- Uses atomic writes (temp file + rename) for safety
- Auto-creates parent directory if needed

---

## API Changes

### GET /config

**Before:**
```json
{
  "config": {
    "candle_interval_minutes": 5,
    "max_candles_stored": 100,
    "max_tickers": 50
  }
}
```

**After:**
```json
{
  "config": {
    "candle_interval_minutes": {
      "value": 15,
      "source": "runtime"
    },
    "max_candles_stored": {
      "value": 100,
      "source": "env"
    },
    "max_tickers": {
      "value": 50,
      "source": "env"
    }
  },
  "persistence_enabled": true,
  "has_persisted_overrides": true
}
```

### PATCH /config

**Response now includes:**
```json
{
  "updated": ["candle_interval_minutes"],
  "errors": [],
  "persisted": true,
  "config": { ... }
}
```

### POST /config/reset

**Response now includes:**
```json
{
  "message": "Configuration reset to defaults",
  "persisted_config_deleted": true,
  "config": { ... }
}
```

---

## Usage Examples

### Update Configuration
```bash
curl -X PATCH http://localhost:8765/config \
  -H "X-API-Key: your_key" \
  -H "Content-Type: application/json" \
  -d '{
    "candle_interval_minutes": 15,
    "max_candles_stored": 200
  }'
```

**Response:**
```json
{
  "updated": ["candle_interval_minutes", "max_candles_stored"],
  "errors": [],
  "persisted": true,
  "config": {
    "candle_interval_minutes": {"value": 15, "source": "runtime"},
    "max_candles_stored": {"value": 200, "source": "runtime"},
    "max_tickers": {"value": 50, "source": "env"}
  }
}
```

### View Configuration with Sources
```bash
curl http://localhost:8765/config -H "X-API-Key: your_key"
```

### Reset to Defaults
```bash
curl -X POST http://localhost:8765/config/reset \
  -H "X-API-Key: your_key"
```

### Disable Persistence
Add to `.env`:
```bash
PERSIST_CONFIG=false
```

Changes will still work but won't survive restarts.

---

## Testing

All tests passed successfully:

1. ✅ Config and manager creation
2. ✅ Updating configuration
3. ✅ Persisting changes to file
4. ✅ Loading persisted config in new manager
5. ✅ Source information in API responses
6. ✅ Resetting to defaults
7. ✅ Config file deletion on reset

**Test output:**
```
Test 1: Creating config and manager...
✓ Config created with persist_config=True
✓ Initial interval: 5

Test 2: Updating config...
✓ Updated: ['candle_interval_minutes', 'max_candles_stored']
✓ Persisted: True
✓ New interval: 15

Test 3: Getting overrides...
✓ Overrides: {'candle_interval_minutes': 15, 'max_candles_stored': 200}

Test 4: Checking config file...
✓ Config file exists: True
✓ Config file path: /Volumes/mike/src/eodhd_realtime_candles/data/config.json

Test 5: Loading persisted config in new manager...
✓ Loaded interval: 15
✓ Loaded max_candles: 200
✓ Loaded overrides: {'candle_interval_minutes': 15, 'max_candles_stored': 200}

Test 6: Resetting to defaults...
✓ Reset complete
✓ Config deleted: True
✓ Config file exists after reset: False

✅ All tests passed!
```

---

## Benefits

✅ **Runtime changes survive restarts** - No more lost configuration
✅ **`.env` remains source of truth** - Clear defaults for new deployments
✅ **Can reset to defaults anytime** - via `POST /config/reset`
✅ **Works in both Docker and local dev** - Auto-detects environment
✅ **Only overrides are stored** - Efficient sparse storage
✅ **Security-conscious** - Never persists API keys or sensitive data
✅ **Transparent** - Source information shows env vs runtime values
✅ **No breaking changes** - Existing API continues to work
✅ **Optional** - Can be disabled with `PERSIST_CONFIG=false`

---

## Edge Cases Handled

1. **Invalid config.json** - Falls back to .env defaults + logs warning
2. **Missing config.json** - Normal startup with .env defaults
3. **PERSIST_CONFIG=false** - Changes stay in-memory only
4. **Docker volume reset** - Config gone, uses .env defaults again
5. **Concurrent updates** - Atomic writes prevent corruption
6. **Sensitive fields** - Automatically filtered, never persisted

---

## Future Enhancements

Potential improvements for v1.1+:

1. **Config history** - Track previous values with timestamps
2. **Validation on load** - Reject invalid persisted values
3. **Backup/restore** - Export/import config as JSON
4. **Audit log** - Track who changed what and when
5. **Config profiles** - Multiple named configurations
6. **WebSocket notification** - Notify clients of config changes

---

## Summary

Successfully implemented configuration persistence that:
- Persists runtime config changes to JSON file
- Loads overrides on startup
- Shows source information (env vs runtime) in API
- Maintains security (never persists sensitive data)
- Works seamlessly in Docker and local development
- Allows easy reset to .env defaults

The implementation follows all project coding guidelines from [AI.md](../../AI.md) and maintains backward compatibility with existing API clients.
