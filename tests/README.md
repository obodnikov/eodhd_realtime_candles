# Test Suite for EODHD Real-Time Candle Aggregator

## Overview

This directory contains the test suite for the candle aggregator service, with a focus on the orphaned candles cleanup functionality added in v0.4.3.

## Test Files

### `test_storage_cleanup.py`
Unit tests for the storage layer cleanup functionality.

**Test Classes:**
- `TestStorageCleanup` - Core cleanup functionality tests
- `TestStorageCleanupConcurrency` - Concurrent operation tests

**Coverage:**
- `cleanup_orphaned_candles()` method
- `delete_all_tickers()` behavior
- `remove_ticker()` behavior
- Edge cases (empty database, no orphans, multiple orphans)
- Idempotency guarantees
- WAL mode compatibility

### `test_api_cleanup.py`
API integration tests for the cleanup endpoint.

**Test Classes:**
- `TestCleanupAPIEndpoint` - Endpoint functionality tests
- `TestCleanupAPIIntegration` - Integration with other endpoints

**Coverage:**
- `POST /candles/cleanup` endpoint
- Authentication (API key, Bearer token, query param)
- Authorization (invalid keys)
- Response format validation
- Integration with ticker deletion endpoints
- Idempotency guarantees

## Running the Tests

### Prerequisites

```bash
# Install test dependencies
pip install -r requirements.txt

# For API tests, ensure aiohttp test utilities are available
pip install aiohttp pytest pytest-aiohttp
```

### Run All Tests

```bash
# From project root
python -m pytest tests/

# With verbose output
python -m pytest tests/ -v

# With coverage report
python -m pytest tests/ --cov=src --cov-report=html
```

### Run Specific Test Files

```bash
# Storage tests only
python -m pytest tests/test_storage_cleanup.py -v

# API tests only
python -m pytest tests/test_api_cleanup.py -v
```

### Run Specific Test Cases

```bash
# Run a specific test class
python -m pytest tests/test_storage_cleanup.py::TestStorageCleanup -v

# Run a specific test method
python -m pytest tests/test_storage_cleanup.py::TestStorageCleanup::test_cleanup_orphaned_candles_with_orphans -v
```

### Alternative: Using unittest

```bash
# Run all tests with unittest
python -m unittest discover tests/

# Run specific test file
python -m unittest tests.test_storage_cleanup

# Run with verbose output
python -m unittest discover tests/ -v
```

## Test Coverage

The test suite covers:

### Storage Layer
- ✅ Cleanup with no orphans
- ✅ Cleanup with orphaned candles
- ✅ Cleanup on empty database
- ✅ Cleanup with multiple orphaned tickers
- ✅ `delete_all_tickers()` removes candles
- ✅ `remove_ticker()` removes candles
- ✅ Idempotent cleanup operations
- ✅ WAL mode compatibility

### API Layer
- ✅ Authentication required
- ✅ Multiple authentication methods (header, bearer, query param)
- ✅ Invalid API key rejection
- ✅ Successful cleanup with orphans
- ✅ Successful cleanup without orphans
- ✅ Response format validation
- ✅ Idempotent endpoint calls
- ✅ Integration with ticker deletion

## Test Data

All tests use temporary databases that are automatically cleaned up after each test. No production data is affected.

## Continuous Integration

These tests should be run:
- Before every commit
- In CI/CD pipeline
- Before deploying to production

## Adding New Tests

When adding new functionality:

1. Create test file in `tests/` directory
2. Follow naming convention: `test_<module>_<feature>.py`
3. Use descriptive test method names: `test_<scenario>_<expected_result>`
4. Add docstrings explaining what is being tested
5. Update this README with new test coverage

## Troubleshooting

### Import Errors

If you see import errors, ensure the `src/` directory is in your Python path:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
```

### Database Lock Errors

If you encounter database lock errors during tests:
- Ensure tests properly clean up connections
- Check that SQLite WAL mode is enabled
- Increase timeout values in test setup if needed

### Async Test Failures

For aiohttp tests, ensure:
- `pytest-aiohttp` is installed
- Using `@unittest_run_loop` decorator for async tests
- Properly awaiting async operations

## Related Documentation

- [Main README](../README.md)
- [Orphaned Candles Fix Documentation](../docs/ORPHANED_CANDLES_FIX.md)
- [Storage Performance Tuning](../docs/sqlite-performance-tuning.md)
